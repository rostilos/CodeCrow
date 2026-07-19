"""Behavioral tests for the bounded agentic PR-review loop."""

from __future__ import annotations

import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from model.dtos import ExecutionManifestV1, RagExecutionConfigV1, ReviewRequestDto
from model.enrichment import PrEnrichmentDataDto
from service.review.agentic.engine import (
    AgenticFinding,
    AgenticReviewEngine,
    build_review_worklist,
)
from service.review.agentic.tool_gateway import AgenticToolGateway
from utils.diff_processor import DiffProcessor


RAW_DIFF = """diff --git a/src/payments.py b/src/payments.py
index 1111111..2222222 100644
--- a/src/payments.py
+++ b/src/payments.py
@@ -1,2 +1,3 @@
 def charge(token):
+    debit_without_limit(token)
     return gateway.charge(token)
"""
MULTI_FILE_DIFF = """diff --git a/src/first.py b/src/first.py
index 1111111..2222222 100644
--- a/src/first.py
+++ b/src/first.py
@@ -1 +1 @@
-old_first = True
+new_first = True
diff --git a/src/second.py b/src/second.py
index 3333333..4444444 100644
--- a/src/second.py
+++ b/src/second.py
@@ -1 +1 @@
-old_second = True
+new_second = True
"""
QUOTED_RENAME_DIFF = r'''diff --git "a/old folder/na\303\257ve.py" "b/new folder/\344\275\240\345\245\275.py"
similarity index 80%
rename from "old folder/na\303\257ve.py"
rename to "new folder/\344\275\240\345\245\275.py"
--- "a/old folder/na\303\257ve.py"
+++ "b/new folder/\344\275\240\345\245\275.py"
@@ -1 +1,2 @@
-old = 1
+new = 1
+validate(new)
'''
HEAD_SHA = "b" * 40
EXECUTION_ID = "agentic-review-1"


def _request() -> ReviewRequestDto:
    return ReviewRequestDto.model_construct(
        executionManifest=SimpleNamespace(
            executionId=EXECUTION_ID,
            headSha=HEAD_SHA,
            baseSha="a" * 40,
        ),
        reviewApproach="AGENTIC",
        rawDiff=RAW_DIFF,
        prTitle="Limit payment debits",
        prDescription="Apply the account debit limit.",
        projectRules=None,
        previousCodeAnalysisIssues=[],
    )


def _bound_previous_finding_request() -> ReviewRequestDto:
    request = _request()
    request.enrichmentData = PrEnrichmentDataDto.model_validate({
        "reviewContext": {
            "schemaVersion": 1,
            "prTitle": "Limit payment debits",
            "prDescription": "Apply the account debit limit.",
            "prAuthor": "review-author",
            "taskContext": {},
            "taskHistoryContext": "",
            "projectRules": "[]",
            "sourceBranchName": "feature/payment-limit",
            "targetBranchName": "main",
            "previousFindings": [{
                "id": "previous-17",
                "type": "quality",
                "severity": "HIGH",
                "title": "Debit limit is bypassed",
                "reason": "The debit call previously bypassed the configured limit.",
                "suggestedFixDescription": "Use the limit-aware debit operation.",
                "suggestedFixDiff": None,
                "file": "src/payments.py",
                "line": 2,
                "branch": "feature/payment-limit",
                "pullRequestId": "17",
                "status": "open",
                "category": "BUG_RISK",
                "prVersion": 1,
                "resolvedDescription": None,
                "resolvedByCommit": None,
                "resolvedInAnalysisId": None,
                "codeSnippet": "debit_without_limit(token)",
            }],
        }
    })
    return request


