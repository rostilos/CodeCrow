"""Focused tests for deterministic agentic batching and exact accounting."""

from __future__ import annotations

import json
import re
import pytest

from model.dtos import ReviewRequestDto
from service.review.agentic.engine import (
    AgenticBatchResult,
    AgenticFinding,
    AgenticHistoricalResolution,
    AgenticReviewEngine,
    AgenticUnreviewableWorkItem,
    build_review_worklist,
)


RAW_DIFF = """diff --git a/src/first.py b/src/first.py
--- a/src/first.py
+++ b/src/first.py
@@ -1 +1 @@
-old_first = True
+new_first = True
diff --git a/src/second.py b/src/second.py
--- a/src/second.py
+++ b/src/second.py
@@ -10 +10 @@
-old_second = True
+new_second = True
"""
HEAD_SHA = "b" * 40


def _request() -> ReviewRequestDto:
    return ReviewRequestDto(
        projectId=1,
        projectVcsWorkspace="acme",
        projectVcsRepoSlug="repo",
        projectWorkspace="acme",
        projectNamespace="repo",
        aiProvider="OPENAI",
        aiModel="test-model",
        aiApiKey="test-key",
        reviewApproach="AGENTIC",
        rawDiff=RAW_DIFF,
        previousCommitHash="a" * 40,
        currentCommitHash=HEAD_SHA,
        agenticRepository={
            "workspaceKey": "d" * 64,
            "snapshotSha": HEAD_SHA,
            "contentDigest": "e" * 64,
            "byteLength": 100,
        },
    )


class _Response:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = []


class _BoundModel:
    def __init__(self, payload: dict):
        self.payload = payload

    async def ainvoke(self, _messages):
        return _Response(json.dumps(self.payload))


class _Model:
    def __init__(self, payload: dict):
        self.payload = payload

    def bind_tools(self, _tools):
        return _BoundModel(self.payload)


class _Gateway:
    @staticmethod
    def langchain_tool_definitions():
        return []

    async def invoke(self, _name, _arguments):
        raise AssertionError("no tool call expected")


def _engine(payload: dict | None = None) -> AgenticReviewEngine:
    request = _request()
    return AgenticReviewEngine(
        llm=_Model(payload or {}),
        gateway=_Gateway(),
        request=request,
    )


def _finding(work_item_ids: list[str], *, file: str, line: int) -> AgenticFinding:
    return AgenticFinding(
        findingType="DEFECT",
        verificationStatus="CONFIRMED",
        severity="HIGH",
        category="BUG_RISK",
        file=file,
        line=line,
        codeSnippet="new value",
        title="Incorrect new value",
        reason="The changed value breaks the required behavior.",
        suggestedFixDescription="Use the expected value.",
        workItemIds=work_item_ids,
    )


def _result(
    *,
    reviewed: list[str],
    unreviewable: list[str] | None = None,
    findings: list[AgenticFinding] | None = None,
    resolutions: list[AgenticHistoricalResolution] | None = None,
) -> AgenticBatchResult:
    return AgenticBatchResult(
        reviewedWorkItemIds=reviewed,
        unreviewableWorkItems=[
            AgenticUnreviewableWorkItem(
                workItemId=item, reason="Insufficient local context"
            )
            for item in (unreviewable or [])
        ],
        findings=findings or [],
        resolvedHistoricalIssues=resolutions or [],
    )


def test_worklist_is_deterministic_and_follows_diff_order():
    request = _request()

    first = build_review_worklist(request)
    second = build_review_worklist(request)

    assert first == second
    assert [item.path for item in first] == ["src/first.py", "src/second.py"]
    assert len({item.work_item_id for item in first}) == 2
    assert all(len(item.work_item_id) == 64 for item in first)


