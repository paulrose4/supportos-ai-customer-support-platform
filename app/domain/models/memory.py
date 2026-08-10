from dataclasses import dataclass, field

from app.domain.models.answer import RecommendedProduct
from app.domain.models.sales import QuestionLedgerItem, SalesMemoryFact


@dataclass(frozen=True, slots=True)
class ConversationSummaryMemory:
    memory_id: str
    kind: str
    content: str
    source_message_ids: tuple[str, ...] = ()
    fact_status: str = "unverified"
    source_hash: str = ""
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    discussed_product_skus: tuple[str, ...] = ()
    recent_intents: tuple[str, ...] = ()
    user_constraints: tuple[str, ...] = ()
    compact_text: str = ""
    summarized_message_count: int = 0


@dataclass(frozen=True, slots=True)
class ConversationWorkingMemory:
    active_product_sku: str | None = None
    pending_product_sku: str | None = None
    candidate_product_skus: tuple[str, ...] = ()
    candidate_product_labels: tuple[str, ...] = ()
    candidate_products: tuple[RecommendedProduct, ...] = ()
    country_code: str | None = None
    currency: str | None = None
    confirmed_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    human_requested: bool = False
    last_intent: str | None = None
    response_language: str = "en"
    unresolved_question: str | None = None
    primary_goal: str | None = None
    preference_facts: tuple[SalesMemoryFact, ...] = ()
    objections: tuple[str, ...] = ()
    sales_stage: str = "first_contact"
    next_best_action: str = "answer"
    question_ledger: tuple[QuestionLedgerItem, ...] = ()
    recent_response_phrases: tuple[str, ...] = ()
    interaction_preferences: tuple[str, ...] = ()
    revision: int = 0


@dataclass(frozen=True, slots=True)
class DurableCustomerMemory:
    kind: str
    content: str
    memory_id: str = ""


@dataclass(frozen=True, slots=True)
class ConversationMemoryContext:
    working: ConversationWorkingMemory = field(default_factory=ConversationWorkingMemory)
    summary: ConversationSummary = field(default_factory=ConversationSummary)
    durable_memories: tuple[DurableCustomerMemory, ...] = ()
    summary_memories: tuple[ConversationSummaryMemory, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    resolved_message: str
    clarification: str | None = None