class _Response:
    def __init__(self, *, content: str = "", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _BoundModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        return self.responses.pop(0)


class _Model:
    def __init__(self, responses):
        self.bound = _BoundModel(responses)
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self.bound


class _Gateway:
    snapshot_id = "snapshot-1"

    def __init__(self):
        self.calls = []
        self._receipts = []

    @property
    def telemetry_summary(self):
        return {"calls_used": len(self.calls), "local_calls": len(self.calls), "rag_calls": 0}

    def tool_definitions(self):
        return [
            {
                "name": "read_file",
                "description": "Read an exact source span",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            }
        ]

    async def invoke(self, name, arguments):
        self.calls.append((name, arguments))
        self._receipts.append(_proof())
        return {
            "content": "    debit_without_limit(token)",
            "proof": _proof(),
        }

    @property
    def receipts(self):
        return list(self._receipts)

    def validate_proof(self, proof, *, expected_path=None):
        return (
            proof == _proof()
            and expected_path in {None, "src/payments.py"}
        )


class _UnrelatedProofGateway(_Gateway):
    """Registers a real receipt, but for a file unrelated to the bound finding."""

    def validate_proof(self, proof, *, expected_path=None):
        registered = _proof(path="src/unrelated.py", salt="unrelated")
        return proof == registered and (
            expected_path is None or proof["path"] == expected_path
        )


class _CoverageTracker:
    def __init__(self, anchor):
        self.ledger = SimpleNamespace(anchors=[anchor])
        self.examined = []
        self.failed = []

    def mark_examined(self, anchor_ids):
        self.examined.extend(anchor_ids)

    def mark_failed(self, anchor_ids, *, reason_code):
        self.failed.extend((item, reason_code) for item in anchor_ids)


def _anchor():
    return SimpleNamespace(
        anchorId="c" * 64,
        kind="TEXT_HUNK",
        mandatory=True,
        initialState="PENDING",
        oldPath="src/payments.py",
        newPath="src/payments.py",
        oldStart=1,
        oldLineCount=2,
        newStart=1,
        newLineCount=3,
        changeStatus="MODIFY",
    )


def _proof(
    *,
    path: str = "src/payments.py",
    start_line: int = 1,
    end_line: int = 3,
    salt: str = "span",
):
    return {
        "kind": "exact_source_span_v1",
        "execution_id": EXECUTION_ID,
        "head_sha": HEAD_SHA,
        "snapshot_id": "snapshot-1",
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
        "source_digest": sha256(b"source").hexdigest(),
        "span_digest": sha256(salt.encode()).hexdigest(),
    }


def _finding(**overrides):
    finding = {
        "findingType": "DEFECT",
        "verificationStatus": "CONFIRMED",
        "severity": "HIGH",
        "category": "BUG_RISK",
        "file": "src/payments.py",
        "line": 2,
        "scope": "LINE",
        "codeSnippet": "debit_without_limit(token)",
        "title": "Debit limit is bypassed",
        "reason": "The new call debits the account without applying the required limit.",
        "suggestedFixDescription": "Call the limit-aware debit operation.",
        "workItemIds": ["c" * 64],
    }
    finding.update(overrides)
    return finding


def _final_payload(*, findings=None, reviewed=None, unreviewable=None):
    return {
        "comment": "Reviewed the changed payment hunk.",
        "reviewedWorkItemIds": reviewed if reviewed is not None else ["c" * 64],
        "unreviewableWorkItems": unreviewable or [],
        "findings": findings if findings is not None else [_finding()],
        "previousFindingDecisions": [],
    }


def _previous_payload(*, decisions):
    return {
        "comment": "",
        "reviewedWorkItemIds": [],
        "unreviewableWorkItems": [],
        "findings": [],
        "previousFindingDecisions": decisions,
    }


def test_worklist_uses_exact_coverage_anchor_identity_and_is_deterministic():
    tracker = _CoverageTracker(_anchor())
    processed = DiffProcessor().process(RAW_DIFF)

    first = build_review_worklist(_request(), processed, tracker)
    second = build_review_worklist(_request(), processed, tracker)

    assert first == second
    assert len(first) == 1
    assert first[0].work_item_id == "c" * 64
    assert first[0].path == "src/payments.py"
    assert first[0].new_start == 1
    assert "debit_without_limit" in first[0].diff


def test_worklist_follows_diff_order_instead_of_random_anchor_hash_order():
    first_anchor = SimpleNamespace(
        anchorId="f" * 64,
        kind="TEXT_HUNK",
        mandatory=True,
        initialState="PENDING",
        oldPath="src/first.py",
        newPath="src/first.py",
        oldStart=1,
        oldLineCount=1,
        newStart=1,
        newLineCount=1,
        changeStatus="MODIFY",
    )
    second_anchor = SimpleNamespace(
        anchorId="0" * 64,
        kind="TEXT_HUNK",
        mandatory=True,
        initialState="PENDING",
        oldPath="src/second.py",
        newPath="src/second.py",
        oldStart=1,
        oldLineCount=1,
        newStart=1,
        newLineCount=1,
        changeStatus="MODIFY",
    )
    tracker = _CoverageTracker(second_anchor)
    tracker.ledger.anchors = [second_anchor, first_anchor]
    request = _request()
    request.rawDiff = MULTI_FILE_DIFF

    worklist = build_review_worklist(
        request,
        DiffProcessor().process(MULTI_FILE_DIFF),
        tracker,
    )

    assert [item.path for item in worklist] == ["src/first.py", "src/second.py"]


def test_worklist_matches_java_accepted_quoted_utf8_rename_path():
    anchor = SimpleNamespace(
        anchorId="e" * 64,
        kind="TEXT_HUNK",
        mandatory=True,
        initialState="PENDING",
        oldPath="old folder/naïve.py",
        newPath="new folder/你好.py",
        oldStart=1,
        oldLineCount=1,
        newStart=1,
        newLineCount=2,
        changeStatus="RENAME",
    )
    tracker = _CoverageTracker(anchor)
    request = _request()
    request.rawDiff = QUOTED_RENAME_DIFF

    worklist = build_review_worklist(
        request,
        DiffProcessor().process(QUOTED_RENAME_DIFF),
        tracker,
    )

    assert len(worklist) == 1
    assert worklist[0].path == "new folder/你好.py"
    assert worklist[0].change_status == "RENAME"
    assert worklist[0].exact_hunk_match is True
    assert "+validate(new)" in worklist[0].diff


def test_coverage_anchor_never_substitutes_a_different_hunk_from_same_file():
    mismatched = _anchor()
    mismatched.newStart = 99
    tracker = _CoverageTracker(mismatched)

    worklist = build_review_worklist(
        _request(), DiffProcessor().process(RAW_DIFF), tracker
    )

    assert len(worklist) == 1
    assert worklist[0].diff == ""
    assert worklist[0].exact_hunk_match is False


def test_strategy_is_general_repo_aware_review_without_category_playbooks():
    engine = AgenticReviewEngine(
        llm=_Model([]),
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
    )

    strategy = engine._system_prompt()

    assert "concrete, actionable defects" in strategy
    assert "repository and RAG tools" in strategy
    assert "style preferences" in strategy
    assert "new-side line visible" in strategy
    assert "attacker-controlled input" not in strategy
    assert "Never infer RESOLVED from a missing old line or moved snippet" in strategy
    assert "find_symbol/search_text" in strategy


def test_batch_prompt_uses_bound_task_and_relevant_bounded_enrichment_context():
    request = _request()
    request.prTitle = "MUTABLE title must not reach the prompt"
    request.prDescription = "MUTABLE description must not reach the prompt"
    request.projectRules = '["mutable-rule"]'
    task_context = {
        "acceptanceCriteria": (
            "Reject a debit when the configured account limit would be exceeded."
        ),
        "ticket": "PAY-17",
        **{f"z-extra-{index:03d}": "x" * 400 for index in range(80)},
    }
    related_metadata = [
        {
            "path": "src/payments.py",
            "language": "python",
            "imports": ["src/ledger.py"],
            "semantic_names": ["charge"],
            "calls": ["Ledger.debit"],
            "content_digest": "1" * 64,
            "parser_version": "tree-sitter-v1",
            "ast_supported": True,
            "symbols": [{
                "symbol_id": "payment-charge",
                "path": "src/payments.py",
                "name": "charge",
                "qualified_name": "payments.charge",
                "kind": "function",
                "start_line": 1,
                "end_line": 3,
            }],
            "relationships": [{
                "relationship_id": "payment-ledger-call",
                "source_symbol_id": "payment-charge",
                "source_name": "charge",
                "target_name": "Ledger.debit",
                "relationship_type": "CALLS",
                "source_line": 2,
                "target_path": "src/ledger.py",
                "resolution": "resolved",
                "confidence": 1.0,
            }],
        },
        {
            "path": "src/ledger.py",
            "language": "python",
            "semantic_names": ["Ledger.debit"],
            "content_digest": "2" * 64,
            "parser_version": "tree-sitter-v1",
            "ast_supported": True,
        },
        *[
            {
                "path": f"src/z-helper-{index:03d}.py",
                "language": "python",
                "semantic_names": [f"helper_{index}"],
                "content_digest": f"{index + 3:064x}",
                "parser_version": "tree-sitter-v1",
                "ast_supported": True,
            }
            for index in range(50)
        ],
        {
            "path": "src/unrelated.py",
            "language": "python",
            "semantic_names": ["NeverPromptThis"],
            "content_digest": "f" * 64,
            "parser_version": "tree-sitter-v1",
            "ast_supported": True,
        },
    ]
    relevant_relationships = [
        {
            "sourceFile": "src/payments.py",
            "targetFile": "src/ledger.py",
            "relationshipType": "CALLS",
            "matchedOn": "Ledger.debit",
            "strength": 100,
        },
        *[
            {
                "sourceFile": "src/payments.py",
                "targetFile": f"src/z-helper-{index:03d}.py",
                "relationshipType": "REFERENCES",
                "matchedOn": f"helper_{index}",
                "strength": 50,
            }
            for index in range(50)
        ],
        {
            "sourceFile": "src/unrelated.py",
            "targetFile": "src/other.py",
            "relationshipType": "CALLS",
            "matchedOn": "NeverPromptThis",
            "strength": 100,
        },
    ]
    request.enrichmentData = PrEnrichmentDataDto.model_validate({
        "fileContents": [{
            "path": "src/payments.py",
            "content": "RAW_FULL_FILE_CONTENT_MUST_NOT_APPEAR",
            "sizeBytes": 37,
        }],
        "fileMetadata": related_metadata,
        "relationships": relevant_relationships,
        "reviewContext": {
            "schemaVersion": 2,
            "prTitle": "Bound payment limit title",
            "prDescription": "Bound payment limit description",
            "prAuthor": "bound-author",
            "taskContext": task_context,
            "taskHistoryContext": "Bound task history: " + "h" * 10_000,
            "projectRules": '["bound-rule"]',
            "sourceBranchName": "feature/payment-limit",
            "targetBranchName": "main",
            "reviewApproach": "AGENTIC",
        },
    })
    engine = AgenticReviewEngine(
        llm=_Model([]),
        gateway=_Gateway(),
        request=request,
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=_CoverageTracker(_anchor()),
    )

    first = json.loads(
        engine._batch_prompt(engine.worklist, previous_findings=[])
    )
    second = json.loads(
        engine._batch_prompt(engine.worklist, previous_findings=[])
    )

    assert first == second
    assert first["pullRequest"] == {
        "author": "bound-author",
        "description": "Bound payment limit description",
        "title": "Bound payment limit title",
    }
    assert first["projectRules"] == '["bound-rule"]'
    context = first["boundContext"]
    assert context["taskContext"]["acceptanceCriteria"].startswith(
        "Reject a debit"
    )
    assert context["taskContext"]["ticket"] == "PAY-17"
    assert context["taskContextTruncated"] is True
    assert len(context["taskContext"]) <= 32
    assert context["taskHistoryContext"].startswith("Bound task history:")
    assert context["taskHistoryContext"].endswith("[truncated]")
    assert context["taskHistoryContextTruncated"] is True
    structural = context["structuralEnrichment"]
    metadata_paths = [item["path"] for item in structural["fileMetadata"]]
    assert metadata_paths[0] == "src/payments.py"
    assert "src/ledger.py" in metadata_paths
    assert "src/unrelated.py" not in metadata_paths
    assert len(metadata_paths) <= 8
    assert all(
        relation["sourceFile"] == "src/payments.py"
        or relation["targetFile"] == "src/payments.py"
        for relation in structural["relationships"]
    )
    assert len(structural["relationships"]) <= 32
    assert structural["truncated"] is True
    encoded = json.dumps(first, sort_keys=True, ensure_ascii=False)
    assert "NeverPromptThis" not in encoded
    assert "RAW_FULL_FILE_CONTENT_MUST_NOT_APPEAR" not in encoded
    assert "MUTABLE title" not in encoded
    assert "mutable-rule" not in encoded


@pytest.mark.asyncio
async def test_tool_loop_publishes_only_a_proven_confirmed_changed_line_defect():
    tracker = _CoverageTracker(_anchor())
    gateway = _Gateway()
    model = _Model(
        [
            _Response(
                tool_calls=[
                    {
                        "id": "tool-1",
                        "name": "read_file",
                        "args": {
                            "path": "src/payments.py",
                            "start_line": 1,
                            "end_line": 3,
                        },
                    }
                ]
            ),
            _Response(content=json.dumps(_final_payload())),
        ]
    )
    engine = AgenticReviewEngine(
        llm=model,
        gateway=gateway,
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )

    result = await engine.review()

    assert gateway.calls == [
        (
            "read_file",
            {"path": "src/payments.py", "start_line": 1, "end_line": 3},
        )
    ]
    assert model.bound_tools[0]["type"] == "function"
    assert len(result["issues"]) == 1
    assert result["issues"][0]["file"] == "src/payments.py"
    assert result["issues"][0]["line"] == 2
    assert tracker.examined == ["c" * 64]
    assert tracker.failed == []
    assert result["agenticReview"]["publishedFindings"] == 1
    assert result["agenticReview"]["filteredFindings"] == 0
    assert result["agenticReview"]["toolUsage"]["calls_used"] == 1
    tool_messages = model.bound.messages[-1]
    assert any(
        isinstance(item, dict) and item.get("role") == "tool"
        for item in tool_messages
    )


@pytest.mark.asyncio
async def test_logic_category_is_normalized_to_publishable_bug_risk():
    tracker = _CoverageTracker(_anchor())
    model = _Model([
        _Response(content=json.dumps(
            _final_payload(findings=[_finding(category="LOGIC")])
        )),
    ])

    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    ).review()

    assert len(result["issues"]) == 1
    assert result["issues"][0]["category"] == "BUG_RISK"
    assert result["agenticReview"]["hypotheses"][0][
        "publicationDisposition"
    ] == "PUBLISHED"


