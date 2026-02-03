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
import threading

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

# Wrap stderr to filter out JSONRPC parsing errors from MCP library
# These occur when Java MCP servers leak log messages to stdout
class FilteredStderr:
    def __init__(self, original_stderr):
        self.original_stderr = original_stderr
        self.buffer = ""
        self._lock = threading.Lock()
        self._suppress_next_lines = 0

    def write(self, text):
        with self._lock:
            # Check if this is the start of a JSONRPC parsing error
            if "Failed to parse JSONRPC message from server" in text:
                self._suppress_next_lines = 15  # Suppress the next ~15 lines (traceback)
                return

            # If we're suppressing lines, decrement counter
            if self._suppress_next_lines > 0:
                self._suppress_next_lines -= 1
                return

            # Otherwise, write to original stderr
            self.original_stderr.write(text)

    def flush(self):
        self.original_stderr.flush()

    def __getattr__(self, name):
        return getattr(self.original_stderr, name)

# Install the filtered stderr wrapper
sys.stderr = FilteredStderr(sys.stderr)


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