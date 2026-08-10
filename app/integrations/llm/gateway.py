import asyncio
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.domain.ports import ChatModelPort, ChatModelRequest, ChatModelResult

_BUDGET_SCRIPT = """
local rpm = tonumber(redis.call('GET', KEYS[1]) or '0')
local tpm = tonumber(redis.call('GET', KEYS[2]) or '0')
if rpm + 1 > tonumber(ARGV[1]) then return 0 end
if tpm + tonumber(ARGV[3]) > tonumber(ARGV[2]) then return 0 end
redis.call('INCR', KEYS[1])
redis.call('INCRBY', KEYS[2], ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
redis.call('EXPIRE', KEYS[2], ARGV[4])
return 1
"""


class RedisModelGateway:
    def __init__(
        self,
        delegate: ChatModelPort,
        client: Redis,
        *,
        requests_per_minute: int,
        tokens_per_minute: int,
        reserved_output_tokens: int,
        circuit_failure_threshold: int,
        circuit_recovery_seconds: int,
    ) -> None:
        self._delegate = delegate
        self._client = client
        self._rpm = requests_per_minute
        self._tpm = tokens_per_minute
        self._reserved_output = reserved_output_tokens
        self._failure_threshold = circuit_failure_threshold
        self._recovery_seconds = circuit_recovery_seconds
        self._failure_count = 0
        self._opened_at = 0.0
        self._half_open_in_flight = False
        self._lock = asyncio.Lock()

    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        await self._before_request()
        try:
            await self._consume_budget(request)
            result = await self._delegate.generate(request)
        except Exception:
            await self._record_failure()
            raise
        await self._record_success()
        return result

    async def _before_request(self) -> None:
        async with self._lock:
            if not self._opened_at:
                return
            if time.monotonic() - self._opened_at < self._recovery_seconds:
                raise RuntimeError("chat model circuit is open")
            if self._half_open_in_flight:
                raise RuntimeError("chat model circuit is half-open")
            self._half_open_in_flight = True

    async def _consume_budget(self, request: ChatModelRequest) -> None:
        estimated_tokens = self._reserved_output + sum(
            max(1, len(str(message.get("content", ""))) // 4) for message in request.messages
        )
        window = int(time.time()) // 60
        try:
            admitted = await self._client.eval(
                _BUDGET_SCRIPT,
                2,
                f"model-budget:{{global}}:rpm:{window}",
                f"model-budget:{{global}}:tpm:{window}",
                self._rpm,
                self._tpm,
                estimated_tokens,
                61,
            )
        except RedisError as exc:
            raise RuntimeError("model budget backend is unavailable") from exc
        if int(admitted or 0) != 1:
            raise RuntimeError("model provider budget is exhausted")

    async def _record_failure(self) -> None:
        async with self._lock:
            self._half_open_in_flight = False
            self._failure_count += 1
            if self._failure_count >= self._failure_threshold:
                self._opened_at = time.monotonic()

    async def _record_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            self._opened_at = 0.0
            self._half_open_in_flight = False
