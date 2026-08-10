from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.dto import AnswerKnowledgeCommand
from app.application.services import AnswerKnowledgeService
from app.domain.models import (
    AuthenticatedPrincipal,
    CareRiskTier,
    ConversationTurn,
    KnowledgeEvidence,
)
from app.domain.ports import ChatModelRequest, ChatModelResult
from app.domain.rules import classify_care_risk, evaluate_care_guidance
from app.knowledge import MarkdownKnowledgeParser
from tests.fakes.adapters import InMemoryKnowledgeControlPlane, InMemoryKnowledgeRetriever


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="customer-1",
        tenant_id="tenant-a",
        roles=frozenset({"customer"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="mock",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
        site_id="site-a",
    )


def _product(*, special_feature: str = "") -> KnowledgeEvidence:
    feature_text = f" Features: {special_feature}." if special_feature else ""
    return KnowledgeEvidence(
        chunk_id="product-chunk",
        document_id="product-1",
        text=f"Model SAFE100 uses TPE material.{feature_text}",
        score=0.95,
        source="https://shop.example.test/products/safe100.html",
        metadata={
            "tenant_id": "tenant-a",
            "site_id": "site-a",
            "category": "product",
            "product": {"sku": "SAFE100", "material": "TPE"},
        },
    )


def _sop() -> KnowledgeEvidence:
    return KnowledgeEvidence(
        chunk_id="sop-chunk",
        document_id="care-sop-tpe",
        text="Approved TPE care procedure for cleaning and storage.",
        score=0.9,
        source="tpe-care-sop.md",
        metadata={
            "tenant_id": "__global__",
            "category": "product_care_sop",
            "status": "published",
            "approval_status": "approved",
            "authority_level": 90,
            "reviewer": "Jane Reviewer",
            "reviewed_at": "2026-07-20T00:00:00Z",
            "approval_references": ["supplier-manual-safe100-rev-a"],
            "procedure_id": "care.tpe.baseline.v1",
            "applicable_materials": ["tpe"],
            "version": "1.0.0",
            "prohibited_actions": ["unapproved_cleaner"],
            "approved_steps": [
                {
                    "step_id": "care.tpe.keep-dry",
                    "instructions": {
                        "en": "Keep the product dry and follow the model-specific storage guide.",
                        "zh": "保持产品干燥，并按照该型号的收纳说明存放。",
                    },
                }
            ],
        },
    )


def _localized_sop() -> KnowledgeEvidence:
    evidence = _sop()
    metadata = {
        **evidence.metadata,
        "care_pack_id": "company-multi-material-care",
        "care_pack_version": "1.0.0",
        "care_locales": ["en", "de-DE"],
        "approved_steps": [
            {
                "step_id": "care.tpe.keep-dry",
                "instructions": {
                    "en": "Keep the product dry and follow the model-specific storage guide.",
                    "de": (
                        "Halten Sie das Produkt trocken und beachten Sie die Aufbewahrungshinweise."
                    ),
                },
            }
        ],
    }
    return replace(evidence, metadata=metadata)


def _approved_care_evidence(*, material: str) -> KnowledgeEvidence:
    procedure_material = material.replace("_", "-")
    return KnowledgeEvidence(
        chunk_id=f"sop-{procedure_material}-chunk",
        document_id=f"care-sop-{procedure_material}",
        text=f"Approved {material} care procedure.",
        score=0.9,
        source=f"care-sop-{procedure_material}.md",
        metadata={
            "tenant_id": "__global__",
            "category": "product_care_sop",
            "status": "published",
            "approval_status": "approved",
            "authority_level": 90,
            "reviewer": "Jane Reviewer",
            "reviewed_at": "2026-07-20T00:00:00Z",
            "approval_references": ["supplier-manual-hybrid100-rev-a"],
            "procedure_id": f"care.{procedure_material}.baseline.v1",
            "applicable_materials": [material],
            "version": "1.0.0",
            "prohibited_actions": ["unapproved_cleaner"],
            "approved_steps": [
                {
                    "step_id": f"care.{procedure_material}.keep-dry",
                    "instructions": {
                        "en": "Keep the product dry and follow its model-specific guide.",
                        "zh": "保持产品干燥，并按照对应型号的说明操作。",
                    },
                }
            ],
        },
    )


def _approved_general_care_evidence() -> KnowledgeEvidence:
    return KnowledgeEvidence(
        chunk_id="general-care-drying",
        document_id="multi-material-care-general",
        text=(
            "For routine care, inspect for damage first, clean gently with soft non-abrasive "
            "tools, remove residue, dry completely in shade with ventilation, avoid direct heat "
            "and dark fabrics, and stop if there is damage, mold, or an electrical feature."
        ),
        score=0.92,
        source="multi-material-care-general.md",
        metadata={
            "tenant_id": "__global__",
            "category": "product_care_general",
            "status": "published",
            "approval_status": "approved",
            "guidance_scope": "universal_low_risk",
            "authority_level": 90,
            "reviewer": "Jane Reviewer",
            "reviewed_at": "2026-07-20T00:00:00Z",
            "approval_references": ["supplier-care-review-2026-07"],
            "approved_responses": {
                "en": "Inspect gently, dry fully, avoid heat, and tell us the material.",
                "zh": "轻柔检查并彻底干燥，避免高温，并告诉我们产品材质。",
            },
        },
    )


def test_care_risk_classification_is_deterministic() -> None:
    assert classify_care_risk("怎么清洁 TPE 商品？") is CareRiskTier.MATERIAL_DEPENDENT
    assert classify_care_risk("加热系统坏了怎么拆修？") is CareRiskTier.HIGH
    assert classify_care_risk("这款多少钱？") is None


def test_care_guidance_requires_product_identification() -> None:
    decision = evaluate_care_guidance(
        message="TPE商品要怎么保养？",
        response_language="zh-CN",
        page_path="/unknown.html",
        evidence=[_sop()],
    )

    assert decision.status == "clarification"
    assert decision.reason_code == "care_product_unidentified"


def test_care_guidance_uses_approved_general_rag_when_product_is_unknown() -> None:
    decision = evaluate_care_guidance(
        message="平时应该怎么清洁和收纳？",
        response_language="zh-CN",
        page_path="/support.html",
        evidence=[_approved_general_care_evidence()],
    )

    assert decision.status == "general"
    assert decision.reason_code == "care_general_guidance"


def test_care_guidance_falls_back_to_general_rag_when_material_sop_is_missing() -> None:
    decision = evaluate_care_guidance(
        message="SAFE100平时怎么清洁？",
        response_language="zh-CN",
        page_path="/products/safe100.html",
        evidence=[_product(), _approved_general_care_evidence()],
    )

    assert decision.status == "general"
    assert decision.material == "tpe"
    assert decision.reason_code == "care_material_sop_missing"


def test_care_guidance_uses_only_matching_approved_sop() -> None:
    decision = evaluate_care_guidance(
        message="这款商品应该怎么清洁和收纳？",
        response_language="zh-CN",
        page_path="/products/safe100.html",
        evidence=[_product(), _sop()],
    )

    assert decision.status == "approved"
    assert decision.material == "tpe"
    assert decision.procedure is not None
    assert decision.procedure.procedure_id == "care.tpe.baseline.v1"
    assert decision.procedure.steps[0].instruction == "保持产品干燥，并按照该型号的收纳说明存放。"


def test_care_guidance_selects_company_language_pack_without_site_copy() -> None:
    decision = evaluate_care_guidance(
        message="Wie sollte ich dieses Produkt reinigen?",
        response_language="de-DE",
        page_path="/products/safe100.html",
        evidence=[_product(), _localized_sop()],
    )

    assert decision.status == "approved"
    assert decision.procedure is not None
    assert decision.procedure.steps[0].instruction.startswith("Halten Sie")


def test_care_guidance_does_not_silently_fallback_to_english() -> None:
    decision = evaluate_care_guidance(
        message="Wie sollte ich dieses Produkt reinigen?",
        response_language="de-DE",
        page_path="/products/safe100.html",
        evidence=[_product(), _sop()],
    )

    assert decision.status == "handoff"
    assert decision.reason_code == "care_sop_missing"


def test_care_guidance_can_confirm_product_from_evidence_backed_customer_link() -> None:
    aliased_product = _product()
    aliased_product = KnowledgeEvidence(
        chunk_id=aliased_product.chunk_id,
        document_id=aliased_product.document_id,
        text=aliased_product.text,
        score=aliased_product.score,
        source=aliased_product.source,
        metadata={
            **aliased_product.metadata,
            "requested_url": "https://shop.example.test/agent-test.html?121",
        },
    )
    decision = evaluate_care_guidance(
        message=("这个商品怎么保养？ https://shop.example.test/agent-test.html?121"),
        response_language="zh-CN",
        page_path="/support.html",
        evidence=[aliased_product, _sop()],
    )

    assert decision.status == "approved"
    assert decision.product_source == "https://shop.example.test/products/safe100.html"


async def test_url_followup_inherits_care_intent_and_requests_exact_source_filter() -> None:
    model = _ExpertCareChatModel()
    product = _product()
    product = KnowledgeEvidence(
        chunk_id=product.chunk_id,
        document_id=product.document_id,
        text=product.text,
        score=product.score,
        source=product.source,
        metadata={
            **product.metadata,
            "requested_url": "https://shop.example.test/agent-test.html?121",
            "requested_path": "/agent-test.html",
        },
    )
    retriever = InMemoryKnowledgeRetriever([product])
    service = AnswerKnowledgeService(
        retriever=retriever,
        chat_model=model,
        control_plane=InMemoryKnowledgeControlPlane(),
    )

    result = await service.execute(
        AnswerKnowledgeCommand(
            principal=_principal(),
            question="https://shop.example.test/agent-test.html?121",
            language="en",
            page_path="/support.html",
            conversation_history=(
                ConversationTurn(role="user", content="How should I care for a TPE product?"),
                ConversationTurn(role="assistant", content="Please send the product link."),
            ),
        )
    )

    assert result.status == "care_sop_missing"
    assert result.message is None
    assert result.citations == ()
    assert result.related_links == ()
    assert retriever.queries[0].filters["source_urls"] == (
        "https://shop.example.test/agent-test.html?121",
    )
    assert retriever.queries[0].filters["source_paths"] == ("/agent-test.html",)
    assert "How should I care for a TPE product?" in retriever.queries[0].text
    assert model.requests == []


async def test_generic_care_does_not_borrow_unmatched_product_facts() -> None:
    model = _ExpertCareChatModel()
    retriever = InMemoryKnowledgeRetriever([_product()])
    service = AnswerKnowledgeService(
        retriever=retriever,
        chat_model=model,
        control_plane=InMemoryKnowledgeControlPlane(),
    )

    result = await service.execute(
        AnswerKnowledgeCommand(
            principal=_principal(),
            question="How should I care for a TPE product?",
            language="en",
        )
    )

    assert result.status == "care_clarification"
    assert result.citations == ()
    assert "product link or exact model number" in (result.message or "")
    assert "powders, oils, and self-repair" in (result.message or "")
    assert retriever.queries
    assert model.requests == []


async def test_generic_care_uses_approved_global_rag_before_one_followup() -> None:
    model = _ExpertCareChatModel()
    general_care = _approved_general_care_evidence()
    retriever = InMemoryKnowledgeRetriever([general_care])
    service = AnswerKnowledgeService(
        retriever=retriever,
        chat_model=model,
        control_plane=InMemoryKnowledgeControlPlane(),
    )

    result = await service.execute(
        AnswerKnowledgeCommand(
            principal=_principal(),
            question="平时怎么清洁和收纳？",
            language="zh-CN",
            page_path="/support.html",
        )
    )

    assert result.status == "general_guidance"
    assert result.citations == ("multi-material-care-general.md#general-care-drying",)
    assert retriever.queries
    assert any(
        query.filters.get("categories") == ("product_care_general", "product_care_sop")
        for query in retriever.queries
    )
    assert len(model.requests) == 1
    system_prompt = model.requests[0].messages[0]["content"]
    assert "use only the approved global care evidence" in system_prompt
    assert "Do not add care instructions from general model knowledge" in system_prompt


async def test_generic_care_uses_reviewed_rag_response_when_model_is_unavailable() -> None:
    general_care = _approved_general_care_evidence()
    service = AnswerKnowledgeService(
        retriever=InMemoryKnowledgeRetriever([general_care]),
        chat_model=_FailingChatModel(),
        control_plane=InMemoryKnowledgeControlPlane(),
    )

    result = await service.execute(
        AnswerKnowledgeCommand(
            principal=_principal(),
            question="平时怎么清洁和收纳？",
            language="zh-CN",
            page_path="/support.html",
        )
    )

    assert result.status == "general_guidance"
    assert result.message == "轻柔检查并彻底干燥，避免高温，并告诉我们产品材质。"
    assert result.citations == ("multi-material-care-general.md#general-care-drying",)
    assert result.retrieval_degraded


def test_care_guidance_hands_off_special_features_before_sop_use() -> None:
    decision = evaluate_care_guidance(
        message="这款商品怎么保养？",
        response_language="zh-CN",
        page_path="/products/safe100.html",
        evidence=[_product(special_feature="electronic heating system"), _sop()],
    )

    assert decision.status == "handoff"
    assert decision.reason_code == "care_special_feature"


async def test_service_renders_approved_steps_without_calling_llm() -> None:
    service = AnswerKnowledgeService(
        retriever=InMemoryKnowledgeRetriever([_product(), _sop()]),
        chat_model=_ForbiddenChatModel(),
        control_plane=InMemoryKnowledgeControlPlane(),
    )

    result = await service.execute(
        AnswerKnowledgeCommand(
            principal=_principal(),
            question="这款商品应该怎么清洁和收纳？",
            language="zh-CN",
            page_path="/products/safe100.html",
        )
    )

    assert result.status == "care_guidance"
    assert "保持产品干燥" in (result.message or "")
    assert result.care_procedure_ids == ("care.tpe.baseline.v1",)
    assert result.care_step_ids == ("care.tpe.keep-dry",)
    assert result.citations == (
        "tpe-care-sop.md#sop-chunk",
        "https://shop.example.test/products/safe100.html#product-chunk",
    )


async def test_service_uses_bounded_clarification_when_product_is_not_identified() -> None:
    model = _ExpertCareChatModel()
    service = AnswerKnowledgeService(
        retriever=InMemoryKnowledgeRetriever([_sop()]),
        chat_model=model,
        control_plane=InMemoryKnowledgeControlPlane(),
    )

    result = await service.execute(
        AnswerKnowledgeCommand(
            principal=_principal(),
            question="TPE商品要怎么保养最好呢？",
            language="zh-CN",
        )
    )

    assert result.status == "care_clarification"
    assert "商品链接或准确型号" in (result.message or "")
    assert "粉类、油类和自行拆修" in (result.message or "")
    assert model.requests == []


async def test_service_requires_handoff_when_product_sop_is_missing() -> None:
    model = _ExpertCareChatModel()
    service = AnswerKnowledgeService(
        retriever=InMemoryKnowledgeRetriever([_product()]),
        chat_model=model,
        control_plane=InMemoryKnowledgeControlPlane(),
    )

    result = await service.execute(
        AnswerKnowledgeCommand(
            principal=_principal(),
            question="这款商品应该怎么保养？",
            language="zh-CN",
            page_path="/products/safe100.html",
        )
    )

    assert result.status == "care_sop_missing"
    assert result.message is None
    assert result.citations == ()
    assert model.requests == []


async def test_unapproved_private_care_fragment_does_not_bypass_sop_requirement() -> None:
    model = _ExpertCareChatModel()
    private_care = KnowledgeEvidence(
        chunk_id="private-care-chunk",
        document_id="private-care",
        text=("Store care guide for SAFE100: support the torso and legs together when moving it."),
        score=0.97,
        source="https://shop.example.test/guides/safe100-care.html",
        metadata={
            "tenant_id": "tenant-a",
            "site_id": "site-a",
            "category": "product_care",
            "product": {"sku": "SAFE100", "material": "TPE"},
        },
    )
    service = AnswerKnowledgeService(
        retriever=InMemoryKnowledgeRetriever([_product(), private_care]),
        chat_model=model,
        control_plane=InMemoryKnowledgeControlPlane(),
    )

    result = await service.execute(
        AnswerKnowledgeCommand(
            principal=_principal(),
            question="SAFE100应该怎么搬动和保养？",
            language="zh-CN",
        )
    )

    assert result.status == "care_clarification"
    assert result.citations == ()
    assert model.requests == []


async def test_service_never_calls_llm_for_high_risk_care() -> None:
    service = AnswerKnowledgeService(
        retriever=InMemoryKnowledgeRetriever([_product()]),
        chat_model=_ForbiddenChatModel(),
        control_plane=InMemoryKnowledgeControlPlane(),
    )

    result = await service.execute(
        AnswerKnowledgeCommand(
            principal=_principal(),
            question="商品发霉了怎么自行修复？",
            language="zh-CN",
            page_path="/products/safe100.html",
        )
    )

    assert result.status == "care_high_risk"
    assert result.message is None


def test_parser_rejects_unapproved_published_care_sop(tmp_path: Path) -> None:
    path = tmp_path / "care.md"
    path.write_text(_care_markdown(approval_status="pending_review"), encoding="utf-8")

    with pytest.raises(ValueError, match="published care SOP requires approved status"):
        MarkdownKnowledgeParser().parse(path)


def test_parser_accepts_structured_approved_care_sop(tmp_path: Path) -> None:
    path = tmp_path / "care.md"
    path.write_text(_care_markdown(approval_status="approved"), encoding="utf-8")

    document = MarkdownKnowledgeParser().parse(path)

    assert document.metadata["procedure_id"] == "care.tpe.baseline.v1"
    assert document.metadata["approved_steps"][0]["step_id"] == "care.tpe.keep-dry"


def test_parser_accepts_a_company_language_pack_without_english_or_chinese(tmp_path: Path) -> None:
    path = tmp_path / "care-de.md"
    content = (
        _care_markdown(approval_status="approved")
        .replace(
            "language: en\n",
            "language: de-DE\ncare_locales:\n  - de-DE\n",
        )
        .replace(
            (
                "      en: Keep the product dry and follow the model-specific storage guide.\n"
                "      zh: 保持产品干燥，并按照该型号的收纳说明存放。"
            ),
            "      de: Halten Sie das Produkt trocken und beachten Sie die Aufbewahrungshinweise.",
        )
    )
    path.write_text(content, encoding="utf-8")

    document = MarkdownKnowledgeParser().parse(path)

    assert document.metadata["care_locales"] == ["de-DE"]


def test_parser_rejects_a_published_language_pack_with_missing_locale(tmp_path: Path) -> None:
    path = tmp_path / "care-de.md"
    content = _care_markdown(approval_status="approved").replace(
        "language: en\n",
        "language: de-DE\ncare_locales:\n  - de-DE\n",
    )
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="missing a reviewed locale instruction"):
        MarkdownKnowledgeParser().parse(path)


