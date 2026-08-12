from pathlib import Path
from typing import List, Optional, Generator, Mapping
import logging
import re

from llama_index.core.schema import Document
from ..utils.utils import detect_language_from_path, should_exclude_file, should_include_file, clean_archive_path
from ..models.config import RAGConfig
from .source_tree import (
    RepositorySourceTreeError,
    iter_repository_regular_file_paths,
    read_repository_file_bytes,
)

logger = logging.getLogger(__name__)

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

    def iter_repository_files(
        self,
        repo_path: Path,
        extra_include_patterns: Optional[List[str]] = None,
        extra_exclude_patterns: Optional[List[str]] = None,
        expected_file_sha256: Optional[Mapping[str, str]] = None,
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
                )
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
                )
                text = content.decode("utf-8")

                if not text or not text.strip():
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

            # Clean archive root prefix from path (e.g., 'owner-repo-commit/src/file.php' -> 'src/file.php')
            clean_path = clean_archive_path(relative_path_str)

            metadata = {
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "path": clean_path,
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

    def load_specific_files(
        self,
        file_paths: List[Path],
        repo_base: Path,
        workspace: str,
        project: str,
        branch: str,
        commit: str
    ) -> List[Document]:
        """Load specific files (for incremental updates)"""
        documents = []

        for relative_file_path in file_paths:
            # file_paths contains relative paths, join with repo_base to get full path
            full_path = repo_base / relative_file_path
            relative_path = str(relative_file_path)
            
            if should_exclude_file(relative_path, self.config.excluded_patterns):
                logger.debug(f"Excluding file: {relative_path}")
                continue

            if _is_generated_asset(full_path.name):
                logger.debug(f"Generated asset, skipping: {relative_path}")
                continue

            try:
                content = read_repository_file_bytes(
                    repo_base,
                    relative_file_path,
                )
                if len(content) > self.config.max_file_size_bytes:
                    logger.warning(f"File too large, skipping: {relative_path}")
                    continue
                text = _decode_text(content)
                if text is None:
                    logger.debug(f"Binary file, skipping: {relative_path}")
                    continue
            except Exception as e:
                logger.error(f"Error reading file {relative_path}: {e}")
                continue

            language = detect_language_from_path(str(full_path))
            filetype = full_path.suffix.lstrip('.')

            # Incremental callers already provide repository-relative VCS
            # paths.  Archive-root heuristics would corrupt legitimate module
            # directories whose names happen to resemble archive prefixes.
            clean_path = relative_path.replace('\\', '/')

            metadata = {
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "path": clean_path,
                "commit": commit,
                "language": language,
                "filetype": filetype,
            }

            doc = Document(
                text=text,
                metadata=metadata
                # Don't set id_ - let LlamaIndex/Qdrant generate it automatically
            )

            documents.append(doc)
            logger.debug(f"Loaded document: {clean_path}")

        return documents
