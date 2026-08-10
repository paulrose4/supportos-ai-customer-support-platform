import re

from app.domain.models.chat import RiskLevel
from app.domain.models.order_handoff import OrderHandoffDecision

_ORDER_REFERENCE = re.compile(
    r"(?i)\b([a-z0-9][a-z0-9_-]*order[a-z0-9_-]+)\b|"
    r"\border\s*(?:number|no\.?|id)?\s*[:#-]?\s*([a-z0-9][a-z0-9_-]{3,})\b|"
    r"(?:订单号|订单编号|订单)[\s:#-]*([a-z0-9][a-z0-9_-]{3,})\b"
)

_PUBLIC_POLICY_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:refund|return|shipping|delivery)\s+polic(?:y|ies)\b",
        r"\bwhat\s+(?:payment|pay)\s+methods?\b",
        r"\bhow\s+long\s+(?:does|will)\s+(?:shipping|delivery)\s+take\b",
        r"退款政策|退货政策|退换货政策|配送政策|运输政策|支付方式|付款方式",
        r"(?:通常|一般|大概).{0,8}(?:多久|几天).{0,8}(?:发货|送达|配送)",
        r"(?:发货|配送|运输).{0,6}(?:时间|时效|范围)",
    )
)
_SUPPORT_TICKET_PATTERN = re.compile(
    r"(?i)\b(?:support\s+ticket|ticket\s+(?:status|number|id))\b|"
    r"工单状态|我的工单|工单号|工单编号"
)

_ORDER_RULES: tuple[tuple[str, str, str, RiskLevel, int, tuple[re.Pattern[str], ...]], ...] = (
    (
        "order_payment_issue",
        "payment_issue",
        "urgent",
        RiskLevel.HIGH_IMPACT_REQUEST,
        5,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\b(?:charged\s+twice|duplicate\s+charge|payment\s+failed|card\s+charged|unauthori[sz]ed\s+charge|payment\s+issue)\b",
                r"\b(?:send|provide|give|share).{0,24}(?:bank\s+(?:account|details|information)|wire\s+instructions|payment\s+link|payment\s+qr)\b",
                r"\b(?:bank\s+transfer|wire\s+transfer).{0,24}(?:pay|payment|now|details|account)\b",
                r"重复扣款|重复付款|支付失败|付款失败|银行卡.{0,4}扣款|多扣|盗刷|支付问题|扣款问题",
                r"(?:发送|提供|给我|告知).{0,12}(?:银行账户|银行账号|收款账户|收款账号|付款链接|支付链接|付款二维码|收款码)",
                r"(?:银行转账|电汇).{0,12}(?:付款|支付|账户|账号|信息)",
            )
        ),
    ),
    (
        "order_address_change",
        "address_change",
        "urgent",
        RiskLevel.HIGH_IMPACT_REQUEST,
        5,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\b(?:change|update|wrong|incorrect).{0,20}(?:shipping|delivery)\s+address\b",
                r"\b(?:shipping|delivery)\s+address.{0,20}(?:change|update|wrong|incorrect)\b",
                r"(?:修改|更改|改|填错).{0,8}(?:收货|配送|送货)?地址",
                r"地址.{0,6}(?:填错|写错|错了)",
            )
        ),
    ),
    (
        "order_cancellation",
        "order_cancellation",
        "urgent",
        RiskLevel.HIGH_IMPACT_REQUEST,
        5,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bcancel(?:\s+my|\s+the)?\s+order\b",
                r"取消.{0,6}订单|订单.{0,6}取消",
            )
        ),
    ),
    (
        "order_refund",
        "refund",
        "high",
        RiskLevel.HIGH_IMPACT_REQUEST,
        10,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\brefund\b",
                r"退款|退钱|退款.{0,8}(?:到账|进度|状态)",
            )
        ),
    ),
    (
        "order_return",
        "return_request",
        "high",
        RiskLevel.HIGH_IMPACT_REQUEST,
        10,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\breturn\s+(?:my|the|this)\s+(?:order|item|product)\b",
                r"\b(?:want|need|would\s+like)\s+to\s+return\s+(?:it|this|my|the)\b",
                r"我要退货|申请退货|退这个商品|退回商品|退回订单",
            )
        ),
    ),
    (
        "order_fulfillment_issue",
        "fulfillment_issue",
        "high",
        RiskLevel.AUTHENTICATED_READ,
        10,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\b(?:damaged|broken|missing|wrong)\s+(?:item|product|order|package)\b",
                r"\b(?:item|product|order|package)\s+(?:is\s+)?(?:damaged|broken|missing|wrong)\b",
                r"破损|损坏|缺件|少件|漏发|错发|发错|少发|包裹丢失",
            )
        ),
    ),
    (
        "order_tracking",
        "order_tracking",
        "normal",
        RiskLevel.AUTHENTICATED_READ,
        15,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\btracking\s+(?:number|status)\b",
                r"\btrack\s+(?:my|the)?\s*order\b",
                r"\bwhere\s+is\s+(?:my|the)\s+(?:order|package|shipment)\b",
                r"\b(?:my|the)\s+(?:order|package)\s+(?:has\s+not|hasn't|did\s+not|didn't)\s+arrive\b",
                r"\b(?:have\s+not|haven't|did\s+not|didn't)\s+receiv(?:e|ed)\s+(?:my|the)\s+(?:order|package|parcel|shipment)\b",
                r"物流单号|物流轨迹|查物流|追踪订单|订单到哪|包裹到哪|没有收到|未收到",
            )
        ),
    ),
    (
        "order_status",
        "order_status",
        "normal",
        RiskLevel.AUTHENTICATED_READ,
        15,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\border\s+status\b",
                r"\bhas\s+(?:my|the)\s+order\s+shipped\b",
                r"\b(?:my|the)\s+order\b",
                r"订单状态|我的订单|订单号|订单编号|发货了吗|是否发货",
            )
        ),
    ),
)


def is_public_order_policy_question(message: str) -> bool:
    normalized = " ".join(message.strip().split())
    return any(pattern.search(normalized) for pattern in _PUBLIC_POLICY_PATTERNS)


def classify_order_handoff(message: str) -> OrderHandoffDecision | None:
    normalized = " ".join(message.strip().split())
    if not normalized or is_public_order_policy_question(normalized):
        return None
    if _SUPPORT_TICKET_PATTERN.search(normalized):
        return OrderHandoffDecision(
            reason_code="support_ticket_request",
            user_intent="support_ticket",
            priority="normal",
            risk_level=RiskLevel.AUTHENTICATED_READ,
            sla_minutes=15,
            queue_id="support",
            sla_policy_version="support-human-v1",
        )
    order_reference = _extract_order_reference(normalized)
    for reason_code, intent, priority, risk_level, sla_minutes, patterns in _ORDER_RULES:
        if any(pattern.search(normalized) for pattern in patterns):
            return OrderHandoffDecision(
                reason_code=reason_code,
                user_intent=intent,
                priority=priority,
                risk_level=risk_level,
                sla_minutes=sla_minutes,
                order_reference=order_reference,
            )
    if order_reference is not None:
        return OrderHandoffDecision(
            reason_code="order_support",
            user_intent="order_support",
            priority="normal",
            risk_level=RiskLevel.AUTHENTICATED_READ,
            sla_minutes=15,
            order_reference=order_reference,
        )
    return None


def _extract_order_reference(message: str) -> str | None:
    match = _ORDER_REFERENCE.search(message)
    if match is None:
        return None
    return str(next(value for value in match.groups() if value is not None))[:100]
