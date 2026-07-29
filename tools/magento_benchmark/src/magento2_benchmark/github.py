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


@dataclass(frozen=True)
class GitHubResponse:
    value: Any
    headers: Mapping[str, str]
    status: int


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

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        query: Mapping[str, Any] | None = None,
        payload: Any = None,
        cache: bool = True,
    ) -> GitHubResponse:
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else f"{self.api_url}/{path_or_url.lstrip('/')}"
        )
        if query:
            separator = "&" if "?" in url else "?"
            url += separator + urllib.parse.urlencode(query)
        method = method.upper()
        cache_path = self._cache_path(method, url) if cache else None
        cached: Mapping[str, Any] | None = None
        if cache_path is not None and cache_path.exists():
            value = read_json(cache_path)
            if isinstance(value, Mapping):
                cached = value
        if self.offline:
            if cached is None:
                raise RuntimeError(f"offline GitHub cache miss: {url}")
            return GitHubResponse(
                value=cached["value"],
                headers=cached.get("headers") or {},
                status=int(cached.get("status") or 200),
            )

        headers = self._headers()
        if cached and cached.get("etag"):
            headers["If-None-Match"] = str(cached["etag"])
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
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached is not None:
                return GitHubResponse(
                    value=cached["value"],
                    headers=cached.get("headers") or {},
                    status=200,
                )
            detail = exc.read().decode("utf-8", errors="replace")[:4000]
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

        if cache_path is not None:
            write_json(
                cache_path,
                {
                    "url": url,
                    "status": result.status,
                    "etag": result.headers.get("ETag"),
                    "headers": {
                        key: value
                        for key, value in result.headers.items()
                        if key.casefold()
                        in {
                            "etag",
                            "last-modified",
                            "link",
                            "x-ratelimit-limit",
                            "x-ratelimit-remaining",
                            "x-ratelimit-reset",
                        }
                    },
                    "value": result.value,
                },
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
