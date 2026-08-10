from typing import Protocol


class RequestMetricsPort(Protocol):
    def record(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None: ...

    def snapshot(self) -> dict[str, float | int]: ...

    def record_agent_response(
        self,
        *,
        dialogue_act: str,
        response_kind: str,
        generation_count: int,
        repaired: bool,
        fallback: bool,
    ) -> None: ...

    def record_agent_stage(
        self,
        *,
        stage: str,
        outcome: str,
        duration_seconds: float,
    ) -> None: ...

    def record_model_call(
        self,
        *,
        task: str,
        outcome: str,
        duration_seconds: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None: ...

    def record_widget_admission(self, *, outcome: str) -> None: ...

    def record_widget_cache(self, *, outcome: str) -> None: ...

    def adjust_widget_chat_in_flight(self, delta: int) -> None: ...

    def adjust_widget_chat_queue(self, delta: int) -> None: ...

    def adjust_widget_sse_connections(self, delta: int) -> None: ...
