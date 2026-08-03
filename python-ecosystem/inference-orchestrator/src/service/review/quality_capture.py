"""Opt-in, crash-safe evidence capture for real review-model invocations.

This module is deliberately provider-neutral.  It wraps the BYOK model already
selected for a normal review and records the exact model boundary without
issuing, retrying, or replacing any provider call.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

from model.dtos import ReviewRequestDto

logger = logging.getLogger(__name__)

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")
_IMMUTABLE_GIT_REVISION = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_SHA256_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")
_SECRET_KEYS = {
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "oauth_client",
    "oauth_secret",
    "oauthclient",
    "oauthsecret",
    "password",
    "private_key",
    "privatekey",
    "secret",
    "token",
    "x-api-key",
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=lambda item: repr(item))
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except TypeError:
            return _json_safe(value.model_dump())
    if hasattr(value, "dict"):
        return _json_safe(value.dict())
    return str(value)


def _is_secret_key(key: str) -> bool:
    lowered = key.strip().casefold()
    compact = re.sub(r"[^a-z0-9]", "", lowered)
    return (
        lowered in _SECRET_KEYS
        or compact in _SECRET_KEYS
        or compact.endswith("apikey")
        or compact.endswith("accesstoken")
        or compact.endswith("oauthsecret")
        or compact.endswith("privatekey")
    )


def _redact_secrets(value: Any, *, key: str = "") -> Any:
    if _is_secret_key(key):
        return "[REDACTED]" if value not in (None, "", {}, []) else value
    if isinstance(value, dict):
        return {
            str(child_key): _redact_secrets(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, str) and key.casefold().endswith(("baseurl", "base_url")):
        return _sanitize_url(value)
    return value


def _sanitize_url(value: str) -> str:
    """Keep endpoint identity while removing userinfo, query, and fragment."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[REDACTED_INVALID_URL]"
    if not parsed.scheme or not parsed.netloc:
        return value.split("?", 1)[0].split("#", 1)[0]
    hostname = parsed.hostname or ""
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _message_payload(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return _redact_secrets(_json_safe(message))
    payload: dict[str, Any] = {
        "type": getattr(message, "type", message.__class__.__name__),
        "content": _json_safe(getattr(message, "content", str(message))),
    }
    for attribute in (
        "tool_calls",
        "invalid_tool_calls",
        "usage_metadata",
        "response_metadata",
        "id",
        "name",
    ):
        attribute_value = getattr(message, attribute, None)
        if attribute_value not in (None, "", [], {}):
            payload[attribute] = _json_safe(attribute_value)
    return _redact_secrets(payload)


def _serialize_input(input_data: Any) -> tuple[str, Any]:
    if isinstance(input_data, str):
        return input_data, input_data
    if isinstance(input_data, (list, tuple)):
        messages = [_message_payload(message) for message in input_data]
        rendered = "\n\n".join(
            f"[{message.get('role') or message.get('type') or 'message'}]\n"
            f"{message.get('content', '')}"
            for message in messages
        )
        return rendered, messages
    serialized = _redact_secrets(_json_safe(input_data))
    return json.dumps(serialized, ensure_ascii=False, indent=2), serialized


def _schema_name(schema: Any) -> Optional[str]:
    return getattr(schema, "__name__", None) if schema is not None else None


def _schema_definition(schema: Any) -> Optional[dict[str, Any]]:
    if schema is None:
        return None
    if hasattr(schema, "model_json_schema"):
        return _redact_secrets(_json_safe(schema.model_json_schema()))
    return {"name": _schema_name(schema) or str(schema)}


def _classify_stage(schema: Any, rendered: str, tools: tuple[dict[str, Any], ...]) -> str:
    by_schema = {
        "ReviewPlan": "stage_0",
        "FileReviewBatchOutput": "stage_1",
        "CrossFileAnalysisResult": "stage_2",
        "DeduplicatedIssueList": "deduplication",
        "ReconciliationOutput": "branch_reconciliation",
        "CodeReviewOutput": "branch_analysis",
        "RerankResponse": "rag_reranking",
    }
    schema_stage = by_schema.get(_schema_name(schema))
    if schema_stage:
        return schema_stage
    if tools and "Verification Agent" in rendered:
        return "verification"
    if "JSON repair expert" in rendered:
        return "json_repair"
    if "Produce final PR executive summary" in rendered:
        return "stage_3"
    if "reconciliation" in rendered.casefold():
        return "branch_reconciliation"
    return "unclassified"


def _tool_descriptor(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict):
        return _redact_secrets(_json_safe(tool))
    name = getattr(tool, "name", None) or getattr(
        tool, "__name__", tool.__class__.__name__
    )
    descriptor: dict[str, Any] = {"name": str(name)}
    description = getattr(tool, "description", None) or getattr(tool, "__doc__", None)
    if description:
        descriptor["description"] = str(description).strip()
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        descriptor["inputSchema"] = _json_safe(args_schema.model_json_schema())
    return _redact_secrets(descriptor)


def _request_snapshot(request: ReviewRequestDto) -> dict[str, Any]:
    snapshot = request.model_dump(mode="json", by_alias=True)
    snapshot["promptDryRun"] = False
    return _redact_secrets(snapshot)


@lru_cache(maxsize=1)
def _runtime_plugin_catalog():
    from codecrow_plugins.bootstrap import discover_builtin_plugins

    return discover_builtin_plugins()


def _runtime_plugin_identity(request: ReviewRequestDto) -> dict[str, Any]:
    capabilities = request.projectCapabilities
    if capabilities is None:
        return {
            "status": "fallback-unresolved",
            "repositoryPlugins": [],
            "selectionFingerprint": None,
            "requestDescriptorFingerprint": None,
            "runtimeDescriptorFingerprint": None,
            "implementationFingerprint": None,
            "descriptorMatch": None,
        }

    plugin_ids = tuple(capabilities.repositoryPlugins)
    catalog = _runtime_plugin_catalog()
    resolved = tuple(
        descriptor.id
        for descriptor in catalog.registry.resolve(plugin_ids)
    )
    if resolved != plugin_ids:
        raise ValueError(
            "quality capture requires dependency-stable repository plugin order"
        )
    runtime_descriptor = catalog.registry.fingerprint_for(plugin_ids)
    return {
        "status": "resolved",
        "repositoryPlugins": list(plugin_ids),
        "selectionFingerprint": capabilities.fingerprint,
        "requestDescriptorFingerprint": capabilities.descriptorFingerprint,
        "runtimeDescriptorFingerprint": runtime_descriptor,
        "implementationFingerprint": (
            catalog.implementation_fingerprint(plugin_ids)
        ),
        "descriptorMatch": (
            capabilities.descriptorFingerprint == runtime_descriptor
        ),
    }


def _review_runtime_fingerprint(
    plugin_identity: dict[str, Any],
) -> str:
    """Identify prompt/orchestration source plus selected plugin implementation."""
    review_root = Path(__file__).resolve().parent
    source_root = review_root.parents[1]
    candidates = list(review_root.rglob("*.py"))
    prompt_root = source_root / "utils" / "prompts"
    if prompt_root.is_dir():
        candidates.extend(prompt_root.rglob("*.py"))
    candidates.extend(
        path
        for path in (
            source_root / "utils" / "diff_processor.py",
            source_root / "model" / "dtos.py",
            source_root / "model" / "plugins.py",
        )
        if path.is_file()
    )
    projection = [
        {
            "path": path.relative_to(source_root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(
            set(candidates),
            key=lambda item: item.relative_to(source_root).as_posix(),
        )
        if "__pycache__" not in path.parts
    ]
    return _digest({
        "reviewSource": projection,
        "pluginImplementationFingerprint": plugin_identity.get(
            "implementationFingerprint"
        ),
    })


def _credential_values(value: Any, *, key: str = "") -> set[str]:
    collected: set[str] = set()
    if _is_secret_key(key):
        if isinstance(value, str) and value:
            collected.add(value)
        elif isinstance(value, dict):
            for child in value.values():
                if isinstance(child, str) and child:
                    collected.add(child)
        return collected
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            collected.update(_credential_values(child_value, key=str(child_key)))
    elif isinstance(value, list):
        for child in value:
            collected.update(_credential_values(child))
    elif isinstance(value, str) and key.casefold().endswith(("baseurl", "base_url")):
        collected.add(value)
    return collected


def _selected_project_ids() -> set[int]:
    raw = os.environ.get("REVIEW_QUALITY_CAPTURE_PROJECT_IDS", "")
    selected: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            selected.add(int(item))
        except ValueError as exception:
            raise ValueError(
                "REVIEW_QUALITY_CAPTURE_PROJECT_IDS must contain numeric project IDs"
            ) from exception
    return selected


def _provider_reported_models(value: Any) -> list[str]:
    """Return model IDs explicitly reported inside a provider callback result."""

    observed: set[str] = set()

    def inspect_metadata(current: Any) -> None:
        if not isinstance(current, dict):
            return
        for key, child in current.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in {"model", "model_id", "model_name"}:
                if isinstance(child, str) and child.strip():
                    observed.add(child.strip())
            elif normalized in {
                "llm_output",
                "metadata",
                "response_metadata",
            }:
                inspect_metadata(child)

    if isinstance(value, dict):
        inspect_metadata(value.get("llm_output"))
        generations = value.get("generations")
        if isinstance(generations, list):
            for group in generations:
                if not isinstance(group, list):
                    continue
                for generation in group:
                    if not isinstance(generation, dict):
                        continue
                    inspect_metadata(generation.get("generation_info"))
                    message = generation.get("message")
                    if isinstance(message, dict):
                        inspect_metadata(message.get("response_metadata"))
    return sorted(observed)


class _ProviderBoundaryCallback:
    """Collect the underlying provider result before structured parsing."""

    def __init__(self):
        self.events: list[dict[str, Any]] = []
        self.raise_error = False
        self.run_inline = False
        self.ignore_llm = False
        self.ignore_chat_model = False
        self.ignore_chain = True
        self.ignore_agent = True
        self.ignore_retriever = True
        self.ignore_retry = True
        self.ignore_custom_event = True

    def on_llm_start(self, *_: Any, **__: Any) -> None:
        return None

    def on_chat_model_start(self, *_: Any, **__: Any) -> None:
        return None

    def on_llm_new_token(self, *_: Any, **__: Any) -> None:
        return None

    def on_llm_end(self, response: Any, **_: Any) -> None:
        safe_response = _json_safe(response)
        self.events.append({
            "status": "completed",
            "providerReportedModels": _provider_reported_models(safe_response),
            "response": safe_response,
        })

    def on_llm_error(self, error: BaseException, **_: Any) -> None:
        self.events.append({
            "status": "failed",
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
        })


def _invoke_kwargs_with_callback(
    invoke_kwargs: dict[str, Any],
    callback: Any,
) -> dict[str, Any]:
    updated = dict(invoke_kwargs)
    config = dict(updated.get("config") or {})
    callbacks = config.get("callbacks")
    if callbacks is None:
        config["callbacks"] = [callback]
    elif isinstance(callbacks, (list, tuple)):
        config["callbacks"] = [*callbacks, callback]
    else:
        # A pre-built callback manager is opaque. Preserve it rather than
        # replacing model-call behavior merely to collect telemetry.
        return updated
    updated["config"] = config
    return updated


def quality_capture_enabled_for(request: ReviewRequestDto) -> bool:
    if not _env_bool("REVIEW_QUALITY_CAPTURE_ENABLED"):
        return False
    selected = _selected_project_ids()
    if not selected:
        raise ValueError(
            "REVIEW_QUALITY_CAPTURE_ENABLED requires a non-empty "
            "REVIEW_QUALITY_CAPTURE_PROJECT_IDS allowlist"
        )
    return request.projectId in selected


def review_response_indicates_failure(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    if response.get("error"):
        return True
    result = response.get("result")
    return isinstance(result, dict) and bool(result.get("error"))


def _terminal_pipeline_evidence(event: Any) -> Optional[dict[str, Any]]:
    """Validate and project the host-owned terminal review ledger.

    The event deliberately contains no prompts or source. Keeping this compact
    projection in the capture makes coverage independently verifiable without
    relying on operator-entered hunk totals.
    """
    if not isinstance(event, dict) or event.get("state") != "review_evidence_completed":
        return None

    hunk_coverage = event.get("hunkCoverage")
    if not isinstance(hunk_coverage, dict):
        raise ValueError("terminal pipeline evidence has no hunk coverage")
    hunk_states = (
        "ingested",
        "planned",
        "reviewed",
        "validated",
        "completed",
        "excluded",
    )
    normalized_hunks: dict[str, int] = {}
    for state in hunk_states:
        value = hunk_coverage.get(state)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"terminal pipeline evidence has invalid {state!r} hunk count"
            )
        normalized_hunks[state] = value
    active_states = {
        state: normalized_hunks[state]
        for state in ("ingested", "planned", "reviewed", "validated")
        if normalized_hunks[state]
    }
    if active_states:
        raise ValueError(
            "terminal pipeline evidence contains non-terminal hunk states: "
            + ", ".join(
                f"{state}={count}" for state, count in active_states.items()
            )
        )

    review_units = event.get("reviewUnits")
    if not isinstance(review_units, dict):
        raise ValueError("terminal pipeline evidence has no review-unit ledger")
    normalized_units: dict[str, int] = {}
    for field in ("registered", "completed"):
        value = review_units.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"terminal pipeline evidence has invalid reviewUnits.{field}"
            )
        normalized_units[field] = value
    if normalized_units["registered"] != normalized_units["completed"]:
        raise ValueError(
            "terminal pipeline evidence contains incomplete review units"
        )

    candidates = event.get("candidates")
    if not isinstance(candidates, dict):
        raise ValueError("terminal pipeline evidence has no candidate ledger")
    normalized_candidate_counts: dict[str, int] = {}
    for field in ("generated", "published", "rejected"):
        value = candidates.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"terminal pipeline evidence has invalid candidates.{field}"
            )
        normalized_candidate_counts[field] = value
    if (
        normalized_candidate_counts["published"]
        + normalized_candidate_counts["rejected"]
        != normalized_candidate_counts["generated"]
    ):
        raise ValueError(
            "terminal pipeline evidence contains non-terminal candidates"
        )
    candidate_records = candidates.get("records")
    if (
        not isinstance(candidate_records, list)
        or len(candidate_records) != normalized_candidate_counts["generated"]
    ):
        raise ValueError(
            "terminal pipeline evidence candidate records do not match generated count"
        )
    normalized_records: list[dict[str, Any]] = []
    observed_ids: set[str] = set()
    computed_rejections: dict[str, int] = {}
    for record in candidate_records:
        if not isinstance(record, dict):
            raise ValueError("terminal pipeline evidence has invalid candidate record")
        candidate_id = record.get("candidateId")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id.startswith("sha256:")
            or len(candidate_id) != 71
            or any(character not in "0123456789abcdef" for character in candidate_id[7:])
            or candidate_id in observed_ids
        ):
            raise ValueError(
                "terminal pipeline evidence has invalid candidate identity"
            )
        observed_ids.add(candidate_id)
        stage = record.get("stage")
        terminal_state = record.get("terminalState")
        if stage not in {"stage_1", "stage_2"}:
            raise ValueError("terminal pipeline evidence has invalid candidate stage")
        if terminal_state not in {"published", "rejected"}:
            raise ValueError(
                "terminal pipeline evidence has invalid candidate terminal state"
            )
        generation_prompt_digest = record.get("generationPromptDigest")
        if (
            not isinstance(generation_prompt_digest, str)
            or not generation_prompt_digest.startswith("sha256:")
            or len(generation_prompt_digest) != 71
            or any(
                character not in "0123456789abcdef"
                for character in generation_prompt_digest[7:]
            )
        ):
            raise ValueError(
                "terminal pipeline evidence has invalid candidate "
                "generation prompt digest"
            )

        normalized_lists: dict[str, list[str]] = {}
        for field in (
            "reviewUnitIds",
            "promptHunkIds",
            "anchorHunkIds",
            "evidenceRefs",
            "visibleEvidenceIds",
        ):
            values = record.get(field)
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) or not value for value in values)
                or values != sorted(set(values))
            ):
                raise ValueError(
                    f"terminal pipeline evidence has invalid candidate {field}"
                )
            normalized_lists[field] = list(values)

        fact_digests = record.get("visibleEvidenceFactDigests")
        if (
            not isinstance(fact_digests, dict)
            or set(fact_digests) != set(
                normalized_lists["visibleEvidenceIds"]
            )
        ):
            raise ValueError(
                "terminal pipeline evidence has invalid candidate "
                "visibleEvidenceFactDigests"
            )
        normalized_fact_digests: dict[str, list[str]] = {}
        for evidence_id in sorted(fact_digests):
            digests = fact_digests[evidence_id]
            if (
                not isinstance(digests, list)
                or digests != sorted(set(digests))
                or any(
                    not isinstance(digest, str)
                    or not digest.startswith("sha256:")
                    or len(digest) != 71
                    or any(
                        character not in "0123456789abcdef"
                        for character in digest[7:]
                    )
                    for digest in digests
                )
            ):
                raise ValueError(
                    "terminal pipeline evidence has invalid candidate "
                    "visible evidence fact digest"
                )
            normalized_fact_digests[evidence_id] = list(digests)

        if (
            terminal_state == "published"
            and not set(normalized_lists["evidenceRefs"]).issubset(
                normalized_lists["visibleEvidenceIds"]
            )
        ):
            raise ValueError(
                "published terminal candidate cites evidence outside its "
                "generation prompt"
            )

        rejection = record.get("rejection")
        normalized_rejection = None
        if terminal_state == "published":
            if rejection is not None:
                raise ValueError(
                    "published terminal candidate contains a rejection reason"
                )
        else:
            if not isinstance(rejection, dict):
                raise ValueError(
                    "rejected terminal candidate has no rejection reason"
                )
            gate = rejection.get("gate")
            code = rejection.get("code")
            if (
                not isinstance(gate, str)
                or not gate
                or not isinstance(code, str)
                or not code
            ):
                raise ValueError(
                    "rejected terminal candidate has invalid rejection reason"
                )
            normalized_rejection = {"gate": gate, "code": code}
            rejection_key = f"{gate}:{code}"
            computed_rejections[rejection_key] = (
                computed_rejections.get(rejection_key, 0) + 1
            )
        normalized_records.append({
            "candidateId": candidate_id,
            "stage": stage,
            "generationPromptDigest": generation_prompt_digest,
            **normalized_lists,
            "visibleEvidenceFactDigests": normalized_fact_digests,
            "terminalState": terminal_state,
            "rejection": normalized_rejection,
        })
    if normalized_records != sorted(
        normalized_records,
        key=lambda record: record["candidateId"],
    ):
        raise ValueError(
            "terminal pipeline evidence candidate records are not deterministic"
        )
    rejection_counts = candidates.get("rejectionCounts")
    if (
        not isinstance(rejection_counts, dict)
        or rejection_counts != dict(sorted(computed_rejections.items()))
    ):
        raise ValueError(
            "terminal pipeline evidence rejection counts do not match records"
        )

    raw_hunk_receipts = event.get("hunkReceipts")
    if (
        not isinstance(raw_hunk_receipts, list)
        or len(raw_hunk_receipts) != normalized_hunks["completed"]
    ):
        raise ValueError(
            "terminal pipeline evidence hunk receipts do not match completed hunks"
        )
    records_by_id = {
        record["candidateId"]: record for record in normalized_records
    }
    normalized_hunk_receipts: list[dict[str, Any]] = []
    observed_hunk_ids: set[str] = set()
    for receipt in raw_hunk_receipts:
        if not isinstance(receipt, dict):
            raise ValueError("terminal pipeline evidence has invalid hunk receipt")
        hunk_id = receipt.get("hunkId")
        path = receipt.get("path")
        if (
            not isinstance(hunk_id, str)
            or not hunk_id
            or hunk_id in observed_hunk_ids
            or not isinstance(path, str)
            or not path
        ):
            raise ValueError("terminal pipeline evidence has invalid hunk receipt")
        observed_hunk_ids.add(hunk_id)
        normalized_candidate_lists: dict[str, list[str]] = {}
        for field in (
            "promptCandidateIds",
            "anchoredCandidateIds",
            "publishedCandidateIds",
            "rejectedCandidateIds",
        ):
            values = receipt.get(field)
            if (
                not isinstance(values, list)
                or values != sorted(set(values))
                or any(value not in records_by_id for value in values)
            ):
                raise ValueError(
                    "terminal pipeline evidence has invalid hunk candidate receipt"
                )
            normalized_candidate_lists[field] = list(values)

        expected_prompt = sorted(
            candidate_id
            for candidate_id, record in records_by_id.items()
            if hunk_id in record["promptHunkIds"]
        )
        expected_anchored = sorted(
            candidate_id
            for candidate_id, record in records_by_id.items()
            if hunk_id in record["anchorHunkIds"]
        )
        expected_published = sorted(
            candidate_id
            for candidate_id in expected_anchored
            if records_by_id[candidate_id]["terminalState"] == "published"
        )
        expected_rejected = sorted(
            candidate_id
            for candidate_id in expected_anchored
            if records_by_id[candidate_id]["terminalState"] == "rejected"
        )
        if (
            normalized_candidate_lists["promptCandidateIds"] != expected_prompt
            or normalized_candidate_lists["anchoredCandidateIds"]
            != expected_anchored
            or normalized_candidate_lists["publishedCandidateIds"]
            != expected_published
            or normalized_candidate_lists["rejectedCandidateIds"]
            != expected_rejected
        ):
            raise ValueError(
                "terminal pipeline evidence hunk receipt conflicts with "
                "candidate records"
            )
        expected_outcome = (
            "published"
            if expected_published
            else "rejected"
            if expected_rejected
            else "no_anchored_candidate"
        )
        if receipt.get("outcome") != expected_outcome:
            raise ValueError(
                "terminal pipeline evidence has invalid hunk receipt outcome"
            )
        normalized_hunk_receipts.append({
            "hunkId": hunk_id,
            "path": path,
            **normalized_candidate_lists,
            "outcome": expected_outcome,
        })
    if normalized_hunk_receipts != sorted(
        normalized_hunk_receipts,
        key=lambda receipt: receipt["hunkId"],
    ):
        raise ValueError(
            "terminal pipeline evidence hunk receipts are not deterministic"
        )

    retrieval = event.get("retrieval")
    if not isinstance(retrieval, dict):
        raise ValueError("terminal pipeline evidence has no retrieval ledger")
    deterministic_states = retrieval.get("deterministicStates")
    if not isinstance(deterministic_states, list) or any(
        not isinstance(state, str) or not state
        for state in deterministic_states
    ):
        raise ValueError(
            "terminal pipeline evidence has invalid deterministic retrieval states"
        )
    if (
        normalized_units["registered"] > 0
        and not deterministic_states
    ):
        raise ValueError(
            "terminal pipeline evidence has no deterministic retrieval state"
        )
    incomplete_retrieval = sorted({
        state for state in deterministic_states if state != "complete"
    })
    if incomplete_retrieval:
        raise ValueError(
            "terminal pipeline evidence contains incomplete deterministic "
            "retrieval states: " + ", ".join(incomplete_retrieval)
        )
    semantic_failures = retrieval.get("semanticFailures")
    exact_evidence_ids = retrieval.get("exactEvidenceIds")
    for field, value in (
        ("semanticFailures", semantic_failures),
        ("exactEvidenceIds", exact_evidence_ids),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"terminal pipeline evidence has invalid retrieval.{field}"
            )
    if semantic_failures:
        raise ValueError(
            "terminal pipeline evidence contains semantic retrieval failures"
        )
    semantic_disabled = retrieval.get("semanticDisabled")
    if not isinstance(semantic_disabled, bool):
        raise ValueError(
            "terminal pipeline evidence has invalid retrieval.semanticDisabled"
        )

    revision_binding = event.get("revisionBinding")
    if not isinstance(revision_binding, dict):
        raise ValueError("terminal pipeline evidence has no revision binding")
    pr_indexed = revision_binding.get("prIndexed")
    pull_request_id = revision_binding.get("pullRequestId")
    target_branch = revision_binding.get("targetBranch")
    source_revision = revision_binding.get("sourceRevision")
    base_revision = revision_binding.get("baseRevision")
    base_manifest = revision_binding.get("baseGenerationManifestSha256")
    pr_fingerprint = revision_binding.get("prGenerationFingerprint")
    overlay_manifest = revision_binding.get(
        "prOverlayGenerationManifestSha256"
    )
    base_plugin_fingerprint = revision_binding.get(
        "basePluginFingerprint"
    )
    base_plugin_descriptor_fingerprint = revision_binding.get(
        "basePluginDescriptorFingerprint"
    )
    base_plugin_implementation_fingerprint = revision_binding.get(
        "basePluginImplementationFingerprint"
    )
    base_index_representation_fingerprint = revision_binding.get(
        "baseIndexRepresentationFingerprint"
    )
    if not isinstance(pr_indexed, bool):
        raise ValueError(
            "terminal pipeline evidence has invalid revisionBinding.prIndexed"
        )
    if (
        pull_request_id is not None
        and (
            not isinstance(pull_request_id, int)
            or isinstance(pull_request_id, bool)
            or pull_request_id < 1
        )
    ):
        raise ValueError(
            "terminal pipeline evidence has invalid revisionBinding.pullRequestId"
        )
    if not isinstance(target_branch, str) or not target_branch.strip():
        raise ValueError(
            "terminal pipeline evidence has invalid revisionBinding.targetBranch"
        )
    if (
        not isinstance(source_revision, str)
        or _IMMUTABLE_GIT_REVISION.fullmatch(source_revision) is None
    ):
        raise ValueError(
            "terminal pipeline evidence has invalid revisionBinding.sourceRevision"
        )
    if (
        base_revision is not None
        and (
            not isinstance(base_revision, str)
            or _IMMUTABLE_GIT_REVISION.fullmatch(base_revision) is None
        )
    ):
        raise ValueError(
            "terminal pipeline evidence has invalid revisionBinding.baseRevision"
        )
    if (
        base_manifest is not None
        and (
            not isinstance(base_manifest, str)
            or _SHA256_HEX.fullmatch(base_manifest) is None
        )
    ):
        raise ValueError(
            "terminal pipeline evidence has invalid "
            "revisionBinding.baseGenerationManifestSha256"
        )
    if (
        pr_fingerprint is not None
        and (
            not isinstance(pr_fingerprint, str)
            or _SHA256_FINGERPRINT.fullmatch(pr_fingerprint) is None
        )
    ):
        raise ValueError(
            "terminal pipeline evidence has invalid "
            "revisionBinding.prGenerationFingerprint"
        )
    if (
        overlay_manifest is not None
        and (
            not isinstance(overlay_manifest, str)
            or _SHA256_HEX.fullmatch(overlay_manifest) is None
        )
    ):
        raise ValueError(
            "terminal pipeline evidence has invalid "
            "revisionBinding.prOverlayGenerationManifestSha256"
        )
    for field, value in (
        ("basePluginFingerprint", base_plugin_fingerprint),
        (
            "basePluginDescriptorFingerprint",
            base_plugin_descriptor_fingerprint,
        ),
        (
            "basePluginImplementationFingerprint",
            base_plugin_implementation_fingerprint,
        ),
        (
            "baseIndexRepresentationFingerprint",
            base_index_representation_fingerprint,
        ),
    ):
        if value is not None and (
            not isinstance(value, str)
            or _SHA256_FINGERPRINT.fullmatch(value) is None
        ):
            raise ValueError(
                "terminal pipeline evidence has invalid "
                f"revisionBinding.{field}"
            )
    if pr_indexed and (
        pull_request_id is None
        or base_revision is None
        or base_manifest is None
        or pr_fingerprint is None
        or overlay_manifest is None
        or base_plugin_fingerprint is None
        or base_plugin_descriptor_fingerprint is None
        or base_plugin_implementation_fingerprint is None
        or base_index_representation_fingerprint is None
    ):
        raise ValueError(
            "terminal pipeline evidence has incomplete indexed revision binding"
        )
    if not pr_indexed and (
        base_manifest is not None
        or pr_fingerprint is not None
        or overlay_manifest is not None
        or base_plugin_fingerprint is not None
        or base_plugin_descriptor_fingerprint is not None
        or base_plugin_implementation_fingerprint is not None
        or base_index_representation_fingerprint is not None
    ):
        raise ValueError(
            "terminal pipeline evidence has receipts for an unindexed PR overlay"
        )

    return {
        "state": "review_evidence_completed",
        "hunkCoverage": normalized_hunks,
        "reviewUnits": normalized_units,
        "candidates": {
            **normalized_candidate_counts,
            "rejectionCounts": dict(sorted(computed_rejections.items())),
            "records": normalized_records,
        },
        "hunkReceipts": normalized_hunk_receipts,
        "retrieval": {
            "deterministicStates": list(deterministic_states),
            "semanticFailures": semantic_failures,
            "semanticDisabled": semantic_disabled,
            "exactEvidenceIds": exact_evidence_ids,
        },
        "revisionBinding": {
            "prIndexed": pr_indexed,
            "pullRequestId": pull_request_id,
            "targetBranch": target_branch,
            "sourceRevision": source_revision,
            "baseRevision": base_revision,
            "baseGenerationManifestSha256": base_manifest,
            "prGenerationFingerprint": pr_fingerprint,
            "prOverlayGenerationManifestSha256": overlay_manifest,
            "basePluginFingerprint": base_plugin_fingerprint,
            "basePluginDescriptorFingerprint": (
                base_plugin_descriptor_fingerprint
            ),
            "basePluginImplementationFingerprint": (
                base_plugin_implementation_fingerprint
            ),
            "baseIndexRepresentationFingerprint": (
                base_index_representation_fingerprint
            ),
        },
    }