@pytest.mark.asyncio
async def test_prompt_uses_manifest_bound_previous_findings_from_review_context():
    tracker = _CoverageTracker(_anchor())
    model = _Model([
        _Response(content=json.dumps(_final_payload(findings=[]))),
        _Response(content=json.dumps(_previous_payload(decisions=[{
            "issueId": "previous-17",
            "status": "STILL_PRESENT",
            "reason": "The root cause remains reachable after searching the symbol.",
            "evidence": [_proof()],
        }]))),
    ])
    request = _bound_previous_finding_request()
    engine = AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=request,
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )

    result = await engine.review()

    work_prompt = json.loads(model.bound.messages[0][1]["content"])
    previous_prompt = json.loads(model.bound.messages[1][1]["content"])
    assert work_prompt["previousFindings"] == []
    assert previous_prompt["mode"] == "PREVIOUS_FINDING_RECONCILIATION"
    assert previous_prompt["previousFindings"][0]["decisionIssueId"] == "previous-17"
    assert previous_prompt["previousFindings"][0]["id"] == "previous-17"
    assert result["agenticReview"]["previousFindingDecisions"][0]["status"] == "STILL_PRESENT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finding",
    [
        _finding(findingType="ADVISORY"),
        _finding(verificationStatus="INCONCLUSIVE"),
        _finding(verificationStatus="REJECTED"),
        _finding(category="STYLE"),
    ],
)
async def test_unproven_or_non_defect_findings_are_not_published(finding):
    tracker = _CoverageTracker(_anchor())
    model = _Model([_Response(content=json.dumps(_final_payload(findings=[finding])))])
    engine = AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )

    result = await engine.review()

    assert result["issues"] == []
    assert result["agenticReview"]["publishedFindings"] == 0
    assert result["agenticReview"]["filteredFindings"] == 1
    assert tracker.examined == ["c" * 64]
    assert tracker.failed == []


