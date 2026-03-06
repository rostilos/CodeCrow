"""
JSON parsing, repair, and cleaning utilities for LLM responses.
"""
import json
import logging
from typing import Any, Dict, Optional

from service.review.orchestrator.agents import extract_llm_response_text

logger = logging.getLogger(__name__)


async def parse_llm_response(content: str, model_class: Any, llm, retries: int = 2) -> Any:
    """
    Robustly parse JSON response into a Pydantic model with retries.
    Falls back to manual parsing if structured output wasn't used.
    """
    last_error = None
    
    # Initial cleaning attempt
    try:
        cleaned = clean_json_text(content)
        logger.debug(f"Cleaned JSON for {model_class.__name__} (first 500 chars): {cleaned[:500]}")
        data = json.loads(cleaned)
        return model_class(**data)
    except Exception as e:
        last_error = e
        logger.warning(f"Initial parse failed for {model_class.__name__}: {e}")
        logger.debug(f"Raw content (first 1000 chars): {content[:1000]}")

    # Retry with structured output if available
    try:
        logger.info(f"Attempting structured output retry for {model_class.__name__}")
        structured_llm = llm.with_structured_output(model_class)
        result = await structured_llm.ainvoke(
            f"Parse and return this as valid {model_class.__name__}:\n{content[:4000]}"
        )
        if result:
            logger.info(f"Structured output retry succeeded for {model_class.__name__}")
            return result
    except Exception as e:
        logger.warning(f"Structured output retry failed: {e}")
        last_error = e

    # Final fallback: LLM repair loop
    for attempt in range(retries):
        try:
            logger.info(f"Repairing JSON for {model_class.__name__}, attempt {attempt+1}")
            repaired = await repair_json_with_llm(
                llm,
                content, 
                str(last_error), 
                model_class.model_json_schema()
            )
            cleaned = clean_json_text(repaired)
            logger.debug(f"Repaired JSON attempt {attempt+1} (first 500 chars): {cleaned[:500]}")
            data = json.loads(cleaned)
            return model_class(**data)
        except Exception as e:
            last_error = e
            logger.warning(f"Retry {attempt+1} failed: {e}")
    
    raise ValueError(f"Failed to parse {model_class.__name__} after retries: {last_error}")


async def repair_json_with_llm(llm, broken_json: str, error: str, schema: Any) -> str:
    """
    Ask LLM to repair malformed JSON.
    """
    # Truncate the broken JSON to avoid token limits but show enough context
    truncated_json = broken_json[:3000] if len(broken_json) > 3000 else broken_json
    
    prompt = f"""You are a JSON repair expert. 
The following JSON failed to parse/validate:
Error: {error}

Broken JSON:
{truncated_json}

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
    response = await llm.ainvoke(prompt)
    return extract_llm_response_text(response)


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
