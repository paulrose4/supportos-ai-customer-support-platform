import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from app.application.dto.lead_scoring import LeadScoreResult
from app.application.dto.presence import (
    ListVisitorPresenceQuery,
    ListVisitorPresenceResult,
    RecordVisitorPresenceCommand,
)
from app.application.services.lead_scoring import LeadScoringService
from app.application.visitor_context import (
    normalize_visitor_country_code,
    normalize_visitor_ip_address,
)
from app.domain.models import ConversationLeadContext, VisitorPresence
from app.domain.ports import (
    ConversationLeadContextPort,
    ConversationVisitorContextPort,
    VisitorPresenceStorePort,
)

_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
_AUTOMATED_USER_AGENT_PATTERN = re.compile(
    r"(?:googlebot|bingbot|yandexbot|baiduspider|bytespider|petalbot|duckduckbot|"
    r"gptbot|claudebot|anthropic-ai|ccbot|perplexitybot|headlesschrome|"
    r"(?:bot|crawler|spider)(?:[\s/;:_-]|$))",
    re.IGNORECASE,
)


class VisitorPresenceService:
    def __init__(
        self,
        store: VisitorPresenceStorePort,
        conversation_context: ConversationVisitorContextPort | None = None,
        conversation_lead_context: ConversationLeadContextPort | None = None,
        scoring_service: LeadScoringService | None = None,
    ) -> None:
        self._store = store
        self._conversation_context = conversation_context
        self._conversation_lead_context = conversation_lead_context
        self._scoring_service = scoring_service or LeadScoringService()

    def score(
        self,
        presence: VisitorPresence,
        *,
        now: datetime | None = None,
    ) -> LeadScoreResult:
        """Return the canonical score projection for an already trusted item."""

        return self._scoring_service.score(presence, now=now)

    async def record(self, command: RecordVisitorPresenceCommand) -> VisitorPresence:
        principal = command.principal
        if (
            principal.authentication_method
            not in {
                "public_presence_token",
                "public_widget_token",
                "widget_site_key",
                "wordpress_site_key",
            }
            or not principal.site_id
        ):
            raise PermissionError("trusted widget site identity is required")
        visitor_id = _validate_opaque_id(command.visitor_id, "visitor_id")
        conversation_id = (
            _validate_opaque_id(command.conversation_id, "conversation_id")
            if command.conversation_id
            else None
        )
        if (
            conversation_id
            and principal.authentication_method
            in {
                "public_presence_token",
                "public_widget_token",
            }
            and not principal.visitor_session_id
        ):
            raise PermissionError(
                "conversation binding requires a session-bound public widget credential"
            )
        if conversation_id and self._conversation_context is not None:
            authorized = await self._conversation_context.authorize_conversation(
                principal=principal,
                conversation_id=conversation_id,
            )
            if not authorized:
                raise PermissionError("conversation does not belong to the trusted visitor")
        elif conversation_id and principal.authentication_method in {
            "public_presence_token",
            "public_widget_token",
        }:
            raise PermissionError("conversation authorization is unavailable")
        page_view_id = (
            _validate_opaque_id(command.page_view_id, "page_view_id")
            if command.page_view_id
            else None
        )
        page_path = command.page_path.strip() or "/"
        if not page_path.startswith("/") or page_path.startswith("//") or len(page_path) > 500:
            raise ValueError("page_path must be a relative path of at most 500 characters")
        page_kind = _bounded_text(getattr(command, "page_kind", None), 32)
        if page_kind is not None and page_kind not in {
            "home",
            "category",
            "product",
            "comparison",
            "shipping",
            "payment",
            "pricing",
            "cart",
            "checkout",
            "order_confirmation",
            "support",
            "content",
            "unknown",
        }:
            raise ValueError("page_kind is not supported")
        now = datetime.now(UTC)
        presence_source = command.presence_source or "widget"
        if presence_source not in {"page_load", "widget"}:
            raise ValueError("presence_source must be page_load or widget")
        widget_state = command.widget_state or ("open" if presence_source == "widget" else "closed")
        if widget_state not in {"closed", "open"}:
            raise ValueError("widget_state must be closed or open")
        connector_type = _bounded_text(command.connector_type, 32)
        if connector_type is not None and connector_type not in {
            "public",
            "wordpress",
            "static_php",
            "cloudflare_worker",
            "legacy",
        }:
            raise ValueError("connector_type is not supported")
        user_agent = _bounded_text(command.user_agent, 500)
        if user_agent and _AUTOMATED_USER_AGENT_PATTERN.search(user_agent):
            raise ValueError("automated clients cannot record visitor presence")
        browser, operating_system, device_type = _parse_user_agent(user_agent)
        presence = VisitorPresence(
            tenant_id=principal.tenant_id,
            site_id=principal.site_id,
            visitor_id=visitor_id,
            conversation_id=conversation_id,
            page_path=page_path,
            last_seen_at=now,
            first_seen_at=now,
            page_kind=page_kind,
            page_title=_bounded_text(command.page_title, 200),
            referrer=_validate_referrer(command.referrer),
            ip_address=normalize_visitor_ip_address(command.source_address),
            country_code=normalize_visitor_country_code(command.country_code),
            user_agent=user_agent,
            browser=browser,
            operating_system=operating_system,
            device_type=device_type,
            language=_bounded_text(command.language, 35),
            timezone=_bounded_text(command.timezone, 100),
            session_started_at=now,
            current_page_entered_at=now,
            last_page_view_id=page_view_id,
            widget_state=widget_state,
            presence_source=presence_source,
            runtime_version=_bounded_text(command.runtime_version, 100),
            config_version=_bounded_text(command.config_version, 100),
            connector_type=connector_type,
            connector_version=_bounded_text(command.connector_version, 100),
        )
        stored = await self._store.upsert(presence)
        if conversation_id and self._conversation_context is not None:
            await self._conversation_context.update_visitor_context(
                principal=principal,
                conversation_id=conversation_id,
                ip_address=stored.ip_address,
                country_code=stored.country_code,
            )
        return stored

    async def list_active(self, query: ListVisitorPresenceQuery) -> ListVisitorPresenceResult:
        if "support:inbox:read" not in query.principal.scopes:
            raise PermissionError("support inbox read permission is required")
        if not 15 <= query.active_within_seconds <= 300:
            raise ValueError("active window must be between 15 and 300 seconds")
        items = await self._store.list_active(
            tenant_id=query.principal.tenant_id,
            active_after=datetime.now(UTC) - timedelta(seconds=query.active_within_seconds),
        )
        scored_at = datetime.now(UTC)
        lead_contexts: dict[str, ConversationLeadContext] = {}
        if self._conversation_lead_context is not None:
            conversation_ids = tuple(
                dict.fromkeys(item.conversation_id for item in items if item.conversation_id)
            )
            contexts = await self._conversation_lead_context.list_lead_contexts(
                tenant_id=query.principal.tenant_id,
                conversation_ids=conversation_ids,
            )
            lead_contexts = {context.conversation_id: context for context in contexts}
        scores = tuple(
            self._scoring_service.score(
                item,
                now=scored_at,
                conversation_intent=(
                    lead_contexts[item.conversation_id].scoring_intent
                    if item.conversation_id in lead_contexts
                    else None
                ),
            )
            for item in items
        )
        return ListVisitorPresenceResult(tuple(items), scores)


