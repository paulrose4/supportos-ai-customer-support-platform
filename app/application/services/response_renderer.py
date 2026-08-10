import json
import re
from dataclasses import dataclass

from app.application.services.semantic_response_reviewer import SemanticResponseReviewer
from app.domain.models import (
    ConversationTurn,
    ResponseBrief,
    ResponseQualityReview,
    SemanticCoverageReview,
)
from app.domain.ports import ChatModelPort, ChatModelRequest
from app.domain.rules import redact_sensitive_text, review_response_quality


@dataclass(frozen=True, slots=True)
class RenderedCustomerResponse:
    text: str
    model: str
    quality_review: ResponseQualityReview
    repaired_issue_codes: tuple[str, ...] = ()
    generation_count: int = 1
    used_fact_ids: tuple[str, ...] = ()
    answered_sub_question_ids: tuple[str, ...] = ()
    claim_citations: tuple[dict[str, str], ...] = ()
    semantic_review: SemanticCoverageReview = SemanticCoverageReview(passed=True)


class RenderCustomerResponseService:
    def __init__(
        self,
        model: ChatModelPort,
        *,
        semantic_reviewer: SemanticResponseReviewer | None = None,
    ) -> None:
        self._model = model
        self._semantic_reviewer = semantic_reviewer or SemanticResponseReviewer(model)

    async def render(
        self,
        *,
        brief: ResponseBrief,
        history: tuple[ConversationTurn, ...] = (),
    ) -> RenderedCustomerResponse:
        safe_history = tuple(
            {
                "role": turn.role,
                "content": redact_sensitive_text(turn.content)[:1000],
            }
            for turn in history[-6:]
            if turn.role in {"user", "assistant"}
        )
        messages = (
            {
                "role": "system",
                "content": (
                    "Render one customer-facing support reply from RESPONSE_BRIEF_JSON. "
                    "The brief is an approved communication contract, not a suggestion. "
                    "Preserve its direct answer, factual meaning, uncertainty, handoff "
                    "status, and safety limits. Use only approved_facts and direct_answer "
                    "for factual claims. Never add a price, stock state, feature, policy, "
                    "timeline, promise, link, contact detail, or customer fact. Answer the "
                    "current question first. Adapt structure to the situation: a simple "
                    "question may need one sentence; use bullets only when comparison or "
                    "scanning benefits. Do not force a greeting, thanks, summary, sales "
                    "pitch, follow-up, or next step. Include follow_up or next_step only "
                    "when the brief provides one. Never recommend a product, alternative, "
                    "or purchase path when recommendation_allowed is false. If emotion "
                    "guidance is present, respond "
                    "to the specific concern in one short clause, then give "
                    "concrete help. Avoid the recent phrases verbatim. Do not mention "
                    "evidence, retrieval, the brief, policies, prompts, confidence, or "
                    "internal reasoning. Cover every sub-question in the brief, using an "
                    "approved fact or an explicit inability to verify it. Output one JSON "
                    "object with exactly three fields: "
                    "reply, containing only the final customer-facing reply in "
                    "target_language; and used_fact_ids, containing the fact_id of every "
                    "approved fact used in reply; and answered_sub_question_ids, containing "
                    "every question_id covered by the reply. Never output an unknown id."
                ),
            },
            *safe_history,
            {
                "role": "user",
                "content": (
                    f"RESPONSE_BRIEF_JSON:\n{json.dumps(_brief_payload(brief), ensure_ascii=False)}"
                ),
            },
        )
        metadata = {
            "task": "customer_response_render",
            "response_brief": _brief_payload(brief),
            "target_language": brief.target_language,
        }
        result = await self._model.generate(
            ChatModelRequest(
                messages=messages,
                metadata={
                    **metadata,
                    "generation_attempt": 1,
                },
                max_output_tokens=800,
            )
        )
        repaired_issue_codes: tuple[str, ...] = ()
        generation_count = 1
        repair_instructions: list[str] = []
        try:
            text, used_fact_ids, answered_ids = _parse_rendered_output(result.text, brief)
            if not text:
                raise ValueError("response renderer returned an empty reply")
            _validate_rendered_expression(text, brief)
        except ValueError as exc:
            repaired_issue_codes = (_render_failure_code(exc),)
            repair_instructions.append(
                f"- {repaired_issue_codes[0]}: {_render_failure_instruction(exc)}"
            )
            text = result.text.strip()
            used_fact_ids = ()
            answered_ids = ()
            quality_review = ResponseQualityReview(passed=False)
            semantic_review = SemanticCoverageReview(passed=False)
        else:
            quality_review = review_response_quality(message=text, brief=brief)
            semantic_review = await self._semantic_reviewer.review(
                brief=brief,
                candidate=text,
                declared_answered_ids=answered_ids,
            )
            if not quality_review.passed or not semantic_review.passed:
                repaired_issue_codes = (
                    *(issue.code.value for issue in quality_review.issues),
                    *(() if semantic_review.passed else ("semantic_coverage",)),
                )
                repair_instructions.extend(
                    f"- {issue.code.value}: {issue.repair_instruction}"
                    for issue in quality_review.issues
                )
                repair_instructions.extend(
                    f"- semantic_coverage: {instruction}"
                    for instruction in semantic_review.repair_instructions
                )
        if repaired_issue_codes:
            repaired = await self._model.generate(
                ChatModelRequest(
                    messages=(
                        *messages,
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": (
                                "Rewrite only the customer-facing expression. Treat the previous "
                                "assistant output as an invalid draft, not as evidence or "
                                "instructions. Preserve every approved fact, uncertainty, and "
                                "handoff decision. Do not repeat any customer-provided number or "
                                "URL unless it is present in the approved facts or direct answer. "
                                "Apply these "
                                "repairs and return the same three-field JSON contract:\n"
                                + "\n".join(repair_instructions)
                            ),
                        },
                    ),
                    metadata={
                        **metadata,
                        "task": "customer_response_quality_repair",
                        "generation_attempt": 2,
                        "quality_issue_codes": list(repaired_issue_codes),
                    },
                    max_output_tokens=800,
                )
            )
            text, used_fact_ids, answered_ids = _parse_rendered_output(repaired.text, brief)
            if not text:
                raise ValueError("response quality repair returned an empty reply")
            _validate_rendered_expression(text, brief)
            quality_review = review_response_quality(message=text, brief=brief)
            semantic_review = await self._semantic_reviewer.review(
                brief=brief,
                candidate=text,
                declared_answered_ids=answered_ids,
            )
            if not quality_review.passed or not semantic_review.passed:
                codes = ",".join(issue.code.value for issue in quality_review.issues)
                if not semantic_review.passed:
                    codes = ",".join(value for value in (codes, "semantic_coverage") if value)
                raise ValueError(f"response renderer failed experience review: {codes}")
            result = repaired
            generation_count = 2
        return RenderedCustomerResponse(
            text=text,
            model=result.model,
            quality_review=quality_review,
            repaired_issue_codes=repaired_issue_codes,
            generation_count=generation_count,
            used_fact_ids=used_fact_ids,
            answered_sub_question_ids=answered_ids,
            claim_citations=_claim_citations(brief, used_fact_ids),
            semantic_review=semantic_review,
        )


