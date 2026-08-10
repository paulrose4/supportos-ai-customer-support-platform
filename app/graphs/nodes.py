import json
from datetime import UTC, datetime, timedelta

from app.application.dto import (
    AnswerKnowledgeCommand,
    CreateHandoffCommand,
    KnowledgeAnswerResult,
    QueryBusinessDataCommand,
    RetrieveTenantExperienceCommand,
)
from app.application.services import (
    AnswerKnowledgeService,
    CreateHandoffService,
    QueryBusinessDataService,
    RenderCustomerResponseService,
    ResponseRepairService,
    SalesConversationService,
    SkeletonResponseService,
    TenantExperienceService,
    TurnUnderstandingService,
)
from app.domain.models import (
    ApprovedResponseFact,
    AuthenticatedPrincipal,
    CareRiskTier,
    ConversationLanguageContext,
    ConversationSummaryMemory,
    ConversationTurn,
    ConversationWorkingMemory,
    DurableCustomerMemory,
    EvidenceNeed,
    ExperienceApplicability,
    ExperienceGuidance,
    ExperiencePolarity,
    MemoryFactStatus,
    OutcomeEvidenceGrade,
    QuestionLedgerItem,
    RecommendedProduct,
    ResponseBrief,
    ResponseKind,
    RetrievalContext,
    RiskLevel,
    SalesMemoryFact,
    SalesNextAction,
    SalesResponsePlan,
    SalesStage,
    SiteIdentityProfile,
    TurnPlan,
    TurnSubQuestion,
)
from app.domain.rules import (
    build_response_brief,
    clarification_for_ambiguous_input,
    classify_care_risk,
    classify_conversation_speech_act,
    classify_customer_sentiment,
    classify_minimum_risk,
    classify_order_handoff,
    classify_support_intent,
    extract_product_skus,
    follow_up_permission,
    interaction_preference_acknowledgement,
    merge_current_turn_sales_memory,
    offsite_refusal,
    protected_internal_data_refusal,
    recommendation_permission,
    resolve_memory_reference,
    response_validation_failure_action,
    review_response_quality,
    select_relevant_memories,
    validate_response_draft,
)
from app.graphs.state import GraphState


