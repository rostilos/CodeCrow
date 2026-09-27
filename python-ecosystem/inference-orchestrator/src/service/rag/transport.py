"""HTTP pool ownership and observable fail-open RAG query transport."""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Dict, List, Optional, Any
import httpx

logger = logging.getLogger(__name__)

_REVIEW_CONTEXT_MUTATION_CONFLICT_PREFIX = (
    "another RAG mutation is active for "
)
_REVIEW_CONTEXT_MUTATION_RETRY_SECONDS = 1.0


def _http_error_detail(error: httpx.HTTPError) -> tuple[Optional[int], str]:
    if not isinstance(error, httpx.HTTPStatusError):
        detail = str(error).strip()
        if not detail:
            request = getattr(error, "request", None)
            request_target = (
                f" for {request.method} {request.url}"
                if request is not None
                else ""
            )
            detail = f"{type(error).__name__}{request_target}"
        return None, detail
    response = error.response
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("error") or "")
    except (ValueError, TypeError):
        detail = response.text.strip()
    return response.status_code, detail or str(error)


def _is_review_context_mutation_conflict(result: Dict[str, Any]) -> bool:
    """Identify the one transient 409 emitted by mutation coordination."""
    return (
        result.get("status_code") == 409
        and str(result.get("error") or "").startswith(
            _REVIEW_CONTEXT_MUTATION_CONFLICT_PREFIX
        )
    )


