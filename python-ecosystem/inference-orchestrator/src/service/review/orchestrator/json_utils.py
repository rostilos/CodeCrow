"""
JSON parsing, repair, and cleaning utilities for LLM responses.
"""
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from utils.llm_delegate import llm_class_names
from utils.llm_response import extract_llm_response_text
from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs
from service.review.orchestrator.structured_output import (
    StructuredOutputInvocation,
    extract_structured_payload_text,
    invoke_structured_output,
)

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


STRUCTURED_OUTPUT_ENABLED = _env_bool("REVIEW_STRUCTURED_OUTPUT_ENABLED", True)
CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED = _env_bool("REVIEW_CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED", False)
JSON_REPAIR_INPUT_TOKEN_TARGET = max(
    10_000,
    _env_int(
        "REVIEW_JSON_REPAIR_INPUT_TOKEN_TARGET",
        _env_int("REVIEW_STAGE1_BATCH_TOKEN_BUDGET", 60_000),
    ),
)
_JSON_REPAIR_ESTIMATOR_SAFETY_TOKENS = 256


class JsonRepairInputTooLarge(ValueError):
    """The complete malformed result cannot be safely sent for model repair."""


def _json_request_tokens(prompt: str, schema: Any) -> int:
    """Conservatively estimate a repair request without clipping either input."""
    schema_bytes = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    request_bytes = len(prompt.encode("utf-8")) + len(schema_bytes)
    return max(
        1,
        (request_bytes + 2) // 3 + _JSON_REPAIR_ESTIMATOR_SAFETY_TOKENS,
    )


def _require_repair_request_fit(
    prompt: str,
    schema: Any,
    input_token_target: int,
) -> None:
    estimated_tokens = _json_request_tokens(prompt, schema)
    if estimated_tokens <= input_token_target:
        return
    raise JsonRepairInputTooLarge(
        "complete malformed JSON repair input exceeds the semantic request "
        f"target ({estimated_tokens} estimated tokens > "
        f"{input_token_target}); refusing to slice or partially repair it"
    )


def supports_structured_output(llm) -> bool:
    if not STRUCTURED_OUTPUT_ENABLED:
        return False
    if CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED:
        return True

    class_names = llm_class_names(llm)
    if "ChatCloudflareOpenAI" in class_names:
        return False
    return True


async def resolve_structured_output(
    invocation: StructuredOutputInvocation,
    model_class: Any,
    llm: Any,
) -> Any:
    """Validate a structured result or recover JSON from the same response.

    LangChain can fail its first schema validation while still returning valid
    tool arguments or JSON content.  Recovering that payload locally avoids an
    unnecessary provider call and keeps the existing retry budget intact.
    """

    parsed_error: Optional[BaseException] = None
    if invocation.parsed is not None:
        try:
            if isinstance(invocation.parsed, model_class):
                return invocation.parsed
            return model_class.model_validate(invocation.parsed)
        except (TypeError, ValueError, AttributeError) as exc:
            parsed_error = exc

    payload = extract_structured_payload_text(invocation)
    if payload.strip():
        return await parse_llm_response(
            payload,
            model_class,
            llm,
            max_provider_repairs=0,
        )

    failure = invocation.parsing_error or parsed_error
    if failure is not None:
        raise ValueError(
            f"Structured {model_class.__name__} response could not be parsed: "
            f"{type(failure).__name__}"
        ) from failure
    raise ValueError(
        f"Structured {model_class.__name__} response contained no parsed value "
        "or recoverable JSON payload"
    )


