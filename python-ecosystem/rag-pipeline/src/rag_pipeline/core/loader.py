from pathlib import Path
from typing import List, Optional, Generator
import logging

from llama_index.core.schema import Document
from ..utils.utils import detect_language_from_path, should_exclude_file, should_include_file, is_binary_file, clean_archive_path
from ..models.config import RAGConfig

logger = logging.getLogger(__name__)


class DocumentLoader:
    """Load repository files as documents"""

    def __init__(self, config: RAGConfig):
        self.config = config

    def iter_repository_files(
        self,
        repo_path: Path,
        extra_include_patterns: Optional[List[str]] = None,
        extra_exclude_patterns: Optional[List[str]] = None
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

        for file_path in repo_path.rglob("*"):
            if not file_path.is_file():
                continue

            relative_path = file_path.relative_to(repo_path)
            relative_path_str = str(relative_path)

            # Step 1: Apply inclusion filter first
            # If include patterns are specified, only files matching at least one pattern pass
            if include_patterns and not should_include_file(relative_path_str, include_patterns):
                continue

            # Step 2: Apply exclusion filter
            if should_exclude_file(relative_path_str, exclude_patterns):
                continue

            if file_path.stat().st_size > self.config.max_file_size_bytes:
                continue

            if is_binary_file(file_path):
                continue

            yield relative_path

    def load_file_batch(
        self,
        file_paths: List[Path],
        repo_base: Path,
        workspace: str,
        project: str,
        branch: str,
        commit: str
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

            try:
                text = full_path.read_text(encoding="utf-8")

                if not text or not text.strip():
                    continue

            except UnicodeDecodeError:
                logger.warning(f"Cannot decode file, skipping: {relative_path_str}")
                continue
            except Exception as e:
                logger.error(f"Error reading file {relative_path_str}: {e}")
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
        documents = []

        if not repo_path.exists():
            logger.error(f"Repository path does not exist: {repo_path}")
            return documents

        # Combine default exclude patterns with project-specific ones
        exclude_patterns = list(self.config.excluded_patterns)
        if extra_exclude_patterns:
            exclude_patterns.extend(extra_exclude_patterns)
            logger.info(f"Using {len(extra_exclude_patterns)} additional exclude patterns from project config: {extra_exclude_patterns}")

        excluded_count = 0
        for file_path in repo_path.rglob("*"):
            if not file_path.is_file():
                continue

            relative_path = str(file_path.relative_to(repo_path))

            if should_exclude_file(relative_path, exclude_patterns):
                logger.debug(f"Excluding file: {relative_path}")
                excluded_count += 1
                continue

            if file_path.stat().st_size > self.config.max_file_size_bytes:
                logger.warning(f"File too large, skipping: {relative_path}")
                continue

            if is_binary_file(file_path):
                logger.debug(f"Binary file, skipping: {relative_path}")
                continue

            try:
                text = file_path.read_text(encoding="utf-8")

                # Skip empty files
                if not text or not text.strip():
                    logger.debug(f"Empty file, skipping: {relative_path}")
                    continue

            except UnicodeDecodeError:
                logger.warning(f"Cannot decode file, skipping: {relative_path}")
                continue
            except Exception as e:
                logger.error(f"Error reading file {relative_path}: {e}")
                continue

            language = detect_language_from_path(str(file_path))
            filetype = file_path.suffix.lstrip('.')

            # Clean archive root prefix from path
            clean_path = clean_archive_path(relative_path)

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
            logger.debug(f"Loaded document: {clean_path} ({language})")

        logger.info(f"Loaded {len(documents)} documents from {repo_path} (excluded {excluded_count} files by patterns)")
        return documents

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
            
            if not full_path.exists():
                logger.warning(f"File does not exist: {full_path} (relative: {relative_path})")
                continue

            if not full_path.is_file():
                continue

            if should_exclude_file(relative_path, self.config.excluded_patterns):
                logger.debug(f"Excluding file: {relative_path}")
                continue

            if full_path.stat().st_size > self.config.max_file_size_bytes:
                logger.warning(f"File too large, skipping: {relative_path}")
                continue

            if is_binary_file(full_path):
                logger.debug(f"Binary file, skipping: {relative_path}")
                continue

            try:
                text = full_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"Error reading file {relative_path}: {e}")
                continue

            language = detect_language_from_path(str(full_path))
            filetype = full_path.suffix.lstrip('.')

            # Clean archive root prefix from path
            clean_path = clean_archive_path(relative_path)

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

