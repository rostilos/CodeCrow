"""Review changed behavior using one pinned proposed-tree graph."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from llm.llm_factory import LLMFactory
from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs
from model.dtos import ReviewRequestDto
from service.rag.rag_client import RagClient
from service.review.snapshot_identity import (
    resolve_exact_structural_base_revision,
    validate_review_snapshot_identity,
)
from utils.diff_processor import HunkDisposition, process_raw_diff


logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Review the changed behavior for actionable defects. The diff is
the complete changed-code worklist. The graph is a navigation map, not proof
that code is safe. Confirm a finding against exact source when the diff and
graph do not already establish its failure mechanism. Inspect callers,
implementations and tests when a change can affect them. Do not report style,
speculative risks or issues outside the changed lines.

Return only one JSON object:
{"reviewedHunkIds":["hunk id"],"findings":[{"partId":"hunk id",
"file":"path","line":1,"severity":"HIGH|MEDIUM|LOW",
"category":"BUG_RISK|SECURITY|PERFORMANCE|ERROR_HANDLING|ARCHITECTURE|CODE_QUALITY|TESTING",
"title":"short defect","reason":"source-supported failure mechanism",
"suggestedFixDescription":"practical fix"}],
"reads":[{"kind":"graph|unit|file|diff|search","pattern":"for graph",
"target":"for graph","cursor":0,"unitId":"for unit","offset":0,
"path":"for file or diff","startLine":1,"endLine":20,
"side":"proposed or target","query":"for literal search"}],
"unresolvedReason":"if still uncertain"}

Only include fields that apply to each requested read. A graph read uses
pattern and target; a unit read uses unitId; a file read uses path and optional
line range/side; a diff read uses path; a search uses query and optional cursor.
You can request more reads on later
turns, including other files. Put a hunk in reviewedHunkIds only after deciding
whether it has a defect. Anchor each finding to an anchor line in its hunk.
If evidence remains insufficient, leave that hunk unreviewed and say why.
Do not repeat a read whose actual result is already supplied."""


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


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, Mapping) and item.get("type") == "text"
        ).strip()
    return str(content).strip()


def _parse_turn(response: Any) -> dict[str, Any]:
    content = _response_text(response)
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    turn = json.loads(content)
    if not isinstance(turn, dict):
        raise ValueError("review model did not return the requested JSON object")
    for field in ("reviewedHunkIds", "findings", "reads"):
        value = turn.get(field)
        if value is None:
            turn[field] = []
        elif field == "reviewedHunkIds" and isinstance(value, str):
            turn[field] = [value]
        elif field != "reviewedHunkIds" and isinstance(value, dict):
            turn[field] = [value]
        elif not isinstance(value, list):
            raise ValueError(f"review model returned an invalid {field} field")
    return turn


def _compact_relation(value: Mapping[str, Any]) -> dict[str, Any]:
    def endpoint(name: str) -> dict[str, Any]:
        unit = value.get(name)
        if not isinstance(unit, Mapping):
            return {}
        return {
            key: unit.get(key)
            for key in ("unitId", "qualifiedName", "path", "startLine")
            if unit.get(key) is not None
        }

    return {
        "kind": value.get("kind"),
        "source": endpoint("sourceUnit"),
        "target": endpoint("targetUnit"),
        "origin": value.get("origin"),
    }


