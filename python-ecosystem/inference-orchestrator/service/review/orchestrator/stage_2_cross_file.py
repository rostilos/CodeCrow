"""
Stage 2: Cross-file & architectural analysis — duplication, conflicts, data flow.
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.enrichment import PrEnrichmentDataDto
from model.multi_stage import ReviewPlan, CrossFileAnalysisResult
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff
from utils.signature_patterns import (
    extract_function_names,
    extract_class_names,
    extract_import_modules,
    extract_decorators,
    CONFIG_EXTENSIONS,
)

from service.review.orchestrator.agents import extract_llm_response_text
from service.review.orchestrator.json_utils import parse_llm_response
from service.review.orchestrator.context_helpers import format_duplication_context
from service.review.orchestrator.stage_helpers import format_project_rules_digest

logger = logging.getLogger(__name__)

_MIGRATION_PATH_MARKERS = (
    '/db/migrate/', '/migrations/', '/migration/',
    '/flyway/', '/liquibase/', '/alembic/', '/changeset/',
)

_STAGE_2_STRIP_FIELDS = {
    'suggestedFixDiff', 'suggestedFixDescription', 'codeSnippet',
    'resolutionExplanation', 'resolvedInCommit', 'visibility',
}


async def execute_stage_2_cross_file(
    llm,
    request: ReviewRequestDto,
    stage_1_issues: List[CodeReviewIssue],
    plan: ReviewPlan,
    processed_diff: Optional[ProcessedDiff] = None,
    rag_client=None,
) -> CrossFileAnalysisResult:
    issues_json = _slim_issues_for_stage_2(stage_1_issues)
    architecture_context = _build_architecture_context(
        enrichment=request.enrichmentData,
        changed_files=request.changedFiles,
    )
    migrations = _detect_migration_paths(processed_diff)
    cross_module_context = await _fetch_cross_module_context(
        rag_client=rag_client,
        request=request,
        processed_diff=processed_diff,
    )

    prompt = PromptBuilder.build_stage_2_cross_file_prompt(
        repo_slug=request.projectVcsRepoSlug,
        pr_title=request.prTitle or "",
        commit_hash=request.commitHash or "HEAD",
        stage_1_findings_json=issues_json,
        architecture_context=architecture_context,
        migrations=migrations,
        cross_file_concerns=plan.cross_file_concerns,
        cross_module_context=cross_module_context,
        project_rules=format_project_rules_digest(request.projectRules),
    )

    try:
        structured_llm = llm.with_structured_output(CrossFileAnalysisResult)
        result = await structured_llm.ainvoke(prompt)
        if result:
            logger.info("Stage 2 cross-file analysis completed with structured output")
            return result
    except Exception as e:
        logger.warning(f"Structured output failed for Stage 2: {e}")

    try:
        response = await llm.ainvoke(prompt)
        content = extract_llm_response_text(response)
        return await parse_llm_response(content, CrossFileAnalysisResult, llm)
    except Exception as e:
        logger.error(f"Stage 2 cross-file analysis failed: {e}")
        raise


# ── Helpers ───────────────────────────────────────────────────


def _build_architecture_context(
    enrichment: Optional[PrEnrichmentDataDto],
    changed_files: Optional[List[str]],
) -> str:
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


def _detect_migration_paths(processed_diff: Optional[ProcessedDiff]) -> str:
    if not processed_diff:
        return "No migration scripts detected."

    migration_files: List[str] = []
    for f in processed_diff.files:
        path_lower = f.path.lower()
        if path_lower.endswith('.sql') or any(m in path_lower for m in _MIGRATION_PATH_MARKERS):
            migration_files.append(f.path)

    if not migration_files:
        return "No migration scripts detected in this PR."

    listing = "\n".join(f"- {p}" for p in migration_files[:15])
    return f"Migration files in this PR ({len(migration_files)}):\n{listing}"


def _slim_issues_for_stage_2(issues: List[CodeReviewIssue]) -> str:
    slim = []
    for issue in issues:
        d = issue.model_dump()
        for key in _STAGE_2_STRIP_FIELDS:
            d.pop(key, None)
        slim.append(d)
    return json.dumps(slim, indent=2)


async def _fetch_cross_module_context(
    rag_client,
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
) -> str:
    if not rag_client:
        return ""

    try:
        rag_branch = request.targetBranchName or request.commitHash or "main"
        changed_files = request.changedFiles or []

        queries = []

        all_diff_text = ""
        if processed_diff:
            for f in processed_diff.files:
                all_diff_text += f.content + "\n"

        # ── Language-agnostic queries ──────────────────────────────
        # 1. Class/interface/trait definitions
        for cls_name in extract_class_names(all_diff_text, min_length=2):
            queries.append(f"usage of {cls_name} implementation reference")

        # 2. Function/method signatures
        for func_name in extract_function_names(all_diff_text, min_length=3):
            queries.append(f"existing implementation of {func_name}")

        # 3. Import/require/use statements
        for short in extract_import_modules(all_diff_text):
            queries.append(f"module {short} interface contract")

        # 4. Decorator/annotation patterns
        for dec in extract_decorators(all_diff_text):
            queries.append(f"annotation decorator {dec} handler")

        # ── Config file queries (any config format) ──
        for fp in changed_files:
            basename = os.path.basename(fp)
            if any(basename.endswith(ext) for ext in CONFIG_EXTENSIONS):
                queries.append(f"{basename} configuration definition")

        if request.prTitle:
            queries.append(f"existing implementation: {request.prTitle}")

        if not queries:
            return ""

        seen = set()
        unique_queries = []
        for q in queries:
            if q not in seen and len(q) > 10:
                seen.add(q)
                unique_queries.append(q)
        unique_queries = unique_queries[:10]

        logger.info(f"Stage 2 cross-module RAG: {len(unique_queries)} queries")

        dup_results = await rag_client.search_for_duplicates(
            workspace=request.projectWorkspace,
            project=request.projectNamespace,
            branch=rag_branch,
            queries=unique_queries,
            top_k=6,
        )

        if not dup_results:
            return ""

        changed_set = set(changed_files)
        formatted = format_duplication_context(
            duplication_results=dup_results,
            batch_file_paths=list(changed_set),
            max_chunks=10,
        )

        if formatted:
            logger.info(f"Stage 2 cross-module context: {len(formatted)} chars")

        return formatted

    except Exception as e:
        logger.warning(f"Failed to fetch cross-module context for Stage 2: {e}")
        return ""
