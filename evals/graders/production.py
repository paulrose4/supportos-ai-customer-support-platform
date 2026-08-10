import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from app.domain.rules import build_answer_plan

_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?|[\u4e00-\u9fff]")
_FORBIDDEN_INTERNAL_PHRASES = (
    "available evidence",
    "based on the evidence",
    "from the provided information",
    "i can't verify",
    "i cannot verify",
    "knowledge base",
    "retrieved chunk",
    "system prompt",
    "根据现有资料",
    "知识库中",
    "检索结果",
)
_UNSUPPORTED_COMMITMENTS = (
    "100% guaranteed",
    "guaranteed delivery",
    "definitely arrive",
    "promise it will",
    "保证送达",
    "一定会到",
)


@dataclass(frozen=True, slots=True)
class ProductionCaseGrade:
    case_id: str
    intent_correct: bool
    next_action_correct: bool
    numeric_claims_supported: bool
    citations_supported: bool
    required_content_present: bool
    forbidden_content_absent: bool
    response_within_limit: bool
    cross_tenant_safe: bool

    @property
    def passed(self) -> bool:
        values = asdict(self)
        return all(value for key, value in values.items() if key != "case_id")


@dataclass(frozen=True, slots=True)
class ProductionGateReport:
    case_count: int
    passed_case_count: int
    metrics: dict[str, float]
    hard_failures: dict[str, int]
    failed_case_ids: tuple[str, ...]

    def passes(self, thresholds: Mapping[str, float | int]) -> bool:
        for name, minimum in thresholds.items():
            if name.endswith("_count_max"):
                metric_name = name.removesuffix("_max")
                if self.hard_failures.get(metric_name, 0) > minimum:
                    return False
            elif self.metrics.get(name, 0.0) < minimum:
                return False
        return True


def grade_production_case(case: Mapping[str, Any]) -> ProductionCaseGrade:
    message = _required_text(case, "message")
    response = _required_text(case, "response")
    product_ids = tuple(str(value) for value in case.get("product_ids", ()))
    plan = build_answer_plan(message=message, product_ids=product_ids)
    evidence = tuple(str(value) for value in case.get("evidence", ()))
    approved_numbers = {_normalize_number(str(value)) for value in case.get("approved_numbers", ())}
    evidence_numbers = {
        _normalize_number(value) for text in evidence for value in _NUMBER.findall(text)
    }
    response_numbers = {_normalize_number(value) for value in _NUMBER.findall(response)}
    citations = {str(value) for value in case.get("citations", ())}
    evidence_ids = {str(value) for value in case.get("evidence_ids", ())}
    normalized_response = response.casefold()
    forbidden = tuple(_FORBIDDEN_INTERNAL_PHRASES) + tuple(
        str(value).casefold() for value in case.get("must_not_contain", ())
    )
    forbidden += _UNSUPPORTED_COMMITMENTS
    tenant_tokens = tuple(
        str(value).casefold() for value in case.get("forbidden_tenant_tokens", ())
    )
    required = tuple(str(value).casefold() for value in case.get("must_contain", ()))
    word_limit = int(case.get("max_words") or plan.max_words)
    return ProductionCaseGrade(
        case_id=_required_text(case, "id"),
        intent_correct=plan.intent.value == _required_text(case, "expected_intent"),
        next_action_correct=plan.next_action.value == _required_text(case, "expected_next_action"),
        numeric_claims_supported=response_numbers <= (evidence_numbers | approved_numbers),
        citations_supported=citations <= evidence_ids and (not evidence_ids or bool(citations)),
        required_content_present=all(value in normalized_response for value in required),
        forbidden_content_absent=not any(value in normalized_response for value in forbidden),
        response_within_limit=len(_WORD.findall(response)) <= word_limit,
        cross_tenant_safe=not any(value in normalized_response for value in tenant_tokens),
    )


def build_gate_report(grades: Sequence[ProductionCaseGrade]) -> ProductionGateReport:
    if not grades:
        raise ValueError("production evaluation requires at least one case")
    count = len(grades)

    def rate(attribute: str) -> float:
        return sum(bool(getattr(grade, attribute)) for grade in grades) / count

    metrics = {
        "case_pass_rate": sum(grade.passed for grade in grades) / count,
        "intent_accuracy": rate("intent_correct"),
        "next_action_accuracy": rate("next_action_correct"),
        "numeric_support_rate": rate("numeric_claims_supported"),
        "citation_support_rate": rate("citations_supported"),
        "required_content_rate": rate("required_content_present"),
        "forbidden_language_free_rate": rate("forbidden_content_absent"),
        "response_limit_rate": rate("response_within_limit"),
        "cross_tenant_safety_rate": rate("cross_tenant_safe"),
    }
    hard_failures = {
        "unsupported_numeric_count": sum(not grade.numeric_claims_supported for grade in grades),
        "unsupported_citation_count": sum(not grade.citations_supported for grade in grades),
        "forbidden_language_count": sum(not grade.forbidden_content_absent for grade in grades),
        "cross_tenant_leakage_count": sum(not grade.cross_tenant_safe for grade in grades),
    }
    return ProductionGateReport(
        case_count=count,
        passed_case_count=sum(grade.passed for grade in grades),
        metrics=metrics,
        hard_failures=hard_failures,
        failed_case_ids=tuple(grade.case_id for grade in grades if not grade.passed),
    )


def _normalize_number(value: str) -> str:
    return value.replace(",", ".").lstrip("0") or "0"


def _required_text(case: Mapping[str, Any], field: str) -> str:
    value = str(case.get(field) or "").strip()
    if not value:
        raise ValueError(f"production evaluation field is required: {field}")
    return value
