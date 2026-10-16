"""Exercise real MCP/LangChain schemas outside the unit suite's module stubs."""
import os
from pathlib import Path
import subprocess
import sys


def test_real_graph_tool_schema_bounds():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-c', PROBE], cwd=project,
        env={**os.environ, 'PYTHONPATH': str(project / 'src'), 'MCP_USE_ANONYMIZED_TELEMETRY': 'false'},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


PROBE = r'''"""Replay the registered graph-tool schema through the real MCP adapter."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from mcp_use.client.connectors.base import BaseConnector

from service.agent.agent_execution_service import AgentExecutionService, _tool_with_host_bound_arguments
from service.rag.rag_mcp_server import server


async def initialized_tools():
    original = {tool.name: tool for tool in await server.list_tools()}
    connector = MagicMock(spec=BaseConnector)
    connector.list_tools = AsyncMock(return_value=list(original.values()))
    client = SimpleNamespace(get_all_active_sessions=lambda: {'rag': SimpleNamespace(connector=connector)})
    service = AgentExecutionService(llm=object(), client=client)
    await service.initialize(required_server_names=['rag'])
    return original, {tool.name: tool for tool in service.available_tools_for(set(original))}


async def test_actual_graph_bounds_survive_adapter_and_host_binding(name):
    original, converted = await initialized_tools()
    tool = converted[name]
    bindings = {'focusPaths': ['src/exact.py']} if 'focusPaths' in tool.args_schema.model_fields else {}
    bound = _tool_with_host_bound_arguments(tool, bindings)
    for current in (tool, bound):
        properties = current.tool_call_schema.model_json_schema()['properties']
        if name == 'queryCodeGraph':
            assert properties['maxSourceWindows'].get('maximum') == 20, properties['maxSourceWindows']
        for field, source in original[name].inputSchema['properties'].items():
            if field == 'focusPaths':
                continue
            for constraint in ('minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf'):
                if constraint in source:
                    assert properties[field].get(constraint) == source[constraint], (name, field, constraint, properties[field])
    assert 'focusPaths' not in bound.tool_call_schema.model_json_schema()['properties']
    if bindings:
        assert 'focusPaths' in tool.tool_call_schema.model_json_schema()['properties']


async def test_actual_query_defaults_optional_values_and_tool_contract_unchanged():
    _, converted = await initialized_tools()
    tool = converted['queryCodeGraph']
    bound = _tool_with_host_bound_arguments(tool, {'focusPaths': ['src/exact.py']})
    schema = bound.tool_call_schema.model_json_schema()['properties']
    assert schema['maxSourceWindows']['default'] == 6
    assert schema['cursor']['default'] == 0
    assert schema['includeSource']['default'] is True
    assert schema['evidenceId']['default'] is None
    assert {'type': 'null'} in schema['evidenceId']['anyOf']
    assert bound.args_schema().maxSourceWindows == 6
    assert bound.args_schema(evidenceId=None).evidenceId is None
    assert bound.name == tool.name and bound.description == tool.description

async def main():
    for name in ['queryCodeGraph', 'getMinimalReviewContext', 'getImpactRadius', 'traverseCodeGraph', 'exploreReviewContext', 'getStructuralUnit']:
        await test_actual_graph_bounds_survive_adapter_and_host_binding(name)
        print('PASS numeric bounds:', name)
    await test_actual_query_defaults_optional_values_and_tool_contract_unchanged()
    print('PASS defaults, optional null and host-bound scope controls')

import asyncio
asyncio.run(main())
'''
