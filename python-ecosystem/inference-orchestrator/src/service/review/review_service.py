"""Plan complete changes, discover defects, and verify cross-file evidence."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from llm.llm_factory import LLMFactory
from llm.request_capture import review_capture
from model.dtos import ReviewRequestDto
from service.rag.rag_client import RagClient
from service.review.planner import ReviewPlanner
from service.review.change_context import resolve_changed_anchor
from service.review.review_stages import review_batch, review_cross_batch
from service.review.snapshot_identity import (
    resolve_exact_structural_base_revision, validate_review_snapshot_identity,
)
from service.review.verification_tools import LocalReviewSource
from service.review.verifier import ReviewVerifier
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


def _visible_source_lines(part: ReviewPart) -> set[int]:
    """Exact lines on the anchor's side already supplied by the complete hunk."""
    match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", part.diff)
    if not match:
        return set()
    line = int(match.group(2 if part.side == "proposed" else 1))
    marker = "+" if part.side == "proposed" else "-"
    visible: set[int] = set()
    for text in part.diff.splitlines()[1:]:
        if text.startswith((marker, " ")):
            visible.add(line)
            line += 1
    return visible


class ReviewService:
    MAX_CONCURRENT_REVIEWS = int(os.environ.get("MAX_CONCURRENT_REVIEWS", "4"))

    def __init__(self, rag_client: RagClient | None = None):
        self.rag_client = rag_client or RagClient()
        self._review_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REVIEWS)
        # Share capacity across requests instead of multiplying concurrency by
        # the number of files in every queued review.
        self._batch_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REVIEWS)

    async def process_review_request(
        self, request: ReviewRequestDto,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        async with self._review_semaphore:
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
        if not base_revision:
            diagnostics.append("Target snapshot binding unavailable; only staged proposed files can supply local source.")
        if not (self.rag_client.enabled and target_branch and head_revision and base_revision
                and binding["target_repo_path"] and binding["review_overlay_path"]):
            diagnostics.append("Proposed-tree graph unavailable; continuing with changed-code and available local source.")
            return binding, diagnostics
        self._emit(callback, "graph_preparing", "Preparing proposed-tree graph")
        try:
            prepared = await self.rag_client.prepare_review_generation(**binding)
            if (prepared.get("status") != "ready" or prepared.get("source_revision") != head_revision
                    or not prepared.get("collection_target") or not prepared.get("generation_manifest_sha256")):
                raise ValueError(str(prepared.get("error") or "graph snapshot receipt is unavailable"))
            binding["review_collection_target"] = prepared["collection_target"]
            binding["review_generation_manifest_sha256"] = prepared["generation_manifest_sha256"]
            self._emit(callback, "graph_ready", "Proposed-tree graph is ready")
        except Exception as error:
            diagnostics.append(f"Proposed-tree graph unavailable; continuing with local source: {error}")
        return binding, diagnostics

    async def _review(self, request: ReviewRequestDto, callback: Any) -> dict[str, Any]:
        raw_diff = (request.deltaDiff if str(request.analysisMode or "").upper() == "INCREMENTAL"
                    and request.deltaDiff else request.rawDiff)
        if not raw_diff:
            raise ValueError("changed-code diff is unavailable")
        parts, unparsed_paths = _parts(raw_diff)
        # Incremental jobs own only delta anchors, but previous changes remain
        # accessible as evidence when a contract spans PR iterations.
        context_parts = _parts(request.rawDiff)[0] if request.rawDiff and request.rawDiff != raw_diff else []
        if not parts and not unparsed_paths:
            return {"status": "complete", "comment": "No changed text to review.", "issues": []}
        if not parts:
            return {"status": "partial", "comment": "Review incomplete: the changed text could not be parsed.",
                    "issues": [], "reviewedHunkIds": [],
                    "unresolvedScopes": {f"path:{path}": "changed text could not be parsed into a hunk" for path in unparsed_paths}}
        binding, diagnostics = await self._prepare_context(request, callback)
        self._emit(callback, "planning", "Planning change ownership and cross-file dependencies")

        async def graph_reader(**query: Any) -> list[dict[str, Any]]:
            return await self._graph_results(binding, **query)

        plan = await ReviewPlanner(graph_reader if binding.get("review_collection_target") else None).plan(parts)
        diagnostics.extend(plan.diagnostics)
        llm = LLMFactory.create_llm(request.aiModel, request.aiProvider, request.aiApiKey,
                                    ai_base_url=request.aiBaseUrl, ai_custom_parameters=request.aiCustomParameters)
        source = LocalReviewSource(binding, [part.path for part in parts])
        source_context: dict[str, list[dict[str, Any]]] = {}

        async def discover(batch: Any) -> Any:
            async with self._batch_semaphore:
                self._emit(callback, "reviewing", f"Reviewing {', '.join(sorted({part.path for part in batch.parts}))}")
                try:
                    owner_source = await self._owner_source((*batch.parts, *batch.companion_parts), plan.graph_context, source, binding)
                except Exception as error:
                    owner_source = [{"status": "unavailable", "diagnostic": f"Owner source unavailable: {error}"}]
                    logger.warning("Batch source context unavailable: %s", error)
                source_context[batch.id] = owner_source
                return await review_batch(llm=llm, request=request, batch=batch, plan=plan,
                                          owner_source=owner_source, normalize=self._finding)

        results = await asyncio.gather(*(discover(batch) for batch in plan.batches))
        candidates = [issue for result in results for issue in result.findings]
        reviewed = {part_id for result in results for part_id in result.reviewed}
        summaries = [summary for result in results for summary in result.summaries]
        investigations = [question for result in results for question in result.investigations]
        diagnostics.extend(message for result in results for message in result.diagnostics)
        if plan.cross_batch_scopes:
            self._emit(callback, "cross_file", "Checking interactions between batch summaries")
        async with self._batch_semaphore:
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
                    semaphore=self._batch_semaphore,
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
        """Supply complete owners, with an actual file scope when graph ownership is absent."""
        visible: dict[tuple[str, str], set[int]] = {}
        ranges: dict[tuple[str, str], list[tuple[int, int]]] = {}
        file_scopes: set[tuple[str, str]] = set()
        for part in parts:
            key = (part.path, part.side)
            visible.setdefault(key, set()).update(_visible_source_lines(part))
            context = graph_context.get(part.id) or {}
            context = context if isinstance(context, Mapping) else {}
            units = context.get("units") or []
            units = units if isinstance(units, (list, tuple)) else []
            owner_spans = []
            # Graph coordinates describe the proposed tree. A deletion's
            # target-side location must never be resolved against those spans.
            for unit in units if part.side == "proposed" else ():
                if not isinstance(unit, Mapping):
                    continue
                try:
                    start, end = int(unit["startLine"]), int(unit["endLine"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if str(unit.get("path") or part.path) == part.path and 0 < start <= end:
                    owner_spans.append((start, end))
            ranges.setdefault(key, []).extend(owner_spans)
            if (context.get("structuralOwnershipComplete") is False or not owner_spans
                    or any(not any(start <= line <= end for start, end in owner_spans) for line in part.anchors)):
                file_scopes.add(key)

        windows: list[dict[str, Any]] = []
        for (path, side), spans in sorted(ranges.items()):
            merged: list[tuple[int, int | None]] = []
            if (path, side) in file_scopes:
                merged = [(1, None)]
            else:
                for start, end in sorted(set(spans)):
                    if all(line in visible[(path, side)] for line in range(start, end + 1)):
                        continue
                    # Adjacent definitions remain independent evidence scopes.
                    # Only overlapping owners require a shared complete range.
                    if merged and start <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                    else:
                        merged.append((start, end))
            for start, end in merged:
                value = await asyncio.to_thread(source.read, path, side=side, start_line=start, end_line=end)
                if (value.get("status") != "ready" and side == "proposed"
                        and binding.get("review_collection_target")):
                    try:
                        value = await self.rag_client.get_review_file_content(
                            **binding, focus_paths=[path], path=path, side=side, start_line=start, end_line=end,
                        )
                    except Exception as error:
                        value = {"status": "unavailable", "path": path, "side": side, "diagnostic": str(error)}
                if value.get("status") == "ready" and end is None:
                    # Complete additions/deletions already present in the diff
                    # need no duplicate file body after resolving its extent.
                    try:
                        source_end = int(value["endLine"])
                    except (KeyError, TypeError, ValueError, OverflowError):
                        source_end = None
                    if source_end is not None and all(line in visible[(path, side)] for line in range(start, source_end + 1)):
                        continue
                windows.append(value)
        return windows

    async def _graph_results(self, binding: dict[str, Any], *, pattern: str,
                             target: str, focus_path: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        cursor = 0
        while True:
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
