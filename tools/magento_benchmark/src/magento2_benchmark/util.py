from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Mapping, Sequence


def _git_metadata_directories(repository: Path) -> list[Path]:
    dot_git = repository / ".git"
    if dot_git.is_dir():
        return [dot_git.resolve()]
    if not dot_git.is_file():
        if all(
            path.exists()
            for path in (
                repository / "HEAD",
                repository / "config",
                repository / "objects",
                repository / "refs",
            )
        ):
            return [repository.resolve()]
        return []
    try:
        marker = dot_git.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"cannot read linked-worktree metadata: {exc}") from exc
    prefix = "gitdir:"
    if not marker.casefold().startswith(prefix):
        raise ValueError("linked-worktree .git file is malformed")
    raw_git_dir = marker[len(prefix) :].strip()
    git_dir = Path(raw_git_dir)
    if not git_dir.is_absolute():
        git_dir = (repository / git_dir).resolve()
    directories = [git_dir]
    common_marker = git_dir / "commondir"
    if common_marker.is_file():
        try:
            raw_common = common_marker.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(
                f"cannot read linked-worktree common metadata: {exc}"
            ) from exc
        common_dir = Path(raw_common)
        if not common_dir.is_absolute():
            common_dir = (git_dir / common_dir).resolve()
        directories.append(common_dir)
    return list(dict.fromkeys(directories))


def is_local_git_repository(repository: Path) -> bool:
    """Return whether *repository* is a real local worktree or bare store."""

    if not repository.is_dir() or repository.is_symlink():
        return False
    completed = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "rev-parse",
            "--git-dir",
        ],
        env=hermetic_git_environment(offline=True),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return completed.returncode == 0


def validate_git_evidence_repository(repository: Path) -> None:
    """Fail on host-only Git state that can rewrite benchmark evidence."""

    for git_dir in _git_metadata_directories(repository):
        for relative, label in (
            (Path("info") / "attributes", "host-only Git info/attributes"),
            (Path("info") / "grafts", "legacy Git grafts"),
            (Path("shallow"), "shallow Git history"),
        ):
            path = git_dir / relative
            try:
                nonempty = path.is_file() and bool(path.read_bytes())
            except OSError as exc:
                raise ValueError(f"cannot inspect {label}: {exc}") from exc
            if nonempty:
                raise ValueError(
                    f"{label} must be absent or empty for benchmark evidence"
                )
    if _git_metadata_directories(repository):
        completed = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repository),
                "config",
                "--local",
                "--get-regexp",
                r"^diff\.",
            ],
            env=hermetic_git_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            raise ValueError(
                "repository-local diff.* configuration must be absent for "
                "benchmark evidence"
            )
        if completed.returncode not in {0, 1}:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ValueError(
                "cannot inspect repository-local diff configuration"
                + (f": {detail}" if detail else "")
            )


def deterministic_git_diff_command(
    repository: Path,
    *arguments: str,
) -> list[str]:
    """Build a Git diff command whose output is independent of user config."""

    validate_git_evidence_repository(repository)
    return [
        "git",
        "--no-replace-objects",
        "-c",
        "diff.algorithm=myers",
        "-c",
        "diff.indentHeuristic=false",
        "-c",
        f"core.attributesFile={os.devnull}",
        "-c",
        "core.quotePath=true",
        "-c",
        "color.ui=false",
        "-c",
        "color.diff=false",
        "-c",
        "diff.noprefix=false",
        "-c",
        "diff.context=3",
        "-c",
        "diff.interHunkContext=0",
        "-c",
        "diff.submodule=short",
        "-c",
        "diff.ignoreSubmodules=none",
        "-c",
        "diff.renameLimit=1000",
        "-C",
        str(repository),
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--text",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--line-prefix=",
        "--unified=3",
        "--inter-hunk-context=0",
        "--submodule=short",
        "--ignore-submodules=none",
        "--output-indicator-new=+",
        "--output-indicator-old=-",
        "--output-indicator-context= ",
        "--abbrev=40",
        "--no-relative",
        "--find-renames=50%",
        *arguments,
    ]


