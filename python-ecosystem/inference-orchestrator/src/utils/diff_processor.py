"""
Diff Processing Utilities for Code Review.

Handles parsing, filtering, and size-bounding of PR diffs.
Applies same rules as MCP server LargeContentFilter (25KB file limit).
"""

import os
import re
import logging
from typing import List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# Constants from environment or defaults (matching MCP server LargeContentFilter)
# See: org.rostilos.codecrow.mcp.filter.LargeContentFilter
DEFAULT_FILE_SIZE_THRESHOLD_BYTES = 25 * 1024  # 25KB - same as LargeContentFilter.DEFAULT_SIZE_THRESHOLD_BYTES

MAX_FILE_SIZE_BYTES = int(os.environ.get("DIFF_MAX_FILE_SIZE", str(DEFAULT_FILE_SIZE_THRESHOLD_BYTES)))
MAX_FILES_IN_DIFF = int(os.environ.get("DIFF_MAX_FILES", "400"))  # Maximum files to process
MAX_DIFF_SIZE_BYTES = int(os.environ.get("DIFF_MAX_TOTAL_SIZE", "1000000"))  # 1MB total diff size
MAX_LINES_PER_FILE = int(os.environ.get("DIFF_MAX_LINES_PER_FILE", "3000"))  # Maximum lines per file

# Placeholder message matching LargeContentFilter.FILTERED_PLACEHOLDER
FILTERED_PLACEHOLDER = "[CodeCrow Filter: file too large (>25KB), omitted from analysis]"
FILTERED_DIFF_TEMPLATE = """diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
[CodeCrow Filter: file diff too large (>{threshold_kb}KB), omitted from analysis. File type: {diff_type}]
"""

def summarize_oversized_diff(diff_content: str, path: str, max_changed_lines: int = 80) -> str:
    """
    Generate a compact summary for an oversized diff instead of omitting entirely.

    The summary is intentionally neutral: it preserves diff statistics, hunk
    headers, and representative raw changed lines. It does not try to recognize
    language-specific signatures or infer what code constructs matter.
    """
    added_lines = 0
    removed_lines = 0
    hunk_headers = []
    changed_lines = []

    for line in diff_content.split('\n'):
        if line.startswith('+') and not line.startswith('+++'):
            added_lines += 1
            if len(changed_lines) < max_changed_lines:
                changed_lines.append(line)
        elif line.startswith('-') and not line.startswith('---'):
            removed_lines += 1
            if len(changed_lines) < max_changed_lines:
                changed_lines.append(line)
        # Capture hunk headers — they often contain the enclosing function name
        elif line.startswith('@@'):
            hunk_headers.append(line.strip())

    # Deduplicate while preserving order
    hunk_headers = list(dict.fromkeys(hunk_headers))[:20]
    changed_lines = list(dict.fromkeys(changed_lines))[:max_changed_lines]

    parts = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        f"[CodeCrow Summary: diff too large for full inclusion — summary below]",
        f"",
        f"Change statistics: +{added_lines} lines added, -{removed_lines} lines removed",
    ]

    if hunk_headers:
        parts.append(f"\nAffected code regions ({len(hunk_headers)} hunks):")
        for hh in hunk_headers:
            parts.append(f"  {hh}")

    if changed_lines:
        parts.append(f"\nRepresentative changed lines ({len(changed_lines)} shown):")
        parts.extend(changed_lines)
    else:
        parts.append("\n(No changed lines found in diff summary)")

    return "\n".join(parts)


class DiffChangeType(Enum):
    """Type of change in diff."""
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    BINARY = "binary"


@dataclass
class DiffFile:
    """Represents a single file in the diff."""
    path: str
    change_type: DiffChangeType
    old_path: Optional[str] = None  # For renamed files
    additions: int = 0
    deletions: int = 0
    content: str = ""  # Diff content (unified diff format)
    full_content: Optional[str] = None  # Full file content (populated separately if needed)
    hunks: List[str] = field(default_factory=list)
    is_binary: bool = False
    is_skipped: bool = False
    skip_reason: Optional[str] = None
    
    @property
    def total_changes(self) -> int:
        return self.additions + self.deletions
    
    @property
    def size_bytes(self) -> int:
        return len(self.content.encode('utf-8'))


