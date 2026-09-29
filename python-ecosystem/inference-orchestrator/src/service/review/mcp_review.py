"""Agent-planned, graph-first review with focused analysis and source verification."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from llm.reasoning_policy import ReasoningEffort
from service.review import mcp_prompts
from service.review.async_work import gather_review_work
from service.review.change_context import anchor_ranges
from service.review.review_stages import investigations_from, normalize_candidates, unanchored_questions
from service.review.tool_conversation import ToolConversation, converse
from service.review.verification_tools import VerificationTools
from service.review.verifier import ReviewVerifier


# A packing target, never a truncation boundary: an individual hunk stays whole.
# Source definitions remain independently retrievable through the same tools.
TARGET_BATCH_DIFF_CHARACTERS = 24_000
PLANNING_TOOLS = {
    "listReviewChanges", "queryCodeGraph", "getMinimalReviewContext",
    "getImpactRadius", "traverseCodeGraph",
}


@dataclass
class McpBatch:
    id: str
    parts: tuple[Any, ...]
    focus: str
    related_paths: tuple[str, ...] = ()


@dataclass
class McpOutcome:
    findings: list[dict[str, Any]] = field(default_factory=list)
    reviewed: set[str] = field(default_factory=set)
    unresolved: dict[str, str] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)


def objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def change_inventory(parts: Sequence[Any]) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for part in parts:
        value = by_path.setdefault(part.path, {"path": part.path, "hunks": 0, "diffCharacters": 0})
        value["hunks"] += 1
        value["diffCharacters"] += len(part.diff)
    return list(by_path.values())


def plan_batches(parts: Sequence[Any], outputs: Sequence[dict[str, Any]]) -> tuple[list[McpBatch], list[str]]:
    """Honor useful model grouping; recover omissions without dropping any hunk."""
    by_path: dict[str, list[Any]] = {}
    for part in parts:
        by_path.setdefault(part.path, []).append(part)
    groups = next((objects(output["groups"]) for output in reversed(outputs) if "groups" in output), [])
    assigned: set[str] = set()
    selected = []
    for group in groups:
        paths = [path for path in dict.fromkeys(strings(group.get("paths"))) if path in by_path and path not in assigned]
        if not paths:
            continue
        assigned.update(paths)
        selected.append((paths, str(group.get("focus") or "Review changed behavior and its dependencies"),
                         tuple(dict.fromkeys(strings(group.get("relatedPaths"))))))
    missing = [path for path in by_path if path not in assigned]
    diagnostics = []
    if missing:
        diagnostics.append(f"MCP planner left {len(missing)} path(s) unassigned; complete file scopes added for analysis.")
        selected.extend(([path], "Review changed behavior and follow relevant graph/source dependencies", ()) for path in missing)
    batches = []
    for paths, focus, related in selected:
        current: list[Any] = []
        characters = 0
        for path in paths:
            for part in by_path[path]:
                if current and characters + len(part.diff) > TARGET_BATCH_DIFF_CHARACTERS:
                    batches.append(McpBatch(f"mcp-{len(batches) + 1}", tuple(current), focus, related))
                    current, characters = [], 0
                current.append(part)
                characters += len(part.diff)
        if current:
            batches.append(McpBatch(f"mcp-{len(batches) + 1}", tuple(current), focus, related))
    return batches, diagnostics


def read_hunks(run: ToolConversation) -> set[str]:
    return {str(part["id"]) for record in run.evidence.values() if record["kind"] == "getReviewDiff"
            for part in record["result"].get("parts", []) if isinstance(part, dict) and part.get("id")}


def acknowledged_hunks(run: ToolConversation) -> set[str]:
    return {part_id for output in run.outputs for part_id in strings(output.get("reviewedHunkIds"))} & read_hunks(run)


class McpReview:
    def __init__(self, rag_client: Any):
        self.rag_client = rag_client

    async def review(self, *, llm: Any, request: Any, parts: Sequence[Any],
                     context_parts: Sequence[Any], binding: dict[str, Any],
                     normalize: Callable, callback: Any = None) -> McpOutcome:
        outcome = McpOutcome()
        cache, in_flight = {}, {}
        parts_by_id = {part.id: part for part in parts}
        runs: list[ToolConversation] = []

        def emit(state: str, message: str):
            if callback:
                callback({"type": "status", "state": state, "message": message})

        def tools(paths=()):
            value = VerificationTools(rag_client=self.rag_client, binding=binding, parts=parts,
                                      context_parts=context_parts, focus_paths=paths)
            value.cache, value.in_flight = cache, in_flight
            return value

        common = {"changePurpose": {"title": request.prTitle, "description": request.prDescription},
                  "projectRules": request.projectRules, "taskContext": request.taskContext,
                  "graphAvailable": bool(binding.get("review_collection_target"))}
        emit("planning", "Planning focused review tasks with graph metadata")
        planning = await converse(llm=llm, request=request, tools=tools(), system=mcp_prompts.PLAN,
                                 payload={**common, "changedFiles": change_inventory(parts)},
                                 stage="mcp_planning", batch_ids=[], allowed=PLANNING_TOOLS,
                                 effort=ReasoningEffort.LOW)
        runs.append(planning)
        batches, diagnostics = plan_batches(parts, planning.outputs)
        outcome.diagnostics.extend(diagnostics)

        async def analyze(batch: McpBatch):
            owned = {part.id for part in batch.parts}

            def feedback(run):
                remaining = owned - acknowledged_hunks(run)
                obstacles = [value.get("unresolvedReason") for value in run.outputs if value.get("unresolvedReason")]
                if not remaining or obstacles:
                    return None
                return {"remainingHunkIds": sorted(remaining),
                        "instruction": "Retrieve the remaining complete hunks and analyze them. Report reviewed IDs or a concrete unresolvedReason."}

            emit("reviewing", f"Analyzing graph/source evidence for task {batch.id}")
            return await converse(
                llm=llm, request=request, tools=tools(sorted({part.path for part in batch.parts})),
                system=mcp_prompts.ANALYZE, stage="mcp_analysis", batch_ids=[batch.id],
                payload={**common, "taskId": batch.id, "focus": batch.focus,
                         "relatedPaths": list(batch.related_paths), "ownedParts": [
                             {"id": part.id, "path": part.path, "side": part.side,
                              "anchorRanges": anchor_ranges(part.anchors), "diffCharacters": len(part.diff)}
                             for part in batch.parts]},
                feedback=feedback, checkpoint=True,
            )

        analyzed = await gather_review_work(*(analyze(batch) for batch in batches))
        runs.extend(analyzed)
        candidates, investigations, summaries = [], [], []

        def ingest(run: ToolConversation, batch_ids: list[str], scoped_parts: dict[str, Any]):
            for value in run.outputs:
                raw = []
                for finding in objects(value.get("findings")):
                    locations = []
                    for evidence_id in strings(finding.get("evidenceIds")):
                        source = (run.evidence.get(evidence_id) or {}).get("result") or {}
                        if source.get("status") == "ready" and isinstance(source.get("content"), str) and source.get("path"):
                            locations.append({key: source[key] for key in ("path", "startLine", "endLine", "side") if key in source})
                    raw.append({**finding, "sourceLocations": locations,
                                "relatedPaths": sorted(set(strings(finding.get("relatedPaths"))) | {item["path"] for item in locations})})
                for issue in normalize_candidates(raw, parts_by_id, normalize, batch_ids=batch_ids):
                    # Source pointers route independent verification; bodies stay
                    # in the owning conversation instead of being preloaded again.
                    if issue not in candidates:
                        candidates.append(issue)
                questions = objects(value.get("investigations"))
                summary = value.get("summary")
                if isinstance(summary, dict):
                    questions.extend(objects(summary.get("unresolvedQuestions")))
                questions.extend(unanchored_questions(raw, parts_by_id, normalize))
                for question in investigations_from(questions, origin=batch_ids[0], parts=scoped_parts):
                    key = {name: item for name, item in question.items() if name != "id"}
                    if any({name: item for name, item in old.items() if name != "id"} == key for old in investigations):
                        continue
                    question["id"] = f"{batch_ids[0]}:question:{len(investigations) + 1}"
                    investigations.append(question)

        for batch, run in zip(batches, analyzed):
            owned = {part.id: part for part in batch.parts}
            reviewed = acknowledged_hunks(run) & owned.keys()
            outcome.reviewed.update(reviewed)
            ingest(run, [batch.id], owned)
            if not run.complete:
                outcome.unresolved[f"task:{batch.id}"] = "Analysis conversation was interrupted; completed work retained"
            summary = next((value["summary"] for value in reversed(run.outputs) if isinstance(value.get("summary"), dict)), {})
            summaries.append({"batchId": batch.id, "paths": sorted({part.path for part in batch.parts}),
                              "partIds": list(owned), "summary": {
                                  key: summary[key] for key in ("behaviorChanges", "contracts") if key in summary
                              }})
            for part_id in owned.keys() - reviewed:
                reason = next((str(value["unresolvedReason"]) for value in reversed(run.outputs) if value.get("unresolvedReason")),
                              "MCP analysis did not finish this changed hunk")
                outcome.unresolved[f"hunk:{part_id}"] = reason

        if len(batches) > 1:
            emit("cross_file", "Checking remaining contracts across focused review tasks")
            def cross_feedback(run):
                if any(isinstance(value.get("findings"), list) and isinstance(value.get("investigations"), list)
                       for value in run.outputs):
                    return None
                return {"instruction": "Finish with the requested findings and investigations arrays, including empty arrays when no incompatible contract remains."}

            cross = await converse(llm=llm, request=request, tools=tools(), system=mcp_prompts.CROSS,
                                   payload={**common, "batchSummaries": summaries,
                                            "existingCandidates": [{key: issue[key] for key in ("partId", "file", "line", "title", "reason")}
                                                                   for issue in candidates],
                                            "pendingInvestigations": investigations},
                                   stage="mcp_cross_file", batch_ids=[batch.id for batch in batches],
                                   checkpoint=True, feedback=cross_feedback)
            runs.append(cross)
            ingest(cross, ["mcp-cross"], parts_by_id)
            if not cross.complete:
                outcome.unresolved["cross_file"] = "Cross-task review could not finish; completed candidates retained"

        for question in investigations:
            outcome.unresolved[question["id"]] = question["question"]
        if candidates or investigations:
            emit("verifying", "Independently verifying candidates and questions with graph/source tools")
            try:
                verification = await ReviewVerifier(self.rag_client).verify(
                    llm=llm, request=request, findings=candidates, summaries=summaries,
                    parts=parts, context_parts=context_parts, binding=binding,
                    investigations=investigations, callback=callback, demand_driven=True,
                )
                candidates = verification.issues
                outcome.diagnostics.extend(verification.diagnostics + verification.warnings)
                if verification.diagnostics:
                    outcome.unresolved["verification"] = "; ".join(verification.diagnostics)
                for question_id in verification.resolved_investigation_ids:
                    outcome.unresolved.pop(question_id, None)
            except Exception as error:
                outcome.unresolved["verification"] = f"Verification unavailable; completed candidates retained: {error}"
                outcome.diagnostics.append(outcome.unresolved["verification"])
        outcome.findings = candidates
        for run in runs:
            outcome.diagnostics.extend(run.diagnostics)
        outcome.statistics = {"analysisTasks": len(batches),
                              "planningAndAnalysisToolCalls": sum(run.tool_calls for run in runs),
                              "planningAndAnalysisGraphCalls": sum(run.graph_calls for run in runs)}
        return outcome
