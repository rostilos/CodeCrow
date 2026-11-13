from __future__ import annotations

import itertools
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Callable, Iterable, Mapping, Sequence

from .corpus import validate_corpus
from .judge import (
    JUDGMENT_KIND,
    MATCH_SYSTEM,
    NOVEL_SYSTEM,
    PROMPT_VERSION,
    _extract_json,
    _candidate_evidence,
    _gold_prompt,
    _majority_match,
    _majority_novel,
    _maximum_assignment,
    _novel_prompt,
    _path_evidence,
    _right_added_lines,
    _validate_local_snapshot,
    _validate_match_response,
    _validate_novel,
)
from .replay import (
    validate_replay_attestation,
    validate_replay_attestation_freshness,
    validate_replay_lock,
)
from .protocol import validate_protocol_bundle
from .postfix import (
    POST_FIX_ATTESTATION_KIND,
    POST_FIX_CONTROL_KIND,
    POST_FIX_CONTROL_SET_KIND,
    POST_FIX_JUDGMENT_KIND,
    POST_FIX_LOCK_KIND,
    POST_FIX_PLAN_KIND,
    POST_FIX_RUN_KIND,
)
from .runner import (
    MAX_PAPER_ATTESTATION_AGE_SECONDS,
    RUN_KIND,
    _analysis_result,
    _findings,
    _redacted_request,
    _request_payload,
    _request_control_digest,
    _validate_attempt_ledger,
    _validate_bound_model_call_evidence,
    _validate_index_selection_policy,
)
from .repository_evidence import validate_repository_evidence
from .util import (
    is_local_git_repository,
    read_json,
    sha256_json,
    sha256_text,
    write_json,
)


METRICS_KIND = "codecrow-magento2-benchmark-metrics"
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
SHA256_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
MIN_PUBLICATION_BOOTSTRAP_ITERATIONS = 10_000
PUBLICATION_PROTOCOL_GATE_FAILURES = (
    "preregistration_artifact_not_bound",
    "sealed_partition_access_and_unseal_evidence_not_bound",
    "judge_calibration_or_preregistered_human_audit_not_bound",
    "post_fix_control_not_bound",
    "post_fix_judge_human_audit_not_bound",
    "reproducibility_package_not_bound",
)
POST_FIX_ARTIFACT_KINDS = {
    POST_FIX_PLAN_KIND,
    POST_FIX_LOCK_KIND,
    POST_FIX_ATTESTATION_KIND,
    POST_FIX_RUN_KIND,
    POST_FIX_JUDGMENT_KIND,
    POST_FIX_CONTROL_KIND,
    POST_FIX_CONTROL_SET_KIND,
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _utc_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be UTC ISO-8601")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp must be UTC ISO-8601")
    return parsed


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return round(float(numerator) / float(denominator), 6)


def _post_fix_artifacts(
    supplied_paths: Sequence[Path] | None,
) -> tuple[dict[str, list[Mapping[str, Any]]], dict[int, Path]]:
    by_kind: dict[str, list[Mapping[str, Any]]] = {
        kind: [] for kind in POST_FIX_ARTIFACT_KINDS
    }
    roots: dict[int, Path] = {}
    seen: set[Path] = set()
    for supplied in supplied_paths or []:
        if supplied.is_symlink():
            raise ValueError("post-fix artifact paths must not be symlinks")
        candidates = (
            [supplied]
            if supplied.is_file()
            else sorted(supplied.rglob("*.json"))
            if supplied.is_dir()
            else []
        )
        if not candidates:
            raise ValueError(f"post-fix artifact path is empty: {supplied}")
        for candidate in candidates:
            if candidate.is_symlink():
                raise ValueError(
                    "post-fix artifact trees must not contain symlinks"
                )
            resolved = candidate.resolve(strict=True)
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                value = read_json(resolved)
            except (OSError, ValueError):
                continue
            kind = value.get("kind") if isinstance(value, Mapping) else None
            if kind in by_kind:
                by_kind[str(kind)].append(value)
                roots[id(value)] = resolved.parent
    return by_kind, roots


def _one_post_fix_artifact(
    artifacts: Mapping[str, list[Mapping[str, Any]]],
    kind: str,
) -> Mapping[str, Any]:
    values = artifacts.get(kind)
    if not isinstance(values, list) or len(values) != 1:
        raise ValueError(
            f"post-fix evidence must contain exactly one {kind!r} artifact"
        )
    return values[0]


def _f1(precision: float | None, recall: float | None) -> float:
    if precision is None or recall is None or precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 6)


def _counts_metrics(counts: Mapping[str, int]) -> dict[str, Any]:
    tp = int(counts.get("truePositive", 0))
    fn = int(counts.get("falseNegative", 0))
    fp = int(counts.get("referenceSetFalsePositive", 0))
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return {
        "counts": {
            "truePositive": tp,
            "falseNegative": fn,
            "referenceSetFalsePositive": fp,
            "goldIssues": tp + fn,
            "candidateFindings": tp + fp,
        },
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
    }


def _sum_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    total = Counter()
    for row in rows:
        counts = row["primary"]["counts"]
        total.update(
            {
                "truePositive": int(counts["truePositive"]),
                "falseNegative": int(counts["falseNegative"]),
                "referenceSetFalsePositive": int(
                    counts["referenceSetFalsePositive"]
                ),
            }
        )
    return dict(total)


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * fraction,
        6,
    )


def _interval(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "low": _percentile(values, 0.025),
        "high": _percentile(values, 0.975),
        "iterations": len(values),
    }


def _seed_for(seed: int, label: str) -> int:
    digest = sha256_json({"seed": seed, "label": label})
    return int(digest[:16], 16)


def _bootstrap_primary(
    rows: Sequence[Mapping[str, Any]],
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    if not rows or iterations <= 0:
        empty = {"low": None, "high": None, "iterations": 0}
        return {"micro": dict(empty), "macro": dict(empty)}
    rng = random.Random(seed)
    micro_precision: list[float] = []
    micro_recall: list[float] = []
    micro_f1: list[float] = []
    macro_precision: list[float] = []
    macro_recall: list[float] = []
    macro_f1: list[float] = []
    for _ in range(iterations):
        sampled = [rows[rng.randrange(len(rows))] for _ in rows]
        micro = _counts_metrics(_sum_counts(sampled))
        if micro["precision"] is not None:
            micro_precision.append(micro["precision"])
        if micro["recall"] is not None:
            micro_recall.append(micro["recall"])
        micro_f1.append(micro["f1"])
        precisions = [
            float(row["primary"]["precision"])
            for row in sampled
            if row["primary"]["precision"] is not None
        ]
        recalls = [float(row["primary"]["recall"]) for row in sampled]
        f1_values = [float(row["primary"]["f1"]) for row in sampled]
        if precisions:
            macro_precision.append(fmean(precisions))
        macro_recall.append(fmean(recalls))
        macro_f1.append(fmean(f1_values))
    return {
        "micro": {
            "precision": _interval(micro_precision),
            "recall": _interval(micro_recall),
            "f1": _interval(micro_f1),
        },
        "macro": {
            "precision": _interval(macro_precision),
            "recall": _interval(macro_recall),
            "f1": _interval(macro_f1),
        },
    }


def _aggregate_primary(
    rows: Sequence[Mapping[str, Any]],
    *,
    iterations: int,
    seed: int,
    label: str,
) -> dict[str, Any]:
    micro = _counts_metrics(_sum_counts(rows))
    precision_values = [
        float(row["primary"]["precision"])
        for row in rows
        if row["primary"]["precision"] is not None
    ]
    recall_values = [float(row["primary"]["recall"]) for row in rows]
    f1_values = [float(row["primary"]["f1"]) for row in rows]
    macro = {
        "precision": (
            round(fmean(precision_values), 6) if precision_values else None
        ),
        "recall": round(fmean(recall_values), 6) if recall_values else None,
        "f1": round(fmean(f1_values), 6) if f1_values else None,
        "precisionDefinedCases": len(precision_values),
        "caseCount": len(rows),
    }
    intervals = _bootstrap_primary(
        rows,
        iterations=iterations,
        seed=_seed_for(seed, label),
    )
    micro["confidenceInterval95"] = intervals["micro"]
    macro["confidenceInterval95"] = intervals["macro"]
    return {
        "definition": (
            "One-to-one substantive matches against eligible reviewer issues. "
            "Unmatched findings are reference-set false positives; they are not "
            "proven invalid defects."
        ),
        "micro": micro,
        "macro": macro,
    }


def _validate_judgment(
    value: Any,
    *,
    corpus_digest: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or value.get("kind") != JUDGMENT_KIND:
        raise ValueError("judgment file kind is invalid")
    if value.get("corpusDigest") != corpus_digest:
        raise ValueError("judgment file belongs to a different corpus")
    digest_value = dict(value)
    declared = digest_value.pop("judgmentDigest", None)
    computed = sha256_json(digest_value)
    if declared != computed:
        raise ValueError("judgmentDigest mismatch")
    if not isinstance(value.get("cases"), list):
        raise ValueError("judgment cases must be an array")
    return value


def _analysis_runs(
    paths: Sequence[Path] | None,
    *,
    corpus_digest: str,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Path]]:
    result = {}
    artifact_roots = {}
    for path in paths or []:
        value = read_json(path)
        if not isinstance(value, Mapping) or value.get("kind") != RUN_KIND:
            raise ValueError(f"analysis run kind is invalid: {path}")
        digest_value = dict(value)
        declared = digest_value.pop("runDigest", None)
        if declared != sha256_json(digest_value):
            raise ValueError(f"analysis run digest mismatch: {path}")
        if value.get("corpusDigest") != corpus_digest:
            raise ValueError(f"analysis run belongs to another corpus: {path}")
        run_id = str(value.get("runId") or "")
        if not run_id or run_id in result:
            raise ValueError(f"duplicate/invalid analysis run ID: {run_id!r}")
        result[run_id] = value
        artifact_roots[run_id] = path.resolve().parent
    return result, artifact_roots


def _replay_evidence(
    *,
    corpus: Mapping[str, Any],
    corpus_summary: Mapping[str, Any],
    lock_paths: Sequence[Path] | None,
    attestation_paths: Sequence[Path] | None,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    locks: dict[str, Mapping[str, Any]] = {}
    for path in lock_paths or []:
        value = read_json(path)
        if not isinstance(value, Mapping):
            raise ValueError(f"replay lock must be an object: {path}")
        validate_replay_lock(
            value,
            corpus,
            corpus_summary=corpus_summary,
        )
        digest = str(value.get("lockDigest") or "")
        if not digest or digest in locks:
            raise ValueError(f"duplicate/invalid replay lock: {path}")
        locks[digest] = value

    attestations: dict[str, Mapping[str, Any]] = {}
    for path in attestation_paths or []:
        value = read_json(path)
        if not isinstance(value, Mapping):
            raise ValueError(f"replay attestation must be an object: {path}")
        lock_digest = str(value.get("replayLockDigest") or "")
        lock = locks.get(lock_digest)
        if lock is None:
            raise ValueError(
                f"replay attestation has no supplied replay lock: {path}"
            )
        digest = validate_replay_attestation(
            value,
            lock,
            corpus,
            corpus_summary=corpus_summary,
        )
        if digest in attestations:
            raise ValueError(f"duplicate replay attestation: {path}")
        attestations[digest] = value
    return locks, attestations


def _is_int(value: Any, *, minimum: int = 0) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value >= minimum
    )


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and SHA256_HEX.fullmatch(value) is not None


def _safe_raw_artifact(
    artifact_root: Path | None,
    raw_name: Any,
) -> Mapping[str, Any] | None:
    if (
        artifact_root is None
        or not isinstance(raw_name, str)
        or not raw_name
    ):
        return None
    relative = Path(raw_name)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = artifact_root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    try:
        value = read_json(path)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def _provider_content(value: Mapping[str, Any]) -> str | None:
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content, list):
        if any(not isinstance(item, Mapping) for item in content):
            return None
        content = "".join(str(item.get("text") or "") for item in content)
    return content if isinstance(content, str) else None


def _expected_judge_request(
    *,
    judge_config: Mapping[str, Any],
    system: str,
    prompt: str,
) -> Mapping[str, Any] | None:
    model = judge_config.get("model")
    custom = judge_config.get("custom_parameters")
    if not isinstance(model, str) or not model.strip():
        return None
    if custom is not None and not isinstance(custom, Mapping):
        return None
    reserved = {"model", "messages", "response_format", "temperature"}
    if reserved.intersection(custom or {}):
        return None
    try:
        temperature = float(judge_config.get("temperature") or 0)
    except (TypeError, ValueError):
        return None
    request: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    request.update(dict(custom or {}))
    return request


