from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.api.schemas.customer_experience import SaveAutomationRuleRequest
from app.application.dto import (
    CreateOfflineTicketCommand,
    SubmitSatisfactionCommand,
)
from app.application.dto import (
    TestAutomationRuleCommand as AutomationRuleTestCommand,
)
from app.application.services import CustomerExperienceService, default_widget_config
from app.application.services.customer_experience import validate_widget_config
from app.domain.models import (
    AuthenticatedPrincipal,
    AutomationConditions,
    AutomationFacts,
)
from app.integrations.postgres.customer_experience import PostgreSQLCustomerExperienceAdapter
from app.integrations.postgres.models import ConversationModel, SupportTicketModel


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="manager-1",
        tenant_id="tenant-a",
        roles=frozenset({"support_manager"}),
        scopes=frozenset(
            {"automation:manage", "automation:read", "support:inbox:read", "support:inbox:write"}
        ),
        authentication_method="test",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
        site_id="site-a",
    )


def _public_principal(visitor_session_id: str | None) -> AuthenticatedPrincipal:
    return replace(
        _principal(),
        subject_id="anonymous-widget-visitor",
        roles=frozenset({"anonymous"}),
        scopes=frozenset({"chat:public"}),
        authentication_method="public_widget_token",
        visitor_session_id=visitor_session_id,
    )


def _postgres_adapter_with_scalar_results(
    *results: object,
) -> tuple[PostgreSQLCustomerExperienceAdapter, MagicMock]:
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=results)
    session.flush = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=session)
    transaction.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock()
    session_factory.begin.return_value = transaction
    return PostgreSQLCustomerExperienceAdapter(session_factory), session


def test_automation_rule_matching_is_deterministic() -> None:
    service = CustomerExperienceService(object())  # type: ignore[arg-type]
    result = service.test_rule(
        AutomationRuleTestCommand(
            _principal(),
            AutomationConditions(
                site_id="site-a",
                page_path_prefix="/orders/",
                business_hours=False,
                user_intent="order",
                minimum_risk_level=1,
                minimum_dwell_seconds=30,
                has_assignee=False,
            ),
            AutomationFacts(
                site_id="site-a",
                page_path="/orders/123",
                within_business_hours=False,
                user_intent="order",
                risk_level=1,
                authenticated=False,
                dwell_seconds=45,
                has_assignee=False,
                has_ticket=False,
            ),
        )
    )
    assert result.matched is True
    assert result.reasons == ("all_conditions_matched",)


def test_automation_rule_reports_each_failed_condition() -> None:
    service = CustomerExperienceService(object())  # type: ignore[arg-type]
    result = service.test_rule(
        AutomationRuleTestCommand(
            _principal(),
            AutomationConditions(site_id="site-b", minimum_dwell_seconds=60),
            AutomationFacts(
                site_id="site-a",
                page_path="/",
                within_business_hours=True,
                user_intent="other",
                risk_level=0,
                authenticated=False,
                dwell_seconds=10,
                has_assignee=False,
                has_ticket=False,
            ),
        )
    )
    assert result.matched is False
    assert "site_id:expected=site-b,actual=site-a" in result.reasons
    assert "minimum_dwell_seconds:not_reached" in result.reasons


def test_widget_configuration_is_validated_before_persistence() -> None:
    config = default_widget_config("zh-CN")
    assert validate_widget_config(config) == config
    always_online = replace(
        config,
        business_hours={
            day: "00:00-24:00" for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        },
    )
    assert validate_widget_config(always_online) == always_online
    with pytest.raises(ValueError, match="business hour ranges"):
        validate_widget_config(replace(config, business_hours={"mon": "24:00-24:00"}))
    with pytest.raises(ValueError, match="primary_color"):
        validate_widget_config(replace(config, primary_color="javascript:alert(1)"))


def test_automation_api_rejects_arbitrary_script_fields() -> None:
    with pytest.raises(ValidationError):
        SaveAutomationRuleRequest.model_validate(
            {
                "name": "unsafe",
                "conditions": {},
                "actions": {"create_ticket": True, "script": "refund()"},
                "idempotency_key": "request-1",
            }
        )


@pytest.mark.asyncio
async def test_public_customer_experience_writes_forward_bound_visitor_session() -> None:
    port = AsyncMock()
    port.create_offline_ticket.return_value = "ticket-1"
    service = CustomerExperienceService(port)
    principal = _public_principal("visitor-session-a")

    await service.submit_satisfaction(
        SubmitSatisfactionCommand(principal, "conversation-1", 5, "Helpful", "rating-1")
    )
    ticket_id = await service.create_offline_ticket(
        CreateOfflineTicketCommand(
            principal,
            "conversation-1",
            "visitor@example.com",
            "Please contact me",
            "/products/1",
            "offline-1",
        )
    )

    assert port.submit_satisfaction.await_args.kwargs["visitor_session_id"] == "visitor-session-a"
    assert port.create_offline_ticket.await_args.kwargs["visitor_session_id"] == (
        "visitor-session-a"
    )
    assert ticket_id == "ticket-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["satisfaction", "offline-message"])
