import re
from collections.abc import Sequence
from typing import Any

from app.domain.models import ResponseKind, ResponseValidationResult, RiskLevel
from app.domain.rules.conversation import detect_message_language
from app.domain.rules.sales import question_key

_PROHIBITED_UNVERIFIED_PROMISES = ("马上", "很快", "24 小时内")
_EMAIL_PATTERN = re.compile(r"(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_INTERNAL_REASONING_PATTERN = re.compile(
    r"(?im)(?:<\/?think\b|^\s*(?:analysis|reasoning|chain[ -]of[ -]thought|"
    r"思考过程|推理过程|分析过程)\s*[:：])"
)
_URL_PATTERN = re.compile(r"https?://([^/\s?#]+)", re.IGNORECASE)
_ABSOLUTE_SALES_CLAIMS = (
    "100% legal",
    "completely legal",
    "absolutely safe",
    "guaranteed delivery",
    "only today",
    "last chance",
    "完全合法",
    "绝对安全",
    "保证到货",
    "仅限今天",
    "最后机会",
    "vollständig legal",
    "garantierte lieferung",
)
_REWRITEABLE_VALIDATION_REASONS = frozenset(
    {
        "empty_response",
        "internal_reasoning_exposed",
        "response_language_mismatch",
        "repeated_customer_question",
        "repeated_agent_introduction",
        "clarification_has_citations",
        "response_repeats_recent_phrase",
        "unplanned_follow_up",
    }
)
_CLARIFICATION_VALIDATION_REASONS = frozenset(
    {
        "knowledge_evidence_missing",
        "knowledge_citation_invalid",
    }
)


def redact_sensitive_text(text: str) -> str:
    redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
    redacted = _PHONE_PATTERN.sub("[REDACTED_PHONE]", redacted)
    return _BEARER_PATTERN.sub("[REDACTED_TOKEN]", redacted)


def response_validation_failure_action(reason_code: str | None, *, rewrite_count: int) -> str:
    if reason_code in _REWRITEABLE_VALIDATION_REASONS and rewrite_count < 1:
        return "rewrite"
    if reason_code in _CLARIFICATION_VALIDATION_REASONS:
        return "clarify"
    return "handoff"


def validate_response_draft(
    *,
    message: str,
    response_kind: ResponseKind,
    risk_level: RiskLevel,
    citations: Sequence[str],
    knowledge_status: str | None,
    knowledge_evidence: Sequence[dict[str, Any]],
    business_status: str | None,
    business_evidence: Sequence[dict[str, Any]],
    tool_executions: Sequence[dict[str, Any]],
    care_procedure_ids: Sequence[str] = (),
    care_step_ids: Sequence[str] = (),
    sales_plan: dict[str, Any] | None = None,
    language_context: dict[str, Any] | None = None,
    site_identity: dict[str, Any] | None = None,
    conversation_memory: dict[str, Any] | None = None,
) -> ResponseValidationResult:
    if not message.strip():
        return ResponseValidationResult(False, "empty_response")
    if any(term in message for term in _PROHIBITED_UNVERIFIED_PROMISES):
        return ResponseValidationResult(False, "unapproved_sla_promise")
    if redact_sensitive_text(message) != message:
        return ResponseValidationResult(False, "sensitive_data_in_response")
    if _INTERNAL_REASONING_PATTERN.search(message):
        return ResponseValidationResult(False, "internal_reasoning_exposed")
    sales_validation = _validate_sales_expression(
        message=message,
        response_kind=response_kind,
        sales_plan=sales_plan or {},
        language_context=language_context or {},
        site_identity=site_identity or {},
        conversation_memory=conversation_memory or {},
    )
    if sales_validation is not None:
        return sales_validation

    if response_kind is ResponseKind.CLARIFICATION:
        if citations:
            return ResponseValidationResult(False, "clarification_has_citations")
        return ResponseValidationResult(True)

    if response_kind is ResponseKind.HANDOFF:
        if citations:
            return ResponseValidationResult(False, "handoff_has_citations")
        return ResponseValidationResult(True)

    if response_kind is not ResponseKind.ANSWER:
        return ResponseValidationResult(False, "unsupported_response_kind")

    if risk_level is RiskLevel.PUBLIC:
        return _validate_knowledge_answer(
            citations,
            knowledge_status,
            knowledge_evidence,
            care_procedure_ids,
            care_step_ids,
        )
    if risk_level is RiskLevel.AUTHENTICATED_READ:
        return _validate_business_answer(
            citations,
            business_status,
            business_evidence,
            tool_executions,
        )
    return ResponseValidationResult(False, "answer_not_allowed_for_risk")


