from collections.abc import Sequence
from typing import Any

from openai import AsyncOpenAI

from app.domain.ports import ChatModelRequest, ChatModelResult


class OpenAIChatModelAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        base_url: str | None = None,
        api_mode: str = "responses",
        client: Any | None = None,
    ) -> None:
        if api_mode not in {"responses", "chat_completions"}:
            raise ValueError("unsupported OpenAI-compatible chat API mode")
        self._model = model
        self._api_mode = api_mode
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=1,
        )

    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        if self._api_mode == "chat_completions":
            options: dict[str, Any] = {}
            if request.max_output_tokens is not None:
                options["max_completion_tokens"] = request.max_output_tokens
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=list(request.messages),
                **options,
            )
            text = response.choices[0].message.content or ""
            finish_reason = getattr(response.choices[0], "finish_reason", None)
        else:
            options = {}
            if request.max_output_tokens is not None:
                options["max_output_tokens"] = request.max_output_tokens
            response = await self._client.responses.create(
                model=self._model,
                input=list(request.messages),
                **options,
            )
            text = response.output_text or ""
            finish_reason = getattr(response, "status", None)
        usage = getattr(response, "usage", None)
        return ChatModelResult(
            text=text,
            model=response.model,
            metadata={
                "provider": "openai_compatible",
                "response_id": response.id,
                "api_mode": self._api_mode,
                "input_tokens": (
                    getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
                ),
                "output_tokens": (
                    getattr(usage, "output_tokens", None)
                    or getattr(usage, "completion_tokens", None)
                ),
                "finish_reason": finish_reason,
                "grounded": (
                    request.metadata.get("task") == "grounded_answer" and bool(text.strip())
                ),
                "general_guidance": (
                    request.metadata.get("task") == "general_guidance" and bool(text.strip())
                ),
            },
        )


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimension: int,
        timeout_seconds: float = 30.0,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.dimension = dimension
        self._model = model
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=1,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.embeddings.create(
            model=self._model,
            input=list(texts),
            dimensions=self.dimension,
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(texts):
            raise RuntimeError("embedding provider returned an unexpected vector count")
        return [list(item.embedding) for item in ordered]