def hermetic_git_environment(*, offline: bool = False) -> dict[str, str]:
    """Remove inherited Git redirection/config and pin evidence controls."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_GRAFT_FILE": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if offline:
        environment["GIT_NO_LAZY_FETCH"] = "1"
    return environment


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON from {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> str:
    completed = subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        env=dict(env) if env is not None else None,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{' '.join(command)}\n{detail}"
        )
    return completed.stdout


def require_full_sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase 40-character Git SHA")
    return value


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def sanitize_public_url(value: str) -> str:
    """Strip credentials and non-path URL components from public artifacts."""

    if "://" not in value:
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return "<redacted-invalid-url>"
    if not parsed.scheme or not parsed.netloc:
        return value
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError:
        return "<redacted-invalid-url>"
    if port is not None:
        hostname = f"{hostname}:{port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme, hostname, parsed.path, "", "")
    )


def url_has_private_components(value: str) -> bool:
    """Return whether an absolute URL carries userinfo, query, or fragment."""

    if "://" not in value:
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return True
    return bool(
        parsed.scheme
        and parsed.netloc
        and (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        )
    )


def _url_private_values(value: str) -> set[str]:
    if "://" not in value:
        return set()
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return set()
    if not parsed.scheme or not parsed.netloc:
        return set()
    values = {
        item
        for item in (
            parsed.password,
            parsed.fragment,
        )
        if item
    }
    for _, item in urllib.parse.parse_qsl(
        parsed.query,
        keep_blank_values=False,
    ):
        if item:
            values.add(item)
    return values


def public_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recursively redacted configuration for run manifests."""

    secret_fragments = (
        "authorization",
        "cookie",
        "key",
        "secret",
        "token",
        "password",
        "credential",
    )

    def redact(value: Any, key: str = "") -> Any:
        lowered = key.casefold()
        if any(fragment in lowered for fragment in secret_fragments):
            if lowered.endswith("_env") or lowered.endswith("env"):
                return value
            return "<redacted>" if value not in (None, "") else value
        if isinstance(value, Mapping):
            return {
                str(child_key): redact(child_value, str(child_key))
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str):
            return sanitize_public_url(value)
        return value

    return redact(dict(config))


def configured_secret_values(value: Any) -> set[str]:
    """Return literal strings removed by :func:`public_config`."""

    redacted = public_config(value) if isinstance(value, Mapping) else value
    result: set[str] = set()

    def collect_strings(item: Any) -> None:
        if isinstance(item, str) and item:
            result.add(item)
        elif isinstance(item, Mapping):
            for child in item.values():
                collect_strings(child)
        elif isinstance(item, list):
            for child in item:
                collect_strings(child)

    def collect(original: Any, safe: Any) -> None:
        if safe == "<redacted>":
            collect_strings(original)
            return
        if (
            isinstance(original, str)
            and isinstance(safe, str)
            and original != safe
        ):
            result.update(_url_private_values(original))
            return
        if isinstance(original, Mapping) and isinstance(safe, Mapping):
            for key, child in original.items():
                collect(child, safe.get(str(key)))
            return
        if isinstance(original, list) and isinstance(safe, list):
            for child, safe_child in zip(original, safe):
                collect(child, safe_child)

    collect(value, redacted)
    return result


def require_no_secret_values(
    value: Any,
    secrets: Sequence[str],
    *,
    context: str,
) -> None:
    """Refuse to persist an external artifact that echoes a credential."""

    candidates = {secret for secret in secrets if isinstance(secret, str) and secret}

    def contains(item: Any) -> bool:
        if isinstance(item, str):
            return any(secret in item for secret in candidates)
        if isinstance(item, Mapping):
            return any(
                contains(str(key)) or contains(child)
                for key, child in item.items()
            )
        if isinstance(item, (list, tuple)):
            return any(contains(child) for child in item)
        return False

    if candidates and contains(value):
        raise RuntimeError(
            f"{context} contains a credential-like value; refusing to persist it"
        )


def redact_secret_text(value: str, secrets: Sequence[str]) -> str:
    """Remove known literal credentials from an error before writing it."""

    result = value
    for secret in sorted(
        {item for item in secrets if isinstance(item, str) and item},
        key=len,
        reverse=True,
    ):
        result = result.replace(secret, "<redacted>")
    return result