def test_large_hunk_is_split_without_hiding_any_diff_line():
    body = [f"+value_{index} = {'x' * 40}" for index in range(120)]
    raw_diff = (
        "diff --git a/src/large.py b/src/large.py\n"
        "--- /dev/null\n"
        "+++ b/src/large.py\n"
        f"@@ -0,0 +1,{len(body)} @@\n"
        + "\n".join(body)
        + "\n"
    )
    request = _request()
    request.rawDiff = raw_diff

    worklist = build_review_worklist(request, max_item_chars=512)

    assert len(worklist) > 1
    assert all(len(item.diff) <= 512 for item in worklist)
    observed = [
        line
        for item in worklist
        for line in item.diff.splitlines()[1:]
    ]
    assert observed == body
    assert sum(len(item.visible_lines) for item in worklist) == len(body)


def test_split_mixed_hunk_renders_exact_chunk_coordinates_and_suffix():
    body = [
        " " + "context_a = " + "a" * 70,
        "-" + "removed_a = " + "b" * 70,
        "+" + "added_a = " + "c" * 70,
        " " + "context_b = " + "d" * 70,
        "-" + "removed_b = " + "e" * 70,
        "+" + "added_b = " + "f" * 70,
        " " + "context_c = " + "g" * 70,
    ]
    old_count = sum(line.startswith((" ", "-")) for line in body)
    new_count = sum(line.startswith((" ", "+")) for line in body)
    request = _request()
    request.rawDiff = (
        "diff --git a/src/mixed.py b/src/mixed.py\n"
        "--- a/src/mixed.py\n"
        "+++ b/src/mixed.py\n"
        f"@@ -10,{old_count} +20,{new_count} @@ def calculate():\n"
        + "\n".join(body)
        + "\n"
    )

    worklist = build_review_worklist(request, max_item_chars=256)

    assert len(worklist) > 1
    assert [
        line for item in worklist for line in item.diff.splitlines()[1:]
    ] == body
    old_cursor = 10
    new_cursor = 20
    header_pattern = re.compile(
        r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@ def calculate\(\):$"
    )
    for item in worklist:
        chunk_lines = item.diff.splitlines()[1:]
        chunk_old_cursor = old_cursor
        chunk_new_cursor = new_cursor
        old_coordinates = []
        new_coordinates = []
        for line in chunk_lines:
            if line.startswith("-"):
                old_coordinates.append(old_cursor)
                old_cursor += 1
            elif line.startswith("+"):
                new_coordinates.append(new_cursor)
                new_cursor += 1
            else:
                old_coordinates.append(old_cursor)
                new_coordinates.append(new_cursor)
                old_cursor += 1
                new_cursor += 1
        expected = (
            old_coordinates[0]
            if old_coordinates
            else max(0, chunk_old_cursor - 1),
            len(old_coordinates),
            new_coordinates[0]
            if new_coordinates
            else max(0, chunk_new_cursor - 1),
            len(new_coordinates),
        )
        match = header_pattern.fullmatch(item.diff.splitlines()[0])
        assert match is not None
        assert tuple(map(int, match.groups())) == expected
        assert (
            item.old_start,
            item.old_line_count,
            item.new_start,
            item.new_line_count,
        ) == expected
        assert all(item.contains(item.path, line) for line, _ in item.visible_lines)


def test_one_oversized_diff_line_fails_closed():
    request = _request()
    request.rawDiff = (
        "diff --git a/src/large.py b/src/large.py\n"
        "--- /dev/null\n"
        "+++ b/src/large.py\n"
        "@@ -0,0 +1 @@\n+"
        + "x" * 600
        + "\n"
    )

    with pytest.raises(ValueError, match="one diff line"):
        build_review_worklist(request, max_item_chars=512)


@pytest.mark.parametrize(
    "raw_diff",
    [
        "not a unified diff",
        (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n+++ b/src/other.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        ),
        (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n+++ b/src/a.py\n"
            "@@ malformed @@\n-old\n+new\n"
        ),
        (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n+++ b/src/a.py\n"
            "@@ -1,2 +1 @@\n-old\n+new\n"
        ),
        (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n+++ b/src/a.py\n"
            "-old\n+new\n"
        ),
        "diff --git a/src/a.py b/src/a.py\n",
        (
            "diff --git a/src/a.py b/src/a.py\n"
            "index 1111111..2222222 100644\n"
        ),
    ],
)
def test_malformed_diff_never_becomes_an_empty_success(raw_diff):
    request = _request()
    request.rawDiff = raw_diff

    with pytest.raises(ValueError):
        build_review_worklist(request)