@pytest.mark.asyncio
async def test_confirmed_diff_anchored_defect_publishes_without_receipt_bookkeeping():
    tracker = _CoverageTracker(_anchor())
    model = _Model([_Response(content=json.dumps(
        _final_payload(findings=[_finding()])
    ))])
    engine = AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )

    result = await engine.review()

    assert len(result["issues"]) == 1
    assert result["issues"][0]["line"] == 2
    assert len(model.bound.messages) == 1
    assert engine.gateway.calls == []


@pytest.mark.asyncio
async def test_unique_visible_snippet_repairs_an_inaccurate_line_hint_locally():
    tracker = _CoverageTracker(_anchor())
    finding = _finding(line=3)
    model = _Model([_Response(content=json.dumps(
        _final_payload(findings=[finding])
    ))])

    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    ).review()

    assert len(result["issues"]) == 1
    assert result["issues"][0]["line"] == 2
    assert len(model.bound.messages) == 1


@pytest.mark.asyncio
async def test_valid_hunk_line_publishes_with_diff_normalized_snippet():
    tracker = _CoverageTracker(_anchor())
    finding = _finding(
        line=2,
        codeSnippet="the new debit call shown in the hunk",
    )
    model = _Model([_Response(content=json.dumps(
        _final_payload(findings=[finding])
    ))])

    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    ).review()

    assert result["issues"][0]["line"] == 2
    assert result["issues"][0]["codeSnippet"] == "debit_without_limit(token)"