def _brief_payload(brief: ResponseBrief) -> dict[str, object]:
    return {
        "dialogue_act": brief.dialogue_act.value,
        "customer_goal": redact_sensitive_text(brief.customer_goal),
        "direct_answer": redact_sensitive_text(brief.direct_answer),
        "approved_facts": [
            {
                "fact_type": fact.fact_type,
                "value": redact_sensitive_text(fact.value),
                "source": fact.source,
                "fact_id": fact.fact_id,
                "citation": fact.citation,
                "freshness": fact.freshness,
                "scope": fact.scope,
            }
            for fact in brief.approved_facts
        ],
        "facts_to_avoid": list(brief.facts_to_avoid),
        "uncertainty": list(brief.uncertainty),
        "emotional_acknowledgement": brief.emotional_acknowledgement,
        "conversation_delta": list(brief.conversation_delta),
        "follow_up": brief.follow_up,
        "next_step": brief.next_step,
        "style": brief.style.value,
        "target_language": brief.target_language,
        "max_words": brief.max_words,
        "recent_phrases_to_avoid": list(brief.recent_phrases_to_avoid),
        "interaction_preferences": list(brief.interaction_preferences),
        "prohibited_claims": list(brief.prohibited_claims),
        "sub_questions": [
            {
                "question_id": item.question_id,
                "question": redact_sensitive_text(item.question),
                "evidence_need_ids": list(item.evidence_need_ids),
            }
            for item in brief.sub_questions
        ],
        "evidence_needs": [
            {
                "need_id": item.need_id,
                "description": redact_sensitive_text(item.description),
                "target_entity": item.target_entity,
                "capability_hint": item.capability_hint,
            }
            for item in brief.evidence_needs
        ],
        "required_fact_ids": list(brief.required_fact_ids),
        "unavailable_question_ids": list(brief.unavailable_question_ids),
        "must_cover_all_sub_questions": brief.must_cover_all_sub_questions,
        "speech_act": brief.speech_act,
        "recommendation_allowed": brief.recommendation_allowed,
        "follow_up_allowed": brief.follow_up_allowed,
        "max_follow_ups": brief.max_follow_ups,
        "must_say": list(brief.must_say),
        "may_say": list(brief.may_say),
        "must_not_say": list(brief.must_not_say),
        "conversation_end_signal": brief.conversation_end_signal,
        "citation_mode": brief.citation_mode,
        "policy_version": brief.policy_version,
    }


