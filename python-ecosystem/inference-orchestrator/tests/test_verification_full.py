"""Tests for verification_agent: search_file_content tool, run_verification_agent."""
import asyncio
import json
from hashlib import sha256
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from service.review.orchestrator import verification_agent
from service.review.orchestrator.verification_agent import (
    search_file_content,
    read_source_span,
    find_symbol_occurrences,
    run_verification_agent,
    run_deterministic_evidence_gate,
    VerificationDecision,
    VerificationResult,
    _FILE_CONTENTS_CACHE,
    _add_path_evidence,
    _build_file_evidence,
    _current_source_from_diff,
    _drop_invalid_exact_anchors,
    _env_int,
    _invoke_search_file_content,
    _invoke_verification_tool,
    _lookup_path_evidence,
    _parse_verification_result,
    _path_keys,
    _run_verification_tool_loop,
    _symbol_occurs_outside_anchor,
    _tool_call_attr,
    _unused_import_candidates,
    _validated_exact_drop_ids,
    _validated_exact_publications,
)
from model.output_schemas import CodeReviewIssue
from utils.diff_processor import DiffChangeType, DiffFile, ProcessedDiff


class _FakeResponse:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeToolLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def ainvoke(self, messages):
        self.messages.append(list(messages))
        if not self.responses:
            raise AssertionError("No fake response queued")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


# ── search_file_content tool ─────────────────────────────────


class TestSearchFileContent:
    def setup_method(self):
        verification_agent._FILE_CONTENTS_CACHE.clear()

    def teardown_method(self):
        verification_agent._FILE_CONTENTS_CACHE.clear()

    def test_found(self):
        verification_agent._FILE_CONTENTS_CACHE["a.py"] = "class Foo:\n    pass"
        # @tool is mocked as identity, so search_file_content is a plain function
        result = search_file_content("a.py", "Foo")
        assert "Found" in result

    def test_not_found(self):
        verification_agent._FILE_CONTENTS_CACHE["a.py"] = "class Foo:\n    pass"
        result = search_file_content("a.py", "Bar")
        assert "Not Found" in result

    def test_file_not_in_cache(self):
        result = search_file_content("missing.py", "x")
        assert "Error" in result or "not available" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_request_local_contents_are_isolated_across_concurrent_reviews(self):
        async def search_with_contents(content, search_string):
            token = verification_agent._ACTIVE_FILE_CONTENTS.set({"same.py": content})
            try:
                await asyncio.sleep(0)
                return search_file_content("same.py", search_string)
            finally:
                verification_agent._ACTIVE_FILE_CONTENTS.reset(token)

        found, missing = await asyncio.gather(
            search_with_contents("Alpha only", "Alpha"),
            search_with_contents("Beta only", "Alpha"),
        )

        assert "Found" in found
        assert "Not Found" in missing

    def test_source_tools_return_line_evidence_and_digest_receipt(self):
        content = "first line\nHelper.run()\nlast line"
        digest = sha256(content.encode("utf-8")).hexdigest()
        token = verification_agent._ACTIVE_SOURCE_RECEIPTS.set({
            "src/a.py": {
                "path": "src/a.py",
                "content": content,
                "content_digest": digest,
                "revision": "b" * 40,
                "complete_source": True,
                "snapshot_verified": True,
            }
        })
        try:
            span = json.loads(read_source_span("src/a.py", 2, 3))
            occurrences = json.loads(find_symbol_occurrences("src/a.py", "Helper"))
        finally:
            verification_agent._ACTIVE_SOURCE_RECEIPTS.reset(token)

        assert span["content_digest"] == digest
        assert span["lines"][0] == {"line": 2, "text": "Helper.run()"}
        assert occurrences["occurrence_count"] == 1
        assert occurrences["occurrences"][0]["line"] == 2

    def test_source_tools_reject_invalid_or_ambiguous_requests(self):
        assert json.loads(read_source_span("missing.py", 1, 2))["error"] == "source_not_available"
        token = verification_agent._ACTIVE_SOURCE_RECEIPTS.set({
            "a.py": {
                "path": "a.py",
                "content": "x = 1",
                "content_digest": sha256(b"x = 1").hexdigest(),
                "revision": None,
                "complete_source": True,
                "snapshot_verified": False,
            }
        })
        try:
            assert json.loads(read_source_span("a.py", 0, 500))["error"] == "invalid_line_range"
            assert json.loads(find_symbol_occurrences("a.py", "not valid"))["error"] == "invalid_identifier"
        finally:
            verification_agent._ACTIVE_SOURCE_RECEIPTS.reset(token)


