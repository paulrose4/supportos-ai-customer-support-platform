import re

from app.domain.models import (
    AuthenticatedPrincipal,
    BusinessQueryIntent,
    BusinessResourceType,
)

_RESOURCE_ID_PATTERN = re.compile(r"\b[A-Z0-9]+(?:[-_][A-Z0-9]+)+\b", re.IGNORECASE)
_ORDER_TERMS = frozenset({"我的订单", "订单状态", "物流状态", "物流", "订单号", "订单编号"})
_TICKET_TERMS = frozenset({"工单状态", "我的工单", "工单号", "工单编号"})


def classify_business_query(message: str) -> BusinessQueryIntent:
    normalized = message.casefold()
    resource_type = BusinessResourceType.UNKNOWN
    terms: frozenset[str] = frozenset()
    if any(term in normalized for term in _TICKET_TERMS):
        resource_type = BusinessResourceType.SUPPORT_TICKET
        terms = _TICKET_TERMS
    elif any(term in normalized for term in _ORDER_TERMS):
        resource_type = BusinessResourceType.ORDER
        terms = _ORDER_TERMS

    resource_id = _extract_resource_id(message, normalized, terms)
    return BusinessQueryIntent(resource_type=resource_type, resource_id=resource_id)


def required_scope(resource_type: BusinessResourceType) -> str | None:
    if resource_type is BusinessResourceType.ORDER:
        return "orders:read:self"
    if resource_type is BusinessResourceType.SUPPORT_TICKET:
        return "tickets:read:self"
    return None


def can_read_own_business_data(
    principal: AuthenticatedPrincipal,
    resource_type: BusinessResourceType,
) -> bool:
    scope = required_scope(resource_type)
    return not principal.is_anonymous and scope is not None and scope in principal.scopes


def _extract_resource_id(message: str, normalized: str, terms: frozenset[str]) -> str | None:
    positions = [normalized.rfind(term) for term in terms if term in normalized]
    if not positions:
        return None
    search_start = max(positions)
    identifier = _RESOURCE_ID_PATTERN.search(message, search_start)
    return identifier.group(0).upper() if identifier else None


def can_read_handoff_queue(principal: AuthenticatedPrincipal) -> bool:
    return not principal.is_anonymous and "handoffs:read" in principal.scopes
