import logging
import asyncio
import json
import re
from typing import Dict, Any, List, Optional, Callable

from model.models import (
    ReviewRequestDto, 
    ReviewPlan, 
    CodeReviewOutput, 
    CodeReviewIssue,
    ReviewFile,
    FileGroup,
    CrossFileAnalysisResult,
    FileReviewBatchOutput
)
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff, DiffProcessor
from mcp_use import MCPAgent

logger = logging.getLogger(__name__)


def extract_llm_response_text(response: Any) -> str:
    """
    Extract text content from LLM response, handling different response formats.
    Some LLM providers return content as a list of objects instead of a string.
    """
    if hasattr(response, 'content'):
        content = response.content
        if isinstance(content, list):
            # Handle list content (e.g., from Gemini or other providers)
            text_parts = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict):
                    if 'text' in item:
                        text_parts.append(item['text'])
                    elif 'content' in item:
                        text_parts.append(item['content'])
                elif hasattr(item, 'text'):
                    text_parts.append(item.text)
            return "".join(text_parts)
        return str(content)
    return str(response)


# Prevent duplicate logs from mcp_use
mcp_logger = logging.getLogger("mcp_use")
mcp_logger.propagate = False
if not mcp_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    mcp_logger.addHandler(handler)

class RecursiveMCPAgent(MCPAgent):
    """
    Subclass of MCPAgent that enforces a higher recursion limit on the internal agent executor.
    """
    def __init__(self, *args, recursion_limit: int = 50, **kwargs):
        self._custom_recursion_limit = recursion_limit
        super().__init__(*args, **kwargs)

    async def stream(self, *args, **kwargs):
        """
        Override stream to ensure recursion_limit is applied.
        """
        # Ensure the executor exists
        if self._agent_executor is None:
            await self.initialize()
        
        # Patch the executor's astream if not already patched
        executor = self._agent_executor
        if executor and not getattr(executor, "_is_patched_recursion", False):
            original_astream = executor.astream
            limit = self._custom_recursion_limit

            async def patched_astream(input_data, config=None, **astream_kwargs):
                if config is None:
                    config = {}
                config["recursion_limit"] = limit
                async for chunk in original_astream(input_data, config=config, **astream_kwargs):
                    yield chunk

            executor.astream = patched_astream
            executor._is_patched_recursion = True
            logger.info(f"RecursiveMCPAgent: Patched recursion limit to {limit}")
        
        # Call parent stream
        async for item in super().stream(*args, **kwargs):
            yield item

