import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.orchestrator import branch_reconciliation_packing as packing


def _request(max_allowed_tokens=30_000):
    return SimpleNamespace(maxAllowedTokens=max_allowed_tokens)


def _metadata(issue):
    return {
        "branch": "feature",
        "commitHash": "commit-abc",
        "previousCodeAnalysisIssues": [issue],
    }


def _json_section(prompt, heading):
    decoder = json.JSONDecoder()
    payload, _ = decoder.raw_decode(prompt.split(heading + "\n", 1)[1])
    return payload


@pytest.mark.asyncio(loop_scope="function")
async def test_complete_rendered_prompt_uses_one_call_when_it_fits():
    issue = {
        "id": "old-1",
        "file": "src/a.py",
        "title": "Complete issue title",
        "reason": "COMPLETE_ISSUE_REASON",
        "suggestedFixDescription": "Use the corrected call.",
        "evidenceRefs": ["RAG-1"],
        "claimKind": "python-call",
    }
    executor = AsyncMock(return_value={
        "issues": [{
            "issueId": "old-1",
            "isResolved": True,
            "resolutionReason": "Corrected call is present.",
        }],
        "comment": "resolved",
    })

    result = await packing.execute_packed_branch_reconciliation(
        llm=object(),
        request=_request(200_000),
        pr_metadata=_metadata(issue),
        file_contents={"src/a.py": "corrected_call()\n"},
        raw_diff=(
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n+++ b/src/a.py\n"
            "@@ -1 +1 @@\n-old_call()\n+corrected_call()\n"
        ),
        direct_executor=executor,
    )

    executor.assert_awaited_once()
    prompt = executor.await_args.args[1]
    assert "COMPLETE_ISSUE_REASON" in prompt
    assert '"evidenceRefs":["RAG-1"]' in prompt
    assert len(result["issues"]) == 1
    provenance = result["reconciliationPromptProvenance"]
    assert len(provenance["shards"]) == 1
    assert provenance["issues"]["old-1"]["resolutionAccepted"] is True


