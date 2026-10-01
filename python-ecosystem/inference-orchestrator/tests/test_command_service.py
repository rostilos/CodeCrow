"""
Tests for CommandService helper methods.

Covers: _build_summarize_prompt, _build_ask_prompt, _build_jvm_props_for_*,
        _build_platform_jvm_props, _parse_json_response, _extract_json_object,
        _extract_summary_field_fallback, _emit_event, _create_mcp_client
"""
import pytest
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

from service.command.command_service import (
    AskOutput,
    COMMAND_MAX_OUTPUT_TOKENS,
    CommandInputLimitError,
    CommandService,
    _CommandProviderInputGuard,
    _command_input_token_budget,
    _estimated_command_input_tokens,
)
from utils.mcp_config import MCPConfigBuilder


@pytest.fixture
def service():
    with patch.dict("os.environ", {
        "MCP_SERVER_JAR": "/tmp/test.jar",
        "COMMAND_TIMEOUT_SECONDS": "60",
    }):
        with patch("service.command.command_service.RagClient"):
            svc = CommandService()
    return svc


# ── _emit_event ──────────────────────────────────────────────────

class TestEmitEvent:
    def test_calls_callback(self, service):
        cb = MagicMock()
        CommandService._emit_event(cb, {"type": "test"})
        cb.assert_called_once_with({"type": "test"})

    def test_none_callback(self, service):
        CommandService._emit_event(None, {"type": "test"})  # Should not raise

    def test_callback_exception_swallowed(self, service):
        cb = MagicMock(side_effect=RuntimeError("boom"))
        CommandService._emit_event(cb, {"type": "test"})  # Should not raise


# ── _build_jvm_props_for_summarize ───────────────────────────────

class TestBuildJvmPropsForSummarize:
    def test_returns_dict(self, service):
        request = MagicMock(
            projectId=1,
            pullRequestId=42,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            oAuthClient="client_id",
            oAuthSecret="secret",
            accessToken=None,
            maxAllowedTokens=100000,
            vcsProvider="bitbucket",
        )
        result = service._build_jvm_props_for_summarize(request)
        assert isinstance(result, dict)


# ── _build_jvm_props_for_ask ─────────────────────────────────────

class TestBuildJvmPropsForAsk:
    def test_returns_dict(self, service):
        request = MagicMock(
            projectId=1,
            pullRequestId=42,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            oAuthClient="oc",
            oAuthSecret="os",
            accessToken=None,
            maxAllowedTokens=50000,
            vcsProvider="github",
        )
        result = service._build_jvm_props_for_ask(request)
        assert isinstance(result, dict)


# ── _build_platform_jvm_props ────────────────────────────────────

class TestBuildPlatformJvmProps:
    def test_basic_props(self, service):
        request = MagicMock(
            projectId=5,
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            accessToken="tok",
            oAuthClient=None,
            oAuthSecret=None,
            vcsProvider="github",
        )
        result = service._build_platform_jvm_props(request)
        assert "api.base.url" in result
        assert result["project.id"] == "5"

    def test_with_oauth(self, service):
        request = MagicMock(
            projectId=5,
            pullRequestId=None,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            accessToken=None,
            oAuthClient="client",
            oAuthSecret="secret",
            vcsProvider="bitbucket",
        )
        result = service._build_platform_jvm_props(request)
        assert result.get("oAuthClient") == "client"

    @patch("os.path.exists", return_value=True)
    def test_command_credentials_and_internal_secret_do_not_enter_process_args(
            self,
            mock_exists,
            service,
    ):
        request = MagicMock(
            projectId=5,
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            accessToken="command-flow-token-sentinel",
            oAuthClient=None,
            oAuthSecret=None,
            vcsProvider="github",
            vcsBaseUrl=None,
        )
        with patch.dict(
                "os.environ",
                {"INTERNAL_API_SECRET": "command-internal-secret-sentinel"},
        ):
            platform_props = service._build_platform_jvm_props(request)

        config = MCPConfigBuilder.build_config(
            "/vcs.jar",
            include_platform_mcp=True,
            platform_mcp_jar_path="/platform.jar",
            platform_jvm_props=platform_props,
        )["mcpServers"]["codecrow-platform-mcp"]
        loggable_args = " ".join(config["args"])

        assert "command-flow-token-sentinel" not in loggable_args
        assert "command-internal-secret-sentinel" not in loggable_args
        assert config["env"]["CODECROW_MCP_ACCESS_TOKEN"] == (
            "command-flow-token-sentinel"
        )
        assert config["env"]["CODECROW_MCP_INTERNAL_API_SECRET"] == (
            "command-internal-secret-sentinel"
        )