@pytest.mark.asyncio
async def test_multiline_markdown_snippet_is_anchored_to_its_nearest_exact_line():
    tracker = _CoverageTracker(_anchor())
    finding = _finding(
        line=3,
        codeSnippet=(
            "2:     debit_without_limit(token)\n"
            "3:     return gateway.charge(token)\n"
        ),
    )
    model = _Model([_Response(content=json.dumps(
        _final_payload(findings=[finding])
    ))])

    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    ).review()

    assert result["issues"][0]["line"] == 3
    assert result["issues"][0]["codeSnippet"] == "return gateway.charge(token)"


@pytest.mark.asyncio
async def test_context_line_is_a_valid_anchor_when_the_fault_is_at_the_caller():
    tracker = _CoverageTracker(_anchor())
    context_anchor = _finding(
        line=1,
        codeSnippet="def charge(token):",
    )
    model = _Model([_Response(content=json.dumps(
        _final_payload(findings=[context_anchor])
    ))])

    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    ).review()

    assert result["issues"][0]["line"] == 1
    assert result["issues"][0]["codeSnippet"] == "def charge(token):"


@pytest.mark.asyncio
async def test_unanchored_finding_is_filtered_without_a_correction_model_call():
    tracker = _CoverageTracker(_anchor())
    model = _Model([_Response(content=json.dumps(_final_payload(findings=[
        _finding(line=99, codeSnippet="not present in the PR diff")
    ])))])
    engine = AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )
    result = await engine.review()

    assert result["issues"] == []
    assert result["agenticReview"]["filteredFindings"] == 1
    assert result["agenticReview"]["hypotheses"][0]["publicationReason"] == (
        "anchor_not_visible_in_diff"
    )
    assert len(model.bound.messages) == 1


def test_finding_schema_has_no_receipt_or_causal_taxonomy_requirements():
    finding_schema = AgenticFinding.model_json_schema()

    assert "evidenceChain" not in finding_schema.get("properties", {})
    assert "rootSymbol" not in finding_schema.get("properties", {})
    assert "failureMode" not in finding_schema.get("properties", {})


@pytest.mark.asyncio
async def test_partial_inconclusive_hypothesis_is_retained_without_failing_batch():
    tracker = _CoverageTracker(_anchor())
    partial = _finding(verificationStatus="INCONCLUSIVE")
    model = _Model([_Response(content=json.dumps(
        _final_payload(findings=[partial])
    ))])

    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    ).review()

    assert result["issues"] == []
    assert result["agenticReview"]["failedBatches"] == 0
    assert result["agenticReview"]["hypotheses"][0]["verificationStatus"] == "INCONCLUSIVE"
    assert tracker.examined == ["c" * 64]
    assert tracker.failed == []


