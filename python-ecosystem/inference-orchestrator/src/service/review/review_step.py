"""Work-scoped native evidence tools and one structured assessment contract.

The provider sees ordinary source tools. The host normalizes their requests,
commits independent outcomes and defers source-dependent outcomes until their
reads are assessed. Provider prose and reasoning are never source evidence.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Collection, Literal, Mapping, Sequence

STEP_TOOL = "assessReviewWork"
_SCOPE_FIELDS = frozenset({"workIds", "missingFact"})


def _assessment_schema() -> dict[str, Any]:
    strings = {"type": "array", "items": {"type": "string"}}
    issue = {"type": "object", "properties": {
        "title": {"type": "string"}, "reason": {"type": "string"},
        "file": {"type": "string"}, "line": {"type": "integer"},
        "partId": {"type": "string"}, "severity": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "category": {"type": "string"}, "suggestedFixDescription": {"type": "string"},
        "evidenceIds": strings,
    }}
    assessment = {"type": "object", "required": ["workId", "verdict", "reason", "evidenceIds"], "properties": {
        "workId": {"type": "string"},
        "verdict": {"type": "string", "enum": ["confirmed", "refuted", "uncertain", "duplicate", "needs_evidence"]},
        "reason": {"type": "string", "description": "The source-based conclusion, or for needs_evidence the concrete missing fact that must be resolved."}, "evidenceIds": strings,
        "duplicateOf": {"type": "string"}, "issue": {"anyOf": [issue, {"type": "null"}]},
    }}
    finding = deepcopy(issue)
    finding["required"] = ["file", "line", "title", "reason", "evidenceIds"]
    return {"name": STEP_TOOL,
            "description": "Assess supplied work from the current source. Settle supported outcomes independently. Use needs_evidence with the concrete missing fact in reason when source is insufficient; the host will provide a source investigation step. This tool must assess at least one work item.",
            "inputSchema": {"type": "object", "required": ["assessments"], "properties": {
                "assessments": {"type": "array", "minItems": 1, "items": assessment},
                "findings": {"type": "array", "items": finding},
            }}}


def review_tool_schemas(
    tools: Sequence[Mapping[str, Any]], *, phase: Literal["assessment", "evidence"] = "evidence",
) -> list[dict[str, Any]]:
    """Bind the operations for one step without competing with adjudication.

    Assessment steps expose only the outcome operation. Evidence steps retain
    scoped reads and the same outcome operation so an already-known answer can
    settle without an unnecessary read. The controller returns to assessment
    after observations; there is no retrieval quota or source clipping.
    """
    if phase == "assessment":
        return [_assessment_schema()]
    if phase != "evidence":
        raise ValueError(f"Unknown verification phase: {phase}")
    schemas = []
    for tool in tools:
        schema = deepcopy(dict(tool))
        arguments = schema["inputSchema"]
        properties = arguments.setdefault("properties", {})
        if _SCOPE_FIELDS.intersection(properties):
            raise ValueError("Evidence tool arguments conflict with review work scope")
        properties.update({
            "workIds": {"type": "array", "minItems": 1, "items": {"type": "string"},
                        "description": "Supplied pending work IDs whose outcome depends on this source."},
            "missingFact": {"type": "string", "minLength": 1,
                            "description": "The concrete missing fact this read will establish for those work IDs."},
        })
        arguments["required"] = list(dict.fromkeys([*arguments.get("required", []), "workIds", "missingFact"]))
        schema["description"] = (schema.get("description") or "") + " Use only for named pending review work."
        schemas.append(schema)
    schemas.append(_assessment_schema())
    return schemas


def submitted_steps(turn: Any, *, tool_names: Collection[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize native/JSON calls while preserving complete sibling outcomes.

    The internal step shape is deliberately independent of provider protocols.
    It is also accepted from non-native JSON models without a second paid call.
    """
    steps: list[dict[str, Any]] = []
    errors: list[str] = []
    for call in turn.tool_calls:
        if call.get("error"):
            errors.append(call["error"])
            continue
        arguments = call.get("arguments")
        if not isinstance(arguments, Mapping):
            errors.append("Review tool arguments must be a JSON object")
            continue
        name = call.get("name")
        if name == STEP_TOOL:
            assessments = arguments.get("assessments")
            if not isinstance(assessments, list) or not assessments:
                errors.append(f"{STEP_TOOL} needs at least one assessment of supplied work; submit needs_evidence with the concrete missing fact when source is insufficient.")
                continue
            steps.append({"assessments": assessments, "findings": arguments.get("findings", []), "evidenceRequests": []})
        elif name in tool_names:
            ids, fact = arguments.get("workIds"), arguments.get("missingFact")
            if (not isinstance(ids, list) or not ids or any(not isinstance(key, str) or not key.strip() for key in ids)
                    or not isinstance(fact, str) or not fact.strip()):
                errors.append(f"{name} needs nonempty workIds and a concrete missingFact; this call was not executed.")
                continue
            steps.append({"assessments": [], "findings": [], "evidenceRequests": [{
                "workIds": ids, "missingFact": fact,
                "calls": [{"name": name, "arguments": {key: value for key, value in arguments.items() if key not in _SCOPE_FIELDS}}],
            }]})
        else:
            errors.append(f"Unknown review tool {name!r}; use the supplied read tools or {STEP_TOOL}.")
    # JSON fallback is an adapter concern, not another published tool contract.
    if isinstance(turn.output, dict):
        if any(key in turn.output for key in ("assessments", "evidenceRequests", "findings")):
            if any(turn.output.get(key) for key in ("assessments", "evidenceRequests", "findings")):
                normalized = {key: turn.output.get(key, [])
                              for key in ("assessments", "evidenceRequests", "findings")}
                if normalized not in steps:
                    steps.append(normalized)
            else:
                errors.append("Empty review output resolves no work; assess a supplied work ID or request its missing source.")
        elif not turn.tool_calls:
            errors.append(f"Call the supplied source tools or {STEP_TOOL} using toolCalls:[{{name,arguments}}].")
    if turn.output_error:
        errors.append("The response was not a complete structured review step. Submit the existing outcomes; do not restart investigation to repair formatting.")
    if not steps and not errors:
        errors.append(f"Assess the current work with {STEP_TOOL} or call a source tool for its concrete missing fact.")
    return steps, list(dict.fromkeys(errors))
