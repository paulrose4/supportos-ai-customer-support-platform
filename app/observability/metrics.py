from collections import Counter, deque
from threading import Lock
from time import perf_counter

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class InMemoryRequestMetrics:
    def __init__(self, *, latency_sample_limit: int = 10_000) -> None:
        self._lock = Lock()
        self._request_count = 0
        self._error_count = 0
        self._duration_total = 0.0
        self._duration_max = 0.0
        self._status_counts: Counter[int] = Counter()
        self._latencies: deque[float] = deque(maxlen=latency_sample_limit)
        self._agent_response_count = 0
        self._agent_repair_count = 0
        self._agent_fallback_count = 0
        self._agent_generation_count = 0
        self._agent_stage_latencies: dict[str, deque[float]] = {}
        self._agent_stage_errors: Counter[str] = Counter()
        self._model_call_count = 0
        self._model_call_latencies: dict[str, deque[float]] = {}
        self._widget_admissions: Counter[str] = Counter()
        self._widget_cache: Counter[str] = Counter()
        self._widget_chat_in_flight = 0
        self._widget_chat_queue = 0
        self._widget_sse_connections = 0

    def record(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        del method, route
        with self._lock:
            self._request_count += 1
            self._status_counts[status_code] += 1
            if status_code >= 500:
                self._error_count += 1
            self._duration_total += duration_seconds
            self._duration_max = max(self._duration_max, duration_seconds)
            self._latencies.append(duration_seconds)

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            total = self._request_count
            errors = self._error_count
            ordered = sorted(self._latencies)
            return {
                "request_count": total,
                "server_error_count": errors,
                "server_error_rate": errors / total if total else 0.0,
                "average_latency_ms": (self._duration_total / total * 1000 if total else 0.0),
                "maximum_latency_ms": self._duration_max * 1000,
                "p50_latency_ms": _percentile(ordered, 0.50) * 1000,
                "p95_latency_ms": _percentile(ordered, 0.95) * 1000,
                "p99_latency_ms": _percentile(ordered, 0.99) * 1000,
                "responses_2xx": sum(
                    count for status, count in self._status_counts.items() if 200 <= status < 300
                ),
                "responses_4xx": sum(
                    count for status, count in self._status_counts.items() if 400 <= status < 500
                ),
                "responses_5xx": sum(
                    count for status, count in self._status_counts.items() if status >= 500
                ),
                "agent_response_count": self._agent_response_count,
                "agent_repair_count": self._agent_repair_count,
                "agent_fallback_count": self._agent_fallback_count,
                "agent_generation_count": self._agent_generation_count,
                "model_call_count": self._model_call_count,
                **{
                    f"agent_stage_{stage}_p95_ms": _percentile(sorted(values), 0.95) * 1000
                    for stage, values in self._agent_stage_latencies.items()
                },
                **{
                    f"model_task_{task}_p95_ms": _percentile(sorted(values), 0.95) * 1000
                    for task, values in self._model_call_latencies.items()
                },
                "widget_chat_in_flight": self._widget_chat_in_flight,
                "widget_chat_queue": self._widget_chat_queue,
                "widget_sse_connections": self._widget_sse_connections,
            }

    def record_agent_response(
        self,
        *,
        dialogue_act: str,
        response_kind: str,
        generation_count: int,
        repaired: bool,
        fallback: bool,
    ) -> None:
        del dialogue_act, response_kind
        with self._lock:
            self._agent_response_count += 1
            self._agent_generation_count += max(0, generation_count)
            self._agent_repair_count += int(repaired)
            self._agent_fallback_count += int(fallback)

    def record_agent_stage(
        self,
        *,
        stage: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            values = self._agent_stage_latencies.setdefault(stage, deque(maxlen=10_000))
            values.append(max(0.0, duration_seconds))
            if outcome != "success":
                self._agent_stage_errors[stage] += 1

    def record_model_call(
        self,
        *,
        task: str,
        outcome: str,
        duration_seconds: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        del outcome, input_tokens, output_tokens
        with self._lock:
            self._model_call_count += 1
            values = self._model_call_latencies.setdefault(task, deque(maxlen=10_000))
            values.append(max(0.0, duration_seconds))

    def record_widget_admission(self, *, outcome: str) -> None:
        with self._lock:
            self._widget_admissions[outcome] += 1

    def record_widget_cache(self, *, outcome: str) -> None:
        with self._lock:
            self._widget_cache[outcome] += 1

    def adjust_widget_chat_in_flight(self, delta: int) -> None:
        with self._lock:
            self._widget_chat_in_flight = max(0, self._widget_chat_in_flight + delta)

    def adjust_widget_chat_queue(self, delta: int) -> None:
        with self._lock:
            self._widget_chat_queue = max(0, self._widget_chat_queue + delta)

    def adjust_widget_sse_connections(self, delta: int) -> None:
        with self._lock:
            self._widget_sse_connections = max(0, self._widget_sse_connections + delta)


def _percentile(ordered: list[float], percentile: float) -> float:
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = perf_counter() - started
            route_object = request.scope.get("route")
            route = getattr(route_object, "path", request.url.path)
            container = getattr(request.app.state, "container", None)
            metrics = getattr(container, "request_metrics", None)
            if metrics is not None:
                metrics.record(
                    method=request.method,
                    route=route,
                    status_code=status_code,
                    duration_seconds=duration,
                )
            structlog.get_logger("http_request").info(
                "http_request.completed",
                method=request.method,
                route=route,
                status_code=status_code,
                duration_ms=round(duration * 1000, 3),
            )
