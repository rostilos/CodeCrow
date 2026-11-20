"""Bound model observations without changing the sealed graph's exact facts."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def relation_field_page(reader, evidence_id: str, field: str, offset: int,
                        max_characters: int) -> dict[str, Any]:
    """Page exact relation values inside the already tenant-bound reader."""
    if field not in {"source", "target", "attributes"}:
        raise ValueError("Relation field must be source, target or attributes")
    relation = reader.get_relation(evidence_id)
    if relation is None:
        return {"status": "unavailable", "operation": "query_review_graph",
                "snapshot": reader.snapshot(), "evidenceId": evidence_id,
                "reason": "relation_not_found"}
    value = relation.get(field, "")
    content = value if isinstance(value, str) else _encoded(value)
    offset = min(max(0, offset), len(content))
    end = min(len(content), offset + max(1, min(max_characters, 60000)))
    return {
        "status": "ready", "operation": "query_review_graph",
        "snapshot": reader.snapshot(), "evidenceId": evidence_id,
        "field": field, "encoding": "text" if isinstance(value, str) else "json",
        "content": content[offset:end],
        "contentWindow": {"offset": offset, "endOffset": end,
                          "totalCharacters": len(content), "truncated": end < len(content),
                          "nextOffset": end if end < len(content) else None},
    }


def bound_graph_response(value: dict[str, Any] | None, token_budget: int | None):
    """Fit complete JSON, including labels/metadata and escaping, to a budget.

    None preserves the existing full-fact API. Budgets use the existing four
    serialized characters per estimated token convention, additionally bounding
    UTF-8 bytes so non-ASCII labels cannot multiply transport volume.
    """
    if value is None or token_budget is None:
        return value
    response = deepcopy(value)
    if "freshness" in response:
        # The composite FastAPI response model includes this default too.
        response.setdefault("continuations", [])
    budget = max(512, min(int(token_budget), 16000))
    ceiling = budget * 4
    coverage = response.setdefault("coverage", {})
    coverage.update(tokenBudget=budget, serializedCharacters=0, serializedBytes=0,
                    modelObservationBytes=0, estimatedTokens=0)
    omitted: dict[str, int] = {}
    references: list[dict[str, Any]] = []

    def mark(path: str) -> None:
        omitted[path] = omitted.get(path, 0) + 1
        coverage["state"] = "bounded"
        coverage["truncated"] = True
        reasons = coverage.setdefault("partialReasons", [])
        if "token_budget" not in reasons:
            reasons.append("token_budget")
        if "graphState" in coverage:
            coverage["graphState"] = "bounded"

    def project(item: Any, path: str = "") -> None:
        if isinstance(item, list):
            for index, child in enumerate(item):
                project(child, f"{path}/{index}")
        elif isinstance(item, dict):
            evidence_id = item.get("evidenceId", "")
            for field in ("source", "target", "attributes"):
                data = item.get(field)
                if (str(evidence_id).startswith("relation:") and data is not None
                        and len(_encoded(data).encode()) > min(1200, ceiling // 2)):
                    text = data if isinstance(data, str) else _encoded(data)
                    reference = {
                        "tool": "queryCodeGraph", "reason": "token_budget",
                        "arguments": {"evidenceId": evidence_id, "field": field,
                                      "offset": 0, "maxCharacters": 12000},
                        "totalCharacters": len(text),
                    }
                    item.pop(field)
                    item.setdefault("fieldReferences", {})[field] = reference
                    if reference not in references and len(references) < 4:
                        references.append(reference)
                    mark(f"{path}/{field}")
            for key, child in list(item.items()):
                # Source and exact field pages are reduced separately with
                # coordinates. Long navigation echoes have no evidence authority.
                if isinstance(child, str) and key != "content" and len(child.encode()) > 1200:
                    item.pop(key)
                    mark(f"{path}/{key}")
                else:
                    project(child, f"{path}/{key}")

    project(response)
    if references:
        response["continuations"] = references + list(response.get("continuations") or [])[:4]

    # Every measurement includes the final counts and their omission fields.
    # Otherwise adding those fields after fitting can trigger an unnecessary
    # emergency fallback and discard source that still fits.
    original_counts = {
        key: coverage[key]
        for key in (
            'returnedNodes', 'returnedRelations', 'returnedResults',
            'returnedRoots', 'sourceWindowCount',
        )
        if key in coverage
    }
    original_omissions = {
        key: value for key, value in coverage.items()
        if key.startswith('omitted')
    }

    def refresh_coverage() -> None:
        # Refresh returned counts instead of claiming the original cardinalities.
        for name, key in (("returnedNodes", "nodes"), ("returnedRelations", "edges"),
                          ("returnedResults", "results"), ("returnedRoots", "roots"),
                          ("sourceWindowCount", "sourceWindows")):
            if name in coverage and key in response:
                old = int(original_counts.get(name, coverage[name]))
                coverage[name] = len(response[key])
                if old > coverage[name]:
                    omitted_key = "omitted" + name.removeprefix("returned")
                    coverage[omitted_key] = int(original_omissions.get(omitted_key, 0)) + old - coverage[name]
        evidence = response.get("evidence", {})
        if "relationCount" in coverage and isinstance(evidence, dict):
            coverage["relationCount"] = len(evidence.get("relations", []))
        changed = response.get("changed", {})
        if "changedUnitCount" in coverage and isinstance(changed, dict):
            coverage["changedUnitCount"] = len(changed.get("units", []))
        source = coverage.get("source")
        if isinstance(source, dict):
            source["returnedSourceCharacters"] = sum(len(x.get("content", "")) for x in response.get("sourceWindows", []))
            if omitted.get("source_content"):
                source.update(state="bounded", truncated=True)
        if omitted.get("results") and "cursor" in response:
            response["nextCursor"] = int(response["cursor"]) + len(response["results"])

    def measure() -> int:
        refresh_coverage()
        coverage["budgetOmissions"] = dict(list(omitted.items())[:20])
        for _ in range(8):
            encoded = _encoded(response)
            # FastMCP emits two-space JSON in its text block. Budget that
            # representation as well as the compact HTTP/structured payload.
            wire = json.dumps(response, ensure_ascii=False, indent=2, default=str).encode()
            sizes = len(encoded), len(encoded.encode()), len(wire)
            old = (coverage["serializedCharacters"], coverage["serializedBytes"],
                   coverage["modelObservationBytes"])
            (coverage["serializedCharacters"], coverage["serializedBytes"],
             coverage["modelObservationBytes"]) = sizes
            coverage["estimatedTokens"] = (sizes[2] + 3) // 4
            if old == sizes:
                break
        return coverage["modelObservationBytes"]

    def update_candidate_page() -> None:
        if response.get("status") != "ambiguous" or "candidates" not in response:
            return
        # Candidate cursors index candidates, never the empty relation results.
        # Stop before an undisplayable identity rather than skipping its fact.
        items = response["candidates"]
        for index, item in enumerate(items):
            if not item.get("unitId"):
                del items[index:]
                break
        count = len(items)
        cursor = int(value.get("cursor") or 0)
        total = int(value.get("candidateCount", len(value.get("candidates", []))))
        more = cursor + count < total
        response.update(candidateCount=total, candidateResultCount=count,
                        candidatesTruncated=more, truncated=more, cursor=cursor,
                        nextCursor=cursor + count if count and more else None)
        if count:
            response["hint"] = ("Retry with a candidate unitId; use nextCursor for more candidates."
                                if more else "Retry with a candidate unitId.")
        elif value.get("candidates"):
            response["reason"] = "candidate_budget_exhausted"
            response["hint"] = "Use an exact source path or qualified symbol to narrow the target."

    # Reduce optional navigation/cardinality before exact source. These lists
    # are already semantically selected; their omitted tails stay observable.
    arrays = [
        (response, key) for key in ("candidates", "omittedFollowups", "frontier", "connections",
                                   "impactedFiles", "resolvedUnits", "roots", "results", "edges", "nodes")
    ]
    for key in ("evidence", "changed"):
        owner = response.get(key)
        if isinstance(owner, dict):
            arrays.extend((owner, name) for name in
                          ("frontier", "focusMatches", "units", "relations", "nodes", "callPaths", "blastRadius", "tests", "framework"))
    for owner, key in arrays:
        items = owner.get(key)
        if not isinstance(items, list):
            continue
        minimum = 1 if key in {"candidates", "results", "edges", "relations", "nodes", "roots"} else 0
        while len(items) > minimum and measure() > ceiling:
            items.pop()
            mark(key)

    update_candidate_page()

    if isinstance(response.get("impactScores"), dict) and "nodes" in response:
        visible = {node.get("unitId") for node in response["nodes"]}
        response["impactScores"] = {key: score for key, score in response["impactScores"].items()
                                    if key in visible}

    source_refs = {}
    for window in response.get("sourceWindows") or []:
        if window.get("unitId") and (window.get("truncated") or measure() > ceiling):
            reference = {"tool": "getStructuralUnit", "reason": "source_window_limit",
                         "arguments": {"unitId": window["unitId"], "offset": 0, "maxCharacters": 12000}}
            source_refs[id(window)] = reference
            response.setdefault("continuations", []).append(reference)

    # Coordinate-preserving source/field paging: shrink actual content, never
    # JSON text or whole serialized messages (which would break their meaning).
    windows = list(response.get("sourceWindows") or [])
    if isinstance(response.get("unit"), dict):
        windows.append(response["unit"])
    if "contentWindow" in response:
        windows.append(response)
    for window in reversed(windows):
        original = window.get("content")
        if not isinstance(original, str) or measure() <= ceiling:
            continue
        mark("source_content")
        low, high = 0, len(original)
        while low < high:
            middle = (low + high + 1) // 2
            window["content"] = original[:middle]
            if measure() <= ceiling - 128:
                low = middle
            else:
                high = middle - 1
        selected = original[:low]
        page = window.get("contentWindow")
        if isinstance(page, dict):
            end = int(page.get("offset", 0)) + len(selected)
            page.update(endOffset=end, truncated=end < page["totalCharacters"],
                        nextOffset=end if end < page["totalCharacters"] else None)
        else:
            if "\n" in selected:
                selected = selected.rsplit("\n", 1)[0] + "\n"
            window["truncated"] = True
            start = int(window.get("startLine") or 1)
            window["endLine"] = start + max(0, len(selected.splitlines()) - 1)
        window["content"] = selected

    for window in response.get("sourceWindows") or []:
        if id(window) in source_refs:
            source_refs[id(window)]["arguments"]["offset"] = len(window.get("content", ""))

    # If optional metadata alone dominates the envelope, retain the smallest
    # useful exact page/identity. Never advance a cursor past undisclosed facts.
    if measure() > ceiling:
        original = response
        response = {key: original[key] for key in ("status", "operation") if key in original}
        response["snapshot"] = {key: val for key, val in original.get("snapshot", {}).items()
                                if key in {"kind", "revision", "generationManifestSha256"}
                                and isinstance(val, str) and len(val.encode()) <= 128}
        coverage = {"state": "bounded", "truncated": True, "partialReasons": ["token_budget"],
                    "tokenBudget": budget, "serializedCharacters": 0, "serializedBytes": 0,
                    "modelObservationBytes": 0, "estimatedTokens": 0}
        response["coverage"] = coverage
        omitted = {"response_envelope": 1}
        # Preserve required keys of the existing composite-context contract.
        for key in ("freshness", "changed", "evidence", "provenance"):
            if key in original:
                response[key] = {}
        for key in ("sourceWindows", "omittedFollowups"):
            if key in original:
                response[key] = []
        refs = [ref for ref in original.get("continuations", [])
                if isinstance(ref, dict) and len(_encoded(ref).encode()) < 600]
        if refs:
            response["continuations"] = refs[:1]
        if "results" in original:
            response["cursor"] = original.get("cursor", 0)
            response["results"] = []
            for item in original["results"][:1]:
                response["results"].append({key: item[key] for key in ("evidenceId", "unitId")
                                            if key in item})
            count = len(response["results"])
            response["nextCursor"] = (int(response["cursor"]) + count
                                      if len(original["results"]) > count or original.get("nextCursor") is not None
                                      else None)
            coverage["returnedResults"] = count
        if original.get("status") == "ambiguous" and "candidates" in value:
            response["candidates"] = []
            for item in value["candidates"][:1]:
                response["candidates"].append({key: item[key] for key in
                    ("unitId", "path", "kind", "startLine", "endLine")
                    if key in item and len(_encoded(item[key]).encode()) <= 300})
            update_candidate_page()
        # An exact field/unit read must still return a progressing page even if
        # optional attributes were enormous. Source windows use unit paging.
        page_source = value.get("unit") if isinstance(value.get("unit"), dict) else value
        page = page_source.get("contentWindow")
        if isinstance(page, dict):
            destination = response.setdefault("unit", {}) if page_source is not value else response
            destination.update({key: page_source[key] for key in ("unitId", "evidenceId", "field", "encoding")
                                if key in page_source})
            destination["contentWindow"] = dict(page)
            content = page_source.get("content", "")
            low, high = 0, len(content)
            while low < high:
                middle = (low + high + 1) // 2
                destination["content"] = content[:middle]
                end = int(page.get("offset", 0)) + middle
                destination["contentWindow"].update(endOffset=end, truncated=end < page["totalCharacters"],
                    nextOffset=end if end < page["totalCharacters"] else None)
                if measure() <= ceiling:
                    low = middle
                else:
                    high = middle - 1
            destination["content"] = content[:low]
            end = int(page.get("offset", 0)) + low
            destination["contentWindow"].update(endOffset=end, truncated=end < page["totalCharacters"],
                nextOffset=end if end < page["totalCharacters"] else None)
        elif value.get("sourceWindows"):
            window = value["sourceWindows"][0]
            if window.get("unitId"):
                response["sourceWindows"] = [{key: window[key] for key in
                    ("unitId", "path", "startLine") if key in window and len(_encoded(window[key]).encode()) < 300}]
                response["sourceWindows"][0].update(content=window.get("content", "")[:80], truncated=True)
                response["continuations"] = [{"tool": "getStructuralUnit", "reason": "token_budget",
                    "arguments": {"unitId": window["unitId"], "offset": 0, "maxCharacters": 12000}}]
        # All retained metadata above is bounded. Tiny budgets may omit snapshot
        # echoes; request-bound tenant/revision checks still govern follow-ups.
        if measure() > ceiling:
            response.pop("snapshot", None)
        if measure() > ceiling and response.get("sourceWindows"):
            response["sourceWindows"] = []
        if measure() > ceiling:
            response.pop("continuations", None)
        if measure() > ceiling and response.get("candidates"):
            # The exact ID is sufficient for a retry when path labels do not fit.
            response["candidates"] = [{"unitId": response["candidates"][0]["unitId"]}]
        if measure() > ceiling and response.get("candidates"):
            response["candidates"] = []
            update_candidate_page()
        measure()
    return response
