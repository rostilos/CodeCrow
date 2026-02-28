"""
Stage 1: Parallel file reviews — batching, RAG context, and per-batch LLM calls.
"""
import asyncio
import logging
import re
import os
import time
from typing import Any, Callable, Dict, List, Optional

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.multi_stage import ReviewPlan, FileReviewBatchOutput
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff, DiffProcessor
from utils.dependency_graph import create_smart_batches

from service.review.orchestrator.agents import extract_llm_response_text
from service.review.orchestrator.json_utils import parse_llm_response
from service.review.orchestrator.reconciliation import (
    issue_matches_files,
    format_previous_issues_for_batch,
)
from service.review.orchestrator.context_helpers import (
    extract_diff_snippets,
    format_rag_context,
    format_duplication_context,
)
from service.review.orchestrator.stage_helpers import (
    emit_progress,
    format_project_rules,
    filter_rag_chunks_for_batch,
)

logger = logging.getLogger(__name__)


# ── Batching ──────────────────────────────────────────────────


def chunk_files(file_groups: List[Any], max_files_per_batch: int = 5) -> List[List[Dict[str, Any]]]:
    all_files = []
    for group in file_groups:
        for f in group.files:
            all_files.append({"file": f, "priority": group.priority})
    return [all_files[i:i + max_files_per_batch] for i in range(0, len(all_files), max_files_per_batch)]


def create_smart_batches_wrapper(
    file_groups: List[Any],
    processed_diff: Optional[ProcessedDiff],
    request: ReviewRequestDto,
    rag_client,
    max_files_per_batch: int = 7,
) -> List[List[Dict[str, Any]]]:
    branches = []
    if request.targetBranchName:
        branches.append(request.targetBranchName)
    if not branches:
        branches = ['main', 'master']

    enrichment_data = getattr(request, 'enrichmentData', None)

    try:
        batches = create_smart_batches(
            file_groups=file_groups,
            workspace=request.projectWorkspace,
            project=request.projectNamespace,
            branches=branches,
            rag_client=rag_client,
            max_batch_size=max_files_per_batch,
            enrichment_data=enrichment_data,
        )
        total_files = sum(len(b) for b in batches)
        related_files = sum(1 for b in batches for f in b if f.get('has_relationships'))
        enrichment_source = "enrichment data" if enrichment_data else "RAG discovery"
        logger.info(
            f"Smart batching ({enrichment_source}): {total_files} files in "
            f"{len(batches)} batches, {related_files} files have cross-file relationships"
        )
        return batches
    except Exception as e:
        logger.warning(f"Smart batching failed, falling back to simple batching: {e}")
        return chunk_files(file_groups, max_files_per_batch)


# ── RAG Context ───────────────────────────────────────────────