async def parse_llm_response(
    content: str,
    model_class: Any,
    llm,
    retries: int = 2,
    *,
    input_token_target: Optional[int] = None,
    max_provider_repairs: Optional[int] = None,
) -> Any:
    """
    Parse JSON locally, with an explicit shared budget for optional provider repair.

    ``max_provider_repairs=0`` guarantees that malformed output never triggers
    a nested model call.  ``None`` preserves the legacy structured retry plus
    ``retries`` repair calls for standalone callers that do not own a call budget.
    """
    last_error = None
    
    # Initial cleaning attempt
    try:
        cleaned, data = load_json_with_local_repairs(content)
        logger.debug(f"Cleaned JSON for {model_class.__name__} (first 500 chars): {cleaned[:500]}")
        return model_class(**data)
    except Exception as e:
        last_error = e
        logger.warning(f"Initial parse failed for {model_class.__name__}: {e}")
        logger.debug(f"Raw content (first 1000 chars): {content[:1000]}")

    if max_provider_repairs is None:
        provider_repairs_remaining = 1 + max(0, retries)
    else:
        provider_repairs_remaining = max(0, int(max_provider_repairs))
    if provider_repairs_remaining == 0:
        raise ValueError(
            f"Failed to parse {model_class.__name__} locally: {last_error}"
        )

    repair_target = max(
        1_000,
        input_token_target or JSON_REPAIR_INPUT_TOKEN_TARGET,
    )
    schema = model_class.model_json_schema()

    # Retry with structured output if available and known to be supported.
    if provider_repairs_remaining and supports_structured_output(llm):
        structured_attempt_consumed = False
        try:
            logger.info(f"Attempting structured output retry for {model_class.__name__}")
            retry_prompt = (
                f"Parse and return this as valid {model_class.__name__}:\n{content}"
            )
            _require_repair_request_fit(retry_prompt, schema, repair_target)
            structured_attempt_consumed = True
            invocation = await invoke_structured_output(
                llm,
                retry_prompt,
                model_class,
                effort=ReasoningEffort.NONE,
                label=f"json-repair-{model_class.__name__}",
            )
            result = await resolve_structured_output(
                invocation,
                model_class,
                llm,
            )
            logger.info(f"Structured output retry succeeded for {model_class.__name__}")
            return result
        except JsonRepairInputTooLarge as e:
            logger.warning("Structured JSON retry skipped atomically: %s", e)
            raise ValueError(
                f"Failed to parse {model_class.__name__}: {e}"
            ) from e
        except Exception as e:
            logger.warning(f"Structured output retry failed: {e}")
            last_error = e
        finally:
            if structured_attempt_consumed:
                provider_repairs_remaining = max(
                    0,
                    provider_repairs_remaining - 1,
                )
    else:
        logger.info("Structured output retry skipped for %s", model_class.__name__)

    # Final fallback: LLM repair loop
    repair_attempts = min(max(0, retries), provider_repairs_remaining)
    for attempt in range(repair_attempts):
        try:
            logger.info(f"Repairing JSON for {model_class.__name__}, attempt {attempt+1}")
            repaired = await repair_json_with_llm(
                llm,
                content, 
                str(last_error), 
                schema,
                input_token_target=repair_target,
            )
            cleaned, data = load_json_with_local_repairs(repaired)
            logger.debug(f"Repaired JSON attempt {attempt+1} (first 500 chars): {cleaned[:500]}")
            return model_class(**data)
        except JsonRepairInputTooLarge as e:
            logger.warning("JSON repair skipped atomically: %s", e)
            raise ValueError(
                f"Failed to parse {model_class.__name__}: {e}"
            ) from e
        except Exception as e:
            last_error = e
            logger.warning(f"Retry {attempt+1} failed: {e}")
    
    raise ValueError(f"Failed to parse {model_class.__name__} after retries: {last_error}")


def _build_json_repair_prompt(
    broken_json: str,
    error: str,
    schema: Any,
) -> str:
    return f"""You are a JSON repair expert.
The following JSON failed to parse/validate:
Error: {error}

Broken JSON:
{broken_json}

Required Schema (the output MUST be a JSON object, not an array):
{json.dumps(schema, indent=2)}

CRITICAL INSTRUCTIONS:
1. Return ONLY the fixed valid JSON object
2. The response MUST start with {{ and end with }}
3. All property names MUST be enclosed in double quotes
4. No markdown code blocks (no ```)
5. No explanatory text before or after the JSON
6. Ensure all required fields from the schema are present

Output the corrected JSON object now:"""