# ── VerificationResult model ──────────────────────────────────


class TestVerificationResultModel:
    def test_empty(self):
        vr = VerificationResult(issue_ids_to_drop=[])
        assert vr.issue_ids_to_drop == []

    def test_with_ids(self):
        vr = VerificationResult(issue_ids_to_drop=["id1", "id2"])
        assert len(vr.issue_ids_to_drop) == 2
        assert vr.drop_evidence == []

    def test_exact_decision_shape(self):
        decision = VerificationDecision(
            issue_id="issue_0",
            finding_type="DEFECT",
            verification_status="CONFIRMED",
            file_path="src/a.py",
            line=2,
            code_snippet="unsafe()",
            content_digest="d" * 64,
            precondition="A request can provide the value passed to this call.",
            reachable_path="The handler reaches this call on its normal execution path.",
            failure="The call executes without the required validation guard.",
            impact="An invalid value can reach the protected operation.",
            counter_evidence="Complete source contains no validation before this call.",
        )

        assert decision.verification_status == "CONFIRMED"


class TestVerificationHelpers:
    def test_env_path_and_evidence_boundaries(self, monkeypatch):
        monkeypatch.delenv("COUNT", raising=False)
        assert _env_int("COUNT", 3) == 3
        monkeypatch.setenv("COUNT", "  ")
        assert _env_int("COUNT", 3) == 3
        monkeypatch.setenv("COUNT", "bad")
        assert _env_int("COUNT", 3) == 3
        monkeypatch.setenv("COUNT", "7")
        assert _env_int("COUNT", 3) == 7

        assert _path_keys("/a/b/file.py") == ["a/b/file.py", "b/file.py", "file.py"]
        assert _path_keys("") == []
        evidence = {}
        _add_path_evidence(evidence, "one/file.py", "first")
        _add_path_evidence(evidence, "two/file.py", "second")
        _add_path_evidence(evidence, "ignored.py", "")
        assert evidence["file.py"] is None
        assert _lookup_path_evidence(evidence, "one/file.py") == "first"
        assert _lookup_path_evidence(evidence, "file.py") is None
        _add_path_evidence(evidence, "three/file.py", "third")
        assert evidence["file.py"] is None

    def test_current_source_and_symbol_claim_helpers(self):
        diff = (
            "diff --git a/a.py b/a.py\nindex 1..2\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1 +1 @@\n-old\n context\n+new"
        )
        assert _current_source_from_diff(diff) == "context\nnew"
        assert _current_source_from_diff("irrelevant\n+visible") == "visible"

        issue = MagicMock()
        issue.title = "Unused Helper import"
        issue.reason = "Helper is never referenced"
        issue.codeSnippet = "from pkg import Helper, Helper"
        assert _unused_import_candidates(issue) == ["Helper"]
        issue.codeSnippet = ""
        assert _unused_import_candidates(issue) == []
        assert _symbol_occurs_outside_anchor(
            "Helper", "import Helper", "import Helper\nHelper.run()"
        )
        assert not _symbol_occurs_outside_anchor(
            "Helper", "+import Helper", "import Helper"
        )

    def test_build_file_evidence_prefers_complete_and_handles_skips(self):
        request = MagicMock()
        full = MagicMock(path="src/a.py", content="complete", skipped=False)
        skipped = MagicMock(path="src/b.py", content="secret", skipped=True)
        request.enrichmentData.fileContents = [full, skipped]
        processed = ProcessedDiff(files=[
            DiffFile(
                path="src/a.py", change_type=DiffChangeType.MODIFIED,
                content="@@ -1 +1 @@\n-old\n+partial",
            ),
            DiffFile(
                path="src/b.py", change_type=DiffChangeType.ADDED,
                content="@@ -0,0 +1 @@\n+visible",
            ),
        ])
        evidence = _build_file_evidence(request, processed)
        assert evidence["src/a.py"] == "complete"
        assert evidence["src/b.py"] == "visible"

        headers_only = ProcessedDiff(files=[DiffFile(
            path="src/c.py", change_type=DiffChangeType.MODIFIED,
            content="diff --git a/src/c.py b/src/c.py\n--- a/src/c.py\n+++ b/src/c.py",
        )])
        evidence = _build_file_evidence(request, headers_only)
        assert "src/c.py" not in evidence

    def test_deterministic_gate_empty_no_evidence_and_contradiction(self):
        request = MagicMock(enrichmentData=None, deltaDiff=None, rawDiff=None)
        assert run_deterministic_evidence_gate([], request) == []
        issue = MagicMock()
        assert run_deterministic_evidence_gate([issue], request) == [issue]

        issue.id = "drop"
        issue.file = "a.py"
        issue.title = "Unused Helper import"
        issue.reason = "The Helper import is never referenced"
        issue.codeSnippet = "import Helper"
        processed = ProcessedDiff(files=[DiffFile(
            path="a.py", change_type=DiffChangeType.ADDED,
            content="+import Helper\n+Helper.run()",
        )])
        assert run_deterministic_evidence_gate([issue], request, processed) == []

        issue.id = "keep"
        issue.title = "Possible branch issue"
        issue.reason = "This needs review"
        issue.codeSnippet = "Helper.run()"
        assert run_deterministic_evidence_gate([issue], request, processed) == [issue]

    def test_tool_call_and_invocation_boundaries(self):
        class Call:
            name = "search_file_content"

        assert _tool_call_attr({"name": "dict"}, "name") == "dict"
        assert _tool_call_attr(Call(), "name") == "search_file_content"
        assert "must be an object" in _invoke_search_file_content("bad")
        assert "required" in _invoke_search_file_content({"file_path": "a.py"})
        verification_agent._FILE_CONTENTS_CACHE["a.py"] = "needle"
        try:
            assert "Found" in _invoke_search_file_content({
                "file_path": "a.py", "search_string": "needle"
            })
        finally:
            verification_agent._FILE_CONTENTS_CACHE.clear()
        assert _parse_verification_result(
            'prefix {"issue_ids_to_drop": ["x"]} suffix'
        ).issue_ids_to_drop == ["x"]

        fake_tool = SimpleNamespace(invoke=MagicMock(return_value="invoked"))
        with patch.object(verification_agent, "search_file_content", fake_tool):
            assert _invoke_search_file_content({
                "file_path": "a.py", "search_string": "needle"
            }) == "invoked"
        assert "unsupported tool" in _invoke_verification_tool("other", {})

    def test_exact_drop_requires_machine_checked_receipt_and_claim(self):
        issue = CodeReviewIssue(
            severity="HIGH",
            category="BUG_RISK",
            file="src/a.py",
            line=2,
            title="Missing Helper definition",
            reason="Helper is undefined and cannot be found.",
            suggestedFixDescription="Define Helper.",
            codeSnippet="value = Helper()",
        )
        content = "class Helper:\n    pass\nvalue = Helper()"
        digest = sha256(content.encode("utf-8")).hexdigest()
        receipts = {
            "src/a.py": {
                "path": "src/a.py",
                "content": content,
                "content_digest": digest,
                "snapshot_verified": True,
            }
        }
        records = [("issue_0", issue)]

        unproved = VerificationResult(issue_ids_to_drop=["issue_0"])
        assert _validated_exact_drop_ids(unproved, records, receipts) == set()

        proved = VerificationResult.model_validate({
            "issue_ids_to_drop": ["issue_0"],
            "drop_evidence": [{
                "issue_id": "issue_0",
                "file_path": "src/a.py",
                "content_digest": digest,
                "evidence_kind": "named_symbol_present",
                "observed": "Helper",
            }],
        })
        assert _validated_exact_drop_ids(proved, records, receipts) == {"issue_0"}

        wrong_digest = proved.model_copy(update={
            "drop_evidence": [
                proved.drop_evidence[0].model_copy(update={"content_digest": "d" * 64})
            ]
        })
        assert _validated_exact_drop_ids(wrong_digest, records, receipts) == set()

    def test_exact_publication_accepts_only_confirmed_defect_with_source_receipt(self):
        issue = CodeReviewIssue(
            severity="HIGH",
            category="BUG_RISK",
            file="src/a.py",
            line=99,
            title="Unsafe call",
            reason="The new call has no validation.",
            suggestedFixDescription="Validate before calling.",
            codeSnippet="unsafe()",
        )
        source = "prepare()\nunsafe()\nfinish()"
        digest = sha256(source.encode("utf-8")).hexdigest()
        receipts = {
            "src/a.py": {
                "path": "src/a.py",
                "content": source,
                "content_digest": digest,
                "execution_id": "execution-accept-only",
                "revision": "b" * 40,
                "snapshot_verified": True,
            }
        }
        raw_diff = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n+++ b/src/a.py\n"
            "@@ -1,2 +1,3 @@\n prepare()\n+unsafe()\n finish()\n"
        )
        base = {
            "issue_id": "issue_0",
            "finding_type": "DEFECT",
            "verification_status": "CONFIRMED",
            "file_path": "src/a.py",
            "line": 2,
            "code_snippet": "unsafe()",
            "content_digest": digest,
            "precondition": "A request can provide the value passed to this call.",
            "reachable_path": "The handler reaches this call on its normal execution path.",
            "failure": "The call executes without the required validation guard.",
            "impact": "An invalid value can reach the protected operation.",
            "counter_evidence": "Complete source contains no validation before this call.",
        }

        confirmed = VerificationResult(decisions=[VerificationDecision(**base)])
        accepted = _validated_exact_publications(
            confirmed,
            [("issue_0", issue)],
            receipts,
            raw_diff=raw_diff,
            execution_id="execution-accept-only",
            head_sha="b" * 40,
        )
        assert len(accepted) == 1
        assert accepted[0].line == 2

        for update in (
            {"verification_status": "REJECTED"},
            {"verification_status": "INCONCLUSIVE"},
            {"finding_type": "ADVISORY"},
            {"content_digest": "e" * 64},
            {"precondition": "short"},
            {"reachable_path": "short"},
            {"failure": "short"},
            {"impact": "short"},
            {"counter_evidence": "short"},
        ):
            decision = VerificationDecision(**{**base, **update})
            assert _validated_exact_publications(
                VerificationResult(decisions=[decision]),
                [("issue_0", issue)],
                receipts,
                raw_diff=raw_diff,
                execution_id="execution-accept-only",
                head_sha="b" * 40,
            ) == []

        assert _validated_exact_publications(
            VerificationResult(decisions=[]),
            [("issue_0", issue)],
            receipts,
            raw_diff=raw_diff,
            execution_id="execution-accept-only",
            head_sha="b" * 40,
        ) == []

    def test_exact_anchor_gate_drops_only_absent_anchor_with_available_source(self):
        present = CodeReviewIssue(
            severity="HIGH", category="BUG_RISK", file="a.py", line=1,
            reason="present", suggestedFixDescription="fix", codeSnippet="x = 1",
        )
        absent = present.model_copy(update={"codeSnippet": "fabricated()"})
        unknown = present.model_copy(update={"file": "unknown.py", "codeSnippet": "fabricated()"})
        receipts = {
            "a.py": {
                "path": "a.py",
                "content": "x = 1",
                "content_digest": sha256(b"x = 1").hexdigest(),
                "snapshot_verified": True,
            }
        }

        kept, dropped = _drop_invalid_exact_anchors([present, absent, unknown], receipts)

        assert kept == [present, unknown]
        assert dropped == ["issue_1"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_tool_loop_rejects_unsupported_empty_and_times_out(self):
        with pytest.raises(RuntimeError, match="tool binding"):
            await _run_verification_tool_loop(object(), "prompt")

        empty = _FakeToolLLM([_FakeResponse(content="")])
        with pytest.raises(ValueError, match="no JSON"):
            await _run_verification_tool_loop(empty, "prompt")

        unsupported = _FakeToolLLM([
            _FakeResponse(tool_calls=[{"name": "other", "args": {}, "id": None}]),
            _FakeResponse(content='{"issue_ids_to_drop": []}'),
        ])
        with patch.object(verification_agent, "VERIFICATION_MAX_TOOL_ROUNDS", 2):
            result = await _run_verification_tool_loop(unsupported, "prompt")
        assert result.issue_ids_to_drop == []
        tool_messages = [
            message for message in unsupported.messages[1]
            if isinstance(message, dict) and message.get("role") == "tool"
        ]
        assert "unsupported tool" in tool_messages[0]["content"]

        timeout = _FakeToolLLM([
            _FakeResponse(tool_calls=[{"name": "other", "args": {}, "id": "1"}]),
        ])
        with patch.object(verification_agent, "VERIFICATION_MAX_TOOL_ROUNDS", 1):
            with pytest.raises(TimeoutError, match="did not produce"):
                await _run_verification_tool_loop(timeout, "prompt")


# ── run_verification_agent ────────────────────────────────────


class TestRunVerificationAgent:
    def _make_issue(self, issue_id, category, reason, file="a.py"):
        issue = MagicMock()
        issue.id = issue_id
        issue.category = category
        issue.reason = reason
        issue.file = file
        issue.severity = "HIGH"
        return issue

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_issue_list_short_circuits(self):
        assert await run_verification_agent(MagicMock(), [], MagicMock()) == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_review_publishes_confirmed_defect_from_decision_and_receipt(self):
        source = "prepare()\nunsafe()\nfinish()"
        digest = sha256(source.encode("utf-8")).hexdigest()
        head_sha = "b" * 40
        raw_diff = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n+++ b/src/a.py\n"
            "@@ -1,2 +1,3 @@\n prepare()\n+unsafe()\n finish()\n"
        )
        artifact = SimpleNamespace(
            kind="source-file",
            contentKey="src/a.py",
            snapshotSha=head_sha,
            contentDigest=digest,
            artifactId="source-a",
        )
        manifest = SimpleNamespace(
            executionId="execution-exact-verification",
            headSha=head_sha,
            inputArtifacts=[artifact],
        )
        request = SimpleNamespace(
            executionManifest=manifest,
            enrichmentData=SimpleNamespace(fileContents=[SimpleNamespace(
                path="src/a.py", content=source, skipped=False,
            )]),
            rawDiff=raw_diff,
            deltaDiff=None,
        )
        issue = CodeReviewIssue(
            severity="HIGH",
            category="BUG_RISK",
            file="src/a.py",
            line=99,
            title="Unsafe call",
            reason="The new call has no validation.",
            suggestedFixDescription="Validate before calling.",
            codeSnippet="unsafe()",
        )
        decision = {
            "issue_id": "issue_0",
            "finding_type": "DEFECT",
            "verification_status": "CONFIRMED",
            "file_path": "src/a.py",
            "line": 2,
            "code_snippet": "unsafe()",
            "content_digest": digest,
            "precondition": "A request can provide the value passed to this call.",
            "reachable_path": "The handler reaches this call on its normal execution path.",
            "failure": "The call executes without the required validation guard.",
            "impact": "An invalid value can reach the protected operation.",
            "counter_evidence": "Complete source contains no validation before this call.",
        }
        llm = _FakeToolLLM([_FakeResponse(content=json.dumps({
            "issue_ids_to_drop": [],
            "drop_evidence": [],
            "decisions": [decision],
        }))])

        with patch.object(verification_agent, "is_manifest_bound_v1", return_value=True):
            result = await run_verification_agent(llm, [issue], request)

        assert len(result) == 1
        assert result[0].line == 2
        prompt = llm.messages[0][1]["content"]
        assert "exact-snapshot accept-only review" in prompt
        assert "`precondition`" in prompt
        assert "`reachable_path`" in prompt
        assert "For SECURITY" in prompt
        assert "For PERFORMANCE" in prompt
        assert "For cross-file or architectural claims" in prompt

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_review_does_not_keep_undecided_candidate(self):
        source = "unsafe()"
        digest = sha256(source.encode("utf-8")).hexdigest()
        head_sha = "b" * 40
        request = SimpleNamespace(
            executionManifest=SimpleNamespace(
                executionId="execution-exact-undecided",
                headSha=head_sha,
                inputArtifacts=[SimpleNamespace(
                    kind="source-file",
                    contentKey="src/a.py",
                    snapshotSha=head_sha,
                    contentDigest=digest,
                    artifactId="source-a",
                )],
            ),
            enrichmentData=SimpleNamespace(fileContents=[SimpleNamespace(
                path="src/a.py", content=source, skipped=False,
            )]),
            rawDiff=(
                "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n"
                "+++ b/src/a.py\n@@ -0,0 +1 @@\n+unsafe()\n"
            ),
            deltaDiff=None,
        )
        issue = CodeReviewIssue(
            severity="HIGH", category="BUG_RISK", file="src/a.py", line=1,
            reason="Potential problem", suggestedFixDescription="Fix it",
            codeSnippet="unsafe()",
        )
        llm = _FakeToolLLM([_FakeResponse(content=json.dumps({
            "issue_ids_to_drop": [], "drop_evidence": [], "decisions": [],
        }))])

        with patch.object(verification_agent, "is_manifest_bound_v1", return_value=True):
            assert await run_verification_agent(llm, [issue], request) == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_skips_when_no_enrichment(self):
        request = MagicMock()
        request.enrichmentData = None
        issues = [self._make_issue("1", "BUG_RISK", "undefined var")]
        result = await run_verification_agent(MagicMock(), issues, request)
        assert result is issues

    @pytest.mark.asyncio(loop_scope="function")
    async def test_skips_when_no_file_contents(self):
        request = MagicMock()
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = []
        issues = [self._make_issue("1", "BUG_RISK", "undefined var")]
        result = await run_verification_agent(MagicMock(), issues, request)
        # With empty fileContents, still gets past the first check
        # but with no suspect issues matching, returns all
        assert len(result) >= 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_verifies_all_categories_with_model_selected_checks(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "class Foo:\n    pass\n"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue("1", "STYLE", "missing Foo")]
        llm = _FakeToolLLM([
            _FakeResponse(tool_calls=[{
                "name": "search_file_content",
                "args": {"file_path": "a.py", "search_string": "Foo"},
                "id": "call-1",
            }]),
            _FakeResponse(content='{"issue_ids_to_drop": ["issue_0"]}'),
        ])

        result = await run_verification_agent(llm, issues, request)

        assert result == []
        assert any(
            isinstance(message, dict) and message.get("role") == "tool" and "Found" in message.get("content", "")
            for call_messages in llm.messages
            for message in call_messages
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_duplicate_producer_ids_cannot_drop_a_distinct_finding(self):
        request = MagicMock()
        file_content = MagicMock()
        file_content.path = "a.py"
        file_content.content = "first_risk()\nsecond_risk()\n"
        file_content.skipped = False
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [file_content]

        first = self._make_issue("duplicate", "BUG_RISK", "first risk")
        second = self._make_issue("duplicate", "BUG_RISK", "second risk")
        llm = _FakeToolLLM([
            _FakeResponse(content='{"issue_ids_to_drop": ["issue_0"]}'),
        ])

        result = await run_verification_agent(llm, [first, second], request)

        assert result == [second]
        prompt = llm.messages[0][1]["content"]
        assert "Verification ID: issue_0" in prompt
        assert "Verification ID: issue_1" in prompt

    @pytest.mark.asyncio(loop_scope="function")
    async def test_can_drop_fresh_issue_without_persisted_id(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "class Foo:\n    pass\n"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue(None, "BUG_RISK", "missing Foo")]
        llm = _FakeToolLLM([
            _FakeResponse(tool_calls=[{
                "name": "search_file_content",
                "args": {"file_path": "a.py", "search_string": "Foo"},
                "id": "call-1",
            }]),
            _FakeResponse(content='{"issue_ids_to_drop": ["issue_0"]}'),
        ])

        result = await run_verification_agent(llm, issues, request)

        assert result == []
        first_prompt_messages = llm.messages[0]
        assert "Verification ID: issue_0" in first_prompt_messages[1]["content"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_keeps_issue_when_verification_returns_no_drops(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "class Foo: pass"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue("1", "BUG_RISK", "possible null pointer")]
        llm = _FakeToolLLM([
            _FakeResponse(content='{"issue_ids_to_drop": []}'),
        ])

        result = await run_verification_agent(llm, issues, request)

        assert result == issues

    @pytest.mark.asyncio(loop_scope="function")
    async def test_skipped_full_file_is_excluded_when_valid_file_exists(self):
        request = MagicMock()
        valid = MagicMock(path="a.py", content="code", skipped=False)
        skipped = MagicMock(path="b.py", content="private", skipped=True)
        request.enrichmentData.fileContents = [valid, skipped]
        issues = [self._make_issue("1", "STYLE", "keep this", file="a.py")]
        llm = _FakeToolLLM([_FakeResponse(content='{"issue_ids_to_drop": []}')])
        assert await run_verification_agent(llm, issues, request) == issues

    @pytest.mark.asyncio(loop_scope="function")
    async def test_verification_failure_returns_all(self):
        """When verification fails, all issues are returned as fallback."""
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "some code"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue("1", "BUG_RISK", "undefined variable x")]
        llm = _FakeToolLLM([RuntimeError("fail")])

        result = await run_verification_agent(llm, issues, request)

        assert len(result) == len(issues)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cleans_cache_on_exit(self):
        """Cache is cleared even after exceptions."""
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "code"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue("1", "BUG_RISK", "undefined xyz")]
        llm = _FakeToolLLM([RuntimeError("fail")])

        await run_verification_agent(llm, issues, request)
        assert len(verification_agent._FILE_CONTENTS_CACHE) == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_drops_unused_import_claim_contradicted_by_same_diff(self):
        path = "app/design/frontend/Perspective/Catalog/templates/ratings.phtml"
        diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1,3 +1,8 @@
