import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_QUESTION = re.compile(r"[?？]")
_SENTENCE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
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


@dataclass(frozen=True, slots=True)
class ConversationExperienceGrade:
    case_id: str
    direct_answered: bool
    context_continuity: bool
    unnecessary_follow_up_free: bool
    unnecessary_cta_free: bool
    phrase_repetition_free: bool
    correction_handled: bool
    handoff_quality: bool
    tone_matched: bool

    @property
    def passed(self) -> bool:
        return all(
            (
                self.direct_answered,
                self.context_continuity,
                self.unnecessary_follow_up_free,
                self.unnecessary_cta_free,
                self.phrase_repetition_free,
                self.correction_handled,
                self.handoff_quality,
                self.tone_matched,
            )
        )


@dataclass(frozen=True, slots=True)
class ConversationExperienceReport:
    case_count: int
    passed_case_count: int
    metrics: dict[str, float]
    failed_case_ids: tuple[str, ...]

    def passes(self, thresholds: Mapping[str, float]) -> bool:
        return all(self.metrics.get(name, 0.0) >= minimum for name, minimum in thresholds.items())


def grade_conversation_case(case: Mapping[str, Any]) -> ConversationExperienceGrade:
    turns = tuple(case.get("turns") or ())
    assistant_turns = tuple(turn for turn in turns if turn.get("role") == "assistant")
    if not assistant_turns:
        raise ValueError("conversation experience case requires an assistant turn")
    latest = str(assistant_turns[-1].get("content") or "").strip()
    first_two = " ".join(_SENTENCE.split(latest)[:2]).casefold()
    required_direct = _normalized_terms(case.get("direct_answer_terms", ()))
    required_context = _normalized_terms(case.get("required_context_terms", ()))
    correction_terms = _normalized_terms(case.get("correction_ack_terms", ()))
    handoff_terms = _normalized_terms(case.get("handoff_context_terms", ()))
    forbidden_tone = _normalized_terms(case.get("forbidden_tone_terms", ()))
    normalized_latest = latest.casefold()
    follow_up_allowed = bool(case.get("follow_up_allowed", False))
    cta_allowed = bool(case.get("cta_allowed", False))
    prior_responses = tuple(
        str(turn.get("content") or "")
        for turn in assistant_turns[:-1]
        if str(turn.get("content") or "").strip()
    )
    return ConversationExperienceGrade(
        case_id=str(case.get("id") or "").strip(),
        direct_answered=not required_direct or all(term in first_two for term in required_direct),
        context_continuity=(
            not required_context or all(term in normalized_latest for term in required_context)
        ),
        unnecessary_follow_up_free=follow_up_allowed or not _QUESTION.search(latest),
        unnecessary_cta_free=cta_allowed
        or not any(term in normalized_latest for term in _CTA_TERMS),
        phrase_repetition_free=not _repeats_prior_sentence(latest, prior_responses),
        correction_handled=(
            not correction_terms or any(term in normalized_latest for term in correction_terms)
        ),
        handoff_quality=not handoff_terms
        or all(term in normalized_latest for term in handoff_terms),
        tone_matched=not any(term in normalized_latest for term in forbidden_tone),
    )


def build_experience_report(
    grades: Sequence[ConversationExperienceGrade],
) -> ConversationExperienceReport:
    if not grades:
        raise ValueError("conversation experience evaluation requires at least one case")
    count = len(grades)

    def rate(attribute: str) -> float:
        return sum(bool(getattr(grade, attribute)) for grade in grades) / count

    metrics = {
        "case_pass_rate": sum(grade.passed for grade in grades) / count,
        "direct_answer_rate": rate("direct_answered"),
        "context_continuity_rate": rate("context_continuity"),
        "unnecessary_follow_up_free_rate": rate("unnecessary_follow_up_free"),
        "unnecessary_cta_free_rate": rate("unnecessary_cta_free"),
        "phrase_repetition_free_rate": rate("phrase_repetition_free"),
        "correction_handling_rate": rate("correction_handled"),
        "handoff_quality_rate": rate("handoff_quality"),
        "tone_match_rate": rate("tone_matched"),
    }
    return ConversationExperienceReport(
        case_count=count,
        passed_case_count=sum(grade.passed for grade in grades),
        metrics=metrics,
        failed_case_ids=tuple(grade.case_id for grade in grades if not grade.passed),
    )


def _normalized_terms(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value).strip().casefold() for value in values if str(value).strip())


def _repeats_prior_sentence(latest: str, prior_responses: tuple[str, ...]) -> bool:
    latest_sentences = {_normalize_sentence(value) for value in _SENTENCE.split(latest)}
    latest_sentences.discard("")
    prior_sentences = {
        _normalize_sentence(value)
        for response in prior_responses[-3:]
        for value in _SENTENCE.split(response)
    }
    prior_sentences.discard("")
    return any(len(sentence) >= 12 and sentence in prior_sentences for sentence in latest_sentences)


def _normalize_sentence(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold().strip(".!?。！？")
