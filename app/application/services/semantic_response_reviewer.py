import json

from app.domain.models import ResponseBrief, SemanticCoverageReview
from app.domain.ports import ChatModelPort, ChatModelRequest
from app.domain.rules import redact_sensitive_text


class SemanticResponseReviewer:
    def __init__(self, model: ChatModelPort, *, mode: str = "always") -> None:
        if mode not in {"always", "conditional"}:
            raise ValueError("semantic reviewer mode must be always or conditional")
        self._model = model
        self._mode = mode

    async def review(
        self,
        *,
        brief: ResponseBrief,
        candidate: str,
        declared_answered_ids: tuple[str, ...] = (),
    ) -> SemanticCoverageReview:
        requested_ids = tuple(item.question_id for item in brief.sub_questions)
        if not requested_ids:
            return SemanticCoverageReview(
                passed=True,
                answered_sub_question_ids=requested_ids,
            )
        if self._mode == "conditional" and len(requested_ids) == 1 and declared_answered_ids:
            answered = tuple(
                question_id
                for question_id in requested_ids
                if question_id in set(declared_answered_ids)
            )
            missing = tuple(
                question_id for question_id in requested_ids if question_id not in answered
            )
            return SemanticCoverageReview(
                passed=not missing,
                answered_sub_question_ids=answered,
                missing_sub_question_ids=missing,
                repair_instructions=("Answer the customer question explicitly.",)
                if missing
                else (),
            )
        if not brief.must_cover_all_sub_questions:
            return SemanticCoverageReview(
                passed=True,
                answered_sub_question_ids=requested_ids,
            )
        payload = {
            "sub_questions": [
                {"question_id": item.question_id, "question": item.question}
                for item in brief.sub_questions
            ],
            "approved_facts": [
                {
                    "fact_id": item.fact_id,
                    "fact_type": item.fact_type,
                    "value": redact_sensitive_text(item.value),
                }
                for item in brief.approved_facts
            ],
            "unavailable_question_ids": list(brief.unavailable_question_ids),
            "candidate": redact_sensitive_text(candidate)[:4000],
            "declared_answered_sub_question_ids": list(declared_answered_ids),
        }
        try:
            result = await self._model.generate(
                ChatModelRequest(
                    messages=(
                        {
                            "role": "system",
                            "content": (
                                "Review a customer-support reply for semantic completeness and "
                                "grounding. Do not answer the customer and do not add facts. "
                                "Mark a "
                                "sub-question answered only when the candidate answers it with an "
                                "approved fact or explicitly says it cannot be verified. Report "
                                "any "
                                "unsupported factual claim. Return JSON only with passed, "
                                "answered_sub_question_ids, missing_sub_question_ids, "
                                "unsupported_claims, and repair_instructions."
                            ),
                        },
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ),
                    metadata={
                        "task": "semantic_response_review",
                        "prompt_version": "semantic-response-review-v1",
                    },
                    max_output_tokens=600,
                )
            )
            return _parse_review(result.text, requested_ids)
        except Exception:  # noqa: BLE001
            answered = tuple(
                question_id
                for question_id in requested_ids
                if question_id in set(declared_answered_ids)
            )
            missing = tuple(
                question_id for question_id in requested_ids if question_id not in answered
            )
            return SemanticCoverageReview(
                passed=not missing,
                answered_sub_question_ids=answered,
                missing_sub_question_ids=missing,
                repair_instructions=("Answer every remaining customer sub-question.",)
                if missing
                else (),
            )


def _parse_review(raw: str, requested_ids: tuple[str, ...]) -> SemanticCoverageReview:
    payload = json.loads(raw.strip())
    if not isinstance(payload, dict):
        raise ValueError("semantic reviewer returned a non-object")
    valid_ids = set(requested_ids)
    answered = _valid_ids(payload.get("answered_sub_question_ids"), valid_ids)
    reported_missing = _valid_ids(payload.get("missing_sub_question_ids"), valid_ids)
    missing = tuple(
        question_id
        for question_id in requested_ids
        if question_id not in set(answered) or question_id in set(reported_missing)
    )
    unsupported = _strings(payload.get("unsupported_claims"), 12, 500)
    instructions = _strings(payload.get("repair_instructions"), 12, 500)
    passed = bool(payload.get("passed")) and not missing and not unsupported
    return SemanticCoverageReview(
        passed=passed,
        answered_sub_question_ids=answered,
        missing_sub_question_ids=missing,
        unsupported_claims=unsupported,
        repair_instructions=instructions,
    )


def _valid_ids(value: object, valid_ids: set[str]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(str(item) for item in value if str(item) in valid_ids))


def _strings(value: object, count: int, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip()[:limit] for item in value if str(item).strip())[:count]
