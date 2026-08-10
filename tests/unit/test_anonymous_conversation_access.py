from datetime import UTC, datetime

import pytest

from app.domain.models import AuthenticatedPrincipal
from app.integrations.postgres.conversations import _require_conversation_access
from app.integrations.postgres.models import ConversationModel


def _principal(
    visitor_session_id: str,
    *,
    authentication_method: str = "public_widget_token",
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="anonymous-widget-visitor",
        tenant_id="tenant-a",
        roles=frozenset({"anonymous"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method=authentication_method,
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
        site_id="site-a",
        visitor_session_id=visitor_session_id,
    )


@pytest.mark.parametrize(
    "authentication_method",
    ["public_presence_token", "public_widget_token"],
)
def test_public_visitor_can_only_access_its_bound_conversation(
    authentication_method: str,
) -> None:
    conversation = ConversationModel(
        tenant_id="tenant-a",
        conversation_id="conversation-1",
        site_id="site-a",
        customer_id=None,
        visitor_session_id="visitor-session-a",
    )

    _require_conversation_access(
        conversation,
        _principal("visitor-session-a", authentication_method=authentication_method),
    )

    with pytest.raises(PermissionError, match="visitor session"):
        _require_conversation_access(
            conversation,
            _principal("visitor-session-b", authentication_method=authentication_method),
        )


def test_legacy_unbound_anonymous_conversation_is_not_publicly_accessible() -> None:
    conversation = ConversationModel(
        tenant_id="tenant-a",
        conversation_id="conversation-legacy",
        site_id="site-a",
        customer_id=None,
        visitor_session_id=None,
    )

    with pytest.raises(PermissionError, match="visitor session"):
        _require_conversation_access(conversation, _principal("visitor-session-a"))
