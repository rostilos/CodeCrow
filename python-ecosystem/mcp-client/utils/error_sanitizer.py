"""
Utility for sanitizing error messages before displaying to users.
Removes sensitive technical details like API keys, quotas, and internal stack traces.
"""

import re
import logging

logger = logging.getLogger(__name__)


def sanitize_error_for_display(error_message: str) -> str:
    """
    Sanitize error messages for user display.
    Removes sensitive technical details and provides user-friendly messages.
    
    Args:
        error_message: The raw error message
        
    Returns:
        A sanitized, user-friendly error message
    """
    if not error_message:
        return "An unexpected error occurred during processing."
    
    error_lower = error_message.lower()
    
    # AI provider quota/rate limit errors
    if any(term in error_lower for term in ["quota", "rate limit", "rate_limit", "429", "exceeded", "too many requests"]):
        return (
            "The AI provider is currently rate-limited or quota has been exceeded. "
            "Please try again later or contact your administrator to check the AI connection settings."
        )
    
    # Authentication/API key errors
    if any(term in error_lower for term in ["401", "403", "unauthorized", "authentication", 
                                            "api key", "apikey", "invalid_api_key", "invalid key"]):
        return (
            "AI provider authentication failed. "
            "Please contact your administrator to verify the AI connection configuration."
        )
    
    # Model not found/invalid
    if "model" in error_lower and any(term in error_lower for term in ["not found", "invalid", "does not exist", "unavailable"]):
        return (
            "The configured AI model is not available. "
            "Please contact your administrator to update the AI connection settings."
        )
    
    # Unsupported model errors (from LLMFactory)
    if "unsupported" in error_lower and "model" in error_lower:
        # Extract the alternative model suggestion if present
        if "instead, such as" in error_lower:
            try:
                # Try to extract the suggested alternative
                match = re.search(r"such as ['\"]?([^'\"]+)['\"]?", error_message, re.IGNORECASE)
                if match:
                    alternative = match.group(1).strip().rstrip(".")
                    return (
                        f"The selected AI model is not supported for this operation. "
                        f"Please use an alternative model such as '{alternative}'."
                    )
            except Exception:
                pass
        return (
            "The selected AI model is not supported for this operation. "
            "Please contact your administrator to select a compatible model."
        )
    
    # Token limit errors
    if "token" in error_lower and any(term in error_lower for term in ["limit", "too long", "maximum", "exceeded", "context"]):
        return (
            "The PR content exceeds the AI model's token limit. "
            "Consider breaking down large PRs or adjusting the token limitation setting."
        )
    
    # Network/connectivity errors
    if any(term in error_lower for term in ["connection", "timeout", "network", "unreachable", 
                                            "connection refused", "connection reset"]):
        return (
            "Failed to connect to the AI provider. "
            "Please try again later."
        )
    
    # Content filter/safety errors
    if any(term in error_lower for term in ["content filter", "safety", "blocked", "harmful", "policy"]):
        return (
            "The AI provider's content filter blocked this request. "
            "Please review the PR content or try a different model."
        )
    
    # Thought signature errors (Gemini thinking models)
    if "thought_signature" in error_lower or "thinking" in error_lower:
        return (
            "This AI model is not compatible with CodeCrow's tool calling feature. "
            "Please use a non-thinking model variant (e.g., gemini-2.5-flash instead of gemini-2.5-pro)."
        )
    
    # MCP/tool errors
    if any(term in error_lower for term in ["mcp", "tool call", "tool_call"]):
        return (
            "An error occurred while executing analysis tools. "
            "Please try again or contact your administrator."
        )
    
    # Generic AI service errors - don't expose internal details
    if any(term in error_lower for term in ["ai service", "ai failed", "generation failed", 
                                            "llm", "langchain", "openai", "anthropic", "gemini"]):
        return (
            "The AI service encountered an error while processing your request. "
            "Please try again later."
        )
    
    # Check for stack traces or technical details
    if any(term in error_message for term in ["Exception", "Traceback", "at org.", "at com.", 
                                               "File \"", "line ", "  at "]):
        return (
            "An internal error occurred while processing your request. "
            "Please check the job logs for more details."
        )
    
    # Check for JSON/technical error structures
    if error_message.startswith("{") or error_message.startswith("["):
        return (
            "An error occurred while processing your request. "
            "Please check the job logs for more details."
        )
    
    # If message is very long, truncate it
    if len(error_message) > 200:
        return (
            "An error occurred while processing your request. "
            "Please check the job logs for more details."
        )
    
    # If it looks safe, return a cleaned version
    # Remove any potential API keys or tokens
    sanitized = re.sub(r'sk-[a-zA-Z0-9]{20,}', '[API_KEY_REDACTED]', error_message)
    sanitized = re.sub(r'api[_-]?key["\s:=]+["\']?[a-zA-Z0-9-_]+["\']?', '[API_KEY_REDACTED]', sanitized, flags=re.IGNORECASE)
    
    return sanitized


def create_user_friendly_error(error: Exception) -> str:
    """
    Create a user-friendly error message from an exception.
    
    Args:
        error: The exception
        
    Returns:
        A sanitized, user-friendly error message
    """
    error_str = str(error)
    error_type = type(error).__name__
    
    # Log the full error for debugging
    logger.error(f"Error ({error_type}): {error_str}")
    
    # Return sanitized message
    return sanitize_error_for_display(error_str)