@pytest.mark.asyncio
async def test_clean_result_marks_manifest_bound_prompt_work_as_examined():
    tracker = _CoverageTracker(_anchor())
    model = _Model([
        _Response(content=json.dumps(_final_payload(findings=[]))),
    ])

    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    ).review()

    assert result["issues"] == []
    assert tracker.examined == ["c" * 64]
    assert tracker.failed == []
    assert result["agenticReview"]["reviewedWorkItems"] == 1
    assert result["agenticReview"]["failedWorkItems"] == 0


@pytest.mark.asyncio
async def test_clean_result_requires_current_batch_exact_source_read():
    tracker = _CoverageTracker(_anchor())
    model = _Model([
        _Response(tool_calls=[{
            "id": "tool-clean-proof",
            "name": "read_file",
            "args": {
                "path": "src/payments.py",
                "start_line": 1,
                "end_line": 3,
            },
        }]),
        _Response(content=json.dumps(_final_payload(findings=[]))),
    ])

    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    ).review()

    assert result["issues"] == []
    assert tracker.examined == ["c" * 64]
    assert tracker.failed == []
    assert result["agenticReview"]["reviewedWorkItems"] == 1


@pytest.mark.asyncio
async def test_anchor_hunk_mismatch_fails_closed_without_calling_the_model():
    mismatched = _anchor()
    mismatched.newStart = 99
    tracker = _CoverageTracker(mismatched)
    model = _Model([])
    engine = AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )

    result = await engine.review()

    assert result["issues"] == []
    assert model.bound.messages == []
    assert tracker.failed == [
        ("c" * 64, "agentic_coverage_anchor_hunk_mismatch")
    ]


@pytest.mark.asyncio
async def test_previous_decisions_fail_inconclusive_when_missing_conflicting_or_unproven():
    request = _bound_previous_finding_request()
    tracker = _CoverageTracker(_anchor())
    model = _Model([
        _Response(content=json.dumps(_final_payload(findings=[]))),
        _Response(content=json.dumps(_previous_payload(decisions=[
            {
                "issueId": "previous-17",
                "status": "STILL_PRESENT",
                "reason": "The original behavior remains.",
                "evidence": [_proof()],
            },
            {
                "issueId": "previous-17",
                "status": "RESOLVED",
                "reason": "The original behavior was removed.",
                "evidence": [_proof()],
            },
        ]))),
    ])
    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=request,
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    ).review()

    assert result["agenticReview"]["previousFindingDecisions"] == [{
        "issueId": "previous-17",
        "status": "INCONCLUSIVE",
        "reason": "The agent returned duplicate or conflicting decisions.",
        "evidence": [],
    }]


@pytest.mark.asyncio
async def test_previous_conclusive_decision_requires_registered_exact_source_proof():
    request = _bound_previous_finding_request()
    model = _Model([
        _Response(content=json.dumps(_final_payload(findings=[]))),
        _Response(content=json.dumps(_previous_payload(decisions=[{
            "issueId": "previous-17",
            "status": "RESOLVED",
            "reason": "The old line moved and the root cause is now guarded.",
            "evidence": [{**_proof(), "span_digest": "e" * 64}],
        }]))),
    ])
    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=request,
        processed_diff=DiffProcessor().process(RAW_DIFF),
    ).review()

    decision = result["agenticReview"]["previousFindingDecisions"][0]
    assert decision["status"] == "INCONCLUSIVE"
    assert decision["evidence"] == []


@pytest.mark.asyncio
async def test_previous_conclusive_decision_requires_proof_for_bound_finding_path():
    request = _bound_previous_finding_request()
    unrelated = _proof(path="src/unrelated.py", salt="unrelated")
    model = _Model([
        _Response(content=json.dumps(_final_payload(findings=[]))),
        _Response(content=json.dumps(_previous_payload(decisions=[{
            "issueId": "previous-17",
            "status": "RESOLVED",
            "reason": "An unrelated source span exists in the same snapshot.",
            "evidence": [unrelated],
        }]))),
    ])

    result = await AgenticReviewEngine(
        llm=model,
        gateway=_UnrelatedProofGateway(),
        request=request,
        processed_diff=DiffProcessor().process(RAW_DIFF),
    ).review()

    decision = result["agenticReview"]["previousFindingDecisions"][0]
    assert decision["status"] == "INCONCLUSIVE"
    assert decision["evidence"] == []


@pytest.mark.asyncio
async def test_missing_previous_decision_is_materialized_as_inconclusive():
    request = _bound_previous_finding_request()
    model = _Model([
        _Response(content=json.dumps(_final_payload(findings=[]))),
        _Response(content=json.dumps(_previous_payload(decisions=[]))),
    ])
    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=request,
        processed_diff=DiffProcessor().process(RAW_DIFF),
    ).review()

    assert result["agenticReview"]["previousFindingDecisions"][0]["status"] == "INCONCLUSIVE"
    assert "missing" in result["agenticReview"]["previousFindingDecisions"][0]["reason"].lower()