def _validate_opaque_id(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not _OPAQUE_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} must be an opaque identifier")
    return normalized


def _bounded_text(value: str | None, maximum_length: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized[:maximum_length] or None


def _validate_referrer(value: str | None) -> str | None:
    normalized = _bounded_text(value, 1000)
    if normalized is None:
        return None
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return normalized


def _parse_user_agent(user_agent: str | None) -> tuple[str | None, str | None, str | None]:
    if not user_agent:
        return None, None, None
    lowered = user_agent.lower()
    if "edg/" in lowered:
        browser = "Edge"
    elif "opr/" in lowered or "opera" in lowered:
        browser = "Opera"
    elif "chrome/" in lowered and "chromium" not in lowered:
        browser = "Chrome"
    elif "firefox/" in lowered:
        browser = "Firefox"
    elif "safari/" in lowered and "chrome/" not in lowered:
        browser = "Safari"
    else:
        browser = "其他浏览器"

    if "android" in lowered:
        operating_system = "Android"
    elif "iphone" in lowered or "ipad" in lowered:
        operating_system = "iOS"
    elif "windows" in lowered:
        operating_system = "Windows"
    elif "mac os" in lowered or "macintosh" in lowered:
        operating_system = "macOS"
    elif "linux" in lowered:
        operating_system = "Linux"
    else:
        operating_system = "其他系统"

    if "ipad" in lowered or "tablet" in lowered:
        device_type = "平板"
    elif "mobile" in lowered or "iphone" in lowered or "android" in lowered:
        device_type = "移动设备"
    else:
        device_type = "桌面设备"
    return browser, operating_system, device_type
