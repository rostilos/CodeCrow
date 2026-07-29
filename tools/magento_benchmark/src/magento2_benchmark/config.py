from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any, Mapping


DEFAULTS: dict[str, Any] = {
    "github": {
        "api_url": "https://api.github.com",
        "repository": "magento/magento2",
        "default_branch": "2.4-develop",
        "token_env": "GITHUB_TOKEN",
        "cache_dir": ".cache/github",
        "timeout_seconds": 60,
    },
    "corpus": {
        "required_cases": 50,
        "small_min": 3,
        "small_max": 10,
        "medium_min": 11,
        "medium_max": 30,
        "large_min": 31,
        "large_max": 80,
        "required_small": 1,
        "required_medium": 1,
        "required_large": 1,
    },
    "analysis": {
        "transport": "redis",
        "endpoint": "http://127.0.0.1:8015/review",
        "rag_endpoint": "http://127.0.0.1:8004",
        "finalizer_endpoint": (
            "http://127.0.0.1:8081"
            "/api/internal/analysis/benchmark-finalize"
        ),
        "redis_container": "codecrow-redis",
        "analysis_container": "codecrow-inference-orchestrator",
        "rag_container": "codecrow-rag-pipeline",
        "finalizer_container": "codecrow-web-application",
        "redis_db": 1,
        "redis_queue": "codecrow:analysis:jobs",
        "service_secret_env": "CODECROW_SERVICE_SECRET",
        "provider": "openrouter",
        "model": "",
        "expected_response_model": "",
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "",
        "project_id": 0,
        "project_vcs_workspace": "",
        "project_vcs_repo_slug": "magento2",
        "project_workspace": "",
        "project_namespace": "",
        "rag_workspace": "",
        "rag_project": "",
        "timeout_seconds": 7200,
        "max_case_attempts": 1,
        "require_model_call_evidence": False,
        "quality_capture_container_dir": (
            "/app/logs/review-quality-captures"
        ),
        "require_exact_index": True,
        "require_retrieval_evidence": True,
        "require_replay_attestation": False,
        "replay_attestation_max_age_seconds": 3_600,
        "require_runtime_provenance": False,
        "required_repository_plugins": ["php", "magento"],
        "max_enrichment_file_bytes": 5_242_880,
        "max_enrichment_total_bytes": 52_428_800,
        "custom_parameters": {"temperature": 0},
    },
    "judge": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "",
        "expected_response_model": "",
        "api_key_env": "OPENROUTER_API_KEY",
        "timeout_seconds": 300,
        "temperature": 0,
        "repeats": 1,
        "max_retries": 4,
        "max_structured_retries": 3,
        "max_prompt_characters": 400_000,
        "validate_unmatched_findings": True,
    },
}


def _merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def load_config(path: Path | None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULTS)
    if path is not None:
        try:
            with path.open("rb") as handle:
                parsed = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"cannot read benchmark config {path}: {exc}") from exc
        _merge(config, parsed)
    return config


def secret_from_env(section: Mapping[str, Any], field: str) -> str:
    variable = section.get(field)
    if not isinstance(variable, str) or not variable:
        raise ValueError(f"{field} must name an environment variable")
    value = os.getenv(variable)
    if not value:
        raise ValueError(f"required secret environment variable is unset: {variable}")
    return value


def apply_model_overrides(
    config: dict[str, Any],
    *,
    analysis_model: str | None = None,
    expected_analysis_response_model: str | None = None,
    judge_model: str | None = None,
) -> None:
    if analysis_model:
        config["analysis"]["model"] = analysis_model
        if expected_analysis_response_model is None:
            config["analysis"]["expected_response_model"] = analysis_model
    if expected_analysis_response_model:
        config["analysis"][
            "expected_response_model"
        ] = expected_analysis_response_model
    if judge_model:
        config["judge"]["model"] = judge_model
