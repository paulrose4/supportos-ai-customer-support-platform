import re
from enum import StrEnum


class ConversationSpeechAct(StrEnum):
    QUESTION = "question"
    COMPARISON = "comparison"
    RECOMMENDATION_REQUEST = "recommendation_request"
    CORRECTION = "correction"
    ACKNOWLEDGEMENT = "acknowledgement"
    THANKS = "thanks"
    REFUSAL = "refusal"
    OBJECTION = "objection"
    TOPIC_SWITCH = "topic_switch"
    CLOSE = "close"
    HUMAN_REQUEST = "human_request"
    GENERAL = "general"


class RecommendationPermission(StrEnum):
    NONE = "none"
    EXPLICIT = "explicit"
    DENIED = "denied"


class FollowUpPermission(StrEnum):
    NONE = "none"
    ONE = "one"


_QUESTION_RE = re.compile(
    r"(?i)[?？]|\b(?:what|which|who|where|when|why|how|is|are|can|could|will|would|"
    r"does|do|tell me|show me|compare|recommend)\b|"
    r"(?:什么|哪(?:个|款|些)?|谁|哪里|何时|为什么|怎么|如何|是否|能否|可以|比较|推荐)"
)
_CORRECTION_TERMS = (
    "that's not what i asked",
    "you did not answer",
    "not what i meant",
    "no, i meant",
    "不对",
    "不是我问的",
    "你没有回答",
    "我说的是",
)
_THANKS_TERMS = (
    "thanks",
    "thank you",
    "thx",
    "谢谢",
    "多谢",
    "不客气",
)
_CLOSE_TERMS = (
    "that's all",
    "that is all",
    "i'm done",
    "i am done",
    "never mind",
    "no need",
    "先这样",
    "就这样",
    "没事了",
    "算了",
    "不用了",
    "不用推荐",
)
_ACK_TERMS = (
    "ok",
    "okay",
    "got it",
    "understood",
    "明白了",
    "好的",
    "好吧",
    "收到",
    "了解",
)
_REFUSAL_TERMS = (
    "don't recommend",
    "do not recommend",
    "no recommendation",
    "不要推荐",
    "别推荐",
    "不用推荐",
    "不要推销",
    "别推销",
)
_RECOMMENDATION_TERMS = (
    "recommend",
    "recommendation",
    "which one should i choose",
    "which should i choose",
    "show me options",
    "find me",
    "looking for",
    "what do you suggest",
    "推荐",
    "建议哪款",
    "帮我选",
    "想找",
    "适合我",
)
_TOPIC_SWITCH_TERMS = (
    "another question",
    "different question",
    "separate issue",
    "换个问题",
    "另一个问题",
    "先不说这个",
)


def classify_conversation_speech_act(message: str) -> ConversationSpeechAct:
    normalized = " ".join(message.casefold().split())
    if not normalized:
        return ConversationSpeechAct.GENERAL
    if _contains(normalized, _CORRECTION_TERMS):
        return ConversationSpeechAct.CORRECTION
    if _contains(normalized, _TOPIC_SWITCH_TERMS):
        return ConversationSpeechAct.TOPIC_SWITCH
    if _contains(normalized, _REFUSAL_TERMS) and not _QUESTION_RE.search(normalized):
        return ConversationSpeechAct.REFUSAL
    if _QUESTION_RE.search(normalized):
        if _contains(normalized, _REFUSAL_TERMS):
            return ConversationSpeechAct.QUESTION
        if _contains(normalized, _RECOMMENDATION_TERMS):
            return ConversationSpeechAct.RECOMMENDATION_REQUEST
        if any(
            term in normalized
            for term in ("compare", "difference", "better", "哪个", "区别", "哪个好")
        ):
            return ConversationSpeechAct.COMPARISON
        return ConversationSpeechAct.QUESTION
    if _contains(normalized, _THANKS_TERMS):
        return ConversationSpeechAct.THANKS
    if _contains(normalized, _CLOSE_TERMS):
        return ConversationSpeechAct.CLOSE
    if _contains(normalized, _ACK_TERMS):
        return ConversationSpeechAct.ACKNOWLEDGEMENT
    return ConversationSpeechAct.GENERAL


def recommendation_permission(message: str) -> RecommendationPermission:
    normalized = " ".join(message.casefold().split())
    if _contains(normalized, _REFUSAL_TERMS):
        return RecommendationPermission.DENIED
    if _contains(normalized, _RECOMMENDATION_TERMS):
        return RecommendationPermission.EXPLICIT
    return RecommendationPermission.NONE


def follow_up_permission(
    message: str,
    *,
    speech_act: ConversationSpeechAct | None = None,
) -> FollowUpPermission:
    act = speech_act or classify_conversation_speech_act(message)
    if act in {
        ConversationSpeechAct.ACKNOWLEDGEMENT,
        ConversationSpeechAct.THANKS,
        ConversationSpeechAct.CLOSE,
        ConversationSpeechAct.REFUSAL,
    }:
        return FollowUpPermission.NONE
    return FollowUpPermission.ONE


def is_conversation_close(message: str) -> bool:
    return classify_conversation_speech_act(message) in {
        ConversationSpeechAct.ACKNOWLEDGEMENT,
        ConversationSpeechAct.THANKS,
        ConversationSpeechAct.CLOSE,
    }


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