# ── _build_summarize_prompt ──────────────────────────────────────

class TestBuildSummarizePrompt:
    def test_basic_prompt(self, service):
        request = MagicMock(
            pullRequestId=42,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            supportsMermaid=False,
            sourceBranch="feature/abc",
            targetBranch="main",
        )
        result = service._build_summarize_prompt(request)
        assert "PR #42" in result or "#42" in result
        assert "ws" in result
        assert "repo" in result
        assert "ASCII" in result

# ── _build_ask_prompt ────────────────────────────────────────────

class TestBuildAskPrompt:
    def test_basic_prompt(self, service):
        request = MagicMock(
            question="What does this PR do?",
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=None,
            issueReferences=None,
        )
        result = service._build_ask_prompt(request, None)
        assert "What does this PR do?" in result
        assert "ws" in result

    def test_with_analysis_context(self, service):
        request = MagicMock(
            question="Q?",
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext="This PR fixes a bug",
            issueReferences=None,
        )
        result = service._build_ask_prompt(request, None)
        assert "ANALYSIS CONTEXT" in result
        assert "This PR fixes a bug" in result

    def test_review_conversation_is_prioritized_for_referential_questions(self, service):
        request = MagicMock(
            question="Explain this issue in more detail",
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=(
                "## Review conversation context\n"
                "Comment by @codecrow-bot:\n"
                "Fractional values are silently truncated"
            ),
            issueReferences=None,
        )
        result = service._build_ask_prompt(request, None)
        assert "Review conversation context" in result
        assert "primary referent" in result
        assert "untrusted contextual evidence" in result

    def test_with_issue_references_and_platform(self, service):
        request = MagicMock(
            question="Tell me about issue 312",
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=None,
            issueReferences=["312", "313"],
        )
        result = service._build_ask_prompt(request, None, has_platform_mcp=True)
        assert "#312" in result
        assert "getIssueDetails" in result

    def test_with_deterministic_code_matches(self, service):
        request = MagicMock(
            question="Q?",
            pullRequestId=None,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=None,
            issueReferences=None,
        )
        matches = [{
            "text": "def authenticate(): ...",
            "path": "file.py",
            "match_reasons": ["symbol:authenticate", "path:file.py"],
        }]
        result = service._build_ask_prompt(request, matches)
        assert "DETERMINISTIC REPOSITORY SEARCH MATCHES" in result
        assert "def authenticate(): ..." in result
        assert "rank score" not in result
        assert "symbol:authenticate" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_code_search_failure_is_fail_open(self, service):
        service.rag_client.search_code = AsyncMock(return_value={
            "status": "error",
            "status_code": 503,
            "error": "search unavailable",
            "results": [],
        })
        request = MagicMock(
            projectWorkspace="ws",
            projectNamespace="project",
            question="where is authentication handled?",
            branch="main",
            repositoryRevision="abc123",
            ragGenerationManifestSha256="receipt",
            ragCollectionTarget="generation-collection",
        )
        events = []

        result = await service._search_code_for_ask(request, events.append)

        assert result is None
        service.rag_client.search_code.assert_awaited_once_with(
            workspace="ws",
            project="project",
            query="where is authentication handled?",
            branch="main",
            repository_revision="abc123",
            repository_generation_manifest_sha256="receipt",
            collection_target="generation-collection",
        )
        assert any(event.get("state") == "code_search_skipped" for event in events)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_code_search_without_complete_binding_uses_exact_tools_only(
        self,
        service,
    ):
        service.rag_client.search_code = AsyncMock()
        request = MagicMock(
            projectWorkspace="ws",
            projectNamespace="project",
            question="where is authentication handled?",
            branch="main",
            repositoryRevision=None,
            ragGenerationManifestSha256=None,
            ragCollectionTarget=None,
        )
        events = []

        result = await service._search_code_for_ask(request, events.append)

        assert result is None
        service.rag_client.search_code.assert_not_awaited()
        assert any(event.get("state") == "code_search_skipped" for event in events)

    def test_no_pr_context(self, service):
        request = MagicMock(
            question="Q?",
            pullRequestId=None,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=None,
            issueReferences=None,
        )
        result = service._build_ask_prompt(request, None)
        assert "Q?" in result

    def test_partial_code_search_is_explicit_and_all_matches_are_rendered(self, service):
        request = MagicMock(
            question="Q?",
            pullRequestId=None,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=None,
            issueReferences=None,
        )
        search = {
            "results": [
                {"path": f"src/{index}.py", "text": f"marker-{index}"}
                for index in range(150)
            ],
            "coverage": {
                "complete": False,
                "partial_reasons": ["global_matching_point_limit"],
            },
        }

        prompt = service._build_ask_prompt(request, search)

        assert "Search coverage: PARTIAL/UNKNOWN" in prompt
        for index in range(150):
            assert prompt.count(f"marker-{index}\n") == 1