class RagTransport:
    def __init__(self, base_url: Optional[str] = None, enabled: Optional[bool] = None):
        """
        Initialize RAG client.

        Args:
            base_url: RAG pipeline API URL (default from env RAG_API_URL)
            enabled: Whether RAG is enabled (default from env RAG_ENABLED)
        """
        self.base_url = base_url or os.environ.get(
            "RAG_API_URL", "http://codecrow-rag-pipeline:8001"
        )
        self.enabled = enabled if enabled is not None else os.environ.get("RAG_ENABLED", "true").lower() == "true"
        self.timeout = 30.0
        self._client: Optional[httpx.AsyncClient] = None
        self._mutation_client: Optional[httpx.AsyncClient] = None
        self._service_secret = (
            os.environ.get("SERVICE_SECRET")
            or os.environ.get("CODECROW_RAG_API_SECRET", "")
        )

        if self.enabled:
            logger.info(f"RAG client initialized: {self.base_url}")
        else:
            logger.info("RAG client disabled")


    async def _get_client(self, *, mutation: bool = False) -> httpx.AsyncClient:
        pool = self._mutation_client if mutation else self._client
        if pool is None or pool.is_closed:
            headers = {"x-service-secret": self._service_secret} if self._service_secret else {}
            pool = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers=headers,
            )
            if mutation:
                self._mutation_client = pool
            else:
                self._client = pool
        return pool

    async def close(self):
        pools = (self._client, self._mutation_client)
        self._client = self._mutation_client = None
        # Both pools close even when one cleanup raises.
        results = await asyncio.gather(
            *(pool.aclose() for pool in pools if pool is not None and not pool.is_closed),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    @staticmethod
    def _review_query_timeout_seconds() -> float:
        try:
            configured_timeout = os.environ.get(
                "RAG_REVIEW_CONTEXT_TIMEOUT_SECONDS",
                "120",
            )
            return max(30.0, float(configured_timeout))
        except (TypeError, ValueError):
            return 120.0


    @staticmethod
    def _review_preparation_timeout_seconds() -> float:
        try:
            configured_timeout = os.environ.get(
                "RAG_REVIEW_PREPARATION_TIMEOUT_SECONDS",
                "1800",
            )
            return max(60.0, float(configured_timeout))
        except (TypeError, ValueError):
            return 1800.0


    async def _post_structural_query(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        empty_result: Dict[str, Any],
        *,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Run an optional structural query without failing the core review."""
        if not self.enabled:
            return dict(empty_result)

        try:
            client = await self._get_client(mutation=endpoint == "/query/review-generation")
            response = await client.post(
                f"{self.base_url}{endpoint}",
                json=payload,
                timeout=(
                    timeout_seconds
                    if timeout_seconds is not None
                    else self.timeout
                ),
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("RAG query returned a non-object response")
            return result
        except httpx.HTTPError as error:
            status_code, detail = _http_error_detail(error)
            logger.debug(
                "Structural repository query failed: endpoint=%s status=%s "
                "detail=%s",
                endpoint,
                status_code or "transport-error",
                detail,
            )
            return {
                **empty_result,
                "status": "error",
                "status_code": status_code,
                "error": detail,
            }
        except Exception as error:
            logger.debug(
                "Unexpected structural repository query failure: endpoint=%s "
                "error=%s",
                endpoint,
                error,
                exc_info=True,
            )
            return {
                **empty_result,
                "status": "error",
                "status_code": None,
                "error": str(error),
            }


    async def _post_review_query(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        empty_result: Dict[str, Any],
        *,
        operation: str,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Run one proposed-tree query, waiting out active graph mutations."""
        timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else self._review_query_timeout_seconds()
        )
        last_mutation_conflict: Optional[Dict[str, Any]] = None
        mutation_conflicts = 0
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    result = await self._post_structural_query(
                        endpoint,
                        payload,
                        empty_result,
                        timeout_seconds=timeout_seconds,
                    )
                    result_status = str(
                        result.get("status") or ""
                    ).strip().casefold()
                    if (
                        result_status
                        in {"error", "failed", "unavailable", "disabled"}
                        or result.get("error")
                        or result.get("unavailable") is True
                    ):
                        degraded_result = {**empty_result, **result}
                        default_coverage = empty_result.get("coverage")
                        observed_coverage = result.get("coverage")
                        if isinstance(default_coverage, dict):
                            merged_coverage = {
                                **default_coverage,
                                **(
                                    observed_coverage
                                    if isinstance(observed_coverage, dict)
                                    else {}
                                ),
                            }
                            for state_key in (
                                "state",
                                "graphState",
                                "treeState",
                            ):
                                if state_key in default_coverage:
                                    merged_coverage[state_key] = (
                                        default_coverage[state_key]
                                    )
                            degraded_result["coverage"] = merged_coverage
                        result = degraded_result
                    if not _is_review_context_mutation_conflict(result):
                        if result.get("status") == "error":
                            logger.warning(
                                "Proposed-tree %s failed: status=%s detail=%s",
                                operation,
                                result.get("status_code")
                                or "transport-error",
                                result.get("error") or "unknown error",
                            )
                        return result
                    last_mutation_conflict = result
                    log_conflict = (
                        logger.warning
                        if mutation_conflicts == 0
                        else logger.debug
                    )
                    mutation_conflicts += 1
                    log_conflict(
                        "Waiting for active RAG mutation before retrying "
                        "proposed-tree %s: workspace=%s project=%s status=%s "
                        "detail=%s",
                        operation,
                        payload.get("workspace"),
                        payload.get("project"),
                        result.get("status_code"),
                        result.get("error"),
                    )
                    await asyncio.sleep(
                        _REVIEW_CONTEXT_MUTATION_RETRY_SECONDS
                    )
        except TimeoutError:
            if last_mutation_conflict is not None:
                logger.debug(
                    "Proposed-tree %s remained blocked by an active RAG "
                    "mutation until its timeout: workspace=%s project=%s",
                    operation,
                    payload.get("workspace"),
                    payload.get("project"),
                )
                return last_mutation_conflict
            return {
                **empty_result,
                "status": "error",
                "status_code": None,
                "error": (
                    f"proposed-tree {operation} timed out after "
                    f"{timeout_seconds:g} seconds"
                ),
            }


    async def is_healthy(self) -> bool:
        """
        Check if RAG pipeline is healthy.

        Returns:
            True if RAG is enabled and healthy, False otherwise
        """
        if not self.enabled:
            return False

        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"RAG health check failed: {e}")
            return False


