"""
Utility for parsing unified diff format and extracting changed files and code snippets.
"""
import re
from typing import List, Dict, Set
from dataclasses import dataclass


@dataclass
class DiffFileInfo:
    """Information about a changed file from diff."""
    path: str
    change_type: str  # 'added', 'modified', 'deleted', 'renamed'
    added_lines: List[str]
    removed_lines: List[str]
    code_snippets: List[str]  # Representative code snippets (function signatures, etc.)


class DiffParser:
    """Parse unified diff format to extract relevant information for RAG queries."""

    # Patterns for detecting function/method signatures across languages
    FUNCTION_PATTERNS = [
        r'^\s*(public|private|protected)?\s*(static)?\s*\w+\s+\w+\s*\([^)]*\)',  # Java/C/C++
        r'^\s*def\s+\w+\s*\([^)]*\)',  # Python
        r'^\s*function\s+\w+\s*\([^)]*\)',  # JavaScript
        r'^\s*const\s+\w+\s*=\s*\([^)]*\)\s*=>',  # Arrow functions
        r'^\s*class\s+\w+',  # Class definitions
        r'^\s*interface\s+\w+',  # Interface definitions
    ]

    @staticmethod
    def parse_diff(diff_content: str, max_snippets_per_file: int = 3) -> List[DiffFileInfo]:
        """
        Parse unified diff and extract file information.

        Args:
            diff_content: Unified diff string
            max_snippets_per_file: Max code snippets to extract per file

        Returns:
            List of DiffFileInfo objects
        """
        files = []
        current_file = None
        added_lines = []
        removed_lines = []

        for line in diff_content.split('\n'):
            # New file diff section
            if line.startswith('diff --git'):
                # Save previous file
                if current_file:
                    current_file.added_lines = added_lines.copy()
                    current_file.removed_lines = removed_lines.copy()
                    current_file.code_snippets = DiffParser._extract_snippets(
                        added_lines, max_snippets_per_file
                    )
                    files.append(current_file)

                # Extract file path
                match = re.search(r'diff --git a/(.*?) b/(.*)', line)
                if match:
                    path = match.group(2)
                    current_file = DiffFileInfo(
                        path=path,
                        change_type='modified',
                        added_lines=[],
                        removed_lines=[],
                        code_snippets=[]
                    )
                    added_lines = []
                    removed_lines = []

            # Detect file change type
            elif current_file:
                if line.startswith('new file mode'):
                    current_file.change_type = 'added'
                elif line.startswith('deleted file mode'):
                    current_file.change_type = 'deleted'
                elif line.startswith('rename from'):
                    current_file.change_type = 'renamed'

                # Extract added/removed lines
                elif line.startswith('+') and not line.startswith('+++'):
                    added_lines.append(line[1:].strip())
                elif line.startswith('-') and not line.startswith('---'):
                    removed_lines.append(line[1:].strip())

        # Save last file
        if current_file:
            current_file.added_lines = added_lines.copy()
            current_file.removed_lines = removed_lines.copy()
            current_file.code_snippets = DiffParser._extract_snippets(
                added_lines, max_snippets_per_file
            )
            files.append(current_file)

        return files

    @staticmethod
    def _extract_snippets(lines: List[str], max_snippets: int) -> List[str]:
        """
        Extract representative code snippets from changed lines.

        Prioritizes function/class definitions and meaningful code changes.
        """
        snippets = []

        for line in lines:
            if not line or line.startswith('//') or line.startswith('#'):
                continue

            # Check if it's a function/class signature
            is_significant = any(
                re.match(pattern, line)
                for pattern in DiffParser.FUNCTION_PATTERNS
            )

            if is_significant:
                snippets.append(line.strip())
                if len(snippets) >= max_snippets:
                    break

        # If no significant lines found, take first non-empty lines
        if not snippets:
            for line in lines:
                if line and len(line.strip()) > 10:
                    snippets.append(line.strip()[:200])  # Limit length
                    if len(snippets) >= max_snippets:
                        break

        return snippets

    @staticmethod
    def get_changed_file_paths(diff_files: List[DiffFileInfo]) -> List[str]:
        """Extract list of changed file paths."""
        return [f.path for f in diff_files if f.change_type != 'deleted']

    @staticmethod
    def build_rag_query_from_diff(
        diff_files: List[DiffFileInfo],
        pr_description: str = None,
        pr_title: str = None,
        max_query_length: int = 500
    ) -> str:
        """
        Build a rich query string for RAG semantic search.

        Combines PR description, file paths, and code snippets intelligently.
        """
        query_parts = []

        # 1. PR title and description (highest priority for semantic intent)
        if pr_title:
            query_parts.append(pr_title)
        if pr_description:
            # Truncate description to keep query manageable
            desc = pr_description[:300] if len(pr_description) > 300 else pr_description
            query_parts.append(desc)

        # 2. Code snippets (function signatures, class names)
        all_snippets = []
        for diff_file in diff_files[:10]:  # Limit to first 10 files
            all_snippets.extend(diff_file.code_snippets[:2])  # Top 2 per file

        if all_snippets:
            query_parts.append("Code changes: " + " | ".join(all_snippets[:8]))

        # 3. File paths (for context)
        file_paths = [f.path for f in diff_files[:15]]  # Limit to 15 files
        if file_paths:
            query_parts.append("Files: " + ", ".join(file_paths))

        # Join and truncate to max length
        query = " ".join(query_parts)
        if len(query) > max_query_length:
            query = query[:max_query_length] + "..."

        return query