def _validate_knowledge_answer(
    citations: Sequence[str],
    knowledge_status: str | None,
    evidence: Sequence[dict[str, Any]],
    care_procedure_ids: Sequence[str],
    care_step_ids: Sequence[str],
) -> ResponseValidationResult:
    if knowledge_status == "care_guidance":
        expected_procedures = {
            str(item.get("procedure_id")) for item in evidence if item.get("procedure_id")
        }
        expected_steps = {
            str(step_id)
            for item in evidence
            for step_id in item.get("approved_step_ids", [])
            if step_id
        }
        if not care_procedure_ids or not set(care_procedure_ids).issubset(expected_procedures):
            return ResponseValidationResult(False, "care_procedure_invalid")
        if not care_step_ids or not set(care_step_ids).issubset(expected_steps):
            return ResponseValidationResult(False, "care_step_invalid")
        expected_citations = {
            f"{item.get('source')}#{item.get('chunk_id')}"
            for item in evidence
            if item.get("source") and item.get("chunk_id")
        }
        if not citations or not set(citations).issubset(expected_citations):
            return ResponseValidationResult(False, "knowledge_citation_invalid")
        return ResponseValidationResult(True)
    if knowledge_status == "general_guidance":
        if not citations:
            return ResponseValidationResult(True)
        expected = {
            f"{item.get('source')}#{item.get('chunk_id')}"
            for item in evidence
            if item.get("source") and item.get("chunk_id")
        }
        if expected and set(citations).issubset(expected):
            return ResponseValidationResult(True)
        return ResponseValidationResult(False, "knowledge_citation_invalid")
    if knowledge_status != "sufficient" or not evidence:
        return ResponseValidationResult(False, "knowledge_evidence_missing")
    expected = {
        f"{item.get('source')}#{item.get('chunk_id')}"
        for item in evidence
        if item.get("source") and item.get("chunk_id")
    }
    if not citations or not set(citations).issubset(expected):
        return ResponseValidationResult(False, "knowledge_citation_invalid")
    return ResponseValidationResult(True)


def _validate_business_answer(
    citations: Sequence[str],
    business_status: str | None,
    evidence: Sequence[dict[str, Any]],
    tool_executions: Sequence[dict[str, Any]],
) -> ResponseValidationResult:
    if business_status != "sufficient" or not evidence:
        return ResponseValidationResult(False, "business_evidence_missing")
    if not tool_executions or any(item.get("status") != "succeeded" for item in tool_executions):
        return ResponseValidationResult(False, "business_tool_not_successful")
    expected = {_business_citation(item) for item in evidence}
    expected.discard(None)
    if not citations or not set(citations).issubset(expected):
        return ResponseValidationResult(False, "business_citation_invalid")
    return ResponseValidationResult(True)


def _business_citation(evidence: dict[str, Any]) -> str | None:
    source = evidence.get("source")
    facts = evidence.get("facts") or {}
    resource_id = facts.get("resource_id")
    if not source or not resource_id:
        return None
    version = evidence.get("version")
    suffix = f"@v{version}" if version else ""
    citation_source = str(source).replace(".", ":")
    return f"{citation_source}:{resource_id}{suffix}"


def _validate_sales_expression(
    *,
    message: str,
    response_kind: ResponseKind,
    sales_plan: dict[str, Any],
    language_context: dict[str, Any],
    site_identity: dict[str, Any],
    conversation_memory: dict[str, Any],
) -> ResponseValidationResult | None:
    normalized = message.casefold()
    if any(claim in normalized for claim in _ABSOLUTE_SALES_CLAIMS):
        return ResponseValidationResult(False, "prohibited_sales_claim")

    trusted_domain = str(site_identity.get("domain") or "").casefold()
    if trusted_domain:
        response_domains = {match.casefold() for match in _URL_PATTERN.findall(message)}
        if any(
            domain != trusted_domain and not domain.endswith(f".{trusted_domain}")
            for domain in response_domains
        ):
            return ResponseValidationResult(False, "cross_site_domain")

    target_language = (
        str(language_context.get("target_language") or sales_plan.get("target_language") or "")
        .replace("_", "-")
        .casefold()
        .split("-", 1)[0]
    )
    detected_language = detect_message_language(message)
    if (
        target_language
        and detected_language
        and detected_language != target_language
        and len(re.findall(r"[A-Za-z\u4e00-\u9fff]", message)) >= 20
    ):
        return ResponseValidationResult(False, "response_language_mismatch")

    asked_keys = {
        str(item.get("key"))
        for item in conversation_memory.get("question_ledger", [])
        if isinstance(item, dict) and item.get("key")
    }
    questions = re.findall(r"[^?？\n]{2,240}[?？]", message)
    if asked_keys and any(question_key(item) in asked_keys for item in questions):
        return ResponseValidationResult(False, "repeated_customer_question")

    if int(conversation_memory.get("revision", 0)) > 0:
        agent_name = str(site_identity.get("agent_display_name") or "").casefold()
        repeated_introductions = (
            f"i am {agent_name}",
            f"i'm {agent_name}",
            f"我是{agent_name}",
            f"ich bin {agent_name}",
        )
        if agent_name and any(value in normalized for value in repeated_introductions):
            return ResponseValidationResult(False, "repeated_agent_introduction")
    recent_phrases = tuple(
        str(value).strip().casefold()
        for value in sales_plan.get("recent_phrases_to_avoid", [])
        if len(str(value).strip()) >= 12
    )
    if any(phrase in normalized for phrase in recent_phrases):
        return ResponseValidationResult(False, "response_repeats_recent_phrase")
    planned_follow_up = sales_plan.get("follow_up_question_key")
    if (
        sales_plan
        and response_kind is ResponseKind.ANSWER
        and questions
        and not planned_follow_up
        and response_kind_allows_no_question(sales_plan)
    ):
        return ResponseValidationResult(False, "unplanned_follow_up")
    return None


def response_kind_allows_no_question(sales_plan: dict[str, Any]) -> bool:
    return str(sales_plan.get("next_best_action") or "answer") in {
        "answer",
        "recommend",
        "compare",
        "address_objection",
        "offer_purchase_path",
    }
