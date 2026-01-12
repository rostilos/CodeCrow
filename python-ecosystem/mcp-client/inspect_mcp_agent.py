
import inspect
import mcp_use.agent
from mcp_use.agent import MCPAgent

print("MCPAgent location:", mcp_use.agent.__file__)
print("\nMCPAgent.__init__ signature:")
print(inspect.signature(MCPAgent.__init__))

print("\nMCPAgent.stream signature:")
print(inspect.signature(MCPAgent.stream))

print("\nMCPAgent.stream source (start):")
try:
    src = inspect.getsource(MCPAgent.stream)
    print(src[:500])
except Exception as e:
    print("Could not get source:", e)