@dataclass
class ProcessedDiff:
    """Result of processing a raw diff."""
    files: List[DiffFile]
    total_additions: int = 0
    total_deletions: int = 0
    total_files: int = 0
    skipped_files: int = 0
    truncated: bool = False
    truncation_reason: Optional[str] = None
    original_size_bytes: int = 0
    processed_size_bytes: int = 0
    refactoring_signals: List[str] = field(default_factory=list)
    
    def get_included_files(self) -> List[DiffFile]:
        """Get files that were not skipped."""
        return [f for f in self.files if not f.is_skipped]
    
    def get_skipped_files(self) -> List[DiffFile]:
        """Get files that were skipped."""
        return [f for f in self.files if f.is_skipped]
    
    def to_unified_diff(self) -> str:
        """Reconstruct unified diff from included files."""
        parts = []
        for f in self.get_included_files():
            parts.append(f.content)
        return "\n".join(parts)


class DiffProcessor:
    """
    Processes raw diff content with same rules as MCP server.
    
    Features:
    - Parses unified diff format
    - Applies file size limits
    - Applies file count limits
    - Skips binary files
    """
    
    def __init__(
        self,
        max_file_size: int = MAX_FILE_SIZE_BYTES,
        max_files: int = MAX_FILES_IN_DIFF,
        max_total_size: int = MAX_DIFF_SIZE_BYTES,
        max_lines_per_file: int = MAX_LINES_PER_FILE
    ):
        self.max_file_size = max_file_size
        self.max_files = max_files
        self.max_total_size = max_total_size
        self.max_lines_per_file = max_lines_per_file
    
    def process(self, raw_diff: str) -> ProcessedDiff:
        """
        Process raw diff and apply all filtering rules.

        Args:
            raw_diff: Raw unified diff content
            
        Returns:
            ProcessedDiff with filtered and prioritized files
        """
        if not raw_diff:
            return ProcessedDiff(files=[], original_size_bytes=0)
        
        original_size = len(raw_diff.encode('utf-8'))
        
        # Parse diff into files
        files = self._parse_diff(raw_diff)
        
        # Apply skip rules
        for f in files:
            if self._should_skip(f):
                f.is_skipped = True
        
        # Keep reviewable files before mechanically skipped files while preserving
        # the original diff order. The LLM planning/batching stages receive the
        # file metadata and make semantic judgments; diff ingestion should not.
        files = self._prioritize_files(files)
        
        # Apply limits
        processed_files, truncated, truncation_reason = self._apply_limits(files)
        
        # Calculate stats
        total_additions = sum(f.additions for f in processed_files if not f.is_skipped)
        total_deletions = sum(f.deletions for f in processed_files if not f.is_skipped)
        total_files = len([f for f in processed_files if not f.is_skipped])
        skipped_files = len([f for f in processed_files if f.is_skipped])
        processed_size = sum(f.size_bytes for f in processed_files if not f.is_skipped)
        
        return ProcessedDiff(
            files=processed_files,
            total_additions=total_additions,
            total_deletions=total_deletions,
            total_files=total_files,
            skipped_files=skipped_files,
            truncated=truncated,
            truncation_reason=truncation_reason,
            original_size_bytes=original_size,
            processed_size_bytes=processed_size,
            refactoring_signals=self._detect_refactoring_signals(processed_files),
        )

    def _detect_refactoring_signals(self, files: List[DiffFile]) -> List[str]:
        """
        Detect common refactoring patterns to reduce false positives.

        Returns list of human-readable signals like:
            "File rename: old_path → new_path"
            "Balanced add/delete (~120 lines) suggests code move"
        """
        signals = []
        
        # 1. Explicit renames
        for f in files:
            if f.change_type == DiffChangeType.RENAMED and f.old_path:
                signals.append(f"File rename: {f.old_path} → {f.path}")
        
        # 2. Paired add + delete of files with same basename
        added = {f.path: f for f in files if f.change_type == DiffChangeType.ADDED}
        deleted = {f.path: f for f in files if f.change_type == DiffChangeType.DELETED}
        
        added_basenames = {}
        for path, f in added.items():
            bn = path.rsplit('/', 1)[-1] if '/' in path else path
            added_basenames.setdefault(bn, []).append(f)
        
        for del_path, del_f in deleted.items():
            bn = del_path.rsplit('/', 1)[-1] if '/' in del_path else del_path
            if bn in added_basenames:
                for add_f in added_basenames[bn]:
                    signals.append(f"Possible file move: {del_path} → {add_f.path}")
        
        # 3. Balanced additions/deletions across the whole PR (suggests refactoring)
        total_add = sum(f.additions for f in files if not f.is_skipped)
        total_del = sum(f.deletions for f in files if not f.is_skipped)
        if total_add > 20 and total_del > 20:
            ratio = min(total_add, total_del) / max(total_add, total_del) if max(total_add, total_del) > 0 else 0
            if ratio > 0.7:
                signals.append(
                    f"Balanced add/delete (+{total_add}/-{total_del}, ratio={ratio:.2f}) "
                    f"suggests refactoring or code move"
                )
        
        if signals:
            logger.info(f"Refactoring signals detected: {signals}")
        
        return signals

    def _parse_diff(self, raw_diff: str) -> List[DiffFile]:
        """Parse unified diff into list of DiffFile objects."""
        files = []
        current_file = None
        current_content = []
        
        lines = raw_diff.split('\n')
        i = 0
        
        while i < len(lines):
            line = lines[i]
            
            # New file diff section
            if line.startswith('diff --git'):
                # Save previous file
                if current_file:
                    current_file.content = '\n'.join(current_content)
                    files.append(current_file)
                
                # Parse file paths
                match = re.match(r'diff --git a/(.+) b/(.+)', line)
                if match:
                    old_path = match.group(1)
                    new_path = match.group(2)
                    
                    current_file = DiffFile(
                        path=new_path,
                        old_path=old_path if old_path != new_path else None,
                        change_type=DiffChangeType.MODIFIED
                    )
                    current_content = [line]
                else:
                    current_file = None
                    current_content = []
                
                i += 1
                continue
            
            if current_file:
                current_content.append(line)
                
                # Detect change type
                if line.startswith('new file mode'):
                    current_file.change_type = DiffChangeType.ADDED
                elif line.startswith('deleted file mode'):
                    current_file.change_type = DiffChangeType.DELETED
                elif line.startswith('rename from'):
                    current_file.change_type = DiffChangeType.RENAMED
                elif line.startswith('Binary files'):
                    current_file.change_type = DiffChangeType.BINARY
                    current_file.is_binary = True
                
                # Count additions/deletions
                if line.startswith('+') and not line.startswith('+++'):
                    current_file.additions += 1
                elif line.startswith('-') and not line.startswith('---'):
                    current_file.deletions += 1
            
            i += 1
        
        # Save last file
        if current_file:
            current_file.content = '\n'.join(current_content)
            files.append(current_file)
        
        return files

    def _should_skip(self, file: DiffFile) -> bool:
        """Check if file should be skipped based on rules (matching LargeContentFilter)."""
        path = file.path
        threshold_kb = self.max_file_size // 1024

        # Skip binary files
        if file.is_binary:
            file.skip_reason = "Binary file"
            return True
        
        # Skip deleted files (no code to review)
        if file.change_type == DiffChangeType.DELETED:
            file.skip_reason = "Deleted file"
            return True
        
        # Skip files that are too large (matching LargeContentFilter)
        if file.size_bytes > self.max_file_size:
            file.skip_reason = f"File too large: {file.size_bytes} bytes > {self.max_file_size}"
            # Generate a compact raw-evidence summary instead of fully omitting
            # the diff. Semantic interpretation is left to the LLM.
            file.content = summarize_oversized_diff(file.content, path)
            return True
        
        # Skip files with too many lines
        line_count = file.content.count('\n')
        if line_count > self.max_lines_per_file:
            file.skip_reason = f"Too many lines: {line_count} > {self.max_lines_per_file}"
            file.content = summarize_oversized_diff(file.content, path)
            return True
        
        return False
    
    def _prioritize_files(self, files: List[DiffFile]) -> List[DiffFile]:
        """Keep non-skipped files first and preserve original diff order."""
        def sort_key(item: Tuple[int, DiffFile]) -> Tuple[int, int]:
            index, f = item
            return (1 if f.is_skipped else 0, index)
        
        return [f for _, f in sorted(enumerate(files), key=sort_key)]
    
    def _apply_limits(self, files: List[DiffFile]) -> Tuple[List[DiffFile], bool, Optional[str]]:
        """
        Apply file count and total size limits.
        
        Returns:
            (files, truncated, truncation_reason)
        """
        truncated = False
        truncation_reason = None
        
        included_count = 0
        total_size = 0
        
        for f in files:
            if f.is_skipped:
                continue
            
            # Check file count limit
            if included_count >= self.max_files:
                f.is_skipped = True
                f.skip_reason = f"Exceeds max files limit: {self.max_files}"
                truncated = True
                truncation_reason = f"Diff truncated: exceeded {self.max_files} files limit"
                continue
            
            # Check total size limit
            if total_size + f.size_bytes > self.max_total_size:
                f.is_skipped = True
                f.skip_reason = f"Would exceed total size limit: {self.max_total_size}"
                truncated = True
                truncation_reason = f"Diff truncated: exceeded {self.max_total_size} bytes total size"
                continue
            
            included_count += 1
            total_size += f.size_bytes
        
        return files, truncated, truncation_reason