def test_deletion_only_hunk_is_explicitly_unreviewable():
    request = _request()
    request.rawDiff = (
        "diff --git a/src/old.py b/src/old.py\n"
        "--- a/src/old.py\n+++ /dev/null\n"
        "@@ -1 +0,0 @@\n-old = True\n"
    )

    engine = AgenticReviewEngine(
        llm=_Model({}), gateway=_Gateway(), request=request
    )

    assert len(engine.worklist) == 1
    assert set(engine.work_item_status.values()) == {"UNREVIEWABLE"}


@pytest.mark.asyncio
async def test_deleted_file_closes_its_historical_open_issues_without_model_review():
    request = _request()
    request.rawDiff = (
        "diff --git a/src/old.py b/src/old.py\n"
        "deleted file mode 100644\n"
        "--- a/src/old.py\n+++ /dev/null\n"
        "@@ -1 +0,0 @@\n-old = True\n"
    )
    request.deletedFiles = ["src/old.py"]
    request.previousCodeAnalysisIssues = [
        {
            "id": "12524", "status": "OPEN", "file": "src/old.py",
            "line": 1, "severity": "MEDIUM", "category": "BUG_RISK",
            "reason": "The deleted value is unsafe.",
            "suggestedFixDescription": "Remove the unsafe value.",
            "codeSnippet": "old = True",
        }
    ]
    engine = AgenticReviewEngine(
        llm=_Model({}), gateway=_Gateway(), request=request
    )

    result = await engine.review()

    assert result["comment"] == "Agentic review completed with no actionable issues."
    assert len(result["issues"]) == 1
    assert result["issues"][0]["id"] == "12524"
    assert result["issues"][0]["isResolved"] is True
    assert "file" in result["issues"][0]["resolutionReason"].lower()


def test_mixed_text_and_binary_sections_are_both_accounted_for():
    request = _request()
    request.rawDiff = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1 +1 @@\n-old = True\n+new = True\n"
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/assets/logo.png and b/assets/logo.png differ\n"
    )

    engine = AgenticReviewEngine(
        llm=_Model({}), gateway=_Gateway(), request=request
    )

    assert [item.path for item in engine.worklist] == [
        "src/app.py",
        "assets/logo.png",
    ]
    assert [item.reviewable for item in engine.worklist] == [True, False]
    assert list(engine.work_item_status.values()) == ["PENDING", "UNREVIEWABLE"]


@pytest.mark.parametrize(
    "metadata",
    [
        "old mode 100644\nnew mode 100755\n",
        "similarity index 100%\nrename from old.py\nrename to new.py\n",
        "new file mode 100644\n",
    ],
)
def test_legal_metadata_only_sections_are_explicitly_unreviewable(metadata):
    request = _request()
    request.rawDiff = "diff --git a/old.py b/new.py\n" + metadata

    worklist = build_review_worklist(request)

    assert len(worklist) == 1
    assert worklist[0].reviewable is False
    assert worklist[0].diff.startswith("diff --git ")


@pytest.mark.parametrize(
    "reported",
    [
        "empty",
        "omission",
        "duplicate",
        "overlap",
        "foreign",
    ],
)
def test_batch_partition_rejects_missing_duplicate_overlap_and_foreign_ids(reported):
    engine = _engine()
    first, second = engine.worklist

    if reported == "empty":
        result = _result(reviewed=[])
    elif reported == "omission":
        result = _result(reviewed=[first.work_item_id])
    elif reported == "duplicate":
        result = _result(
            reviewed=[first.work_item_id, first.work_item_id, second.work_item_id]
        )
    elif reported == "overlap":
        result = _result(
            reviewed=[first.work_item_id, second.work_item_id],
            unreviewable=[first.work_item_id],
        )
    else:
        result = _result(
            reviewed=[first.work_item_id, "f" * 64],
            unreviewable=[second.work_item_id],
        )

    with pytest.raises(ValueError, match="partition"):
        engine._validate_partition(engine.worklist, result)


