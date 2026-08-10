import json
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from redis.exceptions import WatchError

from app.domain.models import VisitorPresence
from app.integrations.presence.mapping import merge_presence


class RedisVisitorPresenceStore:
    """Shared presence store with bounded TTL and tenant-prefixed keys."""

    def __init__(self, client: Redis, *, ttl_seconds: int = 360) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds

    async def upsert(self, presence: VisitorPresence) -> VisitorPresence:
        key = self._key(presence.tenant_id, presence.site_id, presence.visitor_id)
        index_key = self._index_key(presence.tenant_id)
        for _attempt in range(5):
            async with self._client.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(key)
                    current_raw = await pipeline.get(key)
                    current = self._deserialize(current_raw) if current_raw is not None else None
                    merged = merge_presence(current, presence)
                    payload = self._serialize(merged)
                    pipeline.multi()
                    pipeline.set(key, payload, ex=self._ttl_seconds)
                    pipeline.zadd(index_key, {key: merged.last_seen_at.timestamp()})
                    pipeline.expire(index_key, self._ttl_seconds)
                    await pipeline.execute()
                    return merged
                except WatchError:
                    continue
        raise RuntimeError("presence update contention exceeded retry limit")

    async def list_active(self, *, tenant_id: str, active_after: datetime) -> list[VisitorPresence]:
        index_key = self._index_key(tenant_id)
        stale_before = (datetime.now(UTC) - timedelta(seconds=self._ttl_seconds)).timestamp()
        await self._client.zremrangebyscore(index_key, "-inf", stale_before)
        keys = await self._client.zrangebyscore(index_key, active_after.timestamp(), "+inf")
        if not keys:
            return []
        raw_items = await self._client.mget(keys)
        items: list[VisitorPresence] = []
        missing_keys: list[str | bytes] = []
        for key, raw in zip(keys, raw_items, strict=True):
            if raw is None:
                missing_keys.append(key)
                continue
            try:
                presence = self._deserialize(raw)
                if presence is None or presence.last_seen_at < active_after:
                    continue
                items.append(presence)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        if missing_keys:
            await self._client.zrem(index_key, *missing_keys)
        return sorted(items, key=lambda item: item.last_seen_at, reverse=True)

    @staticmethod
    def _serialize(presence: VisitorPresence) -> str:
        return json.dumps(
            {
                "tenant_id": presence.tenant_id,
                "site_id": presence.site_id,
                "visitor_id": presence.visitor_id,
                "conversation_id": presence.conversation_id,
                "page_path": presence.page_path,
                "page_kind": presence.page_kind,
                "last_seen_at": presence.last_seen_at.astimezone(UTC).isoformat(),
                "first_seen_at": (
                    presence.first_seen_at.astimezone(UTC).isoformat()
                    if presence.first_seen_at
                    else None
                ),
                "page_title": presence.page_title,
                "referrer": presence.referrer,
                "ip_address": presence.ip_address,
                "country_code": presence.country_code,
                "user_agent": presence.user_agent,
                "browser": presence.browser,
                "operating_system": presence.operating_system,
                "device_type": presence.device_type,
                "language": presence.language,
                "timezone": presence.timezone,
                "page_view_count": presence.page_view_count,
                "session_started_at": (
                    presence.session_started_at.astimezone(UTC).isoformat()
                    if presence.session_started_at
                    else None
                ),
                "current_page_entered_at": (
                    presence.current_page_entered_at.astimezone(UTC).isoformat()
                    if presence.current_page_entered_at
                    else None
                ),
                "last_page_view_id": presence.last_page_view_id,
                "widget_state": presence.widget_state,
                "presence_source": presence.presence_source,
                "runtime_version": presence.runtime_version,
                "config_version": presence.config_version,
                "connector_type": presence.connector_type,
                "connector_version": presence.connector_version,
                "session_active_dwell_seconds": presence.session_active_dwell_seconds,
            },
            separators=(",", ":"),
        )

    @staticmethod
    def _deserialize(raw: str | bytes) -> VisitorPresence | None:
        try:
            data = json.loads(raw)
            last_seen_at = datetime.fromisoformat(str(data["last_seen_at"]))
            first_seen_value = data.get("first_seen_at")
            return VisitorPresence(
                tenant_id=str(data["tenant_id"]),
                site_id=str(data["site_id"]),
                visitor_id=str(data["visitor_id"]),
                conversation_id=(
                    str(data["conversation_id"])
                    if data.get("conversation_id") is not None
                    else None
                ),
                page_path=str(data["page_path"]),
                last_seen_at=last_seen_at,
                first_seen_at=(
                    datetime.fromisoformat(str(first_seen_value))
                    if first_seen_value
                    else last_seen_at
                ),
                page_kind=_optional_string(data.get("page_kind")),
                page_title=_optional_string(data.get("page_title")),
                referrer=_optional_string(data.get("referrer")),
                ip_address=_optional_string(data.get("ip_address")),
                country_code=_optional_string(data.get("country_code")),
                user_agent=_optional_string(data.get("user_agent")),
                browser=_optional_string(data.get("browser")),
                operating_system=_optional_string(data.get("operating_system")),
                device_type=_optional_string(data.get("device_type")),
                language=_optional_string(data.get("language")),
                timezone=_optional_string(data.get("timezone")),
                page_view_count=max(1, int(data.get("page_view_count", 1))),
                session_started_at=_optional_datetime(
                    data.get("session_started_at"),
                    fallback=last_seen_at,
                ),
                current_page_entered_at=_optional_datetime(
                    data.get("current_page_entered_at"),
                    fallback=last_seen_at,
                ),
                last_page_view_id=_optional_string(data.get("last_page_view_id")),
                widget_state=(
                    str(data.get("widget_state"))
                    if data.get("widget_state") in {"closed", "open"}
                    else "closed"
                ),
                presence_source=(
                    str(data.get("presence_source"))
                    if data.get("presence_source") in {"page_load", "widget"}
                    else "widget"
                ),
                runtime_version=_optional_string(data.get("runtime_version")),
                config_version=_optional_string(data.get("config_version")),
                connector_type=_optional_string(data.get("connector_type")),
                connector_version=_optional_string(data.get("connector_version")),
                session_active_dwell_seconds=max(
                    0,
                    int(data.get("session_active_dwell_seconds", 0)),
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _key(tenant_id: str, site_id: str, visitor_id: str) -> str:
        return f"presence:item:{tenant_id}:{site_id}:{visitor_id}"

    @staticmethod
    def _index_key(tenant_id: str) -> str:
        return f"presence:index:{tenant_id}"


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_datetime(value: object, *, fallback: datetime) -> datetime:
    return datetime.fromisoformat(str(value)) if value else fallback
