from collections.abc import Sequence

from app.domain.models import (
    ApprovedResponseFact,
    DialogueAct,
    ResponseBrief,
    ResponseKind,
    ResponseStyle,
    SalesResponsePlan,
    SupportIntent,
    TurnPlan,
)


def build_response_brief(
    *,
    question: str,
    direct_answer: str,
    response_kind: ResponseKind,
    target_language: str,
    approved_facts: Sequence[ApprovedResponseFact] = (),
    facts_to_avoid: Sequence[str] = (),
    uncertainty: Sequence[str] = (),
    conversation_delta: Sequence[str] = (),
    sales_plan: SalesResponsePlan | None = None,
    sentiment: str = "neutral",
    interaction_preferences: Sequence[str] = (),
    turn_plan: TurnPlan | None = None,
    speech_act: str | None = None,
    recommendation_permission: str | None = None,
    follow_up_permission: str | None = None,
    conversation_end_signal: bool | None = None,
) -> ResponseBrief:
    effective_speech_act = speech_act or (turn_plan.speech_act if turn_plan else "question")
    effective_recommendation_permission = recommendation_permission or (
        turn_plan.recommendation_permission if turn_plan else "none"
    )
    effective_follow_up_permission = follow_up_permission or (
        turn_plan.follow_up_permission if turn_plan else "one"
    )
    effective_end_signal = (
        conversation_end_signal
        if conversation_end_signal is not None
        else bool(turn_plan and turn_plan.conversation_end_signal)
    )
    dialogue_act = _dialogue_act(response_kind, sales_plan, turn_plan)
    if effective_speech_act in {"acknowledgement", "thanks", "close"}:
        dialogue_act = DialogueAct.CLOSE
    style = _response_style(dialogue_act, sentiment)
    recommendation_allowed = _recommendation_allowed(
        sales_plan=sales_plan,
        turn_plan=turn_plan,
        question=question,
        interaction_preferences=interaction_preferences,
        permission=effective_recommendation_permission,
    )
    follow_up_allowed = effective_follow_up_permission == "one" and not effective_end_signal
    follow_up = _follow_up(sales_plan) if follow_up_allowed else None
    return ResponseBrief(
        dialogue_act=dialogue_act,
        customer_goal=question.strip()[:500],
        direct_answer=direct_answer.strip()[:2000],
        approved_facts=tuple(approved_facts)[:30],
        facts_to_avoid=tuple(dict.fromkeys(value for value in facts_to_avoid if value))[:20],
        uncertainty=tuple(dict.fromkeys(value for value in uncertainty if value))[:10],
        emotional_acknowledgement=(
            "Acknowledge the customer's specific concern briefly, then address it."
            if sentiment == "negative"
            else None
        ),
        conversation_delta=tuple(dict.fromkeys(value for value in conversation_delta if value))[
            :10
        ],
        follow_up=follow_up,
        next_step=_next_step(
            dialogue_act,
            sales_plan,
            interaction_preferences,
            recommendation_allowed=recommendation_allowed,
        ),
        style=style,
        target_language=target_language,
        max_words=_max_words(sales_plan, interaction_preferences),
        recent_phrases_to_avoid=(
            sales_plan.recent_phrases_to_avoid[-8:] if sales_plan is not None else ()
        ),
        interaction_preferences=tuple(interaction_preferences)[:10],
        prohibited_claims=sales_plan.prohibited_claims if sales_plan is not None else (),
        sub_questions=turn_plan.sub_questions if turn_plan is not None else (),
        evidence_needs=turn_plan.evidence_needs if turn_plan is not None else (),
        must_cover_all_sub_questions=bool(turn_plan and not turn_plan.planner_fallback),
        speech_act=effective_speech_act,
        recommendation_allowed=recommendation_allowed,
        follow_up_allowed=follow_up_allowed,
        max_follow_ups=1 if follow_up_allowed else 0,
        must_say=(direct_answer.strip(),) if direct_answer.strip() else (),
        may_say=tuple(fact.value for fact in approved_facts[:4]),
        must_not_say=tuple(
            dict.fromkeys((*facts_to_avoid, *(sales_plan.prohibited_claims if sales_plan else ())))
        )[:20],
        conversation_end_signal=effective_end_signal,
        citation_mode="product_card" if recommendation_allowed else "related_links",
    )