async def repair_json_with_llm(
    llm,
    broken_json: str,
    error: str,
    schema: Any,
    *,
    input_token_target: Optional[int] = None,
) -> str:
    """
    Ask LLM to repair malformed JSON.
    """

    prompt = _build_json_repair_prompt(broken_json, error, schema)
    _require_repair_request_fit(
        prompt,
        schema,
        max(1_000, input_token_target or JSON_REPAIR_INPUT_TOKEN_TARGET),
    )
    response = await llm.ainvoke(
        prompt,
        **reasoning_request_kwargs(llm, ReasoningEffort.NONE),
    )
    return extract_llm_response_text(response)


def load_json_with_local_repairs(text: str) -> tuple[str, Any]:
    """Parse JSON after cheap deterministic cleanup, before asking an LLM to repair it."""
    stripped = text.strip()
    try:
        return stripped, json.loads(stripped)
    except Exception:
        # Cleanup is intentionally a fallback: valid JSON strings may contain
        # markdown fences as field content and must not be reinterpreted as an
        # outer response wrapper.
        pass

    cleaned = clean_json_text(text)
    candidates = [
        cleaned,
        _remove_trailing_commas(cleaned),
        _escape_newlines_in_strings(cleaned),
        _escape_newlines_in_strings(_remove_trailing_commas(cleaned)),
    ]

    last_error = None
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return candidate, json.loads(candidate)
        except Exception as exc:
            last_error = exc

    raise last_error or ValueError("No JSON content found")


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _escape_newlines_in_strings(text: str) -> str:
    result = []
    in_string = False
    escaped = False

    for char in text:
        if escaped:
            result.append(char)
            escaped = False
            continue

        if char == "\\":
            result.append(char)
            escaped = True
            continue

        if char == '"':
            in_string = not in_string
            result.append(char)
            continue

        if in_string and char in {"\n", "\r"}:
            result.append("\\n")
            continue

        result.append(char)

    return "".join(result)


def clean_json_text(text: str) -> str:
    """
    Clean markdown and extraneous text from JSON.
    """
    text = text.strip()
    
    # Remove markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        # Skip the opening ``` line (with or without language identifier)
        lines = lines[1:]
        # Remove trailing ``` if present
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    
    # Also handle case where ``` appears mid-text
    if "```json" in text:
        start_idx = text.find("```json")
        end_idx = text.find("```", start_idx + 7)
        if end_idx != -1:
            text = text[start_idx + 7:end_idx].strip()
        else:
            text = text[start_idx + 7:].strip()
    elif "```" in text:
        # Generic code block without language
        start_idx = text.find("```")
        remaining = text[start_idx + 3:]
        end_idx = remaining.find("```")
        if end_idx != -1:
            text = remaining[:end_idx].strip()
        else:
            text = remaining.strip()
    
    # Find JSON object boundaries
    obj_start = text.find("{")
    obj_end = text.rfind("}")
    arr_start = text.find("[")
    arr_end = text.rfind("]")
    
    # Determine if we have an object or array (whichever comes first)
    if obj_start != -1 and obj_end != -1:
        if arr_start == -1 or obj_start < arr_start:
            # Object comes first or no array
            text = text[obj_start:obj_end+1]
        elif arr_start < obj_start and arr_end != -1:
            # Array comes first - but we need an object for Pydantic
            # Check if the object is nested inside the array or separate
            if obj_end > arr_end:
                # Object extends beyond array - likely the object we want
                text = text[obj_start:obj_end+1]
            else:
                # Try to use the object anyway
                text = text[obj_start:obj_end+1]
    elif arr_start != -1 and arr_end != -1 and obj_start == -1:
        # Only array found - log warning as Pydantic models expect objects
        logger.warning(f"JSON cleaning found array instead of object, this may fail parsing")
        text = text[arr_start:arr_end+1]
    
    return text