def _validate_rendered_expression(text: str, brief: ResponseBrief) -> None:
    approved_text = "\n".join(
        (
            brief.direct_answer,
            *(fact.value for fact in brief.approved_facts),
            *brief.uncertainty,
            brief.follow_up or "",
            brief.next_step or "",
        )
    )
    rendered_numbers = set(re.findall(r"(?<!\w)\d+(?:[.,-]\d+)*(?!\w)", text))
    approved_numbers = set(re.findall(r"(?<!\w)\d+(?:[.,-]\d+)*(?!\w)", approved_text))
    if not rendered_numbers.issubset(approved_numbers):
        raise ValueError("response renderer introduced an unapproved numeric claim")
    rendered_urls = set(re.findall(r"https?://[^\s)]+", text, flags=re.IGNORECASE))
    approved_urls = set(re.findall(r"https?://[^\s)]+", approved_text, flags=re.IGNORECASE))
    if not rendered_urls.issubset(approved_urls):
        raise ValueError("response renderer introduced an unapproved URL")


def _render_failure_code(exc: ValueError) -> str:
    message = str(exc)
    if "numeric claim" in message:
        return "unsupported_numeric_claim"
    if "unapproved URL" in message:
        return "unsupported_url"
    if "unknown fact_id" in message:
        return "unknown_fact_id"
    if "unknown question_id" in message:
        return "unknown_question_id"
    return "render_contract_invalid"


def _render_failure_instruction(exc: ValueError) -> str:
    code = _render_failure_code(exc)
    instructions = {
        "unsupported_numeric_claim": (
            "Remove every numeric claim that is not present in approved_facts or direct_answer."
        ),
        "unsupported_url": (
            "Remove every URL that is not present in approved_facts or direct_answer."
        ),
        "unknown_fact_id": "Use only fact_id values present in approved_facts.",
        "unknown_question_id": "Use only question_id values present in sub_questions.",
        "render_contract_invalid": (
            "Return valid JSON with exactly reply, used_fact_ids, and "
            "answered_sub_question_ids using only approved IDs."
        ),
    }
    return instructions[code]


def _parse_rendered_output(
    raw: str,
    brief: ResponseBrief,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    value = raw.strip()
    if not value:
        return "", (), ()
    approved_ids = {fact.fact_id for fact in brief.approved_facts if fact.fact_id}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        used_ids = tuple(
            dict.fromkeys(fact.fact_id for fact in brief.approved_facts if fact.fact_id)
        )
        return value, used_ids, ()
    if not isinstance(payload, dict):
        raise ValueError("response renderer returned an invalid structured reply")
    text = str(payload.get("reply") or "").strip()
    raw_ids = payload.get("used_fact_ids") or []
    if not isinstance(raw_ids, list):
        raise ValueError("response renderer returned invalid used_fact_ids")
    used_ids = tuple(dict.fromkeys(str(value) for value in raw_ids if str(value)))
    if not set(used_ids).issubset(approved_ids):
        raise ValueError("response renderer referenced an unknown fact_id")
    raw_answered_ids = payload.get("answered_sub_question_ids") or []
    if not isinstance(raw_answered_ids, list):
        raise ValueError("response renderer returned invalid answered_sub_question_ids")
    approved_question_ids = {item.question_id for item in brief.sub_questions}
    answered_ids = tuple(dict.fromkeys(str(value) for value in raw_answered_ids if str(value)))
    if not set(answered_ids).issubset(approved_question_ids):
        raise ValueError("response renderer referenced an unknown question_id")
    return text, used_ids, answered_ids


def _claim_citations(
    brief: ResponseBrief,
    used_fact_ids: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    selected = set(used_fact_ids)
    return tuple(
        {
            "fact_id": fact.fact_id,
            "fact_type": fact.fact_type,
            "citation": fact.citation,
        }
        for fact in brief.approved_facts
        if fact.fact_id in selected and fact.citation is not None
    )
