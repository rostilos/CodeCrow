"""
Issue Post-Processor Service

This service handles:
1. Line number validation and correction against actual file content
2. Issue deduplication/merging for semantically similar issues
3. Fix validation and cleanup
"""
import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class IssuePostProcessor:
    """
    Post-processes LLM-generated issues to fix common problems:
    - Line number drift (LLM reporting wrong lines)
    - Duplicate/similar issues that should be merged
    - Invalid or low-quality suggested fixes
    """

    # Similarity threshold for considering issues as duplicates
    SIMILARITY_THRESHOLD = 0.75
    
    # Maximum line number drift to attempt correction
    MAX_LINE_DRIFT = 15
    
    # Keywords that indicate similar issues (for grouping)
    ISSUE_KEYWORDS = [
        'hardcode', 'hardcoded',
        'sql injection', 'injection',
        'xss', 'cross-site',
        'authentication', 'auth bypass',
        'null pointer', 'null check', 'nullpointer',
        'memory leak', 'resource leak',
        'n+1', 'n+1 query',
        'store id', 'store_id',
        'environment', 'config', 'configuration',
        'secret', 'password', 'api key', 'apikey',
        'deprecated', 'deprecated method',
        'unused', 'dead code',
        'performance', 'slow', 'inefficient',
    ]

    def __init__(self, diff_content: Optional[str] = None, file_contents: Optional[Dict[str, str]] = None):
        """
        Initialize post-processor.
        
        Args:
            diff_content: Raw diff content for line number validation
            file_contents: Map of file paths to their content for validation
        """
        self.diff_content = diff_content
        self.file_contents = file_contents or {}
        self._diff_line_map = self._parse_diff_lines() if diff_content else {}

    def _parse_diff_lines(self) -> Dict[str, Dict[int, str]]:
        """
        Parse diff to create a map of file -> line number -> content.
        This helps validate and correct LLM-reported line numbers.
        
        Returns:
            Dict mapping file paths to dict of line numbers to line content
        """
        if not self.diff_content:
            return {}
        
        result = {}
        current_file = None
        current_new_line = 0
        
        for line in self.diff_content.split('\n'):
            # Detect file header
            if line.startswith('+++ b/') or line.startswith('+++ '):
                # Extract file path
                match = re.match(r'\+\+\+ [ab]/(.+)', line)
                if match:
                    current_file = match.group(1)
                    result[current_file] = {}
                continue
            
            # Detect hunk header: @@ -old_start,old_count +new_start,new_count @@
            hunk_match = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if hunk_match:
                current_new_line = int(hunk_match.group(1))
                continue
            
            if current_file is None:
                continue
            
            # Track lines in the new file
            if line.startswith('+') and not line.startswith('+++'):
                # Added line
                result[current_file][current_new_line] = line[1:]  # Remove +
                current_new_line += 1
            elif line.startswith('-') and not line.startswith('---'):
                # Deleted line - don't increment new line counter
                pass
            elif line.startswith(' ') or line == '':
                # Context line or empty line
                if current_new_line > 0:
                    result[current_file][current_new_line] = line[1:] if line.startswith(' ') else line
                    current_new_line += 1
        
        logger.debug(f"Parsed diff lines for {len(result)} files")
        return result

    def process_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Main entry point: process a list of issues.
        
        Steps:
        1. Group issues by file
        2. Detect and merge duplicates
        3. Validate/correct line numbers
        4. Clean up fix suggestions
        
        Args:
            issues: List of issue dictionaries from LLM
            
        Returns:
            Processed list of issues with duplicates merged and lines corrected
        """
        if not issues:
            return []
        
        logger.info(f"Post-processing {len(issues)} issues")
        
        # Step 1: Fix line numbers for each issue
        issues_with_fixed_lines = []
        for issue in issues:
            fixed_issue = self._fix_line_number(issue)
            issues_with_fixed_lines.append(fixed_issue)
        
        # Step 2: Merge duplicates
        merged_issues = self._merge_duplicate_issues(issues_with_fixed_lines)
        
        # Step 3: Validate and clean fix suggestions
        cleaned_issues = []
        for issue in merged_issues:
            cleaned = self._clean_fix_suggestion(issue)
            cleaned_issues.append(cleaned)
        
        logger.info(f"Post-processing complete: {len(issues)} -> {len(cleaned_issues)} issues")
        return cleaned_issues

    def _fix_line_number(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """
        Attempt to correct the line number based on diff content or file content.
        
        Strategy:
        1. If we have diff content, find the line that best matches the issue context
        2. Search within a window (±MAX_LINE_DRIFT lines) for matching content
        """
        file_path = issue.get('file', '')
        reported_line = issue.get('line', 0)
        
        try:
            reported_line = int(reported_line)
        except (ValueError, TypeError):
            reported_line = 0
        
        if reported_line == 0:
            return issue
        
        # Try to find the correct line using diff content
        if file_path in self._diff_line_map:
            corrected_line = self._find_correct_line_in_diff(
                file_path, reported_line, issue.get('reason', '')
            )
            if corrected_line and corrected_line != reported_line:
                logger.debug(f"Corrected line for {file_path}: {reported_line} -> {corrected_line}")
                issue = issue.copy()
                issue['line'] = str(corrected_line)
                issue['_line_corrected'] = True
        
        # Try to find the correct line using file content
        elif file_path in self.file_contents:
            corrected_line = self._find_correct_line_in_file(
                file_path, reported_line, issue.get('reason', '')
            )
            if corrected_line and corrected_line != reported_line:
                logger.debug(f"Corrected line for {file_path}: {reported_line} -> {corrected_line}")
                issue = issue.copy()
                issue['line'] = str(corrected_line)
                issue['_line_corrected'] = True
        
        return issue

    def _find_correct_line_in_diff(
        self, 
        file_path: str, 
        reported_line: int, 
        reason: str
    ) -> Optional[int]:
        """
        Search the diff content for the line that best matches the issue.
        """
        file_lines = self._diff_line_map.get(file_path, {})
        if not file_lines:
            return None
        
        # Extract keywords from the reason to search for
        keywords = self._extract_keywords_from_reason(reason)
        if not keywords:
            return None
        
        best_match_line = reported_line
        best_match_score = 0
        
        # Search within a window around the reported line
        for line_num in range(
            max(1, reported_line - self.MAX_LINE_DRIFT),
            reported_line + self.MAX_LINE_DRIFT + 1
        ):
            if line_num not in file_lines:
                continue
            
            line_content = file_lines[line_num].lower()
            score = sum(1 for kw in keywords if kw.lower() in line_content)
            
            # Prefer lines closer to reported line in case of tie
            distance_penalty = abs(line_num - reported_line) * 0.1
            adjusted_score = score - distance_penalty
            
            if adjusted_score > best_match_score:
                best_match_score = adjusted_score
                best_match_line = line_num
        
        return best_match_line if best_match_score > 0 else None

    def _find_correct_line_in_file(
        self,
        file_path: str,
        reported_line: int,
        reason: str
    ) -> Optional[int]:
        """
        Search the file content for the line that best matches the issue.
        """
        file_content = self.file_contents.get(file_path, '')
        if not file_content:
            return None
        
        lines = file_content.split('\n')
        keywords = self._extract_keywords_from_reason(reason)
        if not keywords:
            return None
        
        best_match_line = reported_line
        best_match_score = 0
        
        # Search within a window around the reported line
        for line_num in range(
            max(1, reported_line - self.MAX_LINE_DRIFT),
            min(len(lines), reported_line + self.MAX_LINE_DRIFT + 1)
        ):
            line_idx = line_num - 1  # 0-indexed
            if line_idx < 0 or line_idx >= len(lines):
                continue
            
            line_content = lines[line_idx].lower()
            score = sum(1 for kw in keywords if kw.lower() in line_content)
            
            # Prefer lines closer to reported line in case of tie
            distance_penalty = abs(line_num - reported_line) * 0.1
            adjusted_score = score - distance_penalty
            
            if adjusted_score > best_match_score:
                best_match_score = adjusted_score
                best_match_line = line_num
        
        return best_match_line if best_match_score > 0 else None

    def _extract_keywords_from_reason(self, reason: str) -> List[str]:
        """
        Extract meaningful keywords from the issue reason.
        These are used to locate the correct line.
        """
        if not reason:
            return []
        
        # Look for code identifiers (variable names, function names, etc.)
        # Pattern: words with underscores, camelCase, or quoted strings
        identifiers = re.findall(r"['\"`]([^'\"`]+)['\"`]", reason)
        identifiers.extend(re.findall(r'\b([a-z]+(?:_[a-z]+)+)\b', reason, re.IGNORECASE))
        identifiers.extend(re.findall(r'\b([a-z]+(?:[A-Z][a-z]+)+)\b', reason))
        
        # Also look for numbers that might be specific values (like store ID 6)
        numbers = re.findall(r"(?:ID|id|value|code)\s*[=:'\"`]?\s*(\d+)", reason)
        identifiers.extend(numbers)
        
        # Check for known issue keywords
        for keyword in self.ISSUE_KEYWORDS:
            if keyword.lower() in reason.lower():
                # Add specific terms related to this keyword
                if 'store' in keyword:
                    identifiers.extend(['store_id', 'storeId', 'getStoreId'])
                elif 'hardcode' in keyword:
                    numbers = re.findall(r'\b(\d+)\b', reason)
                    identifiers.extend(numbers[:3])  # Take up to 3 hardcoded values
        
        return list(set(identifiers))[:10]  # Limit to 10 keywords

    def _merge_duplicate_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Merge issues that are semantically similar.
        
        Strategy:
        1. Group issues by file
        2. Within each file, compare issues for similarity
        3. Merge similar issues, keeping the one with the best fix suggestion
        """
        if len(issues) < 2:
            return issues
        
        # Group by file
        by_file: Dict[str, List[Dict[str, Any]]] = {}
        for issue in issues:
            file_path = issue.get('file', 'unknown')
            if file_path not in by_file:
                by_file[file_path] = []
            by_file[file_path].append(issue)
        
        result = []
        
        for file_path, file_issues in by_file.items():
            if len(file_issues) == 1:
                result.extend(file_issues)
                continue
            
            # Find similar issues within this file
            merged_indices = set()
            
            for i, issue1 in enumerate(file_issues):
                if i in merged_indices:
                    continue
                
                # Find all issues similar to issue1
                similar_group = [issue1]
                
                for j, issue2 in enumerate(file_issues[i+1:], i+1):
                    if j in merged_indices:
                        continue
                    
                    similarity = self._calculate_issue_similarity(issue1, issue2)
                    if similarity >= self.SIMILARITY_THRESHOLD:
                        similar_group.append(issue2)
                        merged_indices.add(j)
                
                if len(similar_group) > 1:
                    # Merge the group
                    merged = self._merge_issue_group(similar_group)
                    logger.info(f"Merged {len(similar_group)} similar issues in {file_path}")
                    result.append(merged)
                else:
                    result.append(issue1)
        
        return result

    def _calculate_issue_similarity(
        self, 
        issue1: Dict[str, Any], 
        issue2: Dict[str, Any]
    ) -> float:
        """
        Calculate semantic similarity between two issues.
        
        Returns:
            Float between 0 and 1 indicating similarity
        """
        # Same category bonus
        category_match = issue1.get('category') == issue2.get('category')
        
        # Compare reasons
        reason1 = issue1.get('reason', '').lower()
        reason2 = issue2.get('reason', '').lower()
        
        # Quick keyword check
        keywords1 = set(self._extract_core_keywords(reason1))
        keywords2 = set(self._extract_core_keywords(reason2))
        
        if keywords1 and keywords2:
            keyword_overlap = len(keywords1 & keywords2) / max(len(keywords1), len(keywords2))
        else:
            keyword_overlap = 0
        
        # Sequence similarity
        sequence_sim = SequenceMatcher(None, reason1, reason2).ratio()
        
        # Line proximity (issues on nearby lines are more likely duplicates)
        try:
            line1 = int(issue1.get('line', 0))
            line2 = int(issue2.get('line', 0))
            line_distance = abs(line1 - line2)
            line_proximity = max(0, 1 - (line_distance / 50))  # Decay over 50 lines
        except (ValueError, TypeError):
            line_proximity = 0
        
        # Weighted combination
        similarity = (
            0.4 * keyword_overlap +
            0.3 * sequence_sim +
            0.2 * line_proximity +
            0.1 * (1 if category_match else 0)
        )
        
        return similarity

    def _extract_core_keywords(self, text: str) -> List[str]:
        """Extract core keywords from issue text for comparison."""
        keywords = []
        text_lower = text.lower()
        
        for keyword in self.ISSUE_KEYWORDS:
            if keyword in text_lower:
                keywords.append(keyword)
        
        # Also extract identifiers
        identifiers = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]{2,})\b', text)
        keywords.extend([id.lower() for id in identifiers[:5]])
        
        return keywords

    def _merge_issue_group(self, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Merge a group of similar issues into one.
        
        Strategy:
        - Keep the issue with the best suggested fix
        - Combine reasons if they provide different insights
        - Keep the highest severity
        """
        if not issues:
            return {}
        
        if len(issues) == 1:
            return issues[0]
        
        # Find issue with best fix (longest valid diff)
        best_issue = max(
            issues,
            key=lambda i: len(i.get('suggestedFixDiff', '') or '') 
            if self._is_valid_diff(i.get('suggestedFixDiff'))
            else 0
        )
        
        # Determine highest severity
        severity_order = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
        highest_severity = max(
            issues,
            key=lambda i: severity_order.get(i.get('severity', 'LOW'), 0)
        ).get('severity', 'MEDIUM')
        
        # Combine reasons if different
        unique_insights = set()
        for issue in issues:
            reason = issue.get('reason', '')
            # Extract the core insight (first sentence or 100 chars)
            core = reason.split('.')[0][:100].strip()
            if core:
                unique_insights.add(core)
        
        combined_reason = best_issue.get('reason', '')
        if len(unique_insights) > 1:
            # Multiple unique insights - add a note
            combined_reason = f"{combined_reason}\n\nNote: {len(issues)} similar instances of this issue were found."
        
        # Use the first (or lowest) line number
        try:
            line_numbers = [int(i.get('line', 0)) for i in issues if i.get('line')]
            first_line = min(line_numbers) if line_numbers else 0
        except (ValueError, TypeError):
            first_line = best_issue.get('line', 0)
        
        merged = best_issue.copy()
        merged['severity'] = highest_severity
        merged['reason'] = combined_reason
        merged['line'] = str(first_line)
        merged['_merged_count'] = len(issues)
        
        return merged

    def _is_valid_diff(self, diff: Optional[str]) -> bool:
        """Check if a diff looks valid."""
        if not diff or not isinstance(diff, str):
            return False
        if diff == "No suggested fix provided":
            return False
        if len(diff.strip()) < 10:
            return False
        # Must have diff markers
        return any(marker in diff for marker in ['---', '+++', '@@', '\n-', '\n+'])

    def _clean_fix_suggestion(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """
        Clean and validate the suggested fix.
        """
        issue = issue.copy()
        
        diff = issue.get('suggestedFixDiff', '')
        
        if not self._is_valid_diff(diff):
            # Mark as needing fix but don't remove
            issue['_needs_fix_review'] = True
        else:
            # Validate diff format
            cleaned_diff = self._clean_diff_format(diff)
            if cleaned_diff != diff:
                issue['suggestedFixDiff'] = cleaned_diff
        
        return issue

    def _clean_diff_format(self, diff: str) -> str:
        """
        Clean up diff format issues.
        """
        if not diff:
            return diff
        
        lines = diff.split('\n')
        cleaned_lines = []
        
        for line in lines:
            # Remove markdown code blocks
            if line.strip() in ['```', '```diff']:
                continue
            cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines)


class IssueDeduplicator:
    """
    Specialized class for issue deduplication using multiple strategies.
    """
    
    def __init__(self):
        self.processor = IssuePostProcessor()
    
    def find_duplicates(
        self, 
        issues: List[Dict[str, Any]]
    ) -> List[Tuple[int, int, float]]:
        """
        Find pairs of duplicate issues.
        
        Returns:
            List of (index1, index2, similarity) tuples
        """
        duplicates = []
        
        for i, issue1 in enumerate(issues):
            for j, issue2 in enumerate(issues[i+1:], i+1):
                similarity = self.processor._calculate_issue_similarity(issue1, issue2)
                if similarity >= IssuePostProcessor.SIMILARITY_THRESHOLD:
                    duplicates.append((i, j, similarity))
        
        return duplicates
    
    def auto_merge(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Automatically merge duplicate issues.
        """
        return self.processor._merge_duplicate_issues(issues)


def restore_missing_diffs_from_previous(
    issues: List[Dict[str, Any]],
    previous_issues: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    For branch reconciliation: restore missing suggestedFixDiff from previous issues.
    
    LLMs often omit the diff when reporting persisting issues. This function
    looks up the original issue by issueId and copies the diff if missing.
    
    Args:
        issues: Issues from LLM response (may have missing diffs)
        previous_issues: Original previous issues with diffs
        
    Returns:
        Issues with diffs restored from previous issues where missing
    """
    if not previous_issues:
        return issues
    
    # Build lookup by ID (handle both string and int IDs)
    previous_by_id: Dict[str, Dict[str, Any]] = {}
    for prev in previous_issues:
        issue_id = prev.get('id') or prev.get('issueId')
        if issue_id is not None:
            previous_by_id[str(issue_id)] = prev
    
    restored_issues = []
    restored_count = 0
    
    for issue in issues:
        issue = issue.copy()
        issue_id = issue.get('issueId') or issue.get('id')
        is_resolved = issue.get('isResolved', False)
        
        # Only restore for unresolved issues
        if issue_id and not is_resolved:
            original = previous_by_id.get(str(issue_id))
            if original:
                # Restore suggestedFixDiff if missing or empty
                current_diff = issue.get('suggestedFixDiff', '')
                if not current_diff or current_diff == 'No suggested fix provided' or len(current_diff.strip()) < 10:
                    original_diff = original.get('suggestedFixDiff', '')
                    if original_diff and original_diff != 'No suggested fix provided':
                        issue['suggestedFixDiff'] = original_diff
                        issue['_diff_restored'] = True
                        restored_count += 1
                        logger.debug(f"Restored diff for issue {issue_id}")
                
                # Restore suggestedFixDescription if missing
                current_desc = issue.get('suggestedFixDescription', '')
                if not current_desc or current_desc == 'No suggested fix description provided':
                    original_desc = original.get('suggestedFixDescription', '')
                    if original_desc and original_desc != 'No suggested fix description provided':
                        issue['suggestedFixDescription'] = original_desc
        
        restored_issues.append(issue)
    
    if restored_count > 0:
        logger.info(f"Restored diffs for {restored_count} persisting issues from previous analysis")
    
    return restored_issues


def post_process_analysis_result(
    result: Dict[str, Any],
    diff_content: Optional[str] = None,
    file_contents: Optional[Dict[str, str]] = None,
    previous_issues: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Convenience function to post-process an analysis result.
    
    Args:
        result: Analysis result dict with 'comment' and 'issues'
        diff_content: Optional diff for line validation
        file_contents: Optional file contents for line validation
        previous_issues: Optional previous issues for branch reconciliation (to restore missing diffs)
        
    Returns:
        Processed result with cleaned issues
    """
    if 'issues' not in result:
        return result
    
    issues = result.get('issues', [])
    
    # Step 0: For branch reconciliation, restore missing diffs from previous issues
    if previous_issues:
        issues = restore_missing_diffs_from_previous(issues, previous_issues)
    
    processor = IssuePostProcessor(diff_content, file_contents)
    processed_issues = processor.process_issues(issues)
    
    return {
        **result,
        'issues': processed_issues,
        '_post_processed': True,
        '_original_issue_count': len(result.get('issues', [])),
        '_final_issue_count': len(processed_issues)
    }
