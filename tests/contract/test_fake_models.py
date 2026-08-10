from app.domain.ports import ChatModelRequest
from app.integrations.llm import FakeChatModel, FakeEmbeddingProvider


async def test_fake_chat_model_contract() -> None:
    result = await FakeChatModel().generate(
        ChatModelRequest(messages=({"role": "user", "content": "hello"},))
    )
    assert result.text == "fake:hello"
    assert result.model


async def test_fake_embedding_contract() -> None:
    provider = FakeEmbeddingProvider()
    result = await provider.embed(["same", "same"])
    assert result[0] == result[1]
    assert len(result[0]) == provider.dimension
