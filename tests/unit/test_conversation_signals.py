from app.domain.rules import (
    ConversationSpeechAct,
    RecommendationPermission,
    classify_conversation_speech_act,
    recommendation_permission,
)


def test_short_acknowledgement_is_not_a_new_support_question() -> None:
    assert classify_conversation_speech_act("ok") is ConversationSpeechAct.ACKNOWLEDGEMENT
    assert recommendation_permission("ok") is RecommendationPermission.NONE


def test_comparison_without_explicit_recommendation_stays_comparison() -> None:
    message = "Would you say the black or silver models are better?"

    assert classify_conversation_speech_act(message) is ConversationSpeechAct.COMPARISON
    assert recommendation_permission(message) is RecommendationPermission.NONE


def test_explicit_recommendation_is_allowed() -> None:
    message = "Which model would you recommend for a smaller room?"

    assert classify_conversation_speech_act(message) is ConversationSpeechAct.RECOMMENDATION_REQUEST
    assert recommendation_permission(message) is RecommendationPermission.EXPLICIT


def test_no_sales_preference_is_not_treated_as_a_close_when_question_remains() -> None:
    message = "Don't recommend anything. Just tell me the material."

    assert classify_conversation_speech_act(message) is ConversationSpeechAct.QUESTION
    assert recommendation_permission(message) is RecommendationPermission.DENIED
