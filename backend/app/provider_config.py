"""Server-owned LiteLLM model allowlist and matching credential detection."""

import re
from typing import Mapping


MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
API_KEY_NAMES = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "azure": ("AZURE_API_KEY",),
    "cohere": ("COHERE_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "together_ai": ("TOGETHERAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "fireworks_ai": ("FIREWORKS_AI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
}


def configured_litellm_models(environ: Mapping[str, str]) -> list[str]:
    raw = environ.get("RUNGUARD_LITELLM_MODELS") or environ.get("LLM_MODEL") or ""
    result: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        model = item.strip()
        if MODEL_ID.fullmatch(model) and model not in seen:
            seen.add(model)
            result.append(model)
    return result


def has_model_credential(model_id: str, environ: Mapping[str, str]) -> bool:
    provider = model_id.split("/", 1)[0].lower()
    if provider.startswith(("gpt-", "o1", "o3", "o4")):
        provider = "openai"
    elif provider.startswith("claude-"):
        provider = "anthropic"
    return any(environ.get(name, "").strip() for name in API_KEY_NAMES.get(provider, ()))
