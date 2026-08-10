from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvidenceNeed:
    need_id: str
    description: str
    target_entity: str | None = None
    capability_hint: str = "site_knowledge_search"
    freshness_requirement: str = "source_default"
    exact_entity_required: bool = False
    general_knowledge_allowed: bool = False


@dataclass(frozen=True, slots=True)
class TurnSubQuestion:
    question_id: str
    question: str
    evidence_need_ids: tuple[str, ...] = ()
    priority: int = 0


@dataclass(frozen=True, slots=True)
class TurnPlan:
    primary_goal: str
    sub_questions: tuple[TurnSubQuestion, ...]
    evidence_needs: tuple[EvidenceNeed, ...]
    target_entities: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    answer_shape: str = "adaptive"
    ambiguities: tuple[str, ...] = ()
    routing_label: str = "general"
    planner_fallback: bool = False
    planner_fallback_reason: str | None = None
    speech_act: str = "question"
    recommendation_permission: str = "none"
    follow_up_permission: str = "one"
    conversation_end_signal: bool = False
    comparison_axis: str | None = None
    sensitive_attribute_present: bool = False
    policy_version: str = "turn-planner-v1"

    @property
    def requires_semantic_review(self) -> bool:
        return len(self.sub_questions) > 1 or bool(self.ambiguities)


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    name: str
    description: str
    authorization: str = "public"
    freshness: str = "source_default"
