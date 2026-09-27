"""Adapt provider responses and validate user-visible command results.

This boundary is shared by the agent service and queue handler so accepted
text and empty-result semantics do not drift between transports.
"""
import logging
import re
from typing import Any, Dict, Optional

from model.output_schemas import SummarizeOutput, AskOutput
from utils.llm_json import extract_json_object, parse_json_object as parse_json_response

logger = logging.getLogger(__name__)

EMPTY_RESULT_SENTINELS = {
    "null",
    "none",
    "no output generated",
    "failed to generate summary",
    "i couldn't generate an answer. please try rephrasing your question.",
}

def normalize_summarize_result(result: Any, supports_mermaid: bool) -> Dict[str, Any]:
    """Validate summarize output before the queue consumer publishes a final event."""
    if not isinstance(result, dict):
        return {"error": "AI service returned an invalid summarize result"}
    if result.get("error"):
        return {"error": str(result["error"])}

    summary = result.get("summary")
    if not has_usable_text(summary):
        return {"error": "AI service returned an empty summary"}

    diagram_type = result.get("diagramType") or ("MERMAID" if supports_mermaid else "ASCII")
    return {
        "summary": str(summary),
        "diagram": string_or_empty(result.get("diagram")),
        "diagramType": str(diagram_type),
    }


def normalize_ask_result(result: Any) -> Dict[str, Any]:
    """Validate ask output before the queue consumer publishes a final event."""
    if not isinstance(result, dict):
        return {"error": "AI service returned an invalid ask result"}
    if result.get("error"):
        return {"error": str(result["error"])}

    answer = result.get("answer")
    if not has_usable_text(answer):
        return {"error": "AI service returned an empty answer"}

    return {"answer": str(answer)}


def has_usable_text(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in EMPTY_RESULT_SENTINELS


def string_or_empty(value: Any) -> str:
    return "" if value is None else str(value)


def coerce_summarize_final_result(
        final_result: Any,
        supports_mermaid: bool
) -> Dict[str, Any]:
    """Convert structured, dict, message, or text agent output into a summary dict."""
    diagram_type = "MERMAID" if supports_mermaid else "ASCII"

    if isinstance(final_result, SummarizeOutput):
        logger.info("Successfully received structured summarize output")
        return summary_or_empty_error(
            summary=final_result.summary,
            diagram=final_result.diagram,
            diagram_type=final_result.diagramType or diagram_type,
        )

    if isinstance(final_result, dict) and "summary" in final_result:
        return summary_or_empty_error(
            summary=final_result.get("summary"),
            diagram=final_result.get("diagram"),
            diagram_type=final_result.get("diagramType") or diagram_type,
        )

    text = extract_agent_item_text(final_result)
    if not has_usable_text(text):
        return {"error": "AI service returned an empty summary"}

    logger.debug(f"Summarize raw result (first 500 chars): {str(text)[:500] if text else 'None'}")
    parsed = parse_json_response(str(text))
    if parsed:
        logger.info("Successfully parsed JSON response for summarize")
        return summary_or_empty_error(
            summary=parsed.get("summary"),
            diagram=parsed.get("diagram"),
            diagram_type=parsed.get("diagramType") or diagram_type,
        )

    extracted = extract_summary_field_fallback(str(text))
    if extracted:
        logger.warning("Used regex fallback to extract summary field")
        return summary_or_empty_error(
            summary=extracted,
            diagram="",
            diagram_type=diagram_type,
        )

    logger.warning("JSON parsing failed for summarize, using raw result")
    return summary_or_empty_error(
        summary=str(text),
        diagram="",
        diagram_type=diagram_type,
    )


def summary_or_empty_error(
        summary: Any,
        diagram: Any,
        diagram_type: Any
) -> Dict[str, Any]:
    if not has_usable_text(summary):
        return {"error": "AI service returned an empty summary"}
    return {
        "summary": str(summary),
        "diagram": string_or_empty(diagram),
        "diagramType": str(diagram_type or "ASCII"),
    }


def extract_summary_field_fallback(text: str) -> Optional[str]:
    """
    Fallback extraction of summary field when JSON parsing fails.
    Tries to extract the content between "summary": " and the closing quote.
    """
    if not text:
        return None

    import re

    # Look for "summary": "..." pattern
    # This pattern handles multi-line strings and escaped quotes
    pattern = r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"|\'summary\'\s*:\s*\'((?:[^\'\\]|\\.)*)\''
    match = re.search(pattern, text, re.DOTALL)
    if match:
        # Get the captured group (either double or single quoted)
        content = match.group(1) or match.group(2)
        if content:
            # Unescape common JSON escapes
            content = content.replace('\\"', '"')
            content = content.replace('\\n', '\n')
            content = content.replace('\\t', '\t')
            content = content.replace('\\\\', '\\')
            return content

    return None


def coerce_ask_final_result(final_result: Any) -> Dict[str, Any]:
    """Convert structured, dict, message, or text agent output into an answer dict."""
    if isinstance(final_result, AskOutput):
        logger.info("Successfully received structured ask output")
        return answer_or_empty_error(final_result.answer)

    if isinstance(final_result, dict) and "answer" in final_result:
        return answer_or_empty_error(final_result.get("answer"))

    text = extract_agent_item_text(final_result)
    if not has_usable_text(text):
        return {"error": "AI service returned an empty answer"}

    parsed = parse_json_response(str(text))
    if parsed and "answer" in parsed:
        return answer_or_empty_error(parsed.get("answer"))

    return {"answer": str(text)}


def answer_or_empty_error(answer: Any) -> Dict[str, Any]:
    if not has_usable_text(answer):
        return {"error": "AI service returned an empty answer"}
    return {"answer": str(answer)}


def extract_agent_item_text(item: Any) -> Optional[str]:
    """Extract final text from common LangChain/mcp_use stream item shapes."""
    if item is None:
        return None

    if isinstance(item, str):
        return item

    if isinstance(item, (list, tuple)):
        return coerce_text_content(item)

    if isinstance(item, dict):
        for key in ("answer", "output", "final_output", "response", "result", "content", "text"):
            if key in item:
                return extract_agent_item_text(item.get(key))

        messages = item.get("messages")
        if isinstance(messages, list) and messages:
            return extract_agent_item_text(messages[-1])

        return None

    if hasattr(item, "content"):
        return coerce_text_content(getattr(item, "content"))

    if hasattr(item, "model_dump"):
        try:
            dumped = item.model_dump()
            if isinstance(dumped, dict):
                return extract_agent_item_text(dumped)
        except Exception:
            return None

    return None


def coerce_text_content(content: Any) -> str:
    """Convert provider content blocks to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if text is not None:
                    parts.append(str(text))
            elif hasattr(block, "text"):
                parts.append(str(block.text))
        return "".join(parts)
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        return "" if text is None else str(text)
    return str(content)