def _dialogue_act(
    response_kind: ResponseKind,
    sales_plan: SalesResponsePlan | None,
    turn_plan: TurnPlan | None,
) -> DialogueAct:
    if turn_plan is not None:
        mapped = {
            "acknowledgement": DialogueAct.CLOSE,
            "thanks": DialogueAct.CLOSE,
            "close": DialogueAct.CLOSE,
            "comparison": DialogueAct.COMPARE,
            "recommendation_request": DialogueAct.RECOMMEND,
            "objection": DialogueAct.REASSURE,
            "correction": DialogueAct.ANSWER,
        }.get(turn_plan.speech_act)
        if mapped is not None:
            return mapped
    if response_kind is ResponseKind.HANDOFF:
        return DialogueAct.HANDOFF
    if response_kind is ResponseKind.CLARIFICATION:
        return DialogueAct.CLARIFY
    if sales_plan is None:
        return DialogueAct.ANSWER
    intent = SupportIntent(sales_plan.current_intent)
    if sales_plan.primary_objection:
        return DialogueAct.REASSURE
    if intent is SupportIntent.PRODUCT_COMPARISON:
        return DialogueAct.COMPARE
    if intent is SupportIntent.PRODUCT_RECOMMENDATION:
        return DialogueAct.RECOMMEND
    return DialogueAct.ANSWER


def _response_style(dialogue_act: DialogueAct, sentiment: str) -> ResponseStyle:
    if sentiment == "negative" or dialogue_act is DialogueAct.REASSURE:
        return ResponseStyle.EMPATHETIC
    if dialogue_act in {DialogueAct.RECOMMEND, DialogueAct.COMPARE}:
        return ResponseStyle.CONSULTATIVE
    if dialogue_act is DialogueAct.HANDOFF:
        return ResponseStyle.TRANSACTIONAL
    if dialogue_act is DialogueAct.CLARIFY:
        return ResponseStyle.CONCISE
    return ResponseStyle.EXPLANATORY


def _follow_up(sales_plan: SalesResponsePlan | None) -> str | None:
    if sales_plan is None or not sales_plan.follow_up_question_key:
        return None
    return sales_plan.follow_up_question_key


def _follow_up_allowed(turn_plan: TurnPlan | None) -> bool:
    return turn_plan is None or turn_plan.follow_up_permission == "one"


def _recommendation_allowed(
    *,
    sales_plan: SalesResponsePlan | None,
    turn_plan: TurnPlan | None,
    question: str,
    interaction_preferences: Sequence[str],
    permission: str,
) -> bool:
    if "sales_tone=no_sales" in interaction_preferences:
        return False
    if permission in {"explicit", "denied"}:
        return permission == "explicit"
    if turn_plan is not None:
        return turn_plan.recommendation_permission == "explicit"
    normalized = question.casefold()
    return bool(
        sales_plan
        and sales_plan.current_intent in {"product_recommendation", "product_comparison"}
        and any(term in normalized for term in ("recommend", "which one", "推荐", "帮我选"))
    )


def _next_step(
    dialogue_act: DialogueAct,
    sales_plan: SalesResponsePlan | None,
    interaction_preferences: Sequence[str],
    *,
    recommendation_allowed: bool,
) -> str | None:
    if "sales_tone=no_sales" in interaction_preferences:
        return None
    if dialogue_act is DialogueAct.HANDOFF:
        return "Explain what has been handed off and what information is useful next."
    if not recommendation_allowed:
        return None
    if sales_plan is None:
        return None
    if sales_plan.next_best_action.value == "offer_purchase_path":
        return "Offer the verified purchase path without urgency."
    return None


def _max_words(
    sales_plan: SalesResponsePlan | None,
    interaction_preferences: Sequence[str],
) -> int:
    default = sales_plan.max_words if sales_plan is not None else 120
    if "response_length=concise" in interaction_preferences:
        return min(default, 60)
    if "response_length=detailed" in interaction_preferences:
        return max(default, 160)
    return default
