"""
Diff Processing Utilities for Code Review.

Handles parsing, filtering, and prioritization of PR diffs.
Applies same rules as MCP server LargeContentFilter (25KB file limit).
"""

import os
import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# Constants from environment or defaults (matching MCP server LargeContentFilter)
# See: org.rostilos.codecrow.mcp.filter.LargeContentFilter
DEFAULT_FILE_SIZE_THRESHOLD_BYTES = 25 * 1024  # 25KB - same as LargeContentFilter.DEFAULT_SIZE_THRESHOLD_BYTES

MAX_FILE_SIZE_BYTES = int(os.environ.get("DIFF_MAX_FILE_SIZE", str(DEFAULT_FILE_SIZE_THRESHOLD_BYTES)))
MAX_FILES_IN_DIFF = int(os.environ.get("DIFF_MAX_FILES", "100"))  # Maximum files to process
MAX_DIFF_SIZE_BYTES = int(os.environ.get("DIFF_MAX_TOTAL_SIZE", "500000"))  # 500KB total diff size
MAX_LINES_PER_FILE = int(os.environ.get("DIFF_MAX_LINES_PER_FILE", "1000"))  # Maximum lines per file

# Placeholder message matching LargeContentFilter.FILTERED_PLACEHOLDER
FILTERED_PLACEHOLDER = "[CodeCrow Filter: file too large (>25KB), omitted from analysis]"
FILTERED_DIFF_TEMPLATE = """diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
[CodeCrow Filter: file diff too large (>{threshold_kb}KB), omitted from analysis. File type: {diff_type}]
"""


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
    - Prioritizes important files
    - Skips binary and generated files
    """
    
    # File patterns to skip (generated, lock files, etc.)
    SKIP_PATTERNS = [
        r'package-lock\.json$',
        r'yarn\.lock$',
        r'pnpm-lock\.yaml$',
        r'Gemfile\.lock$',
        r'poetry\.lock$',
        r'Cargo\.lock$',
        r'composer\.lock$',
        r'\.min\.(js|css)$',
        r'\.bundle\.(js|css)$',
        r'\.map$',
        r'\.snap$',
        r'__snapshots__/',
        r'\.generated\.',
        r'dist/',
        r'build/',
        r'node_modules/',
        r'vendor/',
        r'\.idea/',
        r'\.vscode/',
        r'\.git/',
    ]
    
    # High priority file patterns (business logic, entry points)
    HIGH_PRIORITY_PATTERNS = [
        r'(^|/)src/',
        r'(^|/)app/',
        r'(^|/)lib/',
        r'(^|/)core/',
        r'(^|/)api/',
        r'(^|/)service/',
        r'(^|/)controller/',
        r'(^|/)handler/',
        r'(^|/)model/',
        r'(^|/)entity/',
        r'\.py$',
        r'\.java$',
        r'\.kt$',
        r'\.ts$',
        r'\.tsx$',
        r'\.go$',
    ]
    
    # Low priority file patterns
    LOW_PRIORITY_PATTERNS = [
        r'test[s]?/',
        r'spec[s]?/',
        r'__test__/',
        r'\.test\.',
        r'\.spec\.',
        r'_test\.',
        r'\.md$',
        r'\.txt$',
        r'\.json$',
        r'\.yaml$',
        r'\.yml$',
        r'\.toml$',
        r'\.ini$',
        r'\.cfg$',
        r'\.conf$',
    ]
    
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
        
        # Compile patterns
        self._skip_patterns = [re.compile(p, re.IGNORECASE) for p in self.SKIP_PATTERNS]
        self._high_priority = [re.compile(p, re.IGNORECASE) for p in self.HIGH_PRIORITY_PATTERNS]
        self._low_priority = [re.compile(p, re.IGNORECASE) for p in self.LOW_PRIORITY_PATTERNS]
    
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
        
        # Sort by priority
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
            processed_size_bytes=processed_size
        )
    
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
        
        # Skip by pattern
        for pattern in self._skip_patterns:
            if pattern.search(path):
                file.skip_reason = f"Matches skip pattern: {pattern.pattern}"
                return True
        
        # Skip files that are too large (matching LargeContentFilter)
        if file.size_bytes > self.max_file_size:
            file.skip_reason = f"File too large: {file.size_bytes} bytes > {self.max_file_size}"
            # Replace content with placeholder matching Java LargeContentFilter
            file.content = FILTERED_DIFF_TEMPLATE.format(
                path=path,
                threshold_kb=threshold_kb,
                diff_type=file.change_type.value
            )
            return True
        
        # Skip files with too many lines
        line_count = file.content.count('\n')
        if line_count > self.max_lines_per_file:
            file.skip_reason = f"Too many lines: {line_count} > {self.max_lines_per_file}"
            file.content = FILTERED_DIFF_TEMPLATE.format(
                path=path,
                threshold_kb=threshold_kb,
                diff_type=file.change_type.value
            )
            return True
        
        return False
    
    def _get_priority(self, file: DiffFile) -> int:
        """
        Get priority score for file (lower = higher priority).
        
        Returns:
            0 = High priority
            1 = Medium priority  
            2 = Low priority
        """
        path = file.path
        
        # Check high priority patterns
        for pattern in self._high_priority:
            if pattern.search(path):
                return 0
        
        # Check low priority patterns
        for pattern in self._low_priority:
            if pattern.search(path):
                return 2
        
        return 1  # Medium priority
    
    def _prioritize_files(self, files: List[DiffFile]) -> List[DiffFile]:
        """Sort files by priority, keeping non-skipped files first."""
        def sort_key(f: DiffFile) -> Tuple[int, int, int]:
            # (skipped, priority, -changes)
            # Non-skipped first, then by priority, then by number of changes (desc)
            return (
                1 if f.is_skipped else 0,
                self._get_priority(f),
                -f.total_changes
            )
        
        return sorted(files, key=sort_key)
    
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
