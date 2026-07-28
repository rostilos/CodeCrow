"""Fixed prompt-assembly settings for provider-free quality gates."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import MutableMapping
from collections.abc import Mapping, Sequence
from typing import Any


# These values are part of the checked-in gate corpus. Keep them explicit so a
# developer's .env, import order, or production shell cannot silently change
# batching and then produce incomparable prompt digests/token diagnostics.
FIXED_PROMPT_GATE_ENV = {
    "REVIEW_CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED": "false",
    "REVIEW_DETERMINISTIC_RAG_MAX_CHUNKS": "80",
    "REVIEW_FAST_CHECK_ENABLED": "true",
    "REVIEW_FAST_CHECK_MAX_CHANGED_LINES": "800",
    "REVIEW_FAST_CHECK_DEDUP_MAX_ISSUES": "5",
    "REVIEW_FAST_CHECK_MAX_DIFF_BYTES": "120000",
    "REVIEW_FAST_CHECK_MAX_FILES": "4",
    "REVIEW_LLM_DEDUP_ENABLED": "false",
    "REVIEW_MEDIUM_MAX_CHANGED_LINES": "3000",
    "REVIEW_MEDIUM_MAX_DIFF_BYTES": "450000",
    "REVIEW_MEDIUM_MAX_FILES": "15",
    "REVIEW_OUTPUT_CAP_MODEL_KWARG": "",
    "REVIEW_OUTPUT_CAPS_ENABLED": "true",
    "REVIEW_STAGE_0_LARGE_MAX_OUTPUT_TOKENS": "20000",
    "REVIEW_STAGE_0_MEDIUM_MAX_OUTPUT_TOKENS": "15000",
    "REVIEW_STAGE_0_SMALL_MAX_OUTPUT_TOKENS": "10000",
    "REVIEW_STAGE1_BATCH_TOKEN_BUDGET": "60000",
    "REVIEW_STAGE1_CURRENT_SOURCE_BATCH_CHAR_BUDGET": "48000",
    "REVIEW_STAGE1_DIFF_CHUNK_TOKEN_BUDGET": "35000",
    "REVIEW_STAGE1_MAX_CURRENT_FILE_CHARS": "12000",
    "REVIEW_STAGE1_MAX_FILES_PER_BATCH": "15",
    "REVIEW_STAGE1_METADATA_CHAR_BUDGET": "24000",
    "REVIEW_STAGE1_METADATA_PER_FILE_CHAR_BUDGET": "6000",
    "REVIEW_STAGE_1_LARGE_MAX_OUTPUT_TOKENS": "80000",
    "REVIEW_STAGE_1_MEDIUM_MAX_OUTPUT_TOKENS": "50000",
    "REVIEW_STAGE_1_SMALL_MAX_OUTPUT_TOKENS": "30000",
    "REVIEW_STAGE_2_LARGE_MAX_OUTPUT_TOKENS": "25000",
    "REVIEW_STAGE_2_MEDIUM_MAX_OUTPUT_TOKENS": "18000",
    "REVIEW_STAGE_2_SMALL_MAX_OUTPUT_TOKENS": "11000",
    "REVIEW_STAGE_3_LARGE_MAX_OUTPUT_TOKENS": "24000",
    "REVIEW_STAGE_3_MEDIUM_MAX_OUTPUT_TOKENS": "20000",
    "REVIEW_STAGE_3_SMALL_MAX_OUTPUT_TOKENS": "12000",
    "REVIEW_STAGE_2_ENABLED": "true",
    "REVIEW_STRUCTURED_OUTPUT_ENABLED": "true",
    "REVIEW_VERIFICATION_LARGE_MAX_OUTPUT_TOKENS": "12000",
    "REVIEW_VERIFICATION_MEDIUM_MAX_OUTPUT_TOKENS": "8000",
    "REVIEW_VERIFICATION_SMALL_MAX_OUTPUT_TOKENS": "5000",
    "REVIEW_DEDUP_LARGE_MAX_OUTPUT_TOKENS": "8000",
    "REVIEW_DEDUP_MEDIUM_MAX_OUTPUT_TOKENS": "5000",
    "REVIEW_DEDUP_SMALL_MAX_OUTPUT_TOKENS": "3000",
}


def apply_fixed_prompt_gate_profile(
    environment: MutableMapping[str, str] | None = None,
) -> None:
    """Overwrite prompt knobs before importing inference modules."""
    target = os.environ if environment is None else environment
    target.update(FIXED_PROMPT_GATE_ENV)


_STAGE_ORDER = {
    "stage_0": 0,
    "rag_reranking": 1,
    "stage_1": 2,
    "stage_2": 3,
    "verification": 4,
    "deduplication": 5,
    "stage_3": 6,
}


def stable_prompt_digest(
    prompts: Sequence[Mapping[str, Any]],
) -> str:
    """Hash prompt content while ignoring parallel same-stage completion order."""
    encoded = _stable_prompt_records(prompts)
    return hashlib.sha256(
        "\n".join(encoded).encode("utf-8")
    ).hexdigest()


def stable_prompt_record_digests(
    prompts: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Expose source-free per-prompt hashes for cross-process audit."""
    return tuple(
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        for value in _stable_prompt_records(prompts)
    )


def _stable_prompt_records(
    prompts: Sequence[Mapping[str, Any]],
) -> list[str]:
    records = []
    for prompt in prompts:
        normalized = {
            key: value
            for key, value in prompt.items()
            if key != "sequence"
        }
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        records.append((
            _STAGE_ORDER.get(str(prompt.get("stage")), 100),
            encoded,
        ))
    return [
        encoded
        for _, encoded in sorted(records)
    ]
