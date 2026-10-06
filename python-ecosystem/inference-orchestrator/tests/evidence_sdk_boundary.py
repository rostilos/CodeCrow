"""Real LangGraph reducer contract; runs in a fresh process without conftest mocks."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph.message import add_messages

src = Path(__file__).resolve().parents[1] / "src"
for package in ("service", "service.review", "service.agent"):
    module = ModuleType(package)
    module.__path__ = []
    sys.modules[package] = module
for module_name, path in (("service.review.inference_usage", "service/review/inference_usage.py"), ("service.agent.evidence_acquisition", "service/agent/evidence_acquisition.py")):
    spec = importlib.util.spec_from_file_location(module_name, src / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

from service.agent.evidence_acquisition import EvidenceAcquisitionMiddleware, observation_status

async def main():
    middleware = EvidenceAcquisitionMiddleware()
    executed = []
    async def handler(request):
        executed.append(request.tool_call)
        return ToolMessage(content=json.dumps({"fileContent": "return user.name"}), tool_call_id=request.tool_call["id"])
    transcript = []
    for call_id in ("call1", "call2"):
        call = {"id": call_id, "name": "getReviewFileContent", "args": {"filePath": "a.py"}, "type": "tool_call"}
        transcript = add_messages(transcript, [AIMessage(content="", tool_calls=[call])])
        result = await middleware.awrap_tool_call(SimpleNamespace(tool_call=call), handler)
        transcript = add_messages(transcript, [result])
    assert len(executed) == 1
    assert [message.tool_call_id for message in transcript if isinstance(message, ToolMessage)] == ["call1", "call2"]
    assert len({message.id for message in transcript}) == 4
    assert observation_status(ToolMessage(content="provider offline", status="error", tool_call_id="x")) == "unavailable"
    assert observation_status(ToolMessage(content="Error: File not found", tool_call_id="x")) == "unavailable"
    assert observation_status(ToolMessage(content='raise Error("invalid")', tool_call_id="x")) == "acquired"
    assert observation_status(ToolMessage(content=json.dumps({"context": {"evidence": {"format": "normalized_nodes_edges", "nodes": [], "relations": [], "frontier": [], "callPaths": [], "blastRadius": [], "tests": [], "framework": []}, "sourceWindows": []}}), tool_call_id="x")) == "empty"
    assert observation_status(SimpleNamespace(isError=True, content="missing source")) == "unavailable"
    empty = EvidenceAcquisitionMiddleware()
    async def no_results(request):
        return ToolMessage(content=json.dumps({"results": []}), tool_call_id=request.tool_call["id"])
    for index in range(2):
        await empty.awrap_tool_call(SimpleNamespace(tool_call={"name": "queryCodeGraph", "args": {"question": "caller " + str(index)}, "id": str(index)}), no_results)
    async def model(request):
        assert [tool["name"] for tool in request.tools] == ["queryCodeGraph", "getReviewFileContent"]
        assert request.model_settings["tool_choice"]["function"]["name"] == "queryCodeGraph"
        return "fallback source available"
    request = SimpleNamespace(tools=[{"name":"queryCodeGraph"}, {"name":"getReviewFileContent"}], model_settings={"tool_choice": {"type":"function", "function":{"name":"queryCodeGraph"}}})
    request.override = lambda **kwargs: SimpleNamespace(**kwargs)
    assert await empty.awrap_model_call(request, model) == "fallback source available"
    fresh = EvidenceAcquisitionMiddleware()
    await fresh.awrap_tool_call(SimpleNamespace(tool_call={"id":"other-review", "name":"getReviewFileContent", "args":{"filePath":"a.py"}}), handler)
    assert len(executed) == 2

asyncio.run(main())
print("Real SDK cache/reducer/recovery and per-review isolation passed")
