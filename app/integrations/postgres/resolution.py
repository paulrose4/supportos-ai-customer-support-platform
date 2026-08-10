from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.rules import (
    classify_conversation_intent,
    extract_product_skus,
    redact_sensitive_text,
)
from app.integrations.postgres.experience import record_censored_resolution_experience
from app.integrations.postgres.models import (
    AuditEventModel,
    ConversationModel,
    HandoffRequestModel,
    MessageModel,
    ResolutionEpisodeModel,
)


class PostgreSQLAutoResolutionAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def count_due(self, *, evaluated_at: datetime) -> int:
        async with self._session_factory() as session:
            value = await session.scalar(
                select(func.count())
                .select_from(ConversationModel)
                .where(*_due_conditions(evaluated_at))
            )
        return int(value or 0)

    async def resolve_due(self, *, evaluated_at: datetime, limit: int) -> int:
        async with self._session_factory.begin() as session:
            conversations = list(
                await session.scalars(
                    select(ConversationModel)
                    .where(*_due_conditions(evaluated_at))
                    .order_by(ConversationModel.auto_resolution_eligible_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for conversation in conversations:
                conversation.status = "resolved"
                conversation.resolved_at = evaluated_at
                conversation.auto_resolved_at = evaluated_at
                conversation.resolution_source = "auto_inactivity"
                conversation.auto_resolution_eligible_at = None
                conversation.updated_at = evaluated_at
                if conversation.customer_id is not None:
                    messages = list(
                        await session.scalars(
                            select(MessageModel)
                            .where(
                                MessageModel.tenant_id == conversation.tenant_id,
                                MessageModel.conversation_id == conversation.conversation_id,
                                MessageModel.message_type == "chat",
                            )
                            .order_by(MessageModel.id.desc())
                            .limit(2)
                        )
                    )
                    latest_user = next((item for item in messages if item.role == "user"), None)
                    latest_assistant = next(
                        (item for item in messages if item.role == "assistant"), None
                    )
                    if latest_user is not None and latest_assistant is not None:
                        episode_id = str(
                            uuid5(
                                NAMESPACE_URL,
                                f"{conversation.tenant_id}:{conversation.conversation_id}:auto_inactivity",
                            )
                        )
                        episode = await session.scalar(
                            select(ResolutionEpisodeModel).where(
                                ResolutionEpisodeModel.tenant_id == conversation.tenant_id,
                                ResolutionEpisodeModel.episode_id == episode_id,
                            )
                        )
                        if episode is None:
                            session.add(
                                ResolutionEpisodeModel(
                                    tenant_id=conversation.tenant_id,
                                    episode_id=episode_id,
                                    customer_id=conversation.customer_id,
                                    conversation_id=conversation.conversation_id,
                                    intent=classify_conversation_intent(latest_user.content),
                                    issue_summary=redact_sensitive_text(latest_user.content)[:2000],
                                    product_reference=(
                                        extract_product_skus(latest_user.content) or (None,)
                                    )[0],
                                    order_reference=None,
                                    actions_taken=[],
                                    resolution_summary=redact_sensitive_text(
                                        latest_assistant.content
                                    )[:3000],
                                    resolution_source="auto_inactivity",
                                    resolved_at=evaluated_at,
                                    reopened_at=None,
                                    confidence=0.0,
                                    expires_at=evaluated_at + timedelta(days=90),
                                    status="censored",
                                )
                            )
                await record_censored_resolution_experience(
                    session,
                    tenant_id=conversation.tenant_id,
                    site_id=conversation.site_id or "default-site",
                    conversation_id=conversation.conversation_id,
                    occurred_at=evaluated_at,
                )
                session.add(
                    AuditEventModel(
                        tenant_id=conversation.tenant_id,
                        event_id=str(uuid4()),
                        event_type="conversation.auto_resolved",
                        actor_subject_id="auto-resolution-service",
                        resource_type="conversation",
                        resource_id=conversation.conversation_id,
                        details={"resolution_source": "auto_inactivity"},
                        created_at=evaluated_at,
                    )
                )
        return len(conversations)


def _due_conditions(evaluated_at: datetime):  # type: ignore[no-untyped-def]
    return (
        ConversationModel.status == "open",
        ConversationModel.ownership_mode == "ai",
        ConversationModel.auto_resolution_eligible_at.is_not(None),
        ConversationModel.auto_resolution_eligible_at <= evaluated_at,
        ~exists(
            select(HandoffRequestModel.id).where(
                HandoffRequestModel.tenant_id == ConversationModel.tenant_id,
                HandoffRequestModel.conversation_id == ConversationModel.conversation_id,
            )
        ),
        ~exists(
            select(MessageModel.id).where(
                MessageModel.tenant_id == ConversationModel.tenant_id,
                MessageModel.conversation_id == ConversationModel.conversation_id,
                MessageModel.role == "agent",
                MessageModel.message_type == "chat",
            )
        ),
    )
