from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import secret_from_env
from .corpus import validate_corpus
from .runner import RUN_KIND
from .util import (
    canonical_json,
    configured_secret_values,
    deterministic_git_diff_command,
    hermetic_git_environment,
    public_config,
    read_json,
    redact_secret_text,
    require_no_secret_values,
    run,
    sha256_json,
    sha256_text,
    write_json,
)


JUDGMENT_KIND = "codecrow-magento2-judgment-run"
SAFE_JUDGMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
PROMPT_VERSION = "magento2-semantic-match-bounded-2026-07-29"
PAIR_PROMPT_COMPACTION_POLICY = "uniform-candidate-text-prefix-v1"
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


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _validated_judgment_id(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or SAFE_JUDGMENT_ID.fullmatch(value) is None
    ):
        raise ValueError(
            "judgment ID must be a safe 1-256 character identifier"
        )
    return value


def _resolved_judge_config(
    config: Mapping[str, Any],
    *,
    model: str | None,
    expected_response_model: str | None,
) -> tuple[dict[str, Any], str | None]:
    section = config.get("judge")
    if not isinstance(section, Mapping):
        raise ValueError("judge configuration is required")
    judge_config = dict(section)
    if model is not None:
        if not model.strip():
            raise ValueError("--judge-model must not be empty")
        judge_config["model"] = model.strip()
        if expected_response_model is None:
            judge_config["expected_response_model"] = model.strip()
    if expected_response_model is not None:
        if not expected_response_model.strip():
            raise ValueError("--expected-response-model must not be empty")
        judge_config["expected_response_model"] = (
            expected_response_model.strip()
        )
    requested_model = judge_config.get("model")
    if not isinstance(requested_model, str) or not requested_model.strip():
        raise ValueError("judge model is required")
    judge_config["model"] = requested_model.strip()
    expected_model_value = judge_config.get("expected_response_model", "")
    if not isinstance(expected_model_value, str):
        raise ValueError("judge.expected_response_model must be a string")
    resolved_model_expectation = (
        expected_model_value.strip() or judge_config["model"]
    )
    judge_config["expected_response_model"] = resolved_model_expectation
    return judge_config, resolved_model_expectation


