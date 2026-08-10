import json
from dataclasses import replace

import pytest

from app.application.services import RenderCustomerResponseService, SemanticResponseReviewer
from app.domain.models import (
    ApprovedResponseFact,
    DialogueAct,
    EvidenceNeed,
    ResponseBrief,
    ResponseStyle,
    TurnSubQuestion,
)
from app.domain.ports import ChatModelRequest, ChatModelResult


class StubRendererModel:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[ChatModelRequest] = []

    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        self.requests.append(request)
        return ChatModelResult(text=self.text, model="renderer-test")


class SequentialRendererModel:
    def __init__(self, texts: tuple[str, ...]) -> None:
        self.texts = list(texts)
        self.requests: list[ChatModelRequest] = []

    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        self.requests.append(request)
        return ChatModelResult(text=self.texts.pop(0), model="renderer-test")


class SemanticRepairModel:
    def __init__(self, *, second_review_passes: bool = True) -> None:
        self.second_review_passes = second_review_passes
        self.requests: list[ChatModelRequest] = []
        self.review_count = 0

    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        self.requests.append(request)
        task = request.metadata["task"]
        if task == "customer_response_render":
            return ChatModelResult(
                text=json.dumps(
                    {
                        "reply": "It is made from TPE.",
                        "used_fact_ids": ["material-1"],
                        "answered_sub_question_ids": ["q2"],
                    }
                ),
                model="renderer-test",
            )
        if task == "customer_response_quality_repair":
            return ChatModelResult(
                text=json.dumps(
                    {
                        "reply": (
                            "It is an Example Brand model made from TPE, and the SKU is SKU-100."
                        ),
                        "used_fact_ids": ["brand-1", "material-1", "sku-1"],
                        "answered_sub_question_ids": ["q1", "q2", "q3"],
                    }
                ),
                model="renderer-test",
            )
        self.review_count += 1
        passes = self.review_count > 1 and self.second_review_passes
        return ChatModelResult(
            text=json.dumps(
                {
                    "passed": passes,
                    "answered_sub_question_ids": ["q1", "q2", "q3"] if passes else ["q2"],
                    "missing_sub_question_ids": [] if passes else ["q1", "q3"],
                    "unsupported_claims": [],
                    "repair_instructions": []
                    if passes
                    else ["Answer the brand and SKU questions."],
                }
            ),
            model="reviewer-test",
        )


def brief() -> ResponseBrief:
    return ResponseBrief(
        dialogue_act=DialogueAct.ANSWER,
        customer_goal="SKU-100 多少钱？",
        direct_answer="SKU-100 当前商品资料显示价格为 279 美元。",
        approved_facts=(ApprovedResponseFact("price", "SKU-100 price: 279 USD", "catalog"),),
        facts_to_avoid=(),
        uncertainty=(),
        emotional_acknowledgement=None,
        conversation_delta=(),
        follow_up=None,
        next_step=None,
        style=ResponseStyle.CONCISE,
        target_language="zh",
        max_words=60,
    )


async def test_renderer_uses_structured_brief() -> None:
    model = StubRendererModel("SKU-100 的价格是 279 美元。")
    result = await RenderCustomerResponseService(model).render(brief=brief())

    assert result.text == "SKU-100 的价格是 279 美元。"
    assert model.requests[0].metadata["task"] == "customer_response_render"
    assert model.requests[0].metadata["response_brief"]["next_step"] is None


async def test_conditional_semantic_review_uses_declared_id_without_model_call() -> None:
    model = StubRendererModel(
        json.dumps(
            {
                "reply": "SKU-100 的价格是 279 美元。",
                "used_fact_ids": [],
                "answered_sub_question_ids": ["q1"],
            }
        )
    )
    planned_brief = replace(
        brief(),
        sub_questions=(TurnSubQuestion("q1", "SKU-100 多少钱？"),),
        must_cover_all_sub_questions=True,
    )

    result = await RenderCustomerResponseService(
        model,
        semantic_reviewer=SemanticResponseReviewer(model, mode="conditional"),
    ).render(brief=planned_brief)

    assert result.semantic_review.passed is True
    assert len(model.requests) == 1


async def test_renderer_rejects_unapproved_numeric_claim() -> None:
    model = StubRendererModel("SKU-100 的价格是 279 美元，库存还有 12 件。")

    with pytest.raises(ValueError, match="numeric claim"):
        await RenderCustomerResponseService(model).render(brief=brief())

    assert len(model.requests) == 2


async def test_renderer_rejects_unapproved_url() -> None:
    model = StubRendererModel("价格为 279 美元，详情见 https://other.example.test/item。")

    with pytest.raises(ValueError, match="unapproved URL"):
        await RenderCustomerResponseService(model).render(brief=brief())

    assert len(model.requests) == 2


