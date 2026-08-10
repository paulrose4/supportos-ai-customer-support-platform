from hashlib import sha256
from uuid import uuid4

from app.application.dto import (
    CreateHandoffCommand,
    ListHandoffsQuery,
    ListHandoffsResult,
)
from app.domain.models import HandoffNotification, HandoffRequest
from app.domain.ports import HandoffNotificationPort, HandoffPort, HandoffQueuePort
from app.domain.rules import (
    can_read_handoff_queue,
    classify_customer_sentiment,
    handoff_context_issues,
    redact_sensitive_text,
)


class CreateHandoffService:
    def __init__(
        self,
        handoff_port: HandoffPort,
        notification_port: HandoffNotificationPort | None = None,
    ) -> None:
        self._handoff_port = handoff_port
        self._notification_port = notification_port

    async def execute(self, command: CreateHandoffCommand) -> HandoffRequest:
        idempotency_source = ":".join(
            (
                command.principal.tenant_id,
                command.conversation_id,
                command.trace_id,
                command.reason_code,
            )
        )
        idempotency_key = sha256(idempotency_source.encode("utf-8")).hexdigest()
        summary = redact_sensitive_text(command.summary)[:2000]
        identity_status = command.identity_status or (
            "anonymous" if command.principal.is_anonymous else "authenticated"
        )
        request = HandoffRequest(
            handoff_id=str(uuid4()),
            tenant_id=command.principal.tenant_id,
            customer_id=(None if command.principal.is_anonymous else command.principal.subject_id),
            conversation_id=command.conversation_id,
            reason_code=command.reason_code,
            risk_level=int(command.risk_level),
            summary=summary,
            verified_evidence=command.verified_evidence,
            failed_tools=command.failed_tools,
            knowledge_sources=command.knowledge_sources,
            user_intent=command.user_intent or "unclassified",
            unresolved_question=command.unresolved_question or summary,
            ai_attempt=command.ai_attempt or "No approved automated answer was produced.",
            suggested_next_action=(
                command.suggested_next_action or "Review the conversation and confirm next steps."
            ),
            reply_draft=(
                command.reply_draft
                or "Hello, I have taken over your request and am reviewing the details."
            ),
            context_schema_version=command.context_schema_version,
            customer_language=command.customer_language or "und",
            customer_region=command.customer_region,
            identity_status=identity_status,
            product_ids=tuple(dict.fromkeys(command.product_ids))[:10],
            order_id=command.order_id,
            confirmed_fields=tuple(dict.fromkeys(command.confirmed_fields))[:20],
            customer_sentiment=(
                command.customer_sentiment or classify_customer_sentiment(command.summary)
            ),
            customer_request=command.customer_request or summary,
            queue_id=command.queue_id,
            priority=command.priority,
            sla_policy_version=command.sla_policy_version,
            expected_response_at=command.expected_response_at,
            commitment_deadline=command.commitment_deadline,
            idempotency_key=idempotency_key,
            trace_id=command.trace_id,
        )
        issues = handoff_context_issues(request)
        if issues:
            raise ValueError(f"handoff context is incomplete: {','.join(issues)}")
        stored = await self._handoff_port.create(request)
        if (
            self._notification_port is not None
            and command.principal.site_id
            and stored.handoff_id == request.handoff_id
        ):
            try:
                await self._notification_port.send(
                    HandoffNotification(
                        tenant_id=stored.tenant_id,
                        site_id=command.principal.site_id,
                        handoff_id=stored.handoff_id,
                        conversation_id=stored.conversation_id,
                        reason_code=stored.reason_code,
                        risk_level=stored.risk_level,
                        summary=stored.summary,
                    )
                )
            except Exception:  # noqa: BLE001
                pass
        return stored


class ListHandoffsService:
    def __init__(self, queue: HandoffQueuePort) -> None:
        self._queue = queue

    async def execute(self, query: ListHandoffsQuery) -> ListHandoffsResult:
        if not can_read_handoff_queue(query.principal):
            raise PermissionError("scope access denied")
        if not 1 <= query.limit <= 100:
            raise ValueError("handoff limit must be between 1 and 100")
        page_method = getattr(self._queue, "list_page_for_tenant", None)
        if page_method is None:
            handoffs = await self._queue.list_for_tenant(
                tenant_id=query.principal.tenant_id,
                status=query.status,
                limit=query.limit,
            )
            next_cursor = None
        else:
            handoffs, next_cursor = await page_method(
                tenant_id=query.principal.tenant_id,
                status=query.status,
                limit=query.limit,
                cursor=query.cursor,
                site_id=query.site_id,
                assigned_agent_id=query.assigned_agent_id,
            )
        return ListHandoffsResult(tuple(handoffs), next_cursor)