def process_raw_diff(raw_diff: Optional[str]) -> ProcessedDiff:
    """
    Convenience function to process raw diff with default settings.
    
    Args:
        raw_diff: Raw unified diff content or None
        
    Returns:
        ProcessedDiff object
    """
    if not raw_diff:
        return ProcessedDiff(files=[], original_size_bytes=0)
    
    processor = DiffProcessor()
    return processor.process(raw_diff)


def format_diff_for_prompt(
    processed_diff: ProcessedDiff,
    include_stats: bool = True,
    max_chars: Optional[int] = None
) -> str:
    """
    Format processed diff for inclusion in LLM prompt.
    
    Args:
        processed_diff: ProcessedDiff from processor
        include_stats: Whether to include statistics header
        max_chars: Optional character limit
        
    Returns:
        Formatted diff string
    """
    parts = []
    
    if include_stats:
        parts.append(f"=== DIFF STATISTICS ===")
        parts.append(f"Files changed: {processed_diff.total_files}")
        parts.append(f"Additions: +{processed_diff.total_additions}")
        parts.append(f"Deletions: -{processed_diff.total_deletions}")
        if processed_diff.skipped_files > 0:
            parts.append(f"Files skipped: {processed_diff.skipped_files}")
        if processed_diff.truncated:
            parts.append(f"⚠️ {processed_diff.truncation_reason}")
        parts.append("")
    
    # Add file list
    included_files = processed_diff.get_included_files()
    if included_files:
        parts.append("=== CHANGED FILES ===")
        for f in included_files:
            change_symbol = {
                DiffChangeType.ADDED: "A",
                DiffChangeType.MODIFIED: "M",
                DiffChangeType.DELETED: "D",
                DiffChangeType.RENAMED: "R",
                DiffChangeType.BINARY: "B",
            }.get(f.change_type, "?")
            parts.append(f"  [{change_symbol}] {f.path} (+{f.additions}/-{f.deletions})")
        parts.append("")
    
    # Add actual diff content
    parts.append("=== DIFF CONTENT ===")
    diff_content = processed_diff.to_unified_diff()
    
    # Apply character limit if needed
    if max_chars and len(diff_content) > max_chars:
        diff_content = diff_content[:max_chars] + "\n... (truncated)"
    
    parts.append(diff_content)
    
    return "\n".join(parts)
