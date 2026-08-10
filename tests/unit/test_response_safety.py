from app.domain.models import ResponseKind, RiskLevel
from app.domain.rules import (
    redact_sensitive_text,
    response_validation_failure_action,
    validate_response_draft,
)


def test_redactor_removes_email_phone_and_bearer_token() -> None:
    text = "联系 me@example.com 或 13812345678，Bearer secret-token-value"

    redacted = redact_sensitive_text(text)

    assert "me@example.com" not in redacted
    assert "13812345678" not in redacted
    assert "secret-token-value" not in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted
    assert "[REDACTED_TOKEN]" in redacted


def test_validator_accepts_grounded_knowledge_answer() -> None:
    result = validate_response_draft(
        message="根据知识库回答。",
        response_kind=ResponseKind.ANSWER,
        risk_level=RiskLevel.PUBLIC,
        citations=["faq.md#chunk-1"],
        knowledge_status="sufficient",
        knowledge_evidence=[{"source": "faq.md", "chunk_id": "chunk-1"}],
        business_status=None,
        business_evidence=[],
        tool_executions=[],
    )

    assert result.is_valid


def test_validator_rejects_unsupported_citation() -> None:
    result = validate_response_draft(
        message="没有证据支持的回答。",
        response_kind=ResponseKind.ANSWER,
        risk_level=RiskLevel.PUBLIC,
        citations=["other.md#chunk-9"],
        knowledge_status="sufficient",
        knowledge_evidence=[{"source": "faq.md", "chunk_id": "chunk-1"}],
        business_status=None,
        business_evidence=[],
        tool_executions=[],
    )

    assert not result.is_valid
    assert result.reason_code == "knowledge_citation_invalid"


def test_validator_accepts_low_risk_general_guidance_without_citations() -> None:
    result = validate_response_draft(
        message="我们建议用温水和温和清洁剂轻柔清洁，并彻底阴干后再收纳。",
        response_kind=ResponseKind.ANSWER,
        risk_level=RiskLevel.PUBLIC,
        citations=[],
        knowledge_status="general_guidance",
        knowledge_evidence=[],
        business_status=None,
        business_evidence=[],
        tool_executions=[],
    )

    assert result.is_valid


def test_validator_rejects_internal_reasoning_labels() -> None:
    result = validate_response_draft(
        message="Analysis: I should answer from the store perspective.\n我们建议温和清洁。",
        response_kind=ResponseKind.ANSWER,
        risk_level=RiskLevel.PUBLIC,
        citations=[],
        knowledge_status="general_guidance",
        knowledge_evidence=[],
        business_status=None,
        business_evidence=[],
        tool_executions=[],
    )

    assert not result.is_valid
    assert result.reason_code == "internal_reasoning_exposed"


def test_validator_requires_care_procedure_and_step_provenance() -> None:
    evidence = [
        {
            "source": "care.md",
            "chunk_id": "care-chunk",
            "procedure_id": "care.tpe.v1",
            "approved_step_ids": ["care.tpe.keep-dry"],
        }
    ]
    valid = validate_response_draft(
        message="1. 保持产品干燥。",
        response_kind=ResponseKind.ANSWER,
        risk_level=RiskLevel.PUBLIC,
        citations=["care.md#care-chunk"],
        knowledge_status="care_guidance",
        knowledge_evidence=evidence,
        business_status=None,
        business_evidence=[],
        tool_executions=[],
        care_procedure_ids=["care.tpe.v1"],
        care_step_ids=["care.tpe.keep-dry"],
    )
    invalid = validate_response_draft(
        message="1. 使用未经审核的护理粉。",
        response_kind=ResponseKind.ANSWER,
        risk_level=RiskLevel.PUBLIC,
        citations=["care.md#care-chunk"],
        knowledge_status="care_guidance",
        knowledge_evidence=evidence,
        business_status=None,
        business_evidence=[],
        tool_executions=[],
        care_procedure_ids=["care.tpe.v1"],
        care_step_ids=["care.tpe.unapproved-powder"],
    )

    assert valid.is_valid
    assert not invalid.is_valid
    assert invalid.reason_code == "care_step_invalid"


def test_validator_rejects_business_answer_after_failed_tool() -> None:
    result = validate_response_draft(
        message="订单 DEMO-ORDER-1001 当前状态为：已发货。",
        response_kind=ResponseKind.ANSWER,
        risk_level=RiskLevel.AUTHENTICATED_READ,
        citations=["postgres:orders:DEMO-ORDER-1001@v1"],
        knowledge_status=None,
        knowledge_evidence=[],
        business_status="sufficient",
        business_evidence=[
            {
                "source": "postgres.orders",
                "version": "1",
                "facts": {"resource_id": "DEMO-ORDER-1001", "status": "shipped"},
            }
        ],
        tool_executions=[{"tool_name": "query_order_status", "status": "failed"}],
    )

    assert not result.is_valid
    assert result.reason_code == "business_tool_not_successful"


def test_validator_rejects_sensitive_output_and_unapproved_sla() -> None:
    sensitive = validate_response_draft(
        message="请联系 me@example.com。",
        response_kind=ResponseKind.CLARIFICATION,
        risk_level=RiskLevel.PUBLIC,
        citations=[],
        knowledge_status=None,
        knowledge_evidence=[],
        business_status=None,
        business_evidence=[],
        tool_executions=[],
    )
    promised = validate_response_draft(
        message="我们会在 24 小时内处理。",
        response_kind=ResponseKind.CLARIFICATION,
        risk_level=RiskLevel.PUBLIC,
        citations=[],
        knowledge_status=None,
        knowledge_evidence=[],
        business_status=None,
        business_evidence=[],
        tool_executions=[],
    )

    assert sensitive.reason_code == "sensitive_data_in_response"
    assert promised.reason_code == "unapproved_sla_promise"


def test_validator_rejects_cross_site_language_mismatch_and_repeated_question() -> None:
    common = {
        "response_kind": ResponseKind.CLARIFICATION,
        "risk_level": RiskLevel.PUBLIC,
        "citations": [],
        "knowledge_status": None,
        "knowledge_evidence": [],
        "business_status": None,
        "business_evidence": [],
        "tool_executions": [],
    }
    cross_site = validate_response_draft(
        message="Please continue at https://other.example.com/product/1.",
        site_identity={"domain": "shop.example.com"},
        **common,
    )
    wrong_language = validate_response_draft(
        message="这是一个完整的中文客服回复，但客户当前明确要求我们使用英语进行回答。",
        language_context={"target_language": "en"},
        **common,
    )
    repeated = validate_response_draft(
        message="What is your budget?",
        conversation_memory={
            "question_ledger": [{"key": "budget", "text": "What is your budget?"}]
        },
        **common,
    )

    assert cross_site.reason_code == "cross_site_domain"
    assert wrong_language.reason_code == "response_language_mismatch"
    assert repeated.reason_code == "repeated_customer_question"


def test_validation_failure_action_is_fail_closed_after_one_rewrite() -> None:
    assert response_validation_failure_action("response_language_mismatch", rewrite_count=0) == (
        "rewrite"
    )
    assert response_validation_failure_action("response_language_mismatch", rewrite_count=1) == (
        "handoff"
    )
    assert response_validation_failure_action("knowledge_evidence_missing", rewrite_count=0) == (
        "clarify"
    )
    assert response_validation_failure_action("cross_site_domain", rewrite_count=0) == "handoff"