class TestCommandInputPacking:
    def test_request_budget_reserves_output_without_setting_output_cap(self):
        assert _command_input_token_budget(MagicMock(maxAllowedTokens=50_000)) == 30_000
        assert _command_input_token_budget(MagicMock(maxAllowedTokens=200_000)) == 60_000
        assert _command_input_token_budget(MagicMock(maxAllowedTokens=8_000)) == 4_000

    def test_estimator_counts_utf8_schema_and_tools(self):
        ascii_only = _estimated_command_input_tokens("a" * 2_000)
        unicode_text = _estimated_command_input_tokens("界" * 2_000)
        with_declarations = _estimated_command_input_tokens(
            "a" * 2_000,
            tool_definitions=[{"description": "tool" * 1_000}],
            response_schema=AskOutput,
        )
        assert unicode_text > ascii_only
        assert with_declarations > ascii_only

    @pytest.mark.asyncio(loop_scope="function")
    async def test_complete_ask_prompt_uses_no_synthesis_call_when_it_fits(self, service):
        request = MagicMock(
            question="What changed?",
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext="complete small context",
            issueReferences=None,
        )
        llm = MagicMock()
        llm.ainvoke = AsyncMock()

        prompt = await service._prepare_ask_prompt(
            request,
            None,
            has_platform_mcp=False,
            llm=llm,
            input_token_budget=10_000,
            event_callback=None,
        )

        assert "complete small context" in prompt
        llm.ainvoke.assert_not_awaited()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_huge_unicode_context_is_bounded_with_coverage_diagnostic(self, service):
        paragraphs = [
            f"## Section {index}\nmarker-{index:04d}-界\n" + ("payload λ界 " * 180)
            for index in range(70)
        ]
        analysis_context = "\n\n".join(paragraphs)
        request = MagicMock(
            question="Which sections affect authentication?",
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=analysis_context,
            issueReferences=None,
        )
        calls = []

        async def synthesize(prompt):
            calls.append(prompt)
            record_ids = re.findall(r"--- RECORD ([^ ]+) START ---", prompt)
            response = MagicMock()
            response.content = json.dumps({
                "evidenceSynthesis": "condensed " + ",".join(record_ids)
            })
            return response

        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=synthesize)
        budget = 5_000

        final_prompt = await service._prepare_ask_prompt(
            request,
            None,
            has_platform_mcp=False,
            llm=llm,
            input_token_budget=budget,
            event_callback=None,
        )

        level_one = [prompt for prompt in calls if "Hierarchy level: 1;" in prompt]
        visible = "\n".join(level_one)
        assert 1 <= len(calls) <= 3
        assert "marker-0000-界" in visible
        assert sum(
            f"marker-{index:04d}-界" in visible for index in range(70)
        ) < 70
        assert "COMMAND_COVERAGE_DIAGNOSTIC" in visible
        assert "COMMAND_COVERAGE_DIAGNOSTIC" in final_prompt
        assert all(_estimated_command_input_tokens(prompt) <= budget for prompt in calls)
        assert _estimated_command_input_tokens(
            final_prompt,
            response_schema=AskOutput,
        ) <= budget
        assert "Original evidence SHA-256" in final_prompt
        assert all(call.kwargs == {} for call in llm.ainvoke.await_args_list)

    def test_indivisible_unicode_atom_hard_split_keeps_exact_bytes_and_fits(self, service):
        source = "界λ" * 12_000
        budget = 2_000

        fragments = service._hard_split_synthesis_record(
            "source-000001",
            source,
            question="Where is this used?",
            level=1,
            input_token_budget=budget,
        )

        assert "".join(fragment for _, fragment in fragments) == source
        assert len({fragment_id for fragment_id, _ in fragments}) == len(fragments)
        for fragment in fragments:
            prompt = service._render_synthesis_prompt(
                question="Where is this used?",
                records=[fragment],
                level=1,
                batch_index=1,
                batch_count=len(fragments),
            )
            assert _estimated_command_input_tokens(prompt) <= budget

    def test_provider_callback_rejects_tool_schema_growth_before_call(self):
        guard = _CommandProviderInputGuard(2_000, AskOutput)
        with pytest.raises(CommandInputLimitError, match="no evidence was truncated"):
            guard.on_chat_model_start(
                {"model": "test"},
                [[{"role": "user", "content": "question"}]],
                invocation_params={"tools": [{"description": "界" * 10_000}]},
            )


