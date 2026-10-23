"""Real LangChain/provider serialization regression; HTTP stays in MockTransport."""
import asyncio
import json
import httpx
from unittest.mock import patch
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from llm.llm_factory import ChatOpenRouter, LLMFactory
from model.multi_stage import FileReviewBatchOutput
from service.agent.recursive_mcp_agent import FinalResponseReserveMiddleware

@tool
def getReviewFileContent(filePath: str) -> str:
    """Read repository source."""
    raise AssertionError('Repository tools must not execute during finalization')

async def exercise(model_type, correction):
    requests = []
    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if model_type is ChatOpenRouter:
            assert 'parallel_tool_calls' not in body
            assert body['provider'] == {'only': ['cloudflare', 'relace'], 'require_parameters': True}
        assert body['tool_choice'] == {'type': 'function', 'function': {'name': 'FileReviewBatchOutput'}}, body['tool_choice']
        assert [t['function']['name'] for t in body['tools']] == ['FileReviewBatchOutput']
        assert 'reviews' in body['tools'][0]['function']['parameters']['properties']
        name = 'FileReviewBatchOutput'
        args = {'reviews': json.dumps([{'file': 'a.py', 'analysis_summary': 'checked', 'issues': []}])}
        if correction == 'repository' and len(requests) == 1:
            name, args = 'getReviewFileContent', {'filePath': 'a.py'}
        if correction == 'schema' and len(requests) == 1:
            args = {'reviews': [{'file': 'a.py', 'issues': []}]}
        if len(requests) == 2:
            assert body['messages'][-1]['role'] == 'tool'
            assert body['messages'][-1]['tool_call_id'] == 'call-1'
            if correction == 'schema':
                feedback = body['messages'][-1]['content']
                assert 'schema validation' in feedback
                assert 'analysis_summary' in feedback
                assert 'Preserve the review findings' in feedback
                assert 'was not executed' not in feedback
        return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'model': 'offline', 'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'call-{len(requests)}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}}], 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        options = {'extra_body': {'provider': {'only': ['cloudflare', 'relace'], 'require_parameters': True}}} if model_type is ChatOpenRouter else {}
        model = model_type(api_key='offline-test-key', model='offline', http_async_client=http, max_retries=0, **options)
        reserve = FinalResponseReserveMiddleware(exploration_model_calls=0, structured_output=True)
        graph = create_agent(model, tools=[getReviewFileContent], response_format=ToolStrategy(FileReviewBatchOutput), middleware=[reserve, ModelCallLimitMiddleware(run_limit=2, exit_behavior='error')])
        result = await graph.ainvoke({'messages': [('user', 'Review a.py')]})
        assert result['structured_response'].reviews[0].file == 'a.py'
        assert len(requests) == (2 if correction else 1)

async def exercise_saved_context_description():
    from service.command.command_service import CommandService
    async def respond(request):
        payload = json.loads(request.content)
        assert payload['response_format']['type'] == 'json_schema'
        return httpx.Response(200, json={
            'id': 'offline-description', 'object': 'chat.completion', 'model': 'offline',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps({
                    'summary': 'Description from saved PR context', 'diagram': '', 'diagramType': 'ASCII'})}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        model = ChatOpenAI(api_key='offline-test-key', model='offline', http_async_client=http, max_retries=0)
        result = await CommandService()._execute_summarize(model, None, 'Saved PR context', False, None)
        assert result['summary'] == 'Description from saved PR context'


async def exercise_openrouter_exploration():
    requests, tool_reads = [], []
    routing = {'only': ['cloudflare', 'relace'], 'order': ['cloudflare', 'relace'], 'require_parameters': True}
    @tool
    def read_source(path: str) -> str:
        """Read a source file."""
        tool_reads.append(path)
        return 'def example(): return 1'
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert 'parallel_tool_calls' not in body
        assert body['provider'] == routing
        assert {t['function']['name'] for t in body['tools']} == {'read_source', 'FileReviewBatchOutput'}
        if len(requests) == 1:
            name, args = 'read_source', {'path': 'a.py'}
        else:
            assert body['messages'][-1]['role'] == 'tool'
            assert body['messages'][-1]['content'] == 'def example(): return 1'
            name, args = 'FileReviewBatchOutput', {'reviews': [{'file': 'a.py', 'analysis_summary': 'checked', 'issues': []}]}
        return httpx.Response(200, json={'id': 'offline-exploration', 'object': 'chat.completion', 'model': 'offline',
            'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                'tool_calls': [{'id': f'call-{len(requests)}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}}],
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        model = ChatOpenRouter(api_key='offline-test-key', model='offline', http_async_client=http, max_retries=0,
                               extra_body={'provider': routing})
        graph = create_agent(model, tools=[read_source], response_format=ToolStrategy(FileReviewBatchOutput),
            middleware=[FinalResponseReserveMiddleware(exploration_model_calls=2, structured_output=True)])
        result = await graph.ainvoke({'messages': [('user', 'Review a.py')]})
        assert result['structured_response'].reviews[0].file == 'a.py'
        assert len(requests) == 2 and tool_reads == ['a.py']


