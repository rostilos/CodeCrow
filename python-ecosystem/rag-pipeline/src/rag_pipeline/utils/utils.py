from pathlib import Path
from typing import Dict
import codecs
import functools
from fnmatch import fnmatchcase
import re


LANGUAGE_MAP: Dict[str, str] = {
    '.py': 'python',
    '.js': 'javascript',
    '.jsx': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.java': 'java',
    '.kt': 'kotlin',
    '.phtml': 'php',
    '.php': 'php',
    '.go': 'go',
    '.rs': 'rust',
    '.cpp': 'cpp',
    '.cc': 'cpp',
    '.cxx': 'cpp',
    '.c': 'c',
    '.h': 'c',
    '.hpp': 'cpp',
    '.rb': 'ruby',
    '.cs': 'csharp',
    '.swift': 'swift',
    '.m': 'objective-c',
    '.scala': 'scala',
    '.sh': 'bash',
    '.bash': 'bash',
    '.zsh': 'zsh',
    '.sql': 'sql',
    '.r': 'r',
    '.R': 'r',
    '.lua': 'lua',
    '.pl': 'perl',
    '.md': 'markdown',
    '.rst': 'rst',
    '.txt': 'text',
    '.json': 'json',
    '.xml': 'xml',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.toml': 'toml',
    '.ini': 'ini',
    '.conf': 'config',
    '.html': 'html',
    '.htm': 'html',
    '.css': 'css',
    '.scss': 'scss',
    '.sass': 'sass',
    '.vue': 'vue',
    '.svelte': 'svelte',
}


def detect_language_from_path(path: str) -> str:
    """Detect programming language from file extension"""
    ext = Path(path).suffix.lower()
    return LANGUAGE_MAP.get(ext, 'text')


def make_namespace(workspace: str, project: str, branch: str) -> str:
    """Create a safe namespace identifier for indexing (includes branch)"""
    return f"{workspace}__{project}__{branch}".replace("/", "_").replace(".", "_").lower()


def _normalize_repository_glob_value(value: str) -> str:
    """Normalize one repository-relative path or glob without hiding dotfiles."""
    normalized = str(value or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return re.sub(r"/{2,}", "/", normalized).lstrip("/")


def _repository_glob_matches(path: str, pattern: str) -> bool:
    """Match one repository glob without allowing ``*`` to cross ``/``.

    A globstar is a complete ``**`` segment and consumes zero or more path
    segments. Therefore suffixes after a globstar remain mandatory instead of
    being discarded as they were by the previous prefix-only implementation.
    """
    normalized_path = _normalize_repository_glob_value(path)
    normalized_pattern = _normalize_repository_glob_value(pattern)
    if not normalized_path or not normalized_pattern:
        return False

    directory_prefix = normalized_pattern.endswith("/")
    normalized_pattern = normalized_pattern.strip("/")
    path_parts = tuple(part for part in normalized_path.split("/") if part != ".")

    # Preserve the established basename-anywhere meaning of slashless patterns.
    if "/" not in normalized_pattern and not directory_prefix:
        return bool(path_parts) and fnmatchcase(path_parts[-1], normalized_pattern)

    pattern_parts = tuple(
        part for part in normalized_pattern.split("/") if part != "."
    )
    if directory_prefix:
        pattern_parts += ("**",)

    @functools.lru_cache(maxsize=None)
    def match(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        token = pattern_parts[pattern_index]
        if token == "**":
            return match(path_index, pattern_index + 1) or (
                path_index < len(path_parts)
                and match(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatchcase(path_parts[path_index], token)
            and match(path_index + 1, pattern_index + 1)
        )

    return match(0, 0)


def _repository_paths_to_check(path: str) -> tuple[str, ...]:
    normalized = _normalize_repository_glob_value(path)
    candidates = [normalized]
    if "/" in normalized:
        # Preserve archive-root compatibility used by VCS archive ingestion.
        candidates.append(normalized.split("/", 1)[1])
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def should_include_file(path: str, include_patterns: list[str]) -> bool:
    """Check if file matches at least one inclusion pattern.
    
    Uses the same pattern matching logic as should_exclude_file.
    Returns True if the file matches ANY of the provided patterns.
    If include_patterns is empty, returns True (no filtering).
    
    Supports:
    - Exact directory matches: 'src/' matches 'src/file.php'
    - Single wildcard: 'src/*' matches 'src/file.php' but not 'src/sub/file.php'
    - Double wildcard (globstar): 'src/**' matches 'src/file.php' and 'src/sub/file.php'
    - File patterns: '*.py' matches any Python file
    """
    if not include_patterns:
        return True
    
    return any(
        _repository_glob_matches(candidate, pattern)
        for candidate in _repository_paths_to_check(path)
        for pattern in include_patterns
    )


def should_exclude_file(path: str, excluded_patterns: list[str]) -> bool:
    """Check if file should be excluded based on patterns.
    
    Supports:
    - Exact directory matches: 'vendor/' matches 'vendor/file.php'
    - Single wildcard: 'vendor/*' matches 'vendor/file.php' but not 'vendor/sub/file.php'
    - Double wildcard (globstar): 'vendor/**' matches 'vendor/file.php' and 'vendor/sub/file.php'
    - File patterns: '*.min.js' matches any file ending with .min.js
    
    Note: Also handles paths with archive root prefix (e.g., 'repo-commit123/lib/file.php' 
    will match pattern 'lib/**')
    """
    return any(
        _repository_glob_matches(candidate, pattern)
        for candidate in _repository_paths_to_check(path)
        for pattern in excluded_patterns
    )


def is_binary_file(file_path: Path) -> bool:
    """Return whether a file cannot be consumed as deterministic UTF-8 text.

    A NUL-byte probe alone is insufficient for formats such as PDF: their
    headers can contain no NUL bytes while still containing binary/non-UTF-8
    data. Repository admission and document loading must agree, so validate
    the complete file incrementally instead of allowing a later strict decode
    to abort an otherwise valid index generation.
    """
    try:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        with open(file_path, "rb") as file:
            while chunk := file.read(8192):
                if b"\0" in chunk:
                    return True
                decoder.decode(chunk, final=False)
            decoder.decode(b"", final=True)
        return False
    except (UnicodeDecodeError, OSError):
        return True
    except Exception:
        # Admission is fail-closed when the file cannot be inspected.
        return True
