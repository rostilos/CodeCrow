#!/usr/bin/env python3
"""
Main entry point for the codecrow-mcp-client application.

This script can be run in two modes:
1. HTTP server mode (default): Starts a FastAPI web server
2. Stdin mode: Reads a single JSON request from stdin and processes it

Usage:
    python main.py                    # Run HTTP server
    python main.py --stdin            # Process single request from stdin
"""

import os
import sys
import logging
import warnings

from server.stdin_handler import StdinHandler
from api.app import run_http_server

# Configure logging - only if not already configured
# This prevents duplicate handlers when libraries also configure logging
root_logger = logging.getLogger()
if not root_logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        force=True
    )

# Ensure no duplicate handlers exist
# Remove duplicate handlers if they exist
if len(root_logger.handlers) > 1:
    handlers_to_keep = [root_logger.handlers[0]]
    root_logger.handlers = handlers_to_keep

# Suppress pydantic warnings
warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')


class _McpJsonRpcFilter(logging.Filter):
    """Filter out noisy JSONRPC parsing errors from the mcp_use library.
    
    These occur when Java MCP servers leak log messages to stdout, which
    the mcp_use library then fails to parse as JSON-RPC. They are harmless
    and clutter the logs.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "Failed to parse JSONRPC message from server" in msg:
            return False
        return True


# Install the filter on the mcp_use loggers instead of wrapping stderr
for _logger_name in ("mcp_use", "mcp_use.client", "mcp_use.client.session"):
    logging.getLogger(_logger_name).addFilter(_McpJsonRpcFilter())


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--stdin":
        # Run in stdin mode for single request processing
        handler = StdinHandler()
        handler.process_stdin_request()
    else:
        # Run in HTTP server mode (default)
        host = os.environ.get("AI_CLIENT_HOST", "0.0.0.0")
        port = int(os.environ.get("AI_CLIENT_PORT", "8000"))
        run_http_server(host=host, port=port)


if __name__ == "__main__":
    main()