"""Exercise real framework binding in a fresh interpreter without unit-test stubs."""
import asyncio
import sys
from pydantic import BaseModel
from langchain.agents.structured_output import ToolStrategy
from service.agent.recursive_mcp_agent import InitialRequiredToolMiddleware

class _Output(BaseModel):
    value: str

async def check(synchronous):
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI

    bindings, executed = [], []
    responses = iter([
        AIMessage(content='', tool_calls=[dict(name='getMinimalReviewContext', args={'question': 'Inspect dependencies'}, id='context')]),
        AIMessage(content='', tool_calls=[dict(name='getReviewFileContent', args={'path': 'a.py'}, id='source')]),
        AIMessage(content='', tool_calls=[dict(name='_Output', args={'value': 'complete'}, id='final')]),
    ])
    adapter = ChatOpenAI(api_key='offline-fixture')

    class RecordingModel(BaseChatModel):
        @property
        def _llm_type(self):
            return 'offline-recording-model'

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            bindings.append(adapter.bind_tools(tools, tool_choice=tool_choice, **kwargs).kwargs)
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=next(responses))])

    @tool
    def getMinimalReviewContext(question: str) -> str:
        """Read the request-bound graph context."""
        executed.append('context')
        return '{"status":"ready"}'

    @tool
    def getReviewFileContent(path: str) -> str:
        """Read request-bound source."""
        executed.append('source')
        return 'x = 1'

    agent = create_agent(RecordingModel(), tools=[getMinimalReviewContext, getReviewFileContent],
        response_format=ToolStrategy(_Output),
        middleware=[InitialRequiredToolMiddleware(tool_name='getMinimalReviewContext')])
    inputs = {'messages': [{'role': 'user', 'content': 'Review a.py'}]}
    result = agent.invoke(inputs) if synchronous else await agent.ainvoke(inputs)
    assert result['structured_response'] == _Output(value='complete')
    assert executed == ['context', 'source']
    assert len(bindings) == 3
    assert bindings[0]['tool_choice'] == {'type': 'function', 'function': {'name': 'getMinimalReviewContext'}}
    assert bindings[1]['tool_choice'] == bindings[2]['tool_choice'] == 'required'
    assert bindings[0]['tools'] == bindings[1]['tools'] == bindings[2]['tools']

if __name__ == "__main__":
    asyncio.run(check(sys.argv[1] == "sync"))
