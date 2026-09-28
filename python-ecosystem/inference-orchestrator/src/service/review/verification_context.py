"""Render a case's exact evidence without replaying overlapping source bodies.

The ledger remains authoritative for citations. This projection only replaces
already-present source with references; it neither summarizes nor clips source.
Diff-before text is never treated as target HEAD source.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from service.review.navigation_context import expand_navigation_result
from service.review.verification_state import fingerprint

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _proposed_diff_lines(diff: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    line: int | None = None
    last_proposed = False
    for raw in diff.splitlines(keepends=True):
        header = _HUNK.match(raw)
        if header:
            line = int(header.group(1))
            last_proposed = False
        elif raw.startswith("\\ No newline at end of file"):
            if result and last_proposed:
                number, text = result[-1]
                result[-1] = number, text.removesuffix("\n").removesuffix("\r")
        elif line is not None and raw[:1] in {" ", "+"}:
            result.append((line, raw[1:]))
            line += 1
            last_proposed = True
        elif raw.startswith(("diff ", "--- ", "+++ ")):
            line = None
            last_proposed = False
        else:
            last_proposed = False
    return result


def _groups(lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for number, text in lines:
        if groups and groups[-1]["endLine"] + 1 == number:
            groups[-1]["endLine"] = number
            groups[-1]["content"] += text
        else:
            groups.append({"startLine": number, "endLine": number, "content": text})
    return groups


def _references(values: list[tuple[str, str, str, int]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for evidence_id, path, side, line in values:
        if refs and all(refs[-1][key] == value for key, value in
                        (("evidenceId", evidence_id), ("path", path), ("side", side))) and refs[-1]["endLine"] + 1 == line:
            refs[-1]["endLine"] = line
        else:
            refs.append({"evidenceId": evidence_id, "path": path, "side": side,
                         "startLine": line, "endLine": line})
    return refs


class VerificationContext:
    """A fresh, lossless source projection of the current case ledger."""

    def __init__(self, state: Any):
        self.state = state

    def fingerprint(self) -> str:
        """Identify observed facts, never retrieval wording or receipt identity.

        Negative searches remain in the prompt as scoped observations, but a
        different absent query does not manufacture new repository facts. Graph
        navigation annotations likewise do not fund more evidence acquisition.
        """
        facts: set[str] = set()

        def add(value: Any) -> None:
            facts.add(fingerprint(value))

        def source_line(path: Any, side: Any, number: Any, text: Any) -> None:
            if isinstance(path, str) and isinstance(number, int) and isinstance(text, str):
                # Grep reports line text without its terminator. Exact bytes,
                # including CRLF/unterminated lines, are preserved by render().
                add(("source", path, side, number, text.rstrip("\r\n")))
                add(("path", path, side))

        for record in self.state.evidence.values():
            kind, result = record["kind"], record["result"]
            path, side = result.get("path"), result.get("side", "proposed")
            if kind in {"readReviewFile", "getStructuralUnit"}:
                if result.get("status") == "ready" and isinstance(result.get("content"), str):
                    try:
                        start = int(result.get("startLine") or 1)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    for number, text in enumerate(result["content"].splitlines(keepends=True), start):
                        source_line(path, side, number, text)
                    if isinstance(path, str):
                        add(("path", path, side))
                elif result.get("status") in {"deleted", "binary"} and isinstance(path, str):
                    add(("source-status", path, side, result["status"]))
                continue
            if kind in {"diff", "getReviewDiff"}:
                parts = [result] if kind == "diff" else result.get("parts", [])
                for part in parts if isinstance(parts, list) else []:
                    if not isinstance(part, Mapping) or not isinstance(part.get("diff"), str):
                        continue
                    add(("diff", part.get("path"), part["diff"]))
                    for number, text in _proposed_diff_lines(part["diff"]):
                        source_line(part.get("path"), "proposed", number, text)
                continue
            if kind == "grepReviewCode":
                for item in result.get("results", []):
                    if isinstance(item, Mapping):
                        for match in item.get("matches", []):
                            if isinstance(match, Mapping):
                                source_line(item.get("path"), side, match.get("line"), match.get("text"))
                continue
            if kind == "findReviewFiles":
                for path in result.get("paths", result.get("files", [])):
                    if isinstance(path, str):
                        add(("path", path, side))
                    elif isinstance(path, Mapping) and isinstance(path.get("path"), str):
                        add(("path", path["path"], side))
                continue
            expanded = expand_navigation_result(dict(result))
            for field in ("results", "resolvedUnits", "candidates", "units", "nodes", "edges", "relationships", "frontier", "roots"):
                records = expanded.get(field)
                for item in records if isinstance(records, list) else []:
                    if isinstance(item, str):
                        add(("graph-identity", item))
                    elif isinstance(item, Mapping):
                        if isinstance(item.get("unitId"), str):
                            add(("graph-identity", item["unitId"]))
                            item = {key: value for key, value in item.items() if key not in {
                                "depth", "score", "impactScore", "relevanceScore", "rank", "distance",
                                "connectionEvidenceId", "sourceEvidenceId", "reason", "continuation",
                            }}
                        add(("graph-record", item))
        return fingerprint(sorted(facts))

    def render(self) -> dict[str, Any]:
        source: dict[tuple[str, str, int, str], str] = {}
        line_source: dict[tuple[str, str, int, str], str] = {}
        diff_ids: dict[tuple[str, str], str] = {}
        rendered: dict[str, dict[str, Any]] = {}
        evidence = self.state.evidence

        # Complete selected hunks retain every before/after byte and their
        # boundaries. Only proposed lines have the same identity as local reads.
        for key, record in evidence.items():
            kind, result = record["kind"], record["result"]
            parts = ([result] if kind == "diff" else result.get("parts", [])
                     if kind == "getReviewDiff" else [])
            if not isinstance(parts, list):
                continue
            projected = []
            for part in parts:
                if not isinstance(part, Mapping):
                    projected.append(part)
                    continue
                part_id = str(part.get("partId") or part.get("id") or "")
                diff_key = (part_id, str(part.get("diff") or ""))
                if part_id and diff_key in diff_ids:
                    projected.append({**{name: value for name, value in part.items() if name != "diff"},
                                      "sourceReference": {"evidenceId": diff_ids[diff_key], "partId": part_id}})
                    continue
                projected.append(dict(part))
                if part_id:
                    diff_ids[diff_key] = key
                if isinstance(part.get("diff"), str) and isinstance(part.get("path"), str):
                    for number, text in _proposed_diff_lines(part["diff"]):
                        source.setdefault((part["path"], "proposed", number, text), key)
                        line_source.setdefault((part["path"], "proposed", number, text.rstrip("\r\n")), key)
            if kind == "diff" and projected:
                rendered[key] = {"id": key, **record, "result": projected[0]}
            elif kind == "getReviewDiff":
                rendered[key] = {"id": key, **record, "result": {**result, "parts": projected}}

        # Reads are resolved before grep so a matching line can point at its
        # complete source definition, including the original line ending.
        for key, record in evidence.items():
            kind, result = record["kind"], record["result"]
            if kind not in {"readReviewFile", "getStructuralUnit"} or result.get("status") != "ready" or not isinstance(result.get("content"), str):
                continue
            path, side = result.get("path"), result.get("side", "proposed")
            try:
                start = int(result.get("startLine") or 1)
            except (TypeError, ValueError, OverflowError):
                continue
            if not isinstance(path, str):
                continue
            missing, references = [], []
            for number, text in enumerate(result["content"].splitlines(keepends=True), start):
                identity = (path, side, number, text)
                owner = source.get(identity)
                if owner is not None:
                    references.append((owner, path, side, number))
                else:
                    source[identity] = key
                    line_source.setdefault((path, side, number, text.rstrip("\r\n")), key)
                    missing.append((number, text))
            if references:
                compact = {name: value for name, value in result.items() if name != "content"}
                compact["sourceSegments"] = _groups(missing)
                compact["sourceReferences"] = _references(references)
                rendered[key] = {"id": key, **record, "result": compact}

        for key, record in evidence.items():
            kind, result = record["kind"], record["result"]
            if kind != "grepReviewCode":
                continue
            projected = []
            side = result.get("side", "proposed")
            for item in result.get("results", []):
                if not isinstance(item, Mapping):
                    projected.append(item)
                    continue
                matches = []
                for match in item.get("matches", []):
                    if not isinstance(match, Mapping):
                        matches.append(match)
                        continue
                    path, number, text = item.get("path"), match.get("line"), match.get("text")
                    owner = line_source.get((path, side, number, text)) if isinstance(text, str) else None
                    if owner is not None and owner != key:
                        matches.append({name: value for name, value in match.items() if name != "text"}
                                       | {"sourceReference": {"evidenceId": owner, "path": path, "side": side, "line": number}})
                    else:
                        matches.append(dict(match))
                        if isinstance(text, str) and isinstance(path, str) and isinstance(number, int):
                            line_source[(path, side, number, text)] = key
                projected.append({**item, "matches": matches})
            rendered[key] = {"id": key, **record, "result": {**result, "results": projected}}
        return {"evidence": [rendered.get(key, {"id": key, **record}) for key, record in evidence.items()]}
