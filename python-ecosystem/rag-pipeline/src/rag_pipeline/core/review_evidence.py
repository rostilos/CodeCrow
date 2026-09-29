"""Assemble graph navigation, source windows and continuation evidence."""
from __future__ import annotations

import json
import re
from typing import Any, Iterator, Mapping, Sequence

from .review_snapshot import ProposedTreeGeneration, _REVIEW_GRAPH_COMPOSITION

_QUESTION_TERM = re.compile(r"[A-Za-z_][A-Za-z0-9_:$\\.\-/]{2,}")
_TEST_PATH = re.compile(r"(^|/)(tests?|specs?|__tests__)(/|$)|(?:test|spec)\.[^.]+$", re.I)
_STOP_TERMS = {
    "about", "after", "again", "against", "analysis", "before", "being",
    "between", "change", "changed", "changes", "could", "during", "from",
    "have", "into", "might", "please", "review", "should", "that", "their",
    "there", "these", "this", "through", "what", "when", "where", "which",
    "with", "would",
}
_MAX_TERM_SEARCHES = 8
_MAX_ANCHOR_GRAPH_TARGETS = 12
_MAX_SOURCE_WINDOW_CHARACTERS = 8000
_DIRECT_GRAPH_PATTERNS = (
    "callers_of",
    "callees_of",
    "references_to",
    "importers_of",
    "inheritors_of",
    "tests_for",
    "framework_relations",
)