def test_batch_partition_accepts_exact_reviewed_and_unreviewable_split():
    engine = _engine()
    first, second = engine.worklist
    result = _result(
        reviewed=[first.work_item_id],
        unreviewable=[second.work_item_id],
    )

    engine._validate_partition(engine.worklist, result)


@pytest.mark.parametrize("case", ["duplicate", "unreviewable", "foreign", "wrong_hunk"])
def test_finding_must_reference_unique_reviewed_ids_and_its_own_hunk(case):
    engine = _engine()
    first, second = engine.worklist
    reviewed = [first.work_item_id]
    unreviewable = [second.work_item_id]
    references = [first.work_item_id]
    file = first.path
    line = first.new_start

    if case == "duplicate":
        references = [first.work_item_id, first.work_item_id]
    elif case == "unreviewable":
        references = [second.work_item_id]
    elif case == "foreign":
        references = ["f" * 64]
    else:
        file = second.path
        line = second.new_start

    result = _result(
        reviewed=reviewed,
        unreviewable=unreviewable,
        findings=[_finding(references, file=file, line=line)],
    )

    with pytest.raises(ValueError, match="finding"):
        engine._validate_partition(engine.worklist, result)


@pytest.mark.asyncio
async def test_invalid_batch_fails_all_items_and_drops_findings():
    engine = _engine()
    first, _second = engine.worklist
    payload = {
        "comment": "invalid incomplete response",
        "reviewedWorkItemIds": [first.work_item_id],
        "unreviewableWorkItems": [],
        "findings": [
            _finding(
                [first.work_item_id],
                file=first.path,
                line=first.new_start,
            ).model_dump(mode="json")
        ],
    }
    engine.llm = _Model(payload)

    with pytest.raises(RuntimeError, match="failed closed"):
        await engine.review()
    assert set(engine.work_item_status.values()) == {"FAILED"}


@pytest.mark.asyncio
async def test_valid_batch_publishes_only_diff_anchored_finding():
    engine = _engine()
    first, second = engine.worklist
    payload = {
        "comment": "Both hunks were reviewed.",
        "reviewedWorkItemIds": [first.work_item_id, second.work_item_id],
        "unreviewableWorkItems": [],
        "findings": [
            _finding(
                [second.work_item_id],
                file=second.path,
                line=second.new_start,
            ).model_dump(mode="json")
        ],
    }
    engine.llm = _Model(payload)

    result = await engine.review()

    assert len(result["issues"]) == 1
    assert result["issues"][0]["file"] == "src/second.py"
    assert result["agenticReview"]["reviewedWorkItems"] == 2


@pytest.mark.asyncio
async def test_fixed_change_confirmation_is_not_published_as_agentic_issue():
    engine = _engine()
    first, second = engine.worklist
    fixed_point = _finding(
        [first.work_item_id],
        file=first.path,
        line=first.new_start,
    ).model_copy(
        update={
            "severity": "MEDIUM",
            "reason": "The current diff already fixes the reported failure.",
            "suggestedFixDescription": "No further code change is required.",
        }
    )
    payload = {
        "comment": "Both hunks were reviewed.",
        "reviewedWorkItemIds": [first.work_item_id, second.work_item_id],
        "unreviewableWorkItems": [],
        "findings": [fixed_point.model_dump(mode="json")],
    }
    engine.llm = _Model(payload)

    result = await engine.review()

    assert result["issues"] == []
    assert result["comment"] == "Agentic review completed with no actionable issues."
    assert result["agenticReview"]["reviewedWorkItems"] == 2