# ── _parse_json_response ─────────────────────────────────────────

class TestParseJsonResponse:
    def test_direct_json(self, service):
        data = {"summary": "test", "diagram": "", "diagramType": "ASCII"}
        result = service._parse_json_response(json.dumps(data))
        assert result["summary"] == "test"

    def test_json_in_code_block(self, service):
        text = '```json\n{"answer": "hello"}\n```'
        result = service._parse_json_response(text)
        assert result["answer"] == "hello"

    def test_json_with_surrounding_text(self, service):
        text = 'Here is the result: {"answer": "test"} done.'
        result = service._parse_json_response(text)
        assert result["answer"] == "test"

    def test_empty_response(self, service):
        assert service._parse_json_response("") is None
        assert service._parse_json_response(None) is None

    def test_invalid_json(self, service):
        assert service._parse_json_response("not json at all") is None

    def test_plain_code_block(self, service):
        text = '```\n{"key": "value"}\n```'
        result = service._parse_json_response(text)
        assert result["key"] == "value"


# ── _extract_json_object ─────────────────────────────────────────

class TestExtractJsonObject:
    def test_simple(self, service):
        text = 'prefix {"a": 1} suffix'
        result = service._extract_json_object(text)
        assert result == '{"a": 1}'

    def test_nested(self, service):
        text = '{"a": {"b": 1}}'
        result = service._extract_json_object(text)
        assert result == '{"a": {"b": 1}}'

    def test_no_json(self, service):
        assert service._extract_json_object("no braces here") is None

    def test_with_string_braces(self, service):
        text = '{"a": "value with {inner} braces"}'
        result = service._extract_json_object(text)
        assert result is not None

    def test_unbalanced_braces(self, service):
        text = '{"a": 1'
        result = service._extract_json_object(text)
        assert result is None


# ── _extract_summary_field_fallback ──────────────────────────────

