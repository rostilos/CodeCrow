import json
import re
from typing import Any, Dict, List, Union


class ResponseParser:
    """Parser class for extracting and structuring AI responses."""

    @staticmethod
    def _normalize_issues(issues: Union[Dict, List, None]) -> List[Dict[str, Any]]:
        """
        Normalize issues field to always be a list.
        Handles cases where AI returns object with numeric keys like {"0": {...}, "1": {...}}
        instead of an array.

        Args:
            issues: Issues in various formats (dict with numeric keys, list, or None)

        Returns:
            List of issue dictionaries
        """
        if issues is None:
            return []

        if isinstance(issues, list):
            return issues

        if isinstance(issues, dict):
            keys = list(issues.keys())
            # Check if all keys are numeric strings
            if keys and all(key.isdigit() or (isinstance(key, str) and key.lstrip('-').isdigit()) for key in keys):
                # Sort by numeric value and convert to list
                sorted_keys = sorted(keys, key=lambda x: int(x))
                return [issues[k] for k in sorted_keys]
            # If it's a single issue without numeric keys, wrap in list
            if keys and any(k in issues for k in ['severity', 'file', 'reason', 'category']):
                return [issues]

        return []

    @staticmethod
    def _find_nested_json(text: str) -> Dict[str, Any]:
        """
        Find and parse nested JSON structures that may contain balanced braces.
        Uses bracket counting to find complete JSON objects.

        Args:
            text: Text that may contain JSON

        Returns:
            Parsed JSON or None if not found
        """
        start_idx = text.find('{')
        if start_idx == -1:
            return None

        brace_count = 0
        in_string = False
        escape_next = False

        for i, char in enumerate(text[start_idx:], start_idx):
            if escape_next:
                escape_next = False
                continue

            if char == '\\' and in_string:
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_str = text[start_idx:i + 1]
                        try:
                            return json.loads(json_str)
                        except json.JSONDecodeError:
                            # Try to find another JSON object
                            remaining = text[i + 1:]
                            return ResponseParser._find_nested_json(remaining)

        return None

    @staticmethod
    def extract_json_from_response(response_text: str) -> Dict[str, Any]:
        """
        Extract and parse JSON from the AI response.
        Tries to find JSON even if wrapped in markdown or other text.
        Normalizes issues field from object-indexed format to array format.

        Args:
            response_text: Raw response text from the AI model

        Returns:
            Parsed JSON dictionary with comment and issues fields (issues as list)
        """
        if not response_text:
            return {"comment": "Empty response received", "issues": []}

        parsed = None

        # Try to parse the entire response as JSON first
        try:
            parsed = json.loads(response_text.strip())
        except json.JSONDecodeError:
            pass

        # If direct parsing failed, try to extract from code blocks
        if parsed is None:
            # Remove markdown code blocks wrapper
            code_block_patterns = [
                r'```json\s*([\s\S]*?)\s*```',
                r'```\s*([\s\S]*?)\s*```',
            ]
            for pattern in code_block_patterns:
                match = re.search(pattern, response_text, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group(1).strip())
                        break
                    except json.JSONDecodeError:
                        continue

        # Try nested JSON extraction for complex responses
        if parsed is None:
            parsed = ResponseParser._find_nested_json(response_text)

        # Validate and normalize the parsed response
        if parsed and isinstance(parsed, dict):
            if "comment" in parsed and "issues" in parsed:
                # Normalize issues to list format
                parsed["issues"] = ResponseParser._normalize_issues(parsed.get("issues"))
                return parsed

        # If no valid JSON found, return error structure
        # Truncate raw response for the error message to avoid huge payloads
        truncated_response = response_text[:500] + "..." if len(response_text) > 500 else response_text
        return {
            "comment": f"Failed to parse structured response. Raw response: {truncated_response}",
            "issues": [
                {
                    "severity": "HIGH",
                    "category": "ERROR_HANDLING",
                    "file": "unknown",
                    "line": "0",
                    "reason": "Response parsing failed - invalid JSON format received from AI",
                    "suggestedFixDescription": "Check AI model configuration and prompt",
                    "isResolved": False
                }
            ]
        }

    @staticmethod
    def create_error_response(error_message: str, exception_str: str = "") -> Dict[str, Any]:
        """
        Create a standardized error response structure.

        Args:
            error_message: Main error message
            exception_str: Optional exception details

        Returns:
            Structured error response dictionary with issues as list
        """
        full_message = f"{error_message}: {exception_str}" if exception_str else error_message

        return {
            "comment": full_message,
            "issues": [
                {
                    "severity": "HIGH",
                    "category": "ERROR_HANDLING",
                    "file": "system",
                    "line": "0",
                    "reason": full_message,
                    "suggestedFixDescription": "Check system configuration and connectivity",
                    "isResolved": False
                }
            ]
        }