"""Plan complete changes, discover defects, and verify cross-file evidence."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from llm.llm_factory import LLMFactory
from llm.request_capture import review_capture
from model.dtos import ReviewRequestDto
from service.rag.rag_client import RagClient
from service.runtime_capacity import review_concurrency, review_call_concurrency
from service.review.planner import ReviewPlanner
from service.review.async_work import gather_review_work
from service.review.execution_scheduler import (
    FairReviewScheduler, review_execution, review_context_slot,
)
from service.review.change_context import resolve_changed_anchor
from service.review.review_stages import review_batch, review_cross_batch
from service.review.snapshot_identity import (
    resolve_exact_structural_base_revision, validate_review_snapshot_identity,
)
from service.review.verification_tools import LocalReviewSource
from service.review.verifier import ReviewVerifier
from service.review.execution_mode import review_execution_mode
from service.review.mcp_review import McpReview
from utils.diff_processor import HunkDisposition, process_raw_diff


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewPart:
    id: str
    path: str
    diff: str
    anchors: Mapping[int, str]
    side: str


def _parts(raw_diff: str) -> tuple[list[ReviewPart], list[str]]:
    processed = process_raw_diff(raw_diff)
    parts: list[ReviewPart] = []
    unparsed: list[str] = []
    if raw_diff.strip() and not processed.files:
        return [], ["<diff>"]
    for file in processed.files:
        if file.is_binary or file.is_gitlink:
            unparsed.append(file.path)
            continue
        if not file.hunks and (file.additions or file.deletions):
            unparsed.append(file.path)
        for hunk in file.hunks:
            if hunk.disposition not in {
                HunkDisposition.REVIEWABLE, HunkDisposition.DELETED,
            }:
                unparsed.append(hunk.path)
                continue
            added: dict[int, str] = {}
            removed: dict[int, str] = {}
            old_line, new_line = hunk.old_start, hunk.new_start
            for line in hunk.content.splitlines()[1:]:
                if line.startswith("+"):
                    added[new_line] = line[1:]
                    new_line += 1
                elif line.startswith("-"):
                    removed[old_line] = line[1:]
                    old_line += 1
                elif line.startswith(" "):
                    old_line += 1
                    new_line += 1
            if added or removed:
                parts.append(ReviewPart(
                    id=hunk.id, path=hunk.path, diff=hunk.content,
                    anchors=added or removed,
                    side="proposed" if added else "target",
                ))
    return parts, list(dict.fromkeys(unparsed))


def _visible_proposed_lines(part: ReviewPart) -> set[int]:
    """Lines already present verbatim in a hunk do not need duplicate context."""
    match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", part.diff)
    if not match:
        return set()
    line = int(match.group(1))
    visible: set[int] = set()
    for text in part.diff.splitlines()[1:]:
        if text.startswith(("+", " ")):
            visible.add(line)
            line += 1
    return visible


class ReviewService:
    def __init__(self, rag_client: RagClient | None = None):
        self.rag_client = rag_client or RagClient()
        self._model_scheduler = FairReviewScheduler(review_call_concurrency())
        self._context_semaphore = asyncio.Semaphore(review_concurrency())
        self._preparation_semaphore = asyncio.Semaphore(review_concurrency())

    async def process_review_request(
        self, request: ReviewRequestDto,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        with review_execution(
            self._model_scheduler, self._context_semaphore, event_callback=event_callback,
            preparation_semaphore=self._preparation_semaphore,
            project_id=request.projectId, pull_request_id=request.pullRequestId,
        ):
            try:
                with review_capture(request):
                    return {"result": await self._review(request, event_callback)}
            except Exception as error:
                logger.exception("Review incomplete for project=%s PR=%s", request.projectId, request.pullRequestId)
                return {"result": {"status": "error", "comment": f"Review incomplete: {error}", "issues": []}}

    async def _prepare_context(self, request: ReviewRequestDto, callback: Any) -> tuple[dict[str, Any], list[str]]:
        diagnostics: list[str] = []
        base_revision = resolve_exact_structural_base_revision(request)
        try:
            identity = validate_review_snapshot_identity(request)
            target_branch, head_revision = identity.target_branch, identity.head_revision
        except Exception as error:
            diagnostics.append(f"Graph snapshot identity unavailable: {error}")
            target_branch, head_revision = "", ""
        binding: dict[str, Any] = {
            "workspace": request.projectWorkspace, "project": request.projectNamespace,
            "target_branch": target_branch, "base_revision": base_revision or "",
            "source_revision": head_revision,
            # Do not expose an unbound target snapshot as current source.
            "target_repo_path": (request.localRagRepoPath or request.localRepoPath) if base_revision else None,
            "review_overlay_path": request.localReviewOverlayPath,
        }
        if request.ragCollectionTarget and request.ragBaseGenerationManifestSha256:
            binding["base_collection_target"] = request.ragCollectionTarget
            binding["base_generation_manifest_sha256"] = request.ragBaseGenerationManifestSha256
            if request.ragBaseGenerationRevision:
                binding["base_generation_revision"] = request.ragBaseGenerationRevision
        if not base_revision:
            diagnostics.append("Target snapshot binding unavailable; only staged proposed files can supply local source.")
        if not (self.rag_client.enabled and target_branch and head_revision and base_revision
                and binding["target_repo_path"] and binding["review_overlay_path"]):
            diagnostics.append("Proposed-tree graph unavailable; continuing with changed-code and available local source.")
            return binding, diagnostics
        preparation = dict(binding)
        if request.ragIndexPolicy is not None:
            preparation["index_policy"] = request.ragIndexPolicy.model_dump()
        if request.ragGenerationCandidates:
            preparation["base_generation_candidates"] = [
                candidate.model_dump() for candidate in request.ragGenerationCandidates
            ]
        self._emit(callback, "graph_preparing", "Preparing proposed-tree graph")
        try:
            async with review_context_slot("graph_preparation", preparation=True):
                prepared = await self.rag_client.prepare_review_generation(**preparation)
            if (prepared.get("status") != "ready" or prepared.get("source_revision") != head_revision
                    or not prepared.get("collection_target") or not prepared.get("generation_manifest_sha256")):
                raise ValueError(str(prepared.get("error") or "graph snapshot receipt is unavailable"))
            if ("base_collection_target" in prepared
                    and "base_generation_manifest_sha256" in prepared):
                # Preparation may use a full source build when the supplied
                # base receipt is stale. Subsequent queries must bind the actual
                # sealed generation, including its explicit lack of a base.
                binding["base_collection_target"] = prepared["base_collection_target"]
                binding["base_generation_manifest_sha256"] = prepared["base_generation_manifest_sha256"]
            if "base_generation_revision" in prepared:
                binding["base_generation_revision"] = prepared["base_generation_revision"]
            if isinstance(prepared.get("index_policy"), dict):
                for field in ("include_patterns", "exclude_patterns", "project_type", "source_root"):
                    binding[field] = prepared["index_policy"].get(field)
            binding["review_collection_target"] = prepared["collection_target"]
            binding["review_generation_manifest_sha256"] = prepared["generation_manifest_sha256"]
            self._emit(callback, "graph_ready", "Proposed-tree graph is ready")
        except Exception as error:
            diagnostics.append(f"Proposed-tree graph unavailable; continuing with local source: {error}")
        return binding, diagnostics

    async def _review(self, request: ReviewRequestDto, callback: Any) -> dict[str, Any]:
        execution_mode, mode_diagnostics = review_execution_mode(request)
        raw_diff = (request.deltaDiff if str(request.analysisMode or "").upper() == "INCREMENTAL"
                    and request.deltaDiff else request.rawDiff)
        if not raw_diff:
            raise ValueError("changed-code diff is unavailable")
        parts, unparsed_paths = _parts(raw_diff)
        # Incremental jobs own only delta anchors, but previous changes remain
        # accessible as evidence when a contract spans PR iterations.
        context_parts = _parts(request.rawDiff)[0] if request.rawDiff and request.rawDiff != raw_diff else []
        if not parts and not unparsed_paths:
            return {"status": "complete", "comment": "No changed text to review.", "issues": [],
                    "reviewExecutionMode": execution_mode, "diagnostics": mode_diagnostics}
        if not parts:
            return {"status": "partial", "comment": "Review incomplete: the changed text could not be parsed.",
                    "issues": [], "reviewedHunkIds": [], "reviewExecutionMode": execution_mode,
                    "diagnostics": mode_diagnostics,
                    "unresolvedScopes": {f"path:{path}": "changed text could not be parsed into a hunk" for path in unparsed_paths}}
        binding, diagnostics = await self._prepare_context(request, callback)
        diagnostics.extend(mode_diagnostics)
        self._emit(callback, "review_execution_mode", f"Review execution mode: {execution_mode}")
        llm = LLMFactory.create_llm(request.aiModel, request.aiProvider, request.aiApiKey,
                                    ai_base_url=request.aiBaseUrl, ai_custom_parameters=request.aiCustomParameters)
        if execution_mode == "mcp_only":
            outcome = await McpReview(self.rag_client).review(
                llm=llm, request=request, parts=parts, context_parts=context_parts,
                binding=binding, normalize=self._finding, callback=callback,
            )
            result = self._finish_review(request, callback, parts, unparsed_paths,
                                         outcome.findings, outcome.reviewed, outcome.unresolved,
                                         [*diagnostics, *outcome.diagnostics])
            result["reviewExecutionMode"] = execution_mode
            result["graphAvailable"] = bool(binding.get("review_collection_target"))
            result["executionStatistics"] = outcome.statistics
            return result
        self._emit(callback, "planning", "Planning change ownership and cross-file dependencies")

        async def graph_reader(**query: Any) -> list[dict[str, Any]]:
            return await self._graph_results(binding, **query)

        plan = await ReviewPlanner(graph_reader if binding.get("review_collection_target") else None).plan(parts)
        diagnostics.extend(plan.diagnostics)
        source = LocalReviewSource(binding, [part.path for part in parts])
        source_context: dict[str, list[dict[str, Any]]] = {}

        async def discover(batch: Any) -> Any:
            self._emit(callback, "reviewing", f"Reviewing {', '.join(sorted({part.path for part in batch.parts}))}")
            try:
                async with review_context_slot("discovery_source"):
                    owner_source = await self._owner_source((*batch.parts, *batch.companion_parts), plan.graph_context, source, binding)
            except Exception as error:
                owner_source = [{"status": "unavailable", "diagnostic": f"Owner source unavailable: {error}"}]
                logger.warning("Batch source context unavailable: %s", error)
            source_context[batch.id] = owner_source
            return await review_batch(llm=llm, request=request, batch=batch, plan=plan,
                                      owner_source=owner_source, normalize=self._finding)

        results = await gather_review_work(*(discover(batch) for batch in plan.batches))
        candidates = [issue for result in results for issue in result.findings]
        reviewed = {part_id for result in results for part_id in result.reviewed}
        summaries = [summary for result in results for summary in result.summaries]
        investigations = [question for result in results for question in result.investigations]
        diagnostics.extend(message for result in results for message in result.diagnostics)
        if plan.cross_batch_scopes:
            self._emit(callback, "cross_file", "Checking interactions between batch summaries")
        cross = await review_cross_batch(llm=llm, request=request, plan=plan, summaries=summaries,
                                        candidates=candidates, normalize=self._finding, investigations=investigations)
        candidates.extend(cross.findings)
        investigations.extend(cross.investigations)
        diagnostics.extend(cross.diagnostics)
        unresolved: dict[str, str] = {
            question["id"]: question["question"] for question in investigations
        }
        if candidates or investigations:
            self._emit(callback, "verifying", "Verifying findings and unresolved interactions against source")
            try:
                verification = await ReviewVerifier(rag_client=self.rag_client).verify(
                    llm=llm, request=request, findings=candidates, summaries=summaries,
                    parts=parts, context_parts=context_parts, binding=binding, graph_context=plan.graph_context,
                    cross_batch_scopes=[{"id": scope.id, "batchIds": list(scope.batch_ids),
                                         "relations": list(scope.relations), "reason": scope.reason}
                                        for scope in plan.cross_batch_scopes],
                    investigations=investigations,
                    source_context=[value for values in source_context.values() for value in values], callback=callback,
                )
                candidates = verification.issues
                diagnostics.extend(verification.diagnostics)
                diagnostics.extend(verification.warnings)
                if verification.diagnostics:
                    unresolved["verification"] = "; ".join(verification.diagnostics)
                for question_id in verification.resolved_investigation_ids:
                    unresolved.pop(question_id, None)
                    if question_id.startswith("hunk:"):
                        reviewed.add(question_id[len("hunk:"):])
            except Exception as error:
                message = f"Final verification unavailable; discovery findings retained: {error}"
                diagnostics.append(message)
                unresolved["verification"] = message
        result = self._finish_review(request, callback, parts, unparsed_paths,
                                     candidates, reviewed, unresolved, diagnostics)
        result["reviewExecutionMode"] = execution_mode
        result["graphAvailable"] = bool(binding.get("review_collection_target"))
        return result

    def _finish_review(self, request: Any, callback: Any, parts: Any, unparsed_paths: Any,
                       candidates: Any, reviewed: set[str], unresolved: dict[str, str],
                       diagnostics: list[str]) -> dict[str, Any]:
        for part in parts:
            if part.id not in reviewed:
                unresolved.setdefault(f"hunk:{part.id}", "changed hunk remains unresolved")
        unresolved.update({f"path:{path}": "changed text could not be parsed into a hunk" for path in unparsed_paths})
        # The verifier owns semantic deduplication. Only byte-identical public
        # findings are collapsed here; nearby independent defects must survive.
        public_issues: list[dict[str, Any]] = []
        parts_by_id = {part.id: part for part in parts}
        for candidate in candidates:
            issue = self._finding(candidate, parts_by_id)
            if issue and issue not in public_issues:
                public_issues.append(issue)
        diagnostics = list(dict.fromkeys(diagnostics))
        for diagnostic in diagnostics:
            logger.warning("Review diagnostic project=%s PR=%s: %s", request.projectId, request.pullRequestId, diagnostic)
            self._emit(callback, "review_diagnostic", diagnostic)
        status = "partial" if unresolved else "complete"
        comment = f"Reviewed {len(reviewed)} of {len(parts)} changed-code parts."
        if unresolved:
            comment = f"Partial review: {comment} {len(unresolved)} verification/change scope(s) remain unresolved."
        if diagnostics:
            comment += " Context availability and verification details are recorded in review diagnostics."
        return {"status": status, "comment": comment, "issues": public_issues,
                "reviewedHunkIds": sorted(reviewed), "unresolvedScopes": unresolved, "diagnostics": diagnostics}

    async def _owner_source(self, parts: Any, graph_context: dict[str, Any],
                            source: LocalReviewSource, binding: dict[str, Any]) -> list[dict[str, Any]]:
        units = {unit["unitId"]: unit for part in parts
                 for unit in graph_context.get(part.id, {}).get("units", [])}
        visible: dict[str, set[int]] = {}
        for part in parts:
            visible.setdefault(part.path, set()).update(_visible_proposed_lines(part))
        ranges: dict[str, list[tuple[int, int]]] = {}
        for unit in units.values():
            path = str(unit.get("path") or "")
            try:
                start, end = int(unit["startLine"]), int(unit["endLine"])
            except (KeyError, TypeError, ValueError):
                continue
            if not path or start < 1 or end < start:
                continue
            if all(line in visible.get(path, set()) for line in range(start, end + 1)):
                continue
            ranges.setdefault(path, []).append((start, end))
        windows: list[dict[str, Any]] = []
        for path, spans in sorted(ranges.items()):
            merged: list[tuple[int, int]] = []
            for start, end in sorted(spans):
                if merged and start <= merged[-1][1] + 1:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            for start, end in merged:
                value = await asyncio.to_thread(source.read, path, start_line=start, end_line=end)
                if value.get("status") != "ready" and binding.get("review_collection_target"):
                    try:
                        value = await self.rag_client.get_review_file_content(
                            **binding, focus_paths=[path], path=path, start_line=start, end_line=end,
                        )
                    except Exception as error:
                        value = {"status": "unavailable", "path": path, "diagnostic": str(error)}
                windows.append(value)
        return windows

    async def _graph_results(self, binding: dict[str, Any], *, pattern: str,
                             target: str, focus_path: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        cursor = 0
        while True:
            async with review_context_slot("graph_planning"):
                page = await self.rag_client.query_review_graph(**binding, focus_paths=[focus_path],
                    pattern=pattern, target=target, cursor=cursor, max_results=100, include_source=False)
            if page.get("status") != "ready":
                raise ValueError(str(page.get("error") or f"graph {pattern} query unavailable"))
            values.extend(item for item in page.get("results") or [] if isinstance(item, dict))
            next_cursor = page.get("nextCursor")
            if next_cursor is None:
                return values
            next_cursor = int(next_cursor)
            if next_cursor <= cursor:
                raise ValueError("graph query did not advance its continuation")
            cursor = next_cursor

    @staticmethod
    def _finding(
        value: Any,
        parts_by_id: dict[str, ReviewPart],
    ) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        resolved = resolve_changed_anchor(value, parts_by_id)
        if resolved is None:
            return None
        part, line = resolved
        title = str(value.get("title") or "").strip()
        reason = str(value.get("reason") or "").strip()
        if not title or not reason:
            return None
        return {
            "file": part.path,
            "line": line,
            "codeSnippet": part.anchors[line],
            "severity": str(value.get("severity") or "MEDIUM").upper(),
            "category": str(value.get("category") or "BUG_RISK"),
            "scope": "LINE",
            "title": title,
            "reason": reason,
            "suggestedFixDescription": str(
                value.get("suggestedFixDescription") or ""
            ),
        }

    @staticmethod
    def _emit(
        callback: Callable[[dict[str, Any]], None] | None,
        state: str,
        message: str,
    ) -> None:
        if callback:
            callback({"type": "status", "state": state, "message": message})
