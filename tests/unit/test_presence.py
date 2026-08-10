from datetime import UTC, datetime, timedelta

import pytest

from app.application.dto import ListVisitorPresenceQuery, RecordVisitorPresenceCommand
from app.application.services import VisitorPresenceService
from app.domain.models import AuthenticatedPrincipal, ConversationLeadContext, VisitorPresence
from app.integrations.presence import InMemoryVisitorPresenceStore


def principal(
    *,
    tenant_id: str = "tenant-a",
    site_id: str | None = "site-a",
    method: str = "widget_site_key",
    scopes: frozenset[str] = frozenset({"knowledge:read"}),
    visitor_session_id: str | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="subject-1",
        tenant_id=tenant_id,
        roles=frozenset({"anonymous"}),
        scopes=scopes,
        authentication_method=method,
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
        site_id=site_id,
        visitor_session_id=visitor_session_id,
    )


async def test_presence_requires_trusted_widget_site_identity() -> None:
    service = VisitorPresenceService(InMemoryVisitorPresenceStore())

    with pytest.raises(PermissionError, match="trusted widget"):
        await service.record(
            RecordVisitorPresenceCommand(
                principal=principal(method="anonymous", site_id=None),
                visitor_id="visitor-1",
                conversation_id=None,
                page_path="/products",
            )
        )


@pytest.mark.parametrize("visitor_id", ["", "visitor id", "<script>", "a" * 101])
async def test_presence_rejects_invalid_opaque_ids(visitor_id: str) -> None:
    service = VisitorPresenceService(InMemoryVisitorPresenceStore())

    with pytest.raises(ValueError, match="opaque identifier"):
        await service.record(
            RecordVisitorPresenceCommand(
                principal=principal(),
                visitor_id=visitor_id,
                conversation_id=None,
                page_path="/products",
            )
        )


async def test_presence_rejects_automated_user_agents() -> None:
    service = VisitorPresenceService(InMemoryVisitorPresenceStore())

    with pytest.raises(ValueError, match="automated clients"):
        await service.record(
            RecordVisitorPresenceCommand(
                principal=principal(),
                visitor_id="visitor-1",
                conversation_id=None,
                page_path="/products",
                user_agent="Mozilla/5.0 compatible; Googlebot/2.1",
            )
        )


async def test_rejected_public_conversation_binding_does_not_write_presence() -> None:
    class RejectingConversationContext:
        async def authorize_conversation(self, **_values: object) -> bool:
            return False

        async def update_visitor_context(self, **_values: object) -> None:
            raise AssertionError("visitor context must not update after denied authorization")

    store = InMemoryVisitorPresenceStore()
    service = VisitorPresenceService(
        store,
        conversation_context=RejectingConversationContext(),
    )

    with pytest.raises(PermissionError, match="does not belong"):
        await service.record(
            RecordVisitorPresenceCommand(
                principal=principal(
                    method="public_widget_token",
                    visitor_session_id="session-a",
                ),
                visitor_id="visitor-1",
                conversation_id="forged-conversation",
                page_path="/products/widget",
            )
        )

    assert (
        await store.list_active(
            tenant_id="tenant-a",
            active_after=datetime.now(UTC) - timedelta(minutes=1),
        )
        == []
    )


@pytest.mark.parametrize("method", ["public_presence_token", "public_widget_token"])
async def test_public_presence_conversation_requires_visitor_session(method: str) -> None:
    service = VisitorPresenceService(InMemoryVisitorPresenceStore())

    with pytest.raises(PermissionError, match="session-bound"):
        await service.record(
            RecordVisitorPresenceCommand(
                principal=principal(method=method),
                visitor_id="visitor-1",
                conversation_id="conversation-1",
                page_path="/products/widget",
            )
        )


@pytest.mark.parametrize(
    "page_path",
    ["https://example.com/products", "products", "//example.com/products", "/" + "a" * 500],
)
async def test_presence_requires_bounded_relative_page_path(page_path: str) -> None:
    service = VisitorPresenceService(InMemoryVisitorPresenceStore())

    with pytest.raises(ValueError, match="relative path"):
        await service.record(
            RecordVisitorPresenceCommand(
                principal=principal(),
                visitor_id="visitor-1",
                conversation_id=None,
                page_path=page_path,
            )
        )


