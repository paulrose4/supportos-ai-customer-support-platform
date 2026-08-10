from app.domain.ports import ChatModelRequest, ChatModelResult
from app.integrations.llm import InstrumentedChatModel
from app.observability.metrics import InMemoryRequestMetrics


class _Model:
    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        return ChatModelResult(
            text="ok",
            model="test-model",
            metadata={"input_tokens": 12, "output_tokens": 4},
        )


async def test_instrumented_model_records_trusted_task_latency() -> None:
    metrics = InMemoryRequestMetrics()
    model = InstrumentedChatModel(_Model(), metrics)

    result = await model.generate(
        ChatModelRequest(
            messages=({"role": "user", "content": "hello"},),
            metadata={"task": "turn_understanding"},
        )
    )

    snapshot = metrics.snapshot()
    assert result.text == "ok"
    assert snapshot["model_call_count"] == 1
    assert snapshot["model_task_turn_understanding_p95_ms"] >= 0


async def test_instrumented_model_bounds_unknown_task_label() -> None:
    metrics = InMemoryRequestMetrics()
    model = InstrumentedChatModel(_Model(), metrics)

    await model.generate(
        ChatModelRequest(
            messages=({"role": "user", "content": "hello"},),
            metadata={"task": "tenant-controlled-label"},
        )
    )

    snapshot = metrics.snapshot()
    assert "model_task_other_p95_ms" in snapshot
    assert "tenant-controlled-label" not in snapshot
