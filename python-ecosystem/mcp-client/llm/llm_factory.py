import os
import logging
from typing import Optional
from pydantic import SecretStr
from langchain_openai import ChatOpenAI
from langchain_core.utils.utils import secret_from_env

logger = logging.getLogger(__name__)

# Default temperature from env or 0.0 for deterministic results
DEFAULT_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.0"))

# Gemini thinking/reasoning models that DON'T work with tool calls
# These models require thought_signature preservation which isn't supported
# by LangChain. Users must use non-thinking variants instead.
UNSUPPORTED_GEMINI_THINKING_MODELS = {
    "google/gemini-2.0-flash-thinking-exp",
    "google/gemini-2.0-flash-thinking-exp:free",
    "google/gemini-2.5-flash-preview-05-20",
    "google/gemini-2.5-pro-preview-05-06",
    "google/gemini-3-flash-preview",
    "google/gemini-3-pro-preview",
}

# Mapping from unsupported thinking models to recommended alternatives
GEMINI_MODEL_ALTERNATIVES = {
    "google/gemini-2.0-flash-thinking-exp": "google/gemini-2.0-flash",
    "google/gemini-2.0-flash-thinking-exp:free": "google/gemini-2.0-flash",
    "google/gemini-2.5-flash-preview-05-20": "google/gemini-2.5-flash-preview",
    "google/gemini-2.5-pro-preview-05-06": "google/gemini-2.5-pro-preview",
    "google/gemini-3-flash-preview": "google/gemini-2.5",
    "google/gemini-3-pro-preview": "google/gemini-2.5",
}


class UnsupportedModelError(Exception):
    """Raised when an unsupported model is requested."""
    pass


class ChatOpenRouter(ChatOpenAI):
    """
    Small wrapper to support OpenRouter-style configuration via api_key.
    Keeps compatibility with the previous sample.
    """
    api_key: Optional[SecretStr] = SecretStr(
        secret_from_env("OPENROUTER_API_KEY", default=None) or ""
    )

    @property
    def lc_secrets(self) -> dict[str, str]:
        return {"api_key": "OPENROUTER_API_KEY"}

    def __init__(self,
                 api_key: Optional[str] = None,
                 **kwargs):
        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        super().__init__(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            **kwargs
        )


class LLMFactory:

    @staticmethod
    def create_llm(ai_model: str, ai_provider: str, ai_api_key: str, temperature: Optional[float] = None):
        """
        Create LLM instance.
        
        Args:
            temperature: LLM temperature. If None, uses LLM_TEMPERATURE env var or 0.0.
                        0.0 = deterministic results (recommended for code review)
                        0.1-0.3 = more creative but less consistent
                        
        Raises:
            UnsupportedModelError: If the model is a Gemini thinking model that doesn't
                                   support tool calls.
        """
        if temperature is None:
            temperature = DEFAULT_TEMPERATURE
        
        # model_kwargs to disable parallel tool calls at the API level
        # This prevents stdio transport concurrency issues with MCP servers
        model_kwargs = {
            "parallel_tool_calls": False
        }
        
        # Check if this is an unsupported Gemini thinking model
        model_lower = ai_model.lower()
        for unsupported in UNSUPPORTED_GEMINI_THINKING_MODELS:
            if model_lower == unsupported.lower() or model_lower.startswith(unsupported.lower()):
                alternative = GEMINI_MODEL_ALTERNATIVES.get(unsupported, "google/gemini-2.0-flash")
                error_msg = (
                    f"Model '{ai_model}' is a Gemini thinking model that requires thought_signature "
                    f"preservation for tool calls. This is not supported by the current LangChain integration. "
                    f"Please use a non-thinking variant instead, such as '{alternative}'."
                )
                logger.error(error_msg)
                raise UnsupportedModelError(error_msg)
            
        if ai_provider.lower() in ("openrouter", "open-router"):
            # Extra headers for OpenRouter
            extra_headers = {
                "HTTP-Referer": "https://codecrow.cloud",
                "X-Title": "CodeCrow AI"
            }
            
            return ChatOpenRouter(
                api_key=ai_api_key,
                model_name=ai_model,
                temperature=temperature,
                organization="Codecrow",
                model_kwargs=model_kwargs,
                default_headers=extra_headers
            )

        return ChatOpenAI(
            api_key=ai_api_key,
            model_name=ai_model,
            temperature=temperature,
            model_kwargs=model_kwargs
        )