def _contains_redaction(value: Any) -> bool:
    if value == "<redacted>":
        return True
    if isinstance(value, Mapping):
        return any(_contains_redaction(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_redaction(item) for item in value)
    return False


def _provider_metadata_failures(
    metadata: Any,
    *,
    judge_config: Mapping[str, Any],
    system: str,
    prompt: str,
    response_value: Any,
) -> list[str]:
    if not isinstance(metadata, Mapping):
        return ["provider_metadata_missing"]
    failures = []
    request = metadata.get("request")
    expected_request = _expected_judge_request(
        judge_config=judge_config,
        system=system,
        prompt=prompt,
    )
    if (
        expected_request is None
        or request != expected_request
        or metadata.get("requestSha256") != sha256_json(expected_request)
    ):
        failures.append("provider_request_mismatch")
    if _contains_redaction(request):
        failures.append("provider_request_contains_redaction")
    provider_response = metadata.get("providerResponse")
    if (
        not isinstance(provider_response, Mapping)
        or metadata.get("providerResponseSha256")
        != sha256_json(provider_response)
    ):
        failures.append("provider_response_missing_or_tampered")
        return failures
    content = _provider_content(provider_response)
    if (
        not isinstance(content, str)
        or metadata.get("rawContentSha256") != sha256_text(content)
    ):
        failures.append("provider_content_digest_mismatch")
    else:
        try:
            parsed = _extract_json(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            failures.append("provider_content_not_json")
        else:
            if parsed != response_value:
                failures.append("provider_content_response_mismatch")
    response_id = metadata.get("responseId")
    if (
        not isinstance(response_id, str)
        or not response_id.strip()
        or provider_response.get("id") != response_id
    ):
        failures.append("provider_response_id_mismatch")
    expected_model = (
        judge_config.get("expected_response_model")
        or judge_config.get("model")
    )
    if (
        not isinstance(expected_model, str)
        or not expected_model.strip()
        or provider_response.get("model") != expected_model
        or metadata.get("model") != expected_model
    ):
        failures.append("provider_model_mismatch")
    if metadata.get("usage") != provider_response.get("usage"):
        failures.append("provider_usage_mismatch")
    if metadata.get("promptSha256") != sha256_text(system + "\n" + prompt):
        failures.append("provider_prompt_digest_mismatch")
    return failures


def _pair_prompt_failures(
    prompt: Any,
    *,
    gold_label: str,
    gold: Mapping[str, Any],
    findings: list[Mapping[str, Any]],
    changed_paths: set[str],
    max_prompt_characters: int = 400_000,
    expected_evidence: list[Mapping[str, Any]] | None = None,
) -> list[str]:
    if not isinstance(prompt, str):
        return ["prompt_missing"]
    marker = "\n\nOUTPUT SCHEMA:\n"
    if not prompt.startswith("INPUT:\n") or marker not in prompt:
        return ["prompt_format_invalid"]
    input_text, _ = prompt[len("INPUT:\n") :].split(marker, 1)
    try:
        input_value = json.loads(input_text)
    except (TypeError, json.JSONDecodeError):
        return ["prompt_input_invalid"]
    candidates = (
        input_value.get("candidates")
        if isinstance(input_value, Mapping)
        else None
    )
    if (
        not isinstance(candidates, list)
        or len(candidates) != len(findings)
        or any(not isinstance(item, Mapping) for item in candidates)
    ):
        return ["prompt_candidates_invalid"]
    evidence = [item.get("frozen_evidence") for item in candidates]
    if any(not isinstance(item, Mapping) for item in evidence):
        return ["prompt_evidence_invalid"]
    source_evidence = (
        expected_evidence
        if expected_evidence is not None
        else evidence
    )
    expected_prompt = _gold_prompt(
        gold_label=gold_label,
        gold=gold,
        findings=findings,
        candidate_evidence=source_evidence,
        max_prompt_characters=max_prompt_characters,
    )
    failures = []
    if expected_evidence is not None:
        expected_input_text = expected_prompt[
            len("INPUT:\n") :
        ].split(marker, 1)[0]
        expected_input = json.loads(expected_input_text)
        expected_candidates = expected_input.get("candidates")
        expected_compacted_evidence = [
            item.get("frozen_evidence")
            for item in expected_candidates
            if isinstance(item, Mapping)
        ]
        if evidence != expected_compacted_evidence:
            failures.append("prompt_source_reconstruction_mismatch")
    if prompt != expected_prompt:
        failures.append("prompt_content_mismatch")
    for finding, item in zip(findings, evidence, strict=True):
        path = finding.get("path")
        line = finding.get("line")
        if item.get("inFrozenDiff") != (
            isinstance(path, str) and path in changed_paths
        ):
            failures.append("prompt_path_evidence_mismatch")
        if not _is_digest(item.get("pathDiffSha256")) or not _is_digest(
            item.get("headSourceSha256")
        ):
            failures.append("prompt_source_digest_missing")
        path_diff = item.get("pathDiff")
        if not isinstance(path_diff, str) or not isinstance(
            item.get("headSourceWindow"), str
        ):
            failures.append("prompt_source_evidence_missing")
        elif "[truncated " not in path_diff and item.get(
            "lineOnAddedRightSide"
        ) != (
            isinstance(line, int)
            and not isinstance(line, bool)
            and line in _right_added_lines(path_diff)
        ):
            failures.append("prompt_line_evidence_mismatch")
    return list(dict.fromkeys(failures))


def _novel_prompt_failures(
    prompt: Any,
    *,
    candidate_label: str,
    finding: Mapping[str, Any],
    changed_paths: set[str],
    expected_prompt: str | None = None,
) -> list[str]:
    if not isinstance(prompt, str):
        return ["prompt_missing"]
    try:
        value = json.loads(prompt)
    except (TypeError, json.JSONDecodeError):
        return ["prompt_input_invalid"]
    if not isinstance(value, Mapping):
        return ["prompt_input_invalid"]
    failures = []
    if expected_prompt is not None and prompt != expected_prompt:
        failures.append("prompt_source_reconstruction_mismatch")
    expected_finding = {
        key: finding.get(key)
        for key in (
            "path",
            "line",
            "title",
            "description",
            "category",
            "severity",
            "suggestedFix",
        )
    }
    location = value.get("frozen_location_evidence")
    path_diff = value.get("frozen_path_diff")
    head_source = value.get("frozen_head_source")
    if (
        value.get("candidate_id") != candidate_label
        or value.get("finding") != expected_finding
        or not isinstance(location, Mapping)
        or not isinstance(path_diff, str)
        or not isinstance(head_source, str)
    ):
        return list(dict.fromkeys([*failures, "prompt_content_mismatch"]))
    path = finding.get("path")
    line = finding.get("line")
    if location.get("path_in_diff") != (
        isinstance(path, str) and path in changed_paths
    ):
        failures.append("prompt_path_evidence_mismatch")
    if "[truncated " not in path_diff and location.get(
        "line_on_added_right_side"
    ) != (
        isinstance(line, int)
        and not isinstance(line, bool)
        and line in _right_added_lines(path_diff)
    ):
        failures.append("prompt_line_evidence_mismatch")
    template = json.loads(
        _novel_prompt(
            candidate_label=candidate_label,
            finding=finding,
            path_diff="",
            head_source="",
            path_in_frozen_diff=(
                isinstance(path, str) and path in changed_paths
            ),
        )
    )
    if value.get("output_schema") != template["output_schema"]:
        failures.append("prompt_schema_mismatch")
    return failures


def _paper_judgment_failures(
    judgment: Mapping[str, Any],
    *,
    corpus_cases: Mapping[str, Mapping[str, Any]],
    analysis_run: Mapping[str, Any] | None,
    artifact_root: Path | None,
    repository: Path | None = None,
    require_source_reconstruction: bool = False,
) -> list[str]:
    """Reconstruct a paper judgment from its raw cases and provider calls."""

    failures: list[str] = []
    expected_prompt_digest = sha256_text(
        MATCH_SYSTEM + NOVEL_SYSTEM + PROMPT_VERSION
    )
    if (
        judgment.get("promptVersion") != PROMPT_VERSION
        or judgment.get("promptDigest") != expected_prompt_digest
    ):
        failures.append("prompt_contract_mismatch")
    judge_config = judgment.get("judgeConfig")
    judge_config_digest = judgment.get("judgeConfigDigest")
    if (
        not isinstance(judge_config, Mapping)
        or not _is_digest(judge_config_digest)
        or judge_config_digest != sha256_json(judge_config)
        or judge_config.get("model") != judgment.get("judgeModel")
    ):
        failures.append("judge_configuration_unverifiable")
        return failures
    try:
        repeats = int(judge_config.get("repeats") or 1)
    except (TypeError, ValueError):
        repeats = 0
    if repeats < 1 or repeats % 2 == 0:
        failures.append("judge_repeats_invalid")
    try:
        max_prompt_characters = int(
            judge_config.get("max_prompt_characters") or 400_000
        )
    except (TypeError, ValueError):
        max_prompt_characters = 400_000
        failures.append("judge_max_prompt_characters_invalid")
    if max_prompt_characters < 10_000:
        failures.append("judge_max_prompt_characters_invalid")
        max_prompt_characters = 400_000
    validate_unmatched = judge_config.get(
        "validate_unmatched_findings",
        True,
    )
    if not isinstance(validate_unmatched, bool):
        failures.append("judge_novel_policy_invalid")
    if analysis_run is None:
        failures.append("analysis_artifact_missing")
        return failures

    run_cases_value = analysis_run.get("cases")
    judgment_cases_value = judgment.get("cases")
    if not isinstance(run_cases_value, list) or not isinstance(
        judgment_cases_value, list
    ):
        failures.append("case_artifacts_invalid")
        return failures
    run_cases = {
        str(item.get("caseId")): item
        for item in run_cases_value
        if isinstance(item, Mapping) and isinstance(item.get("caseId"), str)
    }
    judgment_cases = {
        str(item.get("caseId")): item
        for item in judgment_cases_value
        if isinstance(item, Mapping) and isinstance(item.get("caseId"), str)
    }
    expected_ids = set(corpus_cases)
    if (
        len(run_cases) != len(run_cases_value)
        or len(judgment_cases) != len(judgment_cases_value)
        or set(run_cases) != expected_ids
        or set(judgment_cases) != expected_ids
    ):
        failures.append("case_coverage_incomplete")
        return failures

    analysis_run_digest = analysis_run.get("runDigest")
    if (
        judgment.get("analysisRunId") != analysis_run.get("runId")
        or judgment.get("analysisRunDigest") != analysis_run_digest
        or judgment.get("analysisModel") != analysis_run.get("analysisModel")
    ):
        failures.append("analysis_binding_mismatch")

    def verify_call(
        call: Mapping[str, Any] | None,
        *,
        case_id: str,
        kind: str,
        label_field: str,
        label: str,
        repeat: int,
        case_input_digest: str,
        system: str,
        validator: Callable[[Any], Any],
        prompt_validator: Callable[[Any], list[str]],
    ) -> tuple[Any | None, list[str]]:
        local: list[str] = []
        if call is None:
            return None, ["call_missing"]
        checkpoint = _safe_raw_artifact(
            artifact_root,
            call.get("checkpoint"),
        )
        if checkpoint is None:
            return None, ["checkpoint_missing"]
        digest_value = dict(checkpoint)
        declared = digest_value.pop("callDigest", None)
        if declared != sha256_json(digest_value):
            local.append("checkpoint_digest_mismatch")
        prompt = checkpoint.get("prompt")
        if checkpoint.get("system") != system:
            local.append("checkpoint_system_mismatch")
        local.extend(prompt_validator(prompt))
        binding = {
            "kind": kind,
            "caseId": case_id,
            label_field: label,
            "repeat": repeat,
            "caseInputDigest": case_input_digest,
            "judgeConfigDigest": judge_config_digest,
        }
        if isinstance(prompt, str):
            binding_digest = sha256_json(
                {
                    **binding,
                    "systemSha256": sha256_text(system),
                    "promptSha256": sha256_text(prompt),
                }
            )
            if checkpoint.get("bindingDigest") != binding_digest:
                local.append("checkpoint_binding_mismatch")
        response_value = checkpoint.get("response")
        try:
            normalized = validator(response_value)
        except (TypeError, ValueError):
            normalized = None
            local.append("checkpoint_response_invalid")

        metadata = checkpoint.get("metadata")
        if isinstance(prompt, str):
            local.extend(
                _provider_metadata_failures(
                    metadata,
                    judge_config=judge_config,
                    system=system,
                    prompt=prompt,
                    response_value=response_value,
                )
            )
        rejected = checkpoint.get("rejectedStructuredResponses")
        if not isinstance(rejected, list):
            local.append("checkpoint_rejections_invalid")
        else:
            for index, rejected_item in enumerate(rejected, start=1):
                if (
                    not isinstance(rejected_item, Mapping)
                    or rejected_item.get("attempt") != index
                    or not isinstance(
                        rejected_item.get("validationError"),
                        str,
                    )
                    or not rejected_item.get("validationError")
                ):
                    local.append("checkpoint_rejection_record_invalid")
                    continue
                rejected_response = rejected_item.get("response")
                try:
                    validator(rejected_response)
                except (TypeError, ValueError):
                    pass
                else:
                    local.append("checkpoint_rejection_was_valid")
                if isinstance(prompt, str):
                    local.extend(
                        _provider_metadata_failures(
                            rejected_item.get("metadata"),
                            judge_config=judge_config,
                            system=system,
                            prompt=prompt,
                            response_value=rejected_response,
                        )
                    )
        metadata_value = (
            dict(metadata) if isinstance(metadata, Mapping) else {}
        )
        if set(metadata_value).intersection(
            {
                "kind",
                label_field,
                "repeat",
                "checkpoint",
                "bindingDigest",
                "completedAt",
                "system",
                "prompt",
                "response",
                "rejectedStructuredResponses",
                "callDigest",
            }
        ):
            local.append("provider_metadata_field_collision")
        expected_call = {
            "kind": kind,
            label_field: label,
            "repeat": repeat,
            "checkpoint": call.get("checkpoint"),
            **{
                key: value
                for key, value in checkpoint.items()
                if key != "metadata"
            },
            **metadata_value,
        }
        if dict(call) != expected_call:
            local.append("call_checkpoint_projection_mismatch")
        return normalized, list(dict.fromkeys(local))

    for case_id in sorted(expected_ids):
        case = corpus_cases[case_id]
        run_case = run_cases[case_id]
        judgment_case = judgment_cases[case_id]
        prefix = f"{case_id}:"
        if (
            run_case.get("status") != "completed"
            or judgment_case.get("status") != "scored"
        ):
            failures.append(prefix + "case_not_scored")
            continue
        case_input_digest = sha256_json(
            {
                "corpusCase": case,
                "analysisCase": run_case,
                "analysisRunDigest": analysis_run_digest,
                "judgeConfigDigest": judge_config_digest,
                "promptVersion": PROMPT_VERSION,
            }
        )
        raw = _safe_raw_artifact(
            artifact_root,
            judgment_case.get("rawJudgment"),
        )
        if raw is None:
            failures.append(prefix + "raw_judgment_missing")
            continue
        raw_digest_value = dict(raw)
        raw_declared = raw_digest_value.pop("caseDigest", None)
        if raw_declared != sha256_json(raw_digest_value):
            failures.append(prefix + "raw_judgment_digest_mismatch")
        expected_projection = dict(raw)
        expected_projection["rawJudgment"] = judgment_case.get(
            "rawJudgment"
        )
        if dict(judgment_case) != expected_projection:
            failures.append(prefix + "raw_judgment_projection_mismatch")
        if (
            raw.get("caseInputDigest") != case_input_digest
            or raw.get("judgeConfigDigest") != judge_config_digest
        ):
            failures.append(prefix + "case_binding_mismatch")

        findings = [
            item
            for item in (run_case.get("findings") or [])
            if isinstance(item, Mapping)
        ]
        if len(findings) != len(run_case.get("findings") or []):
            failures.append(prefix + "analysis_findings_invalid")
            continue
        gold = list(case["goldenComments"])
        changed_paths = set(case["snapshot"]["changedPaths"])
        reconstructed_evidence: list[Mapping[str, Any]] | None = None
        reconstructed_novel_prompts: dict[str, str] = {}
        if findings and require_source_reconstruction:
            if (
                repository is None
                or not is_local_git_repository(repository)
            ):
                failures.append(prefix + "source_repository_missing")
            else:
                try:
                    _validate_local_snapshot(repository, case)
                    reconstructed_evidence = _candidate_evidence(
                        repository,
                        case,
                        findings,
                    )
                    for index, finding in enumerate(findings, start=1):
                        candidate_label = f"C{index:03d}"
                        path = finding.get("path")
                        path_diff, head_source = _path_evidence(
                            repository,
                            case["snapshot"]["baseSha"],
                            case["snapshot"]["headSha"],
                            path if isinstance(path, str) else None,
                            changed_paths,
                        )
                        reconstructed_novel_prompts[candidate_label] = (
                            _novel_prompt(
                                candidate_label=candidate_label,
                                finding=finding,
                                path_diff=path_diff,
                                head_source=head_source,
                                path_in_frozen_diff=(
                                    isinstance(path, str)
                                    and path in changed_paths
                                ),
                            )
                        )
                except (OSError, RuntimeError, ValueError):
                    failures.append(prefix + "source_reconstruction_failed")
        calls_value = raw.get("calls")
        if not isinstance(calls_value, list) or any(
            not isinstance(item, Mapping) for item in calls_value
        ):
            failures.append(prefix + "calls_invalid")
            continue
        calls = [item for item in calls_value if isinstance(item, Mapping)]
        call_keys: dict[tuple[str, str, int], Mapping[str, Any]] = {}
        observed_order: list[tuple[str, str, int]] = []
        for call in calls:
            kind = str(call.get("kind") or "")
            label = (
                str(call.get("goldId") or "")
                if kind == "pair"
                else str(call.get("candidateId") or "")
            )
            repeat = call.get("repeat")
            key = (
                kind,
                label,
                repeat if _is_int(repeat, minimum=1) else -1,
            )
            if key in call_keys:
                failures.append(prefix + "duplicate_call")
            call_keys[key] = call
            observed_order.append(key)

        expected_order: list[tuple[str, str, int]] = []
        pair_judgments: list[dict[str, Any]] = []
        if findings:
            for gold_index, gold_item in enumerate(gold, start=1):
                gold_label = f"G{gold_index:03d}"
                per_candidate: dict[str, list[dict[str, Any]]] = {
                    f"C{index:03d}": []
                    for index in range(1, len(findings) + 1)
                }
                for repeat in range(1, repeats + 1):
                    key = ("pair", gold_label, repeat)
                    expected_order.append(key)
                    normalized, call_failures = verify_call(
                        call_keys.get(key),
                        case_id=case_id,
                        kind="pair",
                        label_field="goldId",
                        label=gold_label,
                        repeat=repeat,
                        case_input_digest=case_input_digest,
                        system=MATCH_SYSTEM,
                        validator=lambda value, label=gold_label: (
                            _validate_match_response(
                                value,
                                gold_label=label,
                                candidate_count=len(findings),
                            )
                        ),
                        prompt_validator=lambda value, label=gold_label, item=gold_item: (
                            _pair_prompt_failures(
                                value,
                                gold_label=label,
                                gold=item,
                                findings=findings,
                                changed_paths=changed_paths,
                                max_prompt_characters=max_prompt_characters,
                                expected_evidence=reconstructed_evidence,
                            )
                        ),
                    )
                    failures.extend(
                        prefix + failure for failure in call_failures
                    )
                    if isinstance(normalized, list):
                        for item in normalized:
                            per_candidate[item["candidate_id"]].append(item)
                for candidate_label, values in per_candidate.items():
                    if len(values) != repeats:
                        failures.append(prefix + "pair_repeats_incomplete")
                        continue
                    pair_judgments.append(
                        {
                            "goldId": gold_label,
                            "candidateId": candidate_label,
                            **_majority_match(values),
                        }
                    )
        assignments = _maximum_assignment(
            len(gold),
            len(findings),
            pair_judgments,
        )
        matched_gold = {item["goldId"] for item in assignments}
        matched_candidates = {
            item["candidateId"] for item in assignments
        }
        unmatched_gold = [
            f"G{index:03d}"
            for index in range(1, len(gold) + 1)
            if f"G{index:03d}" not in matched_gold
        ]
        unmatched_candidates = [
            f"C{index:03d}"
            for index in range(1, len(findings) + 1)
            if f"C{index:03d}" not in matched_candidates
        ]
        novel: list[dict[str, Any]] = []
        if validate_unmatched is True:
            for candidate_label in unmatched_candidates:
                candidate_index = int(candidate_label[1:]) - 1
                finding = findings[candidate_index]
                values = []
                for repeat in range(1, repeats + 1):
                    key = ("novel", candidate_label, repeat)
                    expected_order.append(key)
                    normalized, call_failures = verify_call(
                        call_keys.get(key),
                        case_id=case_id,
                        kind="novel",
                        label_field="candidateId",
                        label=candidate_label,
                        repeat=repeat,
                        case_input_digest=case_input_digest,
                        system=NOVEL_SYSTEM,
                        validator=lambda value, label=candidate_label: (
                            _validate_novel(value, label)
                        ),
                        prompt_validator=lambda value, label=candidate_label, item=finding: (
                            _novel_prompt_failures(
                                value,
                                candidate_label=label,
                                finding=item,
                                changed_paths=changed_paths,
                                expected_prompt=(
                                    reconstructed_novel_prompts.get(label)
                                    if require_source_reconstruction
                                    else None
                                ),
                            )
                        ),
                    )
                    failures.extend(
                        prefix + failure for failure in call_failures
                    )
                    if isinstance(normalized, Mapping):
                        values.append(dict(normalized))
                if len(values) != repeats:
                    failures.append(prefix + "novel_repeats_incomplete")
                    continue
                novel.append(
                    {
                        "candidateId": candidate_label,
                        **_majority_novel(values),
                    }
                )
        if observed_order != expected_order:
            failures.append(prefix + "call_set_or_order_mismatch")

        expected_case = {
            "caseId": case_id,
            "caseInputDigest": case_input_digest,
            "judgeConfigDigest": judge_config_digest,
            "status": "scored",
            "sizeBand": case["sizeBand"],
            "partition": case["partition"],
            "goldCount": len(gold),
            "candidateCount": len(findings),
            "goldIssues": [
                {
                    "goldId": f"G{index:03d}",
                    "sourceId": item["id"],
                    "sourceUrl": item["sourceUrl"],
                    "path": item["path"],
                    "line": item["originalLine"],
                    "reviewComment": item["body"],
                    "summary": item["expectedIssue"]["summary"],
                    "category": item["expectedIssue"]["category"],
                    "severity": item["expectedIssue"]["severity"],
                }
                for index, item in enumerate(gold, start=1)
            ],
            "candidateFindings": [
                {
                    "candidateId": f"C{index:03d}",
                    **{
                        key: value
                        for key, value in item.items()
                        if key != "raw"
                    },
                }
                for index, item in enumerate(findings, start=1)
            ],
            "pairJudgments": pair_judgments,
            "assignments": assignments,
            "unmatchedGold": unmatched_gold,
            "unmatchedCandidates": unmatched_candidates,
            "novelFindingJudgments": novel,
            "calls": calls,
        }
        expected_case["caseDigest"] = sha256_json(expected_case)
        if dict(raw) != expected_case:
            failures.append(prefix + "derived_judgment_mismatch")
    return list(dict.fromkeys(failures))


def _analysis_control_digest(run: Mapping[str, Any]) -> str:
    """Fingerprint every paper comparison factor except the selected model."""

    analysis_config = run.get("analysisConfig")
    normalized_config = (
        dict(analysis_config)
        if isinstance(analysis_config, Mapping)
        else {"invalid": analysis_config}
    )
    for field in ("model", "expected_response_model"):
        if field in normalized_config:
            normalized_config[field] = "<varied-analysis-model>"
    model_roles = run.get("analysisModelRoles")
    normalized_roles = (
        dict(model_roles)
        if isinstance(model_roles, Mapping)
        else {"invalid": model_roles}
    )
    for field in (
        "reviewPipeline",
        "reviewPipelineRequested",
        "reviewPipelineExpectedResponse",
        "reviewPipelineProviderReported",
    ):
        if field in normalized_roles:
            normalized_roles[field] = "<varied-analysis-model>"
    stage_roles = normalized_roles.get("providerReportedByStage")
    if isinstance(stage_roles, Mapping):
        normalized_roles["providerReportedByStage"] = {
            str(stage): "<varied-analysis-model>"
            for stage in sorted(stage_roles)
        }
    runtime = run.get("runtimeProvenance")
    normalized_runtime: dict[str, Any] = {}
    if isinstance(runtime, Mapping):
        for name, identity in runtime.items():
            if isinstance(identity, Mapping):
                normalized_runtime[str(name)] = {
                    key: value
                    for key, value in identity.items()
                    if key != "containerId"
                }
            else:
                normalized_runtime[str(name)] = identity
    else:
        normalized_runtime = {"invalid": runtime}
    cases = run.get("cases")
    request_controls = sorted(
        (
            {
                "caseId": item.get("caseId"),
                "requestControlDigest": item.get("requestControlDigest"),
            }
            for item in cases
            if isinstance(item, Mapping)
        ),
        key=lambda item: str(item.get("caseId") or ""),
    ) if isinstance(cases, list) else [{"invalid": cases}]
    return sha256_json(
        {
            "analysisProvider": run.get("analysisProvider"),
            "analysisConfig": normalized_config,
            "analysisModelRoles": normalized_roles,
            "replayLockDigest": run.get("replayLockDigest"),
            "replayLockArtifact": run.get("replayLockArtifact"),
            "replayAttestationDigest": run.get(
                "replayAttestationDigest"
            ),
            "replayAttestationArtifact": run.get(
                "replayAttestationArtifact"
            ),
            "runtimeImages": normalized_runtime,
            "selectedCaseIds": run.get("selectedCaseIds"),
            "transport": run.get("transport"),
            "findingSemantics": run.get("findingSemantics"),
            "requestControls": request_controls,
            "indexReceiptsBefore": run.get("indexReceiptsBefore"),
            "indexReceiptsAfter": run.get("indexReceiptsAfter"),
        }
    )


def _receipt_failures(
    receipt: Any,
    *,
    case: Mapping[str, Any],
    analysis_config: Mapping[str, Any],
) -> list[str]:
    if not isinstance(receipt, Mapping):
        return ["missing"]
    failures = []
    workspace = str(
        analysis_config.get("rag_workspace")
        or analysis_config.get("project_workspace")
        or ""
    )
    project = str(
        analysis_config.get("rag_project")
        or analysis_config.get("project_namespace")
        or ""
    )
    expected_identity = {
        "workspace": workspace,
        "project": project,
        "branch": case["replay"]["baseRef"],
        "commit": case["snapshot"]["baseSha"],
        "repository_revision": case["snapshot"]["baseSha"],
    }
    if not workspace or not project:
        failures.append("coordinates_missing")
    if any(
        receipt.get(field) != expected
        for field, expected in expected_identity.items()
    ):
        failures.append("identity_mismatch")
    point_count = receipt.get("point_count")
    member_count = receipt.get("generation_member_count")
    if (
        receipt.get("generation_schema")
        != "codecrow.repository-index-generation"
    ):
        failures.append("generation_unsealed")
    if (
        not _is_int(point_count, minimum=1)
        or not _is_int(member_count, minimum=1)
        or point_count != member_count + 1
    ):
        failures.append("generation_count_invalid")
    for field in (
        "generation_members_sha256",
        "generation_manifest_sha256",
        "repository_facts_sha256",
        "source_tree_sha256",
    ):
        if not _is_digest(receipt.get(field)):
            failures.append(f"{field}_invalid")
    try:
        _validate_index_selection_policy(receipt)
    except RuntimeError:
        failures.append("index_selection_policy_invalid")
    for field in (
        "plugin_fingerprint",
        "plugin_descriptor_fingerprint",
        "plugin_implementation_fingerprint",
        "index_representation_fingerprint",
    ):
        value = receipt.get(field)
        if (
            not isinstance(value, str)
            or SHA256_FINGERPRINT.fullmatch(value) is None
        ):
            failures.append(f"{field}_invalid")
    plugin_ids = receipt.get("plugin_ids")
    if (
        not isinstance(plugin_ids, list)
        or not plugin_ids
        or any(
            not isinstance(plugin_id, str) or not plugin_id.strip()
            for plugin_id in plugin_ids
        )
        or len(plugin_ids) != len(set(plugin_ids))
    ):
        failures.append("plugin_ids_invalid")
    else:
        configured = analysis_config.get("required_repository_plugins")
        configured_required = (
            {
                item
                for item in configured
                if isinstance(item, str) and item.strip()
            }
            if isinstance(configured, list)
            else set()
        )
        required = {"php", "magento"} | configured_required
        if not required.issubset(set(plugin_ids)):
            failures.append("required_plugin_ids_missing")
    return failures


ANALYSIS_REQUEST_FIELDS = {
    "projectId",
    "projectVcsWorkspace",
    "projectVcsRepoSlug",
    "projectWorkspace",
    "projectNamespace",
    "aiProvider",
    "aiModel",
    "aiApiKey",
    "aiBaseUrl",
    "aiCustomParameters",
    "analysisType",
    "targetBranchName",
    "sourceBranchName",
    "pullRequestId",
    "commitHash",
    "currentCommitHash",
    "baseCommitHash",
    "prTitle",
    "prDescription",
    "prAuthor",
    "taskContext",
    "taskHistoryContext",
    "changedFiles",
    "deletedFiles",
    "diffSnippets",
    "rawDiff",
    "vcsProvider",
    "analysisMode",
    "previousCodeAnalysisIssues",
    "enrichmentData",
    "useMcpTools",
    "ragEnabled",
    "projectRules",
}
ENRICHMENT_ENTRY_FIELDS = {
    "path",
    "content",
    "sizeBytes",
    "skipped",
    "skipReason",
}
ENRICHMENT_STATS_FIELDS = {
    "totalFilesRequested",
    "filesEnriched",
    "filesSkipped",
    "relationshipsFound",
    "totalContentSizeBytes",
    "processingTimeMs",
    "skipReasons",
}
ENRICHMENT_SKIP_REASONS = {
    "deleted_file",
    "source_unavailable",
    "file_too_large",
    "binary_file",
    "total_content_limit",
    "non_utf8_source",
}


def _request_failures(
    value: Any,
    *,
    case: Mapping[str, Any],
    run: Mapping[str, Any],
    analysis_config: Mapping[str, Any],
    request_digest: Any,
    request_control_digest: Any,
    replay_case: Mapping[str, Any] | None,
    fork_repository: str | None,
    reconstructed_request: Mapping[str, Any] | None = None,
) -> list[str]:
    if not isinstance(value, Mapping):
        return ["missing"]
    failures = []
    if set(value) != ANALYSIS_REQUEST_FIELDS:
        failures.append("fields_invalid")
    if (
        reconstructed_request is not None
        and dict(value) != dict(reconstructed_request)
    ):
        failures.append("source_reconstruction_mismatch")
    if (
        not _is_digest(request_digest)
        or sha256_json(value) != request_digest
    ):
        failures.append("digest_mismatch")
    if (
        not _is_digest(request_control_digest)
        or _request_control_digest(value) != request_control_digest
    ):
        failures.append("control_digest_mismatch")
    if any(
        field != "aiApiKey" and _contains_redaction(item)
        for field, item in value.items()
    ):
        failures.append("unexpected_redaction")

    workspace = str(
        analysis_config.get("rag_workspace")
        or analysis_config.get("project_workspace")
        or ""
    )
    project = str(
        analysis_config.get("rag_project")
        or analysis_config.get("project_namespace")
        or ""
    )
    expected = {
        "projectId": analysis_config.get("project_id"),
        "projectVcsWorkspace": analysis_config.get(
            "project_vcs_workspace"
        ),
        "projectVcsRepoSlug": analysis_config.get(
            "project_vcs_repo_slug"
        ),
        "projectWorkspace": workspace,
        "projectNamespace": project,
        "aiProvider": run.get("analysisProvider"),
        "aiModel": run.get("analysisModel"),
        "aiApiKey": "<redacted>",
        "aiBaseUrl": analysis_config.get("base_url") or None,
        "aiCustomParameters": dict(
            analysis_config.get("custom_parameters") or {}
        ),
        "analysisType": "PR_REVIEW",
        "targetBranchName": case["replay"]["baseRef"],
        "sourceBranchName": case["replay"]["headRef"],
        "commitHash": case["snapshot"]["headSha"],
        "currentCommitHash": case["snapshot"]["headSha"],
        "baseCommitHash": case["snapshot"]["baseSha"],
        "prTitle": (
            f"Magento 2 review benchmark fixture {case['caseId']}"
        ),
        "prDescription": "",
        "prAuthor": "benchmark-fixture",
        "taskContext": {},
        "taskHistoryContext": "",
        "diffSnippets": [],
        "vcsProvider": "github",
        "analysisMode": "FULL",
        "previousCodeAnalysisIssues": [],
        "useMcpTools": False,
        "ragEnabled": True,
        "projectRules": "[]",
    }
    if any(
        value.get(field) != expected_value
        for field, expected_value in expected.items()
    ):
        failures.append("identity_or_configuration_mismatch")

    fork_parts = (
        fork_repository.split("/", 1)
        if isinstance(fork_repository, str)
        else []
    )
    if (
        len(fork_parts) != 2
        or not all(fork_parts)
        or analysis_config.get("project_vcs_workspace") != fork_parts[0]
        or analysis_config.get("project_vcs_repo_slug") != fork_parts[1]
        or value.get("projectVcsWorkspace") != fork_parts[0]
        or value.get("projectVcsRepoSlug") != fork_parts[1]
    ):
        failures.append("fork_repository_mismatch")
    pull_request_id = value.get("pullRequestId")
    if (
        isinstance(pull_request_id, bool)
        or not isinstance(pull_request_id, int)
        or pull_request_id < 1
        or not isinstance(replay_case, Mapping)
        or replay_case.get("caseId") != case.get("caseId")
        or replay_case.get("forkPrNumber") != pull_request_id
    ):
        failures.append("pull_request_identity_invalid")

    raw_diff = value.get("rawDiff")
    if (
        not isinstance(raw_diff, str)
        or sha256_text(raw_diff) != case["snapshot"]["diffSha256"]
    ):
        failures.append("diff_mismatch")
    changed = value.get("changedFiles")
    deleted = value.get("deletedFiles")
    if (
        not isinstance(changed, list)
        or not isinstance(deleted, list)
        or any(not isinstance(path, str) or not path for path in changed)
        or any(not isinstance(path, str) or not path for path in deleted)
        or changed != sorted(set(changed))
        or deleted != sorted(set(deleted))
        or set(changed).intersection(deleted)
        or sorted(set(changed).union(deleted))
        != case["snapshot"]["changedPaths"]
    ):
        failures.append("changed_paths_mismatch")

    max_file_bytes = analysis_config.get("max_enrichment_file_bytes")
    max_total_bytes = analysis_config.get("max_enrichment_total_bytes")
    if (
        not _is_int(max_file_bytes, minimum=1)
        or not _is_int(max_total_bytes, minimum=1)
    ):
        failures.append("enrichment_limits_invalid")
        max_file_bytes = 0
        max_total_bytes = 0

    enrichment = value.get("enrichmentData")
    if (
        not isinstance(enrichment, Mapping)
        or set(enrichment)
        != {"fileContents", "fileMetadata", "relationships", "stats"}
        or enrichment.get("fileMetadata") != []
        or enrichment.get("relationships") != []
    ):
        failures.append("enrichment_structure_invalid")
        return list(dict.fromkeys(failures))
    entries = enrichment.get("fileContents")
    if (
        not isinstance(entries, list)
        or any(
            not isinstance(entry, Mapping)
            or set(entry) != ENRICHMENT_ENTRY_FIELDS
            for entry in entries
        )
    ):
        failures.append("enrichment_entries_invalid")
        return list(dict.fromkeys(failures))
    if [entry.get("path") for entry in entries] != case["snapshot"][
        "changedPaths"
    ]:
        failures.append("enrichment_paths_mismatch")

    deleted_paths = set(deleted) if isinstance(deleted, list) else set()
    total_content_bytes = 0
    enriched_count = 0
    skip_reasons: Counter[str] = Counter()
    for entry in entries:
        path = entry.get("path")
        content = entry.get("content")
        size = entry.get("sizeBytes")
        skipped = entry.get("skipped")
        reason = entry.get("skipReason")
        if not isinstance(path, str) or not path:
            failures.append("enrichment_entry_invalid")
            continue
        if skipped is False:
            encoded_size = (
                len(content.encode("utf-8"))
                if isinstance(content, str)
                else None
            )
            if (
                encoded_size is None
                or not _is_int(size)
                or size != encoded_size
                or reason is not None
                or size > max_file_bytes
                or path in deleted_paths
            ):
                failures.append("enrichment_entry_invalid")
                continue
            enriched_count += 1
            total_content_bytes += size
        elif skipped is True:
            if (
                content is not None
                or size != 0
                or reason not in ENRICHMENT_SKIP_REASONS
                or (
                    path in deleted_paths
                    and reason != "deleted_file"
                )
                or (
                    path not in deleted_paths
                    and reason == "deleted_file"
                )
            ):
                failures.append("enrichment_entry_invalid")
                continue
            skip_reasons[str(reason)] += 1
        else:
            failures.append("enrichment_entry_invalid")
    if total_content_bytes > max_total_bytes:
        failures.append("enrichment_total_limit_exceeded")

    stats = enrichment.get("stats")
    expected_stats = {
        "totalFilesRequested": len(entries),
        "filesEnriched": enriched_count,
        "filesSkipped": len(entries) - enriched_count,
        "relationshipsFound": 0,
        "totalContentSizeBytes": total_content_bytes,
        "processingTimeMs": 0,
        "skipReasons": dict(sorted(skip_reasons.items())),
    }
    if (
        not isinstance(stats, Mapping)
        or set(stats) != ENRICHMENT_STATS_FIELDS
        or dict(stats) != expected_stats
    ):
        failures.append("enrichment_stats_mismatch")
    return list(dict.fromkeys(failures))


def _retrieval_failures(
    value: Any,
    *,
    raw: Mapping[str, Any] | None,
    case: Mapping[str, Any],
    index_receipt: Any,
) -> list[str]:
    if not isinstance(value, Mapping):
        return ["missing"]
    failures = []
    review_units = value.get("reviewUnits")
    retrieval = value.get("retrieval")
    revision_binding = value.get("revisionBinding")
    if value.get("state") != "review_evidence_completed":
        failures.append("state_invalid")
    if not isinstance(review_units, Mapping):
        failures.append("review_units_invalid")
    else:
        registered = review_units.get("registered")
        completed = review_units.get("completed")
        if (
            not _is_int(registered, minimum=1)
            or not _is_int(completed)
            or completed != registered
        ):
            failures.append("review_units_incomplete")
    if not isinstance(retrieval, Mapping):
        failures.append("summary_invalid")
    else:
        states = retrieval.get("deterministicStates")
        if (
            not isinstance(states, list)
            or not states
            or any(state != "complete" for state in states)
        ):
            failures.append("deterministic_states_incomplete")
        semantic_failures = retrieval.get("semanticFailures")
        if (
            not _is_int(semantic_failures)
            or semantic_failures != 0
        ):
            failures.append("semantic_failures_nonzero")
        if retrieval.get("semanticDisabled") is not False:
            failures.append("semantic_disabled")
        if not _is_int(retrieval.get("exactEvidenceIds")):
            failures.append("exact_evidence_count_invalid")
    expected_base_manifest = (
        index_receipt.get("generation_manifest_sha256")
        if isinstance(index_receipt, Mapping)
        else None
    )
    if not isinstance(revision_binding, Mapping):
        failures.append("revision_binding_invalid")
    else:
        expected_revision_binding = {
            "prIndexed": True,
            "pullRequestId": case["replay"]["forkPrNumber"],
            "targetBranch": case["replay"]["baseRef"],
            "sourceRevision": case["snapshot"]["headSha"],
            "baseRevision": case["snapshot"]["baseSha"],
            "baseGenerationManifestSha256": expected_base_manifest,
            "basePluginFingerprint": (
                index_receipt.get("plugin_fingerprint")
                if isinstance(index_receipt, Mapping)
                else None
            ),
            "basePluginDescriptorFingerprint": (
                index_receipt.get("plugin_descriptor_fingerprint")
                if isinstance(index_receipt, Mapping)
                else None
            ),
            "basePluginImplementationFingerprint": (
                index_receipt.get("plugin_implementation_fingerprint")
                if isinstance(index_receipt, Mapping)
                else None
            ),
            "baseIndexRepresentationFingerprint": (
                index_receipt.get("index_representation_fingerprint")
                if isinstance(index_receipt, Mapping)
                else None
            ),
        }
        if any(
            revision_binding.get(field) != expected
            for field, expected in expected_revision_binding.items()
        ):
            failures.append("revision_binding_mismatch")
        if (
            not isinstance(
                revision_binding.get("prGenerationFingerprint"), str
            )
            or SHA256_FINGERPRINT.fullmatch(
                revision_binding["prGenerationFingerprint"]
            )
            is None
        ):
            failures.append("pr_generation_fingerprint_invalid")
        if not _is_digest(
            revision_binding.get(
                "prOverlayGenerationManifestSha256"
            )
        ):
            failures.append("pr_overlay_generation_manifest_invalid")
    if not _is_digest(value.get("evidenceSha256")):
        failures.append("evidence_digest_invalid")

    events = raw.get("events") if isinstance(raw, Mapping) else None
    if not isinstance(events, list) or any(
        not isinstance(event, Mapping) for event in events
    ):
        failures.append("raw_events_invalid")
        return failures
    terminal = [
        event
        for event in events
        if event.get("type") == "status"
        and event.get("state") == "review_evidence_completed"
    ]
    if len(terminal) != 1:
        failures.append("terminal_event_count_invalid")
        return failures
    event = terminal[0]
    event_review_units = event.get("reviewUnits")
    event_retrieval = event.get("retrieval")
    event_revision_binding = event.get("revisionBinding")
    expected = (
        {
            "state": "review_evidence_completed",
            "reviewUnits": {
                "registered": event_review_units.get("registered"),
                "completed": event_review_units.get("completed"),
            },
            "retrieval": {
                "deterministicStates": list(
                    event_retrieval.get("deterministicStates")
                ),
                "semanticFailures": event_retrieval.get(
                    "semanticFailures"
                ),
                "semanticDisabled": event_retrieval.get(
                    "semanticDisabled"
                ),
                "exactEvidenceIds": event_retrieval.get(
                    "exactEvidenceIds"
                ),
            },
            "revisionBinding": {
                "prIndexed": event_revision_binding.get("prIndexed"),
                "pullRequestId": event_revision_binding.get("pullRequestId"),
                "targetBranch": event_revision_binding.get("targetBranch"),
                "sourceRevision": event_revision_binding.get(
                    "sourceRevision"
                ),
                "baseRevision": event_revision_binding.get("baseRevision"),
                "baseGenerationManifestSha256": event_revision_binding.get(
                    "baseGenerationManifestSha256"
                ),
                "prGenerationFingerprint": event_revision_binding.get(
                    "prGenerationFingerprint"
                ),
                "prOverlayGenerationManifestSha256": (
                    event_revision_binding.get(
                        "prOverlayGenerationManifestSha256"
                    )
                ),
                "basePluginFingerprint": event_revision_binding.get(
                    "basePluginFingerprint"
                ),
                "basePluginDescriptorFingerprint": (
                    event_revision_binding.get(
                        "basePluginDescriptorFingerprint"
                    )
                ),
                "basePluginImplementationFingerprint": (
                    event_revision_binding.get(
                        "basePluginImplementationFingerprint"
                    )
                ),
                "baseIndexRepresentationFingerprint": (
                    event_revision_binding.get(
                        "baseIndexRepresentationFingerprint"
                    )
                ),
            },
            "evidenceSha256": sha256_json(event),
        }
        if isinstance(event_review_units, Mapping)
        and isinstance(event_retrieval, Mapping)
        and isinstance(event_retrieval.get("deterministicStates"), list)
        and isinstance(event_revision_binding, Mapping)
        else None
    )
    if expected is None or value != expected:
        failures.append("terminal_event_binding_mismatch")
    return failures


def _event_stream_failures(
    raw: Mapping[str, Any] | None,
    *,
    expected_job_id: Any,
) -> list[str]:
    if not isinstance(raw, Mapping):
        return ["missing"]
    failures = []
    if (
        not isinstance(expected_job_id, str)
        or not expected_job_id
        or raw.get("jobId") != expected_job_id
    ):
        failures.append("job_binding_mismatch")
    events = raw.get("events")
    if not isinstance(events, list) or any(
        not isinstance(event, Mapping) for event in events
    ):
        return [*failures, "invalid"]
    if any(event.get("type") == "error" for event in events):
        failures.append("contains_error")
    final_indexes = [
        index
        for index, event in enumerate(events)
        if event.get("type") == "final"
    ]
    if len(final_indexes) != 1:
        failures.append("final_count_invalid")
    else:
        final = events[final_indexes[0]]
        if final_indexes[0] != len(events) - 1:
            failures.append("final_not_terminal")
        if final.get("result") != raw.get("response"):
            failures.append("final_response_mismatch")
        if any(
            final.get(field) != expected_job_id
            for field in ("jobId", "job_id")
            if field in final
        ):
            failures.append("final_job_binding_mismatch")
    return failures


def _product_finalization_failures(
    value: Any,
    *,
    findings: Any,
    raw: Mapping[str, Any] | None,
) -> list[str]:
    if not isinstance(value, Mapping):
        return ["missing"]
    failures = []
    expected_state = {
        "kind": "codecrow-isolated-analysis-finalization",
        "analysisDataValidated": True,
        "persisted": False,
        "published": False,
        "previousIssueStateUsed": False,
    }
    if (
        value.get("kind") != expected_state["kind"]
        or value.get("analysisDataValidated") is not True
        or value.get("persisted") is not False
        or value.get("published") is not False
        or value.get("previousIssueStateUsed") is not False
    ):
        failures.append("state_ineligible")
    raw_count = value.get("rawIssueCount")
    final_count = value.get("finalIssueCount")
    if not _is_int(raw_count) or not _is_int(final_count):
        failures.append("counts_invalid")
    if not _is_digest(value.get("responseDigest")):
        failures.append("response_digest_invalid")
    if not isinstance(findings, list) or any(
        not isinstance(finding, Mapping) for finding in findings
    ):
        failures.append("findings_invalid")
    elif _is_int(final_count) and final_count != len(findings):
        failures.append("final_count_mismatch")

    raw_finalization = (
        raw.get("productFinalization")
        if isinstance(raw, Mapping)
        else None
    )
    if not isinstance(raw_finalization, Mapping):
        failures.append("raw_response_missing")
        return failures
    if (
        raw_finalization.get("kind") != expected_state["kind"]
        or raw_finalization.get("analysisDataValidated") is not True
        or raw_finalization.get("persisted") is not False
        or raw_finalization.get("published") is not False
        or raw_finalization.get("previousIssueStateUsed") is not False
    ):
        failures.append("raw_state_ineligible")
    raw_issues = raw_finalization.get("issues")
    if not isinstance(raw_issues, list) or any(
        not isinstance(issue, Mapping) for issue in raw_issues
    ):
        failures.append("raw_issues_invalid")
    elif _is_int(final_count) and final_count != len(raw_issues):
        failures.append("raw_final_count_mismatch")
    try:
        analysis_issues = _analysis_result(raw.get("response"))["issues"]
    except (RuntimeError, TypeError, KeyError):
        analysis_issues = None
        failures.append("analysis_response_invalid")
    if (
        not isinstance(analysis_issues, list)
        or any(
            not isinstance(issue, Mapping)
            for issue in analysis_issues
        )
    ):
        if "analysis_response_invalid" not in failures:
            failures.append("analysis_response_invalid")
    elif _is_int(raw_count) and raw_count != len(analysis_issues):
        failures.append("raw_count_mismatch")
    if (
        raw_finalization.get("rawIssueCount") != raw_count
        or raw_finalization.get("finalIssueCount") != final_count
    ):
        failures.append("manifest_count_binding_mismatch")
    response_digest = sha256_json(raw_finalization)
    if value.get("responseDigest") != response_digest:
        failures.append("response_digest_mismatch")
    expected_projection = {
        **expected_state,
        "rawIssueCount": raw_count,
        "finalIssueCount": final_count,
        "responseDigest": response_digest,
    }
    if value != expected_projection:
        failures.append("manifest_projection_mismatch")
    try:
        normalized = _findings(raw_finalization)
    except RuntimeError:
        normalized = None
        failures.append("normalized_findings_invalid")
    if normalized is not None and findings != normalized:
        failures.append("normalized_findings_mismatch")
    return failures


def _paper_run_failures(
    run: Mapping[str, Any],
    *,
    corpus_cases: Mapping[str, Mapping[str, Any]],
    artifact_root: Path | None,
    repository_path: Path | None = None,
    require_request_source_reconstruction: bool = False,
) -> list[str]:
    failures = []
    if run.get("status") != "completed" or not run.get("completedAt"):
        failures.append("analysis_run_not_completed")
    try:
        run_started_at = _utc_datetime(run.get("startedAt"))
        run_completed_at = _utc_datetime(run.get("completedAt"))
    except (TypeError, ValueError):
        run_started_at = None
        run_completed_at = None
        failures.append("analysis_run_timestamps_invalid")
    if (
        run_started_at is not None
        and run_completed_at is not None
        and run_completed_at < run_started_at
    ):
        failures.append("analysis_run_timestamp_order_invalid")
    if run.get("transport") != "redis":
        failures.append("analysis_transport_not_production_redis")
    if (
        run.get("findingSemantics")
        != "java-finalized-transient-first-iteration"
    ):
        failures.append("findings_not_product_finalized")

    analysis_config = run.get("analysisConfig")
    if not isinstance(analysis_config, Mapping):
        analysis_config = {}
        failures.append("analysis_config_missing")
    elif (
        not _is_digest(run.get("analysisConfigDigest"))
        or run.get("analysisConfigDigest") != sha256_json(analysis_config)
    ):
        failures.append("analysis_config_digest_mismatch")
    if _contains_redaction(analysis_config):
        failures.append("analysis_config_contains_redaction")
    max_attestation_age = analysis_config.get(
        "replay_attestation_max_age_seconds"
    )
    if (
        isinstance(max_attestation_age, bool)
        or not isinstance(max_attestation_age, (int, float))
        or not math.isfinite(float(max_attestation_age))
        or max_attestation_age <= 0
        or max_attestation_age > MAX_PAPER_ATTESTATION_AGE_SECONDS
    ):
        failures.append("replay_attestation_max_age_invalid")
    for field in (
        "require_exact_index",
        "require_retrieval_evidence",
        "require_runtime_provenance",
        "require_model_call_evidence",
    ):
        if analysis_config.get(field) is not True:
            failures.append(f"analysis_config_{field}_not_required")
    if (
        not _is_int(analysis_config.get("project_id"), minimum=1)
        or any(
            not isinstance(analysis_config.get(field), str)
            or not analysis_config[field].strip()
            for field in (
                "project_vcs_workspace",
                "project_vcs_repo_slug",
                "project_workspace",
                "project_namespace",
            )
        )
    ):
        failures.append("analysis_project_identity_missing")
    provider = run.get("analysisProvider")
    if (
        not isinstance(provider, str)
        or not provider.strip()
        or provider != analysis_config.get("provider")
    ):
        failures.append("analysis_provider_mismatch")
    requested_model = analysis_config.get("model")
    expected_response_model = analysis_config.get(
        "expected_response_model"
    )
    if (
        not isinstance(requested_model, str)
        or not requested_model.strip()
        or requested_model != run.get("analysisModel")
    ):
        failures.append("analysis_requested_model_mismatch")
    if (
        not isinstance(expected_response_model, str)
        or not expected_response_model.strip()
    ):
        failures.append("analysis_expected_response_model_missing")
    model_roles = run.get("analysisModelRoles")
    if (
        not isinstance(model_roles, Mapping)
        or model_roles.get("reviewPipeline") != requested_model
        or model_roles.get("reviewPipelineRequested") != requested_model
        or model_roles.get("reviewPipelineExpectedResponse")
        != expected_response_model
        or model_roles.get("reviewPipelineProviderReported")
        != [expected_response_model]
    ):
        failures.append("analysis_model_role_mismatch")
    stage_model_roles = (
        model_roles.get("providerReportedByStage")
        if isinstance(model_roles, Mapping)
        else None
    )
    if (
        not isinstance(stage_model_roles, Mapping)
        or not stage_model_roles
        or any(
            not isinstance(stage, str)
            or not stage.strip()
            or models != [expected_response_model]
            for stage, models in stage_model_roles.items()
        )
    ):
        failures.append("analysis_stage_model_roles_invalid")
    source_repository_available = (
        repository_path is not None
        and is_local_git_repository(repository_path)
    )
    if (
        require_request_source_reconstruction
        and not source_repository_available
    ):
        failures.append("analysis_source_repository_missing")

    corpus_case_ids = set(corpus_cases)
    replay_lock = _safe_raw_artifact(
        artifact_root,
        run.get("replayLockArtifact"),
    )
    replay_cases: dict[str, Mapping[str, Any]] = {}
    fork_repository: str | None = None
    if not isinstance(replay_lock, Mapping):
        failures.append("replay_lock_artifact_missing")
    else:
        lock_digest_value = dict(replay_lock)
        declared_lock_digest = lock_digest_value.pop("lockDigest", None)
        if (
            not _is_digest(declared_lock_digest)
            or declared_lock_digest != sha256_json(lock_digest_value)
            or declared_lock_digest != run.get("replayLockDigest")
        ):
            failures.append("replay_lock_artifact_invalid")
        fork_value = replay_lock.get("forkRepository")
        if (
            not isinstance(fork_value, str)
            or len(fork_value.split("/", 1)) != 2
            or not all(fork_value.split("/", 1))
        ):
            failures.append("replay_fork_repository_invalid")
        else:
            fork_repository = fork_value
        replay_values = replay_lock.get("cases")
        if not isinstance(replay_values, list) or any(
            not isinstance(item, Mapping) for item in replay_values
        ):
            failures.append("replay_lock_cases_invalid")
        else:
            replay_ids = [
                str(item.get("caseId") or "") for item in replay_values
            ]
            replay_cases = {
                case_id: item
                for case_id, item in zip(
                    replay_ids,
                    replay_values,
                    strict=True,
                )
            }
            if (
                len(replay_ids) != len(set(replay_ids))
                or set(replay_cases) != corpus_case_ids
            ):
                failures.append("replay_lock_cases_invalid")
            for case_id in corpus_case_ids.intersection(replay_cases):
                observed = replay_cases[case_id]
                corpus_case = corpus_cases[case_id]
                expected = {
                    "baseRef": corpus_case["replay"]["baseRef"],
                    "baseSha": corpus_case["snapshot"]["baseSha"],
                    "headRef": corpus_case["replay"]["headRef"],
                    "headSha": corpus_case["snapshot"]["headSha"],
                }
                if any(
                    observed.get(field) != value
                    for field, value in expected.items()
                ):
                    failures.append(
                        f"{case_id}:replay_lock_case_binding_mismatch"
                    )

    replay_attestation = _safe_raw_artifact(
        artifact_root,
        run.get("replayAttestationArtifact"),
    )
    if not isinstance(replay_attestation, Mapping):
        failures.append("replay_attestation_artifact_missing")
    else:
        attestation_digest_value = dict(replay_attestation)
        declared_attestation_digest = attestation_digest_value.pop(
            "attestationDigest",
            None,
        )
        if (
            not _is_digest(declared_attestation_digest)
            or declared_attestation_digest
            != sha256_json(attestation_digest_value)
            or declared_attestation_digest
            != run.get("replayAttestationDigest")
            or replay_attestation.get("replayLockDigest")
            != run.get("replayLockDigest")
        ):
            failures.append("replay_attestation_artifact_invalid")
        if run_started_at is not None:
            try:
                validate_replay_attestation_freshness(
                    replay_attestation,
                    reference_at=run.get("startedAt"),
                    max_age_seconds=max_attestation_age,
                )
            except (TypeError, ValueError):
                failures.append("replay_attestation_not_fresh")

    selected = run.get("selectedCaseIds")
    if (
        not isinstance(selected, list)
        or len(selected) != 50
        or any(not isinstance(case_id, str) for case_id in selected)
        or len(selected) != len(set(selected))
        or set(selected) != corpus_case_ids
        or selected != list(corpus_cases)
    ):
        failures.append("analysis_case_selection_incomplete")
    runtime = run.get("runtimeProvenance")
    if not isinstance(runtime, Mapping) or runtime.get("required") is not True:
        failures.append("immutable_runtime_provenance_not_required")
    else:
        for service in ("analysis", "rag", "finalizer"):
            identity = runtime.get(service)
            if (
                not isinstance(identity, Mapping)
                or not isinstance(identity.get("containerId"), str)
                or CONTAINER_ID.fullmatch(identity["containerId"]) is None
                or not isinstance(identity.get("imageId"), str)
                or SHA256_FINGERPRINT.fullmatch(identity["imageId"]) is None
                or not isinstance(identity.get("imageReference"), str)
                or not identity["imageReference"].strip()
            ):
                failures.append(f"missing_{service}_runtime_identity")

    run_values = run.get("cases")
    if not isinstance(run_values, list) or any(
        not isinstance(item, Mapping) for item in run_values
    ):
        run_values = []
        failures.append("analysis_case_artifacts_invalid")
    run_case_ids = [str(item.get("caseId") or "") for item in run_values]
    run_cases = {
        case_id: item
        for case_id, item in zip(run_case_ids, run_values, strict=True)
    }
    if (
        len(run_values) != 50
        or len(run_case_ids) != len(set(run_case_ids))
        or set(run_cases) != corpus_case_ids
    ):
        failures.append("analysis_case_artifacts_incomplete")

    attempts_by_case: dict[str, list[dict[str, Any]]] = {}
    max_case_attempts = analysis_config.get("max_case_attempts")
    if (
        not _is_int(max_case_attempts, minimum=1)
        or max_case_attempts > 100
    ):
        failures.append("analysis_attempt_policy_invalid")
    elif artifact_root is None:
        failures.append("analysis_attempt_ledger_invalid")
    else:
        try:
            attempts_by_case = _validate_attempt_ledger(
                run,
                output_dir=artifact_root,
                selected_ids=list(corpus_cases),
                max_case_attempts=max_case_attempts,
                allow_running=False,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            failures.append("analysis_attempt_ledger_invalid")

    before = run.get("indexReceiptsBefore")
    after = run.get("indexReceiptsAfter")
    if not isinstance(before, Mapping) or set(before) != corpus_case_ids:
        failures.append("index_receipts_before_incomplete")
        before = {}
    if not isinstance(after, Mapping) or set(after) != corpus_case_ids:
        failures.append("index_receipts_after_incomplete")
        after = {}
    if before != after:
        failures.append("index_receipts_changed_during_run")

    for case_id in sorted(corpus_case_ids.intersection(run_cases)):
        case = run_cases[case_id]
        source_corpus_case = corpus_cases[case_id]
        locked_replay_case = replay_cases.get(case_id)
        corpus_case = {
            **source_corpus_case,
            "replay": {
                **source_corpus_case["replay"],
                "forkPrNumber": (
                    locked_replay_case.get("forkPrNumber")
                    if isinstance(locked_replay_case, Mapping)
                    else None
                ),
            },
        }
        if case.get("status") != "completed":
            failures.append(f"{case_id}:analysis_failed")
        if (
            case.get("sizeBand") != corpus_case.get("sizeBand")
            or case.get("partition") != corpus_case.get("partition")
        ):
            failures.append(f"{case_id}:case_metadata_drift")
        for field in (
            "requestDigest",
            "requestControlDigest",
            "responseDigest",
        ):
            if not _is_digest(case.get(field)):
                failures.append(f"{case_id}:{field}_invalid")
        if case.get("error") is not None:
            failures.append(f"{case_id}:completed_case_has_error")

        raw = _safe_raw_artifact(artifact_root, case.get("rawResponse"))
        if raw is None:
            failures.append(f"{case_id}:raw_artifact_missing")
        elif case.get("responseDigest") != sha256_json(raw):
            failures.append(f"{case_id}:raw_artifact_digest_mismatch")
        model_call_evidence = case.get("modelCallEvidence")
        case_attempts = attempts_by_case.get(case_id, [])
        terminal_attempt = case_attempts[-1] if case_attempts else None
        if (
            not isinstance(terminal_attempt, Mapping)
            or terminal_attempt.get("status") != "completed"
            or terminal_attempt.get("resultArtifact")
            != case.get("rawResponse")
            or terminal_attempt.get("resultArtifactDigest")
            != case.get("responseDigest")
            or terminal_attempt.get("modelCallEvidence")
            != model_call_evidence
        ):
            failures.append(
                f"{case_id}:model_call_evidence_attempt_binding_mismatch"
            )
        if (
            raw is None
            or raw.get("modelCallEvidence") != model_call_evidence
        ):
            failures.append(
                f"{case_id}:model_call_evidence_result_binding_mismatch"
            )
        if artifact_root is None or raw is None:
            failures.append(f"{case_id}:model_call_evidence_missing")
        else:
            try:
                _validate_bound_model_call_evidence(
                    model_call_evidence,
                    output_dir=artifact_root,
                    config=analysis_config,
                    pull_request_id=(
                        int(locked_replay_case["forkPrNumber"])
                        if isinstance(locked_replay_case, Mapping)
                        and _is_int(
                            locked_replay_case.get("forkPrNumber"),
                            minimum=1,
                        )
                        else None
                    ),
                    expected_request=(
                        raw.get("redactedRequest")
                        if isinstance(raw.get("redactedRequest"), Mapping)
                        else {}
                    ),
                    require_present=True,
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                failures.append(
                    f"{case_id}:model_call_evidence_invalid"
                )
        reconstructed_request = None
        if (
            require_request_source_reconstruction
            and source_repository_available
            and isinstance(locked_replay_case, Mapping)
        ):
            try:
                reconstructed_request = _redacted_request(
                    _request_payload(
                        config=analysis_config,
                        case=source_corpus_case,
                        replay=locked_replay_case,
                        repository=repository_path,
                        model=str(run.get("analysisModel") or ""),
                        api_key="benchmark-request-reconstruction-secret",
                    )
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                failures.append(
                    f"{case_id}:request_source_reconstruction_failed"
                )
        for failure in _request_failures(
            raw.get("redactedRequest") if raw is not None else None,
            case=corpus_case,
            run=run,
            analysis_config=analysis_config,
            request_digest=case.get("requestDigest"),
            request_control_digest=case.get("requestControlDigest"),
            replay_case=replay_cases.get(case_id),
            fork_repository=fork_repository,
            reconstructed_request=reconstructed_request,
        ):
            failures.append(f"{case_id}:request_{failure}")
        for failure in _event_stream_failures(
            raw,
            expected_job_id=case.get("jobId"),
        ):
            failures.append(f"{case_id}:event_stream_{failure}")
        for failure in _retrieval_failures(
            case.get("retrievalEvidence"),
            raw=raw,
            case=corpus_case,
            index_receipt=case.get("indexReceipt"),
        ):
            failures.append(f"{case_id}:retrieval_{failure}")
        for failure in _product_finalization_failures(
            case.get("productFinalization"),
            findings=case.get("findings"),
            raw=raw,
        ):
            failures.append(f"{case_id}:product_finalization_{failure}")

        case_receipt = case.get("indexReceipt")
        before_receipt = before.get(case_id)
        after_receipt = after.get(case_id)
        if (
            case_receipt != before_receipt
            or case_receipt != after_receipt
        ):
            failures.append(f"{case_id}:index_receipt_binding_mismatch")
        for phase, receipt in (
            ("case", case_receipt),
            ("before", before_receipt),
            ("after", after_receipt),
        ):
            for failure in _receipt_failures(
                receipt,
                case=corpus_case,
                analysis_config=analysis_config,
            ):
                failures.append(
                    f"{case_id}:index_receipt_{phase}_{failure}"
                )
    return list(dict.fromkeys(failures))


def _novel_counts(case: Mapping[str, Any]) -> dict[str, int]:
    counts = Counter(
        str(item.get("verdict"))
        for item in case.get("novelFindingJudgments") or []
        if isinstance(item, Mapping)
    )
    unmatched = len(case.get("unmatchedCandidates") or [])
    judged = sum(counts.values())
    return {
        "validInScopeNovel": counts["valid_in_scope_novel"],
        "invalid": counts["invalid"],
        "outOfScope": counts["out_of_scope"],
        "unverifiable": counts["unverifiable"],
        "unadjudicated": max(0, unmatched - judged),
    }


def _case_row(
    case: Mapping[str, Any],
    *,
    corpus_case: Mapping[str, Any],
    analysis_case: Mapping[str, Any] | None,
) -> dict[str, Any]:
    gold_count = int(case.get("goldCount", 0))
    candidate_count = int(case.get("candidateCount", 0))
    expected_gold_count = len(corpus_case["goldenComments"])
    if gold_count != expected_gold_count:
        raise ValueError(
            f"{case.get('caseId')}: goldCount {gold_count} does not match "
            f"corpus count {expected_gold_count}"
        )
    gold_issues = case.get("goldIssues")
    candidate_findings = case.get("candidateFindings")
    if not isinstance(gold_issues, list) or len(gold_issues) != gold_count:
        raise ValueError(
            f"{case.get('caseId')}: goldIssues does not match goldCount"
        )
    if (
        not isinstance(candidate_findings, list)
        or len(candidate_findings) != candidate_count
    ):
        raise ValueError(
            f"{case.get('caseId')}: candidateFindings does not match candidateCount"
        )
    assignments = case.get("assignments") or []
    if not isinstance(assignments, list):
        raise ValueError(f"{case.get('caseId')}: assignments must be an array")
    tp = len(assignments)
    if tp > min(gold_count, candidate_count):
        raise ValueError(f"{case.get('caseId')}: impossible assignment count")
    gold_ids = [f"G{index:03d}" for index in range(1, gold_count + 1)]
    candidate_ids = [
        f"C{index:03d}" for index in range(1, candidate_count + 1)
    ]
    if [str(item.get("goldId") or "") for item in gold_issues] != gold_ids:
        raise ValueError(
            f"{case.get('caseId')}: goldIssues IDs are incomplete or out of order"
        )
    if [
        str(item.get("candidateId") or "") for item in candidate_findings
    ] != candidate_ids:
        raise ValueError(
            f"{case.get('caseId')}: candidateFinding IDs are incomplete or out of order"
        )
    for index, (gold_issue, source_gold) in enumerate(
        zip(gold_issues, corpus_case["goldenComments"], strict=True),
        start=1,
    ):
        expected = {
            "goldId": f"G{index:03d}",
            "sourceId": source_gold["id"],
            "sourceUrl": source_gold["sourceUrl"],
            "path": source_gold["path"],
            "line": source_gold["originalLine"],
            "reviewComment": source_gold["body"],
            "summary": source_gold["expectedIssue"]["summary"],
            "category": source_gold["expectedIssue"]["category"],
            "severity": source_gold["expectedIssue"]["severity"],
        }
        if not isinstance(gold_issue, Mapping) or any(
            gold_issue.get(key) != value for key, value in expected.items()
        ):
            raise ValueError(
                f"{case.get('caseId')}: gold issue G{index:03d} drifted "
                "from the released corpus"
            )
    if analysis_case is not None:
        if analysis_case.get("status") != "completed":
            raise ValueError(
                f"{case.get('caseId')}: scored case is not completed in "
                "the bound analysis run"
            )
        expected_candidates = [
            {
                "candidateId": f"C{index:03d}",
                **{
                    key: value
                    for key, value in finding.items()
                    if key != "raw"
                },
            }
            for index, finding in enumerate(
                analysis_case.get("findings") or [],
                start=1,
            )
            if isinstance(finding, Mapping)
        ]
        if candidate_findings != expected_candidates:
            raise ValueError(
                f"{case.get('caseId')}: candidate findings drifted from "
                "the bound analysis run"
            )
    pair_judgments = case.get("pairJudgments")
    if not isinstance(pair_judgments, list):
        raise ValueError(
            f"{case.get('caseId')}: pairJudgments must be an array"
        )
    pair_by_edge: dict[tuple[str, str], Mapping[str, Any]] = {}
    for judgment in pair_judgments:
        if not isinstance(judgment, Mapping):
            raise ValueError(
                f"{case.get('caseId')}: pair judgment must be an object"
            )
        edge = (
            str(judgment.get("goldId") or ""),
            str(judgment.get("candidateId") or ""),
        )
        if (
            edge[0] not in gold_ids
            or edge[1] not in candidate_ids
            or edge in pair_by_edge
        ):
            raise ValueError(
                f"{case.get('caseId')}: pairJudgments contain an unknown or "
                "duplicate edge"
            )
        pair_by_edge[edge] = judgment
    expected_edges = {
        (gold_id, candidate_id)
        for gold_id in gold_ids
        for candidate_id in candidate_ids
    }
    if set(pair_by_edge) != expected_edges:
        raise ValueError(
            f"{case.get('caseId')}: pairJudgments do not cover every pair"
        )
    assigned_gold = []
    assigned_candidates = []
    assigned_edges = []
    for assignment in assignments:
        if not isinstance(assignment, Mapping):
            raise ValueError(
                f"{case.get('caseId')}: assignment must be an object"
            )
        gold_id = str(assignment.get("goldId") or "")
        candidate_id = str(assignment.get("candidateId") or "")
        if gold_id not in gold_ids or candidate_id not in candidate_ids:
            raise ValueError(
                f"{case.get('caseId')}: assignment references an unknown ID"
            )
        assigned_gold.append(gold_id)
        assigned_candidates.append(candidate_id)
        assigned_edges.append((gold_id, candidate_id))
    if len(assigned_gold) != len(set(assigned_gold)):
        raise ValueError(
            f"{case.get('caseId')}: duplicate gold violates one-to-one assignment"
        )
    if len(assigned_candidates) != len(set(assigned_candidates)):
        raise ValueError(
            f"{case.get('caseId')}: duplicate candidate violates one-to-one assignment"
        )
    for edge in assigned_edges:
        judgment = pair_by_edge.get(edge)
        if judgment is None or not (
            judgment.get("verdict") == "substantive_match"
            and all(
                judgment.get(field) == "yes"
                for field in (
                    "specific_issue",
                    "grounded_at_snapshot",
                    "same_root_cause",
                    "same_failure_or_consequence",
                    "compatible_required_change",
                )
            )
            and judgment.get("location_relation")
            not in {"unrelated", "unclear"}
        ):
            raise ValueError(
                f"{case.get('caseId')}: assignment is not backed by an "
                "eligible substantive pair judgment"
            )
    expected_unmatched_gold = sorted(set(gold_ids) - set(assigned_gold))
    expected_unmatched_candidates = sorted(
        set(candidate_ids) - set(assigned_candidates)
    )
    if sorted(case.get("unmatchedGold") or []) != expected_unmatched_gold:
        raise ValueError(
            f"{case.get('caseId')}: unmatchedGold does not match assignments"
        )
    if (
        sorted(case.get("unmatchedCandidates") or [])
        != expected_unmatched_candidates
    ):
        raise ValueError(
            f"{case.get('caseId')}: unmatchedCandidates does not match assignments"
        )
    novel_ids = []
    for novel in case.get("novelFindingJudgments") or []:
        if (
            not isinstance(novel, Mapping)
            or novel.get("verdict")
            not in {
                "valid_in_scope_novel",
                "invalid",
                "out_of_scope",
                "unverifiable",
            }
        ):
            raise ValueError(
                f"{case.get('caseId')}: invalid novel-finding judgment"
            )
        if novel.get("verdict") == "valid_in_scope_novel" and (
            novel.get("grounded_at_snapshot") != "yes"
            or novel.get("actionable") != "yes"
        ):
            raise ValueError(
                f"{case.get('caseId')}: valid novel finding is not grounded "
                "and actionable"
            )
        candidate_id = str(novel.get("candidateId") or "")
        if candidate_id not in expected_unmatched_candidates:
            raise ValueError(
                f"{case.get('caseId')}: novel judgment is not for an unmatched "
                "candidate"
            )
        novel_ids.append(candidate_id)
    if len(novel_ids) != len(set(novel_ids)):
        raise ValueError(
            f"{case.get('caseId')}: duplicate novel-finding judgment"
        )
    primary = _counts_metrics(
        {
            "truePositive": tp,
            "falseNegative": gold_count - tp,
            "referenceSetFalsePositive": candidate_count - tp,
        }
    )
    novel = _novel_counts(case)
    confirmed_correct = tp + novel["validInScopeNovel"]
    confirmed_denominator = confirmed_correct + novel["invalid"]
    adjudicated = {
        "matchedReviewerIssues": tp,
        **novel,
        "confirmedCorrectFindings": confirmed_correct,
        "confirmedFindingPrecision": _ratio(
            confirmed_correct,
            confirmed_denominator,
        ),
        "precisionDenominator": confirmed_denominator,
        "reviewerIssueRecall": primary["recall"],
        "note": (
            "Confirmed-finding precision excludes out-of-scope, unverifiable, "
            "and unadjudicated findings. It is not an expanded-gold F1 score."
        ),
    }
    matched_gold = {
        str(item.get("goldId"))
        for item in assignments
        if isinstance(item, Mapping)
    }
    gold_outcomes = []
    for gold in gold_issues:
        if not isinstance(gold, Mapping):
            continue
        gold_outcomes.append(
            {
                **dict(gold),
                "matched": str(gold.get("goldId")) in matched_gold,
            }
        )
    source = corpus_case["sourcePr"]
    return {
        "caseId": case["caseId"],
        "sourcePr": {
            "number": source["number"],
            "url": source["url"],
            "title": source["title"],
        },
        "sizeBand": corpus_case["sizeBand"],
        "partition": corpus_case["partition"],
        "status": "scored",
        "primary": primary,
        "adjudicated": adjudicated,
        "goldIssues": gold_outcomes,
        "candidateFindings": list(case.get("candidateFindings") or []),
        "assignments": assignments,
        "unmatchedGold": list(case.get("unmatchedGold") or []),
        "unmatchedCandidates": list(case.get("unmatchedCandidates") or []),
        "novelFindingJudgments": list(
            case.get("novelFindingJudgments") or []
        ),
    }


def _group_primary(
    rows: Sequence[Mapping[str, Any]],
    key: Callable[[Mapping[str, Any]], str],
    *,
    iterations: int,
    seed: int,
    label: str,
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return {
        name: _aggregate_primary(
            values,
            iterations=iterations,
            seed=seed,
            label=f"{label}:{name}",
        )
        for name, values in sorted(groups.items())
    }


def _gold_recall_strata(
    rows: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    matched: Counter[str] = Counter()
    for row in rows:
        for gold in row["goldIssues"]:
            value = str(gold.get(field) or "unknown")
            totals[value] += 1
            if gold["matched"]:
                matched[value] += 1
    return {
        value: {
            "matchedReviewerIssues": matched[value],
            "reviewerIssues": total,
            "reviewerIssueRecall": _ratio(matched[value], total),
            "note": (
                "Candidate false positives have no reviewer category/severity; "
                "precision is intentionally not computed for this slice."
            ),
        }
        for value, total in sorted(totals.items())
    }


def _aggregate_adjudicated(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    counts = Counter()
    for row in rows:
        adjudicated = row["adjudicated"]
        for key in (
            "matchedReviewerIssues",
            "validInScopeNovel",
            "invalid",
            "outOfScope",
            "unverifiable",
            "unadjudicated",
        ):
            counts[key] += int(adjudicated[key])
    correct = counts["matchedReviewerIssues"] + counts["validInScopeNovel"]
    denominator = correct + counts["invalid"]
    return {
        **{
            key: counts[key]
            for key in (
                "matchedReviewerIssues",
                "validInScopeNovel",
                "invalid",
                "outOfScope",
                "unverifiable",
                "unadjudicated",
            )
        },
        "confirmedCorrectFindings": correct,
        "confirmedFindingPrecision": _ratio(correct, denominator),
        "precisionDenominator": denominator,
        "note": (
            "This secondary precision excludes out-of-scope, unverifiable, and "
            "unadjudicated findings. Valid novel issues have not been pooled "
            "into an expanded gold set, so no adjudicated recall or F1 is shown."
        ),
    }


def _configuration(
    judgment: Mapping[str, Any],
    *,
    corpus_cases: Mapping[str, Mapping[str, Any]],
    total_cases: int,
    iterations: int,
    seed: int,
    analysis_run: Mapping[str, Any] | None,
) -> dict[str, Any]:
    observed_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    analysis_cases = {
        str(item.get("caseId")): item
        for item in ((analysis_run or {}).get("cases") or [])
        if isinstance(item, Mapping)
    }
    not_scored: list[dict[str, Any]] = []
    uncertainty: list[dict[str, Any]] = []
    for case in judgment["cases"]:
        if not isinstance(case, Mapping):
            raise ValueError("judgment case must be an object")
        case_id = str(case.get("caseId") or "")
        if not case_id or case_id not in corpus_cases:
            raise ValueError(f"unknown judgment caseId: {case_id!r}")
        if case_id in observed_ids:
            raise ValueError(f"duplicate judgment caseId: {case_id}")
        observed_ids.add(case_id)
        if case.get("status") != "scored":
            not_scored.append(
                {
                    "caseId": case_id,
                    "reason": case.get("reason") or "not_scored",
                }
            )
            continue
        row = _case_row(
            case,
            corpus_case=corpus_cases[case_id],
            analysis_case=(
                analysis_cases.get(case_id)
                if analysis_run is not None
                else None
            ),
        )
        unverifiable_pairs = sum(
            1
            for item in (case.get("pairJudgments") or [])
            if isinstance(item, Mapping)
            and item.get("verdict") == "unverifiable"
        )
        if unverifiable_pairs:
            reason = "judge_unverifiable_pair"
            not_scored.append({"caseId": case_id, "reason": reason})
            uncertainty.append(
                {
                    "caseId": case_id,
                    "partition": str(corpus_cases[case_id]["partition"]),
                    "reason": reason,
                    "unverifiablePairs": unverifiable_pairs,
                }
            )
            continue
        rows.append(row)
    missing = sorted(set(corpus_cases) - observed_ids)
    not_scored.extend(
        {"caseId": case_id, "reason": "absent_from_judgment"}
        for case_id in missing
    )
    config_id = (
        f"{judgment['analysisRunId']}::{judgment['judgeModel']}::"
        f"{judgment['judgmentId']}"
    )
    scored_ids = {row["caseId"] for row in rows}

    def coverage_for(expected_ids: set[str]) -> dict[str, Any]:
        selected_not_scored = sorted(
            (
                item
                for item in not_scored
                if item["caseId"] in expected_ids
            ),
            key=lambda item: item["caseId"],
        )
        selected_uncertainty = sorted(
            (
                item
                for item in uncertainty
                if item["caseId"] in expected_ids
            ),
            key=lambda item: item["caseId"],
        )
        scored = len(scored_ids & expected_ids)
        return {
            "scoredCases": scored,
            "totalCases": len(expected_ids),
            "rate": _ratio(scored, len(expected_ids)),
            "uncertainCases": len(selected_uncertainty),
            "notScored": selected_not_scored,
            "uncertainty": selected_uncertainty,
        }

    all_ids = set(corpus_cases)
    if total_cases != len(all_ids):
        raise ValueError("configuration total case count is inconsistent")
    sealed_ids = {
        case_id
        for case_id, corpus_case in corpus_cases.items()
        if corpus_case["partition"] == "sealed"
    }
    development_ids = all_ids - sealed_ids
    sealed_rows = [row for row in rows if row["partition"] == "sealed"]
    development_rows = [
        row for row in rows if row["partition"] == "development"
    ]
    return {
        "configId": config_id,
        "analysisRunId": judgment["analysisRunId"],
        "analysisRunDigest": judgment.get("analysisRunDigest"),
        "analysisArtifactBound": analysis_run is not None,
        "judgmentId": judgment["judgmentId"],
        "analysisModel": judgment["analysisModel"],
        "analysisModelRoles": (
            dict(analysis_run["analysisModelRoles"])
            if isinstance(
                (analysis_run or {}).get("analysisModelRoles"),
                Mapping,
            )
            else None
        ),
        "judgeModel": judgment["judgeModel"],
        "promptVersion": judgment.get("promptVersion"),
        "coverage": coverage_for(all_ids),
        "primaryScope": {
            "partition": "sealed",
            "confirmatory": True,
        },
        "confirmatoryCoverage": coverage_for(sealed_ids),
        "primary": _aggregate_primary(
            sealed_rows,
            iterations=iterations,
            seed=seed,
            label=f"{config_id}:sealed-primary",
        ),
        "secondary": {
            "allCases": _aggregate_primary(
                rows,
                iterations=iterations,
                seed=seed,
                label=f"{config_id}:all-cases-secondary",
            ),
            "development": _aggregate_primary(
                development_rows,
                iterations=iterations,
                seed=seed,
                label=f"{config_id}:development-secondary",
            ),
        },
        "adjudicated": _aggregate_adjudicated(sealed_rows),
        "strata": {
            "sizeBand": _group_primary(
                sealed_rows,
                lambda row: str(row["sizeBand"]),
                iterations=iterations,
                seed=seed,
                label=f"{config_id}:sealed:sizeBand",
            ),
            "partition": _group_primary(
                rows,
                lambda row: str(row["partition"]),
                iterations=iterations,
                seed=seed,
                label=f"{config_id}:partition",
            ),
            "category": _gold_recall_strata(sealed_rows, "category"),
            "severity": _gold_recall_strata(sealed_rows, "severity"),
        },
        "cases": sorted(rows, key=lambda row: row["caseId"]),
    }


def _paired_comparison(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    left_cases = {
        row["caseId"]: row
        for row in left["cases"]
        if row["partition"] == "sealed"
    }
    right_cases = {
        row["caseId"]: row
        for row in right["cases"]
        if row["partition"] == "sealed"
    }
    common = sorted(set(left_cases) & set(right_cases))
    deltas = [
        float(right_cases[case_id]["primary"]["f1"])
        - float(left_cases[case_id]["primary"]["f1"])
        for case_id in common
    ]
    point_macro = round(fmean(deltas), 6) if deltas else None
    macro_samples: list[float] = []
    micro_samples: dict[str, list[float]] = {
        "precision": [],
        "recall": [],
        "f1": [],
    }
    if common and iterations > 0:
        rng = random.Random(
            _seed_for(
                seed,
                f"pair:{left['configId']}:{right['configId']}",
            )
        )
        for _ in range(iterations):
            sampled_ids = [
                common[rng.randrange(len(common))] for _ in common
            ]
            macro_samples.append(
                fmean(
                    float(right_cases[case_id]["primary"]["f1"])
                    - float(left_cases[case_id]["primary"]["f1"])
                    for case_id in sampled_ids
                )
            )
            sampled_left = [left_cases[case_id] for case_id in sampled_ids]
            sampled_right = [right_cases[case_id] for case_id in sampled_ids]
            left_sample_micro = _counts_metrics(_sum_counts(sampled_left))
            right_sample_micro = _counts_metrics(_sum_counts(sampled_right))
            for metric in ("precision", "recall", "f1"):
                left_value = left_sample_micro[metric]
                right_value = right_sample_micro[metric]
                if left_value is not None and right_value is not None:
                    micro_samples[metric].append(
                        float(right_value) - float(left_value)
                    )

    left_micro = _counts_metrics(
        _sum_counts([left_cases[case_id] for case_id in common])
    )
    right_micro = _counts_metrics(
        _sum_counts([right_cases[case_id] for case_id in common])
    )

    def metric_delta(metric: str) -> float | None:
        if not common:
            return None
        left_value = left_micro[metric]
        right_value = right_micro[metric]
        if left_value is None or right_value is None:
            return None
        return round(float(right_value) - float(left_value), 6)

    return {
        "leftConfigId": left["configId"],
        "rightConfigId": right["configId"],
        "comparisonScope": {
            "partition": "sealed",
            "confirmatory": True,
        },
        "commonScoredCases": len(common),
        "differenceDirection": "right_minus_left",
        "macroPerCaseF1Delta": point_macro,
        "macroPerCaseF1DeltaConfidenceInterval95": _interval(
            macro_samples
        ),
        "microDeltaOnCommonCases": {
            metric: metric_delta(metric)
            for metric in ("precision", "recall", "f1")
        },
        "microDeltaConfidenceInterval95": {
            metric: _interval(micro_samples[metric])
            for metric in ("precision", "recall", "f1")
        },
        "note": (
            "Confirmatory deltas use only common scored cases in the sealed "
            "partition. Intervals are paired pull-request-cluster bootstraps; "
            "they are descriptive and do not by themselves establish a causal "
            "improvement."
        ),
    }


def build_metrics(
    *,
    corpus_path: Path,
    judgment_paths: Sequence[Path],
    repository_path: Path | None = None,
    repository_evidence_path: Path | None = None,
    analysis_run_paths: Sequence[Path] | None = None,
    post_fix_analysis_run_paths: Sequence[Path] | None = None,
    replay_lock_paths: Sequence[Path] | None = None,
    replay_attestation_paths: Sequence[Path] | None = None,
    study_registration_path: Path | None = None,
    seal_ledger_path: Path | None = None,
    judge_evaluation_path: Path | None = None,
    post_fix_control_set_path: Path | None = None,
    post_fix_artifact_paths: Sequence[Path] | None = None,
    output_path: Path | None = None,
    bootstrap_iterations: int = MIN_PUBLICATION_BOOTSTRAP_ITERATIONS,
    seed: int = 20_260_729,
) -> dict[str, Any]:
    if not judgment_paths:
        raise ValueError("at least one judgment file is required")
    if bootstrap_iterations < 0:
        raise ValueError("bootstrap_iterations must be >= 0")
    protocol_paths = (
        study_registration_path,
        seal_ledger_path,
        judge_evaluation_path,
    )
    if any(path is not None for path in protocol_paths) and not all(
        path is not None for path in protocol_paths
    ):
        raise ValueError(
            "study registration, seal ledger, and judge evaluation must be "
            "supplied together"
        )
    if (
        post_fix_control_set_path is not None or post_fix_artifact_paths
    ) and not all(
        path is not None for path in protocol_paths
    ):
        raise ValueError(
            "post-fix control set requires registration, seal ledger, and "
            "judge evaluation"
        )
    if post_fix_control_set_path is not None and not post_fix_artifact_paths:
        raise ValueError(
            "a post-fix control-set digest is insufficient; supply the raw "
            "post-fix replay, runs, judgments, controls, and control set"
        )
    post_fix_artifacts, post_fix_artifact_roots = _post_fix_artifacts(
        post_fix_artifact_paths
    )
    corpus = read_json(corpus_path)
    corpus_summary = validate_corpus(corpus)
    try:
        validate_corpus(corpus, paper_ready=True)
    except ValueError:
        strict_corpus_paper_ready = False
    else:
        strict_corpus_paper_ready = True
    corpus_summary["paperReady"] = strict_corpus_paper_ready
    repository_evidence_control: dict[str, Any] = {
        "status": "not_bound",
        "evidenceDigest": None,
        "objectIdentityDigest": None,
        "repositoryContentDigest": None,
    }
    if repository_evidence_path is not None:
        if not isinstance(corpus, Mapping):
            raise ValueError("repository evidence requires an object corpus")
        evidence_summary, evidence_repository = validate_repository_evidence(
            manifest_path=repository_evidence_path,
            corpus=corpus,
            evidence_root=repository_evidence_path.parent,
        )
        if (
            repository_path is not None
            and repository_path.resolve(strict=True)
            != evidence_repository.resolve(strict=True)
        ):
            raise ValueError(
                "repository path differs from immutable repository evidence"
            )
        repository_path = evidence_repository
        repository_evidence_control = {
            "status": "validated",
            "evidenceDigest": evidence_summary["evidenceDigest"],
            "objectIdentityDigest": evidence_summary[
                "objectIdentityDigest"
            ],
            "repositoryContentDigest": evidence_summary[
                "repositoryContentDigest"
            ],
        }
    corpus_cases = {case["caseId"]: case for case in corpus["cases"]}
    analysis_runs, analysis_artifact_roots = _analysis_runs(
        analysis_run_paths,
        corpus_digest=corpus_summary["corpusDigest"],
    )
    replay_locks, replay_attestations = _replay_evidence(
        corpus=corpus,
        corpus_summary=corpus_summary,
        lock_paths=replay_lock_paths,
        attestation_paths=replay_attestation_paths,
    )
    judgments = []
    judgment_artifact_roots: dict[str, Path] = {}
    for path in judgment_paths:
        judgment = _validate_judgment(
            read_json(path),
            corpus_digest=corpus_summary["corpusDigest"],
        )
        judgment_id = str(judgment.get("judgmentId") or "")
        if not judgment_id or judgment_id in judgment_artifact_roots:
            raise ValueError(
                f"duplicate/invalid judgment ID: {judgment_id!r}"
            )
        judgments.append(judgment)
        judgment_artifact_roots[judgment_id] = path.resolve().parent
    protocol_controls: dict[str, Any] = {
        "registration": {"status": "not_bound", "digest": None},
        "sealedLabelCustody": {"status": "not_bound", "digest": None},
        "judgeCalibrationOrAudit": {
            "status": "not_bound",
            "digest": None,
        },
        "postFixControl": {"status": "not_bound", "digest": None},
        "reproducibilityPackage": {
            "status": "not_bound",
            "digest": None,
            "note": (
                "The reproducibility package is generated after finalized "
                "metrics and therefore cannot be circularly bound here."
            ),
        },
    }
    if all(path is not None for path in protocol_paths):
        registration = read_json(study_registration_path)
        if not isinstance(registration, Mapping):
            raise ValueError("study registration must be an object")
        registered_bootstrap = registration.get("bootstrap")
        if (
            not isinstance(registered_bootstrap, Mapping)
            or registered_bootstrap.get("iterations")
            != bootstrap_iterations
            or registered_bootstrap.get("seed") != seed
        ):
            raise ValueError(
                "metrics bootstrap settings differ from preregistration"
            )
        validated_controls = validate_protocol_bundle(
            corpus=corpus,
            registration_path=study_registration_path,
            seal_ledger_path=seal_ledger_path,
            judge_evaluation_path=judge_evaluation_path,
            analysis_run_paths=analysis_run_paths,
            post_fix_analysis_run_paths=post_fix_analysis_run_paths,
            judgment_paths=judgment_paths,
        )
        protocol_controls.update(validated_controls)
        if post_fix_artifact_paths:
            from .package_evidence import validate_post_fix_package_bundle

            post_fix_plan = _one_post_fix_artifact(
                post_fix_artifacts,
                POST_FIX_PLAN_KIND,
            )
            post_fix_lock = _one_post_fix_artifact(
                post_fix_artifacts,
                POST_FIX_LOCK_KIND,
            )
            post_fix_attestation = _one_post_fix_artifact(
                post_fix_artifacts,
                POST_FIX_ATTESTATION_KIND,
            )
            post_fix_control_set = _one_post_fix_artifact(
                post_fix_artifacts,
                POST_FIX_CONTROL_SET_KIND,
            )
            if (
                post_fix_control_set_path is not None
                and read_json(post_fix_control_set_path)
                != post_fix_control_set
            ):
                raise ValueError(
                    "explicit post-fix control set differs from raw artifact set"
                )
            explicit_post_fix_runs = [
                read_json(path)
                for path in (post_fix_analysis_run_paths or [])
            ]
            discovered_post_fix_runs = post_fix_artifacts[
                POST_FIX_RUN_KIND
            ]
            if sorted(
                (
                    str(run.get("runId") or ""),
                    str(run.get("runDigest") or ""),
                )
                for run in explicit_post_fix_runs
                if isinstance(run, Mapping)
            ) != sorted(
                (
                    str(run.get("runId") or ""),
                    str(run.get("runDigest") or ""),
                )
                for run in discovered_post_fix_runs
            ):
                raise ValueError(
                    "post-fix analysis run inputs differ from raw artifact set"
                )
            primary_lock_digests = {
                str(run.get("replayLockDigest") or "")
                for run in analysis_runs.values()
            }
            if (
                len(primary_lock_digests) != 1
                or "" in primary_lock_digests
            ):
                raise ValueError(
                    "primary runs do not share one packaged replay lock"
                )
            primary_replay_lock = replay_locks.get(
                next(iter(primary_lock_digests))
            )
            if primary_replay_lock is None:
                raise ValueError(
                    "raw post-fix validation requires the primary replay lock"
                )
            seal_ledger = read_json(seal_ledger_path)
            if not isinstance(seal_ledger, Mapping):
                raise ValueError("seal ledger must be an object")
            post_fix_summary = validate_post_fix_package_bundle(
                corpus=corpus,
                registration=registration,
                seal_ledger=seal_ledger,
                primary_replay_lock=primary_replay_lock,
                primary_runs=list(analysis_runs.values()),
                primary_judgments=judgments,
                post_fix_plan=post_fix_plan,
                post_fix_lock=post_fix_lock,
                post_fix_attestation=post_fix_attestation,
                post_fix_runs=discovered_post_fix_runs,
                post_fix_judgments=post_fix_artifacts[
                    POST_FIX_JUDGMENT_KIND
                ],
                post_fix_controls=post_fix_artifacts[
                    POST_FIX_CONTROL_KIND
                ],
                post_fix_control_set=post_fix_control_set,
                post_fix_run_roots={
                    str(item["runId"]): post_fix_artifact_roots[id(item)]
                    for item in discovered_post_fix_runs
                },
                post_fix_judgment_roots={
                    str(item["judgmentId"]): post_fix_artifact_roots[id(item)]
                    for item in post_fix_artifacts[
                        POST_FIX_JUDGMENT_KIND
                    ]
                },
                repository=repository_path,
            )
            protocol_controls["postFixControl"] = {
                "status": "validated",
                "digest": post_fix_summary["controlSetDigest"],
            }
    if analysis_runs and set(analysis_runs) != {
        str(judgment["analysisRunId"]) for judgment in judgments
    }:
        raise ValueError(
            "supplied analysis run IDs must match judgment analysisRunIds exactly"
        )
    configurations = [
        _configuration(
            judgment,
            corpus_cases=corpus_cases,
            total_cases=len(corpus_cases),
            iterations=bootstrap_iterations,
            seed=seed,
            analysis_run=analysis_runs.get(str(judgment["analysisRunId"])),
        )
        for judgment in judgments
    ]
    if analysis_runs:
        for judgment in judgments:
            run = analysis_runs.get(str(judgment["analysisRunId"]))
            if run is None:
                raise ValueError(
                    f"no analysis run supplied for "
                    f"{judgment['analysisRunId']}"
                )
            if judgment.get("analysisRunDigest") != run.get("runDigest"):
                raise ValueError(
                    f"judgment {judgment['judgmentId']} analysis digest drift"
                )
            if judgment.get("analysisModel") != run.get("analysisModel"):
                raise ValueError(
                    f"judgment {judgment['judgmentId']} analysis model drift"
                )
    config_ids = [item["configId"] for item in configurations]
    if len(config_ids) != len(set(config_ids)):
        raise ValueError("duplicate judgment configuration")
    comparisons = [
        _paired_comparison(
            left,
            right,
            iterations=bootstrap_iterations,
            seed=seed,
        )
        for left, right in itertools.combinations(configurations, 2)
    ]
    artifact_gate_failures: list[str] = []
    if bootstrap_iterations < MIN_PUBLICATION_BOOTSTRAP_ITERATIONS:
        artifact_gate_failures.append(
            "bootstrap_iterations_below_publication_minimum"
        )
    if not strict_corpus_paper_ready:
        artifact_gate_failures.append("corpus_not_strictly_paper_ready")
    if repository_evidence_control["status"] != "validated":
        artifact_gate_failures.append(
            "source_repository_evidence_not_bound"
        )
    if not analysis_runs:
        artifact_gate_failures.append("analysis_artifacts_not_bound")
    judge_fingerprints = []
    for judgment in judgments:
        judgment_id = str(judgment.get("judgmentId") or "unknown")
        judge_config = judgment.get("judgeConfig")
        judge_config_digest = judgment.get("judgeConfigDigest")
        if (
            not isinstance(judge_config, Mapping)
            or not _is_digest(judge_config_digest)
            or judge_config_digest != sha256_json(judge_config)
            or judge_config.get("model") != judgment.get("judgeModel")
            or not isinstance(judgment.get("promptVersion"), str)
            or not str(judgment.get("promptVersion")).strip()
        ):
            artifact_gate_failures.append(
                f"{judgment_id}:judge_configuration_unverifiable"
            )
        else:
            judge_fingerprints.append(
                (
                    str(judgment["judgeModel"]),
                    str(judgment["promptVersion"]),
                    str(judge_config_digest),
                )
            )
        artifact_gate_failures.extend(
            f"{judgment_id}:{failure}"
            for failure in _paper_judgment_failures(
                judgment,
                corpus_cases=corpus_cases,
                analysis_run=analysis_runs.get(
                    str(judgment.get("analysisRunId") or "")
                ),
                artifact_root=judgment_artifact_roots.get(judgment_id),
                repository=repository_path,
                require_source_reconstruction=True,
            )
        )
    if len(judgments) > 1 and (
        len(judge_fingerprints) != len(judgments)
        or len(set(judge_fingerprints)) != 1
    ):
        artifact_gate_failures.append(
            "judge_configuration_not_fixed_across_comparisons"
        )
    analysis_control_digests = [
        _analysis_control_digest(analysis_runs[run_id])
        for run_id in (
            str(judgment.get("analysisRunId") or "")
            for judgment in judgments
        )
        if run_id in analysis_runs
    ]
    if len(judgments) > 1 and (
        len(analysis_control_digests) != len(judgments)
        or len(set(analysis_control_digests)) != 1
    ):
        artifact_gate_failures.append(
            "analysis_control_factors_not_fixed_across_comparisons"
        )
    for configuration in configurations:
        if configuration["coverage"]["scoredCases"] != len(corpus_cases):
            artifact_gate_failures.append(
                f"{configuration['configId']}:judgment_coverage_incomplete"
            )
        run = analysis_runs.get(str(configuration["analysisRunId"]))
        if run is not None:
            replay_lock_digest = run.get("replayLockDigest")
            replay_attestation_digest = run.get(
                "replayAttestationDigest"
            )
            analysis_config = run.get("analysisConfig")
            artifact_root = analysis_artifact_roots.get(
                str(configuration["analysisRunId"])
            )
            run_lock = _safe_raw_artifact(
                artifact_root,
                run.get("replayLockArtifact"),
            )
            if run_lock is not None:
                try:
                    validate_replay_lock(
                        run_lock,
                        corpus,
                        corpus_summary=corpus_summary,
                    )
                except ValueError:
                    artifact_gate_failures.append(
                        f"{configuration['configId']}:"
                        "replay_lock_artifact_invalid"
                    )
                else:
                    observed_lock_digest = str(
                        run_lock.get("lockDigest") or ""
                    )
                    if observed_lock_digest != replay_lock_digest:
                        artifact_gate_failures.append(
                            f"{configuration['configId']}:"
                            "replay_lock_digest_mismatch"
                        )
                    elif observed_lock_digest:
                        replay_locks.setdefault(
                            observed_lock_digest,
                            run_lock,
                        )
            run_attestation = _safe_raw_artifact(
                artifact_root,
                run.get("replayAttestationArtifact"),
            )
            bound_lock = replay_locks.get(str(replay_lock_digest or ""))
            if run_attestation is not None and bound_lock is not None:
                try:
                    observed_attestation_digest = (
                        validate_replay_attestation(
                            run_attestation,
                            bound_lock,
                            corpus,
                            corpus_summary=corpus_summary,
                        )
                    )
                except ValueError:
                    artifact_gate_failures.append(
                        f"{configuration['configId']}:"
                        "replay_attestation_artifact_invalid"
                    )
                else:
                    if (
                        observed_attestation_digest
                        != replay_attestation_digest
                    ):
                        artifact_gate_failures.append(
                            f"{configuration['configId']}:"
                            "replay_attestation_digest_mismatch"
                        )
                    else:
                        replay_attestations.setdefault(
                            observed_attestation_digest,
                            run_attestation,
                        )
            if replay_lock_digest not in replay_locks:
                artifact_gate_failures.append(
                    f"{configuration['configId']}:"
                    "replay_lock_artifact_missing"
                )
            if replay_attestation_digest not in replay_attestations:
                artifact_gate_failures.append(
                    f"{configuration['configId']}:"
                    "replay_attestation_artifact_missing"
                )
            else:
                attestation = replay_attestations[
                    str(replay_attestation_digest)
                ]
                if (
                    attestation.get("replayLockDigest")
                    != replay_lock_digest
                ):
                    artifact_gate_failures.append(
                        f"{configuration['configId']}:"
                        "replay_attestation_lock_mismatch"
                    )
            if (
                not isinstance(analysis_config, Mapping)
                or not (
                    analysis_config.get("require_replay_attestation")
                    is True
                    or analysis_config.get("require_runtime_provenance")
                    is True
                )
            ):
                artifact_gate_failures.append(
                    f"{configuration['configId']}:"
                    "replay_attestation_not_required_by_run"
                )
            artifact_gate_failures.extend(
                f"{configuration['configId']}:{failure}"
                for failure in _paper_run_failures(
                    run,
                    corpus_cases=corpus_cases,
                    artifact_root=analysis_artifact_roots.get(
                        str(configuration["analysisRunId"])
                    ),
                    repository_path=repository_path,
                    require_request_source_reconstruction=True,
                )
            )
    artifact_gate_failures = list(dict.fromkeys(artifact_gate_failures))
    publication_protocol_gate_failures = list(
        PUBLICATION_PROTOCOL_GATE_FAILURES
    )
    if protocol_controls["registration"]["status"] == "validated":
        publication_protocol_gate_failures = [
            failure
            for failure in publication_protocol_gate_failures
            if failure
            not in {
                "preregistration_artifact_not_bound",
                "sealed_partition_access_and_unseal_evidence_not_bound",
                "judge_calibration_or_preregistered_human_audit_not_bound",
            }
        ]
    if protocol_controls["postFixControl"]["status"] == "validated":
        publication_protocol_gate_failures = [
            failure
            for failure in publication_protocol_gate_failures
            if failure != "post_fix_control_not_bound"
        ]
    paper_gate_failures = list(
        dict.fromkeys(
            [
                *artifact_gate_failures,
                *publication_protocol_gate_failures,
            ]
        )
    )
    result = {
        "kind": METRICS_KIND,
        "generatedAt": _now(),
        "corpus": {
            **corpus_summary,
            "repository": corpus["repository"],
            "defaultBranch": corpus["defaultBranch"],
        },
        "methodology": {
            "primary": "reference_set_one_to_one_substantive_matching",
            "unit": "pull_request_review_round",
            "confidenceIntervals": (
                "95% percentile pull-request-cluster bootstrap"
            ),
            "bootstrapIterations": bootstrap_iterations,
            "minimumPublicationBootstrapIterations": (
                MIN_PUBLICATION_BOOTSTRAP_ITERATIONS
            ),
            "bootstrapSeed": seed,
            "analysisArtifactsBound": bool(analysis_runs),
            "analysisControlDigest": (
                analysis_control_digests[0]
                if analysis_control_digests
                and len(set(analysis_control_digests)) == 1
                else None
            ),
            "sourceRepositoryEvidence": repository_evidence_control,
            "artifactIntegrityReady": not artifact_gate_failures,
            "artifactIntegrityGateFailures": artifact_gate_failures,
            "publicationProtocolReady": False,
            "protocolControls": protocol_controls,
            "publicationProtocolGateFailures": (
                publication_protocol_gate_failures
            ),
            "paperReady": False,
            "paperGateFailures": paper_gate_failures,
            "limitations": [
                (
                    "Metrics are not paper-ready unless every judgment is "
                    "bound to and revalidated against its exact analysis-run "
                    "artifact."
                ),
                (
                    "Reviewer comments are an incomplete issue set; unmatched "
                    "findings are reference-set false positives, not automatically "
                    "invalid defects."
                ),
                (
                    "Confirmed novel-finding precision is secondary until novel "
                    "issues are pooled, independently adjudicated, and rematched "
                    "against every model."
                ),
                (
                    "Model comparisons are observational unless analysis model, "
                    "judge calibration, prompts, retrieval state, and run conditions "
                    "are controlled by a preregistered protocol."
                ),
                (
                    "Runtime image identities are local Docker observations. "
                    "Analysis, retrieval, and finalizer responses are not "
                    "cryptographically signed by those service builds, so "
                    "independent publication should retain the isolated runtime "
                    "and its deployment evidence."
                ),
            ],
        },
        "configurations": configurations,
        "pairwiseComparisons": comparisons,
    }
    result["metricsDigest"] = sha256_json(result)
    if output_path is not None:
        write_json(output_path, result)
    return result


def validate_metrics_derivation(
    *,
    metrics: Mapping[str, Any],
    corpus_path: Path,
    repository_path: Path,
    repository_evidence_path: Path,
    judgment_paths: Sequence[Path],
    analysis_run_paths: Sequence[Path],
    post_fix_analysis_run_paths: Sequence[Path],
    replay_lock_paths: Sequence[Path],
    replay_attestation_paths: Sequence[Path],
    study_registration_path: Path,
    seal_ledger_path: Path,
    judge_evaluation_path: Path,
    post_fix_control_set_path: Path | None = None,
    post_fix_artifact_paths: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """Regenerate packaged metrics from the packaged evidence graph.

    A self-consistent ``metricsDigest`` proves only that an artifact was
    rehashed after editing.  Publication-package verification therefore
    rebuilds the complete metrics artifact with the preregistered bootstrap
    settings and compares every semantic field.  ``generatedAt`` is the sole
    excluded field because regeneration necessarily occurs at a later time.

    The repository must be the sanitized bare store resolved from the packaged
    repository-evidence manifest. Both paths are inside the package root, so
    verification reconstructs source, diffs, prompts, and requests offline
    without trusting a mutable host checkout.
    """

    if not isinstance(metrics, Mapping):
        raise ValueError("packaged metrics must be an object")
    required_fields = {
        "kind",
        "generatedAt",
        "corpus",
        "methodology",
        "configurations",
        "pairwiseComparisons",
        "metricsDigest",
    }
    if set(metrics) != required_fields:
        raise ValueError("packaged metrics fields are invalid")
    if metrics.get("kind") != METRICS_KIND:
        raise ValueError("packaged metrics kind is invalid")
    _utc_datetime(metrics.get("generatedAt"))
    digest_payload = dict(metrics)
    declared_digest = digest_payload.pop("metricsDigest", None)
    if not _is_digest(declared_digest):
        raise ValueError("packaged metrics digest is invalid")
    if declared_digest != sha256_json(digest_payload):
        raise ValueError("packaged metrics digest mismatch")
    methodology = metrics.get("methodology")
    if (
        not isinstance(methodology, Mapping)
        or methodology.get("artifactIntegrityReady") is not True
    ):
        raise ValueError(
            "packaged metrics artifact integrity is not ready"
        )

    registration = read_json(study_registration_path)
    if not isinstance(registration, Mapping):
        raise ValueError("packaged study registration must be an object")
    bootstrap = registration.get("bootstrap")
    if not isinstance(bootstrap, Mapping):
        raise ValueError("packaged study registration has no bootstrap plan")
    iterations = bootstrap.get("iterations")
    seed = bootstrap.get("seed")
    if (
        isinstance(iterations, bool)
        or not isinstance(iterations, int)
        or iterations < MIN_PUBLICATION_BOOTSTRAP_ITERATIONS
        or isinstance(seed, bool)
        or not isinstance(seed, int)
    ):
        raise ValueError("packaged bootstrap plan is invalid")

    regenerated = build_metrics(
        corpus_path=corpus_path,
        judgment_paths=judgment_paths,
        repository_path=repository_path,
        repository_evidence_path=repository_evidence_path,
        analysis_run_paths=analysis_run_paths,
        post_fix_analysis_run_paths=post_fix_analysis_run_paths,
        replay_lock_paths=replay_lock_paths,
        replay_attestation_paths=replay_attestation_paths,
        study_registration_path=study_registration_path,
        seal_ledger_path=seal_ledger_path,
        judge_evaluation_path=judge_evaluation_path,
        post_fix_control_set_path=post_fix_control_set_path,
        post_fix_artifact_paths=post_fix_artifact_paths,
        bootstrap_iterations=iterations,
        seed=seed,
    )
    observed_semantics = dict(metrics)
    expected_semantics = dict(regenerated)
    observed_semantics.pop("generatedAt")
    observed_semantics.pop("metricsDigest")
    expected_semantics.pop("generatedAt")
    expected_semantics.pop("metricsDigest")
    if observed_semantics != expected_semantics:
        raise ValueError(
            "packaged metrics are not exactly derivable from packaged "
            "corpus, runs, judgments, protocol controls, and bootstrap plan"
        )
    regenerated_methodology = regenerated["methodology"]
    if regenerated_methodology["artifactIntegrityReady"] is not True:
        raise ValueError(
            "regenerated metrics artifact integrity is not ready"
        )
    configurations = regenerated["configurations"]
    comparisons = regenerated["pairwiseComparisons"]
    return {
        "metricsDigest": str(declared_digest),
        "configurations": len(configurations),
        "scoredCases": sum(
            int(configuration["coverage"]["scoredCases"])
            for configuration in configurations
        ),
        "pairwiseComparisons": len(comparisons),
        "bootstrapIterations": iterations,
        "bootstrapSeed": seed,
        "artifactIntegrityReady": True,
        "paperReady": bool(regenerated_methodology["paperReady"]),
        "paperGateFailures": list(
            regenerated_methodology["paperGateFailures"]
        ),
    }