async def fetch_batch_rag_context(
    rag_client,
    request: ReviewRequestDto,
    batch_file_paths: List[str],
    batch_diff_snippets: List[str],
    pr_indexed: bool = False,
    llm_reranker=None,
) -> Optional[Dict[str, Any]]:
    if not rag_client:
        return None

    try:
        rag_branch = request.targetBranchName or request.commitHash or "main"
        base_branch = "main"

        logger.info(f"Fetching per-batch RAG context for {len(batch_file_paths)} files")

        pr_number = request.pullRequestId if pr_indexed else None
        all_pr_files = request.changedFiles if pr_indexed else None

        # 1. Semantic search
        rag_response = await rag_client.get_pr_context(
            workspace=request.projectWorkspace,
            project=request.projectNamespace,
            branch=rag_branch,
            changed_files=batch_file_paths,
            diff_snippets=batch_diff_snippets,
            pr_title=request.prTitle,
            pr_description=request.prDescription,
            top_k=10,
            pr_number=pr_number,
            all_pr_changed_files=all_pr_files,
            deleted_files=request.deletedFiles or None,
        )

        context = None
        if rag_response and rag_response.get("context"):
            context = rag_response.get("context")
            chunk_count = len(context.get("relevant_code", []))
            logger.info(f"Semantic RAG: retrieved {chunk_count} chunks for batch")

        # 2. Deterministic lookup
        try:
            deterministic_response = await rag_client.get_deterministic_context(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                branches=[rag_branch, base_branch],
                file_paths=batch_file_paths,
                limit_per_file=5,
                pr_number=pr_number,
                pr_changed_files=all_pr_files,
            )

            if deterministic_response and deterministic_response.get("context"):
                det_context = deterministic_response.get("context")
                related_defs = det_context.get("related_definitions", {})

                if related_defs:
                    if context is None:
                        context = {"relevant_code": []}

                    for def_name, def_chunks in related_defs.items():
                        for chunk in def_chunks[:3]:
                            context["relevant_code"].append({
                                "file_path": chunk.get("file_path", ""),
                                "content": chunk.get("content", ""),
                                "score": 0.85,
                                "source": "deterministic",
                                "definition_name": def_name,
                            })

                    logger.info(f"Deterministic RAG: added {len(related_defs)} related definitions")
        except Exception as det_err:
            logger.debug(f"Deterministic RAG lookup skipped: {det_err}")

        # 3. Duplication search
        try:
            duplication_queries = _build_duplication_queries_from_diff(batch_diff_snippets, batch_file_paths)

            if duplication_queries:
                dup_results = await rag_client.search_for_duplicates(
                    workspace=request.projectWorkspace,
                    project=request.projectNamespace,
                    branch=rag_branch,
                    queries=duplication_queries,
                    top_k=8,
                    base_branch=base_branch,
                )

                if dup_results:
                    if context is None:
                        context = {"relevant_code": []}

                    dup_added = 0
                    seen_paths = {
                        existing.get("file_path", existing.get("path", ""))
                        for existing in context.get("relevant_code", [])
                        if existing.get("file_path", existing.get("path", ""))
                    }

                    for dup in dup_results:
                        dup_path = dup.get("metadata", {}).get("path", "")
                        dup_text = dup.get("text", "")

                        if dup_path in batch_file_paths or not dup_text:
                            continue
                        if dup_path in seen_paths:
                            continue
                        seen_paths.add(dup_path)

                        context["relevant_code"].append({
                            "file_path": dup_path,
                            "text": dup_text,
                            "content": dup_text,
                            "score": max(dup.get("score", 0.8), 0.80),
                            "_source": "duplication",
                            "metadata": dup.get("metadata", {}),
                            "_query": dup.get("_query", ""),
                        })
                        dup_added += 1
                        if dup_added >= 5:
                            break

                    if dup_added > 0:
                        logger.info(f"Duplication search: added {dup_added} similar implementation chunks")
        except Exception as dup_err:
            logger.debug(f"Duplication search skipped: {dup_err}")

        if context:
            total_chunks = len(context.get("relevant_code", []))

            if pr_indexed and all_pr_files:
                context["relevant_code"] = _deduplicate_pr_stale_chunks(
                    context.get("relevant_code", []),
                    pr_changed_files=all_pr_files,
                    batch_file_paths=batch_file_paths,
                )
                deduped_count = total_chunks - len(context.get("relevant_code", []))
                if deduped_count > 0:
                    logger.info(f"Post-merge dedup: removed {deduped_count} stale branch chunks")
                total_chunks = len(context.get("relevant_code", []))

            logger.info(f"Total RAG context: {total_chunks} chunks for files {batch_file_paths}")

            if llm_reranker and total_chunks > 0:
                try:
                    chunks = context.get("relevant_code", [])
                    reranked, rerank_result = await llm_reranker.rerank(
                        chunks,
                        pr_title=request.prTitle,
                        pr_description=request.prDescription,
                        changed_files=request.changedFiles,
                    )
                    context["relevant_code"] = reranked
                    logger.info(
                        f"Per-batch reranking: {rerank_result.method} "
                        f"({rerank_result.processing_time_ms:.0f}ms, "
                        f"{rerank_result.original_count}→{rerank_result.reranked_count} chunks)"
                    )
                except Exception as rerank_err:
                    logger.warning(f"Per-batch reranking failed (non-critical): {rerank_err}")

            return context

        return None

    except Exception as e:
        logger.warning(f"Failed to fetch per-batch RAG context: {e}")
        return None


