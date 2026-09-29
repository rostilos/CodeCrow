"""Deterministic semantic packet construction for Stage 2 analysis.

This module owns the bounded packing mechanics.  It has no provider or
orchestration dependencies: callers supply normalized review evidence and
receive immutable prompt values carrying exact visibility provenance.
"""
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from model.dtos import ReviewRequestDto
from model.multi_stage import CrossFileAnalysisResult
from service.review.pr_evidence import PrEvidenceLedger
from utils.prompts.prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)

__all__ = [
    "Stage2Prompt",
    "Stage2SemanticPacketInput",
    "build_stage_2_prompts",
]


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


# Stage 2 shares the review input target with Stage 1. This is a rendered-prompt
# target; the review profile also places a finite ceiling on concrete packets.
STAGE2_INPUT_TOKEN_TARGET = max(
    10_000,
    _env_int("REVIEW_STAGE1_BATCH_TOKEN_BUDGET", 60_000),
)
STAGE_2_ARCHITECTURE_CONTEXT_CHAR_BUDGET = max(
    8_000,
    _env_int("REVIEW_STAGE_2_ARCHITECTURE_CONTEXT_CHAR_BUDGET", 64_000),
)
_STAGE2_COMMON_INPUT_TOKEN_TARGET = 10_000
_STAGE2_CONTEXT_RESERVE_TOKENS = 20_000
_STAGE2_ESTIMATOR_SAFETY_TOKENS = 256
_STAGE2_OMISSION_NOTICE_RESERVE_TOKENS = 256
_SEMANTIC_SHARD_NOTICE = (
    "This is one bounded semantic shard of Stage 2 input. Invocation-coverage "
    "diagnostics identify packets that could not be admitted under the review "
    "profile. Absence from this shard is not evidence that code, a requirement, "
    "or a relationship is absent from the pull request."
)