def test_parser_rejects_unreviewed_published_general_care(tmp_path: Path) -> None:
    path = tmp_path / "general-care.md"
    path.write_text(
        _general_care_markdown(approval_status="pending_review"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires approved status"):
        MarkdownKnowledgeParser().parse(path)


def test_parser_accepts_reviewed_published_general_care(tmp_path: Path) -> None:
    path = tmp_path / "general-care.md"
    path.write_text(
        _general_care_markdown(approval_status="approved"),
        encoding="utf-8",
    )

    document = MarkdownKnowledgeParser().parse(path)

    assert document.metadata["guidance_scope"] == "universal_low_risk"
    assert document.metadata["approval_references"] == ["supplier-care-review-2026-07"]


def test_parser_rejects_published_care_sop_without_approval_reference(tmp_path: Path) -> None:
    path = tmp_path / "care.md"
    path.write_text(
        _care_markdown(approval_status="approved").replace(
            "approval_references:\n  - supplier-manual-safe100-rev-a\n",
            "approval_references: []\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires approval_references"):
        MarkdownKnowledgeParser().parse(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("tenant_id", "tenant-a", "must use the __global__ tenant"),
        ("care_pack_id", "", "requires care_pack_id"),
        ("care_pack_version", "", "requires care_pack_version"),
        ("care_scope", "site_local", "requires company_global care_scope"),
    ),
)
def test_parser_rejects_published_care_outside_company_care_pack(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    path = tmp_path / "care.md"
    content = _care_markdown(approval_status="approved")
    if field == "tenant_id":
        content = content.replace("tenant_id: __global__", f"tenant_id: {value}")
    elif field == "care_pack_id":
        content = content.replace("care_pack_id: company-multi-material-care", "care_pack_id: ")
    elif field == "care_pack_version":
        content = content.replace('care_pack_version: "1.0.0"', "care_pack_version: ")
    else:
        content = content.replace("care_scope: company_global", "care_scope: site_local")
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        MarkdownKnowledgeParser().parse(path)


def test_shared_care_package_is_published_with_platform_owner_approval() -> None:
    paths = (
        Path("examples/global-vault/multi-material-product-care-handbook-review.zh-CN.md"),
        Path("examples/global-vault/care-sop-tpe-baseline-review.md"),
        Path("examples/global-vault/care-sop-silicone-baseline-review.md"),
        Path("examples/global-vault/care-sop-pvc-baseline-review.md"),
        Path("examples/global-vault/care-sop-tpe-silicone-hybrid-review.md"),
    )
    parser = MarkdownKnowledgeParser()

    for path in paths:
        document = parser.parse(path)
        assert document.metadata["tenant_id"] == "__global__"
        assert document.metadata["status"] == "published"
        assert document.metadata["approval_status"] == "approved"
        assert document.metadata["reviewer"] == "platform-owner"
        assert document.metadata["approval_references"] == [
            "platform-owner-care-approval-2026-07-30"
        ]
        assert document.metadata["care_pack_id"] == "company-multi-material-care"
        assert document.metadata["care_locales"] == ["en", "zh-CN"]


def test_shared_care_template_remains_unpublished() -> None:
    document = MarkdownKnowledgeParser().parse(
        Path("examples/global-vault/care-sop-tpe-template.md")
    )

    assert document.metadata["status"] == "review"
    assert document.metadata["approval_status"] == "pending_review"


class _ForbiddenChatModel:
    async def generate(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("care procedures must not be generated by the LLM")


class _FailingChatModel:
    async def generate(self, _request):  # type: ignore[no-untyped-def]
        raise RuntimeError("model unavailable")


class _ExpertCareChatModel:
    def __init__(self) -> None:
        self.requests: list[ChatModelRequest] = []

    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        self.requests.append(request)
        product = request.metadata.get("identified_product", {})
        identifier = str(product.get("sku") or "").strip()
        prefix = f"针对 {identifier}，" if identifier else ""
        return ChatModelResult(
            text=f"{prefix}我们建议先温和清洁并彻底干燥，具体方法可结合商品型号进一步确认。",
            model="test",
            metadata={"general_guidance": True},
        )


def _care_markdown(*, approval_status: str) -> str:
    return f"""---
document_id: care-tpe-baseline
tenant_id: __global__
title: TPE Care Baseline
category: product_care_sop
care_pack_id: company-multi-material-care
care_pack_version: "1.0.0"
care_scope: company_global
audience: public
product: all
region: global
language: en
status: published
authority_level: 90
priority: 90
version: "1.0.0"
effective_from: "2026-07-20T00:00:00Z"
effective_to: null
owner_role: product_safety_owner
reviewer: Jane Reviewer
reviewed_at: "2026-07-20T00:00:00Z"
updated_at: "2026-07-20T00:00:00Z"
approval_status: {approval_status}
approval_references:
  - supplier-manual-safe100-rev-a
procedure_id: care.tpe.baseline.v1
applicable_materials:
  - tpe
prohibited_actions:
  - unapproved_cleaner
approved_steps:
  - step_id: care.tpe.keep-dry
    instructions:
      en: Keep the product dry and follow the model-specific storage guide.
      zh: 保持产品干燥，并按照该型号的收纳说明存放。
---
# Reviewed procedure

Use only the structured approved steps.
"""


def _general_care_markdown(*, approval_status: str) -> str:
    return f"""---
document_id: general-care-baseline
tenant_id: __global__
title: General Care Baseline
category: product_care_general
care_pack_id: company-multi-material-care
care_pack_version: "1.0.0"
care_scope: company_global
audience: public
product: all
region: global
language: en
status: published
authority_level: 90
priority: 90
version: "1.0.0"
effective_from: "2026-07-20T00:00:00Z"
effective_to: null
owner_role: product_safety_owner
reviewer: Jane Reviewer
reviewed_at: "2026-07-20T00:00:00Z"
updated_at: "2026-07-20T00:00:00Z"
approval_status: {approval_status}
approval_references:
  - supplier-care-review-2026-07
guidance_scope: universal_low_risk
prohibited_actions:
  - unknown_cleaner
approved_responses:
  en: Inspect gently, dry fully, avoid heat, and tell us the material.
  zh: 轻柔检查并彻底干燥，避免高温，并告诉我们产品材质。
---
# General care

Inspect first, clean gently, dry fully, avoid direct heat, and stop for damage.
"""


def test_hybrid_tpe_silicone_product_selects_hybrid_procedure() -> None:
    evidence = [
        KnowledgeEvidence(
            chunk_id="product-hybrid",
            document_id="product-hybrid",
            text="Model HYBRID-100 has a silicone head and TPE body.",
            score=1.0,
            source="https://shop.example.test/hybrid-100.html",
            metadata={"product": {"sku": "HYBRID-100", "material": "Silicone Head & TPE Body"}},
        ),
        _approved_care_evidence(material="tpe_silicone"),
    ]

    decision = evaluate_care_guidance(
        message="How should I clean HYBRID-100?",
        response_language="en",
        page_path="/",
        evidence=evidence,
    )

    assert decision.status == "approved"
    assert decision.material == "tpe_silicone"
    assert decision.procedure is not None
    assert decision.procedure.material == "tpe_silicone"
