from types import SimpleNamespace

import pytest

from app.domain.ports import ChatModelRequest
from app.integrations.llm import OpenAIChatModelAdapter, OpenAIEmbeddingProvider


class _FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="response-1",
            model="configured-chat-model",
            output_text="基于证据的回答",
        )


class _FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=[0.0, 1.0]),
                SimpleNamespace(index=0, embedding=[1.0, 0.0]),
            ]
        )


class _FakeChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="chat-1",
            model="gpt-5.4",
            choices=[SimpleNamespace(message=SimpleNamespace(content="中转回答"))],
        )


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()
        self.chat = SimpleNamespace(completions=_FakeChatCompletions())
        self.embeddings = _FakeEmbeddings()


@pytest.mark.asyncio
async def test_openai_chat_adapter_maps_port_request_and_response() -> None:
    client = _FakeClient()
    adapter = OpenAIChatModelAdapter(
        api_key="unused",
        model="configured-chat-model",
        client=client,
    )

    result = await adapter.generate(
        ChatModelRequest(
            messages=({"role": "user", "content": "问题"},),
            metadata={"task": "grounded_answer"},
        )
    )

    assert result.text == "基于证据的回答"
    assert result.metadata["grounded"] is True
    assert client.responses.calls == [
        {
            "model": "configured-chat-model",
            "input": [{"role": "user", "content": "问题"}],
        }
    ]


@pytest.mark.asyncio
async def test_openai_embedding_adapter_preserves_input_order() -> None:
    client = _FakeClient()
    adapter = OpenAIEmbeddingProvider(
        api_key="unused",
        model="configured-embedding-model",
        dimension=2,
        client=client,
    )

    vectors = await adapter.embed(["first", "second"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert client.embeddings.calls == [
        {
            "model": "configured-embedding-model",
            "input": ["first", "second"],
            "dimensions": 2,
            "encoding_format": "float",
        }
    ]


@pytest.mark.asyncio
async def test_openai_embedding_adapter_skips_empty_batches() -> None:
    client = _FakeClient()
    adapter = OpenAIEmbeddingProvider(
        api_key="unused",
        model="configured-embedding-model",
        dimension=2,
        client=client,
    )

    assert await adapter.embed([]) == []
    assert client.embeddings.calls == []


@pytest.mark.asyncio
async def test_openai_chat_adapter_supports_chat_completions() -> None:
    client = _FakeClient()
    adapter = OpenAIChatModelAdapter(
        api_key="unused", model="gpt-5.4", api_mode="chat_completions", client=client
    )

    result = await adapter.generate(
        ChatModelRequest(messages=({"role": "user", "content": "问题"},))
    )

    assert result.text == "中转回答"
    assert result.metadata["api_mode"] == "chat_completions"
    assert client.chat.completions.calls[0]["model"] == "gpt-5.4"


@pytest.mark.asyncio
async def test_openai_chat_adapter_applies_task_output_limit() -> None:
    client = _FakeClient()
    adapter = OpenAIChatModelAdapter(
        api_key="unused",
        model="configured-chat-model",
        client=client,
    )

    await adapter.generate(
        ChatModelRequest(
            messages=({"role": "user", "content": "问题"},),
            max_output_tokens=600,
        )
    )

    assert client.responses.calls[0]["max_output_tokens"] == 600