async def exercise_compatible_recovery():
    from service.review.orchestrator.stage_1_file_review import _invoke_stage_1_batch_llm
    from service.review.orchestrator.stage_2_cross_file import _invoke_stage_2_llm
    requests = []
    answer = {'reviews': [{'file': 'a.py', 'analysis_summary': 'checked', 'issues': [], 'confidence': 'HIGH'}]}
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={
            'id': 'offline-recovery', 'object': 'chat.completion', 'model': 'offline',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(answer)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with httpx.Client(transport=httpx.MockTransport(respond)) as sync:
            with patch('llm.ssrf_safe_transport.create_ssrf_safe_http_client', return_value=sync), \
                 patch('llm.ssrf_safe_transport.create_ssrf_safe_async_http_client', return_value=http):
                model = LLMFactory.create_llm('deepseek/deepseek-v4.1-flash-1017', 'OPENAI_COMPATIBLE', 'offline-test-key',
                    ai_base_url='https://openrouter.ai/api/v1', ai_custom_parameters={
                        'reasoning_effort': 'high', 'extra_body': {'reasoning': {'effort': 'high'}, 'provider': {'only': ['cloudflare']}}})
            assert isinstance(model, ChatOpenRouter)
            for configured, expected in [(None, 131_072), (4096, 4096), (262_144, 262_144)]:
                model.max_tokens = configured
                for stage in (1, 2):
                    answer = ({'reviews': [{'file': 'a.py', 'analysis_summary': 'checked', 'issues': [], 'confidence': 'HIGH'}]}
                              if stage == 1 else {'pr_risk_level': 'LOW', 'cross_file_issues': [], 'pr_recommendation': 'PASS', 'confidence': 'HIGH'})
                    for raw in (False, True):
                        if stage == 1:
                            result = await _invoke_stage_1_batch_llm(model, 'Review a.py', ['a.py'], force_unstructured=raw)
                            assert result == []
                        else:
                            result = await _invoke_stage_2_llm(model, 'Review a.py', 'budget-check', force_unstructured=raw)
                            assert result is not None and result.cross_file_issues == []
                        effort = 'none' if stage == 1 or raw else 'low'
                        body = requests[-1]
                        assert body['max_tokens'] == expected and 'max_completion_tokens' not in body
                        assert body['reasoning_effort'] == effort and body['reasoning'] == {'effort': effort}
                        assert body['provider'] == {'only': ['cloudflare']}
            assert len(requests) == 12
            assert model.reasoning_effort == 'high' and model.extra_body['reasoning']['effort'] == 'high'

async def main():
    await exercise_openrouter_exploration()
    for model_type in (ChatOpenAI, ChatOpenRouter):
        for correction in (None, 'repository', 'schema'):
            await exercise(model_type, correction)
    await exercise_saved_context_description()
    await exercise_compatible_recovery()
    print('Real SDK terminal tools, normalization, correction and saved-context description passed; 0 network/model calls')

asyncio.run(asyncio.wait_for(main(), timeout=15))