async def test_presence_heartbeat_overwrites_same_tenant_site_visitor() -> None:
    store = InMemoryVisitorPresenceStore()
    service = VisitorPresenceService(store)
    site_principal = principal()

    await service.record(RecordVisitorPresenceCommand(site_principal, "visitor-1", None, "/first"))
    await service.record(
        RecordVisitorPresenceCommand(site_principal, "visitor-1", "conversation-1", "/second")
    )
    result = await service.list_active(
        ListVisitorPresenceQuery(
            principal=principal(
                method="admin_session",
                site_id=None,
                scopes=frozenset({"support:inbox:read"}),
            )
        )
    )

    assert len(result.items) == 1
    assert result.items[0].page_path == "/second"
    assert result.items[0].conversation_id == "conversation-1"


async def test_presence_heartbeat_preserves_session_context_and_counts_page_views() -> None:
    store = InMemoryVisitorPresenceStore()
    service = VisitorPresenceService(store)
    site_principal = principal()

    first = await service.record(
        RecordVisitorPresenceCommand(
            site_principal,
            "visitor-1",
            None,
            "/first",
            page_title="First page",
            referrer="https://www.google.com/",
            source_address="203.0.113.24",
            country_code="ro",
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0 Safari/537.36",
            language="ro-RO",
            timezone="Europe/Bucharest",
        )
    )
    second = await service.record(
        RecordVisitorPresenceCommand(
            site_principal,
            "visitor-1",
            "conversation-1",
            "/second",
            page_title="Second page",
            source_address="not-an-ip",
        )
    )

    assert second.first_seen_at == first.first_seen_at
    assert second.page_view_count == 2
    assert second.ip_address == "203.0.113.24"
    assert second.country_code == "RO"
    assert second.page_title == "Second page"
    assert second.referrer == "https://www.google.com/"
    assert second.browser == "Chrome"
    assert second.operating_system == "Windows"
    assert second.device_type == "桌面设备"


async def test_presence_counts_only_bounded_heartbeat_gaps_as_active_dwell() -> None:
    store = InMemoryVisitorPresenceStore()
    started_at = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)
    first = VisitorPresence(
        "tenant-a",
        "site-a",
        "visitor-1",
        None,
        "/products/one",
        started_at,
        first_seen_at=started_at,
        session_started_at=started_at,
        current_page_entered_at=started_at,
        last_page_view_id="page-1",
    )
    heartbeat = VisitorPresence(
        "tenant-a",
        "site-a",
        "visitor-1",
        None,
        "/products/one",
        started_at + timedelta(seconds=25),
        last_page_view_id="page-1",
    )
    resumed_after_hidden = VisitorPresence(
        "tenant-a",
        "site-a",
        "visitor-1",
        None,
        "/products/one",
        started_at + timedelta(seconds=90),
        last_page_view_id="page-1",
    )

    await store.upsert(first)
    active = await store.upsert(heartbeat)
    resumed = await store.upsert(resumed_after_hidden)

    assert active.session_active_dwell_seconds == 25
    assert resumed.session_active_dwell_seconds == 25


async def test_presence_ignores_out_of_order_heartbeats() -> None:
    store = InMemoryVisitorPresenceStore()
    current_at = datetime(2026, 8, 6, 10, 1, tzinfo=UTC)
    current = VisitorPresence(
        "tenant-a",
        "site-a",
        "visitor-1",
        "conversation-current",
        "/products/current",
        current_at,
        first_seen_at=current_at - timedelta(minutes=1),
        current_page_entered_at=current_at - timedelta(seconds=20),
        page_view_count=2,
    )
    delayed = VisitorPresence(
        "tenant-a",
        "site-a",
        "visitor-1",
        "conversation-old",
        "/products/old",
        current_at - timedelta(seconds=10),
    )

    await store.upsert(current)
    merged = await store.upsert(delayed)

    assert merged == current


async def test_presence_page_view_id_makes_retries_idempotent() -> None:
    store = InMemoryVisitorPresenceStore()
    service = VisitorPresenceService(store)
    site_principal = principal()

    await service.record(
        RecordVisitorPresenceCommand(
            site_principal,
            "visitor-1",
            None,
            "/products/one",
            page_view_id="page-1",
            presence_source="page_load",
            runtime_version="asset-build-123",
            config_version="widget-version-7",
            connector_type="wordpress",
            connector_version="0.4.0",
        )
    )
    retried = await service.record(
        RecordVisitorPresenceCommand(
            site_principal,
            "visitor-1",
            None,
            "/products/one",
            page_view_id="page-1",
            presence_source="page_load",
        )
    )
    navigated = await service.record(
        RecordVisitorPresenceCommand(
            site_principal,
            "visitor-1",
            None,
            "/products/one",
            page_view_id="page-2",
            presence_source="page_load",
            widget_state="open",
        )
    )

    assert retried.page_view_count == 1
    assert navigated.page_view_count == 2
    assert navigated.last_page_view_id == "page-2"
    assert navigated.widget_state == "open"
    assert navigated.presence_source == "page_load"
    assert navigated.runtime_version == "asset-build-123"
    assert navigated.config_version == "widget-version-7"
    assert navigated.connector_type == "wordpress"
    assert navigated.connector_version == "0.4.0"


