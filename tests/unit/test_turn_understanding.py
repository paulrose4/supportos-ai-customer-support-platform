import json

from app.application.services import TurnUnderstandingService
from app.domain.ports import ChatModelRequest, ChatModelResult


class PlannerModel:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.requests: list[ChatModelRequest] = []

    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        self.requests.append(request)
        return ChatModelResult(text=json.dumps(self.payload), model="planner-test")


async def test_turn_planner_preserves_every_customer_sub_question() -> None:
    model = PlannerModel(
        {
            "primary_goal": "verify product identity",
            "sub_questions": [
                {
                    "id": "q1",
                    "question": "What brand is it?",
                    "evidence_need_ids": ["n1"],
                    "priority": "high",
                },
                {"id": "q2", "question": "What material is it?", "evidence_need_ids": ["n2"]},
                {"id": "q3", "question": "What is the SKU?", "evidence_need_ids": ["n3"]},
            ],
            "evidence_needs": [
                {
                    "id": f"n{index}",
                    "description": description,
                    "target_entity": "current_page_product",
                    "capability_hint": "product_snapshot_lookup",
                }
                for index, description in enumerate(
                    ("identify brand", "identify material", "identify SKU"),
                    start=1,
                )
            ],
            "target_entities": [{"entity": "current_page_product", "source": "current_page_path"}],
            "constraints": ["answer directly"],
            "answer_shape": "concise",
            "ambiguities": [],
            "routing_label": "product_details",
        }
    )

    result = await TurnUnderstandingService(model).understand(
        message="What brand is this product, what material is it made from, and what is the SKU?",
        page_path="/products/example.html",
        target_language="en",
    )

    assert [item.question_id for item in result.plan.sub_questions] == ["q1", "q2", "q3"]
    assert {item.capability_hint for item in result.plan.evidence_needs} == {
        "product_snapshot_lookup"
    }
    assert result.plan.requires_semantic_review is True
    assert result.plan.sub_questions[0].priority == 100
    assert result.plan.target_entities == ("current_page_product",)
    assert result.model == "planner-test"


async def test_turn_planner_invalid_output_uses_deterministic_fallback() -> None:
    result = await TurnUnderstandingService(PlannerModel({"sub_questions": []})).understand(
        message="What is the price?",
        page_path="/products/example.html",
        target_language="en",
    )

    assert result.plan.planner_fallback is True
    assert result.plan.planner_fallback_reason == "ValueError"
    assert len(result.plan.sub_questions) == 1
    assert result.model is None
    assert result.generation_count == 0


async def test_conditional_turn_planner_skips_model_for_simple_first_turn() -> None:
    model = PlannerModel({"should": "not be called"})

    result = await TurnUnderstandingService(model, mode="conditional").understand(
        message="What is the price of SKU-100?",
        page_path="/products/sku-100.html",
        target_language="en",
    )

    assert model.requests == []
    assert result.plan.planner_fallback is True
    assert result.plan.planner_fallback_reason == "deterministic_fast_path"
    assert result.generation_count == 0


async def test_conditional_turn_planner_keeps_model_for_compound_turn() -> None:
    model = PlannerModel(
        {
            "primary_goal": "compare facts",
            "sub_questions": [
                {"id": "q1", "question": "What is the price?", "evidence_need_ids": ["n1"]},
                {"id": "q2", "question": "Is it in stock?", "evidence_need_ids": ["n2"]},
            ],
            "evidence_needs": [
                {"id": "n1", "description": "price"},
                {"id": "n2", "description": "stock"},
            ],
        }
    )

    result = await TurnUnderstandingService(model, mode="conditional").understand(
        message="What is the price and is it in stock?",
        target_language="en",
    )

    assert len(model.requests) == 1
    assert result.plan.requires_semantic_review is True
