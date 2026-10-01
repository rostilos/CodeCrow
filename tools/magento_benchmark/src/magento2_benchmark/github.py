from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .util import canonical_json, read_json, sha256_text, write_json


GITHUB_API_VERSION = "2022-11-28"
GITHUB_REST_GET_CACHE_KIND = "github-rest-get-cache"
GITHUB_REST_GET_CACHE_SCHEMA = "codecrow.github-rest-get-cache-envelope"

_CACHE_ENVELOPE_FIELDS = frozenset(
    {
        "kind",
        "schema",
        "method",
        "url",
        "status",
        "fetchedAt",
        "etag",
        "headers",
        "value",
        "responseSha256",
        "envelopeSha256",
    }
)
_CACHED_RESPONSE_HEADERS = frozenset(
    {
        "etag",
        "last-modified",
        "link",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
    }
)


@dataclass(frozen=True)
class GitHubResponse:
    value: Any
    headers: Mapping[str, str]
    status: int
    url: str | None = None
    fetched_at: str | None = None
    cache_envelope_digest: str | None = None
    cache_envelope: Mapping[str, Any] | None = None


class GitHubClient:
    """Small cached GitHub REST client with explicit mutation boundaries."""

    def __init__(
        self,
        *,
        api_url: str = "https://api.github.com",
        token: str | None = None,
        cache_dir: Path | None = None,
        timeout: int = 60,
        offline: bool = False,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.offline = offline

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        root: Path,
        offline: bool = False,
    ) -> "GitHubClient":
        token_env = str(config.get("token_env") or "GITHUB_TOKEN")
        token = os.getenv(token_env) or os.getenv("GH_TOKEN")
        cache = Path(str(config.get("cache_dir") or ".cache/github"))
        if not cache.is_absolute():
            cache = root / cache
        return cls(
            api_url=str(config.get("api_url") or "https://api.github.com"),
            token=token,
            cache_dir=cache,
            timeout=int(config.get("timeout_seconds") or 60),
            offline=offline,
        )

    def _cache_path(self, method: str, url: str) -> Path | None:
        if self.cache_dir is None or method != "GET":
            return None
        return self.cache_dir / f"{sha256_text(url)}.json"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "codecrow-magento2-benchmark",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request_url(
        self,
        path_or_url: str,
        query: Mapping[str, Any] | None,
    ) -> str:
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else f"{self.api_url}/{path_or_url.lstrip('/')}"
        )
        if query:
            separator = "&" if "?" in url else "?"
            url += separator + urllib.parse.urlencode(query)
        return url

    @staticmethod
    def _cached_headers(headers: Mapping[str, str]) -> dict[str, str]:
        return {
            key: value
            for key, value in headers.items()
            if key.casefold() in _CACHED_RESPONSE_HEADERS
        }

    @staticmethod
    def _header_value(
        headers: Mapping[str, str],
        name: str,
    ) -> str | None:
        matches = [
            value
            for key, value in headers.items()
            if key.casefold() == name.casefold()
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _fetched_at() -> str:
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _is_canonical_utc_timestamp(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return False
        return parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value

    @staticmethod
    def _is_cacheable_status(value: Any) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and (200 <= value <= 299 or value in {404, 410})
        )

    @classmethod
    def _cache_envelope(
        cls,
        *,
        url: str,
        status: int,
        headers: Mapping[str, str],
        value: Any,
        fetched_at: str | None = None,
    ) -> dict[str, Any]:
        if not cls._is_cacheable_status(status):
            raise ValueError(f"GitHub HTTP status {status} is not cacheable")
        stored_headers = cls._cached_headers(headers)
        envelope: dict[str, Any] = {
            "kind": GITHUB_REST_GET_CACHE_KIND,
            "schema": GITHUB_REST_GET_CACHE_SCHEMA,
            "method": "GET",
            "url": url,
            "status": status,
            "fetchedAt": fetched_at or cls._fetched_at(),
            "etag": cls._header_value(stored_headers, "etag"),
            "headers": stored_headers,
            "value": value,
            "responseSha256": sha256_text(canonical_json(value)),
        }
        envelope["envelopeSha256"] = sha256_text(canonical_json(envelope))
        return envelope

    @classmethod
    def _validate_cache_envelope(
        cls,
        value: Any,
        *,
        expected_url: str,
    ) -> tuple[GitHubResponse | None, str | None]:
        if not isinstance(value, Mapping):
            return None, "cache envelope is not an object"
        if set(value) != _CACHE_ENVELOPE_FIELDS:
            return None, "cache envelope fields do not match the schema"
        if value.get("kind") != GITHUB_REST_GET_CACHE_KIND:
            return None, "cache envelope kind mismatch"
        if value.get("schema") != GITHUB_REST_GET_CACHE_SCHEMA:
            return None, "cache envelope schema mismatch"
        if value.get("method") != "GET":
            return None, "cache envelope method mismatch"
        if value.get("url") != expected_url:
            return None, "cache envelope URL mismatch"
        status = value.get("status")
        if not cls._is_cacheable_status(status):
            return None, "cache envelope status is invalid"
        fetched_at = value.get("fetchedAt")
        if not cls._is_canonical_utc_timestamp(fetched_at):
            return None, "cache envelope fetchedAt is not canonical UTC"
        headers = value.get("headers")
        if not isinstance(headers, Mapping) or any(
            not isinstance(key, str)
            or not key
            or not isinstance(header_value, str)
            for key, header_value in headers.items()
        ):
            return None, "cache envelope headers are invalid"
        folded_header_names = [key.casefold() for key in headers]
        if len(folded_header_names) != len(set(folded_header_names)):
            return None, "cache envelope headers repeat a name"
        etag = value.get("etag")
        if etag is not None and not isinstance(etag, str):
            return None, "cache envelope ETag is invalid"
        if etag != cls._header_value(headers, "etag"):
            return None, "cache envelope ETag mismatch"
        response_digest = value.get("responseSha256")
        if response_digest != sha256_text(canonical_json(value.get("value"))):
            return None, "cache envelope response digest mismatch"
        envelope_digest = value.get("envelopeSha256")
        unsigned = dict(value)
        unsigned.pop("envelopeSha256")
        if envelope_digest != sha256_text(canonical_json(unsigned)):
            return None, "cache envelope digest mismatch"
        return (
            GitHubResponse(
                value=value.get("value"),
                headers=dict(headers),
                status=status,
                url=expected_url,
                fetched_at=fetched_at,
                cache_envelope_digest=envelope_digest,
                cache_envelope=dict(value),
            ),
            None,
        )

    def _load_cached_get(
        self,
        url: str,
    ) -> tuple[GitHubResponse | None, str | None, bool]:
        cache_path = self._cache_path("GET", url)
        if cache_path is None or not cache_path.exists():
            return None, None, False
        try:
            value = read_json(cache_path)
        except (OSError, ValueError) as exc:
            return None, str(exc), True
        response, error = self._validate_cache_envelope(
            value,
            expected_url=url,
        )
        return response, error, True

    def inspect_cached_get(
        self,
        path_or_url: str,
        *,
        query: Mapping[str, Any] | None = None,
    ) -> GitHubResponse | None:
        """Return an exact, validated cached GET response without networking."""

        url = self._request_url(path_or_url, query)
        cached, _error, _exists = self._load_cached_get(url)
        return cached

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        query: Mapping[str, Any] | None = None,
        payload: Any = None,
        cache: bool = True,
    ) -> GitHubResponse:
        url = self._request_url(path_or_url, query)
        method = method.upper()
        cache_path = self._cache_path(method, url) if cache else None
        cached: GitHubResponse | None = None
        cache_error: str | None = None
        cache_exists = False
        if cache_path is not None:
            cached, cache_error, cache_exists = self._load_cached_get(url)
        if self.offline:
            if cached is None:
                if cache_exists:
                    raise RuntimeError(
                        f"invalid cached GitHub GET response for {url}: "
                        f"{cache_error or 'validation failed'}"
                    )
                raise RuntimeError(f"offline GitHub cache miss: {url}")
            if cached.status >= 400:
                raise RuntimeError(
                    f"GitHub {method} {url} failed with HTTP {cached.status}: "
                    f"{canonical_json(cached.value)[:4000]}"
                )
            return cached

        headers = self._headers()
        cached_etag = (
            self._header_value(cached.headers, "etag") if cached else None
        )
        if cached_etag:
            headers["If-None-Match"] = cached_etag
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                value = json.loads(raw) if raw else None
                response_headers = dict(response.headers.items())
                result = GitHubResponse(
                    value=value,
                    headers=response_headers,
                    status=response.status,
                    url=url,
                    fetched_at=self._fetched_at(),
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached is not None and cached_etag:
                if cached.status >= 400:
                    raise RuntimeError(
                        f"GitHub {method} {url} failed with cached HTTP "
                        f"{cached.status}: "
                        f"{canonical_json(cached.value)[:4000]}"
                    ) from exc
                return cached
            detail = exc.read().decode("utf-8", errors="replace")[:4000]
            if cache_path is not None and exc.code in {404, 410}:
                try:
                    error_value = json.loads(detail)
                except json.JSONDecodeError:
                    error_value = {"message": detail}
                error_headers = dict(exc.headers.items())
                envelope = self._cache_envelope(
                    url=url,
                    status=exc.code,
                    headers=error_headers,
                    value=error_value,
                )
                write_json(cache_path, envelope)
            reset = exc.headers.get("X-RateLimit-Reset")
            if exc.code in {403, 429} and reset:
                remaining = max(0, int(reset) - int(time.time()))
                raise RuntimeError(
                    "GitHub API rate limit reached; retry after "
                    f"{remaining} seconds or set a token. {detail}"
                ) from exc
            raise RuntimeError(
                f"GitHub {method} {url} failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"GitHub {method} {url} failed: {exc}. "
                "Cached evidence is never substituted after a live-request "
                "failure; use explicit --offline mode for a cache-only run."
            ) from exc

        if cache_path is not None and self._is_cacheable_status(result.status):
            envelope = self._cache_envelope(
                url=url,
                status=result.status,
                headers=result.headers,
                value=result.value,
                fetched_at=result.fetched_at,
            )
            write_json(cache_path, envelope)
            result = GitHubResponse(
                value=result.value,
                headers=result.headers,
                status=result.status,
                url=result.url,
                fetched_at=result.fetched_at,
                cache_envelope_digest=envelope["envelopeSha256"],
                cache_envelope=envelope,
            )
        return result

    def get(self, path: str, **query: Any) -> Any:
        return self.request("GET", path, query=query or None).value

    def graphql(self, query: str, variables: Mapping[str, Any]) -> Any:
        """Run and cache an authenticated, read-only GraphQL query."""

        request_payload = {"query": query, "variables": dict(variables)}
        request_digest = sha256_text(canonical_json(request_payload))
        cache_path = (
            self.cache_dir / f"graphql-{request_digest}.json"
            if self.cache_dir is not None
            else None
        )
        if self.offline:
            if cache_path is None or not cache_path.exists():
                raise RuntimeError(
                    f"offline GitHub GraphQL cache miss: {request_digest}"
                )
            cached = read_json(cache_path)
            if not isinstance(cached, Mapping):
                raise RuntimeError("invalid cached GitHub GraphQL response")
            cached_value = cached.get("value")
            if (
                cached.get("requestDigest") != request_digest
                or cached.get("request") != request_payload
                or not isinstance(cached_value, Mapping)
                or cached.get("responseDigest")
                != sha256_text(canonical_json(cached_value))
            ):
                raise RuntimeError(
                    "cached GitHub GraphQL request/response digest mismatch"
                )
            return cached_value
        self.require_token()
        response = self.request(
            "POST",
            "/graphql",
            payload=request_payload,
            cache=False,
        )
        if not isinstance(response.value, Mapping):
            raise RuntimeError("GitHub GraphQL returned a non-object")
        errors = response.value.get("errors")
        if errors:
            raise RuntimeError(
                "GitHub GraphQL query failed: "
                + canonical_json(errors)[:4000]
            )
        if cache_path is not None:
            write_json(
                cache_path,
                {
                    "requestDigest": request_digest,
                    "request": request_payload,
                    "fetchedAt": datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "value": response.value,
                    "responseDigest": sha256_text(
                        canonical_json(response.value)
                    ),
                },
            )
        return response.value

    def paginate(
        self,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        max_pages: int | None = None,
    ) -> Iterable[Any]:
        page = 1
        while max_pages is None or page <= max_pages:
            page_query = dict(query or {})
            page_query.setdefault("per_page", 100)
            page_query["page"] = page
            response = self.request("GET", path, query=page_query)
            if not isinstance(response.value, list):
                raise RuntimeError(f"expected an array from GitHub endpoint {path}")
            yield from response.value
            if len(response.value) < int(page_query["per_page"]):
                break
            page += 1

    def require_token(self) -> None:
        if not self.token:
            raise RuntimeError(
                "a GitHub token is required for fork/PR mutations; set the "
                "configured token environment variable"
            )

    def create_ref(self, repository: str, ref: str, sha: str) -> Any:
        self.require_token()
        return self.request(
            "POST",
            f"/repos/{repository}/git/refs",
            payload={"ref": f"refs/heads/{ref}", "sha": sha},
            cache=False,
        ).value

    def get_ref(self, repository: str, ref: str) -> Any:
        encoded = urllib.parse.quote(ref, safe="")
        return self.request(
            "GET",
            f"/repos/{repository}/git/ref/heads/{encoded}",
            cache=False,
        ).value

    def create_pull(
        self,
        repository: str,
        *,
        title: str,
        body: str,
        base: str,
        head: str,
    ) -> Any:
        self.require_token()
        return self.request(
            "POST",
            f"/repos/{repository}/pulls",
            payload={"title": title, "body": body, "base": base, "head": head},
            cache=False,
        ).value

    def find_pull(
        self,
        repository: str,
        *,
        owner: str,
        head: str,
    ) -> Mapping[str, Any] | None:
        value = self.request(
            "GET",
            f"/repos/{repository}/pulls",
            query={"state": "all", "head": f"{owner}:{head}", "per_page": 10},
            cache=False,
        ).value
        if not isinstance(value, list):
            raise RuntimeError("GitHub pull lookup returned a non-array")
        matches = [item for item in value if isinstance(item, Mapping)]
        return matches[0] if matches else None
