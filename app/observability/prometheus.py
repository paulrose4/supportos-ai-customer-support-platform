from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from app.observability.metrics import InMemoryRequestMetrics


class PrometheusRequestMetrics(InMemoryRequestMetrics):
    def __init__(self) -> None:
        super().__init__()
        self._registry = CollectorRegistry(auto_describe=True)
        self._requests = Counter(
            "http_requests_total",
            "HTTP requests completed by the API.",
            ("method", "route", "status_class"),
            registry=self._registry,
        )
        self._responses = Counter(
            "http_responses_total",
            "HTTP responses completed by the API, including the exact status code.",
            ("method", "route", "status_code"),
            registry=self._registry,
        )
        self._duration = Histogram(
            "http_request_duration_seconds",
            "End-to-end HTTP request duration.",
            ("method", "route"),
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 1.5, 2, 3, 5, 10, 30, 60),
            registry=self._registry,
        )
        self._agent_responses = Counter(
            "agent_responses_total",
            "Customer-support responses completed by dialogue act and response kind.",
            ("dialogue_act", "response_kind"),
            registry=self._registry,
        )
        self._agent_generations = Counter(
            "agent_response_generations_total",
            "Model generations used for customer-response expression.",
            registry=self._registry,
        )
        self._agent_repairs = Counter(
            "agent_response_repairs_total",
            "Customer responses repaired after deterministic experience review.",
            registry=self._registry,
        )
        self._agent_fallbacks = Counter(
            "agent_response_fallbacks_total",
            "Customer responses that retained deterministic prose after renderer failure.",
            registry=self._registry,
        )
        self._agent_stage_duration = Histogram(
            "agent_stage_duration_seconds",
            "Customer-support graph stage duration.",
            ("stage", "outcome"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30),
            registry=self._registry,
        )
        self._model_call_duration = Histogram(
            "model_call_duration_seconds",
            "Chat model call duration by trusted application task.",
            ("task", "outcome"),
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 1.5, 2, 3, 5, 10, 30),
            registry=self._registry,
        )
        self._model_tokens = Counter(
            "model_tokens_total",
            "Chat model token usage reported by the provider.",
            ("task", "direction"),
            registry=self._registry,
        )
        self._prometheus_widget_admissions = Counter(
            "widget_admission_total",
            "Public Widget chat admission decisions.",
            ("outcome",),
            registry=self._registry,
        )
        self._prometheus_widget_cache = Counter(
            "widget_site_cache_total",
            "Trusted public site cache lookups.",
            ("outcome",),
            registry=self._registry,
        )
        self._prometheus_widget_chat_in_flight = Gauge(
            "widget_chat_in_flight",
            "Public Widget chat requests holding a capacity lease.",
            registry=self._registry,
        )
        self._prometheus_widget_chat_queue = Gauge(
            "widget_chat_queue_depth",
            "Public Widget chat requests waiting for a capacity lease.",
            registry=self._registry,
        )
        self._prometheus_widget_sse_connections = Gauge(
            "widget_sse_connections",
            "Active public Widget SSE connections.",
            registry=self._registry,
        )

    def record(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        super().record(
            method=method,
            route=route,
            status_code=status_code,
            duration_seconds=duration_seconds,
        )
        status_class = f"{status_code // 100}xx"
        self._requests.labels(method=method, route=route, status_class=status_class).inc()
        self._responses.labels(
            method=method,
            route=route,
            status_code=str(status_code),
        ).inc()
        self._duration.labels(method=method, route=route).observe(duration_seconds)

    def render(self) -> bytes:
        return generate_latest(self._registry)

    def record_agent_response(
        self,
        *,
        dialogue_act: str,
        response_kind: str,
        generation_count: int,
        repaired: bool,
        fallback: bool,
    ) -> None:
        super().record_agent_response(
            dialogue_act=dialogue_act,
            response_kind=response_kind,
            generation_count=generation_count,
            repaired=repaired,
            fallback=fallback,
        )
        self._agent_responses.labels(
            dialogue_act=dialogue_act or "unknown",
            response_kind=response_kind,
        ).inc()
        self._agent_generations.inc(max(0, generation_count))
        if repaired:
            self._agent_repairs.inc()
        if fallback:
            self._agent_fallbacks.inc()

    def record_widget_admission(self, *, outcome: str) -> None:
        super().record_widget_admission(outcome=outcome)
        self._prometheus_widget_admissions.labels(outcome=outcome).inc()

    def record_agent_stage(
        self,
        *,
        stage: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        super().record_agent_stage(
            stage=stage,
            outcome=outcome,
            duration_seconds=duration_seconds,
        )
        self._agent_stage_duration.labels(stage=stage, outcome=outcome).observe(duration_seconds)

    def record_model_call(
        self,
        *,
        task: str,
        outcome: str,
        duration_seconds: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        super().record_model_call(
            task=task,
            outcome=outcome,
            duration_seconds=duration_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        self._model_call_duration.labels(task=task, outcome=outcome).observe(duration_seconds)
        if input_tokens is not None:
            self._model_tokens.labels(task=task, direction="input").inc(max(0, input_tokens))
        if output_tokens is not None:
            self._model_tokens.labels(task=task, direction="output").inc(max(0, output_tokens))

    def record_widget_cache(self, *, outcome: str) -> None:
        super().record_widget_cache(outcome=outcome)
        self._prometheus_widget_cache.labels(outcome=outcome).inc()

    def adjust_widget_chat_in_flight(self, delta: int) -> None:
        super().adjust_widget_chat_in_flight(delta)
        self._prometheus_widget_chat_in_flight.inc(delta)

    def adjust_widget_chat_queue(self, delta: int) -> None:
        super().adjust_widget_chat_queue(delta)
        self._prometheus_widget_chat_queue.inc(delta)

    def adjust_widget_sse_connections(self, delta: int) -> None:
        super().adjust_widget_sse_connections(delta)
        self._prometheus_widget_sse_connections.inc(delta)
