import json
import sys
import asyncio
from typing import Optional, Dict, Any

from model.models import ReviewRequestDto
from service.review_service import ReviewService


class StdinHandler:
    """Handler for processing requests from stdin."""

    def __init__(self):
        """Initialize the stdin handler."""
        self.review_service = ReviewService()

    def read_request_from_stdin(self) -> Optional[Dict[str, Any]]:
        """
        Read a JSON object from stdin. The pipeline agent can spawn this script
        and provide request JSON via stdin.

        Returns:
            Dictionary containing the request data, or None if reading fails
        """
        try:
            raw = sys.stdin.read()
            if not raw or raw.strip() == "":
                return None
            return json.loads(raw)
        except Exception as e:
            print(json.dumps({
                "error": "Failed reading JSON from stdin",
                "exception": str(e)
            }))
            return None

    def process_stdin_request(self):
        """
        Entry point for synchronous execution: reads a single request from stdin,
        processes it and writes the JSON result to stdout.
        Note: Stdin mode doesn't support X-Processing-Token header, uses default token.
        """
        request_data = self.read_request_from_stdin()
        if request_data is None:
            print(json.dumps({
                "error": "No input request provided (expecting JSON on stdin)"
            }))
            return

        try:
            # Convert dict to ReviewRequestDto
            request = ReviewRequestDto(**request_data)
            # Note: No processing token available in stdin mode, will use default
            result = asyncio.run(self.review_service.process_review_request(request, None))
            print(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            print(json.dumps({
                "error": "Failed to process request",
                "exception": str(e)
            }))