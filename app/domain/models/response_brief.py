from dataclasses import dataclass
from enum import StrEnum

from app.domain.models.claim_graph import ResponseClaim
from app.domain.models.turn_plan import EvidenceNeed, TurnSubQuestion


class DialogueAct(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    REASSURE = "reassure"
    COMPARE = "compare"
    RECOMMEND = "recommend"
    HANDOFF = "handoff"
    CLOSE = "close"


class ResponseStyle(StrEnum):
    CONCISE = "concise"
    EXPLANATORY = "explanatory"
    CONSULTATIVE = "consultative"
    EMPATHETIC = "empathetic"
    TRANSACTIONAL = "transactional"


@dataclass(frozen=True, slots=True)
class ApprovedResponseFact:
    fact_type: str
    value: str
    source: str
    fact_id: str = ""
    citation: str | None = None
    freshness: str = "current"
    scope: str = "conversation"


class ResponseQualityIssueCode(StrEnum):
    EMPTY = "empty_response"
    REPEATED_PHRASE = "response_repeats_recent_phrase"
    UNPLANNED_FOLLOW_UP = "unplanned_follow_up"
    UNPLANNED_CTA = "unplanned_cta"
    HANDOFF_SEMANTICS_MISSING = "handoff_semantics_missing"
    UNPLANNED_RECOMMENDATION = "unplanned_recommendation"


@dataclass(frozen=True, slots=True)
class ResponseQualityIssue:
    code: ResponseQualityIssueCode
    repair_instruction: str


@dataclass(frozen=True, slots=True)
class ResponseQualityReview:
    passed: bool
    issues: tuple[ResponseQualityIssue, ...] = ()
    policy_version: str = "response-quality-v1"


@dataclass(frozen=True, slots=True)
class ResponseBrief:
    dialogue_act: DialogueAct
    customer_goal: str
    direct_answer: str
    approved_facts: tuple[ApprovedResponseFact, ...]
    facts_to_avoid: tuple[str, ...]
    uncertainty: tuple[str, ...]
    emotional_acknowledgement: str | None
    conversation_delta: tuple[str, ...]
    follow_up: str | None
    next_step: str | None
    style: ResponseStyle
    target_language: str
    max_words: int
    recent_phrases_to_avoid: tuple[str, ...] = ()
    interaction_preferences: tuple[str, ...] = ()
    prohibited_claims: tuple[str, ...] = ()
    sub_questions: tuple[TurnSubQuestion, ...] = ()
    evidence_needs: tuple[EvidenceNeed, ...] = ()
    required_fact_ids: tuple[str, ...] = ()
    resolved_claims: tuple[ResponseClaim, ...] = ()
    unavailable_question_ids: tuple[str, ...] = ()
    must_cover_all_sub_questions: bool = False
    speech_act: str = "question"
    recommendation_allowed: bool = False
    follow_up_allowed: bool = False
    max_follow_ups: int = 0
    must_say: tuple[str, ...] = ()
    may_say: tuple[str, ...] = ()
    must_not_say: tuple[str, ...] = ()
    conversation_end_signal: bool = False
    citation_mode: str = "related_links"
    policy_version: str = "response-brief-v1"
