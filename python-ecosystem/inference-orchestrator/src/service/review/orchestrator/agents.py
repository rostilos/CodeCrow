"""
Custom MCP Agent with increased recursion limit.
"""
import logging
from mcp_use import MCPAgent
from utils.llm_response import extract_llm_response_text

logger = logging.getLogger(__name__)


# Prevent duplicate logs from mcp_use
mcp_logger = logging.getLogger("mcp_use")
mcp_logger.propagate = False
if not mcp_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    mcp_logger.addHandler(handler)


class RecursiveMCPAgent(MCPAgent):
    """
    Subclass of MCPAgent that enforces a higher recursion limit on the internal agent executor.
    """
    def __init__(self, *args, recursion_limit: int = 50, **kwargs):
        self._custom_recursion_limit = recursion_limit
        super().__init__(*args, **kwargs)

    async def stream(self, *args, **kwargs):
        """
        Override stream to ensure recursion_limit is applied.
        """
        # Ensure the executor exists
        if self._agent_executor is None:
            await self.initialize()
        
        # Patch the executor's astream if not already patched
        executor = self._agent_executor
        if executor and not getattr(executor, "_is_patched_recursion", False):
            original_astream = executor.astream
            limit = self._custom_recursion_limit

            async def patched_astream(input_data, config=None, **astream_kwargs):
                if config is None:
                    config = {}
                config["recursion_limit"] = limit
                async for chunk in original_astream(input_data, config=config, **astream_kwargs):
                    yield chunk

            executor.astream = patched_astream
            executor._is_patched_recursion = True
            logger.info(f"RecursiveMCPAgent: Patched recursion limit to {limit}")
        
        # Call parent stream
        async for item in super().stream(*args, **kwargs):
            yield item
