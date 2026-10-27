"""Server-side LiteLLM adapter. Provider exceptions must be sanitized by callers."""

from typing import Protocol


class LLMClient(Protocol):
    def complete(self, *, model: str, system: str, user: str,
                 api_key: str | None = None) -> str: ...


class LiteLLMClient:
    def complete(self, *, model: str, system: str, user: str,
                 api_key: str | None = None) -> str:
        import litellm

        request = {"model": model,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                   "max_tokens": 500, "timeout": 50,
                   "num_retries": 0, "stream": False}
        if api_key is not None:
            request["api_key"] = api_key
        response = litellm.completion(**request)
        return response.choices[0].message.content
