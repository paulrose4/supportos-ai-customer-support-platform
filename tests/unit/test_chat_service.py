from datetime import UTC, datetime, timedelta

import pytest

from app.application.dto import HandleChatCommand
from app.application.services import (
    AnswerKnowledgeService,
    CreateHandoffService,
    HandleChatService,
    QueryBusinessDataService,
    SkeletonResponseService,
)
from app.domain.models import (
    AgentResponse,
    AuthenticatedPrincipal,
    ConversationRoutingState,
    ConversationStatus,
    KnowledgeEvidence,
    OwnershipMode,
    ResponseKind,
    RiskLevel,
)
from app.graphs import LangGraphAgentRunner, build_customer_support_graph
from app.observability import InMemoryRequestMetrics
from tests.fakes.adapters import (
    GroundedChatModel,
    InMemoryConversationPersistenceAdapter,
    InMemoryHandoffAdapter,
    InMemoryKnowledgeControlPlane,
    InMemoryKnowledgeRetriever,
    InMemoryOrderRepository,
    InMemorySupportTicketRepository,
)


class NeverCalledRunner:
    async def run(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("agent runner should not be called for resolution confirmation")


class EchoRunner:
    async def run(self, *, conversation_id: str, **_kwargs):  # type: ignore[no-untyped-def]
        return AgentResponse(
            conversation_id=conversation_id,
            message="ok",
            kind=ResponseKind.ANSWER,
            risk_level=RiskLevel.PUBLIC,
            trace_id="trace-1",
        )


async def test_public_widget_unknown_conversation_id_is_replaced_by_server() -> None:
    conversations = InMemoryConversationPersistenceAdapter()
    service = HandleChatService(EchoRunner(), conversations, conversation_context=conversations)
    widget_principal = AuthenticatedPrincipal(
        subject_id="anonymous-widget-visitor",
        tenant_id="tenant-a",
        roles=frozenset({"anonymous"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="public_widget_token",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-widget",
        site_id="site-a",
        visitor_session_id="visitor-session-a",
    )

    result = await service.execute(
        HandleChatCommand(
            conversation_id="attacker-chosen-id",
            message="Hello",
            principal=widget_principal,
        )
    )

    assert result.response.conversation_id != "attacker-chosen-id"
    assert conversations.exchanges[0][2].conversation_id == result.response.conversation_id


@pytest.mark.parametrize(
    ("ip_address", "country_code", "expected_ip", "expected_country"),
    [
        (" 2001:0db8:0:0:0:0:0:1 ", " us ", "2001:db8::1", "US"),
        ("not-an-ip", "USA", None, None),
    ],
)
async def test_chat_service_normalizes_visitor_context_before_persistence(
    ip_address: str,
    country_code: str,
    expected_ip: str | None,
    expected_country: str | None,
) -> None:
    conversations = InMemoryConversationPersistenceAdapter()
    service = HandleChatService(
        NeverCalledRunner(),  # type: ignore[arg-type]
        conversations,
        conversation_context=conversations,
    )
    chat_principal = AuthenticatedPrincipal(
        subject_id="customer-1",
        tenant_id="tenant-a",
        roles=frozenset({"customer"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="mock",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-visitor-context",
    )

    await service.execute(
        HandleChatCommand(
            conversation_id="conversation-visitor-context",
            message="问题已解决，谢谢",
            principal=chat_principal,
            visitor_ip_address=ip_address,
            visitor_country_code=country_code,
        )
    )

    metadata = conversations.exchange_metadata[0]
    assert metadata["visitor_ip_address"] == expected_ip
    assert metadata["visitor_country_code"] == expected_country


async def test_chat_service_persists_exchange() -> None:
    handoffs = InMemoryHandoffAdapter()
    conversations = InMemoryConversationPersistenceAdapter()
    knowledge_service = AnswerKnowledgeService(
        retriever=InMemoryKnowledgeRetriever(
            [
                KnowledgeEvidence(
                    chunk_id="chunk-1",
                    document_id="document-1",
                    text="公开答案",
                    score=0.9,
                    source="faq.md",
                    metadata={"tenant_id": "tenant-a", "version_id": "version-1"},
                )
            ]
        ),
        chat_model=GroundedChatModel(),
        control_plane=InMemoryKnowledgeControlPlane(),
    )
    runner = LangGraphAgentRunner(
        build_customer_support_graph(
            response_service=SkeletonResponseService(),
            handoff_service=CreateHandoffService(handoffs),
            knowledge_answer_service=knowledge_service,
            business_query_service=QueryBusinessDataService(
                orders=InMemoryOrderRepository(),
                support_tickets=InMemorySupportTicketRepository(),
            ),
        )
    )
    metrics = InMemoryRequestMetrics()
    service = HandleChatService(runner, conversations, experience_metrics=metrics)
    principal = AuthenticatedPrincipal(
        subject_id="customer-1",
        tenant_id="tenant-a",
        roles=frozenset({"customer"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="mock",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )

    received_at = datetime.now(UTC) - timedelta(milliseconds=25)
    result = await service.execute(
        HandleChatCommand(
            conversation_id=None,
            message="公开问题",
            principal=principal,
            request_received_at=received_at,
            request_route="/v1/public-widget/chat",
        )
    )

    assert result.response.conversation_id
    assert len(conversations.exchanges) == 1
    assert conversations.exchanges[0][0].tenant_id == "tenant-a"
    metadata = conversations.exchange_metadata[0]
    assert metadata["request_received_at"] == received_at
    assert metadata["response_sent_at"] >= received_at
    assert metadata["request_route"] == "/v1/public-widget/chat"
    assert metrics.snapshot()["agent_response_count"] == 1


async def test_chat_service_redacts_sensitive_user_message_before_persistence() -> None:
    handoffs = InMemoryHandoffAdapter()
    conversations = InMemoryConversationPersistenceAdapter()
    knowledge_service = AnswerKnowledgeService(
        retriever=InMemoryKnowledgeRetriever(),
        chat_model=GroundedChatModel(),
        control_plane=InMemoryKnowledgeControlPlane(),
    )
    runner = LangGraphAgentRunner(
        build_customer_support_graph(
            response_service=SkeletonResponseService(),
            handoff_service=CreateHandoffService(handoffs),
            knowledge_answer_service=knowledge_service,
            business_query_service=QueryBusinessDataService(
                orders=InMemoryOrderRepository(),
                support_tickets=InMemorySupportTicketRepository(),
            ),
        )
    )
    service = HandleChatService(runner, conversations)
    principal = AuthenticatedPrincipal(
        subject_id="customer-1",
        tenant_id="tenant-a",
        roles=frozenset({"customer"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="mock",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )

    await service.execute(
        HandleChatCommand(
            conversation_id=None,
            message="我的邮箱是 me@example.com，如何使用产品？",
            principal=principal,
        )
    )

    persisted_message = conversations.exchanges[0][1]
    assert "me@example.com" not in persisted_message
    assert "[REDACTED_EMAIL]" in persisted_message


async def test_customer_confirmation_resolves_without_model_or_retrieval() -> None:
    conversations = InMemoryConversationPersistenceAdapter()
    principal = AuthenticatedPrincipal(
        subject_id="customer-1",
        tenant_id="tenant-a",
        roles=frozenset({"customer"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="mock",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
        preferred_language="zh-CN",
    )
    service = HandleChatService(
        NeverCalledRunner(),  # type: ignore[arg-type]
        conversations,
        conversation_context=conversations,
    )

    result = await service.execute(
        HandleChatCommand(
            conversation_id="conversation-1",
            message="问题已解决，谢谢",
            principal=principal,
        )
    )

    assert result.response.kind is ResponseKind.ANSWER
    assert "标记为解决" in result.response.message
    assert conversations.exchange_metadata[0]["customer_confirmed_resolution"] is True


async def test_chat_service_uses_same_site_conversation_history_for_follow_up() -> None:
    conversations = InMemoryConversationPersistenceAdapter()
    retriever = InMemoryKnowledgeRetriever(
        [
            KnowledgeEvidence(
                chunk_id="chunk-product",
                document_id="product-1",
                text="Model SKU-103 ships within the United States only.",
                score=0.95,
                source="product.html",
                metadata={"tenant_id": "tenant-a", "version_id": "version-1"},
            )
        ]
    )
    runner = LangGraphAgentRunner(
        build_customer_support_graph(
            response_service=SkeletonResponseService(),
            handoff_service=CreateHandoffService(InMemoryHandoffAdapter()),
            knowledge_answer_service=AnswerKnowledgeService(
                retriever=retriever,
                chat_model=GroundedChatModel(),
                control_plane=InMemoryKnowledgeControlPlane(),
            ),
            business_query_service=QueryBusinessDataService(
                orders=InMemoryOrderRepository(),
                support_tickets=InMemorySupportTicketRepository(),
            ),
        )
    )
    service = HandleChatService(
        runner,
        conversations,
        conversation_context=conversations,
    )
    principal = AuthenticatedPrincipal(
        subject_id="anonymous-widget-visitor",
        tenant_id="tenant-a",
        roles=frozenset({"anonymous"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="public_widget_token",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
        site_id="site-a",
    )

    await service.execute(
        HandleChatCommand(
            conversation_id="conversation-1",
            message="Tell me about model SKU-103.",
            principal=principal,
        )
    )
    await service.execute(
        HandleChatCommand(
            conversation_id="conversation-1",
            message="Can it be delivered to China?",
            principal=principal,
            page_path="/products/sku-103.html",
        )
    )

    assert "SKU-103" in retriever.queries[-1].text
    assert "Can it be delivered to China?" in retriever.queries[-1].text
    assert "CURRENT_PAGE_PATH: /products/sku-103.html" in retriever.queries[-1].text


async def test_chat_service_does_not_call_agent_after_handoff() -> None:
    class FailingAgentRunner:
        async def run(self, **_: object) -> None:
            raise AssertionError("AI runner must not run while a human owns the conversation")

    conversations = InMemoryConversationPersistenceAdapter()
    conversations.routing_states["conversation-human"] = ConversationRoutingState(
        conversation_id="conversation-human",
        status=ConversationStatus.WAITING_HUMAN,
        ownership_mode=OwnershipMode.QUEUED,
        handoff_id="handoff-1",
    )
    principal = AuthenticatedPrincipal(
        subject_id="anonymous-widget-visitor",
        tenant_id="tenant-a",
        roles=frozenset({"anonymous"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="public_widget_token",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-human-follow-up",
        site_id="site-a",
        preferred_language="en",
    )
    service = HandleChatService(
        FailingAgentRunner(),
        conversations,
        conversation_context=conversations,
    )

    result = await service.execute(
        HandleChatCommand(
            conversation_id="conversation-human",
            message="How long will the reply take?",
            principal=principal,
        )
    )

    assert result.response.kind is ResponseKind.HANDOFF
    assert result.response.handoff_id == "handoff-1"
    assert "human agent" in result.response.message
    assert len(conversations.exchanges) == 1