class TestExtractSummaryFieldFallback:
    def test_extracts_summary(self, service):
        text = '{"summary": "This is the summary", "other": "data"}'
        result = service._extract_summary_field_fallback(text)
        assert result == "This is the summary"

    def test_with_escaped_quotes(self, service):
        text = '{"summary": "He said \\"hello\\"", "x": 1}'
        result = service._extract_summary_field_fallback(text)
        assert 'hello' in result

    def test_empty_text(self, service):
        assert service._extract_summary_field_fallback("") is None
        assert service._extract_summary_field_fallback(None) is None

    def test_no_summary_field(self, service):
        text = '{"answer": "something"}'
        result = service._extract_summary_field_fallback(text)
        assert result is None

    def test_with_newlines(self, service):
        text = '{"summary": "line1\\nline2"}'
        result = service._extract_summary_field_fallback(text)
        assert "line1" in result


# -- _normalize_*_result -----------------------------------------

class TestNormalizeSummarizeResult:
    def test_preserves_provider_error(self, service):
        result = service._normalize_summarize_result({"error": "provider failed"}, supports_mermaid=False)
        assert result == {"error": "provider failed"}

    def test_rejects_non_dict_result(self, service):
        result = service._normalize_summarize_result(None, supports_mermaid=False)
        assert result == {"error": "AI service returned an invalid summarize result"}

    @pytest.mark.parametrize("summary", [None, "", "   ", "null", "No output generated", "none"])
    def test_rejects_empty_summary_values(self, service, summary):
        result = service._normalize_summarize_result({"summary": summary}, supports_mermaid=False)
        assert result == {"error": "AI service returned an empty summary"}

    def test_defaults_missing_diagram_fields(self, service):
        result = service._normalize_summarize_result({"summary": "Summary", "diagram": None}, supports_mermaid=False)
        assert result == {
            "summary": "Summary",
            "diagram": "",
            "diagramType": "ASCII",
        }


class TestExecuteSummarize:
    class FakeAgent:
        def __init__(self, stream_items=None, run_result=None, stream_error=None):
            self.stream_items = stream_items or []
            self.run_result = run_result
            self.stream_error = stream_error
            self.run_called = False

        async def stream(self, *_args, **_kwargs):
            if self.stream_error:
                raise self.stream_error
            for item in self.stream_items:
                yield item

        async def run(self, *_args, **_kwargs):
            self.run_called = True
            return self.run_result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extracts_dict_stream_summary(self, service):
        message = MagicMock()
        message.content = '{"summary": "PR summary", "diagram": "", "diagramType": "ASCII"}'
        agent = self.FakeAgent(stream_items=[{"messages": [message]}])

        with patch("service.agent.agent_execution_service.RecursiveMCPAgent", return_value=agent):
            result = await service._execute_summarize(
                llm=MagicMock(),
                client=MagicMock(),
                prompt="prompt",
                supports_mermaid=False,
                event_callback=None,
            )

        assert result == {
            "summary": "PR summary",
            "diagram": "",
            "diagramType": "ASCII",
        }
        assert agent.run_called is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_uses_one_guarded_direct_fallback_when_stream_summary_is_empty(self, service):
        agent = self.FakeAgent(
            stream_items=[{"summary": ""}],
            run_result='{"summary": "Fallback summary", "diagram": "", "diagramType": "ASCII"}',
        )

        response = MagicMock(content='{"summary": "Fallback summary", "diagram": "", "diagramType": "ASCII"}')
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)
        with patch("service.agent.agent_execution_service.RecursiveMCPAgent", return_value=agent):
            result = await service._execute_summarize(
                llm=llm,
                client=MagicMock(),
                prompt="prompt",
                supports_mermaid=False,
                event_callback=None,
            )

        assert result["summary"] == "Fallback summary"
        assert agent.run_called is False
        assert llm.ainvoke.await_args.kwargs == {}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_direct_fallback_is_guarded_when_stream_raises_provider_error(self, service):
        agent = self.FakeAgent(
            stream_error=Exception("The AI provider rejected the request"),
            run_result='{"summary": "Fallback summary", "diagram": "", "diagramType": "ASCII"}',
        )

        response = MagicMock(content='{"summary": "Fallback summary", "diagram": "", "diagramType": "ASCII"}')
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)
        with patch("service.agent.agent_execution_service.RecursiveMCPAgent", return_value=agent):
            result = await service._execute_summarize(
                llm=llm,
                client=MagicMock(),
                prompt="prompt",
                supports_mermaid=False,
                event_callback=None,
            )

        assert result["summary"] == "Fallback summary"
        assert agent.run_called is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_uses_direct_llm_when_agent_outputs_empty_summary_sentinels(self, service):
        agent = self.FakeAgent(stream_items=["null"], run_result="No output generated")
        response = MagicMock()
        response.content = '{"summary": "Direct fallback summary.", "diagram": "", "diagramType": "ASCII"}'
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)

        with patch("service.agent.agent_execution_service.RecursiveMCPAgent", return_value=agent):
            result = await service._execute_summarize(
                llm=llm,
                client=MagicMock(),
                prompt="prompt",
                supports_mermaid=False,
                event_callback=None,
            )

        assert result["summary"] == "Direct fallback summary."
        assert agent.run_called is False
        llm.ainvoke.assert_awaited_once()


