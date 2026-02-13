"""
Stage execution methods for the multi-stage review pipeline.
"""
import json
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Callable

from model.dtos import ReviewRequestDto
from model.enrichment import PrEnrichmentDataDto
from model.output_schemas import CodeReviewOutput, CodeReviewIssue
from model.multi_stage import (
    ReviewPlan,
    ReviewFile,
    FileGroup,
    CrossFileAnalysisResult,
    FileReviewBatchOutput,
)
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff, DiffProcessor
from utils.dependency_graph import create_smart_batches

from service.review.orchestrator.agents import RecursiveMCPAgent, extract_llm_response_text
from service.review.orchestrator.json_utils import parse_llm_response
from service.review.orchestrator.reconciliation import (
    issue_matches_files,
    format_previous_issues_for_batch,
)
from service.review.orchestrator.context_helpers import (
    extract_diff_snippets,
    format_rag_context,
)

logger = logging.getLogger(__name__)


async def execute_branch_analysis(
    llm,
    client,
    prompt: str,
    event_callback: Optional[Callable[[Dict], None]] = None
) -> Dict[str, Any]:
    """
    Execute a single-pass branch analysis using the provided prompt.
    """
    _emit_status(event_callback, "branch_analysis_started", "Starting Branch Analysis & Reconciliation...")
    
    agent = RecursiveMCPAgent(
        llm=llm, 
        client=client,
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
            data = await parse_llm_response(final_text, CodeReviewOutput, llm)
            issues = [i.model_dump() for i in data.issues] if data.issues else []
            return {
                "issues": issues,
                "comment": data.comment or "Branch analysis completed."
            }
            
        return {"issues": [], "comment": "No issues found."}
        
    except Exception as e:
        logger.error(f"Branch analysis failed: {e}", exc_info=True)
        _emit_error(event_callback, str(e))
        raise


async def execute_stage_0_planning(
    llm,
    request: ReviewRequestDto, 
    is_incremental: bool = False,
    processed_diff: Optional[ProcessedDiff] = None,
) -> ReviewPlan:
    """
    Stage 0: Analyze metadata and generate a review plan.
    Uses structured output for reliable JSON parsing.
    """
    # Build a path → DiffFile lookup for real line stats
    diff_by_path: Dict[str, Any] = {}
    if processed_diff:
        for df in processed_diff.files:
            diff_by_path[df.path] = df
            # Also index by basename for fuzzy matching
            if '/' in df.path:
                diff_by_path[df.path.rsplit('/', 1)[-1]] = df

    # Prepare context for prompt
    changed_files_summary = []
    if request.changedFiles:
        for f in request.changedFiles:
            df = diff_by_path.get(f) or diff_by_path.get(f.rsplit('/', 1)[-1] if '/' in f else f)
            changed_files_summary.append({
                "path": f,
                "type": df.change_type.value.upper() if df else "MODIFIED",
                "lines_added": df.additions if df else "?",
                "lines_deleted": df.deletions if df else "?",
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
        structured_llm = llm.with_structured_output(ReviewPlan)
        result = await structured_llm.ainvoke(prompt)
        if result:
            logger.info("Stage 0 planning completed with structured output")
            return result
    except Exception as e:
        logger.warning(f"Structured output failed for Stage 0: {e}")
        
    # Fallback to manual parsing
    try:
        response = await llm.ainvoke(prompt)
        content = extract_llm_response_text(response)
        return await parse_llm_response(content, ReviewPlan, llm)
    except Exception as e:
        logger.error(f"Stage 0 planning failed: {e}")
        raise ValueError(f"Stage 0 planning failed: {e}")


def chunk_files(file_groups: List[Any], max_files_per_batch: int = 5) -> List[List[Dict[str, Any]]]:
    """
    Flatten file groups and chunk into batches.
    DEPRECATED: Use create_smart_batches for dependency-aware batching.
    Kept for fallback when diff content is unavailable.
    """
    all_files = []
    for group in file_groups:
        for f in group.files:
            # Attach priority context for the review
            all_files.append({
                "file": f,
                "priority": group.priority
            })
    
    return [all_files[i:i + max_files_per_batch] for i in range(0, len(all_files), max_files_per_batch)]


def create_smart_batches_wrapper(
    file_groups: List[Any], 
    processed_diff: Optional[ProcessedDiff],
    request: ReviewRequestDto,
    rag_client,
    max_files_per_batch: int = 7
) -> List[List[Dict[str, Any]]]:
    """
    Create dependency-aware batches that keep related files together.
    
    If enrichment data is available from Java (pre-computed relationships),
    use it directly. Otherwise, use RAG's tree-sitter metadata to discover 
    file relationships:
    - imports/exports: which files use symbols from other files
    - class context: methods in the same class
    - namespace context: files in the same package
    
    Falls back to directory-based grouping if both are unavailable.
    """
    # Build branches list for RAG query
    branches = []
    if request.targetBranchName:
        branches.append(request.targetBranchName)
    # Note: sourceBranchName is not in ReviewRequestDto, skip this check
    if not branches:
        branches = ['main', 'master']  # Fallback
    
    # Check for enrichment data from Java
    enrichment_data = getattr(request, 'enrichmentData', None)
    
    try:
        # Use RAG-based dependency analysis for intelligent batching
        batches = create_smart_batches(
            file_groups=file_groups, 
            workspace=request.projectWorkspace,
            project=request.projectNamespace,
            branches=branches,
            rag_client=rag_client,
            max_batch_size=max_files_per_batch,
            enrichment_data=enrichment_data
        )
        
        # Log relationship analysis results
        total_files = sum(len(b) for b in batches)
        related_files = sum(1 for b in batches for f in b if f.get('has_relationships'))
        enrichment_source = "enrichment data" if enrichment_data else "RAG discovery"
        logger.info(f"Smart batching ({enrichment_source}): {total_files} files in {len(batches)} batches, "
                   f"{related_files} files have cross-file relationships")
        
        return batches
    except Exception as e:
        logger.warning(f"Smart batching failed, falling back to simple batching: {e}")
        return chunk_files(file_groups, max_files_per_batch)


async def execute_stage_1_file_reviews(
    llm,
    request: ReviewRequestDto, 
    plan: ReviewPlan,
    rag_client,
    rag_context: Optional[Dict[str, Any]] = None,
    processed_diff: Optional[ProcessedDiff] = None,
    is_incremental: bool = False,
    max_parallel: int = 5,
    event_callback: Optional[Callable[[Dict], None]] = None,
    pr_indexed: bool = False
) -> List[CodeReviewIssue]:
    """
    Stage 1: Execute batch file reviews with per-batch RAG context.
    Uses dependency-aware batching to keep related files together.
    """
    # Use smart batching with RAG-based relationship discovery
    batches = create_smart_batches_wrapper(
        plan.file_groups, processed_diff, request, rag_client, max_files_per_batch=7
    )
    
    total_files = sum(len(batch) for batch in batches)
    related_batches = sum(1 for b in batches if any(f.get('has_relationships') for f in b))
    logger.info(f"Stage 1: Processing {total_files} files in {len(batches)} batches "
               f"({related_batches} batches with cross-file relationships)")
    
    # Process batches with controlled parallelism
    all_issues = []
    
    # Process in waves to avoid rate limits
    for wave_start in range(0, len(batches), max_parallel):
        wave_end = min(wave_start + max_parallel, len(batches))
        wave_batches = batches[wave_start:wave_end]
        wave_num = wave_start // max_parallel + 1
        
        logger.info(f"Stage 1: Processing wave {wave_num}, "
                   f"batches {wave_start + 1}-{wave_end} of {len(batches)} IN PARALLEL")
        
        wave_start_time = time.time()
        
        tasks = []
        for batch_idx, batch in enumerate(wave_batches, start=wave_start + 1):
            batch_paths = [item["file"].path for item in batch]
            has_rels = any(item.get('has_relationships') for item in batch)
            logger.debug(f"Batch {batch_idx}: {batch_paths} (cross-file relationships: {has_rels})")
            # Create coroutine with batch_idx for tracking
            tasks.append(_review_batch_with_timing(
                batch_idx, llm, request, batch, rag_client, processed_diff, 
                is_incremental, rag_context, pr_indexed
            ))
        
        # asyncio.gather runs all tasks CONCURRENTLY
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        wave_elapsed = time.time() - wave_start_time
        logger.info(f"Wave {wave_num} completed in {wave_elapsed:.2f}s "
                   f"({len(wave_batches)} batches parallel)")
        
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
        _emit_progress(event_callback, progress, f"Stage 1: Reviewed {wave_end}/{len(batches)} batches")
    
    logger.info(f"Stage 1 Complete: {len(all_issues)} issues found across {total_files} files")
    return all_issues


async def _review_batch_with_timing(
    batch_idx: int,
    llm,
    request: ReviewRequestDto,
    batch: List[Dict[str, Any]],
    rag_client,
    processed_diff: Optional[ProcessedDiff],
    is_incremental: bool,
    fallback_rag_context: Optional[Dict[str, Any]],
    pr_indexed: bool
) -> List[CodeReviewIssue]:
    """
    Wrapper that adds timing logs to show parallel execution.
    """
    start_time = time.time()
    batch_paths = [item["file"].path for item in batch]
    logger.info(f"[Batch {batch_idx}] STARTED - files: {batch_paths}")
    
    try:
        result = await review_file_batch(
            llm, request, batch, rag_client, processed_diff, is_incremental,
            fallback_rag_context=fallback_rag_context, pr_indexed=pr_indexed
        )
        elapsed = time.time() - start_time
        logger.info(f"[Batch {batch_idx}] FINISHED in {elapsed:.2f}s - {len(result)} issues")
        return result
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[Batch {batch_idx}] FAILED after {elapsed:.2f}s: {e}")
        raise


async def fetch_batch_rag_context(
    rag_client,
    request: ReviewRequestDto,
    batch_file_paths: List[str],
    batch_diff_snippets: List[str],
    pr_indexed: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Fetch RAG context specifically for this batch of files.
    
    Two-pronged approach for comprehensive context:
    1. Semantic search using diff snippets (finds conceptually related code)
    2. Deterministic lookup using tree-sitter metadata (finds imported/referenced definitions)
    
    In hybrid mode (when PR files are indexed), passes pr_number to enable
    queries that prioritize fresh PR data over potentially stale branch data.
    """
    if not rag_client:
        return None
    
    try:
        # Determine branches for RAG query
        rag_branch = request.targetBranchName or request.commitHash or "main"
        base_branch = "main"  # Default base branch for deterministic context
        
        logger.info(f"Fetching per-batch RAG context for {len(batch_file_paths)} files")
        
        # Use hybrid mode if PR files were indexed
        pr_number = request.pullRequestId if pr_indexed else None
        all_pr_files = request.changedFiles if pr_indexed else None
        
        # 1. Semantic search for conceptually related code
        rag_response = await rag_client.get_pr_context(
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
        
        context = None
        if rag_response and rag_response.get("context"):
            context = rag_response.get("context")
            chunk_count = len(context.get("relevant_code", []))
            logger.info(f"Semantic RAG: retrieved {chunk_count} chunks for batch")
        
        # 2. Deterministic lookup for cross-file dependencies (imports, extends, etc.)
        # This uses tree-sitter metadata indexed during repo indexing
        try:
            deterministic_response = await rag_client.get_deterministic_context(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                branches=[rag_branch, base_branch],
                file_paths=batch_file_paths,
                limit_per_file=5  # Limit to avoid overwhelming context
            )
            
            if deterministic_response and deterministic_response.get("context"):
                det_context = deterministic_response.get("context")
                related_defs = det_context.get("related_definitions", {})
                
                if related_defs:
                    # Merge deterministic results into the main context
                    if context is None:
                        context = {"relevant_code": []}
                    
                    # Add related definitions as additional context chunks
                    for def_name, def_chunks in related_defs.items():
                        for chunk in def_chunks[:3]:  # Limit per definition
                            context["relevant_code"].append({
                                "file_path": chunk.get("file_path", ""),
                                "content": chunk.get("content", ""),
                                "score": 0.85,  # High score for deterministic matches
                                "source": "deterministic",
                                "definition_name": def_name
                            })
                    
                    logger.info(f"Deterministic RAG: added {len(related_defs)} related definitions")
                    
        except Exception as det_err:
            # Deterministic context is optional enhancement, don't fail the whole request
            logger.debug(f"Deterministic RAG lookup skipped: {det_err}")
        
        if context:
            total_chunks = len(context.get("relevant_code", []))
            logger.info(f"Total RAG context: {total_chunks} chunks for files {batch_file_paths}")
            return context
        
        return None
        
    except Exception as e:
        logger.warning(f"Failed to fetch per-batch RAG context: {e}")
        return None


def _filter_rag_chunks_for_batch(
    rag_context: Dict[str, Any],
    batch_file_paths: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Pre-filter global RAG context to keep only chunks whose source path
    is related to the current batch.  This avoids injecting the full
    15-chunk global set into every batch when the per-batch fetch fails.
    """
    chunks = rag_context.get("relevant_code", []) or rag_context.get("chunks", [])
    if not chunks:
        return rag_context

    batch_basenames = {p.rsplit("/", 1)[-1] if "/" in p else p for p in batch_file_paths}
    batch_dirs = set()
    for p in batch_file_paths:
        parts = p.rsplit("/", 1)
        if len(parts) == 2:
            batch_dirs.add(parts[0])

    filtered = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        chunk_path = meta.get("path") or chunk.get("path") or chunk.get("file_path", "")
        if not chunk_path:
            filtered.append(chunk)  # keep chunks without path info
            continue

        chunk_basename = chunk_path.rsplit("/", 1)[-1] if "/" in chunk_path else chunk_path
        chunk_dir = chunk_path.rsplit("/", 1)[0] if "/" in chunk_path else ""

        # Keep if same file, same directory, or high-score (>= 0.8) regardless
        score = chunk.get("score", chunk.get("relevance_score", 0))
        if (chunk_basename in batch_basenames
                or chunk_dir in batch_dirs
                or any(chunk_path.endswith(bp) or bp.endswith(chunk_path) for bp in batch_file_paths)
                or score >= 0.8):
            filtered.append(chunk)

    if not filtered:
        # Don't return empty — keep original as fallback
        return rag_context

    result = dict(rag_context)
    result["relevant_code"] = filtered
    return result


async def review_file_batch(
    llm,
    request: ReviewRequestDto,
    batch_items: List[Dict[str, Any]],
    rag_client,
    processed_diff: Optional[ProcessedDiff] = None,
    is_incremental: bool = False,
    fallback_rag_context: Optional[Dict[str, Any]] = None,
    pr_indexed: bool = False
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
                        batch_diff_snippets.extend(extract_diff_snippets(file_diff))
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
    
    if rag_client:
        batch_rag_context = await fetch_batch_rag_context(
            rag_client, request, batch_file_paths, batch_diff_snippets, pr_indexed
        )
    
    # Use batch-specific RAG context if available, otherwise fall back to initial context
    # Hybrid mode: PR-indexed data is already included via fetch_batch_rag_context
    if batch_rag_context:
        logger.info(f"Using per-batch RAG context for: {batch_file_paths}")
        rag_context_text = format_rag_context(
            batch_rag_context,
            set(batch_file_paths),
            pr_changed_files=request.changedFiles
        )
    elif fallback_rag_context:
        # Filter the global RAG context to chunks relevant to this batch.
        # Without filtering, every batch receives the same 15-chunk global set
        # (wasting tokens on context unrelated to the batch's files).
        filtered_fallback = _filter_rag_chunks_for_batch(
            fallback_rag_context, batch_file_paths
        )
        logger.info(
            f"Using filtered fallback RAG context for batch: {batch_file_paths} "
            f"({len((filtered_fallback or {}).get('relevant_code', []))} chunks)"
        )
        rag_context_text = format_rag_context(
            filtered_fallback,
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
            if issue_matches_files(issue, batch_file_paths)
        ]
        if relevant_prev_issues:
            previous_issues_for_batch = format_previous_issues_for_batch(relevant_prev_issues)

    # Build ONE prompt for the batch with cross-file awareness
    prompt = PromptBuilder.build_stage_1_batch_prompt(
        files=batch_files_data,
        priority=batch_items[0]["priority"] if batch_items else "MEDIUM",
        project_rules=project_rules,
        rag_context=rag_context_text,
        is_incremental=is_incremental,
        previous_issues=previous_issues_for_batch,
        all_pr_files=request.changedFiles  # Enable cross-file awareness in prompt
    )

    # Stage 1 uses direct LLM call (no tools needed - diff is already provided)
    try:
        # Try structured output first
        structured_llm = llm.with_structured_output(FileReviewBatchOutput)
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
            response = await llm.ainvoke(prompt)
            content = extract_llm_response_text(response)
            data = await parse_llm_response(content, FileReviewBatchOutput, llm)
            all_batch_issues = []
            for review in data.reviews:
                all_batch_issues.extend(review.issues)
            return all_batch_issues
        except Exception as parse_err:
            logger.error(
                f"Batch review double parse failure for {batch_file_paths}: {parse_err}. "
                "Zero issues will be reported for this batch — results may be incomplete."
            )
            return []


# ---------------------------------------------------------------------------
# Stage 2 / Stage 3 context helpers
# ---------------------------------------------------------------------------

# Simple substrings that mark a path as a migration / schema file.
# Checked with case-insensitive containment — no compiled regexes needed.
_MIGRATION_PATH_MARKERS = (
    '/db/migrate/', '/migrations/', '/migration/',
    '/flyway/', '/liquibase/', '/alembic/', '/changeset/',
)

# Fields to strip from CodeReviewIssue dicts before sending to Stage 2.
# Stage 2 only needs location + severity + reason to detect cross-file patterns.
_STAGE_2_STRIP_FIELDS = {
    'suggestedFixDiff', 'suggestedFixDescription', 'codeSnippet',
    'resolutionExplanation', 'resolvedInCommit', 'visibility',
}


def _build_architecture_context(
    enrichment: Optional[PrEnrichmentDataDto],
    changed_files: Optional[List[str]],
) -> str:
    """
    Synthesise an architecture-reference section from the enrichment data
    that pipeline-agent already computed (class hierarchy, inter-file
    relationships, key imports).  Zero extra LLM / RAG cost.
    """
    sections: List[str] = []

    if enrichment and enrichment.relationships:
        rel_lines = []
        for r in enrichment.relationships:
            rel_lines.append(
                f"  {r.sourceFile} --[{r.relationshipType.value}]--> {r.targetFile}"
                + (f"  (matched on: {r.matchedOn})" if r.matchedOn else "")
            )
        if rel_lines:
            sections.append(
                "### Inter-file relationships (from dependency analysis)\n"
                + "\n".join(rel_lines)
            )

    if enrichment and enrichment.fileMetadata:
        hierarchy_lines = []
        for meta in enrichment.fileMetadata:
            parts = []
            if meta.extendsClasses:
                parts.append(f"extends {', '.join(meta.extendsClasses)}")
            if meta.implementsInterfaces:
                parts.append(f"implements {', '.join(meta.implementsInterfaces)}")
            if parts:
                hierarchy_lines.append(f"  {meta.path}: {'; '.join(parts)}")
        if hierarchy_lines:
            sections.append(
                "### Class hierarchy in changed files\n"
                + "\n".join(hierarchy_lines)
            )

        # Summarise cross-file imports between changed files
        if changed_files:
            changed_set = set(changed_files or [])
            import_lines = []
            for meta in enrichment.fileMetadata:
                cross_imports = [
                    imp for imp in meta.imports
                    if any(imp in cf or cf.endswith(imp) for cf in changed_set)
                ]
                if cross_imports:
                    import_lines.append(
                        f"  {meta.path} imports: {', '.join(cross_imports[:10])}"
                    )
            if import_lines:
                sections.append(
                    "### Cross-file imports among changed files\n"
                    + "\n".join(import_lines)
                )

    if not sections:
        return "No architecture context available (enrichment data not provided)."

    return "\n\n".join(sections)


def _detect_migration_paths(
    processed_diff: Optional[ProcessedDiff],
) -> str:
    """
    Return a short list of migration file paths found in the diff.

    Stage 1 already reviews each migration file in full detail.
    Stage 2 only needs to *know which files are migrations* so it can
    reason about cross-file DB concerns (e.g. code referencing a column
    that a migration drops).  No raw SQL is injected.
    """
    if not processed_diff:
        return "No migration scripts detected."

    migration_files: List[str] = []
    for f in processed_diff.files:
        path_lower = f.path.lower()
        if path_lower.endswith('.sql') or any(m in path_lower for m in _MIGRATION_PATH_MARKERS):
            migration_files.append(f.path)

    if not migration_files:
        return "No migration scripts detected in this PR."

    listing = "\n".join(f"- {p}" for p in migration_files[:15])  # cap at 15
    return f"Migration files in this PR ({len(migration_files)}):\n{listing}"


def _slim_issues_for_stage_2(
    issues: List[CodeReviewIssue],
) -> str:
    """
    Serialize Stage 1 issues for Stage 2, stripping bulky fields that
    Stage 2 does not need (fix diffs, code snippets, resolution details).

    Stage 2 detects *cross-file patterns* — it only needs:
    file, line, severity, category, title/reason.
    """
    slim = []
    for issue in issues:
        d = issue.model_dump()
        for key in _STAGE_2_STRIP_FIELDS:
            d.pop(key, None)
        slim.append(d)
    return json.dumps(slim, indent=2)


def _summarize_issues_for_stage_3(
    issues: List[CodeReviewIssue],
) -> str:
    """
    Build a compact summary of Stage 1 issues for Stage 3 (executive report).

    The full issue list is posted as a separate comment, so Stage 3 only needs
    aggregate counts and a short list of the most critical findings.
    """
    if not issues:
        return "No issues found in Stage 1."

    # --- Counts by severity ---
    severity_counts: Dict[str, int] = {}
    category_counts: Dict[str, int] = {}
    for issue in issues:
        sev = issue.severity.upper()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        cat = issue.category.upper()
        category_counts[cat] = category_counts.get(cat, 0) + 1

    lines = [
        f"Total issues: {len(issues)}",
        "By severity: " + ", ".join(f"{k}: {v}" for k, v in sorted(severity_counts.items())),
        "By category: " + ", ".join(f"{k}: {v}" for k, v in sorted(category_counts.items())),
    ]

    # --- Top critical/high issues (title + file only) ---
    priority_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3, 'INFO': 4}
    ranked = sorted(issues, key=lambda i: priority_order.get(i.severity.upper(), 5))
    top_n = ranked[:10]
    if top_n:
        lines.append("\nTop findings:")
        for i, issue in enumerate(top_n, 1):
            lines.append(f"  {i}. [{issue.severity}] {issue.file}: {issue.reason[:120]}")

    return "\n".join(lines)


def _summarize_plan_for_stage_3(plan: ReviewPlan) -> str:
    """
    Build a compact summary of the Stage 0 plan for Stage 3 (executive report).

    Stage 3 only needs high-level context (scope, file count, key focus areas)
    — not the full file-by-file breakdown that was used in Stage 1 batching.
    """
    lines = []

    # Overall scope
    total_files = sum(len(g.files) for g in plan.file_groups)
    lines.append(f"Total files planned for review: {total_files}")

    # Priority breakdown
    priority_counts: Dict[str, int] = {}
    for group in plan.file_groups:
        p = group.priority.upper()
        priority_counts[p] = priority_counts.get(p, 0) + len(group.files)
    if priority_counts:
        lines.append("By priority: " + ", ".join(
            f"{k}: {v} files" for k, v in sorted(priority_counts.items())
        ))

    # Cross-file concerns (compact)
    if plan.cross_file_concerns:
        lines.append(f"\nCross-file concerns ({len(plan.cross_file_concerns)}):")
        for concern in plan.cross_file_concerns[:5]:
            lines.append(f"  - {concern[:150]}")

    # File list (paths only, no focus areas)
    all_paths = [f.path for g in plan.file_groups for f in g.files]
    if all_paths:
        lines.append(f"\nFiles reviewed: {', '.join(all_paths[:20])}")
        if len(all_paths) > 20:
            lines.append(f"  ... and {len(all_paths) - 20} more")

    return "\n".join(lines)


async def execute_stage_2_cross_file(
    llm,
    request: ReviewRequestDto,
    stage_1_issues: List[CodeReviewIssue],
    plan: ReviewPlan,
    processed_diff: Optional[ProcessedDiff] = None,
) -> CrossFileAnalysisResult:
    """
    Stage 2: Cross-file analysis.

    Uses enrichment data (relationships, class hierarchy) and diff-detected
    migrations to provide the LLM with real architecture context instead of
    placeholders.
    """
    # Slim Stage 1 findings (strip fix diffs, code snippets — Stage 2 only
    # needs location + severity + reason for cross-file pattern detection)
    issues_json = _slim_issues_for_stage_2(stage_1_issues)

    # Build architecture reference from enrichment data (zero-cost)
    architecture_context = _build_architecture_context(
        enrichment=request.enrichmentData,
        changed_files=request.changedFiles,
    )

    # List migration file paths (no raw SQL — Stage 1 already reviewed them)
    migrations = _detect_migration_paths(processed_diff)

    prompt = PromptBuilder.build_stage_2_cross_file_prompt(
        repo_slug=request.projectVcsRepoSlug,
        pr_title=request.prTitle or "",
        commit_hash=request.commitHash or "HEAD",
        stage_1_findings_json=issues_json,
        architecture_context=architecture_context,
        migrations=migrations,
        cross_file_concerns=plan.cross_file_concerns
    )

    # Stage 2 uses direct LLM call (no tools needed - all data is provided from Stage 1)
    try:
        structured_llm = llm.with_structured_output(CrossFileAnalysisResult)
        result = await structured_llm.ainvoke(prompt)
        if result:
            logger.info("Stage 2 cross-file analysis completed with structured output")
            return result
    except Exception as e:
        logger.warning(f"Structured output failed for Stage 2: {e}")
        
    # Fallback to manual parsing
    try:
        response = await llm.ainvoke(prompt)
        content = extract_llm_response_text(response)
        return await parse_llm_response(content, CrossFileAnalysisResult, llm)
    except Exception as e:
        logger.error(f"Stage 2 cross-file analysis failed: {e}")
        raise


async def execute_stage_3_aggregation(
    llm,
    request: ReviewRequestDto,
    plan: ReviewPlan,
    stage_1_issues: List[CodeReviewIssue],
    stage_2_results: CrossFileAnalysisResult,
    is_incremental: bool = False,
    processed_diff: Optional[ProcessedDiff] = None
) -> str:
    """
    Stage 3: Generate Markdown report.
    In incremental mode, includes summary of resolved vs new issues.
    """
    # Compact summary — the full issue list is posted as a separate comment
    stage_1_json = _summarize_issues_for_stage_3(stage_1_issues)
    stage_2_json = stage_2_results.model_dump_json(indent=2)
    plan_summary = _summarize_plan_for_stage_3(plan)
    
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

    # Use real diff stats when available, fall back to 0
    additions = processed_diff.total_additions if processed_diff else 0
    deletions = processed_diff.total_deletions if processed_diff else 0

    prompt = PromptBuilder.build_stage_3_aggregation_prompt(
        repo_slug=request.projectVcsRepoSlug,
        pr_id=str(request.pullRequestId),
        author="Unknown",
        pr_title=request.prTitle or "",
        total_files=len(request.changedFiles or []),
        additions=additions,
        deletions=deletions,
        stage_0_plan=plan_summary,
        stage_1_issues_json=stage_1_json,
        stage_2_findings_json=stage_2_json,
        recommendation=stage_2_results.pr_recommendation,
        incremental_context=incremental_context
    )

    response = await llm.ainvoke(prompt)
    return extract_llm_response_text(response)


# Helper functions for event emission
def _emit_status(callback: Optional[Callable[[Dict], None]], state: str, message: str):
    if callback:
        callback({
            "type": "status",
            "state": state,
            "message": message
        })


def _emit_progress(callback: Optional[Callable[[Dict], None]], percent: int, message: str):
    if callback:
        callback({
            "type": "progress",
            "percent": percent,
            "message": message
        })


def _emit_error(callback: Optional[Callable[[Dict], None]], message: str):
    if callback:
        callback({
            "type": "error",
            "message": message
        })