def _deduplicate_pr_stale_chunks(
    chunks: List[Dict[str, Any]],
    pr_changed_files: List[str],
    batch_file_paths: List[str],
) -> List[Dict[str, Any]]:
    if not chunks or not pr_changed_files:
        return chunks

    pr_changed_set = set()
    for f in pr_changed_files:
        normalized = f.lstrip("/")
        pr_changed_set.add(normalized)
        if "/" in normalized:
            pr_changed_set.add(normalized.rsplit("/", 1)[-1])

    batch_set = set()
    for f in batch_file_paths:
        normalized = f.lstrip("/")
        batch_set.add(normalized)
        if "/" in normalized:
            batch_set.add(normalized.rsplit("/", 1)[-1])

    by_path: Dict[str, List[Dict[str, Any]]] = {}
    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        path = (metadata.get("path") or chunk.get("path") or
                chunk.get("file_path", "")).lstrip("/")
        if not path:
            path = "__unknown__"
        by_path.setdefault(path, []).append(chunk)

    result = []
    for path, path_chunks in by_path.items():
        path_basename = path.rsplit("/", 1)[-1] if "/" in path else path

        is_pr_file = (path in pr_changed_set or path_basename in pr_changed_set)
        is_batch_file = (path in batch_set or path_basename in batch_set)

        if not is_pr_file or is_batch_file:
            result.extend(path_chunks)
            continue

        pr_chunks = [c for c in path_chunks if c.get("_source") == "pr_indexed"]
        non_pr_chunks = [c for c in path_chunks if c.get("_source") != "pr_indexed"]

        if pr_chunks and non_pr_chunks:
            result.extend(pr_chunks)
            logger.info(
                f"Dedup: replaced {len(non_pr_chunks)} stale branch chunk(s) "
                f"with {len(pr_chunks)} PR-indexed chunk(s) for {path}"
            )
        elif pr_chunks:
            result.extend(pr_chunks)
        else:
            for c in non_pr_chunks:
                c["_potentially_stale"] = True
            result.extend(non_pr_chunks)

    return result


def _build_duplication_queries_from_diff(
    diff_snippets: List[str],
    file_paths: List[str],
) -> List[str]:
    queries = []
    all_diff_text = "\n".join(diff_snippets or [])

    if not all_diff_text and not file_paths:
        return []

    func_sigs = re.findall(
        r'(?:public|private|protected|static)?\s*function\s+(\w+)\s*\([^)]*\)',
        all_diff_text,
    )
    for sig in set(func_sigs):
        if sig.lower() not in ('__construct', '__destruct', 'execute', 'run',
                                'get', 'set', 'init', 'setup') and len(sig) > 4:
            queries.append(f"existing implementation of {sig}")

    plugin_methods = re.findall(r'function\s+(before|after|around)(\w+)\s*\(', all_diff_text)
    for prefix, target in plugin_methods:
        queries.append(f"plugin {prefix} {target} existing implementation")

    event_names = re.findall(r'["\'](\w+(?:_\w+){2,})["\']', all_diff_text)
    for event in set(event_names):
        if any(kw in event for kw in ('save', 'load', 'delete', 'submit', 'order',
                                       'checkout', 'customer', 'payment', 'catalog')):
            queries.append(f"observer event {event} handler")

    table_ops = re.findall(
        r'(?:DELETE\s+FROM|SELECT\s+.*?FROM|UPDATE)\s+[`"\']?(\w+)',
        all_diff_text, re.IGNORECASE,
    )
    for table in set(table_ops):
        if len(table) > 3:
            queries.append(f"cron cleanup {table} database operation")

    for fp in file_paths:
        basename = os.path.basename(fp) if fp else ""
        if basename in ('di.xml', 'events.xml', 'crontab.xml', 'widget.xml'):
            queries.append(f"{basename} plugin observer cron configuration")

    seen = set()
    unique = []
    for q in queries:
        if q not in seen and len(q) > 10:
            seen.add(q)
            unique.append(q)

    return unique[:8]


