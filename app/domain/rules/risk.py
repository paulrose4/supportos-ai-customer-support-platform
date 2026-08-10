from app.domain.models import AuthenticatedPrincipal, RiskLevel
from app.domain.rules.business import classify_business_query
from app.domain.rules.order_handoff import is_public_order_policy_question

_HIGH_IMPACT_TERMS = frozenset(
    {
        "退款",
        "取消订单",
        "修改地址",
        "赔偿",
        "补偿",
        "隐私删除",
        "refund",
        "cancel my order",
        "cancel order",
        "change shipping address",
        "change delivery address",
        "compensation",
        "delete my data",
        "erase my data",
        "remboursement",
        "annuler ma commande",
        "rückerstattung",
        "bestellung stornieren",
        "reembolso",
        "cancelar mi pedido",
    }
)
_SEVERE_TERMS = frozenset(
    {
        "盗号",
        "诈骗",
        "泄露",
        "监管投诉",
        "法律投诉",
        "跨租户",
        "account takeover",
        "account stolen",
        "account may have been stolen",
        "fraud",
        "data leak",
        "data breach",
        "other tenant",
    }
)
_PUBLIC_TRUST_QUESTIONS = (
    "is this site legit",
    "is this a scam",
    "can i trust this site",
    "网站靠谱吗",
    "这是骗局吗",
    "网站可信吗",
)


def classify_minimum_risk(message: str, principal: AuthenticatedPrincipal) -> RiskLevel:
    normalized = message.casefold()
    if any(term in normalized for term in _PUBLIC_TRUST_QUESTIONS):
        return RiskLevel.PUBLIC
    if any(term in normalized for term in _SEVERE_TERMS):
        return RiskLevel.SEVERE
    if not is_public_order_policy_question(message) and any(
        term in normalized for term in _HIGH_IMPACT_TERMS
    ):
        return RiskLevel.HIGH_IMPACT_REQUEST
    if classify_business_query(message).resource_type.value != "unknown":
        return RiskLevel.AUTHENTICATED_READ
    if principal.is_anonymous:
        return RiskLevel.PUBLIC
    return RiskLevel.PUBLIC
