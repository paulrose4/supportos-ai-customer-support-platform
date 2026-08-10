from dataclasses import dataclass
from enum import StrEnum


class MemoryFactStatus(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class SalesStage(StrEnum):
    FIRST_CONTACT = "first_contact"
    DISCOVERY = "discovery"
    QUALIFICATION = "qualification"
    RECOMMENDATION = "recommendation"
    COMPARISON = "comparison"
    OBJECTION_HANDLING = "objection_handling"
    DECISION_SUPPORT = "decision_support"
    PURCHASE_READY = "purchase_ready"
    HUMAN_HANDOFF = "human_handoff"


class SalesNextAction(StrEnum):
    ANSWER = "answer"
    ASK_ONE_QUESTION = "ask_one_question"
    RECOMMEND = "recommend"
    COMPARE = "compare"
    ADDRESS_OBJECTION = "address_objection"
    OFFER_PURCHASE_PATH = "offer_purchase_path"
    HANDOFF = "handoff"


@dataclass(frozen=True, slots=True)
class SalesMemoryFact:
    key: str
    value: str
    status: MemoryFactStatus
    source_revision: int


@dataclass(frozen=True, slots=True)
class QuestionLedgerItem:
    key: str
    text: str
    status: str
    asked_revision: int


@dataclass(frozen=True, slots=True)
class SiteIdentityProfile:
    domain: str = ""
    agent_display_name: str = ""
    agent_identity_type: str = "team"
    customer_address_mode: str = "neutral"
    introduce_on_first_turn: bool = True
    profile_version: str = "site-identity-v1"


@dataclass(frozen=True, slots=True)
class ConversationLanguageContext:
    target_language: str = "en"
    language_confidence: float = 0.5
    language_source: str = "site_default"
    previous_language: str | None = None
    language_switched: bool = False
    address_form: str = "neutral"
    formality_level: str = "neutral"
    channel: str = "widget"
    locale_hint: str | None = None
    response_length: str = "short"


@dataclass(frozen=True, slots=True)
class ConversationPlaybookCard:
    card_id: str
    objective: str
    allowed_actions: tuple[str, ...]
    evidence_slots: tuple[str, ...]
    follow_up_key: str | None
    prohibited_claims: tuple[str, ...]
    risk_level: int = 0
    version: str = "adult-retail-playbook-v1"


@dataclass(frozen=True, slots=True)
class SalesResponsePlan:
    current_intent: str
    sales_stage: SalesStage
    customer_primary_goal: str | None
    confirmed_preferences: tuple[str, ...]
    primary_objection: str | None
    direct_answer_required: bool
    evidence_required: tuple[str, ...]
    recommended_product_ids: tuple[str, ...]
    recommendation_reasons: tuple[str, ...]
    response_moves: tuple[str, ...]
    missing_information: tuple[str, ...]
    follow_up_question_key: str | None
    next_best_action: SalesNextAction
    target_language: str
    address_form: str
    formality_level: str
    max_words: int
    should_introduce: bool
    should_acknowledge_emotion: bool
    recent_phrases_to_avoid: tuple[str, ...]
    prohibited_claims: tuple[str, ...]
    handoff_required: bool
    playbook_id: str = "general_support"
    playbook_evidence_slots: tuple[str, ...] = ()
    policy_version: str = "sales-response-v1"
