"""Publication/partition regressions, not an assessment of live model quality."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.issue_reconciliation import reconcile_issues


REPORTED_PAIRS = json.loads(
    (Path(__file__).parent / "fixtures/review_reconciliation/reported_duplicates.json").read_text()
)


def request():
    return SimpleNamespace(aiProvider="openai", pullRequestId="fixture")


def model(*groups):
    return SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps({"groups": groups}))))


def issue(title="Cancellation aborts", **values):
    return {"file": "reminders.ts", "line": 57, "title": title,
            "reason": "A rejected cancellation exits the loop before later reminders are cancelled.",
            "suggestedFixDescription": "Handle failures per reminder.", **values}


def group(*members, representative=None, rationale="The same rejected request aborts the same cancellation loop."):
    return {"memberIds": list(members), "representativeId": representative or members[0], "rationale": rationale}


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", REPORTED_PAIRS, ids=lambda value: value["name"])
async def test_reported_original_plus_verifier_restatement_publishes_one_existing_record(fixture):
    # The partition is scripted: this checks the host's final publication
    # behavior against the reported records, not an LLM's semantic accuracy.
    issues = fixture["issues"]
    llm = model(fixture["expectedGroup"])
    result = await reconcile_issues(llm, request(), issues)
    assert result.issues == [issues[1]]
    assert result.issues[0] is issues[1]
    assert not result.diagnostics
    assert result.groups == [fixture["expectedGroup"]]
    llm.ainvoke.assert_awaited_once()
    payload = json.loads(llm.ainvoke.call_args.args[0][1][1])
    assert [record["reason"] for record in payload["issues"]] == [record["reason"] for record in issues]


@pytest.mark.asyncio
async def test_independent_defects_at_same_anchor_remain_independent():
    issues = [issue(), issue("Tenant isolation omitted", reason="The cancellation query has no workspace predicate.",
                             suggestedFixDescription="Constrain the query to the requesting workspace.")]
    result = await reconcile_issues(model(group("issue-1"), group("issue-2")), request(), issues)
    assert result.issues == issues
    assert not result.diagnostics


@pytest.mark.asyncio
async def test_cross_file_reports_of_one_contract_failure_can_share_a_representative():
    issues = [issue(file="booking.ts"), issue(file="reschedule.ts", title="Rescheduling stops after a failed cancellation")]
    result = await reconcile_issues(model(group("issue-1", "issue-2")), request(), issues)
    assert result.issues == [issues[0]]
    assert not result.diagnostics


@pytest.mark.asyncio
async def test_exact_repeated_record_needs_no_model_call():
    value = issue()
    llm = model()
    result = await reconcile_issues(llm, request(), [value, dict(value)])
    assert result.issues == [value]
    assert result.issues[0] is value
    assert not result.diagnostics
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("issues", [[], [issue()]])
async def test_zero_or_one_record_needs_no_model_call(issues):
    llm = model()
    assert (await reconcile_issues(llm, request(), issues)).issues == issues
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_call_contains_complete_issue_descriptions_without_source_tools_or_history():
    complete_reason = "The complete failure description: " + "causal detail " * 2000 + "END-OF-MECHANISM"
    issues = [issue(reason=complete_reason, codeSnippet="PRIVATE-SOURCE-BODY", evidence={"source": "PRIVATE-EVIDENCE"},
                    trigger="cancellation request rejects", failureMechanism="the shared try block aborts the loop",
                    batchIds=["batch-private"], _verificationEvidenceIds=["read-secret"]), issue("Other issue")]
    llm = model(group("issue-1"), group("issue-2"))
    await reconcile_issues(llm, request(), issues)
    messages = llm.ainvoke.call_args.args[0]
    payload = json.loads(messages[1][1])
    assert set(payload) == {"issues"}
    assert payload["issues"][0]["reason"] == complete_reason
    assert payload["issues"][0]["trigger"] == issues[0]["trigger"]
    assert payload["issues"][0]["failureMechanism"] == issues[0]["failureMechanism"]
    assert not {"tools", "max_tokens", "max_completion_tokens"} & llm.ainvoke.call_args.kwargs.keys()
    assert all(marker not in str(messages) for marker in ("PRIVATE-SOURCE-BODY", "PRIVATE-EVIDENCE", "batch-private", "read-secret"))


@pytest.mark.asyncio
async def test_omitted_record_survives_without_retry_while_valid_group_is_applied():
    issues = [issue(), issue("Expanded restatement"), issue("Different problem")]
    llm = model(group("issue-1", "issue-2", representative="issue-2"))
    result = await reconcile_issues(llm, request(), issues)
    assert result.issues == [issues[1], issues[2]]
    assert any("issue-3" in message for message in result.diagnostics)
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {"memberIds": ["issue-1", "unknown"], "representativeId": "issue-1", "rationale": "same"},
    {"memberIds": ["issue-1", "issue-2"], "representativeId": "invented", "rationale": "same"},
    {"memberIds": ["issue-1", "issue-2"], "representativeId": "issue-1", "rationale": ""},
    {"memberIds": ["issue-1", "issue-1"], "representativeId": "issue-1", "rationale": "same"},
    {"memberIds": ["issue-1", {}], "representativeId": "issue-1", "rationale": "same"},
    {"memberIds": "issue-1", "representativeId": "issue-1", "rationale": "same"},
    "not-a-group",
])
async def test_invalid_membership_never_discards_a_verified_issue(invalid):
    issues = [issue(), issue("Expanded restatement")]
    result = await reconcile_issues(model(invalid), request(), issues)
    assert result.issues == issues
    assert result.diagnostics


@pytest.mark.asyncio
async def test_overlap_preserves_all_affected_members_and_applies_independent_group():
    issues = [issue(str(index)) for index in range(1, 6)]
    llm = model(group("issue-1", "issue-2"), group("issue-2", "issue-3"), group("issue-4", "issue-5"))
    result = await reconcile_issues(llm, request(), issues)
    assert result.issues == issues[:4]
    assert len(result.groups) == 1
    assert any("overlaps" in message for message in result.diagnostics)


@pytest.mark.asyncio
async def test_malformed_competing_group_does_not_silently_claim_a_representative():
    issues = [issue(), issue("Expanded restatement")]
    llm = model(group("issue-1", "issue-2"), group("issue-2", "invented"))
    result = await reconcile_issues(llm, request(), issues)
    assert result.issues == issues
    assert result.diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ['{"groups":null}', '{"findings":[]}', 'not JSON'])
async def test_unusable_response_preserves_verified_issues(response):
    issues = [issue(), issue("Expanded restatement")]
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=response)))
    result = await reconcile_issues(llm, request(), issues)
    assert result.issues == issues
    assert result.diagnostics
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_outage_preserves_verified_issues_without_retry():
    issues = [issue(), issue("Expanded restatement")]
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("provider unavailable")))
    result = await reconcile_issues(llm, request(), issues)
    assert result.issues == issues
    assert "provider unavailable" in result.diagnostics[0]
    llm.ainvoke.assert_awaited_once()


def conflict_model(groups, conflicts):
    return SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(
        content=json.dumps({"groups": groups, "conflicts": conflicts}))))


@pytest.mark.asyncio
async def test_conflicting_nonrepresentative_preserves_whole_duplicate_group_for_source_check():
    issues = [issue("Server applies guard"), issue("Expanded guard report"),
              issue("Server skips guard"), issue("Unrelated failure"), issue("Unrelated restatement")]
    llm = conflict_model([group("issue-1", "issue-2"), group("issue-3"), group("issue-4", "issue-5")],
                        [{"memberIds": ["issue-2", "issue-3"],
                          "question": "Does the same save operation execute the registered validation guard?"}])
    result = await reconcile_issues(llm, request(), issues)
    assert result.issues == issues[:4]
    assert len(result.conflicts) == 1
    assert result.conflicts[0].issues == issues[:3]
    assert all(actual is original for actual, original in zip(result.conflicts[0].issues, issues))
    assert result.conflicts[0].question == "Does the same save operation execute the registered validation guard?"
    assert result.groups == [group("issue-4", "issue-5")]
    assert not result.diagnostics
    llm.ainvoke.assert_awaited_once()
    assert not result.conflicts[0].issues[0].get("verdict")  # Routing never adjudicates a claim.


@pytest.mark.asyncio
async def test_conflict_and_duplicate_overlaps_form_one_complete_source_scope():
    issues = [issue(f"Report {index}") for index in range(1, 7)]
    conflicts = [{"memberIds": ["issue-1", "issue-2"], "question": "Is the guard reached before saving?"},
                 {"memberIds": ["issue-2", "issue-3"], "question": "Does saving validate this record?"}]
    groups = [group("issue-1"), group("issue-2"), group("issue-3", "issue-4"),
              group("issue-4", "issue-5"), group("issue-6")]
    result = await reconcile_issues(conflict_model(groups, conflicts), request(), issues)
    assert result.issues == issues
    assert len(result.conflicts) == 1
    assert result.conflicts[0].issues == issues[:5]
    assert result.conflicts[0].question == "Does saving validate this record?\n\nIs the guard reached before saving?"
    assert result.groups == [group("issue-6")]
    assert any("overlaps" in message for message in result.diagnostics)


@pytest.mark.asyncio
async def test_independent_conflicts_remain_separate_and_deterministic():
    issues = [issue(str(index)) for index in range(1, 7)]
    groups = [group(f"issue-{index}") for index in range(1, 7)]
    conflicts = [{"memberIds": ["issue-4", "issue-3"], "question": "Second distinct contradiction?"},
                 {"memberIds": ["issue-2", "issue-1"], "question": "First contradiction?"}]
    first = await reconcile_issues(conflict_model(groups, conflicts), request(), issues)
    second = await reconcile_issues(conflict_model(list(reversed(groups)), list(reversed(conflicts))), request(), issues)
    assert first.conflicts == second.conflicts
    assert [item.issues for item in first.conflicts] == [issues[:2], issues[2:4]]
    assert first.issues == issues
    assert not first.diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {"memberIds": ["issue-2"], "question": "One record alone is not a conflict"},
    {"memberIds": ["issue-2", "issue-2"], "question": "Repeated identity"},
    {"memberIds": ["issue-2", "missing"], "question": "Unknown member"},
    {"memberIds": ["issue-2", {}], "question": "Malformed member"},
    {"memberIds": ["issue-1", "issue-2"], "question": ""},
    {"memberIds": ["issue-1", "issue-2"], "question": None},
])
async def test_invalid_conflict_preserves_affected_duplicate_members_and_valid_siblings(invalid):
    issues = [issue(str(index)) for index in range(1, 5)]
    result = await reconcile_issues(conflict_model([group("issue-1", "issue-2"), group("issue-3", "issue-4")],
                                                  [invalid]), request(), issues)
    assert result.issues == issues[:3]
    assert not result.conflicts
    assert result.groups == [group("issue-3", "issue-4")]
    assert any("invalid membership or question" in message for message in result.diagnostics)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, "not conflicts", [None], [{"question": "No identities"}]])
async def test_unidentifiable_conflict_output_preserves_all_reports_without_source_route(invalid):
    issues = [issue(), issue("Restatement")]
    result = await reconcile_issues(conflict_model([group("issue-1", "issue-2")], invalid), request(), issues)
    assert result.issues == issues
    assert not result.conflicts
    assert result.diagnostics


@pytest.mark.asyncio
async def test_valid_conflict_can_route_even_when_dedup_partition_is_missing():
    issues = [issue(), issue("Conflicting causal claim")]
    llm = conflict_model(None, [{"memberIds": ["issue-1", "issue-2"], "question": "Which guard applies to this same path?"}])
    result = await reconcile_issues(llm, request(), issues)
    assert result.issues == issues
    assert result.conflicts[0].issues == issues
    assert any("no usable partition" in message for message in result.diagnostics)
    llm.ainvoke.assert_awaited_once()


def test_exact_publication_duplicates_ignore_internal_case_and_proof_identity():
    from service.review.issue_reconciliation import unique_publication_issues
    original = issue(partId="part-1", batchIds=["first"], _verificationEvidenceIds=["read-1"])
    repeated = {**original, "partId": "other-part", "batchIds": ["second"], "_verificationEvidenceIds": ["read-2"]}
    distinct = issue(reason="An independent tenant scoping defect in the same handler")
    assert unique_publication_issues([original, repeated, distinct]) == [original, distinct]