def test_batch_prompt_includes_only_relevant_open_history():
    request = _request()
    request.previousCodeAnalysisIssues = [
        {
            "id": "12524", "status": "OPEN", "file": "src/first.py",
            "line": 1, "severity": "MEDIUM", "category": "BUG_RISK",
            "reason": "The old value crashes.",
            "suggestedFixDescription": "Use the safe value.",
            "codeSnippet": "old_first = True",
        },
        {
            "id": "12525", "status": "RESOLVED", "file": "src/first.py",
            "line": 1, "reason": "Already closed.",
        },
        {
            "id": "12526", "status": "OPEN", "file": "src/other.py",
            "line": 1, "reason": "Unrelated file.",
        },
    ]
    engine = AgenticReviewEngine(
        llm=_Model({}), gateway=_Gateway(), request=request
    )

    prompt = json.loads(engine._batch_prompt([engine.worklist[0]]))

    assert [issue["issueId"] for issue in prompt["previousOpenIssues"]] == [
        "12524"
    ]


def test_renamed_file_exposes_old_path_history_for_resolution():
    request = _request()
    request.rawDiff = (
        "diff --git a/src/old.py b/src/new.py\n"
        "similarity index 70%\n"
        "rename from src/old.py\nrename to src/new.py\n"
        "--- a/src/old.py\n+++ b/src/new.py\n"
        "@@ -1 +1 @@\n-old = True\n+safe = True\n"
    )
    request.previousCodeAnalysisIssues = [
        {
            "id": "12524", "status": "OPEN", "file": "src/old.py",
            "line": 1, "severity": "MEDIUM", "category": "BUG_RISK",
            "reason": "The old value crashes.",
            "suggestedFixDescription": "Use the safe value.",
        }
    ]
    engine = AgenticReviewEngine(
        llm=_Model({}), gateway=_Gateway(), request=request
    )

    item = engine.worklist[0]
    prompt = json.loads(engine._batch_prompt([item]))
    resolution = AgenticHistoricalResolution(
        issueId="12524",
        resolutionReason="The unsafe value was replaced during the rename.",
        workItemIds=[item.work_item_id],
    )
    result = _result(
        reviewed=[item.work_item_id],
        resolutions=[resolution],
    )

    assert item.path == "src/new.py"
    assert item.previous_path == "src/old.py"
    assert [issue["issueId"] for issue in prompt["previousOpenIssues"]] == [
        "12524"
    ]
    engine._validate_partition([item], result)


def test_historical_resolution_must_reference_supplied_open_issue():
    engine = _engine()
    first, second = engine.worklist
    result = _result(
        reviewed=[first.work_item_id, second.work_item_id],
        resolutions=[
            AgenticHistoricalResolution(
                issueId="unknown",
                resolutionReason="The current change fixes it.",
                workItemIds=[first.work_item_id],
            )
        ],
    )

    with pytest.raises(ValueError, match="historical resolution"):
        engine._validate_partition(engine.worklist, result)


@pytest.mark.asyncio
async def test_agentic_review_returns_explicit_resolution_for_historical_open_issue():
    request = _request()
    request.previousCodeAnalysisIssues = [
        {
            "id": "12524", "status": "OPEN", "file": "src/first.py",
            "line": 1, "severity": "MEDIUM", "category": "BUG_RISK",
            "title": "Unsafe old value", "reason": "The old value crashes.",
            "suggestedFixDescription": "Use the safe value.",
            "codeSnippet": "old_first = True",
        }
    ]
    engine = AgenticReviewEngine(
        llm=_Model({}), gateway=_Gateway(), request=request
    )
    first, second = engine.worklist
    payload = {
        "comment": "The previous problem is fixed.",
        "reviewedWorkItemIds": [first.work_item_id, second.work_item_id],
        "unreviewableWorkItems": [],
        "findings": [],
        "resolvedHistoricalIssues": [
            {
                "issueId": "12524",
                "resolutionReason": "The unsafe value was replaced by the safe value.",
                "workItemIds": [first.work_item_id],
            }
        ],
    }
    engine.llm = _Model(payload)

    result = await engine.review()

    assert result["comment"] == "Agentic review completed with no actionable issues."
    assert len(result["issues"]) == 1
    assert result["issues"][0]["id"] == "12524"
    assert result["issues"][0]["isResolved"] is True
    assert result["issues"][0]["resolutionReason"] == (
        "The unsafe value was replaced by the safe value."
    )