def _trim(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return value[:maximum] + f"\n[truncated {len(value) - maximum} characters]"


def _extract_json(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        last_fence = stripped.rfind("```")
        if first_newline >= 0 and last_fence > first_newline:
            stripped = stripped[first_newline + 1 : last_fence].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


class OpenAICompatibleJudge:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.api_key = secret_from_env(config, "api_key_env")
        self.base_url = str(config["base_url"]).rstrip("/")
        self.model = str(config["model"])
        if not self.model:
            raise ValueError("judge model is required")
        expected_response_model = config.get("expected_response_model")
        if expected_response_model is not None and not isinstance(
            expected_response_model, str
        ):
            raise ValueError("judge.expected_response_model must be a string")
        self.expected_response_model = (
            expected_response_model.strip()
            if isinstance(expected_response_model, str)
            and expected_response_model.strip()
            else None
        )
        self.timeout = int(config.get("timeout_seconds") or 300)
        self.max_retries = int(config.get("max_retries") or 4)
        self.temperature = float(config.get("temperature") or 0)
        extra = self.config.get("custom_parameters")
        if extra is not None and not isinstance(extra, Mapping):
            raise ValueError("judge.custom_parameters must be an object")
        reserved = {"model", "messages", "response_format", "temperature"}
        collisions = reserved.intersection(extra or {})
        if collisions:
            raise ValueError(
                "judge.custom_parameters cannot override reserved request "
                "fields: " + ", ".join(sorted(collisions))
            )

    def call(self, *, system: str, user: str) -> tuple[Any, dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        extra = self.config.get("custom_parameters")
        if isinstance(extra, Mapping):
            payload.update(extra)
        secret_values = {
            self.api_key,
            *configured_secret_values(payload),
        }
        body = canonical_json(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "codecrow-magento2-benchmark-judge",
        }
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=body,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = json.loads(response.read())
                require_no_secret_values(
                    raw,
                    secret_values,
                    context="judge provider response",
                )
                choices = raw.get("choices") if isinstance(raw, Mapping) else None
                if not isinstance(choices, list) or not choices:
                    raise RuntimeError("judge response has no choices")
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, list):
                    content = "".join(
                        str(item.get("text") or "")
                        for item in content
                        if isinstance(item, Mapping)
                    )
                if not isinstance(content, str):
                    raise RuntimeError("judge response has no text content")
                provider_model = raw.get("model")
                if (
                    self.expected_response_model is not None
                    and provider_model != self.expected_response_model
                ):
                    raise RuntimeError(
                        "judge provider resolved model mismatch: expected "
                        f"{self.expected_response_model!r}, received "
                        f"{provider_model!r}"
                    )
                archived_request = public_config(payload)
                return _extract_json(content), {
                    "usage": raw.get("usage"),
                    "responseId": raw.get("id"),
                    "model": provider_model,
                    "promptSha256": sha256_text(system + "\n" + user),
                    "rawContentSha256": sha256_text(content),
                    "request": archived_request,
                    "requestSha256": sha256_json(archived_request),
                    "providerResponse": raw,
                    "providerResponseSha256": sha256_json(raw),
                }
            except urllib.error.HTTPError as exc:
                detail = redact_secret_text(
                    exc.read().decode("utf-8", errors="replace")[:2000],
                    secret_values,
                )
                last_error = RuntimeError(
                    f"judge HTTP {exc.code}: {detail}"
                )
                if exc.code < 500 and exc.code != 429:
                    break
            except (
                urllib.error.URLError,
                TimeoutError,
                json.JSONDecodeError,
                RuntimeError,
            ) as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(min(8, 2 ** (attempt - 1)))
        raise RuntimeError(f"judge call failed: {last_error}")


def _validated_judge_call(
    *,
    judge_client: Any,
    system: str,
    prompt: str,
    validator: Callable[[Any], Any],
    checkpoint_path: Path,
    binding: Mapping[str, Any],
    max_structured_retries: int,
    expected_response_model: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    if max_structured_retries < 1:
        raise ValueError("judge.max_structured_retries must be >= 1")
    binding_digest = sha256_json(
        {
            **dict(binding),
            "systemSha256": sha256_text(system),
            "promptSha256": sha256_text(prompt),
        }
    )
    if checkpoint_path.exists():
        cached = read_json(checkpoint_path)
        if not isinstance(cached, Mapping):
            raise ValueError(f"invalid judge checkpoint: {checkpoint_path}")
        digest_value = dict(cached)
        declared = digest_value.pop("callDigest", None)
        if (
            declared != sha256_json(digest_value)
            or cached.get("bindingDigest") != binding_digest
            or cached.get("system") != system
            or cached.get("prompt") != prompt
        ):
            raise ValueError(f"stale judge checkpoint: {checkpoint_path}")
        metadata = cached.get("metadata")
        if expected_response_model and (
            not isinstance(metadata, Mapping)
            or metadata.get("model") != expected_response_model
        ):
            raise ValueError(
                "judge checkpoint provider model mismatch: expected "
                f"{expected_response_model!r}"
            )
        normalized = validator(cached.get("response"))
        return normalized, dict(cached)

    rejected = []
    last_error: Exception | None = None
    for attempt in range(1, max_structured_retries + 1):
        value, metadata = judge_client.call(system=system, user=prompt)
        if expected_response_model and (
            not isinstance(metadata, Mapping)
            or metadata.get("model") != expected_response_model
        ):
            received = (
                metadata.get("model")
                if isinstance(metadata, Mapping)
                else None
            )
            raise RuntimeError(
                "judge provider resolved model mismatch: expected "
                f"{expected_response_model!r}, received {received!r}"
            )
        try:
            normalized = validator(value)
        except (TypeError, ValueError) as exc:
            last_error = exc
            rejected.append(
                {
                    "attempt": attempt,
                    "response": value,
                    "metadata": metadata,
                    "validationError": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        record = {
            "bindingDigest": binding_digest,
            "completedAt": _now(),
            "system": system,
            "prompt": prompt,
            "response": value,
            "metadata": metadata,
            "rejectedStructuredResponses": rejected,
        }
        record["callDigest"] = sha256_json(record)
        write_json(checkpoint_path, record)
        return normalized, record
    raise RuntimeError(
        "judge returned invalid structured output after "
        f"{max_structured_retries} attempts: {last_error}"
    )


MATCH_SYSTEM = """\
You are a blinded code-review benchmark judge. Compare one independently
curated reviewer issue against every candidate finding from an anonymous review
tool. Judge the frozen pre-fix snapshot only.

A substantive match requires the same underlying defect or harmful practice,
compatible consequence, and a corrective change that would satisfy both
reports. Similar file, category, or wording alone is not a match. A candidate
may be related but distinct. Return JSON only and judge every candidate ID once.
Do not infer that either side is correct merely because it is called gold.
"""


NOVEL_SYSTEM = """\
You are a blinded Magento 2 code-review adjudicator. Decide whether one
unmatched anonymous-tool finding identifies a real, actionable problem
introduced or exposed by the frozen diff. It is valid only if grounded in the
provided snapshot evidence and within normal code-review scope. Distinguish a
valid novel issue from an invalid claim, an out-of-scope preference, and a case
that cannot be verified from the supplied evidence. Return JSON only.
"""


def _gold_prompt(
    *,
    gold_label: str,
    gold: Mapping[str, Any],
    findings: list[Mapping[str, Any]],
    candidate_evidence: list[Mapping[str, Any]],
    max_prompt_characters: int | None = None,
) -> str:
    if len(candidate_evidence) != len(findings):
        raise ValueError(
            "candidate evidence must cover every finding exactly once"
        )
    if max_prompt_characters is not None and max_prompt_characters < 1:
        raise ValueError("max_prompt_characters must be positive")

    schema = {
        "gold_id": gold_label,
        "judgments": [
            {
                "candidate_id": "C001",
                "specific_issue": "yes|no|unclear",
                "grounded_at_snapshot": "yes|no|unclear",
                "same_root_cause": "yes|no|unclear",
                "same_failure_or_consequence": "yes|no|unclear",
                "compatible_required_change": "yes|no|unclear",
                "location_relation": (
                    "exact_line|same_symbol|same_functional_area|dependency|"
                    "unrelated|unclear"
                ),
                "verdict": (
                    "substantive_match|partial|related_distinct|no_match|"
                    "unverifiable"
                ),
                "confidence": 0.0,
                "rationale": "concise evidence-based explanation",
            }
        ],
    }

    def render(
        *,
        uniform_text_limit: int | None,
        compacted_for_maximum: int | None,
    ) -> str:
        candidates = []
        for index, finding in enumerate(findings, start=1):
            evidence = dict(candidate_evidence[index - 1])
            candidate = {
                "candidate_id": f"C{index:03d}",
                "path": finding.get("path"),
                "line": finding.get("line"),
                "title": finding.get("title"),
                "description": finding.get("description"),
                "category": finding.get("category"),
                "severity": finding.get("severity"),
                "suggested_fix": finding.get("suggestedFix"),
                "frozen_evidence": evidence,
            }
            if uniform_text_limit is not None:
                for field in ("title", "description", "suggested_fix"):
                    value = candidate.get(field)
                    if isinstance(value, str):
                        candidate[field] = _trim(value, uniform_text_limit)
                for field in ("pathDiff", "headSourceWindow"):
                    value = evidence.get(field)
                    if isinstance(value, str):
                        evidence[field] = _trim(value, uniform_text_limit)
            candidates.append(candidate)

        input_value = {
            "gold": {
                "gold_id": gold_label,
                "path": gold["path"],
                "line": gold["originalLine"],
                "review_comment": gold["body"],
                "curated_summary": gold["expectedIssue"]["summary"],
                "root_cause": gold["expectedIssue"].get("rootCause"),
                "failure_mode": gold["expectedIssue"].get("failureMode"),
                "required_change": gold["expectedIssue"].get("requiredChange"),
                "diff_hunk": gold["diffHunk"],
            },
            "candidates": candidates,
        }
        if uniform_text_limit is not None:
            input_value["evidence_compaction"] = {
                "policy": PAIR_PROMPT_COMPACTION_POLICY,
                "reason": "configured_prompt_character_limit",
                "maxPromptCharacters": compacted_for_maximum,
                "uniformCandidateTextFieldCharacters": uniform_text_limit,
                "candidateCountPreserved": len(candidates),
            }
        return (
            "INPUT:\n"
            + json.dumps(input_value, ensure_ascii=False, indent=2)
            + "\n\nOUTPUT SCHEMA:\n"
            + json.dumps(schema, indent=2)
        )

    prompt = render(
        uniform_text_limit=None,
        compacted_for_maximum=None,
    )
    if (
        max_prompt_characters is None
        or len(prompt) <= max_prompt_characters
    ):
        return prompt

    text_lengths = []
    for finding, evidence in zip(
        findings, candidate_evidence, strict=True
    ):
        text_lengths.extend(
            len(value)
            for value in (
                finding.get("title"),
                finding.get("description"),
                finding.get("suggestedFix"),
                evidence.get("pathDiff"),
                evidence.get("headSourceWindow"),
            )
            if isinstance(value, str)
        )
    upper = max(text_lengths, default=0)
    minimum = render(
        uniform_text_limit=0,
        compacted_for_maximum=max_prompt_characters,
    )
    if len(minimum) > max_prompt_characters:
        raise ValueError(
            "judge pair prompt cannot preserve every candidate within "
            f"the configured {max_prompt_characters}-character maximum; "
            f"minimum complete prompt is {len(minimum)} characters"
        )

    lower = 0
    best = minimum
    while lower <= upper:
        middle = (lower + upper) // 2
        candidate_prompt = render(
            uniform_text_limit=middle,
            compacted_for_maximum=max_prompt_characters,
        )
        if len(candidate_prompt) <= max_prompt_characters:
            best = candidate_prompt
            lower = middle + 1
        else:
            upper = middle - 1
    return best


def _validate_match_response(
    value: Any,
    *,
    gold_label: str,
    candidate_count: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping) or value.get("gold_id") != gold_label:
        raise ValueError("judge returned the wrong gold ID")
    judgments = value.get("judgments")
    if not isinstance(judgments, list):
        raise ValueError("judge judgments must be an array")
    expected = {f"C{index:03d}" for index in range(1, candidate_count + 1)}
    observed = {
        str(item.get("candidate_id"))
        for item in judgments
        if isinstance(item, Mapping)
    }
    if observed != expected or len(judgments) != len(expected):
        raise ValueError("judge must return every candidate ID exactly once")
    normalized = []
    for item in judgments:
        if not isinstance(item, Mapping):
            raise ValueError("judge judgment must be an object")
        for field in (
            "specific_issue",
            "grounded_at_snapshot",
            "same_root_cause",
            "same_failure_or_consequence",
            "compatible_required_change",
        ):
            if item.get(field) not in YES_NO_UNCLEAR:
                raise ValueError(f"invalid judge rubric value for {field}")
        if item.get("location_relation") not in LOCATION_RELATIONS:
            raise ValueError("invalid judge location relation")
        if item.get("verdict") not in MATCH_VERDICTS:
            raise ValueError("invalid judge match verdict")
        confidence = item.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise ValueError("judge confidence must be between zero and one")
        normalized.append(dict(item))
    normalized.sort(key=lambda item: item["candidate_id"])
    return normalized


def _majority_match(
    repeats: list[Mapping[str, Any]],
) -> dict[str, Any]:
    counts = Counter(str(item["verdict"]) for item in repeats)
    verdict, count = counts.most_common(1)[0]
    if count <= len(repeats) // 2:
        verdict = "unverifiable"
    agreeing = [item for item in repeats if item["verdict"] == verdict]
    chosen = max(
        agreeing or repeats,
        key=lambda item: float(item.get("confidence") or 0),
    )
    result = dict(chosen)
    result["verdict"] = verdict
    result["repeatAgreement"] = count / len(repeats)
    result["repeatVerdicts"] = dict(sorted(counts.items()))
    return result


def _edge_weight(judgment: Mapping[str, Any]) -> float:
    location = {
        "exact_line": 0.08,
        "same_symbol": 0.06,
        "dependency": 0.05,
        "same_functional_area": 0.03,
        "unclear": 0.0,
        "unrelated": -0.1,
    }[str(judgment["location_relation"])]
    return (
        float(judgment["confidence"])
        + float(judgment.get("repeatAgreement") or 0) * 0.1
        + location
    )


def _maximum_assignment(
    gold_count: int,
    candidate_count: int,
    judgments: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Maximum-cardinality, then maximum-evidence one-to-one assignment."""

    source = 0
    gold_start = 1
    candidate_start = gold_start + gold_count
    sink = candidate_start + candidate_count
    graph: list[list[dict[str, Any]]] = [[] for _ in range(sink + 1)]

    def edge(left: int, right: int, capacity: int, cost: int, meta: Any = None):
        forward = {
            "to": right,
            "rev": len(graph[right]),
            "capacity": capacity,
            "cost": cost,
            "meta": meta,
        }
        reverse = {
            "to": left,
            "rev": len(graph[left]),
            "capacity": 0,
            "cost": -cost,
            "meta": None,
        }
        graph[left].append(forward)
        graph[right].append(reverse)

    for gold_index in range(gold_count):
        edge(source, gold_start + gold_index, 1, 0)
    for candidate_index in range(candidate_count):
        edge(candidate_start + candidate_index, sink, 1, 0)
    for judgment in judgments:
        if judgment["verdict"] != "substantive_match":
            continue
        if any(
            judgment.get(field) != "yes"
            for field in (
                "specific_issue",
                "grounded_at_snapshot",
                "same_root_cause",
                "same_failure_or_consequence",
                "compatible_required_change",
            )
        ) or judgment.get("location_relation") in {"unrelated", "unclear"}:
            continue
        gold_index = int(str(judgment["goldId"])[1:]) - 1
        candidate_index = int(str(judgment["candidateId"])[1:]) - 1
        evidence = int(round(_edge_weight(judgment) * 10_000))
        edge(
            gold_start + gold_index,
            candidate_start + candidate_index,
            1,
            -1_000_000 - evidence,
            dict(judgment),
        )

    while True:
        distance = [10**18] * len(graph)
        previous: list[tuple[int, int] | None] = [None] * len(graph)
        distance[source] = 0
        for _ in range(len(graph) - 1):
            changed = False
            for node, edges in enumerate(graph):
                if distance[node] == 10**18:
                    continue
                for edge_index, item in enumerate(edges):
                    if item["capacity"] <= 0:
                        continue
                    candidate = distance[node] + item["cost"]
                    if candidate < distance[item["to"]]:
                        distance[item["to"]] = candidate
                        previous[item["to"]] = (node, edge_index)
                        changed = True
            if not changed:
                break
        if previous[sink] is None:
            break
        node = sink
        while node != source:
            parent, edge_index = previous[node]  # type: ignore[misc]
            item = graph[parent][edge_index]
            item["capacity"] -= 1
            graph[node][item["rev"]]["capacity"] += 1
            node = parent

    assignments = []
    for gold_index in range(gold_count):
        for item in graph[gold_start + gold_index]:
            if item["meta"] is not None and item["capacity"] == 0:
                assignments.append(
                    {
                        "goldId": item["meta"]["goldId"],
                        "candidateId": item["meta"]["candidateId"],
                        "weight": round(_edge_weight(item["meta"]), 6),
                        "judgment": item["meta"],
                    }
                )
    assignments.sort(key=lambda item: item["goldId"])
    return assignments


def _novel_prompt(
    *,
    candidate_label: str,
    finding: Mapping[str, Any],
    path_diff: str,
    head_source: str,
    path_in_frozen_diff: bool,
) -> str:
    value = {
        "candidate_id": candidate_label,
        "finding": {
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
        },
        "frozen_path_diff": _trim(path_diff, 24_000),
        "frozen_head_source": _trim(head_source, 24_000),
        "frozen_location_evidence": {
            "path_in_diff": path_in_frozen_diff,
            "line_on_added_right_side": (
                isinstance(finding.get("line"), int)
                and not isinstance(finding.get("line"), bool)
                and finding["line"] in _right_added_lines(path_diff)
            ),
        },
        "output_schema": {
            "candidate_id": candidate_label,
            "verdict": (
                "valid_in_scope_novel|invalid|out_of_scope|unverifiable"
            ),
            "grounded_at_snapshot": "yes|no|unclear",
            "actionable": "yes|no|unclear",
            "confidence": 0.0,
            "rationale": "concise evidence-based explanation",
        },
    }
    return json.dumps(value, ensure_ascii=False, indent=2)


def _validate_novel(value: Any, candidate_label: str) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or value.get("candidate_id") != candidate_label
        or value.get("verdict") not in NOVEL_VERDICTS
        or value.get("grounded_at_snapshot") not in YES_NO_UNCLEAR
        or value.get("actionable") not in YES_NO_UNCLEAR
    ):
        raise ValueError("judge returned an invalid novel-finding verdict")
    confidence = value.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
    ):
        raise ValueError("novel-finding confidence must be between zero and one")
    if value.get("verdict") == "valid_in_scope_novel" and (
        value.get("grounded_at_snapshot") != "yes"
        or value.get("actionable") != "yes"
    ):
        raise ValueError(
            "a valid novel finding must be grounded and actionable"
        )
    return dict(value)


def _majority_novel(values: list[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(item["verdict"]) for item in values)
    verdict, count = counts.most_common(1)[0]
    if count <= len(values) // 2:
        verdict = "unverifiable"
    agreeing = [item for item in values if item["verdict"] == verdict]
    chosen = max(
        agreeing or values,
        key=lambda item: float(item.get("confidence") or 0),
    )
    result = dict(chosen)
    result["verdict"] = verdict
    result["repeatAgreement"] = count / len(values)
    result["repeatVerdicts"] = dict(sorted(counts.items()))
    return result


def _path_evidence(
    repository: Path,
    base_sha: str,
    head_sha: str,
    path: str | None,
    allowed_paths: set[str],
) -> tuple[str, str]:
    if (
        not path
        or path not in allowed_paths
        or path.startswith("/")
        or ".." in Path(path).parts
    ):
        return "", ""
    path_diff = run(
        deterministic_git_diff_command(
            repository,
            "--unified=80",
            base_sha,
            head_sha,
            "--",
            f":(literal){path}",
        ),
        env=hermetic_git_environment(offline=True),
    )
    completed = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "show",
            f"{head_sha}:{path}",
        ],
        env=hermetic_git_environment(offline=True),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        errors="replace",
        check=False,
    )
    return path_diff, completed.stdout if completed.returncode == 0 else ""


def _right_added_lines(diff: str) -> set[int]:
    lines: set[int] = set()
    current: int | None = None
    for value in diff.splitlines():
        if value.startswith("@@"):
            match = re.match(
                r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@",
                value,
            )
            current = int(match.group(1)) if match else None
            continue
        if current is None or value.startswith("\\ No newline"):
            continue
        if value.startswith("+") and not value.startswith("+++"):
            lines.add(current)
            current += 1
        elif value.startswith("-") and not value.startswith("---"):
            continue
        else:
            current += 1
    return lines


def _line_window(source: str, line: Any, radius: int = 30) -> str:
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        return _trim(source, 8_000)
    lines = source.splitlines()
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return "\n".join(
        f"{number:>6} {lines[number - 1]}"
        for number in range(start, end + 1)
    )


def _candidate_evidence(
    repository: Path,
    case: Mapping[str, Any],
    findings: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    allowed_paths = set(case["snapshot"]["changedPaths"])
    for finding in findings:
        path = finding.get("path")
        normalized_path = path if isinstance(path, str) else None
        path_diff, head_source = _path_evidence(
            repository,
            case["snapshot"]["baseSha"],
            case["snapshot"]["headSha"],
            normalized_path,
            allowed_paths,
        )
        line = finding.get("line")
        result.append(
            {
                "inFrozenDiff": normalized_path in allowed_paths,
                "lineOnAddedRightSide": (
                    isinstance(line, int)
                    and not isinstance(line, bool)
                    and line in _right_added_lines(path_diff)
                ),
                "pathDiff": _trim(path_diff, 12_000),
                "headSourceWindow": _trim(
                    _line_window(head_source, line),
                    8_000,
                ),
                "pathDiffSha256": sha256_text(path_diff),
                "headSourceSha256": sha256_text(head_source),
            }
        )
    return result


def _validate_local_snapshot(
    repository: Path,
    case: Mapping[str, Any],
) -> None:
    base = case["snapshot"]["baseSha"]
    head = case["snapshot"]["headSha"]
    diff = run(
        deterministic_git_diff_command(
            repository,
            "--full-index",
            base,
            head,
        ),
        env=hermetic_git_environment(offline=True),
    )
    if sha256_text(diff) != case["snapshot"]["diffSha256"]:
        raise ValueError(f"local diff digest drift for {case['caseId']}")
    paths = sorted(
        value
        for value in run(
            deterministic_git_diff_command(
                repository,
                "--name-only",
                "-z",
                base,
                head,
            ),
            env=hermetic_git_environment(offline=True),
        ).split("\0")
        if value
    )
    if paths != case["snapshot"]["changedPaths"]:
        raise ValueError(f"local changed-path drift for {case['caseId']}")


def judge_run(
    *,
    corpus_path: Path,
    run_path: Path,
    repository: Path,
    output_dir: Path,
    config: Mapping[str, Any],
    judgment_id: str | None = None,
    model: str | None = None,
    expected_response_model: str | None = None,
    client: Any = None,
) -> dict[str, Any]:
    resolved_judgment_id = _validated_judgment_id(judgment_id)
    corpus = read_json(corpus_path)
    corpus_summary = validate_corpus(corpus)
    analysis_run = read_json(run_path)
    if not isinstance(analysis_run, Mapping) or analysis_run.get("kind") != RUN_KIND:
        raise ValueError("analysis run kind is invalid")
    run_digest_payload = dict(analysis_run)
    declared_run_digest = run_digest_payload.pop("runDigest", None)
    if declared_run_digest != sha256_json(run_digest_payload):
        raise ValueError("analysis run digest is missing or invalid")
    if analysis_run.get("corpusDigest") != corpus_summary["corpusDigest"]:
        raise ValueError("analysis run belongs to a different corpus")
    judge_config, resolved_model_expectation = _resolved_judge_config(
        config,
        model=model,
        expected_response_model=expected_response_model,
    )
    repeats = int(judge_config.get("repeats") or 1)
    if repeats < 1 or repeats % 2 == 0:
        raise ValueError("judge.repeats must be a positive odd integer")
    max_prompt_characters = int(
        judge_config.get("max_prompt_characters") or 400_000
    )
    if max_prompt_characters < 10_000:
        raise ValueError("judge.max_prompt_characters must be >= 10000")
    max_structured_retries = int(
        judge_config.get("max_structured_retries") or 3
    )
    if max_structured_retries < 1:
        raise ValueError("judge.max_structured_retries must be >= 1")
    public_judge_config = public_config(judge_config)
    judge_config_digest = sha256_json(public_judge_config)
    judge_client = client or OpenAICompatibleJudge(judge_config)
    if not repository.is_dir() or not (repository / ".git").exists():
        raise ValueError("judge repository must be a local Magento Git clone")
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_dir = output_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    run_cases = {
        item["caseId"]: item
        for item in analysis_run.get("cases") or []
        if isinstance(item, Mapping)
    }
    results = []
    for case in corpus["cases"]:
        run_case = run_cases.get(case["caseId"])
        if not run_case or run_case.get("status") != "completed":
            results.append(
                {
                    "caseId": case["caseId"],
                    "status": "not_scored",
                    "reason": "analysis_not_completed",
                }
            )
            continue
        case_input_digest = sha256_json(
            {
                "corpusCase": case,
                "analysisCase": run_case,
                "analysisRunDigest": declared_run_digest,
                "judgeConfigDigest": judge_config_digest,
                "promptVersion": PROMPT_VERSION,
            }
        )
        raw_path = raw_dir / f"{case['caseId']}.json"
        if raw_path.exists():
            cached_case = read_json(raw_path)
            if not isinstance(cached_case, Mapping):
                raise ValueError(f"invalid judge case checkpoint: {raw_path}")
            digest_value = dict(cached_case)
            declared_case_digest = digest_value.pop("caseDigest", None)
            if (
                declared_case_digest != sha256_json(digest_value)
                or cached_case.get("caseInputDigest") != case_input_digest
                or cached_case.get("judgeConfigDigest")
                != judge_config_digest
            ):
                raise ValueError(
                    f"stale judge case checkpoint for {case['caseId']}"
                )
            resumed_case = dict(cached_case)
            resumed_case["rawJudgment"] = str(
                raw_path.relative_to(output_dir)
            )
            results.append(resumed_case)
            continue
        _validate_local_snapshot(repository, case)
        findings = list(run_case.get("findings") or [])
        gold = list(case["goldenComments"])
        candidate_evidence = _candidate_evidence(
            repository,
            case,
            findings,
        )
        pair_judgments: list[dict[str, Any]] = []
        call_records = []
        if findings:
            for gold_index, gold_item in enumerate(gold, start=1):
                gold_label = f"G{gold_index:03d}"
                per_candidate: dict[str, list[dict[str, Any]]] = {
                    f"C{index:03d}": []
                    for index in range(1, len(findings) + 1)
                }
                for repeat_index in range(repeats):
                    prompt = _gold_prompt(
                        gold_label=gold_label,
                        gold=gold_item,
                        findings=findings,
                        candidate_evidence=candidate_evidence,
                        max_prompt_characters=max_prompt_characters,
                    )
                    if len(prompt) > max_prompt_characters:
                        raise ValueError(
                            f"judge prompt for {case['caseId']} {gold_label} "
                            f"has {len(prompt)} characters; configured maximum "
                            f"is {max_prompt_characters}"
                        )
                    call_binding = {
                        "kind": "pair",
                        "caseId": case["caseId"],
                        "goldId": gold_label,
                        "repeat": repeat_index + 1,
                        "caseInputDigest": case_input_digest,
                        "judgeConfigDigest": judge_config_digest,
                    }
                    checkpoint_name = (
                        "pair-"
                        f"{gold_label}-{repeat_index + 1}-"
                        f"{sha256_text(prompt)[:20]}.json"
                    )
                    judgments, call_record = _validated_judge_call(
                        judge_client=judge_client,
                        system=MATCH_SYSTEM,
                        prompt=prompt,
                        validator=lambda value: _validate_match_response(
                            value,
                            gold_label=gold_label,
                            candidate_count=len(findings),
                        ),
                        checkpoint_path=(
                            checkpoints_dir
                            / case["caseId"]
                            / checkpoint_name
                        ),
                        binding=call_binding,
                        max_structured_retries=max_structured_retries,
                        expected_response_model=resolved_model_expectation,
                    )
                    for item in judgments:
                        per_candidate[item["candidate_id"]].append(item)
                    call_records.append(
                        {
                            "kind": "pair",
                            "goldId": gold_label,
                            "repeat": repeat_index + 1,
                            "checkpoint": str(
                                (
                                    Path("checkpoints")
                                    / case["caseId"]
                                    / checkpoint_name
                                )
                            ),
                            **{
                                key: value
                                for key, value in call_record.items()
                                if key != "metadata"
                            },
                            **dict(call_record.get("metadata") or {}),
                        }
                    )
                for candidate_label, values in per_candidate.items():
                    majority = _majority_match(values)
                    pair_judgments.append(
                        {
                            "goldId": gold_label,
                            "candidateId": candidate_label,
                            **majority,
                        }
                    )
        assignments = _maximum_assignment(
            len(gold),
            len(findings),
            pair_judgments,
        )
        matched_gold = {item["goldId"] for item in assignments}
        matched_candidates = {item["candidateId"] for item in assignments}
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
        novel = []
        if judge_config.get("validate_unmatched_findings", True):
            for candidate_label in unmatched_candidates:
                candidate_index = int(candidate_label[1:]) - 1
                finding = findings[candidate_index]
                path_diff, head_source = _path_evidence(
                    repository,
                    case["snapshot"]["baseSha"],
                    case["snapshot"]["headSha"],
                    (
                        finding.get("path")
                        if isinstance(finding.get("path"), str)
                        else None
                    ),
                    set(case["snapshot"]["changedPaths"]),
                )
                values = []
                for repeat_index in range(repeats):
                    prompt = _novel_prompt(
                        candidate_label=candidate_label,
                        finding=finding,
                        path_diff=path_diff,
                        head_source=head_source,
                        path_in_frozen_diff=(
                            isinstance(finding.get("path"), str)
                            and finding.get("path")
                            in set(case["snapshot"]["changedPaths"])
                        ),
                    )
                    if len(prompt) > max_prompt_characters:
                        raise ValueError(
                            f"novel-finding prompt for {case['caseId']} "
                            f"{candidate_label} exceeds configured maximum"
                        )
                    call_binding = {
                        "kind": "novel",
                        "caseId": case["caseId"],
                        "candidateId": candidate_label,
                        "repeat": repeat_index + 1,
                        "caseInputDigest": case_input_digest,
                        "judgeConfigDigest": judge_config_digest,
                    }
                    checkpoint_name = (
                        "novel-"
                        f"{candidate_label}-{repeat_index + 1}-"
                        f"{sha256_text(prompt)[:20]}.json"
                    )
                    normalized, call_record = _validated_judge_call(
                        judge_client=judge_client,
                        system=NOVEL_SYSTEM,
                        prompt=prompt,
                        validator=lambda value: _validate_novel(
                            value, candidate_label
                        ),
                        checkpoint_path=(
                            checkpoints_dir
                            / case["caseId"]
                            / checkpoint_name
                        ),
                        binding=call_binding,
                        max_structured_retries=max_structured_retries,
                        expected_response_model=resolved_model_expectation,
                    )
                    values.append(normalized)
                    call_records.append(
                        {
                            "kind": "novel",
                            "candidateId": candidate_label,
                            "repeat": repeat_index + 1,
                            "checkpoint": str(
                                (
                                    Path("checkpoints")
                                    / case["caseId"]
                                    / checkpoint_name
                                )
                            ),
                            **{
                                key: value
                                for key, value in call_record.items()
                                if key != "metadata"
                            },
                            **dict(call_record.get("metadata") or {}),
                        }
                    )
                novel.append(
                    {
                        "candidateId": candidate_label,
                        **_majority_novel(values),
                    }
                )
        case_result = {
            "caseId": case["caseId"],
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
            "calls": call_records,
        }
        case_result["caseDigest"] = sha256_json(case_result)
        write_json(raw_path, case_result)
        case_result["rawJudgment"] = str(raw_path.relative_to(output_dir))
        results.append(case_result)

    result = {
        "kind": JUDGMENT_KIND,
        "judgmentId": (
            resolved_judgment_id or f"m2j-{os.urandom(12).hex()}"
        ),
        "createdAt": _now(),
        "promptVersion": PROMPT_VERSION,
        "promptDigest": sha256_text(MATCH_SYSTEM + NOVEL_SYSTEM + PROMPT_VERSION),
        "corpusId": corpus_summary["corpusId"],
        "corpusDigest": corpus_summary["corpusDigest"],
        "analysisRunId": analysis_run["runId"],
        "analysisRunDigest": declared_run_digest,
        "analysisModel": analysis_run["analysisModel"],
        "judgeModel": judge_config["model"],
        "judgeConfig": public_judge_config,
        "judgeConfigDigest": judge_config_digest,
        "cases": results,
    }
    result["judgmentDigest"] = sha256_json(result)
    write_json(output_dir / "judgments.json", result)
    return result
