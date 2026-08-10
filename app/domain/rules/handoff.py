from app.domain.models.support import HandoffRequest

_IDENTITY_STATUSES = frozenset({"anonymous", "authenticated", "verified"})
_SENTIMENTS = frozenset({"negative", "neutral", "positive", "unknown"})


def handoff_context_issues(request: HandoffRequest) -> tuple[str, ...]:
    issues: list[str] = []
    if request.context_schema_version != 2:
        issues.append("context_schema_version_invalid")
    required = {
        "summary": request.summary,
        "customer_language": request.customer_language,
        "identity_status": request.identity_status,
        "user_intent": request.user_intent,
        "unresolved_question": request.unresolved_question,
        "ai_attempt": request.ai_attempt,
        "suggested_next_action": request.suggested_next_action,
        "reply_draft": request.reply_draft,
        "customer_request": request.customer_request,
    }
    issues.extend(
        f"{field}_missing" for field, value in required.items() if not str(value or "").strip()
    )
    if request.identity_status not in _IDENTITY_STATUSES:
        issues.append("identity_status_invalid")
    if request.customer_sentiment not in _SENTIMENTS:
        issues.append("customer_sentiment_invalid")
    if len(request.product_ids) > 10:
        issues.append("product_ids_unbounded")
    if len(request.confirmed_fields) > 20:
        issues.append("confirmed_fields_unbounded")
    if request.commitment_deadline is not None and not request.sla_policy_version:
        issues.append("commitment_without_sla_policy")
    return tuple(issues)


def classify_customer_sentiment(message: str) -> str:
    normalized = message.casefold()
    if any(
        term in normalized
        for term in ("angry", "terrible", "unacceptable", "投诉", "生气", "糟糕", "差评")
    ):
        return "negative"
    if any(term in normalized for term in ("thank", "great", "满意", "谢谢", "很好")):
        return "positive"
    return "neutral"
