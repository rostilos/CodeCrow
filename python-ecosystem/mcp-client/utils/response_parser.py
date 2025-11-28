import json
import re
from typing import Any, Dict


class ResponseParser:
    """Parser class for extracting and structuring AI responses."""

    @staticmethod
    def extract_json_from_response(response_text: str) -> Dict[str, Any]:
        """
        Extract and parse JSON from the AI response.
        Tries to find JSON even if wrapped in markdown or other text.

        Args:
            response_text: Raw response text from the AI model

        Returns:
            Parsed JSON dictionary with comment and issues fields
        """
        if not response_text:
            return {"comment": "Empty response received", "issues": {}}

        # Try to parse the entire response as JSON first
        try:
            return json.loads(response_text.strip())
        except json.JSONDecodeError:
            pass

        # Look for JSON wrapped in code blocks or other text
        json_patterns = [
            r'```json\s*(\{.*?\})\s*```',  # JSON in code blocks
            r'```\s*(\{.*?\})\s*```',  # JSON in generic code blocks
            r'(\{[^{}]*"comment"[^{}]*"issues"[^{}]*\})',  # Simple pattern for our expected structure
            r'(\{.*?\})'  # Any JSON-like structure
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, response_text, re.DOTALL | re.IGNORECASE)
            for match in matches:
                try:
                    parsed = json.loads(match)
                    # Validate it has our expected structure
                    if isinstance(parsed, dict) and "comment" in parsed and "issues" in parsed:
                        return parsed
                except json.JSONDecodeError:
                    continue

        # If no valid JSON found, return error structure
        return {
            "comment": f"Failed to parse structured response. Raw response: {response_text}...",
            "issues": {
                "0": {
                    "severity": "HIGH",
                    "file": "unknown",
                    "line": "0",
                    "reason": "Response parsing failed - invalid JSON format received from AI",
                    "suggestedFix": "Check AI model configuration and prompt",
                    "error": "error"
                }
            }
        }

    @staticmethod
    def create_error_response(error_message: str, exception_str: str = "") -> Dict[str, Any]:
        """
        Create a standardized error response structure.

        Args:
            error_message: Main error message
            exception_str: Optional exception details

        Returns:
            Structured error response dictionary
        """
        full_message = f"{error_message}: {exception_str}" if exception_str else error_message

        return {
            "comment": full_message,
            "issues": {
                "0": {
                    "severity": "HIGH",
                    "file": "system",
                    "line": "0",
                    "reason": full_message,
                    "suggestedFix": "Check system configuration and connectivity"
                }
            }
        }