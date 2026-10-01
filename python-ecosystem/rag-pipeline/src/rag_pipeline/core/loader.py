from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Generator, List, Mapping, Optional
import logging
import re

from .documents import Document
from ..utils.utils import detect_language_from_path, should_exclude_file, should_include_file
from ..models.config import RAGConfig
from .source_tree import (
    RepositoryFileSizeLimitExceeded,
    RepositorySourceTreeError,
    iter_repository_regular_file_paths,
    read_repository_file_bytes,
)

logger = logging.getLogger(__name__)

REPOSITORY_FILE_SIZE_LIMIT_CODE = "repository_file_size_limit_exceeded"


@dataclass(frozen=True)
class RepositoryFileSkip:
    """Observable whole-file omission emitted by the repository loader."""

    code: str
    path: str
    message: str
    size_bytes: int | None = None
    max_file_size_bytes: int | None = None


RepositoryFileSkipCallback = Callable[[RepositoryFileSkip], None]

# Detects build-tool-generated assets with content hashes in their filenames.
# Examples: index-D25HpPdh.js, main.a1b2c3d4.css, vendor~lib.9fca3e.mjs
_HASH_ASSET_PATTERN = re.compile(
    r'[._-]([a-zA-Z0-9]{7,})\.(js|css|mjs|cjs)$'
)


def _is_generated_asset(filename: str) -> bool:
    """Detect build-tool-generated asset files with content hashes in their names.

    Bundlers (webpack, Vite, Rollup, esbuild) produce files like:
      index-D25HpPdh.js, main.a1b2c3d4.css, vendor~lib.9fca3e.mjs

    These files are minified/bundled output and should not be indexed.
    Detection heuristic: filename contains a 7+ char alphanumeric segment
    (preceded by a separator) with BOTH letters AND digits (a real hash),
    followed by a code asset extension.
    """
    match = _HASH_ASSET_PATTERN.search(filename)
    if not match:
        return False
    hash_part = match.group(1)
    has_letter = any(c.isalpha() for c in hash_part)
    has_digit = any(c.isdigit() for c in hash_part)
    return has_letter and has_digit


def _decode_text(content: bytes) -> str | None:
    if b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


