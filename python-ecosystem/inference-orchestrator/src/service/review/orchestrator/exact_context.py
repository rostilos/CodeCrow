"""Bounded, immutable-head exact context resolution for review discovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import inspect
import json
import logging
import os
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional

from model.multi_stage import ReviewContextRequest
from utils.path_identity import normalize_repository_path


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %d", name, raw, default)
        return default


_MAX_EXACT_WINDOW_LINES = 240
_DEFAULT_CONTEXT_RADIUS = 80
_MAX_EXACT_WINDOW_CHARS = 32_000
_MAX_REVIEW_EXACT_READS = max(
    4,
    _env_int("REVIEW_EXACT_SOURCE_MAX_READS", 24),
)


@dataclass
class ReviewFollowupBudget:
    """One explicit per-review budget shared by discovery follow-up stages."""

    max_calls: int = 4
    _used: int = 0
    _entries: list[dict[str, str]] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def try_acquire(self, kind: str, source_key: str) -> bool:
        if not await self.reserve(kind, source_key):
            return False
        await self.commit(kind, source_key)
        return True

    async def reserve(self, kind: str, source_key: str) -> bool:
        normalized_kind = str(kind or "").strip().lower()
        normalized_source = str(source_key or "").strip()
        if not normalized_kind or not normalized_source:
            raise ValueError("follow-up budget kind and source key are required")
        async with self._lock:
            if self._used >= max(0, int(self.max_calls)):
                return False
            self._used += 1
            self._entries.append({
                "kind": normalized_kind,
                "sourceKey": normalized_source,
                "state": "reserved",
            })
            return True

    async def commit(self, kind: str, source_key: str) -> None:
        async with self._lock:
            entry = self._find_entry(kind, source_key, "reserved")
            if entry is None:
                raise RuntimeError("follow-up reservation is unavailable")
            entry["state"] = "committed"

    async def release(self, kind: str, source_key: str) -> bool:
        async with self._lock:
            entry = self._find_entry(kind, source_key, "reserved")
            if entry is None:
                return False
            self._entries.remove(entry)
            self._used -= 1
            return True

    def _find_entry(
        self,
        kind: str,
        source_key: str,
        state: str,
    ) -> Optional[dict[str, str]]:
        normalized_kind = str(kind or "").strip().lower()
        normalized_source = str(source_key or "").strip()
        return next((
            entry for entry in reversed(self._entries)
            if entry.get("kind") == normalized_kind
            and entry.get("sourceKey") == normalized_source
            and entry.get("state") == state
        ), None)

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, int(self.max_calls) - self._used)

    def summary(self) -> dict[str, Any]:
        return {
            "maxCalls": max(0, int(self.max_calls)),
            "used": self._used,
            "remaining": self.remaining,
            "entries": list(self._entries),
        }


@dataclass(frozen=True)
class ExactContextEvidence:
    request_id: str
    evidence_id: str
    path: str
    revision: str
    start_line: int
    end_line: int
    content: str
    content_digest: str
    source: str

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "evidenceId": self.evidence_id,
            "path": self.path,
            "revision": self.revision,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "contentDigest": self.content_digest,
            "source": self.source,
            "content": self.content,
        }

    def ledger_fact(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "revision": self.revision,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "contentDigest": self.content_digest,
            "source": self.source,
            "content": self.content,
        }


@dataclass(frozen=True)
class UnresolvedContextRequest:
    request_id: str
    reason: str

    def prompt_payload(self) -> dict[str, str]:
        return {"requestId": self.request_id, "reason": self.reason}


@dataclass(frozen=True)
class ExactContextResolution:
    resolved: tuple[ExactContextEvidence, ...] = ()
    unresolved: tuple[UnresolvedContextRequest, ...] = ()

    @property
    def visible_evidence_by_id(self) -> dict[str, tuple[dict[str, Any], ...]]:
        return {
            item.evidence_id: (item.ledger_fact(),)
            for item in self.resolved
        }

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "resolved": [item.prompt_payload() for item in self.resolved],
            "unresolved": [item.prompt_payload() for item in self.unresolved],
        }


ExactReader = Callable[..., Awaitable[Any]]
ExactContextCacheKey = tuple[str, str, int, int, str]
ExactReadTask = asyncio.Task[Optional[ExactContextEvidence]]
NavigationCandidate = tuple[str, int, int]
NavigationCacheKey = tuple[
    str,
    str,
    tuple[str, ...],
    tuple[str, ...],
    str,
    str,
    str,
    str,
    str,
]
NavigationTask = asyncio.Task[tuple[NavigationCandidate, ...]]


class ExactContextResolver:
    """Resolve requested evidence from enrichment, then current-head exact reads.

    RAG/parser results are navigation hints only. Their chunk text is never
    returned as exact evidence; after navigation the resolver reads source from
    the immutable revision being reviewed.
    """

    def __init__(
        self,
        request,
        *,
        file_contents: Optional[Mapping[str, Optional[str]]] = None,
        file_metadata: Iterable[Any] = (),
        rag_client=None,
        mcp_client=None,
        exact_reader: Optional[ExactReader] = None,
        max_parallel_reads: int = 4,
    ) -> None:
        self.request = request
        self.revision = str(
            getattr(request, "currentCommitHash", None)
            or getattr(request, "commitHash", None)
            or ""
        ).strip()
        self.rag_client = rag_client
        self.mcp_client = mcp_client
        self.exact_reader = exact_reader
        self._read_semaphore = asyncio.Semaphore(max(1, max_parallel_reads))
        if file_contents is None:
            file_contents = {
                str(getattr(item, "path", "") or ""): getattr(item, "content", None)
                for item in (
                    getattr(
                        getattr(request, "enrichmentData", None),
                        "fileContents",
                        None,
                    )
                    or ()
                )
                if getattr(item, "skipped", False) is not True
            }
        self._content_by_path = self._normalize_content_map(file_contents)
        self._metadata = tuple(file_metadata or ())
        self._cache: dict[ExactContextCacheKey, ExactContextEvidence] = {}
        self._inflight_reads: dict[
            ExactContextCacheKey,
            ExactReadTask,
        ] = {}
        self._inflight_read_waiters: dict[ExactReadTask, int] = {}
        self._navigation_cache: dict[
            NavigationCacheKey,
            tuple[NavigationCandidate, ...],
        ] = {}
        self._inflight_navigation: dict[
            NavigationCacheKey,
            NavigationTask,
        ] = {}
        self._inflight_navigation_waiters: dict[NavigationTask, int] = {}
        self._cache_lock = asyncio.Lock()
        self._executor = self._mcp_executor() if self.mcp_client is not None else None

    async def resolve(
        self,
        requests: Iterable[ReviewContextRequest],
        *,
        originating_paths: Iterable[str] = (),
    ) -> ExactContextResolution:
        local_requests = tuple(
            request
            for request in requests
            if request.kind == "LOCAL_EXACT"
        )
        if not local_requests:
            return ExactContextResolution()
        if not self.revision:
            return ExactContextResolution(unresolved=tuple(
                UnresolvedContextRequest(
                    request.requestId,
                    "The immutable current-head revision is unavailable.",
                )
                for request in local_requests
            ))

        executor = self._executor
        results = await asyncio.gather(*(
            self._resolve_one(
                request,
                tuple(originating_paths),
                executor,
            )
            for request in local_requests
        ))
        resolved = tuple(
            item for item in results if isinstance(item, ExactContextEvidence)
        )
        unresolved = tuple(
            item for item in results if isinstance(item, UnresolvedContextRequest)
        )
        return ExactContextResolution(resolved=resolved, unresolved=unresolved)

    async def resolve_reverse_references(
        self,
        identifier: str,
        *,
        originating_paths: Iterable[str] = (),
        excluded_paths: Iterable[str] = (),
        max_results: int = 3,
        request_id_prefix: str = "reverse",
    ) -> ExactContextResolution:
        """Pinned-read a bounded deterministic fan-out of current references.

        RAG returns navigation coordinates only.  Every admitted coordinate is
        converted to a normal exact request, so the shared immutable-head read
        cache, concurrency bound, content cap, and symbol check still apply.
        """
        normalized_identifier = str(identifier or "").strip()
        if not normalized_identifier or max_results <= 0:
            return ExactContextResolution()
        excluded = {
            normalized
            for path in excluded_paths
            if (normalized := normalize_repository_path(path))
        }
        try:
            candidates = await self._rag_navigation_candidates(
                normalized_identifier,
                tuple(originating_paths),
                reverse_references=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.info(
                "Reverse-reference navigation for %s unavailable: %s",
                normalized_identifier,
                exc,
            )
            return ExactContextResolution(unresolved=(
                UnresolvedContextRequest(
                    f"{request_id_prefix}-navigation",
                    f"Reverse-reference navigation unavailable: {type(exc).__name__}",
                ),
            ))
        requests = []
        seen_paths = set()
        for path, start_line, end_line in sorted(candidates):
            normalized_path = normalize_repository_path(path)
            if not normalized_path or normalized_path in excluded or normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            requests.append(ReviewContextRequest(
                requestId=f"{request_id_prefix}-{len(requests) + 1}",
                kind="LOCAL_EXACT",
                question="Does this current source still reference the removed contract?",
                targetPath=normalized_path,
                targetSymbol=normalized_identifier,
                requiredEvidence=(
                    "Pinned current-head source containing the exact removed identifier."
                ),
                startLine=max(1, int(start_line or 1) - 40),
                endLine=max(
                    1,
                    int(end_line or start_line or 1) + 40,
                ),
            ))
            if len(requests) >= max_results:
                break
        if not requests:
            return ExactContextResolution()
        return await self.resolve(
            requests,
            originating_paths=originating_paths,
        )

    async def _resolve_one(
        self,
        request: ReviewContextRequest,
        originating_paths: tuple[str, ...],
        executor,
    ) -> ExactContextEvidence | UnresolvedContextRequest:
        try:
            target = await self._resolve_target(request, originating_paths)
            if target is None:
                return UnresolvedContextRequest(
                    request.requestId,
                    "No unique current-head path/range could be resolved.",
                )
            path, start_line, end_line = target
            symbol = str(request.targetSymbol or "").strip()
            content = self._content_for_path(path)
            if content is None and start_line <= 0 and symbol:
                ranged_candidates = [
                    candidate
                    for candidate in await self._rag_navigation_candidates(
                        symbol,
                        tuple(dict.fromkeys((*originating_paths, path))),
                    )
                    if candidate[0] == normalize_repository_path(path)
                    and candidate[1] > 0
                ]
                if not ranged_candidates:
                    return UnresolvedContextRequest(
                        request.requestId,
                        "The symbol path was known but no exact current-head line range was available.",
                    )
                _, start_line, end_line = sorted(ranged_candidates)[0]
            cache_key = (
                self.revision,
                normalize_repository_path(path),
                start_line,
                end_line,
                symbol,
            )
            async with self._cache_lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                return self._with_request_id(cached, request.requestId)

            if content is not None:
                window = self._window_from_content(
                    content,
                    start_line,
                    end_line,
                    symbol,
                )
                if window is None:
                    return UnresolvedContextRequest(
                        request.requestId,
                        "Requested symbol or line range is absent from enriched current source.",
                    )
                if symbol and symbol not in window[2]:
                    return UnresolvedContextRequest(
                        request.requestId,
                        "Requested symbol is absent from the selected enriched source range.",
                    )
                evidence = self._make_evidence(
                    request.requestId,
                    path,
                    *window,
                    source="enrichment",
                )
                async with self._cache_lock:
                    self._cache[cache_key] = evidence
                return evidence
            else:
                if self.exact_reader is None and executor is None:
                    return UnresolvedContextRequest(
                        request.requestId,
                        "Exact current-head reader is unavailable for the resolved path.",
                    )
                if start_line <= 0 and not symbol:
                    return UnresolvedContextRequest(
                        request.requestId,
                        "Exact lookup needs a line range or symbol; broad file reads are not allowed.",
                    )
                evidence = await self._read_current_head_coalesced(
                    cache_key,
                    request,
                    path,
                    start_line,
                    end_line,
                    symbol,
                    executor,
                )
                if evidence is None:
                    return UnresolvedContextRequest(
                        request.requestId,
                        "Immutable current-head source read failed or returned no exact source.",
                    )
                return evidence
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.info(
                "Exact context request %s unavailable: %s",
                request.requestId,
                exc,
            )
            return UnresolvedContextRequest(
                request.requestId,
                f"Exact context unavailable: {type(exc).__name__}",
            )

    async def _read_current_head_coalesced(
        self,
        cache_key: ExactContextCacheKey,
        request: ReviewContextRequest,
        path: str,
        start_line: int,
        end_line: int,
        symbol: str,
        executor,
    ) -> Optional[ExactContextEvidence]:
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return self._with_request_id(cached, request.requestId)
            read_task = self._inflight_reads.get(cache_key)
            if read_task is None:
                read_task = asyncio.create_task(
                    self._read_and_cache_current_head(
                        cache_key,
                        request,
                        path,
                        start_line,
                        end_line,
                        symbol,
                        executor,
                    )
                )
                read_task.add_done_callback(self._observe_read_completion)
                self._inflight_reads[cache_key] = read_task
            self._inflight_read_waiters[read_task] = (
                self._inflight_read_waiters.get(read_task, 0) + 1
            )

        caller_cancelled = False
        try:
            evidence = await asyncio.shield(read_task)
            if evidence is None:
                return None
            return self._with_request_id(evidence, request.requestId)
        except asyncio.CancelledError:
            caller_cancelled = True
            raise
        finally:
            async with self._cache_lock:
                remaining_waiters = max(
                    0,
                    self._inflight_read_waiters.get(read_task, 1) - 1,
                )
                if remaining_waiters:
                    self._inflight_read_waiters[read_task] = remaining_waiters
                else:
                    self._inflight_read_waiters.pop(read_task, None)
                    if caller_cancelled and not read_task.done():
                        read_task.cancel()

    async def _read_and_cache_current_head(
        self,
        cache_key: ExactContextCacheKey,
        request: ReviewContextRequest,
        path: str,
        start_line: int,
        end_line: int,
        symbol: str,
        executor,
    ) -> Optional[ExactContextEvidence]:
        current_task = asyncio.current_task()
        try:
            evidence = await self._read_current_head(
                request,
                path,
                start_line,
                end_line,
                symbol,
                executor,
            )
            if evidence is not None:
                async with self._cache_lock:
                    self._cache[cache_key] = evidence
            return evidence
        finally:
            async with self._cache_lock:
                if self._inflight_reads.get(cache_key) is current_task:
                    self._inflight_reads.pop(cache_key, None)

    @staticmethod
    def _observe_read_completion(read_task: asyncio.Task[Any]) -> None:
        if not read_task.cancelled():
            read_task.exception()

    async def _resolve_target(
        self,
        request: ReviewContextRequest,
        originating_paths: tuple[str, ...],
    ) -> Optional[tuple[str, int, int]]:
        path = _safe_repository_path(request.targetPath or "")
        start_line = int(request.startLine or 0)
        end_line = int(request.endLine or 0)
        if path:
            return path, start_line, end_line

        symbol = str(request.targetSymbol or "").strip()
        relationship = str(request.relationship or "").strip()
        identifier = symbol or relationship
        if not identifier:
            return None

        metadata_candidates = self._metadata_candidates(identifier)
        if len(metadata_candidates) == 1:
            return metadata_candidates[0]
        rag_candidates = await self._rag_navigation_candidates(
            identifier,
            originating_paths,
        )
        combined = {
            (_safe_repository_path(path), start, end)
            for path, start, end in (*metadata_candidates, *rag_candidates)
            if _safe_repository_path(path)
        }
        paths = {path for path, _, _ in combined}
        if len(paths) != 1:
            return None
        chosen_path = next(iter(paths))
        ranged = sorted(
            (start, end)
            for path, start, end in combined
            if path == chosen_path and start > 0
        )
        if ranged:
            return chosen_path, ranged[0][0], ranged[0][1]
        return chosen_path, 0, 0

    def _metadata_candidates(self, identifier: str) -> list[tuple[str, int, int]]:
        needle = identifier.casefold()
        candidates = []
        for metadata in self._metadata:
            payload = (
                metadata.model_dump(mode="json", by_alias=True)
                if hasattr(metadata, "model_dump")
                else dict(metadata)
                if isinstance(metadata, Mapping)
                else vars(metadata)
            )
            searchable = json.dumps(payload, ensure_ascii=False, default=str).casefold()
            path = _safe_repository_path(payload.get("path") or "")
            if path and needle in searchable:
                candidates.append((path, 0, 0))
        return sorted(set(candidates))

    async def _rag_navigation_candidates(
        self,
        identifier: str,
        originating_paths: tuple[str, ...],
        *,
        reverse_references: bool = False,
    ) -> list[tuple[str, int, int]]:
        if self.rag_client is None or not self.revision:
            return []
        branches = [
            branch
            for branch in (
                getattr(self.request, "get_rag_branch", lambda: None)(),
                getattr(self.request, "get_rag_base_branch", lambda: None)(),
            )
            if branch
        ]
        branches = list(dict.fromkeys(branches))
        if not branches:
            return []
        effective_paths = tuple(sorted({
            normalized
            for path in (originating_paths or self.request.changedFiles or ())
            if (normalized := normalize_repository_path(path))
        }))
        cache_identifier = (
            f"reverse-reference:{identifier}"
            if reverse_references
            else identifier
        )
        navigation_key: NavigationCacheKey = (
            self.revision,
            cache_identifier,
            tuple(branches),
            effective_paths,
            str(getattr(self.request, "baseCommitHash", None) or ""),
            str(
                getattr(
                    self.request,
                    "ragBaseGenerationManifestSha256",
                    None,
                )
                or ""
            ),
            str(getattr(self.request, "ragPrGenerationFingerprint", None) or ""),
            str(
                getattr(
                    self.request,
                    "ragPrOverlayGenerationManifestSha256",
                    None,
                )
                or ""
            ),
            str(getattr(self.request, "ragCollectionTarget", None) or ""),
        )
        async with self._cache_lock:
            cached = self._navigation_cache.get(navigation_key)
            if cached is not None:
                return list(cached)
            navigation_task = self._inflight_navigation.get(navigation_key)
            if navigation_task is None:
                navigation_task = asyncio.create_task(
                    self._fetch_rag_navigation_candidates(
                        navigation_key,
                        identifier,
                        tuple(branches),
                        effective_paths,
                        reverse_references,
                    )
                )
                navigation_task.add_done_callback(self._observe_read_completion)
                self._inflight_navigation[navigation_key] = navigation_task
            self._inflight_navigation_waiters[navigation_task] = (
                self._inflight_navigation_waiters.get(navigation_task, 0) + 1
            )

        caller_cancelled = False
        try:
            return list(await asyncio.shield(navigation_task))
        except asyncio.CancelledError:
            caller_cancelled = True
            raise
        finally:
            async with self._cache_lock:
                remaining_waiters = max(
                    0,
                    self._inflight_navigation_waiters.get(
                        navigation_task,
                        1,
                    ) - 1,
                )
                if remaining_waiters:
                    self._inflight_navigation_waiters[navigation_task] = (
                        remaining_waiters
                    )
                else:
                    self._inflight_navigation_waiters.pop(navigation_task, None)
                    if caller_cancelled and not navigation_task.done():
                        navigation_task.cancel()

    async def _fetch_rag_navigation_candidates(
        self,
        navigation_key: NavigationCacheKey,
        identifier: str,
        branches: tuple[str, ...],
        effective_paths: tuple[str, ...],
        reverse_references: bool,
    ) -> tuple[NavigationCandidate, ...]:
        current_task = asyncio.current_task()
        try:
            candidates = await self._request_rag_navigation_candidates(
                identifier,
                branches,
                effective_paths,
                reverse_references,
            )
            async with self._cache_lock:
                self._navigation_cache[navigation_key] = candidates
            return candidates
        finally:
            async with self._cache_lock:
                if self._inflight_navigation.get(navigation_key) is current_task:
                    self._inflight_navigation.pop(navigation_key, None)

    async def _request_rag_navigation_candidates(
        self,
        identifier: str,
        branches: tuple[str, ...],
        effective_paths: tuple[str, ...],
        reverse_references: bool,
    ) -> tuple[NavigationCandidate, ...]:
        overlay_binding_complete = all((
            self.request.pullRequestId,
            self.revision,
            getattr(self.request, "baseCommitHash", None),
            getattr(self.request, "ragBaseGenerationManifestSha256", None),
            getattr(self.request, "ragPrGenerationFingerprint", None),
            getattr(
                self.request,
                "ragPrOverlayGenerationManifestSha256",
                None,
            ),
        ))
        response = await self.rag_client.get_deterministic_context(
            workspace=self.request.projectWorkspace,
            project=self.request.projectNamespace,
            branches=list(branches),
            file_paths=list(effective_paths),
            limit_per_file=8 if reverse_references else 4,
            pr_number=(self.request.pullRequestId if overlay_binding_complete else None),
            pr_changed_files=(list(dict.fromkeys([
                *(
                    getattr(self.request, "fullPrChangedFiles", None)
                    or self.request.changedFiles
                    or ()
                ),
                *(
                    getattr(self.request, "fullPrDeletedFiles", None)
                    or getattr(self.request, "deletedFiles", None)
                    or ()
                ),
            ])) if overlay_binding_complete else None),
            additional_identifiers=[identifier],
            navigation_mode=(
                "REVERSE_REFERENCES" if reverse_references else "CONTEXT"
            ),
            reference_identifiers=([identifier] if reverse_references else None),
            source_revision=(self.revision if overlay_binding_complete else None),
            base_revision=getattr(self.request, "baseCommitHash", None),
            base_generation_manifest_sha256=getattr(
                self.request,
                "ragBaseGenerationManifestSha256",
                None,
            ),
            pr_generation_fingerprint=(getattr(
                self.request,
                "ragPrGenerationFingerprint",
                None,
            ) if overlay_binding_complete else None),
            pr_overlay_generation_manifest_sha256=(getattr(
                self.request,
                "ragPrOverlayGenerationManifestSha256",
                None,
            ) if overlay_binding_complete else None),
            collection_target=getattr(self.request, "ragCollectionTarget", None),
        )
        deleted = {
            normalize_repository_path(path)
            for path in (
                getattr(self.request, "fullPrDeletedFiles", None)
                or getattr(self.request, "deletedFiles", None)
                or ()
            )
            if normalize_repository_path(path)
        }
        parsed_candidates = (
            _reverse_navigation_candidates(response)
            if reverse_references
            else _navigation_candidates(response, identifier)
        )
        return tuple(
            candidate
            for candidate in parsed_candidates
            if candidate[0] not in deleted
        )

    async def _read_current_head(
        self,
        request: ReviewContextRequest,
        path: str,
        start_line: int,
        end_line: int,
        symbol: str,
        executor,
    ) -> Optional[ExactContextEvidence]:
        effective_start = start_line
        effective_end = end_line
        if effective_start > 0:
            effective_end = effective_end or (
                effective_start + _MAX_EXACT_WINDOW_LINES - 1
            )
            effective_end = min(
                effective_end,
                effective_start + _MAX_EXACT_WINDOW_LINES - 1,
            )
        async with self._read_semaphore:
            if self.exact_reader is not None:
                result = self.exact_reader(
                    path=path,
                    start_line=effective_start,
                    end_line=effective_end,
                    revision=self.revision,
                )
                if inspect.isawaitable(result):
                    result = await result
            else:
                arguments: dict[str, Any] = {"filePath": path}
                if effective_start > 0:
                    arguments.update({
                        "startLine": effective_start,
                        "endLine": effective_end,
                    })
                result = await executor.execute_tool(
                    "getBranchFileContent",
                    arguments,
                )
        content, actual_start, actual_end = _exact_reader_payload(
            result,
            effective_start,
            effective_end,
        )
        if not content:
            return None
        if symbol and symbol not in content:
            return None
        if actual_start <= 0:
            actual_start = effective_start or 1
        if actual_end <= 0:
            actual_end = actual_start + max(0, len(content.splitlines()) - 1)
        return self._make_evidence(
            request.requestId,
            path,
            actual_start,
            actual_end,
            content,
            source="vcs-current-head",
        )

    def _window_from_content(
        self,
        content: str,
        start_line: int,
        end_line: int,
        symbol: str,
    ) -> Optional[tuple[int, int, str]]:
        lines = content.splitlines()
        if not lines:
            return None
        if start_line <= 0 and symbol:
            matching = [
                index
                for index, line in enumerate(lines, start=1)
                if symbol in line
            ]
            if not matching:
                return None
            start_line = max(1, matching[0] - _DEFAULT_CONTEXT_RADIUS)
            end_line = min(
                len(lines),
                matching[0] + _DEFAULT_CONTEXT_RADIUS,
            )
        elif start_line <= 0:
            return None
        end_line = end_line or (start_line + _MAX_EXACT_WINDOW_LINES - 1)
        end_line = min(
            len(lines),
            end_line,
            start_line + _MAX_EXACT_WINDOW_LINES - 1,
        )
        if start_line > len(lines) or end_line < start_line:
            return None
        selected = lines[start_line - 1:end_line]
        used_chars = 0
        retained: list[str] = []
        for line in selected:
            required = len(line) + (1 if retained else 0)
            if used_chars + required > _MAX_EXACT_WINDOW_CHARS:
                remaining = _MAX_EXACT_WINDOW_CHARS - used_chars
                if remaining > 0:
                    retained.append(line[:remaining])
                break
            retained.append(line)
            used_chars += required
        if not retained:
            return None
        actual_end = start_line + len(retained) - 1
        return start_line, actual_end, "\n".join(retained)

    def _make_evidence(
        self,
        request_id: str,
        path: str,
        start_line: int,
        end_line: int,
        content: str,
        *,
        source: str,
    ) -> ExactContextEvidence:
        digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        evidence_identity = hashlib.sha256(
            f"{self.revision}\0{path}\0{start_line}\0{end_line}\0{digest}".encode(
                "utf-8"
            )
        ).hexdigest()[:20]
        return ExactContextEvidence(
            request_id=request_id,
            evidence_id="CTX-" + evidence_identity,
            path=_safe_repository_path(path),
            revision=self.revision,
            start_line=start_line,
            end_line=end_line,
            content=content,
            content_digest=digest,
            source=source,
        )

    def _content_for_path(self, path: str) -> Optional[str]:
        return self._content_by_path.get(_safe_repository_path(path))

    @staticmethod
    def _normalize_content_map(
        values: Mapping[str, Optional[str]],
    ) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for path, content in values.items():
            key = _safe_repository_path(path)
            if key and isinstance(content, str) and content:
                normalized[key] = content
        return normalized

    @staticmethod
    def _with_request_id(
        evidence: ExactContextEvidence,
        request_id: str,
    ) -> ExactContextEvidence:
        return ExactContextEvidence(
            request_id=request_id,
            evidence_id=evidence.evidence_id,
            path=evidence.path,
            revision=evidence.revision,
            start_line=evidence.start_line,
            end_line=evidence.end_line,
            content=evidence.content,
            content_digest=evidence.content_digest,
            source=evidence.source,
        )

    def _mcp_executor(self):
        from service.review.orchestrator.mcp_tool_executor import McpToolExecutor

        return McpToolExecutor(
            self.mcp_client,
            self.request,
            "stage_1",
            review_revision=self.revision,
            max_calls=_MAX_REVIEW_EXACT_READS,
        )


def _navigation_candidates(
    response: Any,
    identifier: str,
) -> list[tuple[str, int, int]]:
    needle = identifier.casefold()
    candidates: set[tuple[str, int, int]] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            serialized = json.dumps(value, ensure_ascii=False, default=str).casefold()
            path = _safe_repository_path(
                value.get("path")
                or value.get("file_path")
                or value.get("filePath")
                or ""
            )
            if path and needle in serialized:
                start = _integer_value(
                    value,
                    "start_line",
                    "startLine",
                    "line_start",
                    "line",
                )
                end = _integer_value(
                    value,
                    "end_line",
                    "endLine",
                    "line_end",
                )
                candidates.add((path, start, end))
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(response)
    return sorted(candidates)


def _reverse_navigation_candidates(response: Any) -> list[NavigationCandidate]:
    context = response.get("context", response) if isinstance(response, Mapping) else {}
    values = context.get("reference_navigation", ()) if isinstance(context, Mapping) else ()
    candidates = {
        (
            _safe_repository_path(value.get("path", "")),
            _integer_value(value, "start_line", "startLine"),
            _integer_value(value, "end_line", "endLine"),
        )
        for value in values
        if isinstance(value, Mapping) and _safe_repository_path(value.get("path", ""))
    }
    return sorted(candidates)


def _integer_value(value: Mapping[str, Any], *names: str) -> int:
    for name in names:
        try:
            parsed = int(value.get(name) or 0)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    metadata = value.get("metadata")
    if isinstance(metadata, Mapping):
        return _integer_value(metadata, *names)
    return 0


def _exact_reader_payload(
    result: Any,
    requested_start: int,
    requested_end: int,
) -> tuple[str, int, int]:
    if isinstance(result, Mapping):
        content = result.get("fileContent") or result.get("content") or ""
        if "[CodeCrow Filter:" in str(content):
            return "", 0, 0
        return (
            str(content),
            _integer_value(result, "startLine", "start_line") or requested_start,
            _integer_value(result, "endLine", "end_line") or requested_end,
        )
    text = str(result or "").strip()
    if (
        not text
        or "[CodeCrow Filter:" in text
        or text.startswith((
            "Tool call failed:",
            "Error executing tool:",
            "Tool budget exhausted",
            "Tool not allowed:",
        ))
    ):
        return "", 0, 0
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text, requested_start, requested_end
    if not isinstance(payload, Mapping) or payload.get("error"):
        return "", 0, 0
    content = payload.get("fileContent") or payload.get("content") or ""
    if "[CodeCrow Filter:" in str(content):
        return "", 0, 0
    return (
        str(content),
        _integer_value(payload, "startLine", "start_line") or requested_start,
        _integer_value(payload, "endLine", "end_line") or requested_end,
    )


def _safe_repository_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/"):
        return ""
    normalized = normalize_repository_path(raw)
    if not normalized or any(part == ".." for part in normalized.split("/")):
        return ""
    return normalized