# ── Batch Review ──────────────────────────────────────────────


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
    pr_indexed: bool = False,
    llm_reranker=None,
) -> List[CodeReviewIssue]:
    batches = create_smart_batches_wrapper(
        plan.file_groups, processed_diff, request, rag_client, max_files_per_batch=7,
    )

    total_files = sum(len(batch) for batch in batches)
    related_batches = sum(1 for b in batches if any(f.get('has_relationships') for f in b))
    logger.info(
        f"Stage 1: Processing {total_files} files in {len(batches)} batches "
        f"({related_batches} batches with cross-file relationships)"
    )

    all_issues: List[CodeReviewIssue] = []

    for wave_start in range(0, len(batches), max_parallel):
        wave_end = min(wave_start + max_parallel, len(batches))
        wave_batches = batches[wave_start:wave_end]
        wave_num = wave_start // max_parallel + 1

        logger.info(
            f"Stage 1: Processing wave {wave_num}, "
            f"batches {wave_start + 1}-{wave_end} of {len(batches)} IN PARALLEL"
        )

        wave_start_time = time.time()

        tasks = []
        for batch_idx, batch in enumerate(wave_batches, start=wave_start + 1):
            batch_paths = [item["file"].path for item in batch]
            has_rels = any(item.get('has_relationships') for item in batch)
            logger.debug(f"Batch {batch_idx}: {batch_paths} (cross-file relationships: {has_rels})")
            tasks.append(_review_batch_with_timing(
                batch_idx, llm, request, batch, rag_client, processed_diff,
                is_incremental, rag_context, pr_indexed,
                llm_reranker=llm_reranker,
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        wave_elapsed = time.time() - wave_start_time
        logger.info(f"Wave {wave_num} completed in {wave_elapsed:.2f}s ({len(wave_batches)} batches parallel)")

        for idx, res in enumerate(results):
            batch_num = wave_start + idx + 1
            if isinstance(res, Exception):
                logger.error(f"Error reviewing batch {batch_num}: {res}")
            elif res:
                logger.info(f"Batch {batch_num} completed: {len(res)} issues found")
                all_issues.extend(res)
            else:
                logger.info(f"Batch {batch_num} completed: no issues found")

        progress = 10 + int((wave_end / len(batches)) * 50)
        emit_progress(event_callback, progress, f"Stage 1: Reviewed {wave_end}/{len(batches)} batches")

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
    pr_indexed: bool,
    llm_reranker=None,
) -> List[CodeReviewIssue]:
    start_time = time.time()
    batch_paths = [item["file"].path for item in batch]
    logger.info(f"[Batch {batch_idx}] STARTED - files: {batch_paths}")

    try:
        result = await review_file_batch(
            llm, request, batch, rag_client, processed_diff, is_incremental,
            fallback_rag_context=fallback_rag_context, pr_indexed=pr_indexed,
            llm_reranker=llm_reranker,
        )
        elapsed = time.time() - start_time
        logger.info(f"[Batch {batch_idx}] FINISHED in {elapsed:.2f}s - {len(result)} issues")
        return result
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[Batch {batch_idx}] FAILED after {elapsed:.2f}s: {e}")
        raise


async def review_file_batch(
    llm,
    request: ReviewRequestDto,
    batch_items: List[Dict[str, Any]],
    rag_client,
    processed_diff: Optional[ProcessedDiff] = None,
    is_incremental: bool = False,
    fallback_rag_context: Optional[Dict[str, Any]] = None,
    pr_indexed: bool = False,
    llm_reranker=None,
) -> List[CodeReviewIssue]:
    batch_files_data = []
    batch_file_paths = []
    batch_diff_snippets = []

    diff_source = None
    if is_incremental and request.deltaDiff:
        diff_source = DiffProcessor().process(request.deltaDiff) if request.deltaDiff else None
    else:
        diff_source = processed_diff

    for item in batch_items:
        file_info = item["file"]
        batch_file_paths.append(file_info.path)

        file_diff = ""
        if diff_source:
            for f in diff_source.files:
                if f.path == file_info.path or f.path.endswith("/" + file_info.path):
                    file_diff = f.content
                    if file_diff:
                        batch_diff_snippets.extend(extract_diff_snippets(file_diff))
                    break

        batch_files_data.append({
            "path": file_info.path,
            "type": "MODIFIED",
            "focus_areas": file_info.focus_areas,
            "old_code": "",
            "diff": file_diff or "(Diff unavailable)",
            "is_incremental": is_incremental,
        })

    project_rules = format_project_rules(request.projectRules, batch_file_paths)

    rag_context_text = ""
    batch_rag_context = None

    if rag_client:
        batch_rag_context = await fetch_batch_rag_context(
            rag_client, request, batch_file_paths, batch_diff_snippets, pr_indexed,
            llm_reranker=llm_reranker,
        )

    if batch_rag_context:
        logger.info(f"Using per-batch RAG context for: {batch_file_paths}")
        rag_context_text = format_rag_context(
            batch_rag_context,
            set(batch_file_paths),
            pr_changed_files=request.changedFiles,
            deleted_files=request.deletedFiles,
        )
    elif fallback_rag_context:
        filtered_fallback = filter_rag_chunks_for_batch(fallback_rag_context, batch_file_paths)
        logger.info(
            f"Using filtered fallback RAG context for batch: {batch_file_paths} "
            f"({len((filtered_fallback or {}).get('relevant_code', []))} chunks)"
        )
        rag_context_text = format_rag_context(
            filtered_fallback,
            set(batch_file_paths),
            pr_changed_files=request.changedFiles,
            deleted_files=request.deletedFiles,
        )

    logger.info(f"RAG context for batch: {len(rag_context_text)} chars")

    previous_issues_for_batch = ""
    has_previous_issues = request.previousCodeAnalysisIssues and len(request.previousCodeAnalysisIssues) > 0
    if has_previous_issues:
        relevant_prev_issues = [
            issue for issue in request.previousCodeAnalysisIssues
            if issue_matches_files(issue, batch_file_paths)
        ]
        if relevant_prev_issues:
            previous_issues_for_batch = format_previous_issues_for_batch(relevant_prev_issues)

    file_outlines_text = ""
    if request.enrichmentData and request.enrichmentData.fileMetadata:
        outlines = []
        for meta in request.enrichmentData.fileMetadata:
            if meta.path in batch_file_paths or any(meta.path.endswith(bp) for bp in batch_file_paths):
                outline = f"File: {meta.path}\n"
                if meta.imports:
                    outline += f"Imports: {', '.join(meta.imports[:20])}\n"
                if meta.semanticNames:
                    outline += f"Symbols/Methods: {', '.join(meta.semanticNames[:30])}\n"
                outlines.append(outline)
        if outlines:
            file_outlines_text = "AST Outlines for this batch:\n" + "\n".join(outlines)

    prompt = PromptBuilder.build_stage_1_batch_prompt(
        files=batch_files_data,
        priority=batch_items[0]["priority"] if batch_items else "MEDIUM",
        project_rules=project_rules,
        file_outlines=file_outlines_text,
        rag_context=rag_context_text,
        is_incremental=is_incremental,
        previous_issues=previous_issues_for_batch,
        all_pr_files=request.changedFiles,
        deleted_files=request.deletedFiles,
    )

    try:
        structured_llm = llm.with_structured_output(FileReviewBatchOutput)
        result = await structured_llm.ainvoke(prompt)
        if result:
            return _extract_calibrated_issues(result)
    except Exception as e:
        logger.warning(f"Structured output failed for Stage 1 batch: {e}")

        try:
            response = await llm.ainvoke(prompt)
            content = extract_llm_response_text(response)
            data = await parse_llm_response(content, FileReviewBatchOutput, llm)
            return _extract_calibrated_issues(data)
        except Exception as parse_err:
            logger.error(
                f"Batch review double parse failure for {batch_file_paths}: {parse_err}. "
                "Zero issues will be reported for this batch."
            )
            return []


def _extract_calibrated_issues(batch_output: FileReviewBatchOutput) -> List[CodeReviewIssue]:
    all_batch_issues: List[CodeReviewIssue] = []
    for review in batch_output.reviews:
        review_confidence = (review.confidence or "MEDIUM").upper()
        for issue in review.issues:
            if review_confidence == "LOW" and issue.severity.upper() == "HIGH":
                logger.info(
                    f"Downgrading issue in {review.file} from HIGH to MEDIUM "
                    f"(batch confidence: LOW): {issue.reason[:80]}"
                )
                issue.severity = "MEDIUM"
        all_batch_issues.extend(review.issues)
    return all_batch_issues
