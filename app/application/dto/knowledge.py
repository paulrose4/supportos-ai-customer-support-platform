from dataclasses import dataclass

from app.domain.models import (
    AnswerPlan,
    AuthenticatedPrincipal,
    ConversationLanguageContext,
    ConversationTurn,
    ConversationWorkingMemory,
    ExperienceGuidance,
    KnowledgeEvidence,
    RecommendedProduct,
    RetrievalContext,
    RetrievalPlan,
    SiteIdentityProfile,
    TurnPlan,
)


@dataclass(frozen=True, slots=True)
class AnswerKnowledgeCommand:
    principal: AuthenticatedPrincipal
    question: str
    language: str = "zh-CN"
    conversation_history: tuple[ConversationTurn, ...] = ()
    page_path: str = "/"
    conversation_summary: str = ""
    durable_memories: tuple[str, ...] = ()
    retrieval_context: RetrievalContext = RetrievalContext()
    sales_memory: ConversationWorkingMemory = ConversationWorkingMemory()
    language_context: ConversationLanguageContext = ConversationLanguageContext()
    site_identity: SiteIdentityProfile = SiteIdentityProfile()
    render_final_response: bool = True
    turn_plan: TurnPlan | None = None
    experience_guidance: tuple[ExperienceGuidance, ...] = ()


@dataclass(frozen=True, slots=True)
class KnowledgeAnswerResult:
    status: str
    message: str | None
    citations: tuple[str, ...]
    evidence: tuple[KnowledgeEvidence, ...]
    conflict_id: str | None = None
    care_procedure_ids: tuple[str, ...] = ()
    care_step_ids: tuple[str, ...] = ()
    related_links: tuple[str, ...] = ()
    answer_plan: AnswerPlan | None = None
    retrieval_plan: RetrievalPlan | None = None
    recommended_products: tuple[RecommendedProduct, ...] = ()
    retrieval_degraded: bool = False