async def test_public_customer_experience_writes_require_bound_visitor_session(
    operation: str,
) -> None:
    port = AsyncMock()
    service = CustomerExperienceService(port)
    principal = _public_principal(None)

    with pytest.raises(PermissionError, match="visitor session"):
        if operation == "satisfaction":
            await service.submit_satisfaction(
                SubmitSatisfactionCommand(principal, "conversation-1", 5, None, "rating-1")
            )
        else:
            await service.create_offline_ticket(
                CreateOfflineTicketCommand(
                    principal,
                    "conversation-1",
                    "visitor@example.com",
                    "Please contact me",
                    "/products/1",
                    "offline-1",
                )
            )

    port.submit_satisfaction.assert_not_awaited()
    port.create_offline_ticket.assert_not_awaited()


@pytest.mark.asyncio
async def test_satisfaction_authorizes_visitor_before_idempotent_replay() -> None:
    conversation = ConversationModel(
        tenant_id="tenant-a",
        conversation_id="conversation-1",
        customer_id=None,
        site_id="site-a",
        visitor_session_id="visitor-session-a",
        status="resolved",
    )
    adapter, session = _postgres_adapter_with_scalar_results(conversation, object())

    with pytest.raises(PermissionError, match="visitor session"):
        await adapter.submit_satisfaction(
            tenant_id="tenant-a",
            site_id="site-a",
            conversation_id="conversation-1",
            rating_id="rating-1",
            score=5,
            comment=None,
            idempotency_key="rating-request-1",
            created_at=datetime.now(UTC),
            visitor_session_id="visitor-session-b",
        )

    assert session.scalar.await_count == 1


@pytest.mark.asyncio
async def test_offline_ticket_authorizes_visitor_before_idempotent_replay() -> None:
    conversation = ConversationModel(
        tenant_id="tenant-a",
        conversation_id="conversation-1",
        customer_id=None,
        site_id="site-a",
        visitor_session_id="visitor-session-a",
    )
    adapter, session = _postgres_adapter_with_scalar_results(conversation, object())

    with pytest.raises(PermissionError, match="visitor session"):
        await adapter.create_offline_ticket(
            tenant_id="tenant-a",
            site_id="site-a",
            conversation_id="conversation-1",
            email="visitor@example.com",
            message="Please contact me",
            page_path="/products/1",
            idempotency_key="offline-request-1",
            created_at=datetime.now(UTC),
            visitor_session_id="visitor-session-b",
        )

    assert session.scalar.await_count == 1


@pytest.mark.asyncio
async def test_offline_ticket_replay_must_match_visitor_conversation() -> None:
    conversation = ConversationModel(
        tenant_id="tenant-a",
        conversation_id="conversation-1",
        customer_id=None,
        site_id="site-a",
        visitor_session_id="visitor-session-a",
    )
    existing = SupportTicketModel(
        tenant_id="tenant-a",
        ticket_id="ticket-1",
        conversation_id="conversation-other",
        status="open",
        subject="Existing ticket",
    )
    adapter, session = _postgres_adapter_with_scalar_results(conversation, existing)

    with pytest.raises(PermissionError, match="offline ticket"):
        await adapter.create_offline_ticket(
            tenant_id="tenant-a",
            site_id="site-a",
            conversation_id="conversation-1",
            email="visitor@example.com",
            message="Please contact me",
            page_path="/products/1",
            idempotency_key="offline-request-1",
            created_at=datetime.now(UTC),
            visitor_session_id="visitor-session-a",
        )

    assert session.scalar.await_count == 2


@pytest.mark.asyncio
async def test_new_offline_conversation_persists_visitor_session_owner() -> None:
    site = MagicMock()
    queue = MagicMock(queue_id="general")
    adapter, session = _postgres_adapter_with_scalar_results(None, None, site, queue)

    await adapter.create_offline_ticket(
        tenant_id="tenant-a",
        site_id="site-a",
        conversation_id="conversation-1",
        email="visitor@example.com",
        message="Please contact me",
        page_path="/products/1",
        idempotency_key="offline-request-1",
        created_at=datetime.now(UTC),
        visitor_session_id="visitor-session-a",
    )

    conversation = session.add.call_args.args[0]
    assert isinstance(conversation, ConversationModel)
    assert conversation.site_id == "site-a"
    assert conversation.visitor_session_id == "visitor-session-a"
    session.flush.assert_awaited_once()