def _schema_declaration_bytes() -> int:
    try:
        schema = CrossFileAnalysisResult.model_json_schema()
    except (AttributeError, TypeError, ValueError):
        return 0
    return len(json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8"))


_STAGE2_SCHEMA_DECLARATION_BYTES = _schema_declaration_bytes()


class Stage2Prompt(str):
    """Rendered prompt plus exact invocation provenance."""

    visible_hunk_ids: frozenset[str]
    visible_evidence_ids: frozenset[str]
    optional_enrichment_only: bool
    complete_pr_evidence_visible: bool
    omitted_packet_count: int
    omitted_unit_count: int
    omitted_hunk_count: int

    def __new__(
        cls,
        value: str,
        *,
        visible_hunk_ids: Iterable[str] = (),
        visible_evidence_ids: Iterable[str] = (),
        optional_enrichment_only: bool = False,
        complete_pr_evidence_visible: bool = False,
        omitted_packet_count: int = 0,
        omitted_unit_count: int = 0,
        omitted_hunk_count: int = 0,
    ):
        instance = str.__new__(cls, value)
        instance.visible_hunk_ids = frozenset(visible_hunk_ids)
        instance.visible_evidence_ids = frozenset(visible_evidence_ids)
        instance.optional_enrichment_only = optional_enrichment_only
        instance.complete_pr_evidence_visible = complete_pr_evidence_visible
        instance.omitted_packet_count = omitted_packet_count
        instance.omitted_unit_count = omitted_unit_count
        instance.omitted_hunk_count = omitted_hunk_count
        return instance


# Historical callers import this private name from ``stage_2_cross_file``.
_Stage2Prompt = Stage2Prompt


@dataclass(frozen=True)
class _DependencyAnchor:
    """Compact repeated navigation fact; never a substitute for full evidence."""

    paths: tuple[str, ...]
    payload: Dict[str, Any]


@dataclass(frozen=True)
class _Stage2Unit:
    """One independently movable Stage 2 evidence unit."""

    key: str
    paths: tuple[str, ...] = ()
    findings: tuple[Dict[str, Any], ...] = ()
    relationships: tuple[Dict[str, Any], ...] = ()
    metadata: tuple[Dict[str, Any], ...] = ()
    full_pr_parts: tuple[str, ...] = ()
    delta_parts: tuple[str, ...] = ()
    task_parts: tuple[str, ...] = ()
    history_parts: tuple[str, ...] = ()
    concerns: tuple[str, ...] = ()
    rule_parts: tuple[str, ...] = ()
    dependency_anchors: tuple[_DependencyAnchor, ...] = ()
    children: tuple["_Stage2Unit", ...] = ()
    review_hunk_ids: frozenset[str] = frozenset()
    optional_enrichment_only: bool = False


@dataclass
class _Stage2Packet:
    """Mutable accumulator used while exact rendered prompts are packed."""

    units: List[_Stage2Unit] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    metadata: List[Dict[str, Any]] = field(default_factory=list)
    full_pr_parts: List[str] = field(default_factory=list)
    delta_parts: List[str] = field(default_factory=list)
    task_parts: List[str] = field(default_factory=list)
    history_parts: List[str] = field(default_factory=list)
    concerns: List[str] = field(default_factory=list)
    rule_parts: List[str] = field(default_factory=list)
    dependency_anchors: List[_DependencyAnchor] = field(default_factory=list)
    review_hunk_ids: set[str] = field(default_factory=set)

    def add(self, unit: _Stage2Unit) -> None:
        self.units.append(unit)
        self.findings.extend(unit.findings)
        self.relationships.extend(unit.relationships)
        self.metadata.extend(unit.metadata)
        self.full_pr_parts.extend(unit.full_pr_parts)
        self.delta_parts.extend(unit.delta_parts)
        self.task_parts.extend(unit.task_parts)
        self.history_parts.extend(unit.history_parts)
        self.concerns.extend(unit.concerns)
        self.rule_parts.extend(unit.rule_parts)
        self.dependency_anchors.extend(unit.dependency_anchors)
        self.review_hunk_ids.update(unit.review_hunk_ids)

    def copy_with(self, unit: _Stage2Unit) -> "_Stage2Packet":
        result = _Stage2Packet()
        for existing in self.units:
            result.add(existing)
        result.add(unit)
        return result

    @property
    def optional_enrichment_only(self) -> bool:
        return bool(self.units) and all(
            unit.optional_enrichment_only
            for unit in self.units
        )


@dataclass(frozen=True)
class _Stage2PromptStatic:
    repo_slug: str
    pr_title: str
    commit_hash: str
    migrations: str
    common_task_context: str
    common_history_context: str
    common_concerns: tuple[str, ...]
    common_project_rules: str
    architecture_path_table: Mapping[str, str]
    full_pr_status: str
    delta_status: str


def _stage_2_packet_priority(packet: _Stage2Packet) -> int:
    """Order admitted calls by review authority, not by serialization order."""
    if (
        packet.findings
        or packet.full_pr_parts
        or packet.delta_parts
        or packet.review_hunk_ids
    ):
        return 0
    if packet.relationships or packet.metadata or packet.dependency_anchors:
        return 1
    return 2


def _stage_2_unit_priority(unit: _Stage2Unit) -> int:
    if (
        unit.findings
        or unit.full_pr_parts
        or unit.delta_parts
        or unit.review_hunk_ids
    ):
        return 0
    if unit.relationships or unit.metadata or unit.dependency_anchors:
        return 1
    return 2


def _positive_int_or_default(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if not isinstance(value, (int, str)):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _stage_2_input_token_budget(request: ReviewRequestDto) -> int:
    """Reserve context space without imposing any generation-output limit."""
    model_context_tokens = _positive_int_or_default(
        getattr(request, "maxAllowedTokens", None),
        200_000,
    )
    if model_context_tokens > _STAGE2_CONTEXT_RESERVE_TOKENS:
        model_safe_input = model_context_tokens - _STAGE2_CONTEXT_RESERVE_TOKENS
    else:
        # Very small context declarations cannot accommodate the normal reserve.
        # Keep half available for generation and let the semantic packer report
        # any indivisible input unit that cannot fit the remaining half intact.
        model_safe_input = max(1, model_context_tokens // 2)
    return min(STAGE2_INPUT_TOKEN_TARGET, model_safe_input)


def _estimated_prompt_tokens(prompt: str) -> int:
    """Conservative UTF-8/schema-aware estimate used only for input packing."""
    request_bytes = (
        len(prompt.encode("utf-8"))
        + _STAGE2_SCHEMA_DECLARATION_BYTES
    )
    return max(
        1,
        (request_bytes + 2) // 3 + _STAGE2_ESTIMATOR_SAFETY_TOKENS,
    )


@dataclass(frozen=True)
class Stage2SemanticPacketInput:
    """Normalized inputs required by deterministic Stage 2 prompt packing."""

    repo_slug: str
    pr_title: str
    commit_hash: str
    stage_1_findings_json: str
    architecture_context: str
    migrations: str
    cross_file_concerns: Sequence[str]
    project_rules: str
    task_context: str
    task_history_context: str
    evidence_ledger: PrEvidenceLedger
    token_budget: int = STAGE2_INPUT_TOKEN_TARGET
    max_packets: int = 4


def build_stage_2_prompts(
    packet_input: Stage2SemanticPacketInput,
) -> List[Stage2Prompt]:
    """Build complete or bounded prompts without depending on orchestration."""
    return _build_stage_2_prompts_impl(
        repo_slug=packet_input.repo_slug,
        pr_title=packet_input.pr_title,
        commit_hash=packet_input.commit_hash,
        stage_1_findings_json=packet_input.stage_1_findings_json,
        architecture_context=packet_input.architecture_context,
        migrations=packet_input.migrations,
        cross_file_concerns=packet_input.cross_file_concerns,
        project_rules=packet_input.project_rules,
        task_context=packet_input.task_context,
        task_history_context=packet_input.task_history_context,
        evidence_ledger=packet_input.evidence_ledger,
        token_budget=packet_input.token_budget,
        max_packets=packet_input.max_packets,
    )


def _build_stage_2_prompts_impl(
    *,
    repo_slug: str,
    pr_title: str,
    commit_hash: str,
    stage_1_findings_json: str,
    architecture_context: str,
    migrations: str,
    cross_file_concerns: Sequence[str],
    project_rules: str,
    task_context: str,
    task_history_context: str,
    evidence_ledger: PrEvidenceLedger,
    token_budget: int,
    max_packets: int,
) -> List[Stage2Prompt]:
    """Render one complete prompt or pack bounded semantic Stage 2 shards.

    The complete prompt remains the normal path.  Sharding begins only after
    the fully rendered request exceeds the review input target. Relationships
    are grouped by connected component first; the profile admits core PR
    evidence, then exact architecture, then optional context up to its packet
    ceiling.
    """
    findings = _parse_stage_1_findings(stage_1_findings_json)
    complete_prompt = PromptBuilder.build_stage_2_cross_file_prompt(
        repo_slug=repo_slug,
        pr_title=pr_title,
        commit_hash=commit_hash,
        stage_1_findings_json=stage_1_findings_json,
        architecture_context=architecture_context,
        migrations=migrations,
        cross_file_concerns=list(cross_file_concerns),
        project_rules=project_rules,
        task_context=task_context,
        task_history_context=task_history_context,
        pr_change_summary=evidence_ledger.full_pr_context,
        incremental_delta_summary=evidence_ledger.incremental_delta_context,
    )
    if _estimated_prompt_tokens(complete_prompt) <= token_budget:
        return [_Stage2Prompt(
            complete_prompt,
            visible_hunk_ids=evidence_ledger.delta_hunk_ids,
            visible_evidence_ids=_finding_evidence_ids(findings),
            complete_pr_evidence_visible=True,
        )]

    # Leave space for the bounded-packet coverage diagnostic before deciding
    # that a semantic packet fits the provider request.
    packing_token_budget = max(
        1,
        token_budget - _STAGE2_OMISSION_NOTICE_RESERVE_TOKENS,
    )

    architecture_payload = _parse_architecture_payload(architecture_context)
    path_table = architecture_payload.get("path_table", {})
    if not isinstance(path_table, dict):
        path_table = {}

    full_status = (
        "CURRENT SHARD EVIDENCE STATUS: PARTIAL. This shard cannot prove that "
        "a task requirement or implementation is absent. The global ledger "
        "metadata reports manifest_complete="
        + str(bool(evidence_ledger.manifest_complete)).lower()
        + " and full_evidence_complete="
        + str(bool(evidence_ledger.full_evidence_complete)).lower()
        + ", but those global flags do not make this shard complete."
    )
    delta_status = (
        "Incremental delta evidence is present."
        if evidence_ledger.incremental
        else "This is a full review; review scope and full PR scope are identical."
    )

    common_static = _Stage2PromptStatic(
        repo_slug=repo_slug,
        pr_title=pr_title,
        commit_hash=commit_hash,
        migrations=migrations,
        common_task_context=task_context,
        common_history_context=task_history_context,
        common_concerns=tuple(cross_file_concerns),
        common_project_rules=project_rules,
        architecture_path_table=path_table,
        full_pr_status=full_status,
        delta_status=delta_status,
    )
    common_probe = _render_stage_2_packet(common_static, _Stage2Packet())
    global_units: List[_Stage2Unit] = []
    if _estimated_prompt_tokens(common_probe) > min(
        packing_token_budget,
        _STAGE2_COMMON_INPUT_TOKEN_TARGET,
    ):
        # Large global context is carried once as semantic records instead of
        # being repeated in every evidence prompt.
        task_anchor = _semantic_navigation_anchor(task_context, "task")
        history_anchor = _semantic_navigation_anchor(
            task_history_context,
            "prior task history",
        )
        common_concerns = (
            tuple(cross_file_concerns)
            if sum(len(item.encode("utf-8")) for item in cross_file_concerns)
            <= 4_000
            else ()
        )
        common_project_rules = (
            project_rules
            if len(project_rules.encode("utf-8")) <= 4_000
            else ""
        )
        common_static = _Stage2PromptStatic(
            repo_slug=repo_slug,
            pr_title=pr_title,
            commit_hash=commit_hash,
            migrations=migrations,
            common_task_context=task_anchor,
            common_history_context=history_anchor,
            common_concerns=common_concerns,
            common_project_rules=common_project_rules,
            architecture_path_table=path_table,
            full_pr_status=full_status,
            delta_status=delta_status,
        )
        if task_context != task_anchor:
            global_units.extend(
                _text_units(
                    "task", task_context, "task_parts", packing_token_budget
                )
            )
        if task_history_context != history_anchor:
            global_units.extend(
                _text_units(
                    "history",
                    task_history_context,
                    "history_parts",
                    packing_token_budget,
                )
            )
        if not common_concerns:
            global_units.extend(
                _Stage2Unit(key=f"concern:{index:06d}", concerns=(concern,))
                for index, concern in enumerate(cross_file_concerns)
            )
        if project_rules != common_project_rules:
            global_units.extend(_project_rule_units(project_rules))

    header_units = [
        *_text_units(
            "full-pr-header",
            _shard_safe_ledger_header(evidence_ledger.full_pr_context),
            "full_pr_parts",
            packing_token_budget,
        ),
        *_text_units(
            "delta-header",
            _shard_safe_ledger_header(evidence_ledger.incremental_delta_context),
            "delta_parts",
            packing_token_budget,
        ),
    ]
    component_units = _build_component_units(
        findings=findings,
        architecture_payload=architecture_payload,
        evidence_ledger=evidence_ledger,
    )
    units = [*global_units, *header_units, *component_units]
    if not units:
        units = [_Stage2Unit(key="empty-stage-2-input")]

    expanded: List[_Stage2Unit] = []
    for unit in units:
        expanded.extend(
            _expand_oversized_unit(
                unit,
                static=common_static,
                token_budget=packing_token_budget,
            )
        )

    packets: List[_Stage2Packet] = []
    current = _Stage2Packet()
    for unit in expanded:
        candidate = current.copy_with(unit)
        candidate_prompt = _render_stage_2_packet(common_static, candidate)
        crosses_failure_boundary = (
            bool(current.units)
            and current.optional_enrichment_only
            != unit.optional_enrichment_only
        )
        crosses_authority_boundary = (
            bool(current.units)
            and _stage_2_packet_priority(current)
            != _stage_2_unit_priority(unit)
        )
        if current.units and (
            crosses_failure_boundary
            or crosses_authority_boundary
            or _estimated_prompt_tokens(candidate_prompt) > packing_token_budget
        ):
            packets.append(current)
            current = _Stage2Packet()
            current.add(unit)
        else:
            current = candidate
    if current.units:
        packets.append(current)

    max_packets = max(1, max_packets)
    prioritized_packets = [
        packet
        for _, packet in sorted(
            enumerate(packets),
            key=lambda item: (_stage_2_packet_priority(item[1]), item[0]),
        )
    ]
    selected_packets = prioritized_packets[:max_packets]
    omitted_packets = prioritized_packets[max_packets:]
    omitted_unit_count = sum(len(packet.units) for packet in omitted_packets)
    omitted_hunk_ids = {
        hunk_id
        for packet in omitted_packets
        for hunk_id in packet.review_hunk_ids
    }
    if omitted_packets and selected_packets:
        notice = (
            "[CodeCrow Stage 2 invocation ceiling reached: "
            f"omitted_packets={len(omitted_packets)}, "
            f"omitted_units={omitted_unit_count}, "
            f"omitted_hunks={len(omitted_hunk_ids)}. Core PR/diff packets and "
            "components containing exact architecture were admitted before "
            "optional enrichment. Absence is not negative evidence.]"
        )
        selected_packets[-1].add(_Stage2Unit(
            key="stage-2-invocation-ceiling-diagnostic",
            history_parts=(notice,),
        ))
        logger.warning(
            "Stage 2 invocation ceiling reached: admitted=%d omitted_packets=%d "
            "omitted_units=%d omitted_hunks=%d",
            len(selected_packets),
            len(omitted_packets),
            omitted_unit_count,
            len(omitted_hunk_ids),
        )
    packets = selected_packets

    prompts = [
        _Stage2Prompt(
            _render_stage_2_packet(common_static, packet),
            visible_hunk_ids=packet.review_hunk_ids,
            visible_evidence_ids=_finding_evidence_ids(packet.findings),
            optional_enrichment_only=packet.optional_enrichment_only,
            complete_pr_evidence_visible=False,
            omitted_packet_count=len(omitted_packets),
            omitted_unit_count=omitted_unit_count,
            omitted_hunk_count=len(omitted_hunk_ids),
        )
        for packet in packets
    ]
    for packet, prompt in zip(packets, prompts):
        if _estimated_prompt_tokens(prompt) > token_budget:
            logger.warning(
                "Indivisible Stage 2 semantic unit exceeds the input packing "
                "target without clipping: keys=%s estimated_tokens=%d "
                "target_tokens=%d",
                [unit.key for unit in packet.units],
                _estimated_prompt_tokens(prompt),
                token_budget,
            )
    logger.info(
        "Stage 2 semantic packing: complete_estimated_tokens=%d shards=%d "
        "target_tokens=%d units=%d",
        _estimated_prompt_tokens(complete_prompt),
        len(prompts),
        token_budget,
        len(expanded),
    )
    return prompts


def _parse_stage_1_findings(value: str) -> List[Dict[str, Any]]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        logger.warning("Stage 1 findings were not valid JSON; preserving as text")
        return [{"unparsed_stage_1_findings": value}]
    if not isinstance(parsed, list):
        return [{"unparsed_stage_1_findings": parsed}]
    return [
        item
        if isinstance(item, dict)
        else {"unparsed_stage_1_finding": item}
        for item in parsed
    ]


def _finding_evidence_ids(
    findings: Iterable[Mapping[str, Any]],
) -> frozenset[str]:
    return frozenset(
        evidence_id.strip()
        for finding in findings
        for evidence_refs in (finding.get("evidenceRefs"),)
        if isinstance(evidence_refs, (list, tuple, set))
        for evidence_id in evidence_refs
        if isinstance(evidence_id, str) and evidence_id.strip()
    )


def _format_complete_project_rules(rules_json: Optional[str]) -> str:
    """Preserve every project-rule field as deterministic semantic records."""
    if not rules_json:
        return ""
    try:
        parsed = json.loads(rules_json)
    except (TypeError, json.JSONDecodeError):
        logger.warning(
            "Stage 2 project rules were malformed; preserving the exact raw "
            "input as one indivisible semantic record"
        )
        parsed = {"unparsed_project_rules": rules_json}
        return "[PROJECT_RULE_INPUT_UNPARSED] " + json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    if not isinstance(parsed, list):
        logger.warning(
            "Stage 2 project rules were not a JSON list; preserving the "
            "complete value as one indivisible semantic record"
        )
        return "[PROJECT_RULE_INPUT_NON_LIST] " + json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return "\n".join(
        f"[PROJECT_RULE_{index:06d}] "
        + json.dumps(
            rule,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for index, rule in enumerate(parsed, start=1)
    )


def _project_rule_units(value: str) -> List[_Stage2Unit]:
    return [
        _Stage2Unit(
            key=f"rule:{index:06d}",
            rule_parts=(record,),
        )
        for index, record in enumerate(value.split("\n"), start=1)
        if record
    ]


def _parse_architecture_payload(value: str) -> Dict[str, Any]:
    if "\n" not in value:
        return {}
    _, serialized = value.split("\n", 1)
    try:
        parsed = json.loads(serialized)
    except (TypeError, json.JSONDecodeError):
        logger.warning(
            "Stage 2 enrichment context was malformed; continuing without "
            "optional architecture enrichment"
        )
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ledger_non_evidence_text(value: str) -> str:
    marker = "\nEVIDENCE EXCERPTS:"
    if marker in value:
        return value.split(marker, 1)[0]
    return value


def _shard_safe_ledger_header(value: str) -> str:
    """Preserve global ledger facts without labelling a partial shard complete."""
    header = _ledger_non_evidence_text(value)
    header = re.sub(
        r"(?im)^\s*Manifest status:\s*COMPLETE\s*$",
        (
            "Global ledger manifest metadata: all supplied paths were represented. "
            "CURRENT SHARD STATUS REMAINS PARTIAL."
        ),
        header,
    )
    header = re.sub(
        r"(?im)^\s*Changed-line evidence status:\s*COMPLETE\s*$",
        (
            "Global ledger metadata: all supplied changed-line evidence was available. "
            "CURRENT SHARD STATUS REMAINS PARTIAL AND CANNOT PROVE ABSENCE."
        ),
        header,
    )
    return header


def _semantic_navigation_anchor(value: str, label: str) -> str:
    """Repeat exact headings/identity lines while full large context is sharded."""
    selected: List[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if (
            stripped.startswith("#")
            or stripped.startswith("**")
            or stripped.startswith("URL:")
            or any(
                marker in stripped
                for marker in (
                    "Type:",
                    "Status:",
                    "Priority:",
                    "Assignee:",
                    "Reporter:",
                )
            )
        ):
            if line not in selected:
                selected.append(line)
    rendered = "\n".join(selected)
    return (
        f"Additional {label} context is carried by bounded semantic shards "
        "when admitted. Invocation-coverage diagnostics report omitted packets. "
        "The exact navigation anchors below apply to every admitted shard; "
        f"absence from a local shard is not negative evidence.\n{rendered}"
    ).rstrip()


def _semantic_text_fragments(value: str, max_bytes: int) -> List[str]:
    """Split at semantic boundaries while preserving every UTF-8 byte."""
    if not value:
        return []
    if len(value.encode("utf-8")) <= max_bytes:
        return [value]

    paragraphs = value.splitlines(keepends=True)
    if not paragraphs:
        return [value]
    fragments: List[str] = []
    current = ""
    for paragraph in paragraphs:
        paragraph_bytes = len(paragraph.encode("utf-8"))
        if (
            current
            and len(current.encode("utf-8")) + paragraph_bytes > max_bytes
        ):
            fragments.append(current)
            current = ""
        if paragraph_bytes > max_bytes:
            # Split prose and ordinary source at whitespace boundaries. A
            # truly indivisible minified line remains whole and is surfaced as
            # above-target rather than cut at an arbitrary character offset.
            if current:
                fragments.append(current)
                current = ""
            atoms = re.findall(r"\S+\s*|\s+", paragraph)
            if len(atoms) <= 1:
                fragments.append(paragraph)
                continue
            line_fragment = ""
            for atom in atoms:
                atom_bytes = len(atom.encode("utf-8"))
                if (
                    line_fragment
                    and len(line_fragment.encode("utf-8")) + atom_bytes > max_bytes
                ):
                    fragments.append(line_fragment)
                    line_fragment = ""
                if atom_bytes > max_bytes:
                    if line_fragment:
                        fragments.append(line_fragment)
                        line_fragment = ""
                    if atom.isspace():
                        whitespace_fragment = ""
                        whitespace_bytes = 0
                        for character in atom:
                            character_bytes = len(character.encode("utf-8"))
                            if (
                                whitespace_fragment
                                and whitespace_bytes + character_bytes > max_bytes
                            ):
                                fragments.append(whitespace_fragment)
                                whitespace_fragment = ""
                                whitespace_bytes = 0
                            whitespace_fragment += character
                            whitespace_bytes += character_bytes
                        if whitespace_fragment:
                            fragments.append(whitespace_fragment)
                    else:
                        fragments.append(atom)
                    continue
                line_fragment += atom
            if line_fragment:
                fragments.append(line_fragment)
            continue
        current += paragraph
    if current:
        fragments.append(current)
    return fragments


def _text_units(
    key_prefix: str,
    value: str,
    field_name: str,
    token_budget: int,
) -> List[_Stage2Unit]:
    # Reserve roughly half of a prompt for the invariant review contract and
    # neighboring semantic records. This changes packet placement only; it
    # never truncates a fragment.
    fragment_chars = max(4_000, token_budget * 2)
    result: List[_Stage2Unit] = []
    for index, fragment in enumerate(
        _semantic_text_fragments(value, fragment_chars),
        start=1,
    ):
        kwargs = {field_name: (fragment,)}
        result.append(
            _Stage2Unit(key=f"{key_prefix}:{index:06d}", **kwargs)
        )
    return result


class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}

    def add(self, path: str) -> None:
        if path:
            self.parent.setdefault(path, path)

    def find(self, path: str) -> str:
        self.add(path)
        parent = self.parent[path]
        if parent != path:
            self.parent[path] = self.find(parent)
        return self.parent[path]

    def union(self, left: str, right: str) -> None:
        if not left or not right:
            return
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        self.parent[second] = first


def _finding_paths(finding: Mapping[str, Any]) -> tuple[str, ...]:
    candidates: List[Any] = [
        finding.get("file"),
        finding.get("primary_file"),
    ]
    affected = finding.get("affected_files")
    if isinstance(affected, (list, tuple)):
        candidates.extend(affected)
    return tuple(sorted({
        item
        for item in candidates
        if isinstance(item, str) and item
    }))


def _build_component_units(
    *,
    findings: Sequence[Dict[str, Any]],
    architecture_payload: Mapping[str, Any],
    evidence_ledger: PrEvidenceLedger,
) -> List[_Stage2Unit]:
    relationships = [
        item
        for item in architecture_payload.get("relationships", [])
        if isinstance(item, dict)
    ]
    metadata = [
        item
        for item in architecture_payload.get("file_metadata", [])
        if isinstance(item, dict)
    ]
    path_table = architecture_payload.get("path_table", {})
    if not isinstance(path_table, dict):
        path_table = {}

    union_find = _UnionFind()
    relationship_paths: Dict[int, tuple[str, ...]] = {}
    for index, relationship in enumerate(relationships):
        source = path_table.get(relationship.get("source"), relationship.get("source"))
        target = path_table.get(relationship.get("target"), relationship.get("target"))
        paths = tuple(
            path for path in (source, target) if isinstance(path, str) and path
        )
        relationship_paths[index] = paths
        for path in paths:
            union_find.add(path)
        if len(paths) == 2:
            union_find.union(paths[0], paths[1])

    metadata_paths: Dict[int, tuple[str, ...]] = {}
    for index, item in enumerate(metadata):
        path = path_table.get(item.get("path"), item.get("path"))
        paths = (path,) if isinstance(path, str) and path else ()
        metadata_paths[index] = paths
        for candidate in paths:
            union_find.add(candidate)

    finding_paths: Dict[int, tuple[str, ...]] = {}
    for index, finding in enumerate(findings):
        paths = _finding_paths(finding)
        finding_paths[index] = paths
        for path in paths:
            union_find.add(path)
        if paths:
            for path in paths[1:]:
                union_find.union(paths[0], path)

    evidence_records = sorted(
        evidence_ledger.evidence_by_ref.values(),
        key=lambda evidence: evidence.ref,
    )
    for evidence in evidence_records:
        union_find.add(evidence.path)

    buckets: Dict[str, _Stage2Packet] = {}

    def bucket_for(paths: tuple[str, ...], fallback: str) -> _Stage2Packet:
        root = union_find.find(paths[0]) if paths else fallback
        return buckets.setdefault(root, _Stage2Packet())

    for index, relationship in enumerate(relationships):
        paths = relationship_paths[index]
        bucket_for(paths, f"relationship:{index:06d}").add(_Stage2Unit(
            key=f"relationship:{index:06d}",
            paths=paths,
            relationships=(relationship,),
            optional_enrichment_only=True,
        ))
    for index, item in enumerate(metadata):
        paths = metadata_paths[index]
        bucket_for(paths, f"metadata:{index:06d}").add(_Stage2Unit(
            key=f"metadata:{index:06d}",
            paths=paths,
            metadata=(item,),
            optional_enrichment_only=True,
        ))
    for index, finding in enumerate(findings):
        paths = finding_paths[index]
        finding_line = finding.get("line")
        finding_hunk_ids = frozenset(
            evidence.hunk_id
            for evidence in evidence_records
            if evidence.scope == (
                "delta" if evidence_ledger.incremental else "full_pr"
            )
            and evidence.path in paths
            and isinstance(finding_line, int)
            and evidence.line_start <= finding_line <= evidence.line_end
        )
        bucket_for(paths, f"finding:{index:06d}").add(_Stage2Unit(
            key=f"finding:{index:06d}",
            paths=paths,
            findings=(finding,),
            review_hunk_ids=finding_hunk_ids,
        ))
    for evidence in evidence_records:
        field_name = (
            "delta_parts" if evidence.scope == "delta" else "full_pr_parts"
        )
        block = f"[{evidence.ref}] {evidence.path}\n{evidence.excerpt}"
        review_hunk_ids = (
            frozenset({evidence.hunk_id})
            if evidence.scope == (
                "delta" if evidence_ledger.incremental else "full_pr"
            )
            else frozenset()
        )
        bucket_for((evidence.path,), f"evidence:{evidence.ref}").add(
            _Stage2Unit(
                key=f"evidence:{evidence.ref}",
                paths=(evidence.path,),
                review_hunk_ids=review_hunk_ids,
                **{field_name: (block,)},
            )
        )

    component_units: List[_Stage2Unit] = []
    # Dict insertion order follows provider/source record order. Lexical sorting
    # would put component_10 before component_2 and distort admission priority.
    for root in buckets:
        packet = buckets[root]
        component_units.append(_Stage2Unit(
            key=f"component:{root}",
            paths=tuple(sorted({path for unit in packet.units for path in unit.paths})),
            findings=tuple(packet.findings),
            relationships=tuple(packet.relationships),
            metadata=tuple(packet.metadata),
            full_pr_parts=tuple(packet.full_pr_parts),
            delta_parts=tuple(packet.delta_parts),
            children=tuple(packet.units),
            review_hunk_ids=frozenset(packet.review_hunk_ids),
            optional_enrichment_only=packet.optional_enrichment_only,
        ))
    return component_units


def _unit_dependency_anchors(unit: _Stage2Unit) -> List[_DependencyAnchor]:
    anchors: List[_DependencyAnchor] = []
    for relationship in unit.relationships:
        anchors.append(_DependencyAnchor(
            paths=unit.paths,
            payload={"kind": "relationship", **relationship},
        ))
    for item in unit.metadata:
        anchors.append(_DependencyAnchor(
            paths=unit.paths,
            payload={
                "kind": "metadata",
                "path": item.get("path"),
                "available_fields": sorted(item),
            },
        ))
    for finding in unit.findings:
        payload = {
            "kind": "stage_1_finding",
            **{
                key: finding.get(key)
                for key in ("id", "file", "line", "severity", "title")
                if finding.get(key) not in (None, "")
            },
        }
        anchors.append(_DependencyAnchor(paths=unit.paths, payload=payload))
    for field_name in ("full_pr_parts", "delta_parts"):
        for value in getattr(unit, field_name):
            header = value.partition("\n")[0]
            anchors.append(_DependencyAnchor(
                paths=unit.paths,
                payload={"kind": "pr_evidence", "header": header},
            ))
    return anchors


def _dependency_anchors_for_child(
    child: _Stage2Unit,
    siblings: Sequence[_Stage2Unit],
) -> tuple[_DependencyAnchor, ...]:
    child_anchor_keys = {
        json.dumps(
            anchor.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for anchor in _unit_dependency_anchors(child)
    }
    child_paths = set(child.paths)
    bridge_paths = set(child_paths)
    sibling_anchors: List[_DependencyAnchor] = []
    for sibling in siblings:
        anchors = _unit_dependency_anchors(sibling)
        sibling_anchors.extend(anchors)
        if sibling.relationships and child_paths.intersection(sibling.paths):
            bridge_paths.update(sibling.paths)

    selected: Dict[str, _DependencyAnchor] = {}
    for anchor in sibling_anchors:
        key = json.dumps(
            anchor.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if key in child_anchor_keys:
            continue
        if bridge_paths and not bridge_paths.intersection(anchor.paths):
            continue
        selected.setdefault(key, anchor)
    return tuple(selected[key] for key in sorted(selected))


def _explode_unit(unit: _Stage2Unit) -> List[_Stage2Unit]:
    """Break a component into records that retain compact dependency anchors."""
    if unit.children:
        return [
            replace(
                child,
                dependency_anchors=_dependency_anchors_for_child(
                    child,
                    unit.children,
                ),
            )
            for child in unit.children
        ]

    result: List[_Stage2Unit] = []
    for index, relationship in enumerate(unit.relationships):
        result.append(_Stage2Unit(
            key=f"{unit.key}:relationship:{index:06d}",
            paths=unit.paths,
            relationships=(relationship,),
            dependency_anchors=unit.dependency_anchors,
            review_hunk_ids=unit.review_hunk_ids,
            optional_enrichment_only=unit.optional_enrichment_only,
        ))
    for index, item in enumerate(unit.metadata):
        result.append(_Stage2Unit(
            key=f"{unit.key}:metadata:{index:06d}",
            paths=unit.paths,
            metadata=(item,),
            dependency_anchors=unit.dependency_anchors,
            review_hunk_ids=unit.review_hunk_ids,
            optional_enrichment_only=unit.optional_enrichment_only,
        ))
    for index, finding in enumerate(unit.findings):
        result.append(_Stage2Unit(
            key=f"{unit.key}:finding:{index:06d}",
            paths=unit.paths,
            findings=(finding,),
            dependency_anchors=unit.dependency_anchors,
            review_hunk_ids=unit.review_hunk_ids,
            optional_enrichment_only=unit.optional_enrichment_only,
        ))
    for field_name in (
        "full_pr_parts",
        "delta_parts",
        "task_parts",
        "history_parts",
        "concerns",
        "rule_parts",
    ):
        for index, value in enumerate(getattr(unit, field_name)):
            result.append(_Stage2Unit(
                key=f"{unit.key}:{field_name}:{index:06d}",
                paths=unit.paths,
                dependency_anchors=unit.dependency_anchors,
                review_hunk_ids=unit.review_hunk_ids,
                optional_enrichment_only=unit.optional_enrichment_only,
                **{field_name: (value,)},
            ))
    return result or [unit]


def _split_metadata_record(unit: _Stage2Unit) -> List[_Stage2Unit]:
    """Split large list-valued metadata without multiplying list dimensions."""
    if len(unit.metadata) != 1:
        return [unit]
    item = unit.metadata[0]
    sequence_keys = [
        key
        for key, value in item.items()
        if isinstance(value, list) and value
    ]
    if not sequence_keys:
        return [unit]

    base = {
        key: value
        for key, value in item.items()
        if key not in sequence_keys
    }
    fragments: List[_Stage2Unit] = []
    if len(base) > 1:
        fragments.append(_Stage2Unit(
            key=f"{unit.key}:metadata-base",
            paths=unit.paths,
            metadata=(base,),
            dependency_anchors=unit.dependency_anchors,
            review_hunk_ids=unit.review_hunk_ids,
            optional_enrichment_only=unit.optional_enrichment_only,
        ))
    for key in sequence_keys:
        for index, value in enumerate(item[key]):
            fragments.append(_Stage2Unit(
                key=f"{unit.key}:{key}:{index:06d}",
                paths=unit.paths,
                metadata=({"path": item.get("path"), key: [value]},),
                dependency_anchors=unit.dependency_anchors,
                review_hunk_ids=unit.review_hunk_ids,
                optional_enrichment_only=unit.optional_enrichment_only,
            ))
    return fragments or [unit]


def _split_evidence_record(
    unit: _Stage2Unit,
    field_name: str,
    available_bytes: int,
) -> List[_Stage2Unit]:
    values = getattr(unit, field_name)
    if len(values) != 1:
        return [unit]
    value = values[0]
    header, separator, body = value.partition("\n")
    if not separator:
        return [unit]
    body_fragments = _semantic_text_fragments(body, available_bytes)
    if len(body_fragments) <= 1:
        return [unit]
    total = len(body_fragments)
    return [
        _Stage2Unit(
            key=f"{unit.key}:part:{index:06d}",
            paths=unit.paths,
            dependency_anchors=unit.dependency_anchors,
            review_hunk_ids=unit.review_hunk_ids,
            optional_enrichment_only=unit.optional_enrichment_only,
            **{
                field_name: (
                    f"{header} (continuation {index}/{total})\n{fragment}",
                )
            },
        )
        for index, fragment in enumerate(body_fragments, start=1)
    ]


def _expand_oversized_unit(
    unit: _Stage2Unit,
    *,
    static: _Stage2PromptStatic,
    token_budget: int,
) -> List[_Stage2Unit]:
    probe = _Stage2Packet()
    probe.add(unit)
    if _estimated_prompt_tokens(_render_stage_2_packet(static, probe)) <= token_budget:
        return [unit]

    atoms = _explode_unit(unit)
    if len(atoms) > 1:
        result: List[_Stage2Unit] = []
        for atom in atoms:
            result.extend(
                _expand_oversized_unit(
                    atom,
                    static=static,
                    token_budget=token_budget,
                )
            )
        return result


    metadata_fragments = _split_metadata_record(unit)
    if len(metadata_fragments) > 1:
        result: List[_Stage2Unit] = []
        for fragment in metadata_fragments:
            result.extend(
                _expand_oversized_unit(
                    fragment,
                    static=static,
                    token_budget=token_budget,
                )
            )
        return result

    # Text records remain losslessly splittable at line/whitespace boundaries.
    for field_name in (
        "full_pr_parts",
        "delta_parts",
        "task_parts",
        "history_parts",
    ):
        values = getattr(unit, field_name)
        if len(values) != 1:
            continue
        base_packet = _Stage2Packet()
        base_packet.add(replace(unit, **{field_name: ()}))
        base_tokens = _estimated_prompt_tokens(
            _render_stage_2_packet(static, base_packet)
        )
        available_bytes = max(
            1_000,
            (token_budget - base_tokens - 500) * 3,
        )
        if re.match(r"^\[(?:PRF|DELTA)\d+\] ", values[0]):
            evidence_fragments = _split_evidence_record(
                unit,
                field_name,
                available_bytes,
            )
            if len(evidence_fragments) > 1:
                result: List[_Stage2Unit] = []
                for fragment in evidence_fragments:
                    result.extend(_expand_oversized_unit(
                        fragment,
                        static=static,
                        token_budget=token_budget,
                    ))
                return result
        fragments = _semantic_text_fragments(values[0], available_bytes)
        if len(fragments) <= 1:
            continue
        split_units = [
            _Stage2Unit(
                key=f"{unit.key}:part:{index:06d}",
                paths=unit.paths,
                dependency_anchors=unit.dependency_anchors,
                review_hunk_ids=unit.review_hunk_ids,
                optional_enrichment_only=unit.optional_enrichment_only,
                **{field_name: (fragment,)},
            )
            for index, fragment in enumerate(fragments, start=1)
        ]
        result: List[_Stage2Unit] = []
        for fragment in split_units:
            result.extend(_expand_oversized_unit(
                fragment,
                static=static,
                token_budget=token_budget,
            ))
        return result
    return [unit]


def _architecture_payload(
    relationships: List[Dict[str, Any]],
    metadata: List[Dict[str, Any]],
    path_table: Dict[str, str],
) -> Dict[str, Any]:
    metadata_detail_complete = not any(
        any(str(key).endswith("_omitted") for key in item)
        for item in metadata
    )
    relationship_types = Counter(
        str(item.get("type", "UNKNOWN"))
        for item in relationships
    )
    return {
        "inventory": {
            "relationship_count": len(relationships),
            "relationship_types": dict(sorted(relationship_types.items())),
            "metadata_file_count": len(metadata),
            # The record inventory is complete at this stage, but individual
            # metadata lists are deliberately bounded.  Do not let the model
            # interpret a compacted list as proof that additional values are
            # absent.
            "complete": metadata_detail_complete,
            "record_inventory_complete": True,
            "metadata_detail_complete": metadata_detail_complete,
            "path_reference_semantics": (
                "source, target, and metadata path values reference path_table."
            ),
        },
        "path_table": path_table,
        "relationships": relationships,
        "file_metadata": metadata,
    }


def _render_stage_2_packet(
    static: _Stage2PromptStatic,
    packet: _Stage2Packet,
) -> str:
    findings = (
        json.dumps(packet.findings, ensure_ascii=False, separators=(",", ":"))
        if packet.findings
        else "[]"
    )
    findings_context = f"{_SEMANTIC_SHARD_NOTICE}\n{findings}"

    anchors_by_key: Dict[str, Dict[str, Any]] = {}
    for anchor in packet.dependency_anchors:
        key = json.dumps(
            anchor.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        anchors_by_key.setdefault(key, anchor.payload)
    dependency_anchors = [
        anchors_by_key[key]
        for key in sorted(anchors_by_key)
    ]

    referenced_paths = {
        str(item.get(key))
        for item in packet.relationships
        for key in ("source", "target")
        if item.get(key) is not None
    } | {
        str(item.get("path"))
        for item in packet.metadata
        if item.get("path") is not None
    } | {
        str(anchor.get(key))
        for anchor in dependency_anchors
        for key in ("source", "target", "path")
        if anchor.get(key) is not None
    }
    shard_path_table = {
        reference: path
        for reference, path in static.architecture_path_table.items()
        if reference in referenced_paths
    }
    architecture_payload = _architecture_payload(
        packet.relationships,
        packet.metadata,
        shard_path_table,
    )
    architecture_payload["inventory"]["complete"] = False
    architecture_payload["inventory"]["semantic_shard_complete"] = True
    architecture_context = (
        "Structured enrichment context (semantic shard JSON; absence is not "
        "negative evidence):\n"
        + json.dumps(
            architecture_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    if dependency_anchors:
        architecture_context += (
            "\nDependency navigation anchors (repeated compact facts; these "
            "locate related full records in other shards and are not defect "
            "proof by themselves):\n"
            + json.dumps(
                dependency_anchors,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    task_context = (
        "\n".join(packet.task_parts)
        if packet.task_parts
        else static.common_task_context
    )
    history_context = (
        "\n".join(packet.history_parts)
        if packet.history_parts
        else static.common_history_context
    )
    concerns = [*static.common_concerns, *packet.concerns]
    project_rules = "\n".join(
        part
        for part in (static.common_project_rules, *packet.rule_parts)
        if part
    )
    full_pr_context = "\n\n".join(
        (
            _SEMANTIC_SHARD_NOTICE,
            static.full_pr_status,
            *(packet.full_pr_parts or ["No full-PR evidence record is assigned to this shard."]),
        )
    )
    delta_context = "\n\n".join(
        (
            _SEMANTIC_SHARD_NOTICE,
            static.delta_status,
            *(packet.delta_parts or ["No delta evidence record is assigned to this shard."]),
        )
    )
    return PromptBuilder.build_stage_2_cross_file_prompt(
        repo_slug=static.repo_slug,
        pr_title=static.pr_title,
        commit_hash=static.commit_hash,
        stage_1_findings_json=findings_context,
        architecture_context=architecture_context,
        migrations=static.migrations,
        cross_file_concerns=concerns,
        project_rules=project_rules,
        task_context=task_context,
        task_history_context=history_context,
        pr_change_summary=full_pr_context,
        incremental_delta_summary=delta_context,
    )