async def test_renderer_repairs_unapproved_numeric_claim_once() -> None:
    model = SequentialRendererModel(
        (
            "SKU-100 的价格是 279 美元，库存还有 12 件。",
            "SKU-100 的价格是 279 美元。",
        )
    )

    result = await RenderCustomerResponseService(model).render(brief=brief())

    assert result.text == "SKU-100 的价格是 279 美元。"
    assert result.repaired_issue_codes == ("unsupported_numeric_claim",)
    assert result.generation_count == 2


async def test_renderer_redacts_sensitive_brief_content() -> None:
    model = StubRendererModel("可以继续处理。")
    sensitive = replace(
        brief(),
        customer_goal="请联系 test@example.com",
        direct_answer="已记录 test@example.com",
    )
    await RenderCustomerResponseService(model).render(brief=sensitive)

    payload = model.requests[0].metadata["response_brief"]
    assert "test@example.com" not in payload["customer_goal"]
    assert "test@example.com" not in payload["direct_answer"]


async def test_renderer_repairs_expression_issue_once() -> None:
    model = SequentialRendererModel(
        (
            "SKU-100 的价格是 279 美元。需要我再推荐一款吗？",
            "SKU-100 的价格是 279 美元。",
        )
    )
    result = await RenderCustomerResponseService(model).render(brief=brief())

    assert result.text == "SKU-100 的价格是 279 美元。"
    assert result.repaired_issue_codes == ("unplanned_follow_up",)
    assert result.generation_count == 2
    assert [request.metadata["task"] for request in model.requests] == [
        "customer_response_render",
        "customer_response_quality_repair",
    ]


async def test_renderer_maps_used_fact_ids_to_citations() -> None:
    grounded = replace(
        brief(),
        approved_facts=(
            ApprovedResponseFact(
                "price",
                "SKU-100 price: 279 USD",
                "catalog",
                fact_id="price-1",
                citation="catalog#price-1",
            ),
        ),
    )
    model = StubRendererModel(
        json.dumps(
            {
                "reply": "SKU-100 的价格是 279 美元。",
                "used_fact_ids": ["price-1"],
            },
            ensure_ascii=False,
        )
    )
    result = await RenderCustomerResponseService(model).render(brief=grounded)

    assert result.used_fact_ids == ("price-1",)
    assert result.claim_citations == (
        {"fact_id": "price-1", "fact_type": "price", "citation": "catalog#price-1"},
    )


async def test_renderer_rejects_unknown_fact_id() -> None:
    model = StubRendererModel(
        json.dumps(
            {
                "reply": "SKU-100 的价格是 279 美元。",
                "used_fact_ids": ["unknown"],
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(ValueError, match="unknown fact_id"):
        await RenderCustomerResponseService(model).render(brief=brief())


def compound_brief() -> ResponseBrief:
    return replace(
        brief(),
        customer_goal="What brand is it, what material is it, and what is the SKU?",
        direct_answer="Brand: Example Brand. Material: TPE. SKU: SKU-100.",
        approved_facts=(
            ApprovedResponseFact("brand", "Example Brand", "catalog", "brand-1"),
            ApprovedResponseFact("material", "TPE", "catalog", "material-1"),
            ApprovedResponseFact("sku", "SKU-100", "catalog", "sku-1"),
        ),
        sub_questions=(
            TurnSubQuestion("q1", "What brand is it?", ("n1",), 1),
            TurnSubQuestion("q2", "What material is it?", ("n2",), 2),
            TurnSubQuestion("q3", "What is the SKU?", ("n3",), 3),
        ),
        evidence_needs=(
            EvidenceNeed("n1", "identify brand"),
            EvidenceNeed("n2", "identify material"),
            EvidenceNeed("n3", "identify SKU"),
        ),
        must_cover_all_sub_questions=True,
    )


async def test_renderer_repairs_semantically_incomplete_compound_answer_once() -> None:
    model = SemanticRepairModel()

    result = await RenderCustomerResponseService(model).render(brief=compound_brief())

    assert "Example Brand" in result.text
    assert "SKU-100" in result.text
    assert result.answered_sub_question_ids == ("q1", "q2", "q3")
    assert result.repaired_issue_codes == ("semantic_coverage",)
    assert result.generation_count == 2
    assert result.semantic_review.passed is True


async def test_renderer_fails_after_second_semantic_coverage_failure() -> None:
    model = SemanticRepairModel(second_review_passes=False)

    with pytest.raises(ValueError, match="semantic_coverage"):
        await RenderCustomerResponseService(model).render(brief=compound_brief())