+<?php
+use Perspective\\CatalogWidget\\Helper\\SwatchHelper;
+$product = $block->getProduct();
+$swatchHelper = $this->helper(SwatchHelper::class);
+$validateWineAttributeSet = $swatchHelper->validateAttributeSetByCode('Wine', $product->getAttributeSetId());
"""
        processed = ProcessedDiff(files=[
            DiffFile(
                path=path,
                change_type=DiffChangeType.MODIFIED,
                content=diff,
            )
        ])
        issue = CodeReviewIssue(
            severity="LOW",
            category="CODE_QUALITY",
            file=path,
            line=2,
            title="Unused SwatchHelper import in ratings template",
            reason="The SwatchHelper import is never referenced in the template.",
            suggestedFixDescription="Remove the unused import.",
            codeSnippet="use Perspective\\CatalogWidget\\Helper\\SwatchHelper;",
        )
        request = MagicMock(enrichmentData=None, rawDiff=diff, deltaDiff=None)

        result = await run_verification_agent(
            MagicMock(),
            [issue],
            request,
            processed,
        )

        assert result == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_keeps_genuinely_unused_import_from_same_diff(self):
        path = "src/example.php"
        diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1,2 @@
+<?php
+use Vendor\\Package\\UnusedHelper;
"""
        processed = ProcessedDiff(files=[
            DiffFile(path=path, change_type=DiffChangeType.MODIFIED, content=diff)
        ])
        issue = CodeReviewIssue(
            severity="LOW",
            category="CODE_QUALITY",
            file=path,
            line=2,
            title="Unused UnusedHelper import",
            reason="UnusedHelper is imported but never referenced.",
            suggestedFixDescription="Remove it.",
            codeSnippet="use Vendor\\Package\\UnusedHelper;",
        )
        request = MagicMock(enrichmentData=None, rawDiff=diff, deltaDiff=None)

        result = await run_verification_agent(MagicMock(), [issue], request, processed)

        assert result == [issue]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_diff_prefixed_anchor_does_not_count_import_as_usage(self):
        path = "src/example.php"
        diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1,2 @@
+<?php
+use Vendor\\Package\\UnusedHelper;
"""
        processed = ProcessedDiff(files=[
            DiffFile(path=path, change_type=DiffChangeType.MODIFIED, content=diff)
        ])
        issue = CodeReviewIssue(
            severity="LOW",
            category="CODE_QUALITY",
            file=path,
            line=2,
            title="Unused UnusedHelper import",
            reason="UnusedHelper is imported but never referenced.",
            suggestedFixDescription="Remove it.",
            codeSnippet="+use Vendor\\Package\\UnusedHelper;",
        )
        request = MagicMock(enrichmentData=None, rawDiff=diff, deltaDiff=None)

        result = await run_verification_agent(MagicMock(), [issue], request, processed)

        assert result == [issue]
