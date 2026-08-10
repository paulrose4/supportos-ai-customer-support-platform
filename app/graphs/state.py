from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    state_schema_version: int
    conversation_id: str
    message: str
    page_path: str
    normalized_message: str
    response_language: str
    language_context: dict[str, Any]
    site_identity: dict[str, Any]
    input_status: str
    principal: dict[str, Any]
    conversation_history: list[dict[str, str]]
    conversation_memory: dict[str, Any]
    planning_memory: dict[str, Any]
    turn_plan: dict[str, Any]
    speech_act: str
    recommendation_permission: str
    follow_up_permission: str
    conversation_end_signal: bool
    conversation_summary: dict[str, Any]
    conversation_summary_memories: list[dict[str, Any]]
    durable_memories: list[dict[str, str]]
    memory_usage: list[str]
    tenant_experience_memories: list[dict[str, Any]]
    experience_usage: list[str]
    experience_retrieved: list[str]
    experience_treatment: str
    experience_release_version: str
    experience_warnings: list[str]
    reference_clarification: str | None
    resolved_message: str
    risk_level: int
    response_message: str
    response_kind: str
    render_mode: str
    citations: list[str]
    related_links: list[str]
    knowledge_status: str
    knowledge_evidence: list[dict[str, Any]]
    answer_plan: dict[str, Any]
    sales_plan: dict[str, Any]
    response_brief: dict[str, Any]
    response_quality: dict[str, Any]
    retrieval_plan: dict[str, Any]
    retrieval_degraded: bool
    recommended_products: list[dict[str, Any]]
    business_status: str
    business_evidence: list[dict[str, Any]]
    tool_executions: list[dict[str, Any]]
    failed_tools: list[str]
    validation_status: str
    validation_reason: str | None
    validation_disposition: str
    validation_rewrite_count: int
    model_version: str | None
    planner_model_version: str | None
    conflict_id: str | None
    care_procedure_ids: list[str]
    care_step_ids: list[str]
    trace_id: str
    handoff_id: str | None
    handoff_reason: str
    handoff_policy: dict[str, Any]
