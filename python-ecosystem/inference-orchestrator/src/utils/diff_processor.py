"""Parse the full provider diff into changed-code hunks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum


_FILE_HEADER = re.compile(
    r'^diff --git (?P<old>"(?:[^"\\]|\\.)*"|a/.+?) '
    r'(?P<new>"(?:[^"\\]|\\.)*"|b/.+)$'
)
_HUNK_HEADER = re.compile(
    r"^@@\s+-(?P<old>\d+)(?:,(?P<old_count>\d+))?\s+"
    r"\+(?P<new>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)


class HunkDisposition(str, Enum):
    REVIEWABLE = "reviewable"
    DELETED = "deleted"
    BINARY = "binary"
    GITLINK = "gitlink"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class DiffHunk:
    id: str
    path: str
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    content: str
    disposition: HunkDisposition


@dataclass
class DiffFile:
    path: str
    old_path: str | None = None
    content: str = ""
    hunks: list[DiffHunk] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    deleted: bool = False
    is_binary: bool = False
    is_gitlink: bool = False


@dataclass
class ProcessedDiff:
    files: list[DiffFile]


def _git_path(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        try:
            value = json.loads(value)
        except ValueError:
            value = value[1:-1]
    if value.startswith(("a/", "b/")):
        return value[2:]
    return value


def _finish_file(file: DiffFile, lines: list[str]) -> DiffFile:
    file.content = "\n".join(lines)
    current: list[str] = []

    def finish_hunk() -> None:
        if not current:
            return
        header = current[0]
        match = _HUNK_HEADER.match(header)
        disposition = (
            HunkDisposition.BINARY if file.is_binary
            else HunkDisposition.GITLINK if file.is_gitlink
            else HunkDisposition.MALFORMED if match is None
            else HunkDisposition.DELETED if file.deleted
            else HunkDisposition.REVIEWABLE
        )
        content = "\n".join(current)
        identifier = hashlib.sha256(
            f"{file.path}\0{content}".encode("utf-8")
        ).hexdigest()
        file.hunks.append(DiffHunk(
            id=f"sha256:{identifier}", path=file.path, header=header,
            old_start=int(match.group("old")) if match else 0,
            old_count=int(match.group("old_count") or 1) if match else 0,
            new_start=int(match.group("new")) if match else 0,
            new_count=int(match.group("new_count") or 1) if match else 0,
            content=content, disposition=disposition,
        ))

    for line in lines:
        if line.startswith("@@"):
            finish_hunk()
            current = [line]
        elif current:
            current.append(line)
    finish_hunk()
    return file


def process_raw_diff(raw_diff: str | None) -> ProcessedDiff:
    """Keep every acquired hunk; unsupported sections remain visible to the caller."""
    if not raw_diff:
        return ProcessedDiff([])
    files: list[DiffFile] = []
    current: DiffFile | None = None
    section: list[str] = []
    for line in raw_diff.splitlines():
        if line.startswith("diff --git "):
            if current is not None:
                files.append(_finish_file(current, section))
            match = _FILE_HEADER.match(line)
            current = DiffFile(path=_git_path(match.group("new"))) if match else None
            if current is not None and match:
                old_path = _git_path(match.group("old"))
                if old_path != current.path:
                    current.old_path = old_path
            section = [line]
            continue
        if current is None:
            continue
        section.append(line)
        if line.startswith("+++ ") and line[4:] != "/dev/null":
            current.path = _git_path(line[4:])
        elif line.startswith("deleted file mode"):
            current.deleted = True
            current.is_gitlink = line.endswith("160000")
        elif line.startswith(("new file mode", "new mode ")):
            current.is_gitlink = line.endswith("160000")
        elif line.startswith("index ") and line.endswith(" 160000"):
            current.is_gitlink = True
        elif line.startswith(("Binary files", "GIT binary patch")):
            current.is_binary = True
        elif line.startswith("+") and not line.startswith("+++"):
            current.additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            current.deletions += 1
    if current is not None:
        files.append(_finish_file(current, section))
    return ProcessedDiff(files)