class MultiStageReviewOrchestrator:
    """
    Orchestrates the 4-stage AI code review pipeline:
    Stage 0: Planning & Prioritization
    Stage 1: Parallel File Review
    Stage 2: Cross-File & Architectural Analysis
    Stage 3: Aggregation & Final Report
    """

    def __init__(
        self, 
        llm, 
        mcp_client, 
        rag_client=None,
        event_callback: Optional[Callable[[Dict], None]] = None
    ):
        self.llm = llm
        self.client = mcp_client
        self.rag_client = rag_client
        self.event_callback = event_callback
        self.max_parallel_stage_1 = 5  # Limit parallel execution to avoid rate limits
        # PR-specific RAG indexing (data goes into main collection with PR metadata)
        self._pr_number: Optional[int] = None
        self._pr_indexed: bool = False

    async def _index_pr_files(
        self,
        request: ReviewRequestDto,
        processed_diff: Optional[ProcessedDiff]
    ) -> None:
        """
        Index PR files into the main RAG collection with PR-specific metadata.
        This enables hybrid queries that prioritize PR data over stale branch data.
        """
        if not self.rag_client or not processed_diff:
            return
        
        pr_number = request.pullRequestId
        if not pr_number:
            logger.info("No PR number, skipping PR file indexing")
            return
        
        # Prepare files for indexing
        # Prefer full_content if available, otherwise use diff content
        # Diff content still provides value for understanding what changed
        files = []
        for f in processed_diff.get_included_files():
            content = f.full_content or f.content  # Use full content if available, fallback to diff
            if content and f.change_type.value != "DELETED":
                files.append({
                    "path": f.path,
                    "content": content,
                    "change_type": f.change_type.value if hasattr(f.change_type, 'value') else str(f.change_type)
                })
        
        if not files:
            logger.info("No files to index for PR")
            return
        
        try:
            result = await self.rag_client.index_pr_files(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                pr_number=pr_number,
                branch=request.targetBranchName or "unknown",
                files=files
            )
            if result.get("status") == "indexed":
                self._pr_number = pr_number
                self._pr_indexed = True
                logger.info(f"Indexed PR #{pr_number}: {result.get('chunks_indexed', 0)} chunks")
            else:
                logger.warning(f"Failed to index PR files: {result}")
        except Exception as e:
            logger.warning(f"Error indexing PR files: {e}")

    async def _cleanup_pr_files(self, request: ReviewRequestDto) -> None:
        """Delete PR-indexed data after analysis completes."""
        if not self._pr_indexed or not self._pr_number or not self.rag_client:
            return
        
        try:
            await self.rag_client.delete_pr_files(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                pr_number=self._pr_number
            )
            logger.info(f"Cleaned up PR #{self._pr_number} indexed data")
        except Exception as e:
            logger.warning(f"Failed to cleanup PR files: {e}")
        finally:
            self._pr_number = None
            self._pr_indexed = False

    async def execute_branch_analysis(self, prompt: str) -> Dict[str, Any]:
        """
        Execute a single-pass branch analysis using the provided prompt.
        """
        self._emit_status("branch_analysis_started", "Starting Branch Analysis & Reconciliation...")
        
        agent = RecursiveMCPAgent(
            llm=self.llm, 
            client=self.client,
            additional_instructions=PromptBuilder.get_additional_instructions()
        )

        
        try:
            final_text = ""
            # Branch analysis expects standard CodeReviewOutput
            async for item in agent.stream(prompt, max_steps=15, output_schema=CodeReviewOutput):
                if isinstance(item, CodeReviewOutput):
                    # Convert to dict format expected by service
                    issues = [i.model_dump() for i in item.issues] if item.issues else []
                    return {
                        "issues": issues,
                        "comment": item.comment or "Branch analysis completed."
                    }
                
                if isinstance(item, str):
                    final_text = item
            
            # If stream finished without object, try parsing text
            if final_text:
                data = await self._parse_response(final_text, CodeReviewOutput)
                issues = [i.model_dump() for i in data.issues] if data.issues else []
                return {
                    "issues": issues,
                    "comment": data.comment or "Branch analysis completed."
                }
                
            return {"issues": [], "comment": "No issues found."}
            
        except Exception as e:
            logger.error(f"Branch analysis failed: {e}", exc_info=True)
            self._emit_error(str(e))
            raise

    async def orchestrate_review(
        self, 
        request: ReviewRequestDto, 
        rag_context: Optional[Dict[str, Any]] = None,
        processed_diff: Optional[ProcessedDiff] = None
    ) -> Dict[str, Any]:
        """
        Main entry point for the multi-stage review.
        Supports both FULL (initial review) and INCREMENTAL (follow-up review) modes.
        The same pipeline is used, but with incremental-aware prompts and issue reconciliation.
        """
        # Determine if this is an incremental review
        is_incremental = (
            request.analysisMode == "INCREMENTAL" 
            and request.deltaDiff
        )
        
        if is_incremental:
            logger.info(f"INCREMENTAL mode: reviewing delta diff, {len(request.previousCodeAnalysisIssues or [])} previous issues to reconcile")
        else:
            logger.info("FULL mode: initial PR review")

        # Generate unique ID for temp diff collection
        analysis_id = f"{request.projectId}_{request.pullRequestId or request.commitHash or 'unknown'}"

        try:
            # === Index PR files into RAG for hybrid queries ===
            # This indexes PR files with metadata (pr=true, pr_number=X) to enable
            # queries that prioritize fresh PR data over potentially stale branch data
            await self._index_pr_files(request, processed_diff)
            
            # === STAGE 0: Planning ===
            self._emit_status("stage_0_started", "Stage 0: Planning & Prioritization...")
            review_plan = await self._execute_stage_0_planning(request, is_incremental)
            
            # Validate and fix the plan to ensure all files are included
            review_plan = self._ensure_all_files_planned(review_plan, request.changedFiles or [])
            self._emit_progress(10, "Stage 0 Complete: Review plan created")
            
            # === STAGE 1: File Reviews ===
            self._emit_status("stage_1_started", f"Stage 1: Analyzing {self._count_files(review_plan)} files...")
            file_issues = await self._execute_stage_1_file_reviews(
                request, review_plan, rag_context, processed_diff, is_incremental
            )
            self._emit_progress(60, f"Stage 1 Complete: {len(file_issues)} issues found across files")

            # === STAGE 1.5: Issue Reconciliation ===
            # Run reconciliation if we have previous issues (both INCREMENTAL and FULL modes)
            if request.previousCodeAnalysisIssues:
                self._emit_status("reconciliation_started", "Reconciling previous issues...")
                file_issues = await self._reconcile_previous_issues(
                    request, file_issues, processed_diff
                )
                self._emit_progress(70, f"Reconciliation Complete: {len(file_issues)} total issues after reconciliation")

            # === STAGE 2: Cross-File Analysis ===
            self._emit_status("stage_2_started", "Stage 2: Analyzing cross-file patterns...")
            cross_file_results = await self._execute_stage_2_cross_file(request, file_issues, review_plan)
            self._emit_progress(85, "Stage 2 Complete: Cross-file analysis finished")
            
            # === STAGE 3: Aggregation ===
            self._emit_status("stage_3_started", "Stage 3: Generating final report...")
            final_report = await self._execute_stage_3_aggregation(
                request, review_plan, file_issues, cross_file_results, is_incremental
            )
            self._emit_progress(100, "Stage 3 Complete: Report generated")

            # Return structure compatible with existing response expected by frontend/controller
            return {
                "comment": final_report,
                "issues": [issue.model_dump() for issue in file_issues],
            }

        except Exception as e:
            logger.error(f"Multi-stage review failed: {e}", exc_info=True)
            self._emit_error(str(e))
            raise
        finally:
            # Cleanup PR-indexed data
            await self._cleanup_pr_files(request)

    async def _reconcile_previous_issues(
        self,
        request: ReviewRequestDto,
        new_issues: List[CodeReviewIssue],
        processed_diff: Optional[ProcessedDiff] = None
    ) -> List[CodeReviewIssue]:
        """
        Reconcile previous issues with new findings in incremental mode.
        - Mark resolved issues as isResolved=true
        - Update line numbers for persisting issues
        - Merge with new issues found in delta diff
        """
        if not request.previousCodeAnalysisIssues:
            return new_issues
        
        logger.info(f"Reconciling {len(request.previousCodeAnalysisIssues)} previous issues with {len(new_issues)} new issues")
        
        # Get the delta diff content to check what files/lines changed
        delta_diff = request.deltaDiff or ""
        
        # Build a set of files that changed in the delta
        changed_files_in_delta = set()
        if processed_diff:
            for f in processed_diff.files:
                changed_files_in_delta.add(f.path)
        
        reconciled_issues = list(new_issues)  # Start with new issues
        
        # Process each previous issue
        for prev_issue in request.previousCodeAnalysisIssues:
            # Convert to dict if needed
            if hasattr(prev_issue, 'model_dump'):
                prev_data = prev_issue.model_dump()
            else:
                prev_data = prev_issue if isinstance(prev_issue, dict) else vars(prev_issue)
            
            # Debug log to verify field mapping
            logger.debug(f"Previous issue data: reason={prev_data.get('reason')}, "
                        f"suggestedFixDescription={prev_data.get('suggestedFixDescription')}, "
                        f"suggestedFixDiff={prev_data.get('suggestedFixDiff')[:50] if prev_data.get('suggestedFixDiff') else None}")
            
            file_path = prev_data.get('file', prev_data.get('filePath', ''))
            issue_id = prev_data.get('id')
            
            # Check if this issue was already found in new issues (by file+line or ID)
            already_reported = False
            for new_issue in new_issues:
                new_data = new_issue.model_dump() if hasattr(new_issue, 'model_dump') else new_issue
                if (new_data.get('file') == file_path and 
                    str(new_data.get('line')) == str(prev_data.get('line', prev_data.get('lineNumber')))):
                    already_reported = True
                    break
                if issue_id and new_data.get('id') == issue_id:
                    already_reported = True
                    break
            
            if already_reported:
                continue  # Already in new issues, skip
            
            # Check if the file was modified in delta diff
            file_in_delta = any(file_path.endswith(f) or f.endswith(file_path) for f in changed_files_in_delta)
            
            # If file wasn't touched in delta, issue persists unchanged
            # If file was touched, we need to check if the specific line was modified
            is_resolved = False
            if file_in_delta:
                # Simple heuristic: if file changed and we didn't re-report this issue, 
                # it might be resolved. But we should be conservative here.
                # For now, we'll re-report it as persisting unless LLM marked it resolved
                pass
            
            # Preserve all original issue data - just pass through as CodeReviewIssue
            # Field mapping from Java DTO:
            #   reason (or title for legacy) -> reason
            #   severity (uppercase) -> severity  
            #   category (or issueCategory) -> category
            #   file -> file
            #   line -> line
            #   suggestedFixDescription -> suggestedFixDescription
            #   suggestedFixDiff -> suggestedFixDiff
            persisting_issue = CodeReviewIssue(
                id=str(issue_id) if issue_id else None,
                severity=(prev_data.get('severity') or prev_data.get('issueSeverity') or 'MEDIUM').upper(),
                category=prev_data.get('category') or prev_data.get('issueCategory') or prev_data.get('type') or 'CODE_QUALITY',
                file=file_path or prev_data.get('file') or prev_data.get('filePath') or 'unknown',
                line=str(prev_data.get('line') or prev_data.get('lineNumber') or '1'),
                reason=prev_data.get('reason') or prev_data.get('title') or prev_data.get('description') or '',
                suggestedFixDescription=prev_data.get('suggestedFixDescription') or prev_data.get('suggestedFix') or '',
                suggestedFixDiff=prev_data.get('suggestedFixDiff') or None,
                isResolved=is_resolved,
                visibility=prev_data.get('visibility'),
                codeSnippet=prev_data.get('codeSnippet')
            )
            reconciled_issues.append(persisting_issue)
        
        logger.info(f"Reconciliation complete: {len(reconciled_issues)} total issues")
        return reconciled_issues

    def _issue_matches_files(self, issue: Any, file_paths: List[str]) -> bool:
        """Check if an issue is related to any of the given file paths."""
        if hasattr(issue, 'model_dump'):
            issue_data = issue.model_dump()
        elif isinstance(issue, dict):
            issue_data = issue
        else:
            issue_data = vars(issue) if hasattr(issue, '__dict__') else {}
        
        issue_file = issue_data.get('file', issue_data.get('filePath', ''))
        
        for fp in file_paths:
            if issue_file == fp or issue_file.endswith('/' + fp) or fp.endswith('/' + issue_file):
                return True
            # Also check basename match
            if issue_file.split('/')[-1] == fp.split('/')[-1]:
                return True
        return False

    def _format_previous_issues_for_batch(self, issues: List[Any]) -> str:
        """Format previous issues for inclusion in batch prompt.
        
        Includes full issue history with resolution tracking so LLM knows:
        - Which issues were previously found
        - Which have been resolved (and how)
        - Which PR version each issue was found/resolved in
        """
        if not issues:
            return ""
        
        lines = ["=== PREVIOUS ISSUES HISTORY (check if resolved/persisting) ==="]
        lines.append("Issues from ALL previous PR iterations. Status indicates if resolved or still open.")
        lines.append("")
        
        for issue in issues:
            if hasattr(issue, 'model_dump'):
                data = issue.model_dump()
            elif isinstance(issue, dict):
                data = issue
            else:
                data = vars(issue) if hasattr(issue, '__dict__') else {}
            
            issue_id = data.get('id', 'unknown')
            severity = data.get('severity', 'MEDIUM')
            file_path = data.get('file', data.get('filePath', 'unknown'))
            line = data.get('line', data.get('lineNumber', '?'))
            reason = data.get('reason', data.get('description', 'No description'))
            status = data.get('status', 'open')
            pr_version = data.get('prVersion', '?')
            
            # Format status with resolution details if resolved
            status_display = status.upper()
            if status == 'resolved':
                resolved_desc = data.get('resolvedDescription', '')
                resolved_in = data.get('resolvedInPrVersion', '')
                if resolved_desc:
                    status_display += f" - {resolved_desc}"
                if resolved_in:
                    status_display += f" (in v{resolved_in})"
            
            lines.append(f"[ID:{issue_id}] {severity} @ {file_path}:{line} (v{pr_version})")
            lines.append(f"  Status: {status_display}")
            lines.append(f"  Issue: {reason}")
            lines.append("")
        
        lines.append("INSTRUCTIONS:")
        lines.append("- For OPEN issues that are now FIXED: report with 'isResolved': true (boolean)")
        lines.append("- For OPEN issues still present: report with 'isResolved': false (boolean)")
        lines.append("- For already RESOLVED issues: Do NOT re-report them (they're just for context)")
        lines.append("- IMPORTANT: 'isResolved' MUST be a JSON boolean (true/false), not a string")
        lines.append("- Preserve the 'id' field for all issues you report from previous issues")
        lines.append("=== END PREVIOUS ISSUES ===")
        return "\n".join(lines)

    def _extract_symbols_from_diff(self, diff_content: str) -> List[str]:
        """
        Extract potential symbols (identifiers, class names, function names) from diff.
        Used to query cross-file context for related changes.
        """
        if not diff_content:
            return []
        
        # Common language keywords/stop-words to filter out
        STOP_WORDS = {
            # Python
            'import', 'from', 'class', 'def', 'return', 'if', 'else', 'elif',
            'for', 'while', 'try', 'except', 'finally', 'with', 'as', 'pass',
            'break', 'continue', 'raise', 'yield', 'lambda', 'async', 'await',
            'True', 'False', 'None', 'and', 'or', 'not', 'in', 'is',
            # Java/TS/JS
            'public', 'private', 'protected', 'static', 'final', 'void',
            'new', 'this', 'super', 'extends', 'implements', 'interface',
            'abstract', 'const', 'let', 'var', 'function', 'export', 'default',
            'throw', 'throws', 'catch', 'instanceof', 'typeof', 'null',
            # Common
            'true', 'false', 'null', 'undefined', 'self', 'args', 'kwargs',
            'string', 'number', 'boolean', 'object', 'array', 'list', 'dict',
        }
        
        symbols = set()
        
        # Patterns for common identifiers
        # Match CamelCase identifiers (likely class/component names)
        camel_case = re.findall(r'\b([A-Z][a-z]+[A-Z][a-zA-Z]*)\b', diff_content)
        symbols.update(camel_case)
        
        # Match snake_case identifiers (variables, functions)
        snake_case = re.findall(r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b', diff_content)
        symbols.update(s for s in snake_case if len(s) > 5)  # Filter short ones
        
        # Match assignments and function calls
        assignments = re.findall(r'\b(\w+)\s*[=:]\s*', diff_content)
        symbols.update(a for a in assignments if len(a) > 3)
        
        # Match import statements
        imports = re.findall(r'(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_.]+)', diff_content)
        symbols.update(imports)
        
        # Filter out stop-words and return
        filtered = [s for s in symbols if s.lower() not in STOP_WORDS and len(s) > 2]
        return filtered[:20]  # Limit to top 20 symbols

    def _extract_diff_snippets(self, diff_content: str) -> List[str]:
        """
        Extract meaningful code snippets from diff content for RAG semantic search.
        Focuses on added/modified lines that represent significant code changes.
        """
        if not diff_content:
            return []
        
        snippets = []
        current_snippet_lines = []
        
        for line in diff_content.splitlines():
            # Focus on added lines (new code)
            if line.startswith("+") and not line.startswith("+++"):
                clean_line = line[1:].strip()
                # Skip trivial lines
                if (clean_line and 
                    len(clean_line) > 10 and  # Minimum meaningful length
                    not clean_line.startswith("//") and  # Skip comments
                    not clean_line.startswith("#") and
                    not clean_line.startswith("*") and
                    not clean_line == "{" and
                    not clean_line == "}" and
                    not clean_line == ""):
                    current_snippet_lines.append(clean_line)
                    
                    # Batch into snippets of 3-5 lines
                    if len(current_snippet_lines) >= 3:
                        snippets.append(" ".join(current_snippet_lines))
                        current_snippet_lines = []
        
        # Add remaining lines as final snippet
        if current_snippet_lines:
            snippets.append(" ".join(current_snippet_lines))
        
        # Limit to most significant snippets
        return snippets[:10]

    def _get_diff_snippets_for_batch(
        self, 
        all_diff_snippets: List[str], 
        batch_file_paths: List[str]
    ) -> List[str]:
        """
        Filter diff snippets to only include those relevant to the batch files.
        
        Note: Java DiffParser.extractDiffSnippets() returns CLEAN CODE SNIPPETS (no file paths).
        These snippets are just significant code lines like function signatures.
        Since snippets don't contain file paths, we return all snippets for semantic search.
        The embedding similarity will naturally prioritize relevant matches.
        """
        if not all_diff_snippets:
            return []
        
        # Java snippets are clean code (no file paths), so we can't filter by path
        # Return all snippets - the semantic search will find relevant matches
        logger.info(f"Using {len(all_diff_snippets)} diff snippets for batch files {batch_file_paths}")
        return all_diff_snippets

    async def _execute_stage_0_planning(self, request: ReviewRequestDto, is_incremental: bool = False) -> ReviewPlan:
        """
        Stage 0: Analyze metadata and generate a review plan.
        Uses structured output for reliable JSON parsing.
        """
        # Prepare context for prompt
        changed_files_summary = []
        if request.changedFiles:
            for f in request.changedFiles:
                changed_files_summary.append({
                    "path": f,
                    "type": "MODIFIED", 
                    "lines_added": "?", 
                    "lines_deleted": "?"
                })
        
        prompt = PromptBuilder.build_stage_0_planning_prompt(
            repo_slug=request.projectVcsRepoSlug,
            pr_id=str(request.pullRequestId),
            pr_title=request.prTitle or "",
            author="Unknown", 
            branch_name="source-branch", 
            target_branch=request.targetBranchName or "main",
            commit_hash=request.commitHash or "HEAD",
            changed_files_json=json.dumps(changed_files_summary, indent=2)
        )

        # Stage 0 uses direct LLM call (no tools needed - all metadata is provided)
        try:
            structured_llm = self.llm.with_structured_output(ReviewPlan)
            result = await structured_llm.ainvoke(prompt)
            if result:
                logger.info("Stage 0 planning completed with structured output")
                return result
        except Exception as e:
            logger.warning(f"Structured output failed for Stage 0: {e}")
            
        # Fallback to manual parsing
        try:
            response = await self.llm.ainvoke(prompt)
            content = extract_llm_response_text(response)
            return await self._parse_response(content, ReviewPlan)
        except Exception as e:
            logger.error(f"Stage 0 planning failed: {e}")
            raise ValueError(f"Stage 0 planning failed: {e}")

    def _chunk_files(self, file_groups: List[Any], max_files_per_batch: int = 5) -> List[List[Dict[str, Any]]]:
        """Flatten file groups and chunk into batches."""
        all_files = []
        for group in file_groups:
            for f in group.files:
                # Attach priority context for the review
                all_files.append({
                    "file": f,
                    "priority": group.priority
                })
        
        return [all_files[i:i + max_files_per_batch] for i in range(0, len(all_files), max_files_per_batch)]

    async def _execute_stage_1_file_reviews(
        self, 
        request: ReviewRequestDto, 
        plan: ReviewPlan,
        rag_context: Optional[Dict[str, Any]] = None,
        processed_diff: Optional[ProcessedDiff] = None,
        is_incremental: bool = False
    ) -> List[CodeReviewIssue]:
        """
        Stage 1: Execute batch file reviews with per-batch RAG context.
        """
        # Use smaller batches (3 files max) for better RAG relevance and review quality
        batches = self._chunk_files(plan.file_groups, max_files_per_batch=3)
        
        total_files = sum(len(batch) for batch in batches)
        logger.info(f"Stage 1: Processing {total_files} files in {len(batches)} batches (max 3 files/batch)")
        
        # Process batches with controlled parallelism
        all_issues = []
        batch_results = []
        
        # Process in waves to avoid rate limits
        for wave_start in range(0, len(batches), self.max_parallel_stage_1):
            wave_end = min(wave_start + self.max_parallel_stage_1, len(batches))
            wave_batches = batches[wave_start:wave_end]
            
            logger.info(f"Stage 1: Processing wave {wave_start // self.max_parallel_stage_1 + 1}, "
                       f"batches {wave_start + 1}-{wave_end} of {len(batches)}")
            
            tasks = []
            for batch_idx, batch in enumerate(wave_batches, start=wave_start + 1):
                batch_paths = [item["file"].path for item in batch]
                logger.debug(f"Batch {batch_idx}: {batch_paths}")
                tasks.append(self._review_file_batch(
                    request, batch, processed_diff, is_incremental, 
                    fallback_rag_context=rag_context
                ))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for idx, res in enumerate(results):
                batch_num = wave_start + idx + 1
                if isinstance(res, Exception):
                    logger.error(f"Error reviewing batch {batch_num}: {res}")
                elif res:
                    logger.info(f"Batch {batch_num} completed: {len(res)} issues found")
                    all_issues.extend(res)
                else:
                    logger.info(f"Batch {batch_num} completed: no issues found")
            
            # Update progress
            progress = 10 + int((wave_end / len(batches)) * 50)
            self._emit_progress(progress, f"Stage 1: Reviewed {wave_end}/{len(batches)} batches")
        
        logger.info(f"Stage 1 Complete: {len(all_issues)} issues found across {total_files} files")
        return all_issues

    async def _fetch_batch_rag_context(
        self,
        request: ReviewRequestDto,
        batch_file_paths: List[str],
        batch_diff_snippets: List[str]
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch RAG context specifically for this batch of files.
        Uses batch file paths and diff snippets for targeted semantic search.
        
        In hybrid mode (when PR files are indexed), passes pr_number to enable
        queries that prioritize fresh PR data over potentially stale branch data.
        """
        if not self.rag_client:
            return None
        
        try:
            # Determine branch for RAG query
            rag_branch = request.targetBranchName or request.commitHash or "main"
            
            logger.info(f"Fetching per-batch RAG context for {len(batch_file_paths)} files")
            
            # Use hybrid mode if PR files were indexed
            pr_number = request.pullRequestId if self._pr_indexed else None
            all_pr_files = request.changedFiles if self._pr_indexed else None
            
            rag_response = await self.rag_client.get_pr_context(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                branch=rag_branch,
                changed_files=batch_file_paths,
                diff_snippets=batch_diff_snippets,
                pr_title=request.prTitle,
                pr_description=request.prDescription,
                top_k=10,  # Fewer chunks per batch for focused context
                pr_number=pr_number,
                all_pr_changed_files=all_pr_files
            )
            
            if rag_response and rag_response.get("context"):
                context = rag_response.get("context")
                chunk_count = len(context.get("relevant_code", []))
                logger.info(f"Per-batch RAG: retrieved {chunk_count} chunks for files {batch_file_paths}")
                return context
            
            return None
            
        except Exception as e:
            logger.warning(f"Failed to fetch per-batch RAG context: {e}")
            return None

    async def _review_file_batch(
        self,
        request: ReviewRequestDto,
        batch_items: List[Dict[str, Any]],
        processed_diff: Optional[ProcessedDiff] = None,
        is_incremental: bool = False,
        fallback_rag_context: Optional[Dict[str, Any]] = None
    ) -> List[CodeReviewIssue]:
        """
        Review a batch of files in a single LLM call with per-batch RAG context.
        In incremental mode, uses delta diff and focuses on new changes only.
        """
        batch_files_data = []
        batch_file_paths = []
        batch_diff_snippets = []
        #TODO: Project custom rules
        project_rules = ""

        # For incremental mode, use deltaDiff instead of full diff
        diff_source = None
        if is_incremental and request.deltaDiff:
            # Parse delta diff to extract per-file diffs
            diff_source = DiffProcessor().process(request.deltaDiff) if request.deltaDiff else None
        else:
            diff_source = processed_diff

        # Collect file paths, diffs, and extract snippets for this batch
        for item in batch_items:
            file_info = item["file"]
            batch_file_paths.append(file_info.path)
            
            # Extract diff from the appropriate source (delta for incremental, full for initial)
            file_diff = ""
            if diff_source:
                for f in diff_source.files:
                    if f.path == file_info.path or f.path.endswith("/" + file_info.path):
                        file_diff = f.content
                        # Extract code snippets from diff for RAG semantic search
                        if file_diff:
                            batch_diff_snippets.extend(self._extract_diff_snippets(file_diff))
                        break
            
            batch_files_data.append({
                "path": file_info.path,
                "type": "MODIFIED",
                "focus_areas": file_info.focus_areas,
                "old_code": "",
                "diff": file_diff or "(Diff unavailable)",
                "is_incremental": is_incremental  # Pass mode to prompt builder
            })

        # Fetch per-batch RAG context using batch-specific files and diff snippets
        rag_context_text = ""
        batch_rag_context = None
        
        if self.rag_client:
            batch_rag_context = await self._fetch_batch_rag_context(
                request, batch_file_paths, batch_diff_snippets
            )
        
        # Use batch-specific RAG context if available, otherwise fall back to initial context
        # Hybrid mode: PR-indexed data is already included via _fetch_batch_rag_context
        if batch_rag_context:
            logger.info(f"Using per-batch RAG context for: {batch_file_paths}")
            rag_context_text = self._format_rag_context(
                batch_rag_context,
                set(batch_file_paths),
                pr_changed_files=request.changedFiles
            )
        elif fallback_rag_context:
            logger.info(f"Using fallback RAG context for batch: {batch_file_paths}")
            rag_context_text = self._format_rag_context(
                fallback_rag_context,
                set(batch_file_paths),
                pr_changed_files=request.changedFiles
            )
        
        logger.info(f"RAG context for batch: {len(rag_context_text)} chars")

        # For incremental mode, filter previous issues relevant to this batch
        # Also pass previous issues in FULL mode if they exist (subsequent PR iterations)
        previous_issues_for_batch = ""
        has_previous_issues = request.previousCodeAnalysisIssues and len(request.previousCodeAnalysisIssues) > 0
        if has_previous_issues:
            relevant_prev_issues = [
                issue for issue in request.previousCodeAnalysisIssues
                if self._issue_matches_files(issue, batch_file_paths)
            ]
            if relevant_prev_issues:
                previous_issues_for_batch = self._format_previous_issues_for_batch(relevant_prev_issues)

        # Build ONE prompt for the batch
        prompt = PromptBuilder.build_stage_1_batch_prompt(
            files=batch_files_data,
            priority=batch_items[0]["priority"] if batch_items else "MEDIUM",
            project_rules=project_rules,
            rag_context=rag_context_text,
            is_incremental=is_incremental,
            previous_issues=previous_issues_for_batch
        )

        # Stage 1 uses direct LLM call (no tools needed - diff is already provided)
        try:
            # Try structured output first
            structured_llm = self.llm.with_structured_output(FileReviewBatchOutput)
            result = await structured_llm.ainvoke(prompt)
            if result:
                all_batch_issues = []
                for review in result.reviews:
                    all_batch_issues.extend(review.issues)
                return all_batch_issues
        except Exception as e:
            logger.warning(f"Structured output failed for Stage 1 batch: {e}")
            
            # Fallback to manual parsing
            try:
                response = await self.llm.ainvoke(prompt)
                content = extract_llm_response_text(response)
                data = await self._parse_response(content, FileReviewBatchOutput)
                all_batch_issues = []
                for review in data.reviews:
                    all_batch_issues.extend(review.issues)
                return all_batch_issues
            except Exception as parse_err:
                logger.error(f"Batch review failed: {parse_err}")
                return []
        
        return []

    async def _execute_stage_2_cross_file(
        self,
        request: ReviewRequestDto,
        stage_1_issues: List[CodeReviewIssue],
        plan: ReviewPlan
    ) -> CrossFileAnalysisResult:
        """
        Stage 2: Cross-file analysis.
        """
        # Serialize Stage 1 findings
        issues_json = json.dumps([i.model_dump() for i in stage_1_issues], indent=2)
        
        prompt = PromptBuilder.build_stage_2_cross_file_prompt(
            repo_slug=request.projectVcsRepoSlug,
            pr_title=request.prTitle or "",
            commit_hash=request.commitHash or "HEAD",
            stage_1_findings_json=issues_json,
            architecture_context="(Architecture context from MCP or knowledge base)", 
            migrations="(Migration scripts found in PR)", 
            cross_file_concerns=plan.cross_file_concerns
        )

        # Stage 2 uses direct LLM call (no tools needed - all data is provided from Stage 1)
        try:
            structured_llm = self.llm.with_structured_output(CrossFileAnalysisResult)
            result = await structured_llm.ainvoke(prompt)
            if result:
                logger.info("Stage 2 cross-file analysis completed with structured output")
                return result
        except Exception as e:
            logger.warning(f"Structured output failed for Stage 2: {e}")
            
        # Fallback to manual parsing
        try:
            response = await self.llm.ainvoke(prompt)
            content = extract_llm_response_text(response)
            return await self._parse_response(content, CrossFileAnalysisResult)
        except Exception as e:
            logger.error(f"Stage 2 cross-file analysis failed: {e}")
            raise

    async def _execute_stage_3_aggregation(
        self,
        request: ReviewRequestDto,
        plan: ReviewPlan,
        stage_1_issues: List[CodeReviewIssue],
        stage_2_results: CrossFileAnalysisResult,
        is_incremental: bool = False
    ) -> str:
        """
        Stage 3: Generate Markdown report.
        In incremental mode, includes summary of resolved vs new issues.
        """
        stage_1_json = json.dumps([i.model_dump() for i in stage_1_issues], indent=2)
        stage_2_json = stage_2_results.model_dump_json(indent=2)
        plan_json = plan.model_dump_json(indent=2)
        
        # Add incremental context to aggregation
        incremental_context = ""
        if is_incremental:
            resolved_count = sum(1 for i in stage_1_issues if i.isResolved)
            new_count = len(stage_1_issues) - resolved_count
            previous_count = len(request.previousCodeAnalysisIssues or [])
            incremental_context = f"""
## INCREMENTAL REVIEW SUMMARY
- Previous issues from last review: {previous_count}
- Issues resolved in this update: {resolved_count}
- New issues found in delta: {new_count}
- Total issues after reconciliation: {len(stage_1_issues)}
"""

        prompt = PromptBuilder.build_stage_3_aggregation_prompt(
            repo_slug=request.projectVcsRepoSlug,
            pr_id=str(request.pullRequestId),
            author="Unknown",
            pr_title=request.prTitle or "",
            total_files=len(request.changedFiles or []),
            additions=0, # Need accurate stats
            deletions=0,
            stage_0_plan=plan_json,
            stage_1_issues_json=stage_1_json,
            stage_2_findings_json=stage_2_json,
            recommendation=stage_2_results.pr_recommendation
        )

        response = await self.llm.ainvoke(prompt)
        return extract_llm_response_text(response)

    async def _parse_response(self, content: str, model_class: Any, retries: int = 2) -> Any:
        """
        Robustly parse JSON response into a Pydantic model with retries.
        Falls back to manual parsing if structured output wasn't used.
        """
        last_error = None
        
        # Initial cleaning attempt
        try:
            cleaned = self._clean_json_text(content)
            logger.debug(f"Cleaned JSON for {model_class.__name__} (first 500 chars): {cleaned[:500]}")
            data = json.loads(cleaned)
            return model_class(**data)
        except Exception as e:
            last_error = e
            logger.warning(f"Initial parse failed for {model_class.__name__}: {e}")
            logger.debug(f"Raw content (first 1000 chars): {content[:1000]}")

        # Retry with structured output if available
        try:
            logger.info(f"Attempting structured output retry for {model_class.__name__}")
            structured_llm = self.llm.with_structured_output(model_class)
            result = await structured_llm.ainvoke(
                f"Parse and return this as valid {model_class.__name__}:\n{content[:4000]}"
            )
            if result:
                logger.info(f"Structured output retry succeeded for {model_class.__name__}")
                return result
        except Exception as e:
            logger.warning(f"Structured output retry failed: {e}")
            last_error = e

        # Final fallback: LLM repair loop
        for attempt in range(retries):
            try:
                logger.info(f"Repairing JSON for {model_class.__name__}, attempt {attempt+1}")
                repaired = await self._repair_json_with_llm(
                    content, 
                    str(last_error), 
                    model_class.model_json_schema()
                )
                cleaned = self._clean_json_text(repaired)
                logger.debug(f"Repaired JSON attempt {attempt+1} (first 500 chars): {cleaned[:500]}")
                data = json.loads(cleaned)
                return model_class(**data)
            except Exception as e:
                last_error = e
                logger.warning(f"Retry {attempt+1} failed: {e}")
        
        raise ValueError(f"Failed to parse {model_class.__name__} after retries: {last_error}")

    async def _repair_json_with_llm(self, broken_json: str, error: str, schema: Any) -> str:
        """
        Ask LLM to repair malformed JSON.
        """
        # Truncate the broken JSON to avoid token limits but show enough context
        truncated_json = broken_json[:3000] if len(broken_json) > 3000 else broken_json
        
        prompt = f"""You are a JSON repair expert. 
The following JSON failed to parse/validate:
Error: {error}

Broken JSON:
{truncated_json}

Required Schema (the output MUST be a JSON object, not an array):
{json.dumps(schema, indent=2)}

CRITICAL INSTRUCTIONS:
1. Return ONLY the fixed valid JSON object
2. The response MUST start with {{ and end with }}
3. All property names MUST be enclosed in double quotes
4. No markdown code blocks (no ```)
5. No explanatory text before or after the JSON
6. Ensure all required fields from the schema are present

Output the corrected JSON object now:"""
        response = await self.llm.ainvoke(prompt)
        return extract_llm_response_text(response)

    def _clean_json_text(self, text: str) -> str:
        """
        Clean markdown and extraneous text from JSON.
        """
        text = text.strip()
        
        # Remove markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            # Skip the opening ``` line (with or without language identifier)
            lines = lines[1:]
            # Remove trailing ``` if present
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        
        # Also handle case where ``` appears mid-text
        if "```json" in text:
            start_idx = text.find("```json")
            end_idx = text.find("```", start_idx + 7)
            if end_idx != -1:
                text = text[start_idx + 7:end_idx].strip()
            else:
                text = text[start_idx + 7:].strip()
        elif "```" in text:
            # Generic code block without language
            start_idx = text.find("```")
            remaining = text[start_idx + 3:]
            end_idx = remaining.find("```")
            if end_idx != -1:
                text = remaining[:end_idx].strip()
            else:
                text = remaining.strip()
        
        # Find JSON object boundaries
        obj_start = text.find("{")
        obj_end = text.rfind("}")
        arr_start = text.find("[")
        arr_end = text.rfind("]")
        
        # Determine if we have an object or array (whichever comes first)
        if obj_start != -1 and obj_end != -1:
            if arr_start == -1 or obj_start < arr_start:
                # Object comes first or no array
                text = text[obj_start:obj_end+1]
            elif arr_start < obj_start and arr_end != -1:
                # Array comes first - but we need an object for Pydantic
                # Check if the object is nested inside the array or separate
                if obj_end > arr_end:
                    # Object extends beyond array - likely the object we want
                    text = text[obj_start:obj_end+1]
                else:
                    # Try to use the object anyway
                    text = text[obj_start:obj_end+1]
        elif arr_start != -1 and arr_end != -1 and obj_start == -1:
            # Only array found - log warning as Pydantic models expect objects
            logger.warning(f"JSON cleaning found array instead of object, this may fail parsing")
            text = text[arr_start:arr_end+1]
        
        return text

    def _format_rag_context(
        self, 
        rag_context: Optional[Dict[str, Any]], 
        relevant_files: Optional[set] = None,
        pr_changed_files: Optional[List[str]] = None
    ) -> str:
        """
        Format RAG context into a readable string for the prompt.
        
        IMPORTANT: We trust RAG's semantic similarity scores for relevance.
        The RAG system already uses embeddings to find semantically related code.
        We only filter out chunks from files being modified in the PR (stale data from main branch).
        
        Args:
            rag_context: RAG response with code chunks
            relevant_files: (UNUSED - kept for API compatibility) - we trust RAG scores instead
            pr_changed_files: Files modified in the PR - chunks from these may be stale
        """
        if not rag_context:
            logger.debug("RAG context is empty or None")
            return ""
        
        # Handle both "chunks" and "relevant_code" keys (RAG API uses "relevant_code")
        chunks = rag_context.get("relevant_code", []) or rag_context.get("chunks", [])
        if not chunks:
            logger.debug("No chunks found in RAG context (keys: %s)", list(rag_context.keys()))
            return ""
        
        logger.info(f"Processing {len(chunks)} RAG chunks (trusting semantic similarity scores)")
        
        # Normalize PR changed files for stale-data detection only
        pr_changed_set = set()
        if pr_changed_files:
            for f in pr_changed_files:
                pr_changed_set.add(f)
                if "/" in f:
                    pr_changed_set.add(f.rsplit("/", 1)[-1])
        
        formatted_parts = []
        included_count = 0
        skipped_stale = 0
        
        for chunk in chunks:
            if included_count >= 15:
                logger.debug(f"Reached chunk limit of 15")
                break
                
            metadata = chunk.get("metadata", {})
            path = metadata.get("path", chunk.get("path", "unknown"))
            chunk_type = metadata.get("content_type", metadata.get("type", "code"))
            score = chunk.get("score", chunk.get("relevance_score", 0))
            
            # Only filter: chunks from PR-modified files with LOW scores (likely stale)
            # High-score chunks from modified files may still be relevant (other parts of same file)
            if pr_changed_set:
                path_filename = path.rsplit("/", 1)[-1] if "/" in path else path
                is_from_modified_file = (
                    path in pr_changed_set or 
                    path_filename in pr_changed_set or
                    any(path.endswith(f) or f.endswith(path) for f in pr_changed_set)
                )
                
                # Skip ONLY low-score chunks from modified files (likely stale/outdated)
                if is_from_modified_file and score < 0.70:
                    logger.debug(f"Skipping stale chunk from modified file: {path} (score={score:.2f})")
                    skipped_stale += 1
                    continue
            
            text = chunk.get("text", chunk.get("content", ""))
            if not text:
                continue
            
            included_count += 1
            
            # Build rich metadata context
            meta_lines = [f"File: {path}"]
            
            if metadata.get("namespace"):
                meta_lines.append(f"Namespace: {metadata['namespace']}")
            elif metadata.get("package"):
                meta_lines.append(f"Package: {metadata['package']}")
            
            if metadata.get("primary_name"):
                meta_lines.append(f"Definition: {metadata['primary_name']}")
            elif metadata.get("semantic_names"):
                meta_lines.append(f"Definitions: {', '.join(metadata['semantic_names'][:5])}")
            
            if metadata.get("extends"):
                extends = metadata["extends"]
                meta_lines.append(f"Extends: {', '.join(extends) if isinstance(extends, list) else extends}")
            
            if metadata.get("implements"):
                implements = metadata["implements"]
                meta_lines.append(f"Implements: {', '.join(implements) if isinstance(implements, list) else implements}")
            
            if metadata.get("imports"):
                imports = metadata["imports"]
                if isinstance(imports, list):
                    if len(imports) <= 5:
                        meta_lines.append(f"Imports: {'; '.join(imports)}")
                    else:
                        meta_lines.append(f"Imports: {'; '.join(imports[:5])}... (+{len(imports)-5} more)")
            
            if metadata.get("parent_context"):
                parent_ctx = metadata["parent_context"]
                if isinstance(parent_ctx, list):
                    meta_lines.append(f"Parent: {'.'.join(parent_ctx)}")
            
            if chunk_type and chunk_type != "code":
                meta_lines.append(f"Type: {chunk_type}")
            
            meta_text = "\n".join(meta_lines)
            # Use file path as primary identifier, not a number
            # This encourages AI to reference by path rather than by chunk number
            formatted_parts.append(
                f"### Context from `{path}` (relevance: {score:.2f})\n"
                f"{meta_text}\n"
                f"```\n{text}\n```\n"
            )
        
        if not formatted_parts:
            logger.warning(f"No RAG chunks included (total: {len(chunks)}, skipped_stale: {skipped_stale})")
            return ""
        
        logger.info(f"Included {len(formatted_parts)} RAG chunks (skipped {skipped_stale} stale from modified files)")
        return "\n".join(formatted_parts)

    def _emit_status(self, state: str, message: str):
        if self.event_callback:
            self.event_callback({
                "type": "status",
                "state": state,
                "message": message
            })

    def _emit_progress(self, percent: int, message: str):
        if self.event_callback:
            self.event_callback({
                "type": "progress",
                "percent": percent,
                "message": message
            })
            
    def _emit_error(self, message: str):
        if self.event_callback:
            self.event_callback({
                "type": "error",
                "message": message
            })

    def _count_files(self, plan: ReviewPlan) -> int:
        count = 0
        for group in plan.file_groups:
            count += len(group.files)
        return count

    def _ensure_all_files_planned(self, plan: ReviewPlan, all_changed_files: List[str]) -> ReviewPlan:
        """
        Ensure all changed files are included in the review plan.
        If LLM missed files, add them to a LOW priority group.
        """
        # Collect files already in the plan
        planned_files = set()
        for group in plan.file_groups:
            for f in group.files:
                planned_files.add(f.path)
        
        # Also count skipped files
        skipped_files = set()
        for skip in plan.files_to_skip:
            skipped_files.add(skip.path)
        
        # Find missing files
        all_files_set = set(all_changed_files)
        missing_files = all_files_set - planned_files - skipped_files
        
        if missing_files:
            logger.warning(
                f"Stage 0 plan missing {len(missing_files)} files out of {len(all_changed_files)}. "
                f"Adding to LOW priority group."
            )
            
            # Create ReviewFile objects for missing files
            missing_review_files = []
            for path in missing_files:
                missing_review_files.append(ReviewFile(
                    path=path,
                    focus_areas=["GENERAL"],
                    risk_level="LOW",
                    estimated_issues=0
                ))
            
            # Add a new group for missing files or append to existing LOW group
            low_group_found = False
            for group in plan.file_groups:
                if group.priority == "LOW":
                    group.files.extend(missing_review_files)
                    low_group_found = True
                    break
            
            if not low_group_found:
                plan.file_groups.append(FileGroup(
                    group_id="GROUP_MISSING_FILES",
                    priority="LOW",
                    rationale="Files not categorized by planner - added automatically",
                    files=missing_review_files
                ))
            
            logger.info(f"Plan now includes {self._count_files(plan)} files for review")
        else:
            logger.info(
                f"Stage 0 plan complete: {len(planned_files)} files to review, "
                f"{len(skipped_files)} files skipped"
            )
        
        return plan