class ReviewQualityCaptureSession:
    """Crash-safe artifact shared by all bound views of one review model."""

    def __init__(self, request: ReviewRequestDto):
        raw_snapshot = request.model_dump(mode="json", by_alias=True)
        self._credential_values = sorted(
            _credential_values(raw_snapshot),
            key=len,
            reverse=True,
        )
        snapshot = _request_snapshot(request)
        plugin_identity = _runtime_plugin_identity(request)
        review_runtime_fingerprint = _review_runtime_fingerprint(
            plugin_identity
        )
        output_dir = Path(os.environ.get(
            "REVIEW_QUALITY_CAPTURE_OUTPUT_DIR",
            "/app/logs/review-quality-captures",
        ))
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            max_files = int(os.environ.get("REVIEW_QUALITY_CAPTURE_MAX_FILES", "20"))
        except ValueError as exception:
            raise ValueError(
                "REVIEW_QUALITY_CAPTURE_MAX_FILES must be an integer"
            ) from exception
        if max_files < 1:
            raise ValueError("REVIEW_QUALITY_CAPTURE_MAX_FILES must be positive")

        pr_part = request.pullRequestId if request.pullRequestId is not None else "branch"
        basename = _SAFE_FILENAME.sub(
            "-",
            f"project-{request.projectId}-review-{pr_part}-{uuid.uuid4().hex}",
        )
        self.path = output_dir / f"{basename}.json"
        self._lock = asyncio.Lock()
        self._artifact: dict[str, Any] = {
            "kind": "review-quality-candidate-capture",
            "status": "recording",
            "createdAt": _utc_now(),
            "completedAt": None,
            "provider": request.aiProvider,
            "model": request.aiModel,
            "pluginIdentity": plugin_identity,
            "reviewRuntimeFingerprint": review_runtime_fingerprint,
            "modeIdentity": _digest({
                "pluginIdentity": plugin_identity,
                "reviewRuntimeFingerprint": review_runtime_fingerprint,
            }),
            "requestDigest": _digest(snapshot),
            "request": snapshot,
            "modelBoundaryInvocations": 0,
            "providerCalls": 0,
            "calls": [],
            "pipelineEvidenceStatus": "pending",
            "pipelineEvidence": None,
            "pipelineEvidenceDigest": None,
            "result": None,
            "resultDigest": None,
            "captureDigest": None,
            "warnings": [
                (
                    "This opt-in artifact contains proprietary source, prompts, "
                    "retrieved context, and model outputs. Credentials are redacted."
                ),
                (
                    "The capture observes the existing BYOK model boundary and does "
                    "not add, retry, or replace provider calls."
                ),
            ],
        }
        self._pipeline_evidence: Optional[dict[str, Any]] = None
        self._pipeline_evidence_error: Optional[str] = None
        self._max_files = max_files
        self._write()
        logger.warning(
            "review_quality_capture_started project=%s pr=%s artifact=%s",
            request.projectId,
            request.pullRequestId,
            self.path,
        )

    def _scrub_known_credentials(self, value: Any) -> Any:
        if isinstance(value, str):
            scrubbed = value
            for credential in self._credential_values:
                scrubbed = scrubbed.replace(credential, "[REDACTED]")
            scrubbed = re.sub(
                r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+",
                r"\1 [REDACTED]",
                scrubbed,
            )
            return scrubbed
        if isinstance(value, dict):
            return {
                str(key): self._scrub_known_credentials(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._scrub_known_credentials(item) for item in value]
        return value

    @property
    def container_path(self) -> str:
        return str(self.path)

    def observe_pipeline_event(self, event: Any) -> None:
        """Observe terminal host evidence without changing review callbacks."""
        try:
            evidence = _terminal_pipeline_evidence(event)
        except ValueError as exception:
            self._pipeline_evidence_error = str(exception)
            return
        if evidence is None:
            return
        if (
            self._pipeline_evidence is not None
            and self._pipeline_evidence != evidence
        ):
            self._pipeline_evidence_error = (
                "conflicting terminal pipeline evidence events"
            )
            return
        self._pipeline_evidence = evidence

    def wrap_event_callback(
        self,
        callback: Optional[Callable[[dict[str, Any]], None]],
    ) -> Callable[[dict[str, Any]], None]:
        """Capture host evidence while preserving the caller's event stream."""
        def wrapped(event: dict[str, Any]) -> None:
            self.observe_pipeline_event(event)
            if callback is not None:
                callback(event)

        return wrapped

    def _write(self) -> None:
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        payload = json.dumps(
            self._artifact,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                os.chmod(temporary, 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _prune(self) -> None:
        candidates = sorted(
            self.path.parent.glob("project-*-review-*.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        terminal: list[Path] = []
        for candidate in candidates:
            try:
                status = json.loads(
                    candidate.read_text(encoding="utf-8")
                ).get("status")
            except (OSError, ValueError):
                continue
            if status in {"completed", "failed"}:
                terminal.append(candidate)
        for stale in terminal[self._max_files:]:
            stale.unlink(missing_ok=True)

    def receipt(self) -> dict[str, Any]:
        """Return a source-free receipt for the completed capture artifact."""

        if (
            self._artifact.get("status") not in {"completed", "failed"}
            or not self._artifact.get("captureDigest")
        ):
            raise ValueError("quality capture receipt requires a terminal artifact")
        call_receipts = []
        all_reported_models: set[str] = set()
        model_evidence_complete = True
        for call in self._artifact["calls"]:
            reported = sorted({
                model
                for event in call.get("providerEvents") or []
                if isinstance(event, dict)
                for model in event.get("providerReportedModels") or []
                if isinstance(model, str) and model
            })
            all_reported_models.update(reported)
            if call.get("status") == "completed" and not reported:
                model_evidence_complete = False
            call_receipts.append({
                "sequence": call.get("sequence"),
                "stage": call.get("stage"),
                "status": call.get("status"),
                "providerCallCount": call.get("providerCallCount"),
                "providerReportedModels": reported,
                "promptDigest": call.get("promptDigest"),
                "responseDigest": call.get("responseDigest"),
            })
        receipt = {
            "kind": "review-quality-capture-receipt",
            "status": self._artifact["status"],
            "artifactContainerPath": self.container_path,
            "captureDigest": self._artifact["captureDigest"],
            "provider": self._artifact["provider"],
            "requestedModel": self._artifact["model"],
            "providerReportedModels": sorted(all_reported_models),
            "providerModelEvidenceComplete": model_evidence_complete,
            "modelBoundaryInvocations": self._artifact[
                "modelBoundaryInvocations"
            ],
            "providerCalls": self._artifact["providerCalls"],
            "calls": call_receipts,
        }
        receipt["receiptDigest"] = _digest(receipt)
        return receipt

    async def invoke(
        self,
        delegate: Any,
        input_data: Any,
        *,
        schema: Any,
        include_raw: bool,
        bindings: dict[str, Any],
        tools: tuple[dict[str, Any], ...],
        invoke_kwargs: dict[str, Any],
    ) -> Any:
        rendered, serialized = _serialize_input(input_data)
        rendered = self._scrub_known_credentials(rendered)
        serialized = self._scrub_known_credentials(serialized)
        schema_definition = _schema_definition(schema)
        declarations: dict[str, Any] = {}
        if schema_definition is not None:
            declarations["responseSchema"] = schema_definition
        if tools:
            declarations["tools"] = list(tools)
        declaration_chars = len(_canonical_bytes(declarations)) if declarations else 0
        started = time.monotonic()
        async with self._lock:
            sequence = len(self._artifact["calls"]) + 1
            record: dict[str, Any] = {
                "sequence": sequence,
                "stage": _classify_stage(schema, rendered, tools),
                "status": "in_progress",
                "startedAt": _utc_now(),
                "completedAt": None,
                "durationMs": None,
                "callType": (
                    "tool-enabled"
                    if tools
                    else "structured"
                    if schema is not None
                    else "raw"
                ),
                "responseSchema": _schema_name(schema),
                "responseSchemaDefinition": schema_definition,
                "includeRawResponse": include_raw,
                "modelBindings": _redact_secrets(_json_safe(bindings)),
                "tools": list(tools),
                "input": serialized,
                "renderedPrompt": rendered,
                "promptDigest": _digest({
                    "input": serialized,
                    "schema": schema_definition,
                    "tools": list(tools),
                    "bindings": _redact_secrets(_json_safe(bindings)),
                }),
                "renderedPromptCharacterCount": len(rendered),
                "estimatedInputTokens": math.ceil(
                    (len(rendered.encode("utf-8")) + declaration_chars) / 4
                ),
                "response": None,
                "responseDigest": None,
                "providerEvents": [],
                "providerCallCount": None,
                "providerCallCountSource": None,
                "error": None,
            }
            self._artifact["calls"].append(record)
            self._write()

        provider_callback = _ProviderBoundaryCallback()
        provider_kwargs = _invoke_kwargs_with_callback(
            invoke_kwargs,
            provider_callback,
        )
        try:
            response = await delegate.ainvoke(input_data, **provider_kwargs)
        except BaseException as exception:
            async with self._lock:
                record["status"] = "failed"
                record["completedAt"] = _utc_now()
                record["durationMs"] = round((time.monotonic() - started) * 1000, 3)
                record["error"] = {
                    "type": type(exception).__name__,
                    "message": self._scrub_known_credentials(str(exception)),
                }
                record["providerEvents"] = self._scrub_known_credentials(
                    _redact_secrets(_json_safe(provider_callback.events))
                )
                record["providerCallCount"] = max(
                    1, len(provider_callback.events)
                )
                record["providerCallCountSource"] = (
                    "callback"
                    if provider_callback.events
                    else "model-boundary-fallback"
                )
                self._artifact["modelBoundaryInvocations"] += 1
                self._artifact["providerCalls"] += record["providerCallCount"]
                self._write()
            raise

        serialized_response = self._scrub_known_credentials(
            _redact_secrets(_json_safe(response))
        )
        async with self._lock:
            record["status"] = "completed"
            record["completedAt"] = _utc_now()
            record["durationMs"] = round((time.monotonic() - started) * 1000, 3)
            record["response"] = serialized_response
            record["responseDigest"] = _digest(serialized_response)
            record["providerEvents"] = self._scrub_known_credentials(
                _redact_secrets(_json_safe(provider_callback.events))
            )
            record["providerCallCount"] = max(1, len(provider_callback.events))
            record["providerCallCountSource"] = (
                "callback"
                if provider_callback.events
                else "model-boundary-fallback"
            )
            self._artifact["modelBoundaryInvocations"] += 1
            self._artifact["providerCalls"] += record["providerCallCount"]
            self._write()
        return response

    async def complete(
        self,
        response: Optional[dict[str, Any]],
        error: Optional[BaseException] = None,
        *,
        failed: bool = False,
    ) -> None:
        async with self._lock:
            result = self._scrub_known_credentials(
                _redact_secrets(_json_safe(response))
            )
            self._artifact["completedAt"] = _utc_now()
            self._artifact["status"] = (
                "failed"
                if error is not None or failed
                else "completed"
            )
            self._artifact["result"] = result
            self._artifact["resultDigest"] = _digest(result)
            if self._pipeline_evidence_error is not None:
                self._artifact["pipelineEvidenceStatus"] = "invalid"
                self._artifact["pipelineEvidenceError"] = (
                    self._pipeline_evidence_error
                )
            elif self._pipeline_evidence is None:
                self._artifact["pipelineEvidenceStatus"] = "missing"
            else:
                self._artifact["pipelineEvidenceStatus"] = "complete"
                self._artifact["pipelineEvidence"] = self._pipeline_evidence
                self._artifact["pipelineEvidenceDigest"] = _digest(
                    self._pipeline_evidence
                )
            if error is not None:
                self._artifact["terminalError"] = {
                    "type": type(error).__name__,
                    "message": self._scrub_known_credentials(str(error)),
                }
            digest_payload = dict(self._artifact)
            digest_payload["captureDigest"] = None
            self._artifact["captureDigest"] = _digest(digest_payload)
            self._write()
            self._prune()
        logger.warning(
            "review_quality_capture_%s artifact=%s provider_calls=%s digest=%s",
            self._artifact["status"],
            self.path,
            self._artifact["providerCalls"],
            self._artifact["captureDigest"],
        )


class ReviewQualityCaptureLLM:
    """Transparent model view that records each actual invocation."""

    def __init__(
        self,
        delegate: Any,
        session: ReviewQualityCaptureSession,
        *,
        schema: Any = None,
        include_raw: bool = False,
        bindings: Optional[dict[str, Any]] = None,
        tools: Iterable[dict[str, Any]] = (),
    ):
        self._delegate = delegate
        self._session = session
        self._schema = schema
        self._include_raw = include_raw
        self._bindings = dict(bindings or {})
        self._tools = tuple(tools)

    @property
    def __codecrow_delegate__(self) -> Any:
        return self._delegate

    @property
    def model_kwargs(self) -> dict[str, Any]:
        return dict(getattr(self._delegate, "model_kwargs", {}) or {})

    @property
    def max_tokens(self) -> Any:
        return getattr(self._delegate, "max_tokens", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def _clone(self, delegate: Any, **updates: Any) -> "ReviewQualityCaptureLLM":
        return ReviewQualityCaptureLLM(
            delegate,
            self._session,
            schema=updates.get("schema", self._schema),
            include_raw=updates.get("include_raw", self._include_raw),
            bindings=updates.get("bindings", self._bindings),
            tools=updates.get("tools", self._tools),
        )

    def model_copy(
        self,
        update: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "ReviewQualityCaptureLLM":
        updates = dict(update or {})
        delegate = self._delegate.model_copy(update=updates, **kwargs)
        bindings = dict(self._bindings)
        bindings.update(updates)
        return self._clone(delegate, bindings=bindings)

    def copy(
        self,
        update: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "ReviewQualityCaptureLLM":
        updates = dict(update or {})
        if hasattr(self._delegate, "copy"):
            delegate = self._delegate.copy(update=updates, **kwargs)
        else:
            delegate = self._delegate.model_copy(update=updates, **kwargs)
        bindings = dict(self._bindings)
        bindings.update(updates)
        return self._clone(delegate, bindings=bindings)

    def bind(self, **kwargs: Any) -> "ReviewQualityCaptureLLM":
        delegate = self._delegate.bind(**kwargs)
        bindings = dict(self._bindings)
        bindings.update(kwargs)
        return self._clone(delegate, bindings=bindings)

    def with_structured_output(
        self,
        schema: Any,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> "ReviewQualityCaptureLLM":
        delegate = self._delegate.with_structured_output(
            schema,
            include_raw=include_raw,
            **kwargs,
        )
        bindings = dict(self._bindings)
        bindings["structured_output"] = {
            "include_raw": include_raw,
            "options": dict(kwargs),
        }
        return self._clone(
            delegate,
            schema=schema,
            include_raw=include_raw,
            bindings=bindings,
        )

    def bind_tools(
        self,
        tools: Iterable[Any],
        **kwargs: Any,
    ) -> "ReviewQualityCaptureLLM":
        materialized = tuple(tools)
        delegate = self._delegate.bind_tools(materialized, **kwargs)
        bindings = dict(self._bindings)
        bindings["tool_binding"] = {
            "options": dict(kwargs),
        }
        return self._clone(
            delegate,
            bindings=bindings,
            tools=tuple(_tool_descriptor(tool) for tool in materialized),
        )

    async def ainvoke(self, input_data: Any, **kwargs: Any) -> Any:
        return await self._session.invoke(
            self._delegate,
            input_data,
            schema=self._schema,
            include_raw=self._include_raw,
            bindings=self._bindings,
            tools=self._tools,
            invoke_kwargs=kwargs,
        )


def create_quality_capture_session(
    request: ReviewRequestDto,
) -> Optional[ReviewQualityCaptureSession]:
    if not quality_capture_enabled_for(request):
        return None
    return ReviewQualityCaptureSession(request)


def wrap_quality_capture_llm(
    llm: Any,
    session: Optional[ReviewQualityCaptureSession],
) -> Any:
    if session is None:
        return llm
    return ReviewQualityCaptureLLM(llm, session)
