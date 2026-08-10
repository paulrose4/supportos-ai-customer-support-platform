import json
from collections.abc import Sequence
from hashlib import sha256
from math import sqrt

from app.domain.ports import ChatModelRequest, ChatModelResult


class FakeChatModel:
    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        if request.metadata.get("task") == "turn_understanding":
            user_payload = json.loads(request.messages[-1]["content"])
            message = str(user_payload.get("message") or "")
            page_path = str(user_payload.get("current_page_path") or "/")
            target = "current_page_product" if page_path not in {"", "/"} else None
            capability = "product_snapshot_lookup" if target else "site_knowledge_search"
            return ChatModelResult(
                text=json.dumps(
                    {
                        "primary_goal": message,
                        "sub_questions": [
                            {
                                "id": "q1",
                                "question": message,
                                "evidence_need_ids": ["need-1"],
                                "priority": 1,
                            }
                        ],
                        "evidence_needs": [
                            {
                                "id": "need-1",
                                "description": message,
                                "target_entity": target,
                                "capability_hint": capability,
                                "freshness_requirement": "source_default",
                                "exact_entity_required": target is not None,
                                "general_knowledge_allowed": False,
                            }
                        ],
                        "target_entities": [target] if target else [],
                        "constraints": [],
                        "answer_shape": "adaptive",
                        "ambiguities": [],
                        "routing_label": "general",
                    },
                    ensure_ascii=False,
                ),
                model="fake-chat-model",
                metadata={"planned": True},
            )
        if request.metadata.get("task") == "customer_response_render":
            brief = request.metadata.get("response_brief") or {}
            return ChatModelResult(
                text=json.dumps(
                    {
                        "reply": str(brief.get("direct_answer") or ""),
                        "used_fact_ids": [
                            item.get("fact_id")
                            for item in brief.get("approved_facts", [])
                            if item.get("fact_id")
                        ],
                        "answered_sub_question_ids": [
                            item.get("question_id")
                            for item in brief.get("sub_questions", [])
                            if item.get("question_id")
                        ],
                    },
                    ensure_ascii=False,
                ),
                model="fake-chat-model",
                metadata={"rendered": True},
            )
        if request.metadata.get("task") == "semantic_response_review":
            payload = json.loads(request.messages[-1]["content"])
            question_ids = [
                item.get("question_id")
                for item in payload.get("sub_questions", [])
                if item.get("question_id")
            ]
            return ChatModelResult(
                text=json.dumps(
                    {
                        "passed": True,
                        "answered_sub_question_ids": question_ids,
                        "missing_sub_question_ids": [],
                        "unsupported_claims": [],
                        "repair_instructions": [],
                    }
                ),
                model="fake-chat-model",
                metadata={"reviewed": True},
            )
        language = str(
            (request.metadata.get("language_context") or {}).get("target_language") or "en"
        ).casefold()
        if request.metadata.get("task") == "grounded_answer":
            evidence = request.metadata.get("evidence", [])
            if evidence:
                return ChatModelResult(
                    text=(
                        f"根据当前商品信息：{evidence[0]['text']}"
                        if language.startswith("zh")
                        else f"Here is the relevant store information: {evidence[0]['text']}"
                    ),
                    model="fake-chat-model",
                    metadata={"grounded": True},
                )
        if request.metadata.get("task") == "general_guidance":
            return ChatModelResult(
                text=(
                    (
                        "我们建议用温水和温和的无香清洁剂轻柔清洁 TPE 表面，"
                        "随后用柔软毛巾吸干并彻底阴干。"
                        "避免酒精、漂白剂、强力清洁剂、高温和阳光直射；完全干燥后可按产品说明薄薄使用护理粉。"
                        "收纳时保持自然姿势并远离深色易染色织物。"
                    )
                    if language.startswith("zh")
                    else (
                        "We recommend gently cleaning the TPE surface with warm water and a mild, "
                        "unscented cleanser, then patting it dry and allowing it to air-dry fully. "
                        "Avoid alcohol, bleach, harsh cleaners, heat, and direct sunlight. "
                        "Store it in a natural position away from dark fabrics that may transfer "
                        "color."
                    )
                ),
                model="fake-chat-model",
                metadata={"general_guidance": True},
            )
        last_message = request.messages[-1]["content"] if request.messages else ""
        return ChatModelResult(text=f"fake:{last_message}", model="fake-chat-model")


class FakeEmbeddingProvider:
    dimension = 64

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [_vector_for_text(text, self.dimension) for text in texts]


def _vector_for_text(text: str, dimension: int) -> list[float]:
    normalized = "".join(text.casefold().split())
    tokens = list(normalized) + [
        normalized[index : index + 2] for index in range(len(normalized) - 1)
    ]
    vector = [0.0] * dimension
    for token in tokens:
        digest = sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % dimension
        direction = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += direction
    norm = sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]