@pytest.mark.asyncio(loop_scope="function")
async def test_huge_unicode_source_and_diff_are_invocation_bounded(
    monkeypatch,
):
    token_target = 2_500
    monkeypatch.setattr(
        packing,
        "BRANCH_RECONCILIATION_INPUT_TOKEN_TARGET",
        token_target,
    )
    source = ("🙂 source semantic unit βeta " * 1_200) + "終"
    diff_parts = [
        "diff --git a/src/huge.py b/src/huge.py\n"
        "--- a/src/huge.py\n+++ b/src/huge.py\n"
    ]
    for index in range(30):
        diff_parts.append(
            f"@@ -{index + 1},1 +{index + 1},1 @@\n"
            f"-old_{index}\n+DIFF_UNICODE_{index}_🙂\n"
        )
    raw_diff = "".join(diff_parts)
    issue = {
        "id": "huge-1",
        "file": "src/huge.py",
        "title": "Huge source issue",
        "reason": "HUGE_ISSUE_MEMBERSHIP_MARKER",
        "suggestedFixDescription": "Fix the huge source.",
    }
    prompts = []

    async def execute(_llm, prompt, _callback):
        prompts.append(prompt)
        return {"issues": [], "comment": "partial evidence"}

    result = await packing.execute_packed_branch_reconciliation(
        llm=object(),
        request=_request(),
        pr_metadata=_metadata(issue),
        file_contents={"src/huge.py": source},
        raw_diff=raw_diff,
        direct_executor=execute,
    )

    assert len(prompts) == packing.BRANCH_RECONCILIATION_MAX_SHARDS
    assert all(
        packing.estimated_branch_reconciliation_tokens(prompt)
        <= token_target
        for prompt in prompts
    )
    owned_issue_records = [
        record
        for prompt in prompts
        for record in _json_section(
            prompt,
            "COMPLETE ISSUE RECORDS ASSIGNED TO THIS SHARD (JSON):",
        )
    ]
    assert [
        record["value"]["reason"] for record in owned_issue_records
    ] == ["HUGE_ISSUE_MEMBERSHIP_MARKER"]

    source_segments = []
    diff_segments = []
    for prompt in prompts:
        for record in _json_section(
            prompt,
            "LOSSLESS CURRENT-SOURCE RECORDS ASSIGNED TO THIS SHARD (JSON):",
        ):
            value = record["value"]
            source_segments.append((
                value["sequence"]["characterStart"],
                value["text"],
            ))
        for record in _json_section(
            prompt,
            "LOSSLESS DIFF HEADER/HUNK RECORDS ASSIGNED TO THIS SHARD (JSON):",
        ):
            value = record["value"]
            diff_segments.append((
                value["sequence"]["recordIndex"],
                value["text"],
            ))

    admitted_source = "".join(text for _, text in sorted(source_segments))
    admitted_diff = "".join(text for _, text in sorted(diff_segments))
    assert admitted_source
    assert admitted_source in source
    assert len(admitted_source) < len(source)
    assert admitted_diff in raw_diff
    assert result["issues"] == []
    issue_provenance = result["reconciliationPromptProvenance"]["issues"][
        "huge-1"
    ]
    assert len(issue_provenance["requiredShardIds"]) > 1
    shard_provenance = result["reconciliationPromptProvenance"]["shards"]
    required_indices = [
        index
        for index, shard in enumerate(shard_provenance)
        if shard["shardId"] in issue_provenance["requiredShardIds"]
    ]
    assert all(
        "HUGE_ISSUE_MEMBERSHIP_MARKER" in prompts[index]
        for index in required_indices
    )
    assert all(
        set(shard["recordKeys"]).isdisjoint(shard["anchorRecordKeys"])
        for shard in shard_provenance
    )
    assert issue_provenance["resolutionAccepted"] is False
    assert issue_provenance["resolutionBlockedByPacking"] is True
    invocation_coverage = result["reconciliationPromptProvenance"][
        "invocationCoverage"
    ]
    assert invocation_coverage == {
        "coverage": "PARTIAL",
        "reason": "branch reconciliation invocation ceiling",
        "maxShards": packing.BRANCH_RECONCILIATION_MAX_SHARDS,
        "sourceShardCount": 13,
        "admittedShardCount": packing.BRANCH_RECONCILIATION_MAX_SHARDS,
        "omittedShardCount": 9,
        "omittedRecordCount": 40,
        "omittedIssueCount": 1,
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_dependency_scoped_anchor_stays_unresolved_when_shards_are_omitted(
    monkeypatch,
):
    monkeypatch.setattr(
        packing,
        "BRANCH_RECONCILIATION_INPUT_TOKEN_TARGET",
        2_500,
    )
    marker = "COMPLETE_DEPENDENCY_ISSUE_AUTHORITY"

    async def execute(_llm, prompt, _callback):
        assert marker in prompt
        return {
            "issues": [{
                "issueId": "anchor-1",
                "isResolved": True,
                "resolutionReason": "Positive fix evidence is present.",
            }],
            "comment": "resolved in this required shard",
        }

    result = await packing.execute_packed_branch_reconciliation(
        llm=object(),
        request=_request(),
        pr_metadata=_metadata({
            "id": "anchor-1",
            "file": "src/anchor.py",
            "reason": marker,
        }),
        file_contents={
            "src/anchor.py": "positive_fix_evidence 🙂 " * 2_000,
        },
        raw_diff=None,
        direct_executor=execute,
    )

    assert result["issues"] == []
    provenance = result["reconciliationPromptProvenance"]["issues"][
        "anchor-1"
    ]
    assert len(provenance["requiredShardIds"]) > 1
    assert provenance["unanimousResolutionVote"] is True
    assert provenance["resolutionBlockedByPacking"] is True
    assert provenance["resolutionAccepted"] is False
    invocation_coverage = result["reconciliationPromptProvenance"][
        "invocationCoverage"
    ]
    assert invocation_coverage["coverage"] == "PARTIAL"
    assert invocation_coverage["admittedShardCount"] == 4
    assert invocation_coverage["omittedShardCount"] == 12
    assert invocation_coverage["omittedIssueCount"] == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_partial_shard_local_absence_cannot_resolve_issue(monkeypatch):
    monkeypatch.setattr(
        packing,
        "BRANCH_RECONCILIATION_INPUT_TOKEN_TARGET",
        2_500,
    )
    issue = {
        "id": "partial-1",
        "file": "src/partial.py",
        "reason": "PARTIAL_ISSUE_RECORD",
        "suggestedFixDescription": "Apply the fix.",
    }

    async def execute(_llm, prompt, _callback):
        assigned = _json_section(
            prompt,
            "COMPLETE ISSUE RECORDS ASSIGNED TO THIS SHARD (JSON):",
        )
        return {
            "issues": ([{
                "issueId": "partial-1",
                "isResolved": True,
                "resolutionReason": "The local fragment lacks the old code.",
            }] if assigned else []),
            "comment": "local result",
        }

    result = await packing.execute_packed_branch_reconciliation(
        llm=object(),
        request=_request(),
        pr_metadata=_metadata(issue),
        file_contents={
            "src/partial.py": "partial evidence 🙂 " * 2_000,
        },
        raw_diff=None,
        direct_executor=execute,
    )

    assert result["issues"] == []
    provenance = result["reconciliationPromptProvenance"]["issues"][
        "partial-1"
    ]
    assert len(provenance["requiredShardIds"]) > 1
    assert provenance["unanimousResolutionVote"] is False
    assert provenance["resolutionAccepted"] is False


@pytest.mark.asyncio(loop_scope="function")
async def test_indivisible_diff_hunk_is_not_sent_or_sliced(monkeypatch):
    monkeypatch.setattr(
        packing,
        "BRANCH_RECONCILIATION_INPUT_TOKEN_TARGET",
        2_500,
    )
    atomic_marker = "ATOMIC_DIFF_MARKER🙂"
    raw_diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1 @@\n+"
        + atomic_marker * 4_000
        + "\n"
    )
    prompts = []

    async def execute(_llm, prompt, _callback):
        prompts.append(prompt)
        return {"issues": [], "comment": "kept unresolved"}

    result = await packing.execute_packed_branch_reconciliation(
        llm=object(),
        request=_request(),
        pr_metadata=_metadata({
            "id": "atomic-1",
            "file": "src/a.py",
            "reason": "Atomic diff claim.",
        }),
        file_contents={"src/a.py": "current()\n"},
        raw_diff=raw_diff,
        direct_executor=execute,
    )

    assert all(atomic_marker not in prompt for prompt in prompts)
    assert all(
        packing.estimated_branch_reconciliation_tokens(prompt) <= 2_500
        for prompt in prompts
    )
    assert result["issues"] == []
    provenance = result["reconciliationPromptProvenance"]
    assert any(
        key.endswith("hunk:000000")
        for key in provenance["blockedRecordKeys"]
    )
    assert provenance["issues"]["atomic-1"][
        "blockedByIndivisibleRecord"
    ] is True


def test_request_target_and_utf8_schema_estimator(monkeypatch):
    monkeypatch.setattr(
        packing,
        "BRANCH_RECONCILIATION_INPUT_TOKEN_TARGET",
        60_000,
    )
    assert packing.branch_reconciliation_input_token_target(
        _request(50_000)
    ) == 30_000
    assert packing.branch_reconciliation_input_token_target(
        _request(10_000)
    ) == 5_000
    prompt = "🙂" * 4
    expected = (
        len(prompt.encode("utf-8"))
        + len(packing._RECONCILIATION_SCHEMA_BYTES)
        + 2
    ) // 3 + packing._BRANCH_ESTIMATOR_SAFETY_TOKENS
    assert packing.estimated_branch_reconciliation_tokens(prompt) == expected


def test_legacy_mcp_reconciliation_stops_fail_open():
    result = packing.legacy_reconciliation_fail_open(
        request=_request(),
        issue_count=7,
    )
    assert result["issues"] == []
    provenance = result["reconciliationPromptProvenance"]
    assert provenance["legacyMcpStoppedFailOpen"] is True
    assert provenance["issuesRetainedUnresolved"] == 7
