from app.domain.models import DialogueAct, ResponseBrief, ResponseStyle
from app.domain.rules import review_response_quality


def brief(**changes: object) -> ResponseBrief:
    values: dict[str, object] = {
        "dialogue_act": DialogueAct.ANSWER,
        "customer_goal": "What is the price?",
        "direct_answer": "The price is $279.",
        "approved_facts": (),
        "facts_to_avoid": (),
        "uncertainty": (),
        "emotional_acknowledgement": None,
        "conversation_delta": (),
        "follow_up": None,
        "next_step": None,
        "style": ResponseStyle.CONCISE,
        "target_language": "en",
        "max_words": 40,
        "recent_phrases_to_avoid": (),
        "prohibited_claims": (),
    }
    values.update(changes)
    return ResponseBrief(**values)  # type: ignore[arg-type]


def test_quality_review_rejects_unplanned_follow_up_and_cta() -> None:
    review = review_response_quality(
        message="The price is $279. Would you like me to recommend another option? Buy now.",
        brief=brief(),
    )

    assert not review.passed
    assert {issue.code.value for issue in review.issues} == {
        "unplanned_follow_up",
        "unplanned_cta",
    }


def test_quality_review_requires_handoff_semantics() -> None:
    review = review_response_quality(
        message="I will look into that.",
        brief=brief(dialogue_act=DialogueAct.HANDOFF),
    )

    assert not review.passed
    assert review.issues[0].code.value == "handoff_semantics_missing"


def test_quality_review_accepts_planned_follow_up() -> None:
    review = review_response_quality(
        message="The material changes the care steps. Which material is your model?",
        brief=brief(follow_up="material"),
    )

    assert review.passed


def test_quality_review_rejects_unplanned_recommendation() -> None:
    review = review_response_quality(
        message="I can recommend another option that may suit you better.",
        brief=brief(),
    )

    assert not review.passed
    assert review.issues[0].code.value == "unplanned_recommendation"