@pytest.mark.asyncio
async def test_deduplication_is_cheap_and_only_removes_same_anchor_and_title():
    exact_duplicate = _finding()
    distinct_title = _finding(title="Scheduled charge also bypasses limit")
    model = _Model([_Response(content=json.dumps(_final_payload(
        findings=[_finding(), exact_duplicate, distinct_title]
    )))])
    result = await AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=_CoverageTracker(_anchor()),
    ).review()

    assert len(result["issues"]) == 2
    assert len(result["agenticReview"]["hypotheses"]) == 3
    assert result["agenticReview"]["filteredFindings"] == 1


def test_previous_findings_are_split_into_bounded_non_repeating_prompt_batches():
    request = _bound_previous_finding_request()
    previous = request.enrichmentData.reviewContext.previousFindings[0]
    request.enrichmentData = PrEnrichmentDataDto.model_validate({
        "reviewContext": {
            **request.enrichmentData.reviewContext.model_dump(
                mode="json", exclude={"previousFindings"}
            ),
            "previousFindings": [
                {
                    **previous.model_dump(mode="json", exclude_none=True),
                    "id": f"previous-{index}",
                    "reason": "x" * 20_000,
                }
                for index in range(12)
            ],
        }
    })
    engine = AgenticReviewEngine(
        llm=_Model([]),
        gateway=_Gateway(),
        request=request,
        processed_diff=DiffProcessor().process(RAW_DIFF),
        max_previous_finding_chars=4_000,
        max_previous_findings_per_batch=3,
    )

    batches = engine._previous_finding_batches()
    ids = [item["decisionIssueId"] for batch in batches for item in batch]

    assert len(batches) >= 4
    assert len(ids) == len(set(ids)) == 12
    assert all(
        len(json.dumps(batch, ensure_ascii=False)) <= 4_000
        for batch in batches
    )


@pytest.mark.asyncio
async def test_valid_batch_covers_work_even_when_model_omits_accounting_id():
    tracker = _CoverageTracker(_anchor())
    payload = _final_payload(reviewed=[], unreviewable=[])
    model = _Model([_Response(content=json.dumps(payload))])
    engine = AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )

    result = await engine.review()

    assert len(result["issues"]) == 1
    assert result["agenticReview"]["reviewedWorkItems"] == 1
    assert result["agenticReview"]["failedWorkItems"] == 0
    assert tracker.examined == ["c" * 64]
    assert tracker.failed == []


@pytest.mark.asyncio
async def test_extra_batch_fields_and_provider_wrapper_do_not_drop_valid_output():
    tracker = _CoverageTracker(_anchor())
    payload = _final_payload()
    payload["providerSummary"] = {"confidence": 0.9}
    model = _Model(
        [_Response(content=json.dumps({"result": payload, "requestId": "abc"}))]
    )
    engine = AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )

    result = await engine.review()

    assert len(result["issues"]) == 1
    assert result["agenticReview"]["failedBatches"] == 0
    assert result["agenticReview"]["invalidModelOutputItems"] == {}
    assert tracker.examined == ["c" * 64]


@pytest.mark.asyncio
async def test_invalid_finding_does_not_discard_valid_findings_or_batch_coverage():
    tracker = _CoverageTracker(_anchor())
    invalid = _finding()
    invalid.pop("title")
    payload = _final_payload(findings=[invalid, _finding()])
    model = _Model([_Response(content=json.dumps(payload))])
    engine = AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )

    result = await engine.review()

    assert len(result["issues"]) == 1
    assert result["agenticReview"]["reviewedWorkItems"] == 1
    assert result["agenticReview"]["failedBatches"] == 0
    assert result["agenticReview"]["invalidModelOutputItems"] == {"finding": 1}
    assert tracker.examined == ["c" * 64]
    assert tracker.failed == []


@pytest.mark.asyncio
async def test_common_model_enum_aliases_are_normalized_at_the_boundary():
    payload = _final_payload(
        findings=[
            _finding(
                findingType="bug",
                verificationStatus="verified",
                severity="critical",
                category="logic_error",
                scope="line",
            )
        ]
    )
    engine = AgenticReviewEngine(
        llm=_Model([_Response(content=json.dumps(payload))]),
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
    )

    result = await engine.review()

    assert len(result["issues"]) == 1
    assert result["issues"][0]["severity"] == "HIGH"
    assert result["issues"][0]["category"] == "BUG_RISK"


@pytest.mark.asyncio
async def test_non_object_final_json_reports_batch_failure_reason():
    tracker = _CoverageTracker(_anchor())
    engine = AgenticReviewEngine(
        llm=_Model([_Response(content="[]")]),
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )

    result = await engine.review()

    assert result["agenticReview"]["failedBatches"] == 1
    assert result["agenticReview"]["failedBatchReasons"] == {"ValueError": 1}
    assert tracker.failed == [("c" * 64, "agentic_batch_failed")]


