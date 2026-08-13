from types import SimpleNamespace

import pytest

from model.dtos import ReviewRequestDto
from model.enrichment import FileContentDto, PrEnrichmentDataDto
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.verification_wave import (
    build_verification_packets,
    build_verification_records,
    causal_evidence_fingerprint,
    merge_exact_candidates,
    run_verification_wave,
)


def _request() -> ReviewRequestDto:
    return ReviewRequestDto(
        projectId=1,
        projectVcsRepoSlug="org/repo",
        projectVcsWorkspace="org",
        projectWorkspace="org",
        projectNamespace="org",
        targetBranchName="main",
        sourceBranchName="feature",
        currentCommitHash="abc123",
        commitHash="abc123",
        analysisType="PULL_REQUEST",
        analysisMode="FULL",
        aiProvider="test",
        aiModel="test",
        aiApiKey="test",
        changedFiles=["a.py"],
        deletedFiles=[],
        enrichmentData=PrEnrichmentDataDto(fileContents=[
            FileContentDto(path="a.py", content="value = parse(raw)\nuse(value)\n"),
            FileContentDto(path="b.py", content="def parse(raw):\n    return None\n"),
        ]),
    )


def _issue(title: str = "Null reaches use") -> CodeReviewIssue:
    issue = CodeReviewIssue(
        severity="MEDIUM",
        category="BUG_RISK",
        file="a.py",
        line=2,
        title=title,
        reason="parse can return null and use dereferences it",
        suggestedFixDescription="Handle the null result.",
        codeSnippet="use(value)",
        relatedLocations=["b.py:1"],
    )
    # The production schema adds these as internal fields.  model_copy keeps the
    # test compatible while the schema migration lands in the same change set.
    for name, value in (
        ("triggerCondition", "parse returns null"),
        ("causalPath", "parse -> value -> use"),
        ("observableImpact", "use raises"),
    ):
        object.__setattr__(issue, name, value)
    return issue


def test_exact_fingerprint_merges_only_complete_equal_causal_evidence():
    first = _issue()
    second = _issue("Same root, other wording")
    assert causal_evidence_fingerprint(first) == causal_evidence_fingerprint(second)
    assert merge_exact_candidates([first, second]) == [first]


def test_packet_contains_candidate_windows_not_whole_files():
    records, missing = build_verification_records([_issue()], _request())
    assert missing == []
    assert len(records) == 1
    assert records[0].payload["currentSource"]["source"].startswith("1: value")
    packets, overflow = build_verification_packets(records)
    assert len(packets) == 1
    assert overflow == []


@pytest.mark.asyncio
async def test_wave_confirms_rejects_and_never_uses_tool_loop():
    issues = [_issue("keep"), _issue("drop")]
    # Make them distinct exact fingerprints.
    object.__setattr__(issues[1], "observableImpact", "different impact")

    class Llm:
        calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            assert [message["role"] for message in messages] == ["system", "user"]
            return SimpleNamespace(content="""{
              "verdicts": [
                {"verificationId":"issue_0","verdict":"CONFIRMED"},
                {"verificationId":"issue_1","verdict":"REJECTED"}
              ]
            }""")

    llm = Llm()
    result = await run_verification_wave(llm, issues, _request())
    assert result.confirmed == (issues[0],)
    assert result.rejected_count == 1
    assert result.incomplete_count == 0
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_model_duplicate_hint_cannot_delete_distinct_real_issue():
    issues = [_issue("first"), _issue("second")]
    object.__setattr__(issues[1], "observableImpact", "another concrete impact")

    class Llm:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content="""{
              "verdicts": [
                {"verificationId":"issue_0","verdict":"CONFIRMED"},
                {"verificationId":"issue_1","verdict":"CONFIRMED","duplicateOf":"issue_0"}
              ]
            }""")

    result = await run_verification_wave(Llm(), issues, _request())

    assert result.confirmed == tuple(issues)


@pytest.mark.asyncio
async def test_malformed_output_is_incomplete_without_repair_call():
    class Llm:
        calls = 0

        async def ainvoke(self, _):
            self.calls += 1
            return SimpleNamespace(content="not json")

    llm = Llm()
    result = await run_verification_wave(llm, [_issue()], _request())
    assert result.confirmed == ()
    assert result.incomplete_count == 1
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_one_transport_retry_only():
    class Llm:
        calls = 0

        async def ainvoke(self, _):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("temporary")
            return SimpleNamespace(content=(
                '{"verdicts":[{"verificationId":"issue_0",'
                '"verdict":"CONFIRMED"}]}'
            ))

    llm = Llm()
    result = await run_verification_wave(llm, [_issue()], _request())
    assert len(result.confirmed) == 1
    assert llm.calls == 2
