import json
import re
from typing import Any, Dict, List, Union, Optional


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
    def _find_analysis_in_object(obj: Any, depth: int = 0) -> Optional[Dict[str, Any]]:
        """
        Recursively search for a valid analysis structure (comment + issues) in a nested object.
        Handles cases where AI returns tool outputs wrapping the actual response.

        Args:
            obj: Object to search through
            depth: Current recursion depth (max 10)

        Returns:
            Dict with comment and issues if found, None otherwise
        """
        if depth > 10:
            return None

        if not isinstance(obj, dict):
            return None

        # Check if this object itself has the required structure
        if "comment" in obj and "issues" in obj:
            return obj

        # Search in nested values
        for key, value in obj.items():
            if isinstance(value, dict):
                result = ResponseParser._find_analysis_in_object(value, depth + 1)
                if result:
                    return result
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        result = ResponseParser._find_analysis_in_object(item, depth + 1)
                        if result:
                            return result
            elif isinstance(value, str):
                # Try to parse string values as JSON (sometimes nested JSON is stringified)
                try:
                    parsed_value = json.loads(value)
                    if isinstance(parsed_value, dict):
                        result = ResponseParser._find_analysis_in_object(parsed_value, depth + 1)
                        if result:
                            return result
                except (json.JSONDecodeError, TypeError):
                    pass

        return None

    @staticmethod
    def _find_nested_json(text: str) -> Optional[Dict[str, Any]]:
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
        Handles cases where AI returns tool outputs wrapping the actual response.
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
            # Direct match - has comment and issues at top level
            if "comment" in parsed and "issues" in parsed:
                parsed["issues"] = ResponseParser._normalize_issues(parsed.get("issues"))
                return parsed

            # Check if this is a tool output wrapper (e.g., {"tool_*_response": {...}})
            # Search recursively for the actual analysis structure
            nested_analysis = ResponseParser._find_analysis_in_object(parsed)
            if nested_analysis:
                nested_analysis["issues"] = ResponseParser._normalize_issues(nested_analysis.get("issues"))
                return nested_analysis

            # Check if response has tool output keys - indicates incomplete agent response
            tool_keys = [k for k in parsed.keys() if k.startswith("tool_") or "_response" in k]
            if tool_keys:
                return {
                    "comment": "AI agent returned tool outputs but did not produce a final analysis. The agent may have failed to complete the review process.",
                    "issues": [
                        {
                            "severity": "HIGH",
                            "category": "ERROR_HANDLING",
                            "file": "system",
                            "line": "0",
                            "reason": "Agent returned intermediate tool results instead of final analysis. This usually indicates the agent reached its step limit or encountered an error during processing.",
                            "suggestedFixDescription": "Try re-running the analysis or check MCP server logs for errors",
                            "isResolved": False
                        }
                    ]
                }

        # Last resort: try to find JSON with comment/issues pattern in raw text using regex
        # This handles cases where JSON is embedded in other text
        comment_issues_pattern = r'\{\s*"comment"\s*:\s*"[^"]*"[^{}]*"issues"\s*:\s*[\[{]'
        if re.search(comment_issues_pattern, response_text):
            # There's likely a valid structure, try to extract it
            all_jsons = ResponseParser._extract_all_json_objects(response_text)
            for obj in all_jsons:
                if isinstance(obj, dict) and "comment" in obj and "issues" in obj:
                    obj["issues"] = ResponseParser._normalize_issues(obj.get("issues"))
                    return obj

        # If no valid JSON found, return error structure
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
    def _extract_all_json_objects(text: str) -> List[Dict[str, Any]]:
        """
        Extract all valid JSON objects from text.

        Args:
            text: Text containing JSON objects

        Returns:
            List of parsed JSON objects
        """
        results = []
        i = 0
        while i < len(text):
            if text[i] == '{':
                # Try to extract JSON starting here
                brace_count = 0
                in_string = False
                escape_next = False
                start = i

                for j in range(i, len(text)):
                    char = text[j]

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
                                try:
                                    obj = json.loads(text[start:j + 1])
                                    results.append(obj)
                                except json.JSONDecodeError:
                                    pass
                                i = j
                                break
                else:
                    # Unclosed brace, move on
                    pass
            i += 1

        return results

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