@pytest.mark.asyncio
async def test_explicitly_unreviewable_work_item_remains_incomplete():
    tracker = _CoverageTracker(_anchor())
    payload = _final_payload(
        reviewed=[],
        unreviewable=[
            {
                "workItemId": "c" * 64,
                "reason": "The supplied hunk could not be parsed.",
            }
        ],
        findings=[],
    )
    model = _Model([_Response(content=json.dumps(payload))])
    engine = AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
    )

    result = await engine.review()

    assert result["issues"] == []
    assert result["agenticReview"]["reviewedWorkItems"] == 0
    assert result["agenticReview"]["failedWorkItems"] == 1
    assert tracker.examined == []
    assert tracker.failed == [("c" * 64, "agentic_work_item_unreviewable")]


@pytest.mark.asyncio
async def test_tool_round_limit_forces_structured_final_result_without_fallback():
    tracker = _CoverageTracker(_anchor())
    endless_call = _Response(
        tool_calls=[{"id": "again", "name": "read_file", "args": {"path": "src/payments.py"}}]
    )
    model = _Model(
        [
            endless_call,
            endless_call,
            _Response(content=json.dumps(_final_payload())),
        ]
    )
    engine = AgenticReviewEngine(
        llm=model,
        gateway=_Gateway(),
        request=_request(),
        processed_diff=DiffProcessor().process(RAW_DIFF),
        coverage_tracker=tracker,
        max_tool_rounds=2,
    )

    result = await engine.review()

    assert len(result["issues"]) == 1
    assert result["agenticReview"]["failedBatches"] == 0
    assert result["agenticReview"]["reviewedWorkItems"] == 1
    assert len(model.bound_tools) == 0
    assert tracker.examined == ["c" * 64]
    assert tracker.failed == []


@pytest.mark.asyncio
async def test_real_local_tool_can_support_finding_without_receipt_in_output(tmp_path):
    repository = tmp_path / "source"
    (repository / "src").mkdir(parents=True)
    (repository / "src" / "payments.py").write_text(
        "def charge(token):\n"
        "    debit_without_limit(token)\n"
        "    return gateway.charge(token)\n",
        encoding="utf-8",
    )
    manifest = ExecutionManifestV1.model_construct(
        executionId=EXECUTION_ID,
        baseSha="a" * 40,
        headSha=HEAD_SHA,
        mergeBaseSha="c" * 40,
        diffDigest=sha256(RAW_DIFF.encode("utf-8")).hexdigest(),
        pullRequestId=17,
    )
    request = ReviewRequestDto.model_construct(
        executionManifest=manifest,
        projectVcsWorkspace="acme",
        projectVcsRepoSlug="payments",
        sourceBranchName="feature/payments",
        targetBranchName="main",
        pullRequestId=17,
        changedFiles=["src/payments.py"],
        rawDiff=RAW_DIFF,
        indexVersion="rag-commit-" + "a" * 40,
        ragContext=RagExecutionConfigV1(
            schemaVersion=1,
            indexVersion="rag-commit-" + "a" * 40,
            parserVersion="tree-sitter-v1",
            chunkerVersion="ast-code-splitter-v1",
            embeddingVersion="configured-v1",
        ),
        reviewApproach="AGENTIC",
        prTitle="Limit debits",
        prDescription="Apply account debit limits.",
        projectRules=None,
        previousCodeAnalysisIssues=[],
    )
    processed = DiffProcessor().process(RAW_DIFF)
    gateway = AgenticToolGateway(
        workspace_root=repository,
        request=request,
        rag_client=None,
        processed_diff=processed,
    )
    tracker = _CoverageTracker(_anchor())

    class _ToolUsingBound:
        async def ainvoke(self, messages):
            tool_messages = [
                item
                for item in messages
                if isinstance(item, dict) and item.get("role") == "tool"
            ]
            if not tool_messages:
                return _Response(
                    tool_calls=[
                        {
                            "id": "read-exact-source",
                            "name": "read_file",
                            "args": {
                                "path": "src/payments.py",
                                "start_line": 1,
                                "end_line": 3,
                            },
                        }
                    ]
                )
            return _Response(
                content=json.dumps(_final_payload(findings=[_finding()]))
            )

    class _ToolUsingModel:
        def bind_tools(self, _tools):
            return _ToolUsingBound()

    result = await AgenticReviewEngine(
        llm=_ToolUsingModel(),
        gateway=gateway,
        request=request,
        processed_diff=processed,
        coverage_tracker=tracker,
    ).review()

    assert len(result["issues"]) == 1
    assert result["agenticReview"]["toolUsage"]["local_calls"] == 1
    assert len(gateway.receipts) == 1
    assert gateway.validate_proof(
        gateway.receipts[0], expected_path="src/payments.py"
    )