class ReviewService:
    MAX_CONCURRENT_REVIEWS = int(os.environ.get("MAX_CONCURRENT_REVIEWS", "4"))

    def __init__(self, rag_client: RagClient | None = None):
        self.rag_client = rag_client or RagClient()
        self._review_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REVIEWS)

    async def process_review_request(
        self,
        request: ReviewRequestDto,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        async with self._review_semaphore:
            try:
                return {"result": await self._review(request, event_callback)}
            except Exception as error:
                logger.exception(
                    "Review incomplete for project=%s PR=%s",
                    request.projectId, request.pullRequestId,
                )
                return {"result": {
                    "status": "error",
                    "comment": f"Review incomplete: {error}",
                    "issues": [],
                }}

    async def _review(
        self,
        request: ReviewRequestDto,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        identity = validate_review_snapshot_identity(request)
        base_revision = resolve_exact_structural_base_revision(request)
        target_path = request.localRagRepoPath or request.localRepoPath
        overlay_path = request.localReviewOverlayPath
        raw_diff = (
            request.deltaDiff
            if str(request.analysisMode or "").upper() == "INCREMENTAL"
            and request.deltaDiff else request.rawDiff
        )
        if not raw_diff:
            raise ValueError("changed-code diff is unavailable")
        parts, unparsed_paths = _parts(raw_diff)
        if not parts and not unparsed_paths:
            return {"status": "complete", "comment": "No changed text to review.", "issues": []}
        if not parts:
            return {
                "status": "partial",
                "comment": "Review incomplete: the changed text could not be parsed.",
                "issues": [],
                "reviewedHunkIds": [],
                "unresolvedScopes": {
                    f"path:{path}": "changed text could not be parsed into a hunk"
                    for path in unparsed_paths
                },
            }
        if not (base_revision and target_path and overlay_path):
            raise ValueError("exact target snapshot and proposed-file overlay are required")
        if not self.rag_client.enabled:
            raise ValueError("proposed-tree graph service is unavailable")

        binding: dict[str, Any] = {
            "workspace": request.projectWorkspace,
            "project": request.projectNamespace,
            "target_branch": identity.target_branch,
            "base_revision": base_revision,
            "source_revision": identity.head_revision,
            "target_repo_path": target_path,
            "review_overlay_path": overlay_path,
        }
        if request.ragCollectionTarget and request.ragBaseGenerationManifestSha256:
            binding["base_collection_target"] = request.ragCollectionTarget
            binding["base_generation_manifest_sha256"] = (
                request.ragBaseGenerationManifestSha256
            )
        self._emit(callback, "graph_preparing", "Preparing proposed-tree graph")
        prepared = await self.rag_client.prepare_review_generation(**binding)
        if (
            prepared.get("status") != "ready"
            or prepared.get("source_revision") != identity.head_revision
        ):
            raise ValueError(
                str(prepared.get("error") or "proposed-tree graph is unavailable")
            )
        binding["review_collection_target"] = prepared.get("collection_target")
        binding["review_generation_manifest_sha256"] = (
            prepared.get("generation_manifest_sha256")
        )
        if not all((
            binding["review_collection_target"],
            binding["review_generation_manifest_sha256"],
        )):
            raise ValueError("proposed-tree graph has no read receipt")
        self._emit(callback, "graph_ready", "Proposed-tree graph is ready")

        groups, graph_context = await self._group_parts(parts, binding)
        llm = LLMFactory.create_llm(
            request.aiModel, request.aiProvider, request.aiApiKey,
            ai_base_url=request.aiBaseUrl,
            ai_custom_parameters=request.aiCustomParameters,
        )
        diff_by_path: dict[str, str] = {}
        for part in parts:
            diff_by_path[part.path] = (
                diff_by_path.get(part.path, "") + part.diff + "\n"
            )
        findings: list[dict[str, Any]] = []
        reviewed: set[str] = set()
        unresolved: dict[str, str] = {}
        read_cache: dict[str, dict[str, Any]] = {}
        for number, group in enumerate(groups, start=1):
            self._emit(
                callback, "reviewing",
                f"Reviewing related change {number}/{len(groups)}",
            )
            try:
                group_findings, group_reviewed, reason = await self._review_group(
                    llm, request, binding, group, graph_context,
                    diff_by_path, read_cache,
                )
            except Exception as error:
                logger.warning(
                    "Related change review incomplete: project=%s PR=%s part=%s error=%s",
                    request.projectId, request.pullRequestId, group[0].id, error,
                )
                group_findings, group_reviewed, reason = [], set(), str(error)
            findings.extend(group_findings)
            reviewed.update(group_reviewed)
            for part in group:
                if part.id not in group_reviewed:
                    unresolved[part.id] = reason or "evidence was insufficient"
        for path in unparsed_paths:
            unresolved[f"path:{path}"] = "changed text could not be parsed into a hunk"
        unique = {
            (issue["file"], issue["line"], issue["title"].casefold()): issue
            for issue in findings
        }
        status = "partial" if unresolved else "complete"
        comment = f"Reviewed {len(reviewed)} of {len(parts)} changed-code parts."
        if unresolved:
            paths = sorted({
                part.path for part in parts if part.id in unresolved
            } | set(unparsed_paths))
            comment = (
                f"Partial review: {comment} {len(unresolved)} change scope(s) "
                f"remain unresolved in: {', '.join(paths)}."
            )
        return {
            "status": status,
            "comment": comment,
            "issues": list(unique.values()),
            "reviewedHunkIds": sorted(reviewed),
            "unresolvedScopes": unresolved,
        }

    async def _graph_results(
        self,
        binding: dict[str, Any],
        *,
        pattern: str,
        target: str,
        focus_path: str,
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        cursor = 0
        while True:
            page = await self.rag_client.query_review_graph(
                **binding, focus_paths=[focus_path],
                pattern=pattern, target=target, cursor=cursor,
                max_results=100, include_source=False,
            )
            if page.get("status") != "ready":
                raise ValueError(
                    str(page.get("error") or f"graph {pattern} query unavailable")
                )
            values.extend(
                item for item in page.get("results") or []
                if isinstance(item, dict)
            )
            next_cursor = page.get("nextCursor")
            if next_cursor is None:
                return values
            next_cursor = int(next_cursor)
            if next_cursor <= cursor:
                raise ValueError("graph query did not advance its continuation")
            cursor = next_cursor

    async def _group_parts(
        self,
        parts: list[ReviewPart],
        binding: dict[str, Any],
    ) -> tuple[list[list[ReviewPart]], dict[str, dict[str, Any]]]:
        by_path: dict[str, list[ReviewPart]] = {}
        for part in parts:
            by_path.setdefault(part.path, []).append(part)
        unit_by_part: dict[str, dict[str, Any]] = {}
        for path, path_parts in by_path.items():
            units = await self._graph_results(
                binding, pattern="file_summary", target=path, focus_path=path,
            )
            for part in path_parts:
                lines = list(part.anchors)
                candidates = [
                    unit for unit in units
                    if unit.get("unitId")
                    and any(
                        int(unit.get("startLine") or 0) <= line
                        <= int(unit.get("endLine") or 0)
                        for line in lines
                    )
                ]
                if candidates:
                    unit_by_part[part.id] = min(
                        candidates,
                        key=lambda unit: (
                            int(unit.get("endLine") or 0)
                            - int(unit.get("startLine") or 0)
                        ),
                    )

        relations_by_unit: dict[str, list[dict[str, Any]]] = {}
        unit_paths: dict[str, str] = {}
        for part in parts:
            unit = unit_by_part.get(part.id)
            if unit:
                unit_paths[str(unit["unitId"])] = part.path
        for unit_id, path in unit_paths.items():
            relations_by_unit[unit_id] = await self._graph_results(
                binding, pattern="relations_of", target=unit_id,
                focus_path=path,
            )

        parent = {part.id: part.id for part in parts}

        def root(part_id: str) -> str:
            while parent[part_id] != part_id:
                parent[part_id] = parent[parent[part_id]]
                part_id = parent[part_id]
            return part_id

        def join(left: str, right: str) -> None:
            parent[root(right)] = root(left)

        parts_by_unit: dict[str, list[str]] = {}
        for part_id, unit in unit_by_part.items():
            parts_by_unit.setdefault(str(unit["unitId"]), []).append(part_id)
        for part_ids in parts_by_unit.values():
            for part_id in part_ids[1:]:
                join(part_ids[0], part_id)
        for relations in relations_by_unit.values():
            for relation in relations:
                relation_kind = str(relation.get("kind") or "").upper()
                relation_action = str(relation.get("relation") or "").lower()
                if not (
                    relation_action.startswith("call")
                    or relation_action in {"implements", "extends", "tests"}
                    or relation_kind in {"CALLS", "IMPLEMENTS", "EXTENDS", "TESTS"}
                ):
                    continue
                source = relation.get("sourceUnit") or {}
                target = relation.get("targetUnit") or {}
                left = parts_by_unit.get(str(source.get("unitId") or ""), [])
                right = parts_by_unit.get(str(target.get("unitId") or ""), [])
                if left and right:
                    join(left[0], right[0])
        groups_by_root: dict[str, list[ReviewPart]] = {}
        for part in parts:
            groups_by_root.setdefault(root(part.id), []).append(part)
        graph_context: dict[str, dict[str, Any]] = {}
        for part in parts:
            unit = unit_by_part.get(part.id)
            if not unit:
                continue
            unit_id = str(unit["unitId"])
            graph_context[part.id] = {
                "unit": {
                    key: unit.get(key)
                    for key in (
                        "unitId", "qualifiedName", "path", "kind",
                        "startLine", "endLine",
                    )
                    if unit.get(key) is not None
                },
                "relations": [
                    _compact_relation(relation)
                    for relation in relations_by_unit.get(unit_id, [])
                ],
            }
        return list(groups_by_root.values()), graph_context

    async def _review_group(
        self,
        llm: Any,
        request: ReviewRequestDto,
        binding: dict[str, Any],
        group: list[ReviewPart],
        graph_context: dict[str, dict[str, Any]],
        diff_by_path: dict[str, str],
        read_cache: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], set[str], str]:
        expected = {part.id for part in group}
        parts_by_id = {part.id: part for part in group}
        reviewed: set[str] = set()
        findings: list[dict[str, Any]] = []
        evidence: dict[str, dict[str, Any]] = {}
        graph = list({
            json.dumps(relation, sort_keys=True): relation
            for part in group
            for relation in graph_context.get(part.id, {}).get("relations", [])
        }.values())
        reason = ""
        while reviewed != expected:
            prompt = {
                "changeParts": [
                    {
                        "id": part.id, "path": part.path,
                        "side": part.side, "anchorLines": list(part.anchors),
                        "diff": part.diff,
                    }
                    for part in group if part.id not in reviewed
                ],
                "graphRelations": graph,
                "changedUnits": [
                    graph_context[part.id]["unit"]
                    for part in group if part.id in graph_context
                ],
                "readResults": list(evidence.values()),
                "acceptedFindings": findings,
                "prTitle": request.prTitle,
                "prDescription": request.prDescription,
                "projectRules": request.projectRules,
                "taskContext": request.taskContext,
            }
            options = reasoning_request_kwargs(llm, ReasoningEffort.LOW)
            if request.aiProvider.lower() in {"openrouter", "openai"}:
                options["response_format"] = {"type": "json_object"}
            try:
                response = await llm.ainvoke([
                    ("system", _SYSTEM_PROMPT),
                    ("human", json.dumps(prompt, ensure_ascii=False)),
                ], **options)
            except Exception as error:
                return findings, reviewed, str(error)
            logger.info(
                "Review model response: PR=%s usage=%s",
                request.pullRequestId,
                getattr(response, "usage_metadata", None),
            )
            try:
                turn = _parse_turn(response)
            except (TypeError, ValueError) as error:
                return findings, reviewed, str(error)
            new_reviewed = set()
            for part_id in turn["reviewedHunkIds"]:
                if isinstance(part_id, str) and part_id in expected:
                    new_reviewed.add(part_id)
            for value in turn["findings"]:
                issue = self._finding(value, parts_by_id)
                if issue:
                    findings.append(issue)
                    new_reviewed.add(str(value["partId"]))
            new_reviewed.difference_update(reviewed)
            reviewed.update(new_reviewed)
            if reviewed == expected:
                break
            new_evidence = False
            for read in turn["reads"]:
                if not isinstance(read, dict):
                    continue
                key = json.dumps({
                    "read": read,
                    "focusPaths": (
                        sorted({part.path for part in group})
                        if read.get("kind") == "graph" else []
                    ),
                }, sort_keys=True, ensure_ascii=False)
                if key not in read_cache:
                    read_cache[key] = await self._execute_read(
                        read, binding, group, diff_by_path,
                    )
                if key not in evidence:
                    evidence[key] = {
                        "request": read,
                        "result": read_cache[key],
                    }
                    new_evidence = True
            if not new_evidence and not new_reviewed:
                reason = str(turn.get("unresolvedReason") or "No new evidence or decision")
                break
        return findings, reviewed, reason

    async def _execute_read(
        self,
        read: dict[str, Any],
        binding: dict[str, Any],
        group: list[ReviewPart],
        diff_by_path: dict[str, str],
    ) -> dict[str, Any]:
        kind = read.get("kind")
        path = str(read.get("path") or "")
        focus_paths = list(dict.fromkeys(part.path for part in group))
        try:
            if kind == "diff":
                if path not in diff_by_path:
                    return {"status": "missing", "path": path}
                return {"status": "ready", "path": path, "diff": diff_by_path[path]}
            if kind == "file":
                start = int(read.get("startLine") or 1)
                end = int(read.get("endLine") or 0) or None
                return await self.rag_client.get_review_file_content(
                    **binding, focus_paths=focus_paths,
                    path=path, side=str(read.get("side") or "proposed"),
                    start_line=start, end_line=end,
                )
            if kind == "unit":
                return await self.rag_client.get_review_structural_unit(
                    **binding, focus_paths=focus_paths,
                    unit_id=str(read.get("unitId") or ""),
                    offset=int(read.get("offset") or 0),
                )
            if kind == "graph":
                return await self.rag_client.query_review_graph(
                    **binding, focus_paths=focus_paths,
                    pattern=str(read.get("pattern") or ""),
                    target=str(read.get("target") or ""),
                    cursor=int(read.get("cursor") or 0),
                    include_source=False,
                )
            if kind == "search":
                return await self.rag_client.search_review_code(
                    **binding, focus_paths=focus_paths,
                    query=str(read.get("query") or ""),
                    cursor=int(read.get("cursor") or 0),
                )
            return {"status": "error", "error": f"unknown read kind: {kind}"}
        except Exception as error:
            return {"status": "error", "error": str(error)}

    @staticmethod
    def _finding(
        value: Any,
        parts_by_id: dict[str, ReviewPart],
    ) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        part = parts_by_id.get(str(value.get("partId") or ""))
        if not part or value.get("file") != part.path:
            return None
        try:
            line = int(value.get("line"))
        except (TypeError, ValueError):
            return None
        if line not in part.anchors:
            return None
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
