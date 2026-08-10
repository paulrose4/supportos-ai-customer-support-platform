from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from app.application.dto import BusinessDataQueryResult, QueryBusinessDataCommand
from app.domain.models import (
    BusinessEvidence,
    BusinessResourceType,
    Order,
    SupportTicket,
    ToolExecution,
)
from app.domain.ports import OrderRepositoryPort, SupportTicketRepositoryPort
from app.domain.rules import (
    can_read_own_business_data,
    classify_business_query,
    classify_order_handoff,
    required_scope,
)

_MESSAGES = {
    "en": {
        "choose_resource": "Please tell me whether you want to check an order or a support ticket.",
        "order_human_required": "Order-related requests are handled by a human support agent.",
        "identity_required": "Please verify your identity before accessing personal account data.",
        "order_id_required": "Please provide the order number you want to check.",
        "ticket_id_required": "Please provide the support ticket number you want to check.",
        "order_not_found": (
            "I couldn't find that order under this account. Please check the order number."
        ),
        "ticket_not_found": (
            "I couldn't find that ticket under this account. Please check the ticket number."
        ),
        "order_status": "Order {resource_id} is currently {status}.",
        "ticket_status": "Support ticket {resource_id} is currently {status}.",
    },
    "zh": {
        "choose_resource": "请说明要查询订单状态还是工单状态。",
        "order_human_required": "订单相关问题统一由人工客服处理。",
        "identity_required": "查询个人业务数据前需要完成可信身份验证。",
        "order_id_required": "请提供要查询的订单号。",
        "ticket_id_required": "请提供要查询的工单号。",
        "order_not_found": "未找到属于当前账户的订单，请核对订单号。",
        "ticket_not_found": "未找到属于当前账户的工单，请核对工单号。",
        "order_status": "订单 {resource_id} 当前状态为：{status}。",
        "ticket_status": "工单 {resource_id} 当前状态为：{status}。",
    },
    "ja": {
        "choose_resource": "注文状況とサポートチケットのどちらを確認しますか？",
        "order_human_required": "注文に関するお問い合わせは担当者が対応します。",
        "identity_required": "個人情報を確認する前に、本人確認を完了してください。",
        "order_id_required": "確認する注文番号を入力してください。",
        "ticket_id_required": "確認するチケット番号を入力してください。",
        "order_not_found": "このアカウントの注文が見つかりません。注文番号をご確認ください。",
        "ticket_not_found": "このアカウントのチケットが見つかりません。番号をご確認ください。",
        "order_status": "注文 {resource_id} の現在の状況は「{status}」です。",
        "ticket_status": "チケット {resource_id} の現在の状況は「{status}」です。",
    },
}

_STATUS_LABELS = {
    "en": {
        "pending": "pending",
        "paid": "paid",
        "processing": "being processed",
        "shipped": "shipped",
        "delivered": "delivered",
        "open": "open",
        "pending_customer": "waiting for your reply",
        "resolved": "resolved",
        "closed": "closed",
    },
    "zh": {
        "pending": "待处理",
        "paid": "已支付",
        "processing": "处理中",
        "shipped": "已发货",
        "delivered": "已送达",
        "open": "处理中",
        "pending_customer": "等待客户补充信息",
        "resolved": "已解决",
        "closed": "已关闭",
    },
    "ja": {
        "pending": "保留中",
        "paid": "支払い済み",
        "processing": "処理中",
        "shipped": "発送済み",
        "delivered": "配達済み",
        "open": "対応中",
        "pending_customer": "お客様からの返信待ち",
        "resolved": "解決済み",
        "closed": "終了",
    },
}


