"""Contract tests for the extracted Stage 2 semantic packet boundary."""

from dataclasses import replace
import json

from service.review.orchestrator import stage_2_cross_file
from service.review.orchestrator import stage_2_semantic_packets
from service.review.orchestrator.stage_2_semantic_packets import (
    Stage2SemanticPacketInput,
    Stage2Prompt,
    build_stage_2_prompts,
)
from service.review.pr_evidence import PrEvidenceLedger


def _empty_ledger() -> PrEvidenceLedger:
    return PrEvidenceLedger(
        full_pr_context="FULL PR STATE LEDGER\nManifest status: COMPLETE",
        incremental_delta_context=(
            "CURRENT INCREMENTAL DELTA\nManifest status: COMPLETE"
        ),
        manifest_complete=True,
        full_evidence_complete=True,
        incremental=False,
        evidence_by_ref={},
        delta_removal_refs=frozenset(),
        delta_hunk_ids=frozenset(),
        task_terms=(),
        task_relevant_paths=(),
    )


def _packet_input() -> Stage2SemanticPacketInput:
    return Stage2SemanticPacketInput(
        repo_slug="owner/repository",
        pr_title="Extract packet construction",
        commit_hash="abc123",
        stage_1_findings_json="[]",
        architecture_context="No architecture context available.",
        migrations="No migration pre-classification.",
        cross_file_concerns=(),
        project_rules="",
        task_context="No task context available.",
        task_history_context="No prior task history available.",
        evidence_ledger=_empty_ledger(),
    )


def test_typed_packet_builder_matches_legacy_field_adapter():
    packet_input = _packet_input()

    typed = build_stage_2_prompts(packet_input)
    legacy = stage_2_cross_file._build_stage_2_prompts(
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

    assert [str(prompt) for prompt in typed] == [str(prompt) for prompt in legacy]
    assert isinstance(typed[0], Stage2Prompt)
    assert typed[0].visible_hunk_ids == legacy[0].visible_hunk_ids
    assert typed[0].complete_pr_evidence_visible is True


def test_stage_2_facade_preserves_existing_packet_imports():
    assert (
        stage_2_cross_file._Stage2Prompt
        is stage_2_semantic_packets._Stage2Prompt
    )
    assert (
        stage_2_cross_file._estimated_prompt_tokens
        is stage_2_semantic_packets._estimated_prompt_tokens
    )
    assert callable(stage_2_cross_file._build_stage_2_prompts)


def test_complete_prompt_records_inherited_stage_1_evidence_ids():
    packet_input = replace(
        _packet_input(),
        stage_1_findings_json=json.dumps([{
            "id": "stage-1-issue",
            "file": "src/a.py",
            "evidenceRefs": ["RAG-search-a", "RAG-search-b"],
        }]),
    )

    prompts = build_stage_2_prompts(packet_input)

    assert len(prompts) == 1
    assert prompts[0].visible_evidence_ids == {
        "RAG-search-a",
        "RAG-search-b",
    }


def test_stage_2_example_does_not_invent_optional_provenance():
    prompt = str(build_stage_2_prompts(_packet_input())[0])

    assert '"evidenceRefs": []' in prompt
    assert '"claimKind": ""' in prompt
    assert '"findingScope": "CONCRETE_DEFECT"' in prompt
    assert '"coverageEvidenceRefs": []' in prompt
    assert "exact repository-context Evidence ID copied" not in prompt
    assert "PRF001 or DELTA001 from the PR evidence ledger" not in prompt
