from time import perf_counter

from app.domain.ports import ChatModelPort, ChatModelRequest, ChatModelResult, RequestMetricsPort

_KNOWN_MODEL_TASKS = frozenset(
    {
        "customer_response_quality_repair",
        "customer_response_render",
        "general_guidance",
        "grounded_answer",
        "response_expression_repair",
        "semantic_response_review",
        "startup_probe",
        "turn_understanding",
    }
)


class InstrumentedChatModel:
    def __init__(self, delegate: ChatModelPort, metrics: RequestMetricsPort) -> None:
        self._delegate = delegate
        self._metrics = metrics

    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        task = str(request.metadata.get("task") or "unknown")
        if task not in _KNOWN_MODEL_TASKS:
            task = "other"
        started = perf_counter()
        outcome = "success"
        result: ChatModelResult | None = None
        try:
            result = await self._delegate.generate(request)
            return result
        except Exception:
            outcome = "error"
            raise
        finally:
            metadata = result.metadata if result is not None else {}
            self._metrics.record_model_call(
                task=task,
                outcome=outcome,
                duration_seconds=perf_counter() - started,
                input_tokens=_optional_int(metadata.get("input_tokens")),
                output_tokens=_optional_int(metadata.get("output_tokens")),
            )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
