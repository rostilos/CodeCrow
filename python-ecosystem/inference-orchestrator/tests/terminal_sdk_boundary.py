"""Real LangChain/provider serialization regression; HTTP stays in MockTransport."""
import asyncio
import json
import httpx
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from llm.llm_factory import ChatOpenRouter
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
        model = model_type(api_key='offline-test-key', model='offline', http_async_client=http, max_retries=0)
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

async def main():
    for model_type in (ChatOpenAI, ChatOpenRouter):
        for correction in (None, 'repository', 'schema'):
            await exercise(model_type, correction)
    await exercise_saved_context_description()
    print('Real SDK terminal tools, normalization, correction and saved-context description passed; 0 network/model calls')

asyncio.run(asyncio.wait_for(main(), timeout=15))
