"""Asks the model for SQL through an OpenAI-compatible chat API.

The same client works for llama.cpp's `llama-server` (CPU, GGUF: development, CI, demo)
and vLLM (GPU, serving): both expose /v1/chat/completions. Which server and model is a
setting (MODEL_SERVER_URL, SERVED_MODEL_NAME), not code.
"""

import httpx

from sqlibrarian.helpers.config import get_settings


class ModelError(Exception):
    """The model server is down, too slow, or answered with something unusable."""


class Generator:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        s = get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=s.model_server_url, timeout=s.model_timeout_seconds
        )

    async def complete(self, messages: list[dict]) -> str:
        s = get_settings()
        try:
            response = await self._client.post(
                "/chat/completions",
                json={
                    "model": s.served_model_name,
                    "messages": messages,
                    "temperature": 0,  # the same question gets the same SQL
                    "max_tokens": s.generation_max_tokens,
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            raise ModelError(f"model server: {type(e).__name__}") from e

    async def aclose(self) -> None:
        await self._client.aclose()
