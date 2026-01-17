import json
import re
from typing import Any, Dict, List, Union, Optional


class ResponseParser:
    """Parser class for extracting and structuring AI responses."""
    
    # Track if we need LLM retry
    _last_parse_needs_retry = False
    _last_raw_response = None
    
    # Valid issue fields - others will be removed
    VALID_ISSUE_FIELDS = {
    'id', 'severity', 'category', 'file', 'line', 'reason', 
    'suggestedFixDescription', 'suggestedFixDiff', 'isResolved'
    }
    
    # Valid severity values
    VALID_SEVERITIES = {'HIGH', 'MEDIUM', 'LOW'}
    
    # Valid category values  
    VALID_CATEGORIES = {
        'SECURITY', 'PERFORMANCE', 'CODE_QUALITY', 'BUG_RISK', 'STYLE',
        'DOCUMENTATION', 'BEST_PRACTICES', 'ERROR_HANDLING', 'TESTING', 'ARCHITECTURE'
    }

    @staticmethod
    def _normalize_diff(diff_value: Any) -> Optional[str]:
        """
        Normalize a suggestedFixDiff value.
        Handles various formats LLMs might output and ensures proper string format.
        
        Args:
            diff_value: Raw diff value from LLM (string, or None)
            
        Returns:
            Normalized diff string or None if invalid/empty
        """
        if diff_value is None or diff_value == 'null' or diff_value == '':
            return None
            
        if not isinstance(diff_value, str):
            return None
            
        diff = diff_value.strip()
        
        # Remove markdown code block wrappers if present
        if diff.startswith('```'):
            lines = diff.split('\n')
            # Remove first line (```diff or ```)
            if lines:
                lines = lines[1:]
            # Remove last line if it's just ```
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            diff = '\n'.join(lines)
        
        # Validate it looks like a diff (has --- or +++ or @@ markers)
        if not any(marker in diff for marker in ['---', '+++', '@@', '\n-', '\n+']):
            return None
            
        return diff if diff else None

    @staticmethod
    def _clean_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
        """
        Clean and normalize a single issue object.
        - Removes unexpected fields (like 'id')
        - Normalizes suggestedFixDiff format
        - Normalizes severity and category
        
        Args:
            issue: Raw issue dictionary
            
        Returns:
            Cleaned issue dictionary
        """
        if not isinstance(issue, dict):
            return issue
            
        cleaned = {}
        
        for key, value in issue.items():
            # Skip fields not in valid set
            if key not in ResponseParser.VALID_ISSUE_FIELDS:
                continue
                
            # Normalize suggestedFixDiff
            if key == 'suggestedFixDiff':
                normalized_diff = ResponseParser._normalize_diff(value)
                if normalized_diff:
                    cleaned[key] = normalized_diff
                continue
                
            # Normalize severity
            if key == 'severity' and isinstance(value, str):
                value = value.upper()
                if value not in ResponseParser.VALID_SEVERITIES:
                    value = 'MEDIUM'  # Default
                    
            # Normalize category
            if key == 'category' and isinstance(value, str):
                value = value.upper().replace(' ', '_')
                if value not in ResponseParser.VALID_CATEGORIES:
                    value = 'CODE_QUALITY'  # Default
                    
            # Ensure line is string
            if key == 'line':
                value = str(value) if value is not None else '0'
                
            # Ensure isResolved is boolean
            if key == 'isResolved':
                if isinstance(value, str):
                    value = value.lower() == 'true'
                elif not isinstance(value, bool):
                    value = False

            # Ensure id is a string when present (preserve mapping to DB ids)
            if key == 'id':
                try:
                    value = str(value) if value is not None else None
                except Exception:
                    value = None
                    
            cleaned[key] = value
            
        return cleaned

    @staticmethod
    def _normalize_issues(issues: Union[Dict, List, None]) -> List[Dict[str, Any]]:
        """
        Normalize issues field to always be a list.
        Handles cases where AI returns object with numeric keys like {"0": {...}, "1": {...}}
        instead of an array.
        Also cleans each issue to remove unexpected fields.

        Args:
            issues: Issues in various formats (dict with numeric keys, list, or None)

        Returns:
            List of cleaned issue dictionaries
        """
        if issues is None:
            return []

        result = []
        
        if isinstance(issues, list):
            result = issues
        elif isinstance(issues, dict):
            keys = list(issues.keys())
            # Check if all keys are numeric strings
            if keys and all(key.isdigit() or (isinstance(key, str) and key.lstrip('-').isdigit()) for key in keys):
                # Sort by numeric value and convert to list
                sorted_keys = sorted(keys, key=lambda x: int(x))
                result = [issues[k] for k in sorted_keys]
            # If it's a single issue without numeric keys, wrap in list
            elif keys and any(k in issues for k in ['severity', 'file', 'reason', 'category']):
                result = [issues]
        
        # Clean each issue
        return [ResponseParser._clean_issue(issue) for issue in result if isinstance(issue, dict)]

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
    def _fix_unescaped_newlines_in_json(text: str) -> str:
        """
        Fix unescaped newlines inside JSON string values.
        JSON requires newlines in strings to be escaped as \\n.
        LLMs sometimes produce literal newlines in strings, breaking JSON parsing.
        
        Args:
            text: Raw JSON text that may have unescaped newlines in strings
            
        Returns:
            Text with literal newlines in strings replaced with \\n
        """
        if not text:
            return text
        
        result = []
        in_string = False
        i = 0
        
        while i < len(text):
            char = text[i]
            
            # Handle escape sequences
            if char == '\\' and in_string and i + 1 < len(text):
                # This is an escape sequence, keep it as-is
                result.append(char)
                result.append(text[i + 1])
                i += 2
                continue
            
            # Handle quote boundaries
            if char == '"':
                in_string = not in_string
                result.append(char)
                i += 1
                continue
            
            # Handle newlines inside strings
            if in_string and char == '\n':
                result.append('\\n')
                i += 1
                continue
            
            # Handle carriage returns inside strings  
            if in_string and char == '\r':
                result.append('\\r')
                i += 1
                continue
            
            # Handle tabs inside strings
            if in_string and char == '\t':
                result.append('\\t')
                i += 1
                continue
            
            result.append(char)
            i += 1
        
        return ''.join(result)

    @staticmethod
    def _remove_problematic_diffs(text: str) -> str:
        """
        Remove suggestedFixDiff fields that are likely to cause JSON parsing issues.
        This is a last-resort recovery when JSON parsing fails due to unescaped quotes
        in diff content.
        
        Args:
            text: Raw JSON text that failed to parse
            
        Returns:
            Text with suggestedFixDiff fields removed or set to null
        """
        if not text or '"suggestedFixDiff"' not in text:
            return text
            
        # Use regex to find and remove/nullify suggestedFixDiff values
        # Pattern matches: "suggestedFixDiff": "..." or "suggestedFixDiff": null
        # This is intentionally aggressive - we'd rather lose the diff than fail parsing
        
        # First try to nullify - replace value with null
        result = re.sub(
            r'"suggestedFixDiff"\s*:\s*"[^"]*(?:\\.[^"]*)*"',
            '"suggestedFixDiff": null',
            text
        )
        
        # If that doesn't help (unescaped quotes break the regex), 
        # try a more aggressive approach
        if result == text:
            # Find each suggestedFixDiff and try to find its end
            parts = text.split('"suggestedFixDiff"')
            if len(parts) > 1:
                new_parts = [parts[0]]
                for part in parts[1:]:
                    # Skip the value part - find the next field or closing brace
                    # Look for pattern: : "..." , or : "..." }
                    match = re.search(r'^(\s*:\s*)(".*?")(\s*[,}])', part, re.DOTALL)
                    if match:
                        new_parts.append(': null' + match.group(3) + part[match.end():])
                    else:
                        # Can't parse it safely - just set to null and hope for the best
                        colon_pos = part.find(':')
                        if colon_pos >= 0:
                            # Find the next comma or closing brace after a reasonable distance
                            rest = part[colon_pos+1:].lstrip()
                            if rest.startswith('"'):
                                # Find end of this potentially malformed string
                                # Look for ", or "}
                                for end_pattern in ['",', '"}', '"\n']:
                                    end_pos = rest.rfind(end_pattern)
                                    if end_pos > 0:
                                        new_parts.append(': null' + rest[end_pos+1:])
                                        break
                                else:
                                    new_parts.append(part)  # Give up, keep original
                            else:
                                new_parts.append(part)
                        else:
                            new_parts.append(part)
                result = '"suggestedFixDiff"'.join(new_parts)
        
        return result

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
        parse_error = None

        # Pre-process: fix unescaped newlines in JSON strings
        preprocessed_text = ResponseParser._fix_unescaped_newlines_in_json(response_text.strip())

        # Try to parse the entire response as JSON first
        try:
            parsed = json.loads(preprocessed_text)
        except json.JSONDecodeError as e:
            parse_error = str(e)
            # Log the error for debugging
            print(f"Direct JSON parse failed: {e}")
            print(f"Response starts with: {preprocessed_text[:200] if len(preprocessed_text) > 200 else preprocessed_text}")
            
            # Try removing problematic suggestedFixDiff fields and parse again
            try:
                fixed_text = ResponseParser._remove_problematic_diffs(preprocessed_text)
                if fixed_text != preprocessed_text:
                    print("Attempting parse after removing problematic diffs...")
                    parsed = json.loads(fixed_text)
                    print("Parse succeeded after removing problematic diffs")
            except json.JSONDecodeError as e2:
                print(f"JSON parse after diff removal also failed: {e2}")

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
                        block_text = ResponseParser._fix_unescaped_newlines_in_json(match.group(1).strip())
                        parsed = json.loads(block_text)
                        break
                    except json.JSONDecodeError:
                        # Try with diff removal
                        try:
                            fixed_block = ResponseParser._remove_problematic_diffs(block_text)
                            parsed = json.loads(fixed_block)
                            break
                        except json.JSONDecodeError:
                            continue

        # Try nested JSON extraction for complex responses
        if parsed is None:
            parsed = ResponseParser._find_nested_json(preprocessed_text)

        # Validate and normalize the parsed response
        if parsed and isinstance(parsed, dict):
            # Direct match - has comment and issues at top level
            if "comment" in parsed and "issues" in parsed:
                parsed["issues"] = ResponseParser._normalize_issues(parsed.get("issues"))
                ResponseParser._last_parse_needs_retry = False
                return parsed

            # Check if this is a tool output wrapper (e.g., {"tool_*_response": {...}})
            # Search recursively for the actual analysis structure
            nested_analysis = ResponseParser._find_analysis_in_object(parsed)
            if nested_analysis:
                nested_analysis["issues"] = ResponseParser._normalize_issues(nested_analysis.get("issues"))
                ResponseParser._last_parse_needs_retry = False
                return nested_analysis

            # Check if response has tool output keys - indicates incomplete agent response
            tool_keys = [k for k in parsed.keys() if k.startswith("tool_") or "_response" in k]
            if tool_keys:
                # Try to extract any useful analysis from tool outputs
                for key in tool_keys:
                    tool_value = parsed.get(key)
                    if isinstance(tool_value, dict):
                        nested = ResponseParser._find_analysis_in_object(tool_value)
                        if nested:
                            nested["issues"] = ResponseParser._normalize_issues(nested.get("issues"))
                            ResponseParser._last_parse_needs_retry = False
                            return nested
                    elif isinstance(tool_value, str):
                        try:
                            tool_parsed = json.loads(tool_value)
                            if isinstance(tool_parsed, dict):
                                nested = ResponseParser._find_analysis_in_object(tool_parsed)
                                if nested:
                                    nested["issues"] = ResponseParser._normalize_issues(nested.get("issues"))
                                    ResponseParser._last_parse_needs_retry = False
                                    return nested
                        except json.JSONDecodeError:
                            pass
                
                # Tool outputs without valid analysis - mark for retry
                ResponseParser._last_parse_needs_retry = True
                ResponseParser._last_raw_response = response_text
                return {
                    "comment": "AI agent returned tool outputs but did not produce a final analysis. The agent may have reached its step limit (120) before completing the review.",
                    "issues": [
                        {
                            "severity": "HIGH",
                            "category": "ERROR_HANDLING",
                            "file": "system",
                            "line": "0",
                            "reason": "Agent returned intermediate tool results instead of final analysis. This indicates the agent hit its step limit or encountered an error. Try re-running the analysis on a smaller PR or with a more capable model.",
                            "suggestedFixDescription": "Try re-running the analysis or check MCP server logs for errors. Consider using a model with better reasoning capabilities.",
                            "isResolved": False
                        }
                    ],
                    "_needs_retry": True,
                    "_raw_response": response_text
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
                    ResponseParser._last_parse_needs_retry = False
                    return obj

        # Try a more lenient pattern - just find any JSON with comment field
        try:
            # Find the first { and try to extract balanced JSON from there
            all_jsons = ResponseParser._extract_all_json_objects(response_text)
            for obj in all_jsons:
                if isinstance(obj, dict) and "comment" in obj:
                    # Found something with a comment, use it
                    if "issues" not in obj:
                        obj["issues"] = []
                    obj["issues"] = ResponseParser._normalize_issues(obj.get("issues"))
                    ResponseParser._last_parse_needs_retry = False
                    return obj
        except Exception as e:
            print(f"Lenient JSON extraction failed: {e}")

        # Mark that this response needs retry and store the raw response
        ResponseParser._last_parse_needs_retry = True
        ResponseParser._last_raw_response = response_text
        
        # If no valid JSON found, return error structure with parse error details
        truncated_response = response_text[:500] + "..." if len(response_text) > 500 else response_text
        error_detail = f" Parse error: {parse_error}" if parse_error else ""
        return {
            "comment": f"Failed to parse structured response.{error_detail} Raw response: {truncated_response}",
            "issues": [
                {
                    "severity": "HIGH",
                    "category": "ERROR_HANDLING",
                    "file": "unknown",
                    "line": "0",
                    "reason": f"Response parsing failed - invalid JSON format received from AI.{error_detail}",
                    "suggestedFixDescription": "Check AI model configuration and prompt",
                    "isResolved": False
                }
            ],
            "_needs_retry": True,
            "_raw_response": response_text
        }
    
    @staticmethod
    def needs_retry() -> bool:
        """Check if the last parse operation needs a retry with LLM."""
        return ResponseParser._last_parse_needs_retry
    
    @staticmethod
    def get_last_raw_response() -> Optional[str]:
        """Get the last raw response that failed to parse."""
        return ResponseParser._last_raw_response
    
    @staticmethod
    def reset_retry_state():
        """Reset the retry tracking state."""
        ResponseParser._last_parse_needs_retry = False
        ResponseParser._last_raw_response = None
    
    @staticmethod
    def get_fix_prompt(raw_response: str) -> str:
        """
        Generate a prompt to ask an LLM to fix/extract the JSON from a raw response.
        
        Args:
            raw_response: The raw response that failed to parse
            
        Returns:
            A prompt string for the fixing LLM
        """
        return f"""You are a JSON extraction assistant. The following text contains a code review response that should be valid JSON but failed to parse.

Your task is to extract and return ONLY a valid JSON object with exactly this structure:
{{
  "comment": "Summary of the code review findings",
  "issues": [
    {{
      "severity": "HIGH|MEDIUM|LOW|INFO",
      "category": "SECURITY|PERFORMANCE|CODE_QUALITY|BUG_RISK|STYLE|DOCUMENTATION|BEST_PRACTICES|ERROR_HANDLING|TESTING|ARCHITECTURE",
      "file": "file-path",
      "line": "line-number",
      "reason": "Explanation of the issue",
      "suggestedFixDescription": "Fix suggestion",
      "suggestedFixDiff": "Optional unified diff format showing the fix",
      "isResolved": false
    }}
  ]
}}

Rules:
1. Extract the actual code review content from the text
2. Return ONLY valid JSON - no markdown, no explanations, no extra text
3. If the text contains valid JSON already, clean it up and return it
4. If issues are empty or missing, use an empty array []
5. Ensure all string values are properly escaped
6. The "issues" field MUST be an array, not an object
7. suggestedFixDiff is optional but should be included if a code diff is present in the original

Raw response to fix:
{raw_response}

Return ONLY the JSON object:"""

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