def _question_terms(question: str, focus_symbols: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in (*focus_symbols, *_QUESTION_TERM.findall(question or "")):
        normalized = value.strip()
        if not normalized or normalized.casefold() in _STOP_TERMS:
            continue
        if normalized not in result:
            result.append(normalized)
        if len(result) >= _MAX_TERM_SEARCHES:
            break
    return result



def _fair_anchor_target_groups(
    anchors: Sequence[Mapping[str, Any]],
    *,
    max_targets: int,
) -> list[tuple[str, tuple[str, ...]]]:
    """Choose graph targets without letting a dense changed path crowd out peers."""

    candidates: list[tuple[str, list[str]]] = []
    for anchor in sorted(
        anchors,
        key=lambda item: str(item.get("path") or ""),
    ):
        path = str(anchor.get("path") or "")
        targets = list(dict.fromkeys(
            str(unit.get("qualifiedName") or unit.get("name") or unit.get("unitId"))
            for unit in anchor.get("symbols") or ()
            if isinstance(unit, Mapping)
            and (unit.get("qualifiedName") or unit.get("name") or unit.get("unitId"))
        ))
        if path and targets:
            candidates.append((path, targets))

    selected: list[list[str]] = [[] for _candidate in candidates]
    selected_count = 0
    depth = 0
    while selected_count < max_targets:
        added = False
        for index, (_path, targets) in enumerate(candidates):
            if depth >= len(targets):
                continue
            selected[index].append(targets[depth])
            selected_count += 1
            added = True
            if selected_count >= max_targets:
                break
        if not added:
            break
        depth += 1
    return [
        (candidates[index][0], tuple(targets))
        for index, targets in enumerate(selected)
        if targets
    ]



def _fair_direct_query_lanes(
    target_groups: Sequence[tuple[str, Sequence[str]]],
) -> Iterator[tuple[str, str, str]]:
    """Interleave changed roots and relation categories deterministically."""

    if not target_groups:
        return
    max_depth = max(len(targets) for _path, targets in target_groups)
    pattern_count = len(_DIRECT_GRAPH_PATTERNS)
    for depth in range(max_depth):
        for pattern_offset in range(pattern_count):
            for root_index, (path, targets) in enumerate(target_groups):
                if depth >= len(targets):
                    continue
                pattern = _DIRECT_GRAPH_PATTERNS[
                    (pattern_offset + root_index) % pattern_count
                ]
                yield path, targets[depth], pattern



def _select_fair_direct_relations(
    reader,
    target_groups: Sequence[tuple[str, Sequence[str]]],
    *,
    existing_evidence_ids: set[str],
    max_relations: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Select a bounded direct slice with one result per root/category lane first."""

    if max_relations <= 0:
        return [], False
    selected: list[dict[str, Any]] = []
    deferred: list[list[dict[str, Any]]] = []
    seen = set(existing_evidence_ids)
    truncated = False
    for _path, target, pattern in _fair_direct_query_lanes(target_groups):
        remaining = max_relations - len(selected)
        if remaining <= 0:
            break
        graph = reader.query_graph(
            pattern,
            target,
            max_results=min(3, remaining),
        )
        truncated = truncated or bool(graph.get("truncated"))
        candidates: list[dict[str, Any]] = []
        for relation in graph.get("results") or ():
            if not isinstance(relation, Mapping):
                continue
            evidence_id = str(relation.get("evidenceId") or "")
            if not evidence_id or evidence_id in seen:
                continue
            seen.add(evidence_id)
            candidates.append(dict(relation))
        if not candidates:
            continue
        selected.append(candidates[0])
        if len(candidates) > 1:
            deferred.append(candidates[1:])

    # Sparse graphs may expose useful evidence through only one lane. Fill the
    # remaining budget from those already-bounded query pages, still taking one
    # result from each lane per pass.
    while deferred and len(selected) < max_relations:
        next_deferred: list[list[dict[str, Any]]] = []
        for candidates in deferred:
            if len(selected) >= max_relations:
                break
            selected.append(candidates[0])
            if len(candidates) > 1:
                next_deferred.append(candidates[1:])
        deferred = next_deferred
    return selected, truncated



def _relation_kind(relation: Mapping[str, Any]) -> str:
    return " ".join((
        str(relation.get("kind") or ""),
        str(relation.get("relation") or ""),
    )).upper().replace("-", "_")



def _relation_paths(relation: Mapping[str, Any]) -> set[str]:
    result = {
        str(path)
        for path in relation.get("relatedPaths") or ()
        if isinstance(path, str) and path
    }
    origin = relation.get("origin") or {}
    if isinstance(origin, Mapping) and isinstance(origin.get("path"), str):
        result.add(origin["path"])
    for endpoint in (relation.get("sourceUnit"), relation.get("targetUnit")):
        if isinstance(endpoint, Mapping) and isinstance(endpoint.get("path"), str):
            result.add(endpoint["path"])
    return result



def _source_detail_for_manifest(reader, unit: Mapping[str, Any]) -> dict[str, Any] | None:
    manifest_resolver = getattr(reader, "source_detail_for_manifest", None)
    if callable(manifest_resolver):
        return manifest_resolver(unit)
    detail = reader.get_unit(str(unit.get("unitId") or ""))
    if detail and detail.get("sourceEvidence"):
        return detail
    path = unit.get("path")
    if not isinstance(path, str) or not path:
        return None
    try:
        line = max(1, int(unit.get("startLine") or 1))
    except (TypeError, ValueError):
        line = 1
    row = reader.connection.execute(
        "SELECT unit_id FROM units WHERE path = ? "
        "AND record_type IN ('source_unit', 'plugin_context') "
        "ORDER BY CASE WHEN start_line <= ? AND end_line >= ? THEN 0 ELSE 1 END, "
        "abs(start_line - ?), start_line, unit_id LIMIT 1",
        (path, line, line, line),
    ).fetchone()
    return reader.get_unit(row["unit_id"]) if row is not None else None



def _bounded_source_window(
    detail: Mapping[str, Any],
    *,
    remaining_characters: int,
    changed_paths: set[str],
    relation_evidence_ids: Sequence[str],
) -> dict[str, Any] | None:
    unit = detail.get("unit")
    if not isinstance(unit, Mapping):
        return None
    content = unit.get("content")
    if not isinstance(content, str) or not content or remaining_characters <= 0:
        return None
    ceiling = min(remaining_characters, _MAX_SOURCE_WINDOW_CHARACTERS)
    truncated = len(content) > ceiling
    selected = content[:ceiling]
    if truncated and "\n" in selected:
        selected = selected.rsplit("\n", 1)[0] + "\n"
    if not selected:
        return None
    start_line = max(1, int(unit.get("startLine") or 1))
    end_line = start_line + selected.count("\n")
    if not selected.endswith("\n"):
        end_line += 1
    path = str(unit.get("path") or "")
    return {
        "evidenceId": "source:" + str(unit.get("unitId")),
        "unitId": unit.get("unitId"),
        "path": path,
        "startLine": start_line,
        "endLine": max(start_line, end_line - 1),
        "content": selected,
        "contentSha256": unit.get("contentSha256"),
        "changedFile": path in changed_paths,
        "truncated": truncated,
        "relationEvidenceIds": list(dict.fromkeys(relation_evidence_ids)),
    }



def _compact_graph_evidence(
    relations: Sequence[Mapping[str, Any]],
    relation_hops: Mapping[str, int],
    source_windows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize repeated endpoint manifests into one node table."""

    source_window_by_unit = {
        str(window.get("unitId")): str(window.get("evidenceId"))
        for window in source_windows
        if window.get("unitId") and window.get("evidenceId")
    }
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for relation in relations:
        endpoint_ids: dict[str, str | None] = {}
        for key in ("sourceUnit", "targetUnit"):
            unit = relation.get(key)
            unit_id = (
                str(unit.get("unitId") or "")
                if isinstance(unit, Mapping)
                else ""
            )
            endpoint_ids[key] = unit_id or None
            if not unit_id or not isinstance(unit, Mapping) or unit_id in nodes:
                continue
            node = {
                "unitId": unit_id,
                "path": unit.get("path"),
                "kind": unit.get("kind"),
                "name": unit.get("name"),
                "qualifiedName": unit.get("qualifiedName"),
                "startLine": unit.get("startLine"),
                "endLine": unit.get("endLine"),
                "language": unit.get("language"),
            }
            source_evidence_id = source_window_by_unit.get(unit_id)
            if source_evidence_id:
                node["sourceEvidenceId"] = source_evidence_id
            nodes[unit_id] = {
                key: value
                for key, value in node.items()
                if value is not None and value != ""
            }

        evidence_id = str(relation.get("evidenceId") or "")
        origin = relation.get("origin") or {}
        compact_origin = (
            {
                key: value
                for key, value in origin.items()
                if key in {"path", "line", "extractor", "plugin", "plugins"}
                and value is not None
                and value != ""
                and value != ()
                and value != []
            }
            if isinstance(origin, Mapping)
            else {}
        )
        edge = {
            "evidenceId": evidence_id,
            "hop": int(relation_hops.get(evidence_id, 0)),
            "kind": relation.get("kind"),
            "source": relation.get("source"),
            "relation": relation.get("relation"),
            "target": relation.get("target"),
            "origin": compact_origin,
            "sourceUnitId": endpoint_ids["sourceUnit"],
            "targetUnitId": endpoint_ids["targetUnit"],
            "relatedPaths": list(dict.fromkeys(
                path
                for path in relation.get("relatedPaths") or ()
                if isinstance(path, str) and path
            )),
        }
        attributes = relation.get("attributes")
        if isinstance(attributes, Mapping) and attributes:
            edge["attributes"] = dict(attributes)
        edges.append({
            key: value
            for key, value in edge.items()
            if value is not None
            and value != ""
            and value != ()
            and value != []
        })
    return list(nodes.values()), edges



def build_review_context(
    reader,
    generation: ProposedTreeGeneration,
    focus_paths: Sequence[str],
    question: str,
    focus_symbols: Sequence[str],
    *,
    max_relations: int,
    max_source_windows: int,
    max_source_characters: int,
) -> dict[str, Any]:
    # Reserve the response budget across anchor, direct, and second-hop
    # evidence. Letting anchor relations consume the whole budget made the
    # nominal second-hop traversal unreachable on relation-dense files.
    anchor_budget = max(1, min(max_relations, max_relations // 3))
    direct_limit = max(anchor_budget, max_relations * 2 // 3)
    anchors = reader.relations_for_paths(
        focus_paths,
        max_relations=anchor_budget,
    )
    changed_units: list[dict[str, Any]] = []
    represented_focus_paths: set[str] = set()
    for anchor in anchors["anchors"]:
        symbols = anchor.get("symbols") or ()
        changed_units.extend(symbols)
        if symbols and isinstance(anchor.get("path"), str):
            represented_focus_paths.add(anchor["path"])

    relation_by_id: dict[str, dict[str, Any]] = {}
    relation_hops: dict[str, int] = {}
    truncated_graph_query = False

    def add_relations(
        relations: Sequence[Mapping[str, Any]],
        *,
        hop: int,
        limit: int,
    ) -> None:
        for relation in relations:
            evidence_id = str(relation.get("evidenceId") or "")
            if not evidence_id:
                continue
            if evidence_id in relation_by_id:
                relation_hops[evidence_id] = min(
                    relation_hops[evidence_id],
                    hop,
                )
                continue
            if len(relation_by_id) >= limit:
                return
            relation_by_id[evidence_id] = dict(relation)
            relation_hops[evidence_id] = hop

    add_relations(
        anchors["relations"],
        hop=0,
        limit=anchor_budget,
    )
    anchor_target_groups = _fair_anchor_target_groups(
        anchors["anchors"],
        max_targets=_MAX_ANCHOR_GRAPH_TARGETS,
    )
    anchor_targets: list[str] = []
    target_depth = 0
    while any(
        target_depth < len(targets)
        for _path, targets in anchor_target_groups
    ):
        anchor_targets.extend(
            targets[target_depth]
            for _path, targets in anchor_target_groups
            if target_depth < len(targets)
        )
        target_depth += 1
    direct_relations, direct_truncated = _select_fair_direct_relations(
        reader,
        anchor_target_groups,
        existing_evidence_ids=set(relation_by_id),
        max_relations=max(0, direct_limit - len(relation_by_id)),
    )
    truncated_graph_query = truncated_graph_query or direct_truncated
    add_relations(
        direct_relations,
        hop=1,
        limit=direct_limit,
    )

    focus_matches: list[dict[str, Any]] = []
    for term in _question_terms(question, focus_symbols):
        for unit in reader.search_units(term, max_results=5):
            unit_id = str(unit.get("unitId") or "")
            if unit_id and all(
                existing.get("unitId") != unit_id for existing in focus_matches
            ):
                focus_matches.append(unit)
        if len(focus_matches) >= 20:
            break

    for unit in focus_matches[:5]:
        if len(relation_by_id) >= direct_limit:
            break
        target = str(
            unit.get("qualifiedName")
            or unit.get("name")
            or unit.get("path")
            or ""
        )
        if not target:
            continue
        remaining = direct_limit - len(relation_by_id)
        graph = reader.query_graph(
            "relations_of",
            target,
            max_results=min(5, remaining),
        )
        truncated_graph_query = truncated_graph_query or bool(
            graph.get("truncated")
        )
        add_relations(
            graph.get("results") or (),
            hop=1,
            limit=direct_limit,
        )

    second_hop_targets: list[str] = []
    for relation in tuple(relation_by_id.values()):
        if "CALL" not in _relation_kind(relation):
            continue
        for endpoint in (relation.get("source"), relation.get("target")):
            value = str(endpoint or "")
            if value and value not in anchor_targets and value not in second_hop_targets:
                second_hop_targets.append(value)
    for target in second_hop_targets[:6]:
        if len(relation_by_id) >= max_relations:
            break
        for pattern in ("callers_of", "callees_of"):
            remaining = max_relations - len(relation_by_id)
            if remaining <= 0:
                break
            graph = reader.query_graph(
                pattern,
                target,
                max_results=min(2, remaining),
            )
            truncated_graph_query = truncated_graph_query or bool(
                graph.get("truncated")
            )
            add_relations(
                graph.get("results") or (),
                hop=2,
                limit=max_relations,
            )

    changed_set = set(generation.changed_paths)
    focus_set = set(focus_paths)
    relations = list(relation_by_id.values())
    call_ids: list[str] = []
    blast_ids: list[str] = []
    test_ids: list[str] = []
    framework_ids: list[str] = []
    for relation in relations:
        evidence_id = relation["evidenceId"]
        kind = _relation_kind(relation)
        relation_paths = _relation_paths(relation)
        if "CALL" in kind:
            call_ids.append(evidence_id)
        if relation_paths - focus_set or (
            relation_paths.intersection(focus_set)
            and "CONTAINS" not in kind
        ):
            blast_ids.append(evidence_id)
        if "TEST" in kind or any(_TEST_PATH.search(path) for path in relation_paths):
            test_ids.append(evidence_id)
        origin = relation.get("origin") or {}
        if isinstance(origin, Mapping) and (
            origin.get("extractor") == "plugin"
            or origin.get("plugin")
            or origin.get("plugins")
        ):
            framework_ids.append(evidence_id)

    endpoint_candidates: list[tuple[dict[str, Any], str]] = []
    for category in (test_ids, blast_ids, call_ids):
        for evidence_id in category:
            relation = relation_by_id[evidence_id]
            for endpoint in (relation.get("sourceUnit"), relation.get("targetUnit")):
                if isinstance(endpoint, dict):
                    endpoint_candidates.append((endpoint, evidence_id))
    for unit in focus_matches:
        endpoint_candidates.append((unit, "focus:" + str(unit.get("unitId"))))

    source_windows: list[dict[str, Any]] = []
    seen_source_units: set[str] = set()
    omitted_source_paths: list[str] = []
    source_evidence_by_unit: dict[str, list[str]] = {}
    for unit, evidence_id in endpoint_candidates:
        unit_id = str(unit.get("unitId") or "")
        if unit_id:
            source_evidence_by_unit.setdefault(unit_id, []).append(evidence_id)
    for unit, evidence_id in endpoint_candidates:
        if len(source_windows) >= max_source_windows:
            path = unit.get("path")
            if isinstance(path, str) and path not in omitted_source_paths:
                omitted_source_paths.append(path)
            continue
        if unit.get("path") in focus_set:
            continue
        detail = _source_detail_for_manifest(reader, unit)
        detail_unit = detail.get("unit") if detail else None
        source_unit_id = (
            str(detail_unit.get("unitId") or "")
            if isinstance(detail_unit, Mapping)
            else ""
        )
        if not source_unit_id or source_unit_id in seen_source_units:
            continue
        used_characters = sum(len(item["content"]) for item in source_windows)
        window = _bounded_source_window(
            detail,
            remaining_characters=max_source_characters - used_characters,
            changed_paths=changed_set,
            relation_evidence_ids=(
                source_evidence_by_unit.get(str(unit.get("unitId") or ""))
                or [evidence_id]
            ),
        )
        if window is None:
            path = unit.get("path")
            if (
                detail is not None
                and isinstance(path, str)
                and path not in omitted_source_paths
            ):
                omitted_source_paths.append(path)
            continue
        source_windows.append(window)
        seen_source_units.add(source_unit_id)

    graph_nodes, compact_relations = _compact_graph_evidence(
        relations,
        relation_hops,
        source_windows,
    )
    frontier_symbols = list(dict.fromkeys(
        str(endpoint or "")
        for relation in relations
        if relation_hops.get(str(relation.get("evidenceId") or ""), 0) >= 2
        for endpoint in (relation.get("source"), relation.get("target"))
        if endpoint
        and str(endpoint) not in anchor_targets
    ))[:8]

    partial_reasons: list[str] = []
    unrepresented_focus_paths = sorted(
        set(focus_paths)
        - set(generation.deleted_paths)
        - represented_focus_paths
    )
    anchor_coverage = anchors.get("coverage") or {}
    if anchor_coverage.get("state") != "complete":
        partial_reasons.append("focus_relation_limit")
    if truncated_graph_query or len(relation_by_id) >= max_relations:
        partial_reasons.append("graph_relation_limit")
    if omitted_source_paths or any(item["truncated"] for item in source_windows):
        partial_reasons.append("source_window_limit")
    if int(generation.receipt.get("skipped_file_count") or 0) > 0:
        partial_reasons.append("index_skipped_files")
    if unrepresented_focus_paths:
        partial_reasons.append("focus_paths_without_structural_units")
    partial_reasons = list(dict.fromkeys(partial_reasons))

    omitted_followups: list[dict[str, Any]] = []
    if "focus_relation_limit" in partial_reasons or "graph_relation_limit" in partial_reasons:
        omitted_followups.append({
            "tool": "exploreReviewContext",
            "reason": "The bounded structural slice omitted additional relations.",
            "arguments": {
                "question": "Narrow the investigation to one named symbol or dependency.",
                "focusSymbols": frontier_symbols[:5] or anchor_targets[:5],
                "maxRelations": max_relations,
            },
        })
    if omitted_source_paths:
        omitted_followups.append({
            "tool": "getReviewFileContent",
            "reason": "Related exact source windows exceeded the response budget.",
            "paths": omitted_source_paths[:10],
        })
    if unrepresented_focus_paths:
        omitted_followups.append({
            "tool": "getReviewFileContent",
            "reason": (
                "These changed paths have exact proposed bytes but no "
                "structural units in the current index representation."
            ),
            "paths": unrepresented_focus_paths[:10],
        })

    snapshot = reader.snapshot()
    snapshot["baseRevision"] = generation.receipt["snapshot_metadata"][
        "base_revision"
    ]
    snapshot["sourceRevision"] = generation.receipt["snapshot_metadata"][
        "source_revision"
    ]
    return {
        "status": "ready",
        "snapshot": snapshot,
        "freshness": {
            "state": "exact_proposed_tree",
            "baseRevision": snapshot["baseRevision"],
            "sourceRevision": snapshot["sourceRevision"],
            "targetSourceTreeSha256": generation.target_source_tree_sha256,
            "overlaySha256": generation.overlay_sha256,
            "proposedSourceTreeSha256": generation.proposed_source_tree_sha256,
        },
        "changed": {
            "paths": list(generation.changed_paths),
            "focusPaths": list(focus_paths),
            "deletedPaths": list(generation.deleted_paths),
            "units": changed_units,
            "focusMatches": focus_matches,
        },
        "evidence": {
            "format": "normalized_nodes_edges",
            "nodes": graph_nodes,
            "relations": compact_relations,
            "frontier": [
                {
                    "symbol": symbol,
                    "next": {
                        "tool": "exploreReviewContext",
                        "arguments": {
                            "question": (
                                "Continue the current review investigation "
                                f"through {symbol}."
                            ),
                            "focusSymbols": [symbol],
                        },
                    },
                }
                for symbol in frontier_symbols
            ],
            "callPaths": call_ids,
            "blastRadius": blast_ids,
            "tests": test_ids,
            "framework": framework_ids,
        },
        "sourceWindows": source_windows,
        "coverage": {
            "treeState": "exact",
            "graphState": "bounded" if partial_reasons else "complete_for_query",
            "partialReasons": partial_reasons,
            "changedFileBodiesComplete": True,
            "focusPathCount": len(focus_paths),
            "changedUnitCount": len(changed_units),
            "relationCount": len(relations),
            "sourceWindowCount": len(source_windows),
            "omittedRelationCount": int(
                anchor_coverage.get("omittedRelations") or 0
            ),
            "omittedSymbolCount": int(
                anchor_coverage.get("omittedSymbols") or 0
            ),
            "indexSkippedFileCount": int(
                generation.receipt.get("skipped_file_count") or 0
            ),
            "unrepresentedFocusPaths": unrepresented_focus_paths,
        },
        "provenance": {
            "targetSourceTreeSha256": generation.target_source_tree_sha256,
            "overlaySha256": generation.overlay_sha256,
            "proposedSourceTreeSha256": generation.proposed_source_tree_sha256,
            "representationIdentity": generation.representation_identity,
            "indexRepresentationFingerprint": generation.receipt.get(
                "index_representation_fingerprint"
            ),
            "collectionTarget": generation.collection_target,
            "cacheHit": generation.cache_hit,
        },
        "omittedFollowups": omitted_followups,
    }

