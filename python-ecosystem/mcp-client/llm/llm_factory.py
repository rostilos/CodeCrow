import os
from typing import Optional
from pydantic import SecretStr
from langchain_openai import ChatOpenAI
from langchain_core.utils.utils import secret_from_env


# Default temperature from env or 0.0 for deterministic results
DEFAULT_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.0"))

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
        """
        if temperature is None:
            temperature = DEFAULT_TEMPERATURE
            
        if ai_provider.lower() in ("openrouter", "open-router"):
            return ChatOpenRouter(
                api_key=ai_api_key,
                model_name=ai_model,
                temperature=temperature,
                organization="Codecrow"
            )

        return ChatOpenAI(api_key=ai_api_key, model_name=ai_model, temperature=temperature)