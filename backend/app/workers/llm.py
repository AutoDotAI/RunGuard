"""Server-side LiteLLM adapter. Provider exceptions must be sanitized by callers."""

from typing import Protocol


class LLMClient(Protocol):
    def complete(self, *, model: str, system: str, user: str) -> str: ...


class LiteLLMClient:
    def complete(self, *, model: str, system: str, user: str) -> str:
        import litellm

        response = litellm.completion(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=500,
            timeout=50,
            num_retries=0,
            stream=False,
        )
        return response.choices[0].message.content