class TestNormalizeAskResult:
    def test_preserves_provider_error(self, service):
        result = service._normalize_ask_result({"error": "provider failed"})
        assert result == {"error": "provider failed"}

    def test_rejects_non_dict_result(self, service):
        result = service._normalize_ask_result(None)
        assert result == {"error": "AI service returned an invalid ask result"}

    @pytest.mark.parametrize("answer", [None, "", "   ", "null", "No output generated", "none"])
    def test_rejects_empty_answer_values(self, service, answer):
        result = service._normalize_ask_result({"answer": answer})
        assert result == {"error": "AI service returned an empty answer"}

    def test_accepts_answer(self, service):
        result = service._normalize_ask_result({"answer": "The PR updates auth handling."})
        assert result == {"answer": "The PR updates auth handling."}


class TestExecuteAsk:
    class FakeAgent:
        def __init__(self, stream_items=None, run_result=None):
            self.stream_items = stream_items or []
            self.run_result = run_result
            self.run_called = False

        async def stream(self, *_args, **_kwargs):
            for item in self.stream_items:
                yield item

        async def run(self, *_args, **_kwargs):
            self.run_called = True
            return self.run_result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extracts_dict_stream_answer(self, service):
        message = MagicMock()
        message.content = '{"answer": "The PR updates auth handling."}'
        agent = self.FakeAgent(stream_items=[{"messages": [message]}])

        with patch("service.agent.agent_execution_service.RecursiveMCPAgent", return_value=agent):
            result = await service._execute_ask(
                llm=MagicMock(),
                client=MagicMock(),
                prompt="prompt",
                event_callback=None,
            )

        assert result == {"answer": "The PR updates auth handling."}
        assert agent.run_called is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_uses_one_guarded_direct_fallback_when_stream_is_empty(self, service):
        agent = self.FakeAgent(
            stream_items=[],
            run_result='{"answer": "Fallback answer from non-structured run."}',
        )

        response = MagicMock(content='{"answer": "Fallback answer from non-structured run."}')
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)
        with patch("service.agent.agent_execution_service.RecursiveMCPAgent", return_value=agent):
            result = await service._execute_ask(
                llm=llm,
                client=MagicMock(),
                prompt="prompt",
                event_callback=None,
            )

        assert result == {"answer": "Fallback answer from non-structured run."}
        assert agent.run_called is False
        assert llm.ainvoke.await_args.kwargs == {}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_uses_direct_fallback_when_stream_answer_is_empty(self, service):
        agent = self.FakeAgent(
            stream_items=[{"answer": ""}],
            run_result='{"answer": "Fallback answer after empty structured output."}',
        )

        response = MagicMock(content='{"answer": "Fallback answer after empty structured output."}')
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)
        with patch("service.agent.agent_execution_service.RecursiveMCPAgent", return_value=agent):
            result = await service._execute_ask(
                llm=llm,
                client=MagicMock(),
                prompt="prompt",
                event_callback=None,
            )

        assert result == {"answer": "Fallback answer after empty structured output."}
        assert agent.run_called is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_uses_direct_llm_when_agent_outputs_empty_sentinels(self, service):
        agent = self.FakeAgent(stream_items=["null"], run_result="No output generated")
        response = MagicMock()
        response.content = '{"answer": "Direct fallback answer."}'
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=response)

        with patch("service.agent.agent_execution_service.RecursiveMCPAgent", return_value=agent):
            result = await service._execute_ask(
                llm=llm,
                client=MagicMock(),
                prompt="prompt",
                event_callback=None,
            )

        assert result == {"answer": "Direct fallback answer."}
        assert agent.run_called is False
        llm.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stops_observably_when_tool_transcript_cannot_fit(self, service):
        action = MagicMock()
        action.tool = "getPullRequestDiff"
        action.tool_input = {"pullRequestId": "42"}
        agent = self.FakeAgent(stream_items=[(action, {"diff": "界" * 20_000})])
        llm = MagicMock()
        llm.ainvoke = AsyncMock()
        events = []

        with patch("service.agent.agent_execution_service.RecursiveMCPAgent", return_value=agent):
            result = await service._execute_ask(
                llm=llm,
                client=MagicMock(),
                prompt="small prompt",
                event_callback=events.append,
                input_token_budget=5_000,
            )

        assert "error" in result
        assert "no evidence was truncated" in result["error"]
        assert any(event.get("state") == "input_limit_exceeded" for event in events)
        llm.ainvoke.assert_not_awaited()

    def test_full_returned_json_is_parsed_without_response_slicing(self, service):
        answer = "界" * 80_000 + "TAIL-EVIDENCE"
        result = service._coerce_ask_final_result(json.dumps({"answer": answer}))
        assert result["answer"] == answer