class QueryBusinessDataService:
    def __init__(
        self,
        *,
        orders: OrderRepositoryPort,
        support_tickets: SupportTicketRepositoryPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._orders = orders
        self._support_tickets = support_tickets
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(self, command: QueryBusinessDataCommand) -> BusinessDataQueryResult:
        if classify_order_handoff(command.message) is not None:
            return _result(
                "human_required",
                _message(command.language, "order_human_required"),
            )
        intent = classify_business_query(command.message)
        if intent.resource_type is BusinessResourceType.UNKNOWN:
            return _result("clarification", _message(command.language, "choose_resource"))
        if intent.resource_type is BusinessResourceType.ORDER:
            return _result(
                "human_required",
                _message(command.language, "order_human_required"),
            )
        if command.principal.is_anonymous:
            return _result("identity_required", _message(command.language, "identity_required"))
        if intent.resource_id is None:
            key = (
                "order_id_required"
                if intent.resource_type is BusinessResourceType.ORDER
                else "ticket_id_required"
            )
            return _result("clarification", _message(command.language, key))

        tool_name = _tool_name(intent.resource_type)
        if not can_read_own_business_data(command.principal, intent.resource_type):
            execution = _execution(
                trace_id=command.trace_id,
                tool_name=tool_name,
                resource_id=intent.resource_id,
                status="denied",
                output_summary={"scope": required_scope(intent.resource_type)},
                error_code="scope_access_denied",
                now=self._clock(),
            )
            return _result(
                "unauthorized",
                None,
                tool_executions=(execution,),
                failed_tools=(tool_name,),
            )

        try:
            if intent.resource_type is BusinessResourceType.ORDER:
                return await self._query_order(command, intent.resource_id, tool_name)
            return await self._query_ticket(command, intent.resource_id, tool_name)
        except Exception as exc:
            execution = _execution(
                trace_id=command.trace_id,
                tool_name=tool_name,
                resource_id=intent.resource_id,
                status="failed",
                output_summary={},
                error_code=type(exc).__name__,
                now=self._clock(),
            )
            return _result(
                "tool_failure",
                None,
                tool_executions=(execution,),
                failed_tools=(tool_name,),
            )

    async def _query_order(
        self,
        command: QueryBusinessDataCommand,
        order_id: str,
        tool_name: str,
    ) -> BusinessDataQueryResult:
        order = await self._orders.get_for_customer(
            tenant_id=command.principal.tenant_id,
            customer_id=command.principal.subject_id,
            order_id=order_id,
        )
        now = self._clock()
        execution = _execution(
            trace_id=command.trace_id,
            tool_name=tool_name,
            resource_id=order_id,
            status="succeeded",
            output_summary={"found": order is not None},
            error_code=None,
            now=now,
        )
        if order is None:
            return _result(
                "not_found",
                _message(command.language, "order_not_found"),
                tool_executions=(execution,),
            )
        evidence = _order_evidence(order, now)
        label = _status_label(command.language, order.status)
        return _result(
            "sufficient",
            _message(command.language, "order_status", resource_id=order.order_id, status=label),
            citations=(f"postgres:orders:{order.order_id}@v{order.version}",),
            evidence=(evidence,),
            tool_executions=(execution,),
        )

    async def _query_ticket(
        self,
        command: QueryBusinessDataCommand,
        ticket_id: str,
        tool_name: str,
    ) -> BusinessDataQueryResult:
        ticket = await self._support_tickets.get_for_customer(
            tenant_id=command.principal.tenant_id,
            customer_id=command.principal.subject_id,
            ticket_id=ticket_id,
        )
        now = self._clock()
        execution = _execution(
            trace_id=command.trace_id,
            tool_name=tool_name,
            resource_id=ticket_id,
            status="succeeded",
            output_summary={"found": ticket is not None},
            error_code=None,
            now=now,
        )
        if ticket is None:
            return _result(
                "not_found",
                _message(command.language, "ticket_not_found"),
                tool_executions=(execution,),
            )
        evidence = _ticket_evidence(ticket, now)
        label = _status_label(command.language, ticket.status)
        return _result(
            "sufficient",
            _message(command.language, "ticket_status", resource_id=ticket.ticket_id, status=label),
            citations=(f"postgres:support_tickets:{ticket.ticket_id}",),
            evidence=(evidence,),
            tool_executions=(execution,),
        )


def _result(
    status: str,
    message: str | None,
    *,
    citations: tuple[str, ...] = (),
    evidence: tuple[BusinessEvidence, ...] = (),
    tool_executions: tuple[ToolExecution, ...] = (),
    failed_tools: tuple[str, ...] = (),
) -> BusinessDataQueryResult:
    return BusinessDataQueryResult(
        status=status,
        message=message,
        citations=citations,
        evidence=evidence,
        tool_executions=tool_executions,
        failed_tools=failed_tools,
    )


def _base_language(language: str) -> str:
    base = language.strip().replace("_", "-").casefold().split("-", 1)[0]
    return base if base in _MESSAGES else "en"


def _message(language: str, key: str, **values: str) -> str:
    return _MESSAGES[_base_language(language)][key].format(**values)


def _status_label(language: str, status: str) -> str:
    return _STATUS_LABELS[_base_language(language)].get(status, status)


def _tool_name(resource_type: BusinessResourceType) -> str:
    if resource_type is BusinessResourceType.ORDER:
        return "query_order_status"
    return "query_support_ticket_status"


def _execution(
    *,
    trace_id: str,
    tool_name: str,
    resource_id: str,
    status: str,
    output_summary: dict[str, object],
    error_code: str | None,
    now: datetime,
) -> ToolExecution:
    fingerprint = sha256(resource_id.encode("utf-8")).hexdigest()[:12]
    return ToolExecution(
        execution_id=str(uuid5(NAMESPACE_URL, f"{trace_id}:{tool_name}:{resource_id}")),
        tool_name=tool_name,
        status=status,
        input_summary={"resource_id_fingerprint": fingerprint},
        output_summary=dict(output_summary),
        error_code=error_code,
        created_at=now,
    )


def _order_evidence(order: Order, fetched_at: datetime) -> BusinessEvidence:
    return BusinessEvidence(
        source="postgres.orders",
        fetched_at=fetched_at,
        version=str(order.version),
        facts={
            "resource_type": "order",
            "resource_id": order.order_id,
            "status": order.status,
            "source_updated_at": order.updated_at.isoformat(),
        },
    )


def _ticket_evidence(ticket: SupportTicket, fetched_at: datetime) -> BusinessEvidence:
    return BusinessEvidence(
        source="postgres.support_tickets",
        fetched_at=fetched_at,
        version=None,
        facts={
            "resource_type": "support_ticket",
            "resource_id": ticket.ticket_id,
            "status": ticket.status,
            "source_created_at": ticket.created_at.isoformat(),
        },
    )