class DocumentLoader:
    """Load repository files as documents"""

    def __init__(self, config: RAGConfig):
        self.config = config

    @staticmethod
    def _report_size_limit_skip(
        exception: RepositoryFileSizeLimitExceeded,
        on_skip: RepositoryFileSkipCallback | None,
        *,
        phase: str,
    ) -> None:
        diagnostic = RepositoryFileSkip(
            code=REPOSITORY_FILE_SIZE_LIMIT_CODE,
            path=exception.relative_path,
            message=(
                "Repository file exceeds the configured indexing ceiling and "
                "was omitted as a whole without truncation."
            ),
            size_bytes=exception.size_bytes,
            max_file_size_bytes=exception.max_size_bytes,
        )
        logger.warning(
            "Repository file exceeds the configured indexing ceiling; "
            "skipping it without truncation: code=%s phase=%s path=%s "
            "bytes=%d max_bytes=%d",
            diagnostic.code,
            phase,
            diagnostic.path,
            diagnostic.size_bytes,
            diagnostic.max_file_size_bytes,
        )
        if on_skip is None:
            return
        try:
            on_skip(diagnostic)
        except Exception as callback_exception:
            # Skip reporting is observability enrichment. It must not turn an
            # otherwise recoverable oversized source file into an index failure.
            logger.warning(
                "Repository file skip callback failed for %s: %s",
                diagnostic.path,
                callback_exception,
            )

    def iter_repository_files(
        self,
        repo_path: Path,
        extra_include_patterns: Optional[List[str]] = None,
        extra_exclude_patterns: Optional[List[str]] = None,
        expected_file_sha256: Optional[Mapping[str, str]] = None,
        on_skip: RepositoryFileSkipCallback | None = None,
    ) -> Generator[Path, None, None]:
        """Iterate over repository files without loading them into memory.
        
        Yields relative file paths that should be indexed.
        This is memory-efficient as it doesn't load file contents.
        
        Filtering order: inclusion patterns first, then exclusion patterns.
        If include patterns are provided and non-empty, only files matching
        at least one include pattern are considered. Then exclusion patterns
        are applied to further filter the results.
        
        Args:
            repo_path: Path to the repository
            extra_include_patterns: Patterns to include (if non-empty, only matching files pass)
            extra_exclude_patterns: Additional patterns to exclude
            
        Yields:
            Relative file paths suitable for indexing
        """
        if not repo_path.exists():
            logger.error(f"Repository path does not exist: {repo_path}")
            return

        # Combine default exclude patterns with project-specific ones
        exclude_patterns = list(self.config.excluded_patterns)
        if extra_exclude_patterns:
            exclude_patterns.extend(extra_exclude_patterns)

        # Include patterns (project-specific only, no defaults)
        include_patterns = extra_include_patterns if extra_include_patterns else []

        candidates = (
            (repo_path / Path(path) for path in sorted(expected_file_sha256))
            if expected_file_sha256 is not None
            else (
                repo_path / path
                for path in iter_repository_regular_file_paths(repo_path)
            )
        )
        total_entries = 0
        yielded_count = 0
        for file_path in candidates:
            total_entries += 1
            relative_path = file_path.relative_to(repo_path)
            relative_path_str = relative_path.as_posix()

            # Step 1: Apply inclusion filter first
            # If include patterns are specified, only files matching at least one pattern pass
            if include_patterns and not should_include_file(relative_path_str, include_patterns):
                continue

            # Step 2: Apply exclusion filter
            if should_exclude_file(relative_path_str, exclude_patterns):
                continue

            expected_digest = (
                expected_file_sha256.get(relative_path_str)
                if expected_file_sha256 is not None
                else None
            )
            try:
                content = read_repository_file_bytes(
                    repo_path,
                    relative_path,
                    expected_sha256=expected_digest,
                    max_size_bytes=self.config.max_file_size_bytes,
                )
            except RepositoryFileSizeLimitExceeded as exception:
                self._report_size_limit_skip(
                    exception,
                    on_skip,
                    phase="scan",
                )
                continue
            except RepositorySourceTreeError:
                if expected_file_sha256 is not None:
                    raise
                logger.warning(
                    "Cannot safely inspect repository file, skipping: %s",
                    relative_path_str,
                )
                continue

            if len(content) > self.config.max_file_size_bytes:
                continue

            if _decode_text(content) is None:
                continue

            # Skip build-tool-generated assets with content hashes
            if _is_generated_asset(file_path.name):
                continue

            yielded_count += 1
            yield relative_path

        logger.info(f"Scanned {total_entries} entries in {repo_path}, yielded {yielded_count} files after filtering.")

    def load_file_batch(
        self,
        file_paths: List[Path],
        repo_base: Path,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        strict: bool = False,
        expected_file_sha256: Optional[Mapping[str, str]] = None,
        on_skip: RepositoryFileSkipCallback | None = None,
    ) -> List[Document]:
        """Load a batch of files as Documents.
        
        This is more memory-efficient than loading all files at once.
        Used by the streaming indexing pipeline.
        
        Args:
            file_paths: List of relative file paths to load
            repo_base: Base path of the repository
            workspace: Workspace identifier
            project: Project identifier
            branch: Branch name
            commit: Commit hash
            
        Returns:
            List of Document objects
        """
        documents = []

        for relative_path in file_paths:
            full_path = repo_base / relative_path
            relative_path_str = str(relative_path)

            # Skip build-tool-generated assets
            if _is_generated_asset(full_path.name):
                continue

            try:
                expected_digest = None
                if expected_file_sha256 is not None:
                    expected_digest = expected_file_sha256.get(
                        Path(relative_path).as_posix()
                    )
                    if expected_digest is None:
                        raise RepositorySourceTreeError(
                            "repository source file was not present in the "
                            f"attested tree: {relative_path_str}"
                        )
                content = read_repository_file_bytes(
                    repo_base,
                    relative_path,
                    expected_sha256=expected_digest,
                    max_size_bytes=self.config.max_file_size_bytes,
                )
                text = content.decode("utf-8")

                if not text or not text.strip():
                    continue

            except RepositoryFileSizeLimitExceeded as exception:
                self._report_size_limit_skip(
                    exception,
                    on_skip,
                    phase="load",
                )
                continue
            except UnicodeDecodeError as exception:
                logger.warning(f"Cannot decode file, skipping: {relative_path_str}")
                if strict:
                    raise RuntimeError(
                        f"Cannot decode repository file selected for indexing: {relative_path_str}"
                    ) from exception
                continue
            except Exception as e:
                logger.error(f"Error reading file {relative_path_str}: {e}")
                if strict:
                    raise RuntimeError(
                        f"Cannot read repository file selected for indexing: {relative_path_str}"
                    ) from e
                continue

            language = detect_language_from_path(str(full_path))
            filetype = full_path.suffix.lstrip('.')

            metadata = {
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "path": Path(relative_path).as_posix(),
                "commit": commit,
                "language": language,
                "filetype": filetype,
            }

            doc = Document(text=text, metadata=metadata)
            documents.append(doc)

        return documents

    def load_from_directory(
        self,
        repo_path: Path,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        extra_exclude_patterns: Optional[List[str]] = None
    ) -> List[Document]:
        """Load all files from a repository directory
        
        Args:
            repo_path: Path to the repository
            workspace: Workspace identifier
            project: Project identifier
            branch: Branch name
            commit: Commit hash
            extra_exclude_patterns: Additional patterns to exclude (from project config)
        """
        file_paths = list(
            self.iter_repository_files(
                repo_path,
                extra_exclude_patterns=extra_exclude_patterns,
            )
        )
        return self.load_file_batch(
            file_paths,
            repo_path,
            workspace,
            project,
            branch,
            commit,
        )