# ── _create_mcp_client ───────────────────────────────────────────

class TestCreateMcpClient:
    def test_creates_client(self, service):
        with patch("service.command.command_service.MCPClient") as mock_cls:
            mock_cls.from_dict.return_value = MagicMock()
            client = service._create_mcp_client({"servers": {}})
            mock_cls.from_dict.assert_called_once()
            client.add_middleware.assert_called_once()

    def test_raises_on_failure(self, service):
        with patch("service.command.command_service.MCPClient") as mock_cls:
            mock_cls.from_dict.side_effect = Exception("fail")
            with pytest.raises(Exception, match="Failed to construct"):
                service._create_mcp_client({})


# ── _create_llm ──────────────────────────────────────────────────

class TestCreateLlm:
    def test_creates_llm(self, service):
        request = MagicMock(
            aiModel="gpt-4",
            aiProvider="openai",
            aiApiKey="key",
            aiBaseUrl=None,
        )
        with patch("service.command.command_service.LLMFactory") as mock_factory:
            mock_factory.create_llm.return_value = MagicMock()
            llm = service._create_llm(request)
            mock_factory.create_llm.assert_called_once_with(
                "gpt-4",
                "openai",
                "key",
                ai_base_url=None,
                max_tokens=COMMAND_MAX_OUTPUT_TOKENS,
            )

    def test_raises_on_failure(self, service):
        request = MagicMock(
            aiModel="gpt-4",
            aiProvider="openai",
            aiApiKey="key",
            aiBaseUrl=None,
        )
        with patch("service.command.command_service.LLMFactory") as mock_factory:
            mock_factory.create_llm.side_effect = Exception("bad")
            with pytest.raises(Exception, match="Failed to create LLM"):
                service._create_llm(request)
