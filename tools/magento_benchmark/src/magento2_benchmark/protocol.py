from __future__ import annotations

import hashlib
import itertools
import os
import re
import tomllib
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .corpus import CORPUS_KIND, validate_corpus
from .collect import (
    DISCOVERY_KIND,
    DISCOVERY_SELECTION_LINK_KIND,
    SELECTION_KIND,
)
from .current_comments import CURRENT_COMMENT_ATTESTATION_KIND
from .curation import (
    DECISIONS_KIND,
    DRAFT_KIND,
    DRAFT_SOURCE_ARCHIVE_KIND,
    PACKET_KIND,
    THREAD_EVIDENCE_KIND,
)
from .judge import JUDGMENT_KIND, _maximum_assignment
from .execution_corpus import (
    EXECUTION_CORPUS_KIND,
    build_execution_corpus,
    validate_execution_corpus,
)
from .postfix import (
    POST_FIX_ATTESTATION_KIND,
    POST_FIX_CONTROL_KIND,
    POST_FIX_CONTROL_SET_KIND,
    POST_FIX_JUDGMENT_KIND,
    POST_FIX_LOCK_KIND,
    POST_FIX_PLAN_KIND,
    POST_FIX_PROMPT_DIGEST,
    POST_FIX_PROMPT_VERSION,
    POST_FIX_RUN_KIND,
)
from .package_evidence import (
    CURRENT_COMMENT_EVIDENCE_KINDS,
    CURATION_EVIDENCE_KINDS,
    POST_FIX_EVIDENCE_KINDS,
    REPLAY_EVIDENCE_KINDS,
    SOURCE_EVIDENCE_KINDS,
    validate_post_fix_package_bundle,
    validate_primary_replay_bundle,
    validate_source_curation_bundle,
)
from .preflight import PREFLIGHT_KIND
from .replay import ATTESTATION_KIND, LOCK_KIND
from .repository_evidence import (
    REPOSITORY_EVIDENCE_KIND,
    validate_repository_evidence,
)
from .runner import RUN_KIND
from .util import (
    canonical_json,
    read_json,
    require_text,
    sha256_json,
    write_json,
)


