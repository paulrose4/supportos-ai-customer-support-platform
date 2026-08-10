import re

from app.domain.models import (
    DialogueAct,
    ResponseBrief,
    ResponseQualityIssue,
    ResponseQualityIssueCode,
    ResponseQualityReview,
)

_QUESTION_PATTERN = re.compile(r"[?？]")
_CTA_TERMS = (
    "buy now",
    "order now",
    "checkout now",
    "would you like me to recommend",
    "立即购买",
    "马上下单",
    "现在下单",
    "要不要我推荐",
)
_RECOMMENDATION_TERMS = (
    "recommend",
    "recommendation",
    "another option",
    "other option",
    "best option",
    "which one should",
    "推荐",
    "推荐一款",
    "其他选择",
    "更适合",
)


def review_response_quality(*, message: str, brief: ResponseBrief) -> ResponseQualityReview:
    normalized = message.strip().casefold()
    issues: list[ResponseQualityIssue] = []
    if not normalized:
        issues.append(
            ResponseQualityIssue(
                ResponseQualityIssueCode.EMPTY,
                "Write the approved direct answer in the target language.",
            )
        )
    if any(
        len(phrase.strip()) >= 12 and phrase.strip().casefold() in normalized
        for phrase in brief.recent_phrases_to_avoid
    ):
        issues.append(
            ResponseQualityIssue(
                ResponseQualityIssueCode.REPEATED_PHRASE,
                "Rephrase the repeated sentence while preserving every approved fact.",
            )
        )
    if brief.follow_up is None and _QUESTION_PATTERN.search(message):
        issues.append(
            ResponseQualityIssue(
                ResponseQualityIssueCode.UNPLANNED_FOLLOW_UP,
                "Remove the question because the response brief did not approve a follow-up.",
            )
        )
    if brief.next_step is None and any(term in normalized for term in _CTA_TERMS):
        issues.append(
            ResponseQualityIssue(
                ResponseQualityIssueCode.UNPLANNED_CTA,
                "Remove the sales call to action because the brief did not approve one.",
            )
        )
    if (
        not brief.recommendation_allowed
        and not _QUESTION_PATTERN.search(message)
        and any(term in normalized for term in _RECOMMENDATION_TERMS)
    ):
        issues.append(
            ResponseQualityIssue(
                ResponseQualityIssueCode.UNPLANNED_RECOMMENDATION,
                (
                    "Remove the unsolicited recommendation and answer only the current "
                    "customer request."
                ),
            )
        )
    if brief.dialogue_act is DialogueAct.HANDOFF and not _contains_handoff_semantics(
        message, brief.target_language
    ):
        issues.append(
            ResponseQualityIssue(
                ResponseQualityIssueCode.HANDOFF_SEMANTICS_MISSING,
                "State clearly that the request has been transferred to human support.",
            )
        )
    deduplicated = tuple(dict.fromkeys(issues))
    return ResponseQualityReview(passed=not deduplicated, issues=deduplicated)


def _contains_handoff_semantics(message: str, language: str) -> bool:
    terms = {
        "zh": ("人工", "转交", "转接"),
        "ja": ("担当者", "引き継"),
        "de": ("mitarbeiter", "weitergeleitet", "support-team"),
        "en": ("human", "agent", "support team", "handed off", "transferred"),
    }
    base = language.replace("_", "-").casefold().split("-", 1)[0]
    normalized = message.casefold()
    return any(term in normalized for term in terms.get(base, terms["en"]))