class GraphNodes:
    def __init__(
        self,
        response_service: SkeletonResponseService,
        handoff_service: CreateHandoffService,
        knowledge_answer_service: AnswerKnowledgeService,
        business_query_service: QueryBusinessDataService,
        sales_conversation_service: SalesConversationService | None = None,
        response_repair_service: ResponseRepairService | None = None,
        response_renderer_service: RenderCustomerResponseService | None = None,
        turn_understanding_service: TurnUnderstandingService | None = None,
        tenant_experience_service: TenantExperienceService | None = None,
        deterministic_render_enabled: bool = True,
    ) -> None:
        self._response_service = response_service
        self._handoff_service = handoff_service
        self._knowledge_answer_service = knowledge_answer_service
        self._business_query_service = business_query_service
        self._sales_conversation_service = sales_conversation_service or SalesConversationService()
        self._response_repair_service = response_repair_service
        self._response_renderer_service = response_renderer_service
        self._turn_understanding_service = turn_understanding_service
        self._tenant_experience_service = tenant_experience_service
        self._deterministic_render_enabled = deterministic_render_enabled

    async def normalize_input(self, state: GraphState) -> GraphState:
        normalized_message = state["message"].strip()
        principal = _principal_from_state(state)
        working_memory = _working_memory_from_state(state)
        planning_memory = merge_current_turn_sales_memory(
            working_memory,
            message=normalized_message,
        )
        site_identity = self._sales_conversation_service.resolve_site_identity(principal)
        language_context = self._sales_conversation_service.resolve_language_context(
            message=normalized_message,
            history=_conversation_history_from_state(state),
            preferred_language=principal.preferred_language,
            previous_language=(
                working_memory.response_language if working_memory.revision > 0 else None
            ),
            address_mode=site_identity.customer_address_mode,
        )
        response_language = language_context.target_language
        speech_act = classify_conversation_speech_act(normalized_message)
        recommendation_access = recommendation_permission(normalized_message)
        resolution = resolve_memory_reference(
            message=normalized_message,
            memory=_working_memory_from_state(state),
            language=response_language,
        )
        return {
            "normalized_message": resolution.resolved_message,
            "resolved_message": resolution.resolved_message,
            "response_language": response_language,
            "language_context": _language_context_payload(language_context),
            "site_identity": _site_identity_payload(site_identity),
            "planning_memory": _working_memory_payload(planning_memory),
            "reference_clarification": resolution.clarification,
            "speech_act": speech_act.value,
            "recommendation_permission": recommendation_access.value,
            "follow_up_permission": follow_up_permission(
                normalized_message, speech_act=speech_act
            ).value,
            "conversation_end_signal": speech_act.value
            in {
                "acknowledgement",
                "thanks",
                "close",
            },
        }

    async def assess_risk(self, state: GraphState) -> GraphState:
        principal = _principal_from_state(state)
        preference_acknowledgement = interaction_preference_acknowledgement(
            state["normalized_message"],
            state.get("response_language") or principal.preferred_language,
        )
        if preference_acknowledgement:
            return {
                "input_status": "clarification",
                "risk_level": int(RiskLevel.PUBLIC),
                "response_message": preference_acknowledgement,
                "response_kind": ResponseKind.CLARIFICATION.value,
                "citations": [],
                "related_links": [],
                "render_mode": "deterministic",
            }
        if state.get("conversation_end_signal"):
            return {
                "input_status": "clarification",
                "risk_level": int(RiskLevel.PUBLIC),
                "response_message": _conversation_close_message(
                    state.get("response_language"),
                    state.get("speech_act", "acknowledgement"),
                ),
                "response_kind": ResponseKind.ANSWER.value,
                "citations": [],
                "related_links": [],
                "render_mode": "deterministic",
            }
        scope_refusal = protected_internal_data_refusal(
            state["normalized_message"],
            state.get("response_language") or principal.preferred_language,
        ) or offsite_refusal(
            state["normalized_message"],
            state.get("response_language") or principal.preferred_language,
        )
        if scope_refusal:
            return {
                "input_status": "clarification",
                "risk_level": int(RiskLevel.PUBLIC),
                "response_message": scope_refusal,
                "response_kind": ResponseKind.CLARIFICATION.value,
                "citations": [],
                "related_links": [],
                "render_mode": "deterministic",
            }
        clarification = clarification_for_ambiguous_input(
            state["normalized_message"],
            _conversation_history_from_state(state),
            principal.preferred_language,
        )
        if state.get("reference_clarification"):
            return {
                "input_status": "clarification",
                "risk_level": int(RiskLevel.PUBLIC),
                "response_message": state["reference_clarification"],
                "response_kind": ResponseKind.CLARIFICATION.value,
                "citations": [],
                "related_links": [],
            }
        if clarification:
            return {
                "input_status": "clarification",
                "risk_level": int(RiskLevel.PUBLIC),
                "response_message": clarification,
                "response_kind": ResponseKind.CLARIFICATION.value,
                "citations": [],
                "related_links": [],
            }
        if classify_support_intent(state["normalized_message"]).value == "human_handoff":
            return {
                "input_status": "actionable",
                "risk_level": int(RiskLevel.PUBLIC),
                "handoff_reason": "human_requested",
                "handoff_policy": {
                    "user_intent": "human_handoff",
                    "priority": "normal",
                    "queue_id": "support",
                },
            }
        order_handoff = classify_order_handoff(state["normalized_message"])
        if order_handoff is not None:
            return {
                "input_status": "actionable",
                "risk_level": int(order_handoff.risk_level),
                "handoff_reason": order_handoff.reason_code,
                "handoff_policy": {
                    "user_intent": order_handoff.user_intent,
                    "priority": order_handoff.priority,
                    "queue_id": order_handoff.queue_id,
                    "sla_minutes": order_handoff.sla_minutes,
                    "sla_policy_version": order_handoff.sla_policy_version,
                    "order_reference": order_handoff.order_reference,
                },
            }
        risk_level = classify_minimum_risk(state["normalized_message"], principal)
        result: GraphState = {"input_status": "actionable", "risk_level": int(risk_level)}
        if risk_level == RiskLevel.HIGH_IMPACT_REQUEST:
            result["handoff_reason"] = "high_impact_request"
        elif risk_level >= RiskLevel.SEVERE:
            result["handoff_reason"] = "severe_risk_detected"
        return result

    async def understand_turn(self, state: GraphState) -> GraphState:
        if self._turn_understanding_service is None:
            return {}
        result = await self._turn_understanding_service.understand(
            message=state["normalized_message"],
            history=_conversation_history_from_state(state),
            page_path=state.get("page_path", "/"),
            target_language=state.get("response_language", "en"),
            summary_memories=_summary_memories_from_state(state),
            experience_guidance=_experience_guidance_from_state(state),
        )
        return {
            "turn_plan": _turn_plan_payload(result.plan),
            "planner_model_version": result.model,
        }

    async def retrieve_tenant_experience(self, state: GraphState) -> GraphState:
        if self._tenant_experience_service is None:
            return {
                "tenant_experience_memories": [],
                "experience_usage": [],
                "experience_warnings": ["tenant_experience_service_unavailable"],
            }
        working = _planning_memory_from_state(state)
        products = tuple(
            dict.fromkeys(
                (
                    *extract_product_skus(state["normalized_message"]),
                    *(
                        value
                        for value in (
                            working.active_product_sku,
                            working.pending_product_sku,
                            *working.candidate_product_skus,
                        )
                        if value
                    ),
                )
            )
        )
        result = await self._tenant_experience_service.retrieve(
            RetrieveTenantExperienceCommand(
                principal=_principal_from_state(state),
                conversation_id=state["conversation_id"],
                trace_id=state["trace_id"],
                message=state["normalized_message"],
                intent=classify_support_intent(state["normalized_message"]).value,
                product_references=products,
                risk_level=int(state["risk_level"]),
            )
        )
        return {
            "tenant_experience_memories": [
                _experience_guidance_payload(item) for item in result.guidance
            ],
            "experience_usage": (
                [item.memory_id for item in result.guidance] if result.applied else []
            ),
            "experience_retrieved": list(result.retrieved_ids),
            "experience_treatment": result.treatment,
            "experience_release_version": result.release_version,
            "experience_warnings": list(result.warnings),
        }

    async def answer_knowledge(self, state: GraphState) -> GraphState:
        principal = _principal_from_state(state)
        working = _planning_memory_from_state(state)
        relevant_memories = select_relevant_memories(
            tuple(
                DurableCustomerMemory(
                    kind=str(item.get("kind") or "preference"),
                    content=str(item.get("content") or ""),
                    memory_id=str(item.get("memory_id") or ""),
                )
                for item in state.get("durable_memories", [])
                if item.get("content")
            ),
            message=state["normalized_message"],
        )
        try:
            result = await self._knowledge_answer_service.execute(
                AnswerKnowledgeCommand(
                    principal=principal,
                    question=state["normalized_message"],
                    language=state["response_language"],
                    conversation_history=_conversation_history_from_state(state),
                    page_path=state.get("page_path", "/"),
                    conversation_summary=str(
                        state.get("conversation_summary", {}).get("compact_text", "")
                    ),
                    durable_memories=tuple(item.content for item in relevant_memories),
                    retrieval_context=RetrievalContext(
                        active_product_sku=working.active_product_sku,
                        pending_product_sku=working.pending_product_sku,
                        candidate_product_skus=working.candidate_product_skus,
                        country_code=working.country_code,
                        currency=working.currency,
                        confirmed_fields=working.confirmed_fields,
                        unresolved_question=working.unresolved_question,
                        user_constraints=tuple(
                            str(value)
                            for value in state.get("conversation_summary", {}).get(
                                "user_constraints", []
                            )
                        ),
                    ),
                    sales_memory=working,
                    language_context=_language_context_from_state(state),
                    site_identity=_site_identity_from_state(state),
                    render_final_response=self._response_renderer_service is None,
                    turn_plan=_turn_plan_from_state(state),
                    experience_guidance=_experience_guidance_from_state(state),
                )
            )
        except Exception:
            care_risk = classify_care_risk(state["normalized_message"])
            if care_risk is CareRiskTier.HIGH:
                return {
                    "knowledge_status": "tool_failure",
                    "knowledge_evidence": [],
                    "citations": [],
                    "related_links": [],
                    "handoff_reason": "knowledge_care_high_risk",
                    "retrieval_degraded": True,
                }
            return {
                "knowledge_status": "tool_failure",
                "knowledge_evidence": [],
                "citations": [],
                "related_links": [],
                "response_message": _localized_message(
                    state["response_language"], "knowledge_soft_failure"
                ),
                "response_kind": ResponseKind.CLARIFICATION.value,
                "retrieval_degraded": True,
            }

        update: GraphState = {
            "knowledge_status": result.status,
            "citations": list(result.citations),
            "related_links": list(result.related_links),
            "knowledge_evidence": [
                {
                    "document_id": item.document_id,
                    "chunk_id": item.chunk_id,
                    "source": item.source,
                    "text": item.text[:1500],
                    "score": item.score,
                    "category": str(item.metadata.get("category") or "knowledge"),
                    "procedure_id": item.metadata.get("procedure_id"),
                    "approved_step_ids": [
                        str(step.get("step_id"))
                        for step in item.metadata.get("approved_steps", [])
                        if isinstance(step, dict) and step.get("step_id")
                    ],
                    "field_facts": [
                        dict(fact)
                        for fact in item.metadata.get("field_facts", [])
                        if isinstance(fact, dict)
                    ],
                }
                for item in result.evidence
            ],
            "conflict_id": result.conflict_id,
            "retrieval_degraded": result.retrieval_degraded,
            "retrieval_plan": (
                {
                    "intent": result.retrieval_plan.intent,
                    "entities": list(result.retrieval_plan.entities),
                    "sources": list(result.retrieval_plan.sources),
                    "filters": dict(result.retrieval_plan.filters),
                    "query_variants": list(result.retrieval_plan.query_variants),
                    "top_k": result.retrieval_plan.top_k,
                    "rerank_mode": result.retrieval_plan.rerank_mode,
                    "latency_budget_ms": result.retrieval_plan.latency_budget_ms,
                    "fallback_policy": result.retrieval_plan.fallback_policy,
                }
                if result.retrieval_plan is not None
                else {}
            ),
            "recommended_products": [
                {
                    "sku": item.sku,
                    "name": item.name,
                    "price": item.price,
                    "currency": item.currency,
                    "source_url": item.source_url,
                    "material": item.material,
                    "weight": item.weight,
                    "dimensions": item.dimensions,
                    "match_reasons": list(item.match_reasons),
                    "tradeoffs": list(item.tradeoffs),
                    "missing_facts": list(item.missing_facts),
                }
                for item in (
                    result.recommended_products if _can_surface_products(state, result) else ()
                )
            ],
            "memory_usage": [],
            "care_procedure_ids": list(result.care_procedure_ids),
            "care_step_ids": list(result.care_step_ids),
            "answer_plan": (
                {
                    "intent": result.answer_plan.intent.value,
                    "product_ids": list(result.answer_plan.product_ids),
                    "missing_fields": list(result.answer_plan.missing_fields),
                    "next_action": result.answer_plan.next_action.value,
                    "source_mode": result.answer_plan.source_mode.value,
                    "max_words": result.answer_plan.max_words,
                    "response_style": result.answer_plan.response_style,
                }
                if result.answer_plan is not None
                else {}
            ),
        }
        update["speech_act"] = state.get("speech_act", "question")
        update["recommendation_permission"] = state.get("recommendation_permission", "none")
        update["follow_up_permission"] = state.get("follow_up_permission", "one")
        update["conversation_end_signal"] = bool(state.get("conversation_end_signal", False))
        if _can_render_knowledge_deterministically(state, result):
            update["render_mode"] = "deterministic"
        if result.answer_plan is not None:
            sales_plan = self._sales_conversation_service.plan_response(
                message=state["normalized_message"],
                answer_plan=result.answer_plan,
                memory=working,
                language_context=_language_context_from_state(state),
                site_identity=_site_identity_from_state(state),
            )
            update["sales_plan"] = _sales_plan_payload(sales_plan)
        if result.status in {"sufficient", "general_guidance", "care_guidance"} and result.message:
            update["response_message"] = result.message
            update["response_kind"] = ResponseKind.ANSWER.value
            update["memory_usage"] = [
                item.memory_id for item in relevant_memories if item.memory_id
            ]
        elif result.status in {"care_clarification", "clarification"} and result.message:
            update["response_message"] = result.message
            update["response_kind"] = ResponseKind.CLARIFICATION.value
        else:
            update["handoff_reason"] = f"knowledge_{result.status}"
        return update

    async def query_business_data(self, state: GraphState) -> GraphState:
        principal = _principal_from_state(state)
        result = await self._business_query_service.execute(
            QueryBusinessDataCommand(
                principal=principal,
                message=state["normalized_message"],
                trace_id=state["trace_id"],
                language=state["response_language"],
            )
        )
        update: GraphState = {
            "business_status": result.status,
            "citations": list(result.citations),
            "related_links": [],
            "business_evidence": [
                {
                    "source": item.source,
                    "fetched_at": item.fetched_at.isoformat(),
                    "version": item.version,
                    "facts": item.facts,
                }
                for item in result.evidence
            ],
            "tool_executions": [
                {
                    "execution_id": item.execution_id,
                    "tool_name": item.tool_name,
                    "status": item.status,
                    "input_summary": item.input_summary,
                    "output_summary": item.output_summary,
                    "error_code": item.error_code,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in result.tool_executions
            ],
            "failed_tools": list(result.failed_tools),
        }
        if result.status == "sufficient" and result.message is not None:
            update["response_message"] = result.message
            update["response_kind"] = ResponseKind.ANSWER.value
        elif result.status in {"clarification", "identity_required", "not_found"}:
            update["response_message"] = result.message or _localized_message(
                state["response_language"], "business_clarification"
            )
            update["response_kind"] = ResponseKind.CLARIFICATION.value
            update["render_mode"] = "deterministic"
        else:
            update["handoff_reason"] = f"business_{result.status}"
        return update

    async def validate_response(self, state: GraphState) -> GraphState:
        if state.get("conversation_end_signal"):
            return {"validation_status": "valid", "validation_disposition": "respond"}
        validation = validate_response_draft(
            message=state["response_message"],
            response_kind=ResponseKind(state["response_kind"]),
            risk_level=RiskLevel(state["risk_level"]),
            citations=state.get("citations", []),
            knowledge_status=state.get("knowledge_status"),
            knowledge_evidence=state.get("knowledge_evidence", []),
            business_status=state.get("business_status"),
            business_evidence=state.get("business_evidence", []),
            tool_executions=state.get("tool_executions", []),
            care_procedure_ids=state.get("care_procedure_ids", []),
            care_step_ids=state.get("care_step_ids", []),
            sales_plan=state.get("sales_plan", {}),
            language_context=state.get("language_context", {}),
            site_identity=state.get("site_identity", {}),
            conversation_memory=state.get("conversation_memory", {}),
        )
        if validation.is_valid:
            return {"validation_status": "valid", "validation_disposition": "respond"}
        reason = validation.reason_code or "unknown_validation_failure"
        rewrite_count = int(state.get("validation_rewrite_count", 0))
        action = response_validation_failure_action(reason, rewrite_count=rewrite_count)
        if action == "clarify":
            return {
                "validation_status": "valid",
                "validation_reason": reason,
                "validation_disposition": "clarify",
                "response_message": _localized_message(
                    state["response_language"], "validation_clarification"
                ),
                "response_kind": ResponseKind.CLARIFICATION.value,
                "citations": [],
                "related_links": [],
            }
        return {
            "validation_status": "repair" if action == "rewrite" else "invalid",
            "validation_reason": reason,
            "validation_disposition": action,
            **({"handoff_reason": "response_validation_failed"} if action == "handoff" else {}),
        }

    async def render_response(self, state: GraphState) -> GraphState:
        sales_plan = _sales_plan_from_payload(state.get("sales_plan", {}))
        brief = build_response_brief(
            question=state["normalized_message"],
            direct_answer=state["response_message"],
            response_kind=ResponseKind(state["response_kind"]),
            target_language=state.get("response_language", "en"),
            approved_facts=_approved_response_facts(state),
            facts_to_avoid=_facts_to_avoid(state),
            uncertainty=_response_uncertainty(state),
            conversation_delta=_conversation_delta(state),
            sales_plan=sales_plan,
            sentiment=classify_customer_sentiment(state["normalized_message"]),
            interaction_preferences=tuple(
                str(value)
                for value in (state.get("planning_memory") or {}).get("interaction_preferences", [])
            ),
            turn_plan=_turn_plan_from_state(state),
            speech_act=state.get("speech_act"),
            recommendation_permission=state.get("recommendation_permission"),
            follow_up_permission=state.get("follow_up_permission"),
            conversation_end_signal=state.get("conversation_end_signal"),
        )
        update: GraphState = {"response_brief": _response_brief_payload(brief)}
        if self._response_renderer_service is None:
            return update
        if self._deterministic_render_enabled and state.get("render_mode") == "deterministic":
            deterministic_quality = review_response_quality(
                message=state["response_message"],
                brief=brief,
            )
            if deterministic_quality.passed:
                used_fact_ids = [
                    fact.fact_id
                    for fact in brief.approved_facts
                    if fact.fact_id
                    and fact.value.casefold() in state["response_message"].casefold()
                ]
                update["response_quality"] = {
                    "passed": True,
                    "issues": [],
                    "policy_version": deterministic_quality.policy_version,
                    "repaired_issue_codes": [],
                    "generation_count": 0,
                    "fallback": False,
                    "used_fact_ids": used_fact_ids,
                    "answered_sub_question_ids": [item.question_id for item in brief.sub_questions],
                    "claim_citations": [
                        {
                            "fact_id": fact.fact_id,
                            "fact_type": fact.fact_type,
                            "citation": fact.citation,
                        }
                        for fact in brief.approved_facts
                        if fact.fact_id in set(used_fact_ids) and fact.citation is not None
                    ],
                    "semantic_review": {
                        "passed": True,
                        "answered_sub_question_ids": [
                            item.question_id for item in brief.sub_questions
                        ],
                        "missing_sub_question_ids": [],
                        "unsupported_claims": [],
                        "repair_instructions": [],
                        "policy_version": "semantic-response-review-v1",
                    },
                }
                return update
        try:
            rendered = await self._response_renderer_service.render(
                brief=brief,
                history=_conversation_history_from_state(state),
            )
        except Exception as exc:
            update["response_quality"] = {
                "passed": False,
                "issues": [{"code": "renderer_fallback", "repair_instruction": ""}],
                "fallback": True,
                "fallback_reason": type(exc).__name__,
                "generation_count": 0,
                "policy_version": "response-quality-v1",
            }
            if _planned_rag_fallback_requires_clarification(state):
                update.update(
                    {
                        "response_message": _localized_message(
                            state["response_language"], "evidence_not_verified"
                        ),
                        "response_kind": ResponseKind.CLARIFICATION.value,
                        "citations": [],
                        "related_links": [],
                    }
                )
            return update
        update["response_message"] = rendered.text
        update["model_version"] = rendered.model
        update["response_quality"] = {
            "passed": rendered.quality_review.passed,
            "issues": [
                {
                    "code": issue.code.value,
                    "repair_instruction": issue.repair_instruction,
                }
                for issue in rendered.quality_review.issues
            ],
            "policy_version": rendered.quality_review.policy_version,
            "repaired_issue_codes": list(rendered.repaired_issue_codes),
            "generation_count": rendered.generation_count,
            "fallback": False,
            "used_fact_ids": list(rendered.used_fact_ids),
            "answered_sub_question_ids": list(rendered.answered_sub_question_ids),
            "claim_citations": list(rendered.claim_citations),
            "semantic_review": {
                "passed": rendered.semantic_review.passed,
                "answered_sub_question_ids": list(
                    rendered.semantic_review.answered_sub_question_ids
                ),
                "missing_sub_question_ids": list(rendered.semantic_review.missing_sub_question_ids),
                "unsupported_claims": list(rendered.semantic_review.unsupported_claims),
                "repair_instructions": list(rendered.semantic_review.repair_instructions),
                "policy_version": rendered.semantic_review.policy_version,
            },
        }
        return update

    async def repair_response(self, state: GraphState) -> GraphState:
        if self._response_repair_service is None:
            return {
                "validation_rewrite_count": int(state.get("validation_rewrite_count", 0)) + 1,
            }
        try:
            repaired = await self._response_repair_service.rewrite(
                draft=state["response_message"],
                reason_code=str(state.get("validation_reason") or "expression_invalid"),
                target_language=state.get("response_language", "en"),
                max_words=int((state.get("sales_plan") or {}).get("max_words", 120)),
            )
        except Exception:
            return {
                "validation_rewrite_count": int(state.get("validation_rewrite_count", 0)) + 1,
            }
        return {
            "response_message": repaired.text,
            "model_version": repaired.model,
            "validation_rewrite_count": int(state.get("validation_rewrite_count", 0)) + 1,
        }

    async def handoff_response(self, state: GraphState) -> GraphState:
        principal = _principal_from_state(state)
        risk_level = RiskLevel(state["risk_level"])
        reason_code = state.get("handoff_reason", "safe_fallback")
        verified_evidence = tuple(state.get("knowledge_evidence", [])) + tuple(
            state.get("business_evidence", [])
        )
        memory = state.get("conversation_memory") or {}
        product_ids = tuple(
            dict.fromkeys(
                value
                for value in (
                    memory.get("active_product_sku"),
                    *memory.get("candidate_product_skus", []),
                )
                if value
            )
        )
        identity_status = "anonymous" if principal.is_anonymous else "authenticated"
        handoff_policy = state.get("handoff_policy") or {}
        deadline = None
        if handoff_policy.get("sla_minutes"):
            deadline = datetime.now(UTC) + timedelta(minutes=int(handoff_policy["sla_minutes"]))
        handoff = await self._handoff_service.execute(
            CreateHandoffCommand(
                principal=principal,
                conversation_id=state["conversation_id"],
                reason_code=reason_code,
                risk_level=risk_level,
                summary=state["normalized_message"],
                trace_id=state["trace_id"],
                verified_evidence=verified_evidence,
                failed_tools=tuple(state.get("failed_tools", [])),
                knowledge_sources=tuple(state.get("citations", [])),
                user_intent=str(
                    handoff_policy.get("user_intent")
                    or classify_support_intent(state["normalized_message"]).value
                ),
                unresolved_question=memory.get("unresolved_question")
                or state["normalized_message"],
                ai_attempt=(
                    _localized_message(state["response_language"], "order_no_lookup")
                    if reason_code.startswith("order_")
                    else state.get("response_message")
                ),
                suggested_next_action=_suggested_handoff_action(reason_code),
                reply_draft=_handoff_reply_draft(state),
                customer_language=state.get("response_language") or "und",
                customer_region=memory.get("country_code"),
                identity_status=identity_status,
                product_ids=product_ids,
                confirmed_fields=tuple(memory.get("confirmed_fields", [])),
                customer_sentiment=classify_customer_sentiment(state["normalized_message"]),
                customer_request=state["normalized_message"],
                order_id=handoff_policy.get("order_reference"),
                queue_id=handoff_policy.get("queue_id"),
                priority=str(handoff_policy.get("priority") or "normal"),
                sla_policy_version=handoff_policy.get("sla_policy_version"),
                expected_response_at=deadline,
                commitment_deadline=deadline,
            )
        )
        return {
            "response_message": self._handoff_message(reason_code, state),
            "response_kind": ResponseKind.HANDOFF.value,
            "handoff_id": handoff.handoff_id,
            "citations": [],
            "related_links": [],
            "render_mode": "deterministic",
        }

    def route_after_risk(self, state: GraphState) -> str:
        if state.get("input_status") == "clarification":
            return "respond"
        if state.get("handoff_reason"):
            return "handoff"
        if state["risk_level"] == int(RiskLevel.PUBLIC):
            return "knowledge"
        if state["risk_level"] == int(RiskLevel.AUTHENTICATED_READ):
            return "business"
        return "handoff"

    def route_after_knowledge(self, state: GraphState) -> str:
        if state.get("handoff_reason"):
            return "handoff"
        return (
            "respond"
            if state["knowledge_status"]
            in {
                "sufficient",
                "general_guidance",
                "care_guidance",
                "care_clarification",
                "clarification",
                "tool_failure",
            }
            else "handoff"
        )

    def route_after_business(self, state: GraphState) -> str:
        if state["business_status"] in {
            "sufficient",
            "clarification",
            "identity_required",
            "not_found",
        }:
            return "respond"
        return "handoff"

    def route_after_validation(self, state: GraphState) -> str:
        if state["validation_status"] == "valid":
            return "respond"
        if state.get("validation_disposition") == "rewrite":
            return "rewrite"
        return "handoff"

    def route_after_handoff(self, state: GraphState) -> str:
        if self._response_renderer_service is None:
            return "respond"
        if RiskLevel(state["risk_level"]) >= RiskLevel.HIGH_IMPACT_REQUEST:
            return "respond"
        if state.get("handoff_reason") == "response_validation_failed":
            return "respond"
        return "render"

    def _handoff_message(self, reason_code: str, state: GraphState) -> str:
        if reason_code.startswith("order_"):
            return _localized_message(state["response_language"], "order_handoff")
        if reason_code in {"knowledge_care_high_risk", "knowledge_care_special_feature"}:
            return _localized_message(state["response_language"], "care_high_risk")
        if reason_code == "knowledge_care_sop_missing":
            return _localized_message(state["response_language"], "care_sop_missing")
        if reason_code == "knowledge_conflict":
            return _localized_message(state["response_language"], "knowledge_conflict")
        if reason_code == "knowledge_tool_failure":
            return _localized_message(state["response_language"], "knowledge_tool_failure")
        if reason_code.startswith("knowledge_"):
            return _localized_message(state["response_language"], "knowledge_insufficient")
        if reason_code.startswith("business_"):
            return _localized_message(state["response_language"], "business_failure")
        if reason_code == "response_validation_failed":
            return _localized_message(state["response_language"], "validation_failure")
        if reason_code == "high_impact_request":
            return _localized_message(state["response_language"], "high_impact")
        if reason_code == "severe_risk_detected":
            return _localized_message(state["response_language"], "severe_risk")
        if reason_code == "human_requested":
            return _localized_message(state["response_language"], "severe_risk")
        principal = _principal_from_state(state)
        message, _ = self._response_service.build_message(
            message=state["normalized_message"],
            principal=principal,
            risk_level=RiskLevel(state["risk_level"]),
        )
        return message


def _can_render_knowledge_deterministically(
    state: GraphState,
    result: KnowledgeAnswerResult,
) -> bool:
    if (
        result.status != "sufficient"
        or not result.message
        or result.retrieval_degraded
        or not result.evidence
        or state.get("conversation_history")
        or (state.get("planning_memory") or {}).get("interaction_preferences")
        or classify_customer_sentiment(state["normalized_message"]) != "neutral"
    ):
        return False
    turn_plan = _turn_plan_from_state(state)
    if turn_plan is not None and turn_plan.requires_semantic_review:
        return False
    if result.answer_plan is None or result.answer_plan.intent.value in {
        "product_comparison",
        "product_recommendation",
    }:
        return False
    return all(
        item.metadata.get("source_type") == "postgres_product_snapshot" for item in result.evidence
    )


def _can_surface_products(state: GraphState, result: KnowledgeAnswerResult) -> bool:
    if not result.recommended_products:
        return False
    permission = state.get("recommendation_permission", "none")
    if permission == "explicit":
        return True
    plan = result.answer_plan
    if plan is None:
        return False
    return plan.intent.value not in {"product_recommendation", "product_comparison"}


def _conversation_close_message(language: str | None, speech_act: str) -> str:
    base = (language or "en").replace("_", "-").casefold().split("-", 1)[0]
    if speech_act == "thanks":
        messages = {
            "zh": "不客气。",
            "ja": "どういたしまして。",
            "de": "Gern geschehen.",
            "es": "De nada.",
            "fr": "Je vous en prie.",
        }
        return messages.get(base, "You're welcome.")
    messages = {
        "zh": "好的。",
        "ja": "承知しました。",
        "de": "Verstanden.",
        "es": "Entendido.",
        "fr": "Compris.",
    }
    return messages.get(base, "Got it.")


def _suggested_handoff_action(reason_code: str) -> str:
    actions = {
        "response_validation_failed": "核对知识来源后由人工回复",
        "knowledge_insufficient": "确认商品或政策信息并补充知识库",
        "knowledge_conflict": "暂停承诺并由知识负责人确认冲突内容",
        "business_tool_failed": "在可信业务系统中核对实时状态",
        "order_status": "在商城后台验证客户身份并核对订单状态",
        "order_tracking": "在商城后台核对物流轨迹后回复客户",
        "order_cancellation": "人工核对订单状态和取消资格，禁止自动取消",
        "order_refund": "人工核对订单与退款资格，禁止自动退款",
        "order_return": "人工核对订单与退货条件后回复客户",
        "order_address_change": "立即人工核对订单状态和地址修改窗口",
        "order_payment_issue": "立即人工核对支付记录，禁止索取银行卡或密码",
        "order_fulfillment_issue": "人工核对缺件、错发或破损情况并确认处理方案",
        "order_support": "在商城后台验证客户身份并处理订单请求",
    }
    return actions.get(reason_code, "查看会话上下文并由人工确认下一步")


def _handoff_reply_draft(state: GraphState) -> str:
    language = state.get("response_language", "en")
    if state.get("handoff_reason", "").startswith("order_"):
        return _localized_message(language, "order_agent_reply_draft")
    if language.startswith("zh"):
        return "您好，我已经接手这次咨询。请稍等，我会根据您刚才提供的信息继续处理。"
    return "Hello, I have taken over your request. Please give me a moment to review the details."


def _summary_memories_from_state(
    state: GraphState,
) -> tuple[ConversationSummaryMemory, ...]:
    return tuple(
        ConversationSummaryMemory(
            memory_id=str(item.get("memory_id") or ""),
            kind=str(item.get("kind") or "conversation_summary"),
            content=str(item.get("content") or "")[:2000],
            source_message_ids=tuple(str(value) for value in item.get("source_message_ids", [])),
            fact_status=str(item.get("fact_status") or "unverified"),
            source_hash=str(item.get("source_hash") or ""),
            schema_version=int(item.get("schema_version") or 1),
        )
        for item in state.get("conversation_summary_memories", [])[:2]
        if item.get("content")
    )


def _experience_guidance_payload(item: ExperienceGuidance) -> dict[str, object]:
    return {
        "memory_id": item.memory_id,
        "polarity": item.polarity.value,
        "summary": item.summary[:1200],
        "avoid_actions": list(item.avoid_actions),
        "suggested_clarifications": list(item.suggested_clarifications),
        "retrieval_hints": list(item.retrieval_hints),
        "escalation_hint": item.escalation_hint,
        "approved_evidence_refs": list(item.approved_evidence_refs),
        "applicability": {
            "intents": list(item.applicability.intents),
            "product_references": list(item.applicability.product_references),
            "materials": list(item.applicability.materials),
            "regions": list(item.applicability.regions),
            "minimum_risk": item.applicability.minimum_risk,
            "maximum_risk": item.applicability.maximum_risk,
            "exclusions": list(item.applicability.exclusions),
        },
        "outcome_evidence_grade": item.outcome_evidence_grade.value,
        "freshness": item.freshness,
        "sample_count": item.sample_count,
        "uncertainty": item.uncertainty,
        "wilson_lower_bound": item.wilson_lower_bound,
        "limitations": list(item.limitations),
    }


def _experience_guidance_from_state(
    state: GraphState,
) -> tuple[ExperienceGuidance, ...]:
    guidance: list[ExperienceGuidance] = []
    for item in state.get("tenant_experience_memories", [])[:2]:
        applicability = item.get("applicability") or {}
        try:
            guidance.append(
                ExperienceGuidance(
                    memory_id=str(item.get("memory_id") or ""),
                    polarity=ExperiencePolarity(str(item.get("polarity") or "neutral")),
                    summary=str(item.get("summary") or "")[:1200],
                    avoid_actions=tuple(str(value) for value in item.get("avoid_actions", [])),
                    suggested_clarifications=tuple(
                        str(value) for value in item.get("suggested_clarifications", [])
                    ),
                    retrieval_hints=tuple(str(value) for value in item.get("retrieval_hints", [])),
                    escalation_hint=(
                        str(item["escalation_hint"]) if item.get("escalation_hint") else None
                    ),
                    approved_evidence_refs=tuple(
                        str(value) for value in item.get("approved_evidence_refs", [])
                    ),
                    applicability=ExperienceApplicability(
                        intents=tuple(str(value) for value in applicability.get("intents", [])),
                        product_references=tuple(
                            str(value) for value in applicability.get("product_references", [])
                        ),
                        materials=tuple(str(value) for value in applicability.get("materials", [])),
                        regions=tuple(str(value) for value in applicability.get("regions", [])),
                        minimum_risk=int(applicability.get("minimum_risk", 0)),
                        maximum_risk=int(applicability.get("maximum_risk", 1)),
                        exclusions=tuple(
                            str(value) for value in applicability.get("exclusions", [])
                        ),
                    ),
                    outcome_evidence_grade=OutcomeEvidenceGrade(
                        str(item.get("outcome_evidence_grade") or "unknown")
                    ),
                    freshness=str(item.get("freshness") or "current"),
                    sample_count=int(item.get("sample_count") or 0),
                    uncertainty=float(item.get("uncertainty") or 1.0),
                    wilson_lower_bound=float(item.get("wilson_lower_bound") or 0.0),
                    limitations=tuple(str(value) for value in item.get("limitations", [])),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(guidance)


def _principal_from_state(state: GraphState) -> AuthenticatedPrincipal:
    data = state["principal"]
    return AuthenticatedPrincipal(
        subject_id=str(data["subject_id"]),
        tenant_id=str(data["tenant_id"]),
        roles=frozenset(data["roles"]),
        scopes=frozenset(data["scopes"]),
        authentication_method=str(data["authentication_method"]),
        authenticated_at=datetime.fromisoformat(str(data["authenticated_at"])),
        correlation_id=str(data["correlation_id"]),
        site_id=str(data["site_id"]) if data.get("site_id") else None,
        preferred_language=(
            str(data["preferred_language"]) if data.get("preferred_language") else None
        ),
        site_domain=str(data["site_domain"]) if data.get("site_domain") else None,
        agent_display_name=(
            str(data["agent_display_name"]) if data.get("agent_display_name") else None
        ),
        agent_identity_type=str(data.get("agent_identity_type") or "team"),
        customer_address_mode=str(data.get("customer_address_mode") or "neutral"),
        introduce_on_first_turn=bool(data.get("introduce_on_first_turn", True)),
        site_identity_version=str(data.get("site_identity_version") or "site-identity-v1"),
    )


def _conversation_history_from_state(state: GraphState) -> tuple[ConversationTurn, ...]:
    return tuple(
        ConversationTurn(
            role=str(item["role"]),
            content=str(item["content"]),
        )
        for item in state.get("conversation_history", [])
    )


def _turn_plan_payload(plan: TurnPlan) -> dict[str, object]:
    return {
        "primary_goal": plan.primary_goal,
        "sub_questions": [
            {
                "question_id": item.question_id,
                "question": item.question,
                "evidence_need_ids": list(item.evidence_need_ids),
                "priority": item.priority,
            }
            for item in plan.sub_questions
        ],
        "evidence_needs": [
            {
                "need_id": item.need_id,
                "description": item.description,
                "target_entity": item.target_entity,
                "capability_hint": item.capability_hint,
                "freshness_requirement": item.freshness_requirement,
                "exact_entity_required": item.exact_entity_required,
                "general_knowledge_allowed": item.general_knowledge_allowed,
            }
            for item in plan.evidence_needs
        ],
        "target_entities": list(plan.target_entities),
        "constraints": list(plan.constraints),
        "answer_shape": plan.answer_shape,
        "ambiguities": list(plan.ambiguities),
        "routing_label": plan.routing_label,
        "planner_fallback": plan.planner_fallback,
        "planner_fallback_reason": plan.planner_fallback_reason,
        "speech_act": plan.speech_act,
        "recommendation_permission": plan.recommendation_permission,
        "follow_up_permission": plan.follow_up_permission,
        "conversation_end_signal": plan.conversation_end_signal,
        "comparison_axis": plan.comparison_axis,
        "sensitive_attribute_present": plan.sensitive_attribute_present,
        "requires_semantic_review": plan.requires_semantic_review,
        "policy_version": plan.policy_version,
    }


def _turn_plan_from_state(state: GraphState) -> TurnPlan | None:
    payload = state.get("turn_plan") or {}
    if not payload.get("sub_questions"):
        return None
    return TurnPlan(
        primary_goal=str(payload.get("primary_goal") or state.get("normalized_message") or ""),
        sub_questions=tuple(
            TurnSubQuestion(
                question_id=str(item.get("question_id") or ""),
                question=str(item.get("question") or ""),
                evidence_need_ids=tuple(str(value) for value in item.get("evidence_need_ids", [])),
                priority=int(item.get("priority", 0)),
            )
            for item in payload.get("sub_questions", [])
            if item.get("question_id") and item.get("question")
        ),
        evidence_needs=tuple(
            EvidenceNeed(
                need_id=str(item.get("need_id") or ""),
                description=str(item.get("description") or ""),
                target_entity=(str(item["target_entity"]) if item.get("target_entity") else None),
                capability_hint=str(item.get("capability_hint") or "site_knowledge_search"),
                freshness_requirement=str(item.get("freshness_requirement") or "source_default"),
                exact_entity_required=bool(item.get("exact_entity_required", False)),
                general_knowledge_allowed=bool(item.get("general_knowledge_allowed", False)),
            )
            for item in payload.get("evidence_needs", [])
            if item.get("need_id") and item.get("description")
        ),
        target_entities=tuple(str(value) for value in payload.get("target_entities", [])),
        constraints=tuple(str(value) for value in payload.get("constraints", [])),
        answer_shape=str(payload.get("answer_shape") or "adaptive"),
        ambiguities=tuple(str(value) for value in payload.get("ambiguities", [])),
        routing_label=str(payload.get("routing_label") or "general"),
        planner_fallback=bool(payload.get("planner_fallback", False)),
        planner_fallback_reason=(
            str(payload["planner_fallback_reason"])
            if payload.get("planner_fallback_reason")
            else None
        ),
        speech_act=str(payload.get("speech_act") or state.get("speech_act") or "question"),
        recommendation_permission=str(
            payload.get("recommendation_permission")
            or state.get("recommendation_permission")
            or "none"
        ),
        follow_up_permission=str(
            payload.get("follow_up_permission") or state.get("follow_up_permission") or "one"
        ),
        conversation_end_signal=bool(
            payload.get("conversation_end_signal", state.get("conversation_end_signal", False))
        ),
        comparison_axis=(
            str(payload["comparison_axis"]) if payload.get("comparison_axis") else None
        ),
        sensitive_attribute_present=bool(payload.get("sensitive_attribute_present", False)),
        policy_version=str(payload.get("policy_version") or "turn-planner-v1"),
    )


def _working_memory_from_state(state: GraphState) -> ConversationWorkingMemory:
    return _working_memory_from_payload(state.get("conversation_memory", {}))


def _planning_memory_from_state(state: GraphState) -> ConversationWorkingMemory:
    return _working_memory_from_payload(
        state.get("planning_memory") or state.get("conversation_memory", {})
    )


def _working_memory_from_payload(payload: dict[str, object]) -> ConversationWorkingMemory:
    return ConversationWorkingMemory(
        active_product_sku=payload.get("active_product_sku"),
        pending_product_sku=payload.get("pending_product_sku"),
        candidate_product_skus=tuple(
            str(value) for value in payload.get("candidate_product_skus", [])
        ),
        candidate_product_labels=tuple(
            str(value) for value in payload.get("candidate_product_labels", [])
        ),
        candidate_products=tuple(
            RecommendedProduct(
                sku=str(item["sku"]),
                name=str(item["name"]),
                price=None if item.get("price") is None else str(item["price"]),
                currency=None if item.get("currency") is None else str(item["currency"]),
                source_url=(None if item.get("source_url") is None else str(item["source_url"])),
                material=None if item.get("material") is None else str(item["material"]),
                weight=None if item.get("weight") is None else str(item["weight"]),
                dimensions=(
                    {str(key): str(value) for key, value in item["dimensions"].items()}
                    if isinstance(item.get("dimensions"), dict)
                    else None
                ),
                match_reasons=tuple(str(value) for value in item.get("match_reasons", [])),
                tradeoffs=tuple(str(value) for value in item.get("tradeoffs", [])),
                missing_facts=tuple(str(value) for value in item.get("missing_facts", [])),
            )
            for item in payload.get("candidate_products", [])
            if isinstance(item, dict) and item.get("sku") and item.get("name")
        ),
        country_code=payload.get("country_code"),
        currency=payload.get("currency"),
        confirmed_fields=tuple(str(value) for value in payload.get("confirmed_fields", [])),
        missing_fields=tuple(str(value) for value in payload.get("missing_fields", [])),
        human_requested=bool(payload.get("human_requested", False)),
        last_intent=payload.get("last_intent"),
        response_language=str(payload.get("response_language") or "en"),
        unresolved_question=payload.get("unresolved_question"),
        primary_goal=payload.get("primary_goal"),
        preference_facts=tuple(
            SalesMemoryFact(
                key=str(item["key"]),
                value=str(item["value"]),
                status=MemoryFactStatus(str(item.get("status") or "confirmed")),
                source_revision=int(item.get("source_revision", 0)),
            )
            for item in payload.get("preference_facts", [])
            if isinstance(item, dict) and item.get("key") and item.get("value")
        ),
        objections=tuple(str(value) for value in payload.get("objections", [])),
        sales_stage=str(payload.get("sales_stage") or "first_contact"),
        next_best_action=str(payload.get("next_best_action") or "answer"),
        question_ledger=tuple(
            QuestionLedgerItem(
                key=str(item["key"]),
                text=str(item.get("text") or ""),
                status=str(item.get("status") or "asked"),
                asked_revision=int(item.get("asked_revision", 0)),
            )
            for item in payload.get("question_ledger", [])
            if isinstance(item, dict) and item.get("key")
        ),
        recent_response_phrases=tuple(
            str(value) for value in payload.get("recent_response_phrases", [])
        ),
        interaction_preferences=tuple(
            str(value) for value in payload.get("interaction_preferences", [])
        ),
        revision=int(payload.get("revision", 0)),
    )


def _working_memory_payload(memory: ConversationWorkingMemory) -> dict[str, object]:
    return {
        "active_product_sku": memory.active_product_sku,
        "pending_product_sku": memory.pending_product_sku,
        "candidate_product_skus": list(memory.candidate_product_skus),
        "candidate_product_labels": list(memory.candidate_product_labels),
        "candidate_products": [
            {
                "sku": item.sku,
                "name": item.name,
                "price": item.price,
                "currency": item.currency,
                "source_url": item.source_url,
                "material": item.material,
                "weight": item.weight,
                "dimensions": item.dimensions,
                "match_reasons": list(item.match_reasons),
                "tradeoffs": list(item.tradeoffs),
                "missing_facts": list(item.missing_facts),
            }
            for item in memory.candidate_products
        ],
        "country_code": memory.country_code,
        "currency": memory.currency,
        "confirmed_fields": list(memory.confirmed_fields),
        "missing_fields": list(memory.missing_fields),
        "human_requested": memory.human_requested,
        "last_intent": memory.last_intent,
        "response_language": memory.response_language,
        "unresolved_question": memory.unresolved_question,
        "primary_goal": memory.primary_goal,
        "preference_facts": [
            {
                "key": item.key,
                "value": item.value,
                "status": item.status.value,
                "source_revision": item.source_revision,
            }
            for item in memory.preference_facts
        ],
        "objections": list(memory.objections),
        "sales_stage": memory.sales_stage,
        "next_best_action": memory.next_best_action,
        "question_ledger": [
            {
                "key": item.key,
                "text": item.text,
                "status": item.status,
                "asked_revision": item.asked_revision,
            }
            for item in memory.question_ledger
        ],
        "recent_response_phrases": list(memory.recent_response_phrases),
        "interaction_preferences": list(memory.interaction_preferences),
        "revision": memory.revision,
    }


def _language_context_from_state(state: GraphState) -> ConversationLanguageContext:
    payload = state.get("language_context", {})
    return ConversationLanguageContext(
        target_language=str(
            payload.get("target_language") or state.get("response_language") or "en"
        ),
        language_confidence=float(payload.get("language_confidence", 0.5)),
        language_source=str(payload.get("language_source") or "site_default"),
        previous_language=(
            str(payload["previous_language"]) if payload.get("previous_language") else None
        ),
        language_switched=bool(payload.get("language_switched", False)),
        address_form=str(payload.get("address_form") or "neutral"),
        formality_level=str(payload.get("formality_level") or "neutral"),
        channel=str(payload.get("channel") or "widget"),
        locale_hint=str(payload["locale_hint"]) if payload.get("locale_hint") else None,
        response_length=str(payload.get("response_length") or "short"),
    )


def _site_identity_from_state(state: GraphState) -> SiteIdentityProfile:
    payload = state.get("site_identity", {})
    return SiteIdentityProfile(
        domain=str(payload.get("domain") or ""),
        agent_display_name=str(payload.get("agent_display_name") or ""),
        agent_identity_type=str(payload.get("agent_identity_type") or "team"),
        customer_address_mode=str(payload.get("customer_address_mode") or "neutral"),
        introduce_on_first_turn=bool(payload.get("introduce_on_first_turn", True)),
        profile_version=str(payload.get("profile_version") or "site-identity-v1"),
    )


def _language_context_payload(context: ConversationLanguageContext) -> dict[str, object]:
    return {
        "target_language": context.target_language,
        "language_confidence": context.language_confidence,
        "language_source": context.language_source,
        "previous_language": context.previous_language,
        "language_switched": context.language_switched,
        "address_form": context.address_form,
        "formality_level": context.formality_level,
        "channel": context.channel,
        "locale_hint": context.locale_hint,
        "response_length": context.response_length,
    }


def _site_identity_payload(profile: SiteIdentityProfile) -> dict[str, object]:
    return {
        "domain": profile.domain,
        "agent_display_name": profile.agent_display_name,
        "agent_identity_type": profile.agent_identity_type,
        "customer_address_mode": profile.customer_address_mode,
        "introduce_on_first_turn": profile.introduce_on_first_turn,
        "profile_version": profile.profile_version,
    }


def _sales_plan_payload(plan: SalesResponsePlan) -> dict[str, object]:
    return {
        "current_intent": plan.current_intent,
        "sales_stage": plan.sales_stage.value,
        "customer_primary_goal": plan.customer_primary_goal,
        "confirmed_preferences": list(plan.confirmed_preferences),
        "primary_objection": plan.primary_objection,
        "direct_answer_required": plan.direct_answer_required,
        "evidence_required": list(plan.evidence_required),
        "recommended_product_ids": list(plan.recommended_product_ids),
        "recommendation_reasons": list(plan.recommendation_reasons),
        "response_moves": list(plan.response_moves),
        "missing_information": list(plan.missing_information),
        "follow_up_question_key": plan.follow_up_question_key,
        "next_best_action": plan.next_best_action.value,
        "target_language": plan.target_language,
        "address_form": plan.address_form,
        "formality_level": plan.formality_level,
        "max_words": plan.max_words,
        "should_introduce": plan.should_introduce,
        "should_acknowledge_emotion": plan.should_acknowledge_emotion,
        "recent_phrases_to_avoid": list(plan.recent_phrases_to_avoid),
        "prohibited_claims": list(plan.prohibited_claims),
        "handoff_required": plan.handoff_required,
        "playbook_id": plan.playbook_id,
        "playbook_evidence_slots": list(plan.playbook_evidence_slots),
        "policy_version": plan.policy_version,
    }


def _sales_plan_from_payload(payload: dict[str, object]) -> SalesResponsePlan | None:
    if not payload.get("current_intent"):
        return None
    return SalesResponsePlan(
        current_intent=str(payload["current_intent"]),
        sales_stage=SalesStage(str(payload.get("sales_stage") or "first_contact")),
        customer_primary_goal=(
            str(payload["customer_primary_goal"]) if payload.get("customer_primary_goal") else None
        ),
        confirmed_preferences=tuple(
            str(value) for value in payload.get("confirmed_preferences", [])
        ),
        primary_objection=(
            str(payload["primary_objection"]) if payload.get("primary_objection") else None
        ),
        direct_answer_required=bool(payload.get("direct_answer_required", True)),
        evidence_required=tuple(str(value) for value in payload.get("evidence_required", [])),
        recommended_product_ids=tuple(
            str(value) for value in payload.get("recommended_product_ids", [])
        ),
        recommendation_reasons=tuple(
            str(value) for value in payload.get("recommendation_reasons", [])
        ),
        response_moves=tuple(str(value) for value in payload.get("response_moves", [])),
        missing_information=tuple(str(value) for value in payload.get("missing_information", [])),
        follow_up_question_key=(
            str(payload["follow_up_question_key"])
            if payload.get("follow_up_question_key")
            else None
        ),
        next_best_action=SalesNextAction(str(payload.get("next_best_action") or "answer")),
        target_language=str(payload.get("target_language") or "en"),
        address_form=str(payload.get("address_form") or "neutral"),
        formality_level=str(payload.get("formality_level") or "neutral"),
        max_words=int(payload.get("max_words", 120)),
        should_introduce=bool(payload.get("should_introduce", False)),
        should_acknowledge_emotion=bool(payload.get("should_acknowledge_emotion", False)),
        recent_phrases_to_avoid=tuple(
            str(value) for value in payload.get("recent_phrases_to_avoid", [])
        ),
        prohibited_claims=tuple(str(value) for value in payload.get("prohibited_claims", [])),
        handoff_required=bool(payload.get("handoff_required", False)),
        playbook_id=str(payload.get("playbook_id") or "general_support"),
        playbook_evidence_slots=tuple(
            str(value) for value in payload.get("playbook_evidence_slots", [])
        ),
        policy_version=str(payload.get("policy_version") or "sales-response-v1"),
    )


def _approved_response_facts(state: GraphState) -> tuple[ApprovedResponseFact, ...]:
    facts: list[ApprovedResponseFact] = []
    for item in state.get("knowledge_evidence", []):
        field_facts = [
            fact
            for fact in item.get("field_facts", [])
            if isinstance(fact, dict) and fact.get("approved") is True
        ]
        if field_facts:
            facts.extend(
                ApprovedResponseFact(
                    fact_type=str(fact.get("predicate") or "product_attribute"),
                    value=str(fact.get("value") or "")[:1500],
                    source=str(fact.get("source") or item.get("source") or "product_snapshot"),
                    fact_id=str(fact.get("fact_id") or ""),
                    citation=str(fact.get("citation") or "") or None,
                    freshness=str(fact.get("freshness") or "current"),
                    scope=str(fact.get("scope") or "product_snapshot"),
                )
                for fact in field_facts
                if fact.get("value") and fact.get("fact_id")
            )
            continue
        if item.get("text"):
            facts.append(
                ApprovedResponseFact(
                    fact_type=str(item.get("category") or "knowledge"),
                    value=str(item.get("text") or "")[:1500],
                    source=str(item.get("source") or item.get("document_id") or "knowledge"),
                    fact_id=str(item.get("chunk_id") or item.get("document_id") or ""),
                    citation=(
                        f"{item.get('source')}#{item.get('chunk_id')}"
                        if item.get("source") and item.get("chunk_id")
                        else None
                    ),
                    freshness="degraded" if state.get("retrieval_degraded") else "current",
                    scope="knowledge",
                )
            )
    trusted_domain = str(state.get("site_identity", {}).get("domain") or "").strip()
    if trusted_domain:
        facts.append(
            ApprovedResponseFact(
                fact_type="storefront_domain",
                value=f"Current storefront domain: {trusted_domain}",
                source="trusted_site_identity",
                fact_id="site:trusted-storefront-domain",
                freshness="current",
                scope="trusted_site_identity",
            )
        )
    facts.extend(
        ApprovedResponseFact(
            fact_type="business",
            value=json.dumps(item.get("facts") or {}, ensure_ascii=False)[:1500],
            source=str(item.get("source") or "business"),
            fact_id=str((item.get("facts") or {}).get("resource_id") or item.get("source") or ""),
            citation=next(iter(state.get("citations", [])), None),
            freshness="current",
            scope="authenticated_business",
        )
        for item in state.get("business_evidence", [])
        if item.get("facts")
    )
    return tuple(facts[:30])


def _planned_rag_fallback_requires_clarification(state: GraphState) -> bool:
    plan = state.get("turn_plan") or {}
    planned_semantic_turn = bool(plan.get("sub_questions")) and not bool(
        plan.get("planner_fallback", False)
    )
    exact_product_facts = any(
        any(
            isinstance(fact, dict) and fact.get("approved") is True
            for fact in item.get("field_facts", [])
        )
        for item in state.get("knowledge_evidence", [])
    )
    return planned_semantic_turn and not exact_product_facts


def _facts_to_avoid(state: GraphState) -> tuple[str, ...]:
    avoided: list[str] = []
    if state.get("retrieval_degraded"):
        avoided.append("Do not imply that retrieval was complete or current.")
    if state.get("knowledge_status") in {"clarification", "tool_failure", "insufficient"}:
        avoided.append("Do not fill missing store or product facts from model knowledge.")
    if state.get("business_status") not in {None, "sufficient"}:
        avoided.append(
            "Do not infer private business data that was not returned by a trusted tool."
        )
    if state.get("site_identity", {}).get("domain"):
        avoided.append(
            "Do not infer that the storefront, seller, product brand, and manufacturer are the "
            "same entity unless an approved fact explicitly establishes that relationship."
        )
    return tuple(avoided)


def _response_uncertainty(state: GraphState) -> tuple[str, ...]:
    uncertainty: list[str] = []
    if state.get("retrieval_degraded"):
        uncertainty.append("Preserve any freshness or availability qualification in direct_answer.")
    return tuple(uncertainty)


def _conversation_delta(state: GraphState) -> tuple[str, ...]:
    delta: list[str] = []
    language = state.get("language_context", {})
    if language.get("language_switched"):
        delta.append(f"The customer switched to {language.get('target_language')}.")
    memory = state.get("planning_memory", {})
    if memory.get("active_product_sku"):
        delta.append(f"Active product: {memory['active_product_sku']}")
    return tuple(delta)


def _response_brief_payload(brief: ResponseBrief) -> dict[str, object]:
    return {
        "dialogue_act": brief.dialogue_act.value,
        "customer_goal": brief.customer_goal,
        "direct_answer": brief.direct_answer,
        "approved_facts": [
            {
                "fact_type": fact.fact_type,
                "value": fact.value,
                "source": fact.source,
                "fact_id": fact.fact_id,
                "citation": fact.citation,
                "freshness": fact.freshness,
                "scope": fact.scope,
            }
            for fact in brief.approved_facts
        ],
        "facts_to_avoid": list(brief.facts_to_avoid),
        "uncertainty": list(brief.uncertainty),
        "emotional_acknowledgement": brief.emotional_acknowledgement,
        "conversation_delta": list(brief.conversation_delta),
        "follow_up": brief.follow_up,
        "next_step": brief.next_step,
        "style": brief.style.value,
        "target_language": brief.target_language,
        "max_words": brief.max_words,
        "recent_phrases_to_avoid": list(brief.recent_phrases_to_avoid),
        "interaction_preferences": list(brief.interaction_preferences),
        "prohibited_claims": list(brief.prohibited_claims),
        "sub_questions": [
            {
                "question_id": item.question_id,
                "question": item.question,
                "evidence_need_ids": list(item.evidence_need_ids),
                "priority": item.priority,
            }
            for item in brief.sub_questions
        ],
        "evidence_needs": [
            {
                "need_id": item.need_id,
                "description": item.description,
                "target_entity": item.target_entity,
                "capability_hint": item.capability_hint,
                "freshness_requirement": item.freshness_requirement,
                "exact_entity_required": item.exact_entity_required,
                "general_knowledge_allowed": item.general_knowledge_allowed,
            }
            for item in brief.evidence_needs
        ],
        "required_fact_ids": list(brief.required_fact_ids),
        "speech_act": brief.speech_act,
        "recommendation_allowed": brief.recommendation_allowed,
        "follow_up_allowed": brief.follow_up_allowed,
        "max_follow_ups": brief.max_follow_ups,
        "must_say": list(brief.must_say),
        "may_say": list(brief.may_say),
        "must_not_say": list(brief.must_not_say),
        "conversation_end_signal": brief.conversation_end_signal,
        "citation_mode": brief.citation_mode,
        "resolved_claims": [
            {
                "claim_id": item.claim_id,
                "text": item.text,
                "status": item.status.value,
                "fact_ids": list(item.fact_ids),
                "sub_question_ids": list(item.sub_question_ids),
                "qualification": item.qualification,
            }
            for item in brief.resolved_claims
        ],
        "unavailable_question_ids": list(brief.unavailable_question_ids),
        "must_cover_all_sub_questions": brief.must_cover_all_sub_questions,
        "policy_version": brief.policy_version,
    }


_LOCALIZED_MESSAGES = {
    "en": {
        "business_clarification": "Please provide the information needed for the lookup.",
        "knowledge_conflict": (
            "I found conflicting information, so I've sent this to a human agent to confirm."
        ),
        "knowledge_tool_failure": (
            "The knowledge service is temporarily unavailable, so I've sent this to a human agent."
        ),
        "knowledge_soft_failure": (
            "I can't verify the exact store details right now. Please send the product link or "
            "model number, and I can continue once the published information is available."
        ),
        "knowledge_insufficient": (
            "I don't have enough reliable information to answer that, "
            "so I've sent it to a human agent."
        ),
        "care_high_risk": (
            "To avoid damage, this care issue needs a specialist to review the exact model. "
            "I've sent it to a human support agent."
        ),
        "care_sop_missing": (
            "I found the product material, but there is no approved care procedure for this exact "
            "case yet. I've sent it to a human support agent rather than risk giving harmful "
            "advice."
        ),
        "business_failure": (
            "I couldn't safely retrieve the account data, so I've sent this to a human agent."
        ),
        "validation_failure": (
            "I couldn't verify a safe response, so I've sent this to a human agent."
        ),
        "validation_clarification": (
            "I cannot verify that detail from the currently published information. "
            "Please send the product link or model number so I can check the exact item."
        ),
        "evidence_not_verified": (
            "The published information available here does not verify that claim, so I won't "
            "assume it is true."
        ),
        "high_impact": (
            "I can't perform refunds, cancellations, or other irreversible actions here. "
            "A human agent will help you."
        ),
        "order_handoff": (
            "I've sent this to our human order-support team. If available, reply with your order "
            "number and a short description of the issue. Do not send passwords or payment-card "
            "details."
        ),
        "order_no_lookup": (
            "Order-related requests are handled by human support; no order system was queried."
        ),
        "order_agent_reply_draft": (
            "Hello, I've taken over your order-related request. I will verify it in our trusted "
            "store system before confirming any status or action."
        ),
        "severe_risk": "I've sent this to a human agent for safe handling.",
    },
    "zh": {
        "business_clarification": "请补充查询所需信息。",
        "knowledge_conflict": "当前资料存在冲突，已为您转交人工确认，请留意后续通知。",
        "knowledge_tool_failure": "知识服务暂时不可用，已为您转交人工处理，请留意后续通知。",
        "knowledge_soft_failure": (
            "我暂时无法核实准确的站内信息。请发送商品链接或型号，待已发布资料可用后我会继续为您核对。"
        ),
        "knowledge_insufficient": "当前知识不足以可靠回答，已为您转交人工处理，请留意后续通知。",
        "care_high_risk": (
            "为避免产品损坏，这个保养问题需要专业人员核对具体型号，已为您转交人工处理。"
        ),
        "care_sop_missing": (
            "已识别产品材质，但目前没有适用于该情况的已审核保养流程，已为您转交人工确认。"
        ),
        "business_failure": "业务数据暂时无法安全查询，已为您转交人工处理，请留意后续通知。",
        "validation_failure": "回复未通过安全验证，已为您转交人工处理，请留意后续通知。",
        "validation_clarification": (
            "我暂时无法从当前已发布信息中核实这个细节。请发送商品链接或型号，我会按具体商品继续核对。"
        ),
        "evidence_not_verified": "当前可用的已发布信息无法证实这一说法，因此我不会把它当作事实。",
        "high_impact": "这里不会执行退款、取消或其他不可逆操作，已为您转交人工处理。",
        "order_handoff": (
            "已为您转交人工订单客服处理。如方便，请补充订单编号和问题简述。"
            "请勿发送密码、银行卡号或完整支付信息。"
        ),
        "order_no_lookup": "订单相关请求统一由人工客服处理，AI 未查询或推断任何订单状态。",
        "order_agent_reply_draft": (
            "您好，我已经接手您的订单问题。我会先在可信商城后台核对，再向您确认状态或处理结果。"
        ),
        "severe_risk": "已为您转交人工安全处理，请留意后续通知。",
    },
    "ja": {
        "business_clarification": "確認に必要な情報を入力してください。",
        "knowledge_conflict": "情報に矛盾があるため、担当者に確認を依頼しました。",
        "knowledge_tool_failure": (
            "ナレッジサービスが一時的に利用できないため、担当者に引き継ぎました。"
        ),
        "knowledge_soft_failure": (
            "現在、店舗の正確な情報を確認できません。商品リンクまたは型番を送っていただければ、"
            "公開情報が利用可能になり次第、確認を続けます。"
        ),
        "knowledge_insufficient": (
            "確実に回答できる情報が不足しているため、担当者に引き継ぎました。"
        ),
        "care_high_risk": (
            "製品の損傷を避けるため、このお手入れは担当者による型番確認が必要です。"
            "専門スタッフに引き継ぎました。"
        ),
        "care_sop_missing": (
            "素材は確認できましたが、このケースに承認済みのお手入れ手順がないため、"
            "担当者に確認を依頼しました。"
        ),
        "business_failure": "アカウント情報を安全に確認できなかったため、担当者に引き継ぎました。",
        "validation_failure": "安全な回答を確認できなかったため、担当者に引き継ぎました。",
        "high_impact": (
            "返金やキャンセルなどの取り消せない操作はここでは実行せず、担当者に引き継ぎます。"
        ),
        "order_handoff": (
            "注文担当者に引き継ぎました。可能であれば注文番号と問題の概要を返信してください。"
            "パスワードやカード情報は送信しないでください。"
        ),
        "order_no_lookup": (
            "注文関連の依頼は担当者が対応し、AI は注文システムを照会していません。"
        ),
        "order_agent_reply_draft": (
            "ご注文に関するお問い合わせを引き継ぎました。信頼できる店舗システムで確認後、"
            "状況または対応内容をご案内します。"
        ),
        "severe_risk": "安全に対応するため、担当者に引き継ぎました。",
    },
}


def _localized_message(language: str, key: str) -> str:
    base = language.replace("_", "-").casefold().split("-", 1)[0]
    messages = _LOCALIZED_MESSAGES.get(base, _LOCALIZED_MESSAGES["en"])
    return messages[key]