REGISTRATION_KIND = "codecrow-magento2-study-registration"
SEAL_LEDGER_KIND = "codecrow-magento2-seal-ledger"
JUDGE_EVALUATION_KIND = "codecrow-magento2-judge-evaluation"
JUDGE_EVALUATION_PACKET_KIND = (
    "codecrow-magento2-blinded-judge-evaluation-packet"
)
REPRODUCIBILITY_PACKAGE_KIND = (
    "codecrow-magento2-reproducibility-package"
)

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
PRIMARY_METRICS = [
    "micro_reference_set_precision",
    "micro_reviewer_issue_recall",
    "micro_f1",
]
PAIR_HUMAN_VERDICTS = [
    "substantive_match",
    "partial",
    "related_distinct",
    "no_match",
    "unverifiable",
]
NOVEL_HUMAN_VERDICTS = [
    "valid_in_scope_novel",
    "invalid",
    "out_of_scope",
    "unverifiable",
]
SECONDARY_SCOPES = {
    "all_50": PRIMARY_METRICS,
    "development_30": PRIMARY_METRICS,
}
COMPARISON_CONTROLS = {
    "analysis_provider",
    "public_analysis_config_except_selected_model",
    "replay_lock_and_case_order",
    "index_receipts_and_retrieval_state",
    "finding_semantics_and_transport",
    "immutable_runtime_images",
    "judge_config_and_prompt_identity",
    "bootstrap_seed_and_iterations",
}
MATCH_VERDICTS = {
    "substantive_match",
    "partial",
    "related_distinct",
    "no_match",
    "unverifiable",
}
NOVEL_VERDICTS = {
    "valid_in_scope_novel",
    "invalid",
    "out_of_scope",
    "unverifiable",
}
YES_NO_UNCLEAR = {"yes", "no", "unclear"}
LOCATION_RELATIONS = {
    "exact_line",
    "same_symbol",
    "same_functional_area",
    "dependency",
    "unrelated",
    "unclear",
}
JUDGE_EVALUATION_SUBJECT_POLICY = (
    "all_pair_and_novel_decisions_for_every_planned_judgment_and_case"
)
REQUIRED_PACKAGE_CATEGORIES = {
    "corpus",
    "registration",
    "seal",
    "judge_evaluation",
    "metrics",
    "dashboard",
    "analysis",
    "judgment",
    "runtime",
    "config",
    "source",
    "curation",
    "replay",
    "current_comment",
    "post_fix",
    "execution",
    "repository",
}
SENSITIVE_KEY_NAMES = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "client_secret",
    "api_key",
    "access_token",
    "refresh_token",
    "credential",
    "credentials",
}
SECRET_TEXT_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(rb"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    rb"""(?im)^\s*(?:export\s+)?[A-Za-z0-9_.-]*"""
    rb"""(?:api[_-]?key|password|secret|access[_-]?token|"""
    rb"""refresh[_-]?token|credential)[A-Za-z0-9_.-]*"""
    rb"""\s*[:=]\s*(?P<value>[^\r\n#]+)"""
)
INLINE_SECRET_ASSIGNMENT_PATTERN = re.compile(
    rb"""(?i)\b(?:api[_-]?key|password|secret|access[_-]?token|"""
    rb"""refresh[_-]?token|credential)\b\s*[:=]\s*"""
    rb"""(?P<value>[^\s&;,'"]+)"""
)
ABSOLUTE_URL_PATTERN = re.compile(
    r"""https?://[^\s<>'"]+""",
    re.IGNORECASE,
)
ENV_PLACEHOLDER = re.compile(r"^\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})$")
SAFE_PUBLIC_URL_FRAGMENT = re.compile(
    r"^(?:discussion_r|issuecomment-|L)\d+(?:-L\d+)?$",
    re.IGNORECASE,
)
SECRET_SCAN_TEXT_SUFFIXES = {
    ".bash",
    ".cfg",
    ".cmd",
    ".conf",
    ".env",
    ".ini",
    ".log",
    ".md",
    ".properties",
    ".ps1",
    ".sh",
    ".text",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
    ".zsh",
}
SECRET_ASSIGNMENT_TEXT_SUFFIXES = SECRET_SCAN_TEXT_SUFFIXES - {".toml"}


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a UTC ISO-8601 timestamp"
        ) from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} must be UTC")
    return parsed


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_HEX.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _integer(value: Any, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _rate(value: Any, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= 1
    ):
        raise ValueError(f"{field} must be a number from 0 to 1")
    return float(value)


def _exact_fields(
    value: Any,
    fields: set[str],
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{field} fields are invalid")
    return value


def _text_list(
    value: Any,
    field: str,
    *,
    minimum: int = 1,
) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) < minimum
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(
            f"{field} must contain at least {minimum} unique strings"
        )
    return list(value)


def _artifact_digest(
    value: Mapping[str, Any],
    *,
    field: str,
    kind: str,
) -> str:
    if value.get("kind") != kind:
        raise ValueError(f"{field}.kind must be {kind!r}")
    payload = dict(value)
    declared = payload.pop(field, None)
    computed = sha256_json(payload)
    if declared != computed:
        raise ValueError(f"{field} mismatch")
    return computed


def _url_contains_private_material(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return True
    if parsed.username is not None or parsed.password is not None:
        return True
    for key, item in urllib.parse.parse_qsl(
        parsed.query,
        keep_blank_values=True,
    ):
        normalized_key = key.casefold().replace("-", "_")
        sensitive_key = (
            normalized_key in SENSITIVE_KEY_NAMES
            or normalized_key.endswith(
                (
                    "_api_key",
                    "_access_token",
                    "_refresh_token",
                    "_token",
                    "_password",
                    "_secret",
                    "_credential",
                    "_credentials",
                )
            )
        )
        if (
            sensitive_key
            and not ENV_PLACEHOLDER.fullmatch(item)
            and item.casefold() not in {"<redacted>", "null", "none"}
        ):
            return True
    return bool(
        parsed.fragment
        and SAFE_PUBLIC_URL_FRAGMENT.fullmatch(parsed.fragment) is None
    )


def _reject_private_urls(value: str, field: str) -> None:
    for match in ABSOLUTE_URL_PATTERN.finditer(value):
        url = match.group(0).rstrip(").,]")
        if _url_contains_private_material(url):
            raise ValueError(
                f"{field} contains URL userinfo, query credentials, or fragment"
            )


def _reject_sensitive_config(value: Any, field: str = "config") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.casefold().replace("-", "_")
            if (
                normalized in SENSITIVE_KEY_NAMES
                or normalized.endswith("_api_key")
                or normalized.endswith("_access_token")
                or normalized.endswith("_refresh_token")
                or normalized.endswith("_token")
                or normalized.endswith("_password")
                or normalized.endswith("_secret")
                or normalized.endswith("_credential")
                or normalized.endswith("_credentials")
            ):
                if normalized.endswith("_env"):
                    continue
                if child not in (None, "", "<redacted>"):
                    raise ValueError(
                        f"{field}.{key} contains a secret-like value"
                    )
            _reject_sensitive_config(child, f"{field}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_config(child, f"{field}[{index}]")
    elif isinstance(value, str):
        _reject_private_urls(value, field)


def _secret_value_is_placeholder(raw_value: bytes) -> bool:
    normalized = raw_value.strip().strip(b"'\"")
    return bool(
        not normalized
        or normalized.lower() in {b"<redacted>", b"null", b"none"}
        or (
            normalized.startswith(b"${")
            and normalized.endswith(b"}")
            and re.fullmatch(
                rb"\$\{[A-Za-z_][A-Za-z0-9_]*\}",
                normalized,
            )
            is not None
        )
        or re.fullmatch(
            rb"\$[A-Za-z_][A-Za-z0-9_]*",
            normalized,
        )
        is not None
    )


def _scan_secret_text(
    data: bytes,
    context: str,
    *,
    assignments: bool,
) -> None:
    for pattern in SECRET_TEXT_PATTERNS:
        if pattern.search(data):
            raise ValueError(
                f"secret-like content detected in {context}"
            )
    if assignments:
        for match in INLINE_SECRET_ASSIGNMENT_PATTERN.finditer(data):
            if not _secret_value_is_placeholder(match.group("value")):
                raise ValueError(
                    f"secret-like assignment detected in {context}"
                )


def _scan_manifest_for_secrets(value: Mapping[str, Any]) -> None:
    _reject_sensitive_config(value, "reproducibility package manifest")
    _scan_secret_text(
        canonical_json(value).encode("utf-8"),
        "reproducibility package manifest",
        assignments=True,
    )


def _validate_digest_artifact(
    value: Any,
    *,
    kind: str,
    digest_field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or value.get("kind") != kind:
        raise ValueError(f"artifact kind must be {kind!r}")
    payload = dict(value)
    declared = payload.pop(digest_field, None)
    if declared != sha256_json(payload):
        raise ValueError(f"{digest_field} mismatch")
    return value


def _strict_corpus(corpus: Mapping[str, Any]) -> dict[str, Any]:
    summary = validate_corpus(corpus, paper_ready=True)
    if summary.get("partitionCounts") != {
        "development": 30,
        "sealed": 20,
    }:
        raise ValueError("strict corpus partition count is not 30/20")
    if summary.get("partitionPolicyPreserved") is not True:
        raise ValueError("strict corpus partition policy is not preserved")
    return summary


def _sealed_commitment(corpus: Mapping[str, Any]) -> str:
    sealed = sorted(
        (
            dict(case)
            for case in corpus["cases"]
            if case.get("partition") == "sealed"
        ),
        key=lambda case: str(case["caseId"]),
    )
    if len(sealed) != 20:
        raise ValueError("sealed commitment requires exactly 20 cases")
    return sha256_json(
        {
            "corpusDigest": corpus["corpusDigest"],
            "partition": "sealed",
            "cases": sealed,
        }
    )


def _normalized_registration_plan(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    fields = {
        "studyId",
        "registeredAt",
        "analysisPlans",
        "judgePlans",
        "endpoints",
        "bootstrap",
        "executionPolicy",
        "comparisonControls",
        "postFixPlan",
        "judgeEvaluationPlan",
        "allowedClaims",
        "prohibitedClaims",
    }
    _exact_fields(plan, fields, "study plan")
    study_id = require_text(plan.get("studyId"), "studyId")
    if SAFE_ID.fullmatch(study_id) is None:
        raise ValueError("studyId is not a safe identifier")
    _timestamp(plan.get("registeredAt"), "registeredAt")

    analysis_plans = []
    raw_analysis = plan.get("analysisPlans")
    if not isinstance(raw_analysis, list) or not raw_analysis:
        raise ValueError("analysisPlans must be a non-empty array")
    for index, item in enumerate(raw_analysis):
        analysis_fields = {"runId", "model", "provider", "config"}
        if isinstance(item, Mapping) and "configDigest" in item:
            analysis_fields.add("configDigest")
        _exact_fields(
            item,
            analysis_fields,
            f"analysisPlans[{index}]",
        )
        run_id = require_text(item.get("runId"), f"analysisPlans[{index}].runId")
        if SAFE_ID.fullmatch(run_id) is None:
            raise ValueError(f"analysisPlans[{index}].runId is unsafe")
        model = require_text(item.get("model"), f"analysisPlans[{index}].model")
        provider = require_text(
            item.get("provider"),
            f"analysisPlans[{index}].provider",
        )
        config = item.get("config")
        if not isinstance(config, Mapping):
            raise ValueError(f"analysisPlans[{index}].config must be an object")
        _reject_sensitive_config(config, f"analysisPlans[{index}].config")
        if config.get("model") not in (None, model):
            raise ValueError(
                f"analysisPlans[{index}].config model disagrees with model"
            )
        config_digest = sha256_json(config)
        if (
            "configDigest" in item
            and item.get("configDigest") != config_digest
        ):
            raise ValueError(
                f"analysisPlans[{index}].configDigest mismatch"
            )
        analysis_plans.append(
            {
                "runId": run_id,
                "model": model,
                "provider": provider,
                "config": dict(config),
                "configDigest": config_digest,
            }
        )
    run_ids = [item["runId"] for item in analysis_plans]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("analysisPlans runId values must be unique")

    judge_plans = []
    raw_judges = plan.get("judgePlans")
    if not isinstance(raw_judges, list) or not raw_judges:
        raise ValueError("judgePlans must be a non-empty array")
    for index, item in enumerate(raw_judges):
        judge_fields = {
            "judgmentId",
            "analysisRunId",
            "model",
            "expectedResponseModel",
            "promptVersion",
            "promptDigest",
            "config",
        }
        if isinstance(item, Mapping) and "configDigest" in item:
            judge_fields.add("configDigest")
        _exact_fields(
            item,
            judge_fields,
            f"judgePlans[{index}]",
        )
        judgment_id = require_text(
            item.get("judgmentId"),
            f"judgePlans[{index}].judgmentId",
        )
        analysis_run_id = require_text(
            item.get("analysisRunId"),
            f"judgePlans[{index}].analysisRunId",
        )
        if SAFE_ID.fullmatch(judgment_id) is None:
            raise ValueError(f"judgePlans[{index}].judgmentId is unsafe")
        if analysis_run_id not in set(run_ids):
            raise ValueError(
                f"judgePlans[{index}] references an unplanned analysis run"
            )
        model = require_text(item.get("model"), f"judgePlans[{index}].model")
        expected_model = require_text(
            item.get("expectedResponseModel"),
            f"judgePlans[{index}].expectedResponseModel",
        )
        prompt_version = require_text(
            item.get("promptVersion"),
            f"judgePlans[{index}].promptVersion",
        )
        prompt_digest = _digest(
            item.get("promptDigest"),
            f"judgePlans[{index}].promptDigest",
        )
        config = item.get("config")
        if not isinstance(config, Mapping):
            raise ValueError(f"judgePlans[{index}].config must be an object")
        _reject_sensitive_config(config, f"judgePlans[{index}].config")
        if config.get("model") != model:
            raise ValueError(
                f"judgePlans[{index}].config.model must equal model"
            )
        configured_expected = (
            config.get("expected_response_model") or model
        )
        if configured_expected != expected_model:
            raise ValueError(
                f"judgePlans[{index}] expected response model drift"
            )
        config_digest = sha256_json(config)
        if (
            "configDigest" in item
            and item.get("configDigest") != config_digest
        ):
            raise ValueError(f"judgePlans[{index}].configDigest mismatch")
        judge_plans.append(
            {
                "judgmentId": judgment_id,
                "analysisRunId": analysis_run_id,
                "model": model,
                "expectedResponseModel": expected_model,
                "promptVersion": prompt_version,
                "promptDigest": prompt_digest,
                "config": dict(config),
                "configDigest": config_digest,
            }
        )
    judgment_ids = [item["judgmentId"] for item in judge_plans]
    if len(judgment_ids) != len(set(judgment_ids)):
        raise ValueError("judgePlans judgmentId values must be unique")

    endpoints = _exact_fields(
        plan.get("endpoints"),
        {"primary", "secondary"},
        "endpoints",
    )
    primary = _exact_fields(
        endpoints.get("primary"),
        {"partition", "caseCount", "metrics"},
        "endpoints.primary",
    )
    if (
        primary.get("partition") != "sealed"
        or primary.get("caseCount") != 20
        or primary.get("metrics") != PRIMARY_METRICS
    ):
        raise ValueError("primary endpoint must be sealed 20-case micro P/R/F1")
    secondary = endpoints.get("secondary")
    if not isinstance(secondary, list) or len(secondary) != 2:
        raise ValueError("endpoints.secondary must contain exactly two scopes")
    observed_scopes: dict[str, Any] = {}
    for index, item in enumerate(secondary):
        _exact_fields(
            item,
            {"scope", "metrics"},
            f"endpoints.secondary[{index}]",
        )
        scope = str(item.get("scope") or "")
        if scope in observed_scopes:
            raise ValueError("secondary endpoint scopes must be unique")
        observed_scopes[scope] = item.get("metrics")
    if observed_scopes != SECONDARY_SCOPES:
        raise ValueError("secondary endpoints must be all_50 and development_30")

    bootstrap = _exact_fields(
        plan.get("bootstrap"),
        {"method", "iterations", "seed", "confidenceLevel"},
        "bootstrap",
    )
    if bootstrap.get("method") != "paired_pull_request_cluster_percentile":
        raise ValueError("bootstrap.method is invalid")
    _integer(bootstrap.get("iterations"), "bootstrap.iterations", 10_000)
    _integer(bootstrap.get("seed"), "bootstrap.seed")
    if float(bootstrap.get("confidenceLevel") or 0) != 0.95:
        raise ValueError("bootstrap.confidenceLevel must be 0.95")

    execution = _exact_fields(
        plan.get("executionPolicy"),
        {
            "analysisMaxCaseAttempts",
            "analysisTransportRetries",
            "judgeTransportRetries",
            "judgeStructuredOutputRetries",
            "missingCasePolicy",
            "zeroFindingPolicy",
            "stoppingRule",
        },
        "executionPolicy",
    )
    _integer(
        execution.get("analysisMaxCaseAttempts"),
        "executionPolicy.analysisMaxCaseAttempts",
        1,
    )
    _integer(
        execution.get("analysisTransportRetries"),
        "executionPolicy.analysisTransportRetries",
    )
    _integer(
        execution.get("judgeTransportRetries"),
        "executionPolicy.judgeTransportRetries",
    )
    _integer(
        execution.get("judgeStructuredOutputRetries"),
        "executionPolicy.judgeStructuredOutputRetries",
        1,
    )
    if execution.get("missingCasePolicy") != "fail_and_report_coverage":
        raise ValueError("executionPolicy.missingCasePolicy is invalid")
    if execution.get("zeroFindingPolicy") != "score_as_zero_candidates":
        raise ValueError("executionPolicy.zeroFindingPolicy is invalid")
    if (
        execution.get("stoppingRule")
        != "complete_all_planned_runs_without_sealed_result_model_selection"
    ):
        raise ValueError("executionPolicy.stoppingRule is invalid")

    controls = _text_list(
        plan.get("comparisonControls"),
        "comparisonControls",
    )
    if set(controls) != COMPARISON_CONTROLS:
        raise ValueError("comparisonControls are incomplete or unsupported")

    raw_post_fix = plan.get("postFixPlan")
    if not isinstance(raw_post_fix, Mapping):
        raise ValueError("postFixPlan must be an object")
    base_post_fix_fields = {
        "required",
        "snapshot",
        "endpoint",
        "sameBaseAndControls",
        "executionArtifactRequired",
    }
    derived_post_fix_fields = {
        "analysisBeforeUnseal",
        "judgingAfterUnseal",
        "scope",
        "outcomeDenominator",
        "analysisPairs",
        "judgmentPairs",
        "promptVersion",
        "promptDigest",
    }
    observed_post_fix_fields = frozenset(raw_post_fix)
    if observed_post_fix_fields not in {
        frozenset(base_post_fix_fields),
        frozenset(base_post_fix_fields | derived_post_fix_fields),
    }:
        raise ValueError("postFixPlan fields are invalid")
    post_fix_base = {
        key: raw_post_fix[key] for key in base_post_fix_fields
    }
    if post_fix_base != {
        "required": True,
        "snapshot": "verified_F",
        "endpoint": "per_gold_same_root_cause_disappearance",
        "sameBaseAndControls": True,
        "executionArtifactRequired": True,
    }:
        raise ValueError("postFixPlan must require a verified F-snapshot control")
    analysis_pairs = []
    for item in analysis_plans:
        post_fix_run_id = f"{item['runId']}:post-fix"
        if (
            SAFE_ID.fullmatch(post_fix_run_id) is None
            or post_fix_run_id in set(run_ids)
            or post_fix_run_id in set(judgment_ids)
        ):
            raise ValueError(
                "derived post-fix analysis run ID is unsafe or colliding"
            )
        analysis_pairs.append(
            {
                "primaryAnalysisRunId": item["runId"],
                "postFixAnalysisRunId": post_fix_run_id,
            }
        )
    analysis_pair_by_primary = {
        item["primaryAnalysisRunId"]: item["postFixAnalysisRunId"]
        for item in analysis_pairs
    }
    judgment_pairs = []
    for item in judge_plans:
        post_fix_judgment_id = f"{item['judgmentId']}:post-fix"
        if (
            SAFE_ID.fullmatch(post_fix_judgment_id) is None
            or post_fix_judgment_id in set(run_ids)
            or post_fix_judgment_id in set(judgment_ids)
            or post_fix_judgment_id
            in {
                pair["postFixAnalysisRunId"]
                for pair in analysis_pairs
            }
        ):
            raise ValueError(
                "derived post-fix judgment ID is unsafe or colliding"
            )
        judgment_pairs.append(
            {
                "primaryJudgmentId": item["judgmentId"],
                "primaryAnalysisRunId": item["analysisRunId"],
                "postFixAnalysisRunId": analysis_pair_by_primary[
                    item["analysisRunId"]
                ],
                "postFixJudgmentId": post_fix_judgment_id,
                "expectedResponseModel": item["expectedResponseModel"],
                "promptVersion": POST_FIX_PROMPT_VERSION,
                "promptDigest": POST_FIX_PROMPT_DIGEST,
            }
        )
    post_fix = {
        **post_fix_base,
        "analysisBeforeUnseal": True,
        "judgingAfterUnseal": True,
        "scope": "all_registered_cases_and_primary_matched_gold",
        "outcomeDenominator": "primary_H_true_positive_gold_only",
        "analysisPairs": analysis_pairs,
        "judgmentPairs": judgment_pairs,
        "promptVersion": POST_FIX_PROMPT_VERSION,
        "promptDigest": POST_FIX_PROMPT_DIGEST,
    }
    if observed_post_fix_fields == frozenset(
        base_post_fix_fields | derived_post_fix_fields
    ):
        if dict(raw_post_fix) != post_fix:
            raise ValueError(
                "postFixPlan derived IDs, chronology, or prompt identity drift"
            )

    evaluation = _exact_fields(
        plan.get("judgeEvaluationPlan"),
        {
            "mode",
            "caseIds",
            "subjectPolicy",
            "minimumIndependentRaters",
            "agreementMetric",
            "minimumAgreement",
            "disagreementResolution",
            "modelIdentityBlinded",
        },
        "judgeEvaluationPlan",
    )
    if evaluation.get("mode") not in {
        "development_calibration",
        "blinded_human_audit",
    }:
        raise ValueError("judgeEvaluationPlan.mode is invalid")
    case_ids = _text_list(
        evaluation.get("caseIds"),
        "judgeEvaluationPlan.caseIds",
        minimum=5,
    )
    if evaluation.get("subjectPolicy") != JUDGE_EVALUATION_SUBJECT_POLICY:
        raise ValueError(
            "judgeEvaluationPlan.subjectPolicy must audit every pair and "
            "novel decision"
        )
    _integer(
        evaluation.get("minimumIndependentRaters"),
        "judgeEvaluationPlan.minimumIndependentRaters",
        2,
    )
    if evaluation.get("agreementMetric") != "percent_pairwise_agreement":
        raise ValueError("judgeEvaluationPlan.agreementMetric is invalid")
    minimum_agreement = _rate(
        evaluation.get("minimumAgreement"),
        "judgeEvaluationPlan.minimumAgreement",
    )
    if minimum_agreement < 0.7:
        raise ValueError(
            "judgeEvaluationPlan.minimumAgreement must be at least 0.70"
        )
    if (
        evaluation.get("disagreementResolution")
        != "independent_adjudicator"
        or evaluation.get("modelIdentityBlinded") is not True
    ):
        raise ValueError("judgeEvaluationPlan policy is invalid")

    return {
        "studyId": study_id,
        "registeredAt": plan["registeredAt"],
        "analysisPlans": analysis_plans,
        "judgePlans": judge_plans,
        "endpoints": {
            "primary": dict(primary),
            "secondary": [dict(item) for item in secondary],
        },
        "bootstrap": dict(bootstrap),
        "executionPolicy": dict(execution),
        "comparisonControls": controls,
        "postFixPlan": dict(post_fix),
        "judgeEvaluationPlan": {
            **dict(evaluation),
            "caseIds": case_ids,
        },
        "allowedClaims": _text_list(
            plan.get("allowedClaims"),
            "allowedClaims",
        ),
        "prohibitedClaims": _text_list(
            plan.get("prohibitedClaims"),
            "prohibitedClaims",
        ),
    }


def create_study_registration(
    *,
    corpus_path: Path,
    plan_path: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    corpus = read_json(corpus_path)
    if not isinstance(corpus, Mapping):
        raise ValueError("corpus must be an object")
    summary = _strict_corpus(corpus)
    plan = read_json(plan_path)
    if not isinstance(plan, Mapping):
        raise ValueError("study plan must be an object")
    normalized = _normalized_registration_plan(plan)
    corpus_cases = {
        str(case["caseId"]): case
        for case in corpus["cases"]
        if isinstance(case, Mapping)
    }
    planned_cases = normalized["judgeEvaluationPlan"]["caseIds"]
    if any(case_id not in corpus_cases for case_id in planned_cases):
        raise ValueError("judgeEvaluationPlan contains an unknown case")
    if (
        normalized["judgeEvaluationPlan"]["mode"]
        == "development_calibration"
        and any(
            corpus_cases[case_id].get("partition") != "development"
            for case_id in planned_cases
        )
    ):
        raise ValueError(
            "development calibration may contain development cases only"
        )
    result = {
        "kind": REGISTRATION_KIND,
        **normalized,
        "executionCorpusDigest": build_execution_corpus(corpus)[
            "executionCorpusDigest"
        ],
        "corpus": {
            "corpusId": summary["corpusId"],
            "corpusDigest": summary["corpusDigest"],
            "caseCount": 50,
            "partitionCounts": {
                "development": 30,
                "sealed": 20,
            },
            "partitionPolicy": (
                "deterministic_label_blind_stratified_by_size_band"
            ),
        },
        "sealedCommitment": {
            "algorithm": "sha256",
            "digest": _sealed_commitment(corpus),
        },
    }
    result["registrationDigest"] = sha256_json(result)
    validate_study_registration(result, corpus)
    if output_path is not None:
        write_json(output_path, result)
    return result


def validate_study_registration(
    value: Any,
    corpus: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("study registration must be an object")
    digest = _artifact_digest(
        value,
        field="registrationDigest",
        kind=REGISTRATION_KIND,
    )
    summary = _strict_corpus(corpus)
    expected_fields = {
        "kind",
        "studyId",
        "registeredAt",
        "analysisPlans",
        "judgePlans",
        "endpoints",
        "bootstrap",
        "executionPolicy",
        "comparisonControls",
        "postFixPlan",
        "judgeEvaluationPlan",
        "allowedClaims",
        "prohibitedClaims",
        "executionCorpusDigest",
        "corpus",
        "sealedCommitment",
        "registrationDigest",
    }
    _exact_fields(value, expected_fields, "study registration")
    normalized = _normalized_registration_plan(
        {
            key: value[key]
            for key in (
                "studyId",
                "registeredAt",
                "analysisPlans",
                "judgePlans",
                "endpoints",
                "bootstrap",
                "executionPolicy",
                "comparisonControls",
                "postFixPlan",
                "judgeEvaluationPlan",
                "allowedClaims",
                "prohibitedClaims",
            )
        }
    )
    if normalized["analysisPlans"] != value["analysisPlans"]:
        raise ValueError("analysisPlans normalization or digest drift")
    if normalized["judgePlans"] != value["judgePlans"]:
        raise ValueError("judgePlans normalization or digest drift")
    if value.get("corpus") != {
        "corpusId": summary["corpusId"],
        "corpusDigest": summary["corpusDigest"],
        "caseCount": 50,
        "partitionCounts": {"development": 30, "sealed": 20},
        "partitionPolicy": (
            "deterministic_label_blind_stratified_by_size_band"
        ),
    }:
        raise ValueError("study registration corpus binding mismatch")
    expected_execution_digest = build_execution_corpus(corpus)[
        "executionCorpusDigest"
    ]
    if value.get("executionCorpusDigest") != expected_execution_digest:
        raise ValueError(
            "study registration execution-corpus binding mismatch"
        )
    if value.get("sealedCommitment") != {
        "algorithm": "sha256",
        "digest": _sealed_commitment(corpus),
    }:
        raise ValueError("study registration sealed commitment mismatch")
    cases = {
        str(case["caseId"]): case
        for case in corpus["cases"]
        if isinstance(case, Mapping)
    }
    planned_cases = normalized["judgeEvaluationPlan"]["caseIds"]
    if any(case_id not in cases for case_id in planned_cases):
        raise ValueError("judge evaluation plan contains unknown cases")
    if (
        normalized["judgeEvaluationPlan"]["mode"]
        == "development_calibration"
        and any(
            cases[case_id].get("partition") != "development"
            for case_id in planned_cases
        )
    ):
        raise ValueError("development calibration includes a sealed case")
    return {
        "studyId": normalized["studyId"],
        "registrationDigest": digest,
        "registeredAt": value["registeredAt"],
        "corpusDigest": summary["corpusDigest"],
        "executionCorpusDigest": expected_execution_digest,
        "analysisRuns": len(normalized["analysisPlans"]),
        "judgePlans": len(normalized["judgePlans"]),
    }


def _validated_run(
    value: Any,
    corpus_digest: str,
    execution_corpus_digest: str,
) -> Mapping[str, Any]:
    run = _validate_digest_artifact(
        value,
        kind=RUN_KIND,
        digest_field="runDigest",
    )
    if (
        run.get("corpusDigest") != corpus_digest
        or run.get("executionCorpusDigest") != execution_corpus_digest
    ):
        raise ValueError(
            "analysis run belongs to another corpus or execution projection"
        )
    started_at = _timestamp(run.get("startedAt"), "analysis run startedAt")
    completed_at = _timestamp(
        run.get("completedAt"),
        "analysis run completedAt",
    )
    if started_at > completed_at:
        raise ValueError("analysis run completedAt predates startedAt")
    if run.get("status") != "completed":
        raise ValueError("analysis run status must be completed")
    selected_ids = run.get("selectedCaseIds")
    if (
        not isinstance(selected_ids, list)
        or len(selected_ids) != 50
        or len(selected_ids) != len(set(selected_ids))
        or any(not isinstance(case_id, str) for case_id in selected_ids)
    ):
        raise ValueError(
            "publication analysis run must select exactly 50 unique cases"
        )
    cases = run.get("cases")
    if not isinstance(cases, list):
        raise ValueError("analysis run cases must be an array")
    observed_ids = [
        str(item.get("caseId") or "")
        for item in cases
        if isinstance(item, Mapping)
    ]
    if (
        len(observed_ids) != len(cases)
        or set(observed_ids) != set(selected_ids)
        or len(observed_ids) != len(set(observed_ids))
        or any(
            not isinstance(item, Mapping)
            or item.get("status") != "completed"
            for item in cases
        )
    ):
        raise ValueError(
            "analysis run must complete every selected case exactly once"
        )
    return run


def _validated_post_fix_run(
    value: Any,
    corpus_digest: str,
    execution_corpus_digest: str,
) -> Mapping[str, Any]:
    run = _validate_digest_artifact(
        value,
        kind=POST_FIX_RUN_KIND,
        digest_field="runDigest",
    )
    if (
        run.get("corpusDigest") != corpus_digest
        or run.get("executionCorpusDigest") != execution_corpus_digest
    ):
        raise ValueError(
            "post-fix analysis run belongs to another corpus or execution "
            "projection"
        )
    started_at = _timestamp(
        run.get("startedAt"),
        "post-fix analysis run startedAt",
    )
    completed_at = _timestamp(
        run.get("completedAt"),
        "post-fix analysis run completedAt",
    )
    if started_at > completed_at or run.get("status") != "completed":
        raise ValueError("post-fix analysis run is not completed")
    selected_ids = run.get("selectedCaseIds")
    cases = run.get("cases")
    observed_ids = [
        str(item.get("caseId") or "")
        for item in cases or []
        if isinstance(item, Mapping)
    ]
    if (
        run.get("snapshotRole") != "verified_F"
        or not isinstance(selected_ids, list)
        or len(selected_ids) != 50
        or len(selected_ids) != len(set(selected_ids))
        or not isinstance(cases, list)
        or observed_ids != selected_ids
        or len(observed_ids) != len(cases)
        or any(
            not isinstance(item, Mapping)
            or item.get("status") != "completed"
            for item in cases
        )
    ):
        raise ValueError(
            "post-fix analysis run must complete all 50 registered F cases"
        )
    return run


def create_seal_ledger(
    *,
    corpus_path: Path,
    registration_path: Path,
    analysis_run_paths: Sequence[Path],
    post_fix_analysis_run_paths: Sequence[Path] = (),
    ledger_plan_path: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    corpus = read_json(corpus_path)
    registration = read_json(registration_path)
    if not isinstance(corpus, Mapping) or not isinstance(
        registration, Mapping
    ):
        raise ValueError("corpus and registration must be objects")
    validate_study_registration(registration, corpus)
    plan = read_json(ledger_plan_path)
    if not isinstance(plan, Mapping):
        raise ValueError("seal ledger plan must be an object")
    _exact_fields(
        plan,
        {"generatedAt", "custodians", "accessEvents", "unseal"},
        "seal ledger plan",
    )
    runs = [
        _validated_run(
            read_json(path),
            str(corpus["corpusDigest"]),
            str(registration["executionCorpusDigest"]),
        )
        for path in analysis_run_paths
    ]
    post_fix_runs = [
        _validated_post_fix_run(
            read_json(path),
            str(corpus["corpusDigest"]),
            str(registration["executionCorpusDigest"]),
        )
        for path in post_fix_analysis_run_paths
    ]
    result = {
        "kind": SEAL_LEDGER_KIND,
        "studyId": registration["studyId"],
        "registrationDigest": registration["registrationDigest"],
        "corpusDigest": corpus["corpusDigest"],
        "executionCorpusDigest": registration["executionCorpusDigest"],
        "sealedCommitment": dict(registration["sealedCommitment"]),
        "generatedAt": plan["generatedAt"],
        "custodians": list(plan["custodians"]),
        "accessEvents": list(plan["accessEvents"]),
        "unseal": dict(plan["unseal"]),
        "boundRuns": [
            {
                "runId": run["runId"],
                "runDigest": run["runDigest"],
                "startedAt": run["startedAt"],
                "completedAt": run["completedAt"],
                "analysisModel": run["analysisModel"],
                "analysisProvider": run["analysisProvider"],
                "analysisConfigDigest": run["analysisConfigDigest"],
                "executionCorpusDigest": run["executionCorpusDigest"],
            }
            for run in sorted(runs, key=lambda item: str(item["runId"]))
        ],
        "boundPostFixRuns": [
            {
                "runId": run["runId"],
                "runDigest": run["runDigest"],
                "pairedPrimaryRunId": run["pairedPrimaryRunId"],
                "pairedPrimaryRunDigest": run["pairedPrimaryRunDigest"],
                "startedAt": run["startedAt"],
                "completedAt": run["completedAt"],
                "analysisModel": run["analysisModel"],
                "analysisProvider": run["analysisProvider"],
                "analysisConfigDigest": run["analysisConfigDigest"],
                "executionCorpusDigest": run["executionCorpusDigest"],
                "postFixReplayPlanDigest": run[
                    "postFixReplayPlanDigest"
                ],
                "replayLockDigest": run["replayLockDigest"],
                "replayAttestationDigest": run[
                    "replayAttestationDigest"
                ],
            }
            for run in sorted(
                post_fix_runs,
                key=lambda item: str(item["runId"]),
            )
        ],
    }
    result["sealLedgerDigest"] = sha256_json(result)
    validate_seal_ledger(
        result,
        registration,
        corpus,
        runs,
        post_fix_runs,
    )
    if output_path is not None:
        write_json(output_path, result)
    return result


def validate_seal_ledger(
    value: Any,
    registration: Mapping[str, Any],
    corpus: Mapping[str, Any],
    analysis_runs: Sequence[Mapping[str, Any]],
    post_fix_analysis_runs: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("seal ledger must be an object")
    digest = _artifact_digest(
        value,
        field="sealLedgerDigest",
        kind=SEAL_LEDGER_KIND,
    )
    validate_study_registration(registration, corpus)
    _exact_fields(
        value,
        {
            "kind",
            "studyId",
            "registrationDigest",
            "corpusDigest",
            "executionCorpusDigest",
            "sealedCommitment",
            "generatedAt",
            "custodians",
            "accessEvents",
            "unseal",
            "boundRuns",
            "boundPostFixRuns",
            "sealLedgerDigest",
        },
        "seal ledger",
    )
    if (
        value.get("studyId") != registration["studyId"]
        or value.get("registrationDigest")
        != registration["registrationDigest"]
        or value.get("corpusDigest") != corpus["corpusDigest"]
        or value.get("executionCorpusDigest")
        != registration["executionCorpusDigest"]
        or value.get("sealedCommitment")
        != registration["sealedCommitment"]
    ):
        raise ValueError("seal ledger registration/corpus binding mismatch")
    registered_at = _timestamp(
        registration["registeredAt"],
        "registration.registeredAt",
    )
    generated_at = _timestamp(value.get("generatedAt"), "generatedAt")

    custodians = value.get("custodians")
    if not isinstance(custodians, list) or len(custodians) < 2:
        raise ValueError("seal ledger requires at least two custodians")
    custodian_ids = []
    for index, item in enumerate(custodians):
        _exact_fields(
            item,
            {"custodianId", "role"},
            f"custodians[{index}]",
        )
        custodian_id = require_text(
            item.get("custodianId"),
            f"custodians[{index}].custodianId",
        )
        require_text(item.get("role"), f"custodians[{index}].role")
        custodian_ids.append(custodian_id)
    if len(custodian_ids) != len(set(custodian_ids)):
        raise ValueError("custodian IDs must be unique")

    unseal = _exact_fields(
        value.get("unseal"),
        {"at", "authorizedBy", "reason", "commitmentDigest"},
        "unseal",
    )
    unsealed_at = _timestamp(unseal.get("at"), "unseal.at")
    authorized = _text_list(
        unseal.get("authorizedBy"),
        "unseal.authorizedBy",
        minimum=2,
    )
    if not set(authorized).issubset(set(custodian_ids)):
        raise ValueError("unseal authorization includes a non-custodian")
    require_text(unseal.get("reason"), "unseal.reason")
    if unseal.get("commitmentDigest") != registration[
        "sealedCommitment"
    ]["digest"]:
        raise ValueError("unseal commitment digest mismatch")
    if not registered_at < unsealed_at <= generated_at:
        raise ValueError(
            "registration must precede unseal, and unseal must precede ledger"
        )

    events = value.get("accessEvents")
    if not isinstance(events, list) or not events:
        raise ValueError("accessEvents must be non-empty")
    event_ids = []
    event_times = []
    unseal_events = 0
    for index, item in enumerate(events):
        _exact_fields(
            item,
            {
                "eventId",
                "at",
                "actor",
                "action",
                "partition",
                "purpose",
            },
            f"accessEvents[{index}]",
        )
        event_id = require_text(
            item.get("eventId"),
            f"accessEvents[{index}].eventId",
        )
        event_ids.append(event_id)
        at = _timestamp(item.get("at"), f"accessEvents[{index}].at")
        event_times.append(at)
        if at < registered_at or at > generated_at:
            raise ValueError(
                "access event must be between registration and ledger generation"
            )
        actor = require_text(
            item.get("actor"),
            f"accessEvents[{index}].actor",
        )
        if actor not in set(custodian_ids):
            raise ValueError("access event actor is not a custodian")
        action = item.get("action")
        if action not in {
            "commitment_created",
            "custody_transfer",
            "development_access",
            "sealed_unseal",
            "sealed_access",
        }:
            raise ValueError("access event action is invalid")
        partition = item.get("partition")
        if partition not in {"development", "sealed"}:
            raise ValueError("access event partition is invalid")
        require_text(
            item.get("purpose"),
            f"accessEvents[{index}].purpose",
        )
        if action == "development_access" and partition != "development":
            raise ValueError("development access event has wrong partition")
        if action in {
            "commitment_created",
            "custody_transfer",
            "sealed_unseal",
            "sealed_access",
        } and partition != "sealed":
            raise ValueError("sealed custody event has wrong partition")
        if action == "commitment_created" and at != registered_at:
            raise ValueError(
                "sealed commitment must be recorded at registration time"
            )
        if action == "sealed_unseal":
            unseal_events += 1
            if at != unsealed_at or actor not in set(authorized):
                raise ValueError("sealed_unseal event disagrees with unseal")
        if action == "sealed_access" and at < unsealed_at:
            raise ValueError("sealed labels were accessed before unseal")
    if event_times != sorted(event_times):
        raise ValueError("access events must be chronological")
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("access event IDs must be unique")
    if (
        sum(
            1
            for item in events
            if item.get("action") == "commitment_created"
        )
        != 1
        or unseal_events != 1
        or not any(item.get("action") == "sealed_access" for item in events)
    ):
        raise ValueError(
            "ledger requires one commitment, one unseal, and sealed access"
        )

    plans = {
        item["runId"]: item for item in registration["analysisPlans"]
    }
    supplied_runs = {}
    for raw in analysis_runs:
        run = _validated_run(
            raw,
            str(corpus["corpusDigest"]),
            str(registration["executionCorpusDigest"]),
        )
        run_id = str(run.get("runId") or "")
        if not run_id or run_id in supplied_runs:
            raise ValueError("analysis run IDs must be unique")
        supplied_runs[run_id] = run
    if set(supplied_runs) != set(plans):
        raise ValueError("seal ledger must cover exactly the planned runs")
    expected_bound = []
    for run_id in sorted(plans):
        run = supplied_runs[run_id]
        plan = plans[run_id]
        started_at = _timestamp(run.get("startedAt"), f"{run_id}.startedAt")
        completed_at = _timestamp(
            run.get("completedAt"),
            f"{run_id}.completedAt",
        )
        if not (
            registered_at
            < started_at
            <= completed_at
            <= unsealed_at
            <= generated_at
        ):
            raise ValueError(
                "every preregistered analysis run must complete before unseal"
            )
        if (
            run.get("analysisModel") != plan["model"]
            or run.get("analysisProvider") != plan["provider"]
            or run.get("analysisConfig") != plan["config"]
            or run.get("analysisConfigDigest") != plan["configDigest"]
        ):
            raise ValueError(f"{run_id} differs from its preregistered plan")
        expected_bound.append(
            {
                "runId": run_id,
                "runDigest": run["runDigest"],
                "startedAt": run["startedAt"],
                "completedAt": run["completedAt"],
                "analysisModel": run["analysisModel"],
                "analysisProvider": run["analysisProvider"],
                "analysisConfigDigest": run["analysisConfigDigest"],
                "executionCorpusDigest": run["executionCorpusDigest"],
            }
        )
    if value.get("boundRuns") != expected_bound:
        raise ValueError("seal ledger boundRuns projection mismatch")

    post_fix_pairs = {
        str(item["postFixAnalysisRunId"]): item
        for item in registration["postFixPlan"]["analysisPairs"]
    }
    supplied_post_fix_runs = {}
    for raw in post_fix_analysis_runs:
        run = _validated_post_fix_run(
            raw,
            str(corpus["corpusDigest"]),
            str(registration["executionCorpusDigest"]),
        )
        run_id = str(run.get("runId") or "")
        if not run_id or run_id in supplied_post_fix_runs:
            raise ValueError("post-fix analysis run IDs must be unique")
        supplied_post_fix_runs[run_id] = run
    if set(supplied_post_fix_runs) != set(post_fix_pairs):
        raise ValueError(
            "seal ledger must cover every registered post-fix run exactly"
        )
    expected_post_fix_bound = []
    for run_id in sorted(post_fix_pairs):
        run = supplied_post_fix_runs[run_id]
        pair = post_fix_pairs[run_id]
        primary = supplied_runs[str(pair["primaryAnalysisRunId"])]
        started_at = _timestamp(
            run.get("startedAt"),
            f"{run_id}.startedAt",
        )
        completed_at = _timestamp(
            run.get("completedAt"),
            f"{run_id}.completedAt",
        )
        if not (
            registered_at
            < started_at
            <= completed_at
            <= unsealed_at
            <= generated_at
        ):
            raise ValueError(
                "every registered post-fix analysis run must complete "
                "before unseal"
            )
        if (
            run.get("pairedPrimaryRunId") != primary["runId"]
            or run.get("pairedPrimaryRunDigest") != primary["runDigest"]
            or run.get("analysisModel") != primary.get("analysisModel")
            or run.get("analysisProvider") != primary.get("analysisProvider")
            or run.get("analysisConfig") != primary.get("analysisConfig")
            or run.get("analysisConfigDigest")
            != primary.get("analysisConfigDigest")
            or run.get("analysisModelRoles")
            != primary.get("analysisModelRoles")
            or run.get("transport") != primary.get("transport")
            or run.get("findingSemantics")
            != primary.get("findingSemantics")
            or run.get("attemptPolicy") != primary.get("attemptPolicy")
            or run.get("indexReceiptsBefore")
            != primary.get("indexReceiptsBefore")
            or run.get("indexReceiptsAfter")
            != primary.get("indexReceiptsAfter")
            or not isinstance(run.get("postFixReplayPlanDigest"), str)
            or not isinstance(run.get("replayLockDigest"), str)
            or not isinstance(run.get("replayAttestationDigest"), str)
        ):
            raise ValueError(
                f"{run_id} differs from its paired preregistered H controls"
            )
        for field in (
            "postFixReplayPlanDigest",
            "replayLockDigest",
            "replayAttestationDigest",
        ):
            _digest(run.get(field), f"{run_id}.{field}")
        expected_post_fix_bound.append(
            {
                "runId": run_id,
                "runDigest": run["runDigest"],
                "pairedPrimaryRunId": run["pairedPrimaryRunId"],
                "pairedPrimaryRunDigest": run["pairedPrimaryRunDigest"],
                "startedAt": run["startedAt"],
                "completedAt": run["completedAt"],
                "analysisModel": run["analysisModel"],
                "analysisProvider": run["analysisProvider"],
                "analysisConfigDigest": run["analysisConfigDigest"],
                "executionCorpusDigest": run["executionCorpusDigest"],
                "postFixReplayPlanDigest": run[
                    "postFixReplayPlanDigest"
                ],
                "replayLockDigest": run["replayLockDigest"],
                "replayAttestationDigest": run[
                    "replayAttestationDigest"
                ],
            }
        )
    if value.get("boundPostFixRuns") != expected_post_fix_bound:
        raise ValueError(
            "seal ledger boundPostFixRuns projection mismatch"
        )
    return {
        "studyId": registration["studyId"],
        "sealLedgerDigest": digest,
        "unsealedAt": unseal["at"],
        "boundRuns": len(expected_bound),
        "boundPostFixRuns": len(expected_post_fix_bound),
        "accessEvents": len(events),
    }


def _pairwise_agreement(
    records: Sequence[Mapping[str, Any]],
) -> float:
    grouped: dict[str, list[str]] = {}
    for item in records:
        grouped.setdefault(str(item["subjectId"]), []).append(
            str(item["humanVerdict"])
        )
    agreements = 0
    comparisons = 0
    for verdicts in grouped.values():
        for left, right in itertools.combinations(verdicts, 2):
            comparisons += 1
            agreements += int(left == right)
    if comparisons == 0:
        raise ValueError("judge evaluation has no independent record pairs")
    return round(agreements / comparisons, 6)


def _planned_judgments(
    registration: Mapping[str, Any],
    judgments: Sequence[Mapping[str, Any]],
    *,
    corpus: Mapping[str, Any],
    analysis_runs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    plans = {
        str(item["judgmentId"]): item
        for item in registration["judgePlans"]
    }
    observed: dict[str, Mapping[str, Any]] = {}
    for judgment in judgments:
        judgment_id = str(judgment.get("judgmentId") or "")
        if not judgment_id or judgment_id in observed:
            raise ValueError("judgment IDs must be non-empty and unique")
        observed[judgment_id] = judgment
    if set(observed) != set(plans):
        raise ValueError(
            "judgments do not exactly cover the preregistered plans"
        )
    for judgment_id, plan in plans.items():
        judgment = observed[judgment_id]
        if (
            judgment.get("analysisRunId") != plan["analysisRunId"]
            or judgment.get("judgeModel") != plan["model"]
            or judgment.get("judgeConfig") != plan["config"]
            or judgment.get("judgeConfigDigest") != plan["configDigest"]
            or judgment.get("promptVersion") != plan["promptVersion"]
            or judgment.get("promptDigest") != plan["promptDigest"]
        ):
            raise ValueError(
                f"judgment {judgment_id} differs from its preregistered plan"
            )
        run = analysis_runs.get(str(plan["analysisRunId"]))
        if run is None:
            raise ValueError(
                f"judgment {judgment_id} has no bound analysis run"
            )
        _validate_judgment_universe(
            judgment,
            run=run,
            corpus=corpus,
        )
    return observed


def _expected_gold_issues(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
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
        for index, item in enumerate(case["goldenComments"], start=1)
    ]


def _expected_candidate_findings(
    run_case: Mapping[str, Any],
) -> list[dict[str, Any]]:
    findings = run_case.get("findings")
    if not isinstance(findings, list) or any(
        not isinstance(item, Mapping) for item in findings
    ):
        raise ValueError("analysis case findings must be an object array")
    return [
        {
            "candidateId": f"C{index:03d}",
            **{
                key: value
                for key, value in item.items()
                if key != "raw"
            },
        }
        for index, item in enumerate(findings, start=1)
    ]


def _validate_repeat_summary(
    value: Mapping[str, Any],
    *,
    allowed_verdicts: set[str],
    field: str,
) -> None:
    confidence = value.get("confidence")
    agreement = value.get("repeatAgreement")
    repeats = value.get("repeatVerdicts")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
        or isinstance(agreement, bool)
        or not isinstance(agreement, (int, float))
        or not 0 <= float(agreement) <= 1
        or not isinstance(repeats, Mapping)
        or not repeats
        or any(
            verdict not in allowed_verdicts
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            for verdict, count in repeats.items()
        )
    ):
        raise ValueError(f"{field} repeat/confidence summary is invalid")


def _validate_pair_decision(
    value: Mapping[str, Any],
    *,
    field: str,
) -> None:
    required = {
        "goldId",
        "candidateId",
        "specific_issue",
        "grounded_at_snapshot",
        "same_root_cause",
        "same_failure_or_consequence",
        "compatible_required_change",
        "location_relation",
        "verdict",
        "confidence",
        "repeatAgreement",
        "repeatVerdicts",
    }
    if frozenset(value) not in {
        frozenset(required),
        frozenset(required | {"rationale"}),
    }:
        raise ValueError(f"{field} fields are invalid")
    if (
        any(
            value.get(key) not in YES_NO_UNCLEAR
            for key in (
                "specific_issue",
                "grounded_at_snapshot",
                "same_root_cause",
                "same_failure_or_consequence",
                "compatible_required_change",
            )
        )
        or value.get("location_relation") not in LOCATION_RELATIONS
        or value.get("verdict") not in MATCH_VERDICTS
        or (
            "rationale" in value
            and not isinstance(value.get("rationale"), str)
        )
    ):
        raise ValueError(f"{field} rubric is invalid")
    _validate_repeat_summary(
        value,
        allowed_verdicts=MATCH_VERDICTS,
        field=field,
    )


def _validate_novel_decision(
    value: Mapping[str, Any],
    *,
    field: str,
) -> None:
    required = {
        "candidateId",
        "verdict",
        "grounded_at_snapshot",
        "actionable",
        "confidence",
        "repeatAgreement",
        "repeatVerdicts",
    }
    if frozenset(value) not in {
        frozenset(required),
        frozenset(required | {"rationale"}),
    }:
        raise ValueError(f"{field} fields are invalid")
    if (
        value.get("verdict") not in NOVEL_VERDICTS
        or value.get("grounded_at_snapshot") not in YES_NO_UNCLEAR
        or value.get("actionable") not in YES_NO_UNCLEAR
        or (
            "rationale" in value
            and not isinstance(value.get("rationale"), str)
        )
    ):
        raise ValueError(f"{field} rubric is invalid")
    _validate_repeat_summary(
        value,
        allowed_verdicts=NOVEL_VERDICTS,
        field=field,
    )


def _validate_judgment_universe(
    judgment: Mapping[str, Any],
    *,
    run: Mapping[str, Any],
    corpus: Mapping[str, Any],
) -> None:
    if (
        judgment.get("analysisRunDigest") != run.get("runDigest")
        or judgment.get("analysisModel") != run.get("analysisModel")
    ):
        raise ValueError("judgment analysis-run binding mismatch")
    if _timestamp(
        judgment.get("createdAt"),
        "judgment.createdAt",
    ) < _timestamp(run.get("completedAt"), "analysis run completedAt"):
        raise ValueError("judgment predates its completed analysis run")
    run_cases_raw = run.get("cases")
    if not isinstance(run_cases_raw, list):
        raise ValueError("bound analysis run cases must be an array")
    run_cases = {
        str(item.get("caseId")): item
        for item in run_cases_raw
        if isinstance(item, Mapping)
    }
    if len(run_cases) != len(run_cases_raw):
        raise ValueError("bound analysis run case IDs are not unique")
    corpus_cases = {
        str(item["caseId"]): item
        for item in corpus["cases"]
        if isinstance(item, Mapping)
    }
    raw_cases = judgment.get("cases")
    if not isinstance(raw_cases, list) or any(
        not isinstance(item, Mapping) for item in raw_cases
    ):
        raise ValueError("judgment cases must be an object array")
    observed_case_ids = [str(item.get("caseId") or "") for item in raw_cases]
    if (
        observed_case_ids != list(corpus_cases)
        or len(observed_case_ids) != len(set(observed_case_ids))
        or set(run_cases) != set(corpus_cases)
    ):
        raise ValueError(
            "judgment must cover every corpus and analysis case exactly once"
        )
    for case in raw_cases:
        case_id = str(case["caseId"])
        corpus_case = corpus_cases[case_id]
        run_case = run_cases[case_id]
        if case.get("status") != "scored":
            raise ValueError(f"judgment case {case_id} was not scored")
        if run_case.get("status") != "completed":
            raise ValueError(
                f"judgment case {case_id} has an incomplete analysis case"
            )
        expected_input_digest = sha256_json(
            {
                "corpusCase": corpus_case,
                "analysisCase": run_case,
                "analysisRunDigest": run["runDigest"],
                "judgeConfigDigest": judgment["judgeConfigDigest"],
                "promptVersion": judgment["promptVersion"],
            }
        )
        if (
            case.get("caseInputDigest") != expected_input_digest
            or case.get("judgeConfigDigest")
            != judgment.get("judgeConfigDigest")
            or case.get("sizeBand") != corpus_case.get("sizeBand")
            or case.get("partition") != corpus_case.get("partition")
        ):
            raise ValueError(f"judgment case {case_id} binding mismatch")
        gold_issues = _expected_gold_issues(corpus_case)
        candidate_findings = _expected_candidate_findings(run_case)
        if (
            case.get("goldCount") != len(gold_issues)
            or case.get("candidateCount") != len(candidate_findings)
            or case.get("goldIssues") != gold_issues
            or case.get("candidateFindings") != candidate_findings
        ):
            raise ValueError(
                f"judgment case {case_id} gold/candidate projection mismatch"
            )
        gold_ids = [item["goldId"] for item in gold_issues]
        candidate_ids = [
            item["candidateId"] for item in candidate_findings
        ]
        expected_pairs = {
            (gold_id, candidate_id)
            for gold_id in gold_ids
            for candidate_id in candidate_ids
        }
        pair_values = case.get("pairJudgments")
        if not isinstance(pair_values, list) or any(
            not isinstance(item, Mapping) for item in pair_values
        ):
            raise ValueError(
                f"judgment case {case_id} pair judgments are invalid"
            )
        observed_pairs: dict[tuple[str, str], Mapping[str, Any]] = {}
        for pair_index, pair in enumerate(pair_values):
            _validate_pair_decision(
                pair,
                field=(
                    f"judgment case {case_id} pairJudgments[{pair_index}]"
                ),
            )
            key = (
                str(pair.get("goldId") or ""),
                str(pair.get("candidateId") or ""),
            )
            if key in observed_pairs or pair.get("verdict") not in MATCH_VERDICTS:
                raise ValueError(
                    f"judgment case {case_id} pair judgments are invalid"
                )
            observed_pairs[key] = pair
        if set(observed_pairs) != expected_pairs:
            raise ValueError(
                f"judgment case {case_id} omits or adds a pair decision"
            )
        assignments = case.get("assignments")
        if not isinstance(assignments, list) or any(
            not isinstance(item, Mapping) for item in assignments
        ):
            raise ValueError(
                f"judgment case {case_id} assignments are invalid"
            )
        expected_assignments = _maximum_assignment(
            len(gold_ids),
            len(candidate_ids),
            pair_values,
        )
        if assignments != expected_assignments:
            raise ValueError(
                f"judgment case {case_id} assignment differs from the "
                "production maximum assignment"
            )
        assigned_gold = {
            str(item["goldId"]) for item in expected_assignments
        }
        assigned_candidates = {
            str(item["candidateId"]) for item in expected_assignments
        }
        unmatched_gold = [
            gold_id for gold_id in gold_ids if gold_id not in assigned_gold
        ]
        unmatched_candidates = [
            candidate_id
            for candidate_id in candidate_ids
            if candidate_id not in assigned_candidates
        ]
        if (
            case.get("unmatchedGold") != unmatched_gold
            or case.get("unmatchedCandidates") != unmatched_candidates
        ):
            raise ValueError(
                f"judgment case {case_id} unmatched projection mismatch"
            )
        novel = case.get("novelFindingJudgments")
        if not isinstance(novel, list) or any(
            not isinstance(item, Mapping) for item in novel
        ):
            raise ValueError(
                f"judgment case {case_id} novel judgments are invalid"
            )
        expected_novel_ids = (
            unmatched_candidates
            if judgment["judgeConfig"].get(
                "validate_unmatched_findings",
                True,
            )
            else []
        )
        observed_novel_ids = []
        for novel_index, item in enumerate(novel):
            _validate_novel_decision(
                item,
                field=(
                    f"judgment case {case_id} "
                    f"novelFindingJudgments[{novel_index}]"
                ),
            )
            observed_novel_ids.append(str(item.get("candidateId") or ""))
        if (
            observed_novel_ids != expected_novel_ids
            or len(observed_novel_ids) != len(set(observed_novel_ids))
            or any(item.get("verdict") not in NOVEL_VERDICTS for item in novel)
        ):
            raise ValueError(
                f"judgment case {case_id} omits or adds a novel decision"
            )
        case_payload = dict(case)
        declared_case_digest = case_payload.pop("caseDigest", None)
        case_payload.pop("rawJudgment", None)
        if declared_case_digest != sha256_json(case_payload):
            raise ValueError(f"judgment case {case_id} digest mismatch")


def _audit_subjects(
    registration: Mapping[str, Any],
    judgments: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    case_ids = set(registration["judgeEvaluationPlan"]["caseIds"])
    subjects: list[dict[str, Any]] = []
    for judgment_id in sorted(judgments):
        judgment = judgments[judgment_id]
        cases = judgment.get("cases")
        if not isinstance(cases, list):
            raise ValueError(f"judgment {judgment_id} cases must be an array")
        selected: dict[str, Mapping[str, Any]] = {}
        for case in cases:
            if not isinstance(case, Mapping):
                raise ValueError(
                    f"judgment {judgment_id} contains a non-object case"
                )
            case_id = str(case.get("caseId") or "")
            if case_id in case_ids:
                if case_id in selected:
                    raise ValueError(
                        f"judgment {judgment_id} duplicates case {case_id}"
                    )
                selected[case_id] = case
        if set(selected) != case_ids:
            raise ValueError(
                f"judgment {judgment_id} does not cover every audit case"
            )
        for case_id in sorted(selected):
            case = selected[case_id]
            if case.get("status") != "scored":
                raise ValueError(
                    f"judgment {judgment_id} case {case_id} was not scored"
                )
            decision_groups = (
                ("pair", case.get("pairJudgments"), MATCH_VERDICTS),
                (
                    "novel",
                    case.get("novelFindingJudgments"),
                    NOVEL_VERDICTS,
                ),
            )
            for decision_kind, raw_decisions, allowed_verdicts in decision_groups:
                if not isinstance(raw_decisions, list):
                    raise ValueError(
                        f"{judgment_id}/{case_id} {decision_kind} decisions "
                        "must be an array"
                    )
                seen_keys: set[tuple[str | None, str]] = set()
                for decision in raw_decisions:
                    if not isinstance(decision, Mapping):
                        raise ValueError("judge decision must be an object")
                    candidate_id = require_text(
                        decision.get("candidateId"),
                        f"{decision_kind}.candidateId",
                    )
                    gold_id = (
                        require_text(decision.get("goldId"), "pair.goldId")
                        if decision_kind == "pair"
                        else None
                    )
                    decision_key = (gold_id, candidate_id)
                    if decision_key in seen_keys:
                        raise ValueError(
                            f"{judgment_id}/{case_id} duplicates "
                            f"{decision_kind} decision {decision_key!r}"
                        )
                    seen_keys.add(decision_key)
                    verdict = decision.get("verdict")
                    if verdict not in allowed_verdicts:
                        raise ValueError(
                            f"{judgment_id}/{case_id} has an invalid "
                            f"{decision_kind} verdict"
                        )
                    decision_binding = {
                        "judgmentId": judgment_id,
                        "caseId": case_id,
                        "decisionKind": decision_kind,
                        "goldId": gold_id,
                        "candidateId": candidate_id,
                        "decision": dict(decision),
                    }
                    decision_digest = sha256_json(decision_binding)
                    subject_binding = {
                        key: value
                        for key, value in decision_binding.items()
                        if key != "decision"
                    }
                    subject_binding.update(
                        {
                            "judgeDecisionDigest": decision_digest,
                            "judgeVerdict": verdict,
                        }
                    )
                    subjects.append(
                        {
                            "subjectId": (
                                "m2e-" + sha256_json(subject_binding)
                            ),
                            **subject_binding,
                        }
                    )
    subjects.sort(key=lambda item: item["subjectId"])
    subject_ids = [item["subjectId"] for item in subjects]
    if len(subject_ids) != len(set(subject_ids)):
        raise ValueError("derived audit subject IDs are not unique")
    if not subjects:
        raise ValueError("planned judge evaluation has no decisions to audit")
    return subjects


def _subject_projection(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in (
            "subjectId",
            "judgmentId",
            "caseId",
            "decisionKind",
            "goldId",
            "candidateId",
            "judgeDecisionDigest",
            "judgeVerdict",
        )
    }


def export_judge_evaluation_packet(
    *,
    corpus_path: Path,
    registration_path: Path,
    seal_ledger_path: Path,
    analysis_run_paths: Sequence[Path],
    post_fix_analysis_run_paths: Sequence[Path] = (),
    judgment_paths: Sequence[Path],
    output_path: Path | None = None,
) -> dict[str, Any]:
    corpus = read_json(corpus_path)
    registration = read_json(registration_path)
    seal = read_json(seal_ledger_path)
    if not all(
        isinstance(item, Mapping)
        for item in (corpus, registration, seal)
    ):
        raise ValueError(
            "corpus, registration, and seal ledger must be objects"
        )
    validate_study_registration(registration, corpus)
    runs = [
        _validated_run(
            read_json(path),
            str(corpus["corpusDigest"]),
            str(registration["executionCorpusDigest"]),
        )
        for path in analysis_run_paths
    ]
    post_fix_runs = [
        _validated_post_fix_run(
            read_json(path),
            str(corpus["corpusDigest"]),
            str(registration["executionCorpusDigest"]),
        )
        for path in post_fix_analysis_run_paths
    ]
    validate_seal_ledger(
        seal,
        registration,
        corpus,
        runs,
        post_fix_runs,
    )
    judgments = [
        _validated_judgment(
            read_json(path),
            str(corpus["corpusDigest"]),
        )
        for path in judgment_paths
    ]
    observed = _planned_judgments(
        registration,
        judgments,
        corpus=corpus,
        analysis_runs={str(run["runId"]): run for run in runs},
    )
    unsealed_at = _timestamp(seal["unseal"]["at"], "unseal.at")
    sealed_access_times = [
        _timestamp(item["at"], "sealed_access.at")
        for item in seal["accessEvents"]
        if item.get("action") == "sealed_access"
    ]
    if (
        not sealed_access_times
        or any(
            _timestamp(
                judgment.get("createdAt"),
                f"{judgment_id}.createdAt",
            )
            < max(unsealed_at, min(sealed_access_times))
            for judgment_id, judgment in observed.items()
        )
    ):
        raise ValueError(
            "blinded evaluation packet requires post-unseal judgments and "
            "sealed-label access"
        )
    subjects = _audit_subjects(registration, observed)
    judgment_cases = {
        (judgment_id, str(case["caseId"])): case
        for judgment_id, judgment in observed.items()
        for case in judgment["cases"]
        if isinstance(case, Mapping)
    }
    blinded_subjects = []
    for subject in subjects:
        case = judgment_cases[
            (str(subject["judgmentId"]), str(subject["caseId"]))
        ]
        gold_by_id = {
            str(item["goldId"]): item
            for item in case["goldIssues"]
        }
        candidate_by_id = {
            str(item["candidateId"]): item
            for item in case["candidateFindings"]
        }
        blinded_subjects.append(
            {
                "subjectId": subject["subjectId"],
                "caseId": subject["caseId"],
                "decisionKind": subject["decisionKind"],
                "goldIssue": (
                    dict(gold_by_id[str(subject["goldId"])])
                    if subject["goldId"] is not None
                    else None
                ),
                "candidateFinding": dict(
                    candidate_by_id[str(subject["candidateId"])]
                ),
                "allowedHumanVerdicts": (
                    PAIR_HUMAN_VERDICTS
                    if subject["decisionKind"] == "pair"
                    else NOVEL_HUMAN_VERDICTS
                ),
            }
        )
    result = {
        "kind": JUDGE_EVALUATION_PACKET_KIND,
        "studyId": registration["studyId"],
        "registrationDigest": registration["registrationDigest"],
        "corpusDigest": corpus["corpusDigest"],
        "generatedAt": _now(),
        "modelIdentityBlinded": True,
        "subjects": blinded_subjects,
        "rubric": {
            "pair": (
                "Independently decide whether the candidate captures the "
                "same specific, snapshot-grounded root cause and compatible "
                "required change as the reviewer issue."
            ),
            "novel": (
                "Independently decide whether the unmatched candidate is a "
                "snapshot-grounded, actionable in-scope issue."
            ),
            "unverifiable": (
                "Use unverifiable when the supplied frozen evidence is "
                "insufficient; do not infer missing facts."
            ),
        },
        "instructions": (
            "Assign an independent human verdict using only the displayed "
            "gold/candidate evidence. Do not reveal model, judgment, or "
            "machine-verdict identity to annotators."
        ),
    }
    result["packetDigest"] = sha256_json(result)
    if output_path is not None:
        write_json(output_path, result)
    return result


def _final_human_results(
    *,
    subjects: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    adjudications: Sequence[Mapping[str, Any]],
) -> tuple[float, list[dict[str, Any]]]:
    by_subject: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        by_subject.setdefault(str(record["subjectId"]), []).append(record)
    adjudication_by_subject = {
        str(item["subjectId"]): item for item in adjudications
    }
    confusion: dict[tuple[str, str, str], int] = {}
    agreements = 0
    for subject in subjects:
        subject_id = str(subject["subjectId"])
        verdicts = {
            str(item["humanVerdict"])
            for item in by_subject.get(subject_id, [])
        }
        if len(verdicts) == 1:
            final_human = next(iter(verdicts))
        else:
            if subject_id not in adjudication_by_subject:
                raise ValueError(
                    f"audit subject {subject_id} requires adjudication"
                )
            final_human = str(
                adjudication_by_subject[subject_id]["finalHumanVerdict"]
            )
        judge_verdict = str(subject["judgeVerdict"])
        decision_kind = str(subject["decisionKind"])
        agreements += int(judge_verdict == final_human)
        confusion_key = (decision_kind, judge_verdict, final_human)
        confusion[confusion_key] = confusion.get(confusion_key, 0) + 1
    agreement = round(agreements / len(subjects), 6)
    rows = [
        {
            "decisionKind": decision_kind,
            "judgeVerdict": judge_verdict,
            "humanVerdict": human_verdict,
            "count": count,
        }
        for (
            decision_kind,
            judge_verdict,
            human_verdict,
        ), count in sorted(confusion.items())
    ]
    return agreement, rows


def create_judge_evaluation(
    *,
    corpus_path: Path,
    registration_path: Path,
    seal_ledger_path: Path,
    analysis_run_paths: Sequence[Path],
    post_fix_analysis_run_paths: Sequence[Path] = (),
    judgment_paths: Sequence[Path],
    evaluation_plan_path: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    corpus = read_json(corpus_path)
    registration = read_json(registration_path)
    seal_ledger = read_json(seal_ledger_path)
    plan = read_json(evaluation_plan_path)
    if not all(
        isinstance(item, Mapping)
        for item in (corpus, registration, seal_ledger, plan)
    ):
        raise ValueError(
            "corpus, registration, seal ledger, and evaluation plan must "
            "be objects"
        )
    validate_study_registration(registration, corpus)
    runs = [
        _validated_run(
            read_json(path),
            str(corpus["corpusDigest"]),
            str(registration["executionCorpusDigest"]),
        )
        for path in analysis_run_paths
    ]
    post_fix_runs = [
        _validated_post_fix_run(
            read_json(path),
            str(corpus["corpusDigest"]),
            str(registration["executionCorpusDigest"]),
        )
        for path in post_fix_analysis_run_paths
    ]
    validate_seal_ledger(
        seal_ledger,
        registration,
        corpus,
        runs,
        post_fix_runs,
    )
    analysis_runs = {
        str(run["runId"]): run for run in runs
    }
    judgments = [
        _validated_judgment(
            read_json(path),
            str(corpus["corpusDigest"]),
        )
        for path in judgment_paths
    ]
    observed_judgments = _planned_judgments(
        registration,
        judgments,
        corpus=corpus,
        analysis_runs=analysis_runs,
    )
    subjects = _audit_subjects(registration, observed_judgments)
    subjects_by_id = {item["subjectId"]: item for item in subjects}
    _exact_fields(
        plan,
        {"createdAt", "records", "adjudications"},
        "judge evaluation input",
    )
    records = []
    raw_records = plan.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("judge evaluation records must be non-empty")
    for item in raw_records:
        if not isinstance(item, Mapping):
            raise ValueError("judge evaluation record must be an object")
        record = dict(item)
        _exact_fields(
            record,
            {
                "subjectId",
                "annotator",
                "humanVerdict",
                "modelIdentityBlinded",
                "at",
            },
            "judge evaluation record",
        )
        subject_id = str(record.get("subjectId") or "")
        subject = subjects_by_id.get(subject_id)
        if subject is None:
            raise ValueError(
                "judge evaluation record has an unbound subjectId"
            )
        record = {
            **_subject_projection(subject),
            "annotator": record["annotator"],
            "humanVerdict": record["humanVerdict"],
            "modelIdentityBlinded": record["modelIdentityBlinded"],
            "at": record["at"],
        }
        record["recordDigest"] = sha256_json(record)
        records.append(record)
    records.sort(
        key=lambda item: (
            str(item["subjectId"]),
            str(item["annotator"]),
        )
    )
    adjudications = []
    raw_adjudications = plan.get("adjudications")
    if not isinstance(raw_adjudications, list):
        raise ValueError("judge evaluation adjudications must be an array")
    for item in raw_adjudications:
        if not isinstance(item, Mapping):
            raise ValueError("judge evaluation adjudication must be an object")
        adjudication = dict(item)
        _exact_fields(
            adjudication,
            {
                "subjectId",
                "adjudicator",
                "finalHumanVerdict",
                "sourceRecordDigests",
                "modelIdentityBlinded",
                "at",
                "rationale",
            },
            "judge evaluation adjudication",
        )
        subject_id = str(adjudication.get("subjectId") or "")
        subject = subjects_by_id.get(subject_id)
        if subject is None:
            raise ValueError(
                "judge evaluation adjudication has an unbound subjectId"
            )
        adjudication = {
            **_subject_projection(subject),
            "adjudicator": adjudication["adjudicator"],
            "finalHumanVerdict": adjudication["finalHumanVerdict"],
            "sourceRecordDigests": adjudication["sourceRecordDigests"],
            "modelIdentityBlinded": adjudication["modelIdentityBlinded"],
            "at": adjudication["at"],
            "rationale": adjudication["rationale"],
        }
        adjudication["adjudicationDigest"] = sha256_json(adjudication)
        adjudications.append(adjudication)
    adjudications.sort(key=lambda item: str(item["subjectId"]))
    policy = dict(registration["judgeEvaluationPlan"])
    observed_agreement = _pairwise_agreement(records)
    judge_human_agreement, judge_human_confusion = _final_human_results(
        subjects=subjects,
        records=records,
        adjudications=adjudications,
    )
    result = {
        "kind": JUDGE_EVALUATION_KIND,
        "studyId": registration["studyId"],
        "registrationDigest": registration["registrationDigest"],
        "corpusDigest": corpus["corpusDigest"],
        "createdAt": plan["createdAt"],
        "mode": policy["mode"],
        "caseIds": list(policy["caseIds"]),
        "judgePlanIds": sorted(
            item["judgmentId"] for item in registration["judgePlans"]
        ),
        "subjects": subjects,
        "policy": {
            key: policy[key]
            for key in (
                "subjectPolicy",
                "minimumIndependentRaters",
                "agreementMetric",
                "minimumAgreement",
                "disagreementResolution",
                "modelIdentityBlinded",
            )
        },
        "records": records,
        "adjudications": adjudications,
        "humanHumanAgreement": observed_agreement,
        "judgeHumanEvaluation": {
            "agreement": judge_human_agreement,
            "confusion": judge_human_confusion,
            "subjectCount": len(subjects),
        },
    }
    result["judgeEvaluationDigest"] = sha256_json(result)
    validate_judge_evaluation(
        result,
        registration,
        corpus,
        judgments=judgments,
        analysis_runs=runs,
        seal_ledger=seal_ledger,
    )
    if output_path is not None:
        write_json(output_path, result)
    return result


def validate_judge_evaluation(
    value: Any,
    registration: Mapping[str, Any],
    corpus: Mapping[str, Any],
    *,
    judgments: Sequence[Mapping[str, Any]],
    analysis_runs: Sequence[Mapping[str, Any]],
    seal_ledger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("judge evaluation must be an object")
    digest = _artifact_digest(
        value,
        field="judgeEvaluationDigest",
        kind=JUDGE_EVALUATION_KIND,
    )
    validate_study_registration(registration, corpus)
    _exact_fields(
        value,
        {
            "kind",
            "studyId",
            "registrationDigest",
            "corpusDigest",
            "createdAt",
            "mode",
            "caseIds",
            "judgePlanIds",
            "subjects",
            "policy",
            "records",
            "adjudications",
            "humanHumanAgreement",
            "judgeHumanEvaluation",
            "judgeEvaluationDigest",
        },
        "judge evaluation",
    )
    plan = registration["judgeEvaluationPlan"]
    if (
        value.get("studyId") != registration["studyId"]
        or value.get("registrationDigest")
        != registration["registrationDigest"]
        or value.get("corpusDigest") != corpus["corpusDigest"]
        or value.get("mode") != plan["mode"]
        or value.get("caseIds") != plan["caseIds"]
        or value.get("judgePlanIds")
        != sorted(item["judgmentId"] for item in registration["judgePlans"])
    ):
        raise ValueError("judge evaluation registration binding mismatch")
    expected_policy = {
        key: plan[key]
        for key in (
            "subjectPolicy",
            "minimumIndependentRaters",
            "agreementMetric",
            "minimumAgreement",
            "disagreementResolution",
            "modelIdentityBlinded",
        )
    }
    if value.get("policy") != expected_policy:
        raise ValueError("judge evaluation policy drift")
    runs_by_id = {
        str(run.get("runId") or ""): run for run in analysis_runs
    }
    if (
        len(runs_by_id) != len(analysis_runs)
        or set(runs_by_id)
        != {
            str(item["runId"])
            for item in registration["analysisPlans"]
        }
    ):
        raise ValueError(
            "judge evaluation analysis runs do not cover the registered plan"
        )
    observed_judgments = _planned_judgments(
        registration,
        judgments,
        corpus=corpus,
        analysis_runs=runs_by_id,
    )
    subjects = _audit_subjects(registration, observed_judgments)
    if value.get("subjects") != subjects:
        raise ValueError(
            "judge evaluation subjects differ from bound judge decisions"
        )
    subjects_by_id = {item["subjectId"]: item for item in subjects}
    created_at = _timestamp(value.get("createdAt"), "createdAt")
    registered_at = _timestamp(
        registration["registeredAt"],
        "registeredAt",
    )
    if created_at < registered_at:
        raise ValueError("judge evaluation predates registration")
    if (
        plan["mode"] == "development_calibration"
        and seal_ledger is not None
        and created_at
        > _timestamp(seal_ledger["unseal"]["at"], "unseal.at")
    ):
        raise ValueError("development calibration occurred after sealed unseal")
    judgment_times = {
        judgment_id: _timestamp(
            judgment.get("createdAt"),
            f"{judgment_id}.createdAt",
        )
        for judgment_id, judgment in observed_judgments.items()
    }
    sealed_access_at: datetime | None = None
    unsealed_at: datetime | None = None
    if seal_ledger is not None:
        unsealed_at = _timestamp(
            seal_ledger["unseal"]["at"],
            "unseal.at",
        )
        sealed_access_times = [
            _timestamp(item["at"], "sealed_access.at")
            for item in seal_ledger["accessEvents"]
            if item.get("action") == "sealed_access"
        ]
        if sealed_access_times:
            sealed_access_at = min(sealed_access_times)
    if plan["mode"] == "blinded_human_audit":
        if (
            unsealed_at is None
            or sealed_access_at is None
            or created_at < unsealed_at
            or created_at < sealed_access_at
            or any(at < unsealed_at for at in judgment_times.values())
            or any(at > created_at for at in judgment_times.values())
        ):
            raise ValueError(
                "blinded human audit and bound judgments must follow sealed "
                "unseal/access and precede artifact creation"
            )

    records = value.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("judge evaluation records must be non-empty")
    expected_order = sorted(
        records,
        key=lambda item: (
            str(item.get("subjectId") if isinstance(item, Mapping) else ""),
            str(item.get("annotator") if isinstance(item, Mapping) else ""),
        ),
    )
    if records != expected_order:
        raise ValueError("judge evaluation records must be sorted")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for index, item in enumerate(records):
        _exact_fields(
            item,
            {
                "subjectId",
                "judgmentId",
                "caseId",
                "decisionKind",
                "goldId",
                "candidateId",
                "judgeDecisionDigest",
                "judgeVerdict",
                "annotator",
                "humanVerdict",
                "modelIdentityBlinded",
                "at",
                "recordDigest",
            },
            f"records[{index}]",
        )
        subject_id = require_text(
            item.get("subjectId"),
            f"records[{index}].subjectId",
        )
        subject = subjects_by_id.get(subject_id)
        if (
            subject is None
            or {
                key: item[key]
                for key in _subject_projection(subject)
            }
            != _subject_projection(subject)
        ):
            raise ValueError(
                "judge evaluation record binding is not judgment-derived"
            )
        annotator = require_text(
            item.get("annotator"),
            f"records[{index}].annotator",
        )
        allowed_verdicts = (
            MATCH_VERDICTS
            if subject["decisionKind"] == "pair"
            else NOVEL_VERDICTS
        )
        if item.get("humanVerdict") not in allowed_verdicts:
            raise ValueError("human audit verdict is invalid")
        if item.get("modelIdentityBlinded") is not True:
            raise ValueError("human evaluator was not model-identity blinded")
        record_at = _timestamp(item.get("at"), f"records[{index}].at")
        if record_at < registered_at or record_at > created_at:
            raise ValueError(
                "judge evaluation record must be between registration and "
                "artifact creation"
            )
        if record_at < judgment_times[str(subject["judgmentId"])]:
            raise ValueError(
                "human audit record predates its bound judgment"
            )
        if (
            plan["mode"] == "blinded_human_audit"
            and (
                sealed_access_at is None
                or record_at < sealed_access_at
            )
        ):
            raise ValueError(
                "human audit record predates sealed-label access"
            )
        payload = dict(item)
        declared = payload.pop("recordDigest")
        if declared != sha256_json(payload):
            raise ValueError("judge evaluation record digest mismatch")
        grouped.setdefault(subject_id, []).append(item)
    if set(grouped) != set(subjects_by_id):
        raise ValueError(
            "judge evaluation records must cover exactly every derived subject"
        )
    minimum_raters = int(plan["minimumIndependentRaters"])
    for subject_id, subject_records in grouped.items():
        annotators = [str(item["annotator"]) for item in subject_records]
        if (
            len(subject_records) < minimum_raters
            or len(annotators) != len(set(annotators))
        ):
            raise ValueError(
                f"judge evaluation subject {subject_id!r} lacks independent raters"
            )

    adjudications = value.get("adjudications")
    if not isinstance(adjudications, list):
        raise ValueError("judge evaluation adjudications must be an array")
    if adjudications != sorted(
        adjudications,
        key=lambda item: str(
            item.get("subjectId") if isinstance(item, Mapping) else ""
        ),
    ):
        raise ValueError("judge evaluation adjudications must be sorted")
    adjudicated_subjects = set()
    for index, item in enumerate(adjudications):
        _exact_fields(
            item,
            {
                "subjectId",
                "judgmentId",
                "caseId",
                "decisionKind",
                "goldId",
                "candidateId",
                "judgeDecisionDigest",
                "judgeVerdict",
                "adjudicator",
                "finalHumanVerdict",
                "sourceRecordDigests",
                "modelIdentityBlinded",
                "at",
                "rationale",
                "adjudicationDigest",
            },
            f"adjudications[{index}]",
        )
        subject_id = str(item.get("subjectId"))
        subject = subjects_by_id.get(subject_id)
        if (
            subject is None
            or subject_id not in grouped
            or subject_id in adjudicated_subjects
            or {
                key: item[key]
                for key in _subject_projection(subject)
            }
            != _subject_projection(subject)
        ):
            raise ValueError("judge evaluation adjudication subject is invalid")
        subject_records = grouped[subject_id]
        if (
            len(
                {
                    str(record["humanVerdict"])
                    for record in subject_records
                }
            )
            == 1
        ):
            raise ValueError("agreement must not receive an adjudication")
        adjudicator = require_text(
            item.get("adjudicator"),
            f"adjudications[{index}].adjudicator",
        )
        if adjudicator in {
            str(record["annotator"]) for record in subject_records
        }:
            raise ValueError("adjudicator must be independent of raters")
        allowed_verdicts = (
            MATCH_VERDICTS
            if subject["decisionKind"] == "pair"
            else NOVEL_VERDICTS
        )
        if item.get("finalHumanVerdict") not in allowed_verdicts:
            raise ValueError("adjudication final verdict is invalid")
        if item.get("modelIdentityBlinded") is not True:
            raise ValueError("adjudicator was not model-identity blinded")
        expected_digests = sorted(
            str(record["recordDigest"]) for record in subject_records
        )
        if item.get("sourceRecordDigests") != expected_digests:
            raise ValueError("adjudication record binding mismatch")
        adjudicated_at = _timestamp(
            item.get("at"),
            f"adjudications[{index}].at",
        )
        if adjudicated_at < registered_at or adjudicated_at > created_at:
            raise ValueError(
                "adjudication must be between registration and artifact"
            )
        if adjudicated_at < judgment_times[str(subject["judgmentId"])]:
            raise ValueError("adjudication predates its bound judgment")
        if (
            plan["mode"] == "blinded_human_audit"
            and (
                sealed_access_at is None
                or adjudicated_at < sealed_access_at
            )
        ):
            raise ValueError("adjudication predates sealed-label access")
        require_text(
            item.get("rationale"),
            f"adjudications[{index}].rationale",
        )
        payload = dict(item)
        declared = payload.pop("adjudicationDigest")
        if declared != sha256_json(payload):
            raise ValueError("adjudication digest mismatch")
        adjudicated_subjects.add(subject_id)
    disagreement_subjects = {
        subject
        for subject, subject_records in grouped.items()
        if len(
            {str(record["humanVerdict"]) for record in subject_records}
        )
        > 1
    }
    if adjudicated_subjects != disagreement_subjects:
        raise ValueError("every disagreement must be independently adjudicated")
    observed = _pairwise_agreement(records)
    if (
        value.get("humanHumanAgreement") != observed
        or observed < float(plan["minimumAgreement"])
    ):
        raise ValueError("judge evaluation agreement threshold is not met")
    judge_human_agreement, judge_human_confusion = _final_human_results(
        subjects=subjects,
        records=records,
        adjudications=adjudications,
    )
    expected_judge_human = {
        "agreement": judge_human_agreement,
        "confusion": judge_human_confusion,
        "subjectCount": len(subjects),
    }
    if value.get("judgeHumanEvaluation") != expected_judge_human:
        raise ValueError("judge-human evaluation summary mismatch")
    return {
        "studyId": registration["studyId"],
        "judgeEvaluationDigest": digest,
        "mode": value["mode"],
        "cases": len(plan["caseIds"]),
        "subjects": len(grouped),
        "humanHumanAgreement": observed,
        "judgeHumanAgreement": judge_human_agreement,
        "judgeHumanConfusion": judge_human_confusion,
    }


def _validated_judgment(
    value: Any,
    corpus_digest: str,
) -> Mapping[str, Any]:
    judgment = _validate_digest_artifact(
        value,
        kind=JUDGMENT_KIND,
        digest_field="judgmentDigest",
    )
    if judgment.get("corpusDigest") != corpus_digest:
        raise ValueError("judgment belongs to another corpus")
    _timestamp(judgment.get("createdAt"), "judgment.createdAt")
    return judgment


def validate_protocol_bundle(
    *,
    corpus: Mapping[str, Any],
    registration_path: Path,
    seal_ledger_path: Path,
    judge_evaluation_path: Path,
    analysis_run_paths: Sequence[Path] | None,
    post_fix_analysis_run_paths: Sequence[Path] | None = None,
    judgment_paths: Sequence[Path],
) -> dict[str, Any]:
    summary = _strict_corpus(corpus)
    registration = read_json(registration_path)
    seal = read_json(seal_ledger_path)
    evaluation = read_json(judge_evaluation_path)
    if not all(
        isinstance(item, Mapping)
        for item in (registration, seal, evaluation)
    ):
        raise ValueError("protocol artifacts must be JSON objects")
    registration_summary = validate_study_registration(
        registration,
        corpus,
    )
    if (
        registration["judgeEvaluationPlan"]["mode"]
        != "blinded_human_audit"
    ):
        raise ValueError(
            "sealed publication protocol requires a preregistered "
            "post-unseal blinded_human_audit; development calibration is "
            "diagnostic unless it uses a separate dev-only artifact"
        )
    runs = [
        _validated_run(
            read_json(path),
            summary["corpusDigest"],
            str(registration["executionCorpusDigest"]),
        )
        for path in (analysis_run_paths or [])
    ]
    post_fix_runs = [
        _validated_post_fix_run(
            read_json(path),
            summary["corpusDigest"],
            str(registration["executionCorpusDigest"]),
        )
        for path in (post_fix_analysis_run_paths or [])
    ]
    seal_summary = validate_seal_ledger(
        seal,
        registration,
        corpus,
        runs,
        post_fix_runs,
    )
    judgments = [
        _validated_judgment(
            read_json(path),
            summary["corpusDigest"],
        )
        for path in judgment_paths
    ]
    _planned_judgments(
        registration,
        judgments,
        corpus=corpus,
        analysis_runs={
            str(run["runId"]): run for run in runs
        },
    )
    evaluation_summary = validate_judge_evaluation(
        evaluation,
        registration,
        corpus,
        judgments=judgments,
        analysis_runs=runs,
        seal_ledger=seal,
    )
    return {
        "registration": {
            "status": "validated",
            "digest": registration_summary["registrationDigest"],
        },
        "sealedLabelCustody": {
            "status": "validated",
            "digest": seal_summary["sealLedgerDigest"],
        },
        "judgeCalibrationOrAudit": {
            "status": "validated",
            "digest": evaluation_summary["judgeEvaluationDigest"],
            "mode": evaluation_summary["mode"],
            "humanHumanAgreement": evaluation_summary[
                "humanHumanAgreement"
            ],
            "judgeHumanAgreement": evaluation_summary[
                "judgeHumanAgreement"
            ],
            "judgeHumanConfusion": evaluation_summary[
                "judgeHumanConfusion"
            ],
        },
    }


def _absolute_without_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_chain(root: Path, candidate: Path) -> None:
    root_absolute = _absolute_without_resolution(root)
    candidate_absolute = _absolute_without_resolution(candidate)
    try:
        relative = candidate_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise ValueError("artifact path escapes artifact root") from exc
    current = root_absolute
    if current.is_symlink():
        raise ValueError("artifact root must not be a symlink")
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"symlink is forbidden in artifact path: {current}")


def _safe_existing_path(root: Path, path: Path) -> tuple[Path, str]:
    candidate = path if path.is_absolute() else root / path
    _reject_symlink_chain(root, candidate)
    try:
        root_resolved = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise ValueError(f"unsafe or missing artifact path: {path}") from exc
    if not relative.parts:
        raise ValueError("artifact root itself cannot be an artifact set")
    return resolved, relative.as_posix()


def _safe_output_path(root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else root / path
    _reject_symlink_chain(root, candidate.parent)
    root_resolved = root.resolve(strict=True)
    parent = candidate.parent.resolve(strict=True)
    try:
        parent.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("output path escapes artifact root") from exc
    if candidate.exists() and candidate.is_symlink():
        raise ValueError("output path must not be a symlink")
    return candidate


def _files_under(root: Path, path: Path) -> list[tuple[Path, str]]:
    resolved, relative = _safe_existing_path(root, path)
    if resolved.is_file():
        return [(resolved, relative)]
    if not resolved.is_dir():
        raise ValueError(f"artifact path is not a file or directory: {path}")
    files = []
    for child in sorted(resolved.rglob("*")):
        if child.is_symlink():
            raise ValueError(f"symlink is forbidden in artifact set: {child}")
        if child.is_file():
            child_relative = child.relative_to(root.resolve(strict=True))
            files.append((child, child_relative.as_posix()))
    if not files:
        raise ValueError(f"artifact directory is empty: {path}")
    return files


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scan_file_for_secrets(path: Path, relative: str) -> None:
    data = path.read_bytes()
    text_suffix = path.suffix.casefold()
    _scan_secret_text(
        data,
        f"package file {relative}",
        assignments=text_suffix in SECRET_ASSIGNMENT_TEXT_SUFFIXES,
    )
    if text_suffix in SECRET_SCAN_TEXT_SUFFIXES:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"invalid UTF-8 text package file {relative}"
            ) from exc
        _reject_private_urls(text, f"package file {relative}")
    if path.suffix.casefold() == ".json":
        value = read_json(path)
        _reject_sensitive_config(value, relative)
    elif path.suffix.casefold() == ".toml":
        try:
            value = tomllib.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(
                f"invalid TOML package file {relative}"
            ) from exc
        _reject_sensitive_config(value, relative)
    if (
            path.name.casefold().startswith(".env")
            or text_suffix
            in SECRET_ASSIGNMENT_TEXT_SUFFIXES
        ):
        for match in SECRET_ASSIGNMENT_PATTERN.finditer(data):
            if _secret_value_is_placeholder(match.group("value")):
                continue
            raise ValueError(
                f"secret-like assignment detected in package file {relative}"
            )


def _artifact_paths_by_kind(
    root: Path,
    supplied_paths: Sequence[Path],
    kind: str,
) -> list[Path]:
    matches = []
    for supplied in supplied_paths:
        for file_path, _ in _files_under(root, supplied):
            if file_path.suffix.casefold() != ".json":
                continue
            try:
                value = read_json(file_path)
            except (OSError, ValueError):
                continue
            if isinstance(value, Mapping) and value.get("kind") == kind:
                matches.append(file_path)
    return sorted(set(matches))


def _single_artifact_by_kind(
    root: Path,
    supplied_paths: Sequence[Path],
    kind: str,
    field: str,
) -> Path:
    matches = _artifact_paths_by_kind(root, supplied_paths, kind)
    if len(matches) != 1:
        raise ValueError(
            f"{field} category must contain exactly one {kind!r} artifact"
        )
    return matches[0]


def _required_kind_path(
    root: Path,
    artifact_sets: Mapping[str, Any],
    *,
    category: str,
    kind: str,
) -> Path:
    roots = artifact_sets.get(category)
    if not isinstance(roots, list):
        raise ValueError(f"package category {category} is invalid")
    return _single_artifact_by_kind(
        root,
        [Path(item) for item in roots],
        kind,
        category,
    )


def _kind_paths(
    root: Path,
    artifact_sets: Mapping[str, Any],
    *,
    category: str,
    kind: str,
) -> list[Path]:
    roots = artifact_sets.get(category)
    if not isinstance(roots, list):
        raise ValueError(f"package category {category} is invalid")
    return _artifact_paths_by_kind(
        root,
        [Path(item) for item in roots],
        kind,
    )


def _single_category_directory(
    root: Path,
    artifact_sets: Mapping[str, Any],
    *,
    category: str,
) -> Path:
    roots = artifact_sets.get(category)
    if not isinstance(roots, list) or len(roots) != 1:
        raise ValueError(
            f"package category {category} must contain exactly one root"
        )
    resolved, _ = _safe_existing_path(root, Path(roots[0]))
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError(
            f"package category {category} root must be a real directory"
        )
    return resolved


def _validate_extended_package_evidence(
    *,
    root: Path,
    artifact_sets: Mapping[str, Any],
    corpus_path: Path,
    registration_path: Path,
    seal_path: Path,
    evaluation_path: Path,
    metrics_path: Path,
    analysis_run_paths: Sequence[Path],
    judgment_paths: Sequence[Path],
) -> dict[str, Any]:
    """Validate every mandatory evidence family and recompute the metrics."""

    source_kinds = {
        DISCOVERY_KIND,
        DISCOVERY_SELECTION_LINK_KIND,
        DRAFT_KIND,
        DRAFT_SOURCE_ARCHIVE_KIND,
        THREAD_EVIDENCE_KIND,
    }
    curation_kinds = {PACKET_KIND, DECISIONS_KIND, SELECTION_KIND}
    replay_kinds = {LOCK_KIND, ATTESTATION_KIND}
    current_comment_kinds = {CURRENT_COMMENT_ATTESTATION_KIND}
    post_fix_kinds = {
        POST_FIX_PLAN_KIND,
        POST_FIX_LOCK_KIND,
        POST_FIX_ATTESTATION_KIND,
        POST_FIX_RUN_KIND,
        POST_FIX_JUDGMENT_KIND,
        POST_FIX_CONTROL_KIND,
        POST_FIX_CONTROL_SET_KIND,
    }
    if (
        source_kinds != SOURCE_EVIDENCE_KINDS
        or curation_kinds != CURATION_EVIDENCE_KINDS
        or replay_kinds != REPLAY_EVIDENCE_KINDS
        or current_comment_kinds != CURRENT_COMMENT_EVIDENCE_KINDS
        or post_fix_kinds != POST_FIX_EVIDENCE_KINDS
    ):
        raise AssertionError("package evidence kind registry drift")

    discovery_path = _required_kind_path(
        root,
        artifact_sets,
        category="source",
        kind=DISCOVERY_KIND,
    )
    discovery_selection_link_path = _required_kind_path(
        root,
        artifact_sets,
        category="source",
        kind=DISCOVERY_SELECTION_LINK_KIND,
    )
    draft_path = _required_kind_path(
        root,
        artifact_sets,
        category="source",
        kind=DRAFT_KIND,
    )
    source_archive_path = _required_kind_path(
        root,
        artifact_sets,
        category="source",
        kind=DRAFT_SOURCE_ARCHIVE_KIND,
    )
    thread_evidence_path = _required_kind_path(
        root,
        artifact_sets,
        category="source",
        kind=THREAD_EVIDENCE_KIND,
    )
    packet_path = _required_kind_path(
        root,
        artifact_sets,
        category="curation",
        kind=PACKET_KIND,
    )
    decisions_path = _required_kind_path(
        root,
        artifact_sets,
        category="curation",
        kind=DECISIONS_KIND,
    )
    selection_path = _required_kind_path(
        root,
        artifact_sets,
        category="curation",
        kind=SELECTION_KIND,
    )
    current_comment_path = _required_kind_path(
        root,
        artifact_sets,
        category="current_comment",
        kind=CURRENT_COMMENT_ATTESTATION_KIND,
    )
    replay_lock_path = _required_kind_path(
        root,
        artifact_sets,
        category="replay",
        kind=LOCK_KIND,
    )
    replay_attestation_path = _required_kind_path(
        root,
        artifact_sets,
        category="replay",
        kind=ATTESTATION_KIND,
    )
    post_fix_plan_path = _required_kind_path(
        root,
        artifact_sets,
        category="post_fix",
        kind=POST_FIX_PLAN_KIND,
    )
    post_fix_lock_path = _required_kind_path(
        root,
        artifact_sets,
        category="post_fix",
        kind=POST_FIX_LOCK_KIND,
    )
    post_fix_attestation_path = _required_kind_path(
        root,
        artifact_sets,
        category="post_fix",
        kind=POST_FIX_ATTESTATION_KIND,
    )
    post_fix_run_paths = _kind_paths(
        root,
        artifact_sets,
        category="post_fix",
        kind=POST_FIX_RUN_KIND,
    )
    post_fix_judgment_paths = _kind_paths(
        root,
        artifact_sets,
        category="post_fix",
        kind=POST_FIX_JUDGMENT_KIND,
    )
    post_fix_control_paths = _kind_paths(
        root,
        artifact_sets,
        category="post_fix",
        kind=POST_FIX_CONTROL_KIND,
    )
    post_fix_control_set_path = _required_kind_path(
        root,
        artifact_sets,
        category="post_fix",
        kind=POST_FIX_CONTROL_SET_KIND,
    )
    execution_corpus_path = _required_kind_path(
        root,
        artifact_sets,
        category="execution",
        kind=EXECUTION_CORPUS_KIND,
    )
    repository_evidence_root = _single_category_directory(
        root,
        artifact_sets,
        category="repository",
    )
    repository_evidence_path = _required_kind_path(
        root,
        artifact_sets,
        category="repository",
        kind=REPOSITORY_EVIDENCE_KIND,
    )
    operator_preflight_path = _required_kind_path(
        root,
        artifact_sets,
        category="runtime",
        kind=PREFLIGHT_KIND,
    )

    corpus = read_json(corpus_path)
    execution_corpus = read_json(execution_corpus_path)
    registration = read_json(registration_path)
    seal = read_json(seal_path)
    metrics = read_json(metrics_path)
    if not all(
        isinstance(item, Mapping)
        for item in (
            corpus,
            execution_corpus,
            registration,
            seal,
            metrics,
        )
    ):
        raise ValueError("package extended semantic artifacts must be objects")
    primary_runs = [read_json(path) for path in analysis_run_paths]
    primary_judgments = [read_json(path) for path in judgment_paths]
    post_fix_runs = [read_json(path) for path in post_fix_run_paths]
    post_fix_judgments = [
        read_json(path) for path in post_fix_judgment_paths
    ]
    post_fix_controls = [
        read_json(path) for path in post_fix_control_paths
    ]
    if any(
        not isinstance(item, Mapping)
        for item in (
            *primary_runs,
            *primary_judgments,
            *post_fix_runs,
            *post_fix_judgments,
            *post_fix_controls,
        )
    ):
        raise ValueError("package run/judgment/control artifact is invalid")

    execution_summary = validate_execution_corpus(execution_corpus)
    if (
        execution_summary["corpusId"] != corpus.get("corpusId")
        or execution_summary["corpusDigest"] != corpus.get("corpusDigest")
        or execution_corpus != build_execution_corpus(
            corpus,
            require_paper_ready=True,
        )
    ):
        raise ValueError(
            "packaged execution corpus is not the exact released-corpus "
            "projection"
        )
    execution_digest = execution_summary["executionCorpusDigest"]
    packaged_run_files = {
        category: {
            file_path.resolve(strict=True)
            for supplied in artifact_sets[category]
            for file_path, _ in _files_under(root, Path(supplied))
        }
        for category in ("analysis", "post_fix")
    }
    for run, run_path, role, category in (
        *[
            (item, path, "primary H", "analysis")
            for item, path in zip(
                primary_runs,
                analysis_run_paths,
                strict=True,
            )
        ],
        *[
            (item, path, "verified F", "post_fix")
            for item, path in zip(
                post_fix_runs,
                post_fix_run_paths,
                strict=True,
            )
        ],
    ):
        if (
            run.get("executionCorpusDigest") != execution_digest
            or run.get("executionCorpusArtifact")
            != "analysis-execution-corpus.json"
        ):
            raise ValueError(
                f"{role} run execution-corpus binding is invalid"
            )
        copied_execution_path = (
            run_path.resolve().parent / "analysis-execution-corpus.json"
        )
        if (
            not copied_execution_path.is_file()
            or copied_execution_path.is_symlink()
            or copied_execution_path.resolve(strict=True)
            not in packaged_run_files[category]
            or read_json(copied_execution_path) != execution_corpus
        ):
            raise ValueError(
                f"{role} run execution-corpus copy is missing or drifted"
            )
    repository_summary, repository_path = validate_repository_evidence(
        manifest_path=repository_evidence_path,
        corpus=corpus,
        evidence_root=repository_evidence_root,
    )
    operator_preflight = read_json(operator_preflight_path)
    if (
        not isinstance(operator_preflight, Mapping)
        or operator_preflight.get("kind") != PREFLIGHT_KIND
    ):
        raise ValueError("packaged operator preflight is invalid")
    preflight_payload = dict(operator_preflight)
    preflight_digest = preflight_payload.pop("readinessDigest", None)
    preflight_corpus = operator_preflight.get("corpus")
    if (
        not isinstance(preflight_digest, str)
        or SHA256_HEX.fullmatch(preflight_digest) is None
        or preflight_digest != sha256_json(preflight_payload)
        or operator_preflight.get("runReady") is not True
        or operator_preflight.get("operationMode") != "strictly-read-only"
        or not isinstance(preflight_corpus, Mapping)
        or preflight_corpus.get("executionCorpusDigest") != execution_digest
    ):
        raise ValueError(
            "operator preflight execution-corpus/readiness binding is invalid"
        )
    side_effect_policy = operator_preflight.get("sideEffectPolicy")
    if (
        not isinstance(side_effect_policy, Mapping)
        or not side_effect_policy
        or any(value is not False for value in side_effect_policy.values())
    ):
        raise ValueError(
            "operator preflight side-effect policy is invalid"
        )

    source_summary = validate_source_curation_bundle(
        corpus=corpus,
        draft_path=draft_path,
        discovery_path=discovery_path,
        discovery_selection_link_path=discovery_selection_link_path,
        source_archive_path=source_archive_path,
        thread_evidence_path=thread_evidence_path,
        curation_packet_path=packet_path,
        decisions_path=decisions_path,
        selection_path=selection_path,
        current_comment_attestation_path=current_comment_path,
    )
    replay_summary = validate_primary_replay_bundle(
        corpus=corpus,
        replay_lock=read_json(replay_lock_path),
        replay_attestation=read_json(replay_attestation_path),
        analysis_runs=primary_runs,
    )
    post_fix_summary = validate_post_fix_package_bundle(
        corpus=corpus,
        registration=registration,
        seal_ledger=seal,
        primary_replay_lock=read_json(replay_lock_path),
        primary_runs=primary_runs,
        primary_judgments=primary_judgments,
        post_fix_plan=read_json(post_fix_plan_path),
        post_fix_lock=read_json(post_fix_lock_path),
        post_fix_attestation=read_json(post_fix_attestation_path),
        post_fix_runs=post_fix_runs,
        post_fix_judgments=post_fix_judgments,
        post_fix_controls=post_fix_controls,
        post_fix_control_set=read_json(post_fix_control_set_path),
        post_fix_run_roots={
            str(item["runId"]): path.resolve().parent
            for item, path in zip(
                post_fix_runs,
                post_fix_run_paths,
                strict=True,
            )
        },
        post_fix_judgment_roots={
            str(item["judgmentId"]): path.resolve().parent
            for item, path in zip(
                post_fix_judgments,
                post_fix_judgment_paths,
                strict=True,
            )
        },
        repository=repository_path,
    )
    # Local import avoids protocol <-> metrics import initialization cycles.
    from .metrics import validate_metrics_derivation

    metrics_summary = validate_metrics_derivation(
        metrics=metrics,
        corpus_path=corpus_path,
        repository_path=repository_path,
        repository_evidence_path=repository_evidence_path,
        judgment_paths=judgment_paths,
        analysis_run_paths=analysis_run_paths,
        post_fix_analysis_run_paths=post_fix_run_paths,
        replay_lock_paths=[replay_lock_path],
        replay_attestation_paths=[replay_attestation_path],
        study_registration_path=registration_path,
        seal_ledger_path=seal_path,
        judge_evaluation_path=evaluation_path,
        post_fix_control_set_path=post_fix_control_set_path,
        post_fix_artifact_paths=[
            root / Path(item) for item in artifact_sets["post_fix"]
        ],
    )
    return {
        "sourceCuration": source_summary,
        "executionCorpus": execution_summary,
        "repositoryEvidence": repository_summary,
        "operatorPreflight": {
            "readinessDigest": preflight_digest,
            "executionCorpusDigest": execution_digest,
            "runReady": True,
        },
        "primaryReplay": replay_summary,
        "postFix": post_fix_summary,
        "metricsDerivation": metrics_summary,
    }


def build_reproducibility_package(
    *,
    artifact_root: Path,
    corpus_path: Path,
    registration_path: Path,
    seal_ledger_path: Path,
    judge_evaluation_path: Path,
    metrics_path: Path,
    dashboard_path: Path,
    analysis_artifacts: Sequence[Path],
    judgment_artifacts: Sequence[Path],
    runtime_artifacts: Sequence[Path],
    config_artifacts: Sequence[Path],
    source_artifacts: Sequence[Path],
    curation_artifacts: Sequence[Path],
    replay_artifacts: Sequence[Path],
    current_comment_artifacts: Sequence[Path],
    post_fix_artifacts: Sequence[Path],
    execution_artifacts: Sequence[Path],
    repository_artifacts: Sequence[Path],
    rerun_instructions: Sequence[str],
    limitations: Sequence[str],
    output_path: Path,
) -> dict[str, Any]:
    root = artifact_root
    if not root.is_dir() or root.is_symlink():
        raise ValueError("artifact_root must be a real directory")
    category_inputs: dict[str, Sequence[Path]] = {
        "corpus": [corpus_path],
        "registration": [registration_path],
        "seal": [seal_ledger_path],
        "judge_evaluation": [judge_evaluation_path],
        "metrics": [metrics_path],
        "dashboard": [dashboard_path],
        "analysis": list(analysis_artifacts),
        "judgment": list(judgment_artifacts),
        "runtime": list(runtime_artifacts),
        "config": list(config_artifacts),
        "source": list(source_artifacts),
        "curation": list(curation_artifacts),
        "replay": list(replay_artifacts),
        "current_comment": list(current_comment_artifacts),
        "post_fix": list(post_fix_artifacts),
        "execution": list(execution_artifacts),
        "repository": list(repository_artifacts),
    }
    if set(category_inputs) != REQUIRED_PACKAGE_CATEGORIES:
        raise ValueError("reproducibility package categories are incomplete")
    if any(not paths for paths in category_inputs.values()):
        raise ValueError("every reproducibility package category is required")
    instructions = _text_list(
        list(rerun_instructions),
        "rerunInstructions",
        minimum=2,
    )
    limitation_values = _text_list(
        list(limitations),
        "limitations",
    )

    artifact_sets: dict[str, list[str]] = {}
    entries = []
    seen_paths: dict[str, str] = {}
    for category in sorted(category_inputs):
        roots = []
        for supplied in category_inputs[category]:
            _, relative_root = _safe_existing_path(root, supplied)
            roots.append(relative_root)
            for file_path, relative in _files_under(root, supplied):
                previous = seen_paths.get(relative)
                if previous is not None and previous != category:
                    raise ValueError(
                        f"package file belongs to multiple categories: {relative}"
                    )
                seen_paths[relative] = category
                _scan_file_for_secrets(file_path, relative)
                entries.append(
                    {
                        "path": relative,
                        "category": category,
                        "sha256": _sha256_file(file_path),
                        "sizeBytes": file_path.stat().st_size,
                    }
                )
        if len(roots) != len(set(roots)):
            raise ValueError(f"duplicate artifact set in {category}")
        artifact_sets[category] = sorted(roots)
    entries.sort(key=lambda item: (item["path"], item["category"]))
    if len(entries) != len({item["path"] for item in entries}):
        raise ValueError("package file paths must be unique")

    safe_output = _safe_output_path(root, output_path)
    output_relative = safe_output.resolve(strict=False).relative_to(
        root.resolve(strict=True)
    ).as_posix()
    if output_relative in {item["path"] for item in entries}:
        raise ValueError("package manifest cannot hash itself")

    corpus = read_json(_safe_existing_path(root, corpus_path)[0])
    registration = read_json(
        _safe_existing_path(root, registration_path)[0]
    )
    seal = read_json(_safe_existing_path(root, seal_ledger_path)[0])
    evaluation = read_json(
        _safe_existing_path(root, judge_evaluation_path)[0]
    )
    metrics_file = _safe_existing_path(root, metrics_path)[0]
    metrics = read_json(metrics_file)
    if not all(
        isinstance(item, Mapping)
        for item in (corpus, registration, seal, evaluation, metrics)
    ):
        raise ValueError("bound JSON artifacts must be objects")
    corpus_summary = _strict_corpus(corpus)
    _artifact_digest(
        registration,
        field="registrationDigest",
        kind=REGISTRATION_KIND,
    )
    _artifact_digest(
        seal,
        field="sealLedgerDigest",
        kind=SEAL_LEDGER_KIND,
    )
    _artifact_digest(
        evaluation,
        field="judgeEvaluationDigest",
        kind=JUDGE_EVALUATION_KIND,
    )
    _artifact_digest(
        metrics,
        field="metricsDigest",
        kind="codecrow-magento2-benchmark-metrics",
    )
    if (
        seal.get("registrationDigest") != registration["registrationDigest"]
        or evaluation.get("registrationDigest")
        != registration["registrationDigest"]
        or registration.get("corpus", {}).get("corpusDigest")
        != corpus_summary["corpusDigest"]
        or metrics.get("corpus", {}).get("corpusDigest")
        != corpus_summary["corpusDigest"]
    ):
        raise ValueError("package control/metrics bindings are inconsistent")
    metrics_methodology = metrics.get("methodology")
    if (
        not isinstance(metrics_methodology, Mapping)
        or metrics_methodology.get("artifactIntegrityReady") is not True
    ):
        raise ValueError(
            "reproducibility package requires artifact-integrity-ready metrics"
        )
    metric_controls = metrics.get("methodology", {}).get("protocolControls")
    expected_control_digests = {
        "registration": registration["registrationDigest"],
        "sealedLabelCustody": seal["sealLedgerDigest"],
        "judgeCalibrationOrAudit": evaluation["judgeEvaluationDigest"],
    }
    if (
        not isinstance(metric_controls, Mapping)
        or any(
            not isinstance(metric_controls.get(name), Mapping)
            or metric_controls[name].get("status") != "validated"
            or metric_controls[name].get("digest") != digest
            for name, digest in expected_control_digests.items()
        )
    ):
        raise ValueError(
            "metrics do not bind the packaged publication protocol controls"
        )
    dashboard_dir = _safe_existing_path(root, dashboard_path)[0]
    if not dashboard_dir.is_dir():
        raise ValueError("dashboard artifact must be a directory")
    required_dashboard = {"index.html", "app.js", "styles.css", "data.json"}
    if not required_dashboard.issubset(
        {item.name for item in dashboard_dir.iterdir() if item.is_file()}
    ):
        raise ValueError("dashboard artifact is incomplete")
    if (dashboard_dir / "data.json").read_bytes() != metrics_file.read_bytes():
        raise ValueError("dashboard data.json does not equal the metrics artifact")
    analysis_run_paths = _artifact_paths_by_kind(
        root,
        list(analysis_artifacts),
        RUN_KIND,
    )
    judgment_paths = _artifact_paths_by_kind(
        root,
        list(judgment_artifacts),
        JUDGMENT_KIND,
    )
    post_fix_run_paths = _artifact_paths_by_kind(
        root,
        list(post_fix_artifacts),
        POST_FIX_RUN_KIND,
    )
    validate_protocol_bundle(
        corpus=corpus,
        registration_path=_safe_existing_path(root, registration_path)[0],
        seal_ledger_path=_safe_existing_path(root, seal_ledger_path)[0],
        judge_evaluation_path=_safe_existing_path(
            root,
            judge_evaluation_path,
        )[0],
        analysis_run_paths=analysis_run_paths,
        post_fix_analysis_run_paths=post_fix_run_paths,
        judgment_paths=judgment_paths,
    )
    extended_summary = _validate_extended_package_evidence(
        root=root,
        artifact_sets=artifact_sets,
        corpus_path=_safe_existing_path(root, corpus_path)[0],
        registration_path=_safe_existing_path(root, registration_path)[0],
        seal_path=_safe_existing_path(root, seal_ledger_path)[0],
        evaluation_path=_safe_existing_path(
            root,
            judge_evaluation_path,
        )[0],
        metrics_path=metrics_file,
        analysis_run_paths=analysis_run_paths,
        judgment_paths=judgment_paths,
    )

    result = {
        "kind": REPRODUCIBILITY_PACKAGE_KIND,
        "generatedAt": _now(),
        "artifactRootLabel": root.resolve(strict=True).name,
        "studyId": registration["studyId"],
        "registrationDigest": registration["registrationDigest"],
        "sealLedgerDigest": seal["sealLedgerDigest"],
        "judgeEvaluationDigest": evaluation["judgeEvaluationDigest"],
        "corpusDigest": registration["corpus"]["corpusDigest"],
        "metricsDigest": metrics["metricsDigest"],
        "semanticVerification": extended_summary,
        "artifactSets": artifact_sets,
        "files": entries,
        "rerunInstructions": instructions,
        "limitations": limitation_values,
        "nonCircularity": (
            "This package binds the finalized metrics artifact. The metrics "
            "artifact does not bind this later package manifest."
        ),
    }
    _scan_manifest_for_secrets(result)
    result["packageDigest"] = sha256_json(result)
    write_json(safe_output, result)
    verify_reproducibility_package(
        artifact_root=root,
        manifest_path=safe_output,
    )
    return result


def verify_reproducibility_package(
    *,
    artifact_root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    root = artifact_root
    if not root.is_dir() or root.is_symlink():
        raise ValueError("artifact_root must be a real directory")
    manifest_file, _ = _safe_existing_path(root, manifest_path)
    value = read_json(manifest_file)
    if not isinstance(value, Mapping):
        raise ValueError("reproducibility package manifest must be an object")
    digest = _artifact_digest(
        value,
        field="packageDigest",
        kind=REPRODUCIBILITY_PACKAGE_KIND,
    )
    _exact_fields(
        value,
        {
            "kind",
            "generatedAt",
            "artifactRootLabel",
            "studyId",
            "registrationDigest",
            "sealLedgerDigest",
            "judgeEvaluationDigest",
            "corpusDigest",
            "metricsDigest",
            "semanticVerification",
            "artifactSets",
            "files",
            "rerunInstructions",
            "limitations",
            "nonCircularity",
            "packageDigest",
        },
        "reproducibility package",
    )
    _scan_manifest_for_secrets(value)
    _timestamp(value.get("generatedAt"), "generatedAt")
    if value.get("artifactRootLabel") != root.resolve(strict=True).name:
        raise ValueError("artifact root label mismatch")
    for field in (
        "registrationDigest",
        "sealLedgerDigest",
        "judgeEvaluationDigest",
        "corpusDigest",
        "metricsDigest",
    ):
        _digest(value.get(field), field)
    if set(value.get("artifactSets") or {}) != REQUIRED_PACKAGE_CATEGORIES:
        raise ValueError("package artifact categories are incomplete")
    _text_list(value.get("rerunInstructions"), "rerunInstructions", minimum=2)
    _text_list(value.get("limitations"), "limitations")
    if value.get("nonCircularity") != (
        "This package binds the finalized metrics artifact. The metrics "
        "artifact does not bind this later package manifest."
    ):
        raise ValueError("package non-circularity statement drift")

    expected_files = []
    observed_categories: dict[str, str] = {}
    artifact_sets = value["artifactSets"]
    for category in sorted(REQUIRED_PACKAGE_CATEGORIES):
        roots = artifact_sets.get(category)
        if not isinstance(roots, list) or not roots:
            raise ValueError(f"package category {category} is empty")
        for relative_root in roots:
            if not isinstance(relative_root, str) or not relative_root:
                raise ValueError("artifact set path is invalid")
            for file_path, relative in _files_under(root, Path(relative_root)):
                if file_path == manifest_file:
                    raise ValueError("package manifest cannot include itself")
                previous = observed_categories.get(relative)
                if previous is not None and previous != category:
                    raise ValueError("package file belongs to multiple categories")
                observed_categories[relative] = category
                _scan_file_for_secrets(file_path, relative)
                expected_files.append(
                    {
                        "path": relative,
                        "category": category,
                        "sha256": _sha256_file(file_path),
                        "sizeBytes": file_path.stat().st_size,
                    }
                )
    expected_files.sort(key=lambda item: (item["path"], item["category"]))
    if value.get("files") != expected_files:
        raise ValueError("package file set, size, or digest mismatch")

    corpus_path = _single_artifact_by_kind(
        root,
        [Path(item) for item in artifact_sets["corpus"]],
        CORPUS_KIND,
        "corpus",
    )
    registration_path = _single_artifact_by_kind(
        root,
        [Path(item) for item in artifact_sets["registration"]],
        REGISTRATION_KIND,
        "registration",
    )
    seal_path = _single_artifact_by_kind(
        root,
        [Path(item) for item in artifact_sets["seal"]],
        SEAL_LEDGER_KIND,
        "seal",
    )
    evaluation_path = _single_artifact_by_kind(
        root,
        [Path(item) for item in artifact_sets["judge_evaluation"]],
        JUDGE_EVALUATION_KIND,
        "judge_evaluation",
    )
    metrics_path = _single_artifact_by_kind(
        root,
        [Path(item) for item in artifact_sets["metrics"]],
        "codecrow-magento2-benchmark-metrics",
        "metrics",
    )
    corpus = read_json(corpus_path)
    registration = read_json(registration_path)
    metrics = read_json(metrics_path)
    if not all(
        isinstance(item, Mapping)
        for item in (corpus, registration, metrics)
    ):
        raise ValueError("package semantic artifacts must be objects")
    corpus_summary = _strict_corpus(corpus)
    if (
        corpus_summary["corpusDigest"] != value["corpusDigest"]
        or registration.get("registrationDigest")
        != value["registrationDigest"]
        or metrics.get("metricsDigest") != value["metricsDigest"]
        or metrics.get("corpus", {}).get("corpusDigest")
        != corpus_summary["corpusDigest"]
    ):
        raise ValueError("package manifest semantic binding mismatch")
    metrics_methodology = metrics.get("methodology")
    if (
        not isinstance(metrics_methodology, Mapping)
        or metrics_methodology.get("artifactIntegrityReady") is not True
    ):
        raise ValueError(
            "packaged metrics artifact integrity is not ready"
        )
    metric_controls = metrics.get("methodology", {}).get("protocolControls")
    if not isinstance(metric_controls, Mapping) or any(
        not isinstance(metric_controls.get(name), Mapping)
        or metric_controls[name].get("status") != "validated"
        or metric_controls[name].get("digest") != value[digest_field]
        for name, digest_field in {
            "registration": "registrationDigest",
            "sealedLabelCustody": "sealLedgerDigest",
            "judgeCalibrationOrAudit": "judgeEvaluationDigest",
        }.items()
    ):
        raise ValueError("packaged metrics protocol-control binding mismatch")
    analysis_run_paths = _artifact_paths_by_kind(
        root,
        [Path(item) for item in artifact_sets["analysis"]],
        RUN_KIND,
    )
    judgment_paths = _artifact_paths_by_kind(
        root,
        [Path(item) for item in artifact_sets["judgment"]],
        JUDGMENT_KIND,
    )
    post_fix_run_paths = _artifact_paths_by_kind(
        root,
        [Path(item) for item in artifact_sets["post_fix"]],
        POST_FIX_RUN_KIND,
    )
    validate_protocol_bundle(
        corpus=corpus,
        registration_path=registration_path,
        seal_ledger_path=seal_path,
        judge_evaluation_path=evaluation_path,
        analysis_run_paths=analysis_run_paths,
        post_fix_analysis_run_paths=post_fix_run_paths,
        judgment_paths=judgment_paths,
    )
    semantic_summary = _validate_extended_package_evidence(
        root=root,
        artifact_sets=artifact_sets,
        corpus_path=corpus_path,
        registration_path=registration_path,
        seal_path=seal_path,
        evaluation_path=evaluation_path,
        metrics_path=metrics_path,
        analysis_run_paths=analysis_run_paths,
        judgment_paths=judgment_paths,
    )
    if value.get("semanticVerification") != semantic_summary:
        raise ValueError("package semantic verification summary drift")
    dashboard_roots = artifact_sets["dashboard"]
    if len(dashboard_roots) != 1:
        raise ValueError("dashboard category must have exactly one root")
    dashboard_dir, _ = _safe_existing_path(
        root,
        Path(dashboard_roots[0]),
    )
    if (
        not dashboard_dir.is_dir()
        or (dashboard_dir / "data.json").read_bytes()
        != metrics_path.read_bytes()
    ):
        raise ValueError("dashboard is not bound to packaged metrics")
    metrics_derivation = semantic_summary.get("metricsDerivation")
    if not isinstance(metrics_derivation, Mapping):
        raise ValueError("package metrics derivation summary is invalid")
    rebuilt_failures = metrics_derivation.get("paperGateFailures")
    if (
        not isinstance(rebuilt_failures, list)
        or any(
            not isinstance(failure, str) or not failure
            for failure in rebuilt_failures
        )
        or len(rebuilt_failures) != len(set(rebuilt_failures))
    ):
        raise ValueError("rebuilt metrics paper-gate failures are invalid")
    resolved_by_package = [
        failure
        for failure in rebuilt_failures
        if failure == "reproducibility_package_not_bound"
    ]
    remaining_blockers = [
        failure
        for failure in rebuilt_failures
        if failure != "reproducibility_package_not_bound"
    ]
    return {
        "studyId": value["studyId"],
        "packageDigest": digest,
        "files": len(expected_files),
        "categories": len(REQUIRED_PACKAGE_CATEGORIES),
        "publicationProtocolReady": not remaining_blockers,
        "metricsDerivationVerified": True,
        "rebuiltPaperGateFailures": list(rebuilt_failures),
        "resolvedByPackage": resolved_by_package,
        "remainingBlockers": remaining_blockers,
    }