async def test_presence_page_change_resets_missing_page_taxonomy() -> None:
    store = InMemoryVisitorPresenceStore()
    service = VisitorPresenceService(store)
    site_principal = principal()

    await service.record(
        RecordVisitorPresenceCommand(
            site_principal,
            "visitor-1",
            None,
            "/products/one",
            page_kind="product",
            page_view_id="page-1",
            presence_source="page_load",
        )
    )
    navigated = await service.record(
        RecordVisitorPresenceCommand(
            site_principal,
            "visitor-1",
            None,
            "/unclassified-page",
            page_view_id="page-2",
            presence_source="page_load",
        )
    )

    assert navigated.page_kind is None
    assert navigated.current_page_entered_at == navigated.last_seen_at


async def test_presence_heartbeat_updates_linked_conversation_visitor_context() -> None:
    class RecordingConversationContext:
        def __init__(self) -> None:
            self.updates: list[dict[str, object]] = []

        async def authorize_conversation(self, **_values: object) -> bool:
            return True

        async def update_visitor_context(self, **values: object) -> None:
            self.updates.append(values)

    conversation_context = RecordingConversationContext()
    service = VisitorPresenceService(
        InMemoryVisitorPresenceStore(),
        conversation_context=conversation_context,
    )
    site_principal = principal()

    await service.record(
        RecordVisitorPresenceCommand(
            site_principal,
            "visitor-1",
            "conversation-1",
            "/products",
            source_address="2001:0db8:0:0:0:0:0:1",
            country_code="de",
        )
    )

    assert conversation_context.updates == [
        {
            "principal": site_principal,
            "conversation_id": "conversation-1",
            "ip_address": "2001:db8::1",
            "country_code": "DE",
        }
    ]


async def test_presence_expires_and_is_tenant_isolated() -> None:
    store = InMemoryVisitorPresenceStore()
    now = datetime.now(UTC)
    await store.upsert(
        VisitorPresence("tenant-a", "site-a", "stale", None, "/", now - timedelta(minutes=5))
    )
    await store.upsert(VisitorPresence("tenant-a", "site-a", "active-a", None, "/a", now))
    await store.upsert(VisitorPresence("tenant-b", "site-b", "active-b", None, "/b", now))
    service = VisitorPresenceService(store)

    result = await service.list_active(
        ListVisitorPresenceQuery(
            principal=principal(
                method="admin_session",
                site_id=None,
                scopes=frozenset({"support:inbox:read"}),
            ),
            active_within_seconds=45,
        )
    )

    assert [item.visitor_id for item in result.items] == ["active-a"]


async def test_presence_scoring_uses_bounded_conversation_sales_context() -> None:
    class LeadContextReader:
        async def list_lead_contexts(
            self,
            *,
            tenant_id: str,
            conversation_ids: tuple[str, ...],
        ) -> tuple[ConversationLeadContext, ...]:
            assert tenant_id == "tenant-a"
            assert conversation_ids == ("conversation-1",)
            return (
                ConversationLeadContext(
                    conversation_id="conversation-1",
                    last_intent="general_question",
                    sales_stage="purchase_ready",
                ),
            )

    now = datetime.now(UTC)
    store = InMemoryVisitorPresenceStore()
    await store.upsert(
        VisitorPresence(
            "tenant-a",
            "site-a",
            "visitor-1",
            "conversation-1",
            "/",
            now,
            first_seen_at=now,
            session_started_at=now,
            current_page_entered_at=now,
        )
    )
    service = VisitorPresenceService(
        store,
        conversation_lead_context=LeadContextReader(),
    )

    result = await service.list_active(
        ListVisitorPresenceQuery(
            principal=principal(
                method="admin_session",
                site_id=None,
                scopes=frozenset({"support:inbox:read"}),
            )
        )
    )

    assert result.scores[0].tier.value == "hot"
    assert result.scores[0].operation_priority.value == "P0"
    assert "intent_purchase_ready" in result.scores[0].signals


async def test_presence_listing_requires_inbox_scope() -> None:
    service = VisitorPresenceService(InMemoryVisitorPresenceStore())

    with pytest.raises(PermissionError, match="support inbox read"):
        await service.list_active(
            ListVisitorPresenceQuery(
                principal=principal(method="admin_session", site_id=None),
            )
        )
