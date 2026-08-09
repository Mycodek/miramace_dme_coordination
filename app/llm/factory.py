"""LLM protocol + provider factory."""

from __future__ import annotations

from typing import Protocol

from app.config import get_settings
from app.entities.models import OrderExtraction, SupplierFacts
from app.llm.providers import FakeLLMClient, GeminiLLMClient, OpenAILLMClient

PROVIDERS = ("gemini", "openai", "fake")


class LLMClient(Protocol):
    def extract_supplier_response(self, transcript: str) -> SupplierFacts: ...

    def extract_pcp_response(self, transcript: str) -> OrderExtraction: ...


def build_llm_client(*, force_fake: bool = False) -> tuple[LLMClient, str]:
    get_settings.cache_clear()
    settings = get_settings()
    provider = (settings.llm_provider or "gemini").strip().lower()

    if force_fake or provider == "fake":
        return FakeLLMClient(), "fake"
    if provider == "gemini" and settings.gemini_api_key:
        return (
            GeminiLLMClient(settings.gemini_api_key, settings.gemini_model),
            f"gemini:{settings.gemini_model}",
        )
    if (
        provider == "openai"
        and settings.openai_api_key
        and not settings.openai_api_key.startswith("sk-your")
    ):
        return (
            OpenAILLMClient(settings.openai_api_key, settings.openai_model),
            f"openai:{settings.openai_model}",
        )
    if provider == "gemini":
        return FakeLLMClient(), "fake(fallback: missing GEMINI_API_KEY)"
    if provider == "openai":
        return FakeLLMClient(), "fake(fallback: missing OPENAI_API_KEY)"
    raise ValueError(f"Unknown LLM_PROVIDER={provider!r}; expected {PROVIDERS}")
