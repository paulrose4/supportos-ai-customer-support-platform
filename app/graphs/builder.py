from collections.abc import Awaitable, Callable
from datetime import datetime
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.application.services import (
    AnswerKnowledgeService,
    CreateHandoffService,
    QueryBusinessDataService,
    RenderCustomerResponseService,
    ResponseRepairService,
    SkeletonResponseService,
    TenantExperienceService,
    TurnUnderstandingService,
)
from app.domain.models import (
    AgentResponse,
    AuthenticatedPrincipal,
    ConversationMemoryContext,
    ConversationTurn,
    RecommendedProduct,
    ResponseKind,
    RiskLevel,
    ToolExecution,
)
from app.domain.ports import RequestMetricsPort
from app.domain.rules import update_working_memory
from app.graphs.nodes import GraphNodes
from app.graphs.state import GraphState


class LangGraphAgentRunner:
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def run(
        self,
        *,
        conversation_id: str,
        message: str,
        principal: AuthenticatedPrincipal,
        history: tuple[ConversationTurn, ...] = (),
        page_path: str = "/",
        memory: ConversationMemoryContext | None = None,
    ) -> AgentResponse:
        trace_id = str(uuid4())
        initial_state: GraphState = {
            "state_schema_version": 10,
            "conversation_id": conversation_id,
            "message": message,
            "page_path": page_path,
            "principal": {
                "subject_id": principal.subject_id,
                "tenant_id": principal.tenant_id,
                "roles": sorted(principal.roles),
                "scopes": sorted(principal.scopes),
                "authentication_method": principal.authentication_method,
                "authenticated_at": principal.authenticated_at.isoformat(),
                "correlation_id": principal.correlation_id,
                "site_id": principal.site_id,
                "preferred_language": principal.preferred_language,
                "site_domain": principal.site_domain,
                "agent_display_name": principal.agent_display_name,
                "agent_identity_type": principal.agent_identity_type,
                "customer_address_mode": principal.customer_address_mode,
                "introduce_on_first_turn": principal.introduce_on_first_turn,
                "site_identity_version": principal.site_identity_version,
            },
            "conversation_history": [
                {"role": turn.role, "content": turn.content} for turn in history
            ],
            "conversation_memory": _working_payload(
                (memory or ConversationMemoryContext()).working
            ),
            "conversation_summary": _summary_payload(
                (memory or ConversationMemoryContext()).summary
            ),
            "conversation_summary_memories": [
                {
                    "memory_id": item.memory_id,
                    "kind": item.kind,
                    "content": item.content,
                    "source_message_ids": list(item.source_message_ids),
                    "fact_status": item.fact_status,
                    "source_hash": item.source_hash,
                    "schema_version": item.schema_version,
                }
                for item in (memory or ConversationMemoryContext()).summary_memories[:2]
            ],
            "durable_memories": [
                {"memory_id": item.memory_id, "kind": item.kind, "content": item.content}
                for item in (memory or ConversationMemoryContext()).durable_memories
            ],
            "trace_id": trace_id,
        }
        result = await self._graph.ainvoke(initial_state)
        response_kind = ResponseKind(result["response_kind"])
        response_language = str(
            result.get("response_language") or principal.preferred_language or "en"
        )
        recommended_products = tuple(
            RecommendedProduct(
                sku=str(item["sku"]),
                name=str(item["name"]),
                price=None if item.get("price") is None else str(item["price"]),
                currency=None if item.get("currency") is None else str(item["currency"]),
                source_url=None if item.get("source_url") is None else str(item["source_url"]),
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
            for item in result.get("recommended_products", [])
            if item.get("sku") and item.get("name") and item.get("source_url")
        )
        recommended_products = _approved_recommended_products(
            recommended_products,
            site_domain=principal.site_domain,
        )
        approved_links = tuple(
            dict.fromkeys(
                product.source_url
                for product in recommended_products
                if product.source_url is not None
            )
        )
        memory_state = update_working_memory(
            current=(memory or ConversationMemoryContext()).working,
            user_message=message,
            resolved_message=str(result.get("resolved_message") or message),
            response_kind=response_kind,
            response_language=response_language,
            candidate_product_skus=tuple(
                str(value) for value in (result.get("answer_plan") or {}).get("product_ids", [])
            ),
            answer_missing_fields=tuple(
                str(value) for value in (result.get("answer_plan") or {}).get("missing_fields", [])
            ),
            recommended_products=recommended_products,
            assistant_message=str(result.get("response_message") or ""),
        )
        return AgentResponse(
            conversation_id=conversation_id,
            message=result["response_message"],
            kind=response_kind,
            risk_level=RiskLevel(result["risk_level"]),
            trace_id=trace_id,
            handoff_id=result.get("handoff_id"),
            handoff_reason=result.get("handoff_reason"),
            citations=tuple(result.get("citations", [])),
            related_links=(
                approved_links if approved_links else tuple(result.get("related_links", []))
            ),
            recommended_products=recommended_products,
            tool_executions=tuple(
                _tool_execution(item) for item in result.get("tool_executions", [])
            ),
            care_procedure_ids=tuple(result.get("care_procedure_ids", [])),
            care_step_ids=tuple(result.get("care_step_ids", [])),
            memory_state=memory_state,
            used_memory_ids=tuple(str(value) for value in result.get("memory_usage", [])),
            used_experience_ids=tuple(str(value) for value in result.get("experience_usage", [])),
            sales_plan=dict(result.get("sales_plan") or {}),
            response_brief=dict(result.get("response_brief") or {}),
            response_quality=dict(result.get("response_quality") or {}),
            turn_plan=dict(result.get("turn_plan") or {}),
            validation_reason=(
                str(result["validation_reason"]) if result.get("validation_reason") else None
            ),
            knowledge_status=(
                str(result["knowledge_status"]) if result.get("knowledge_status") else None
            ),
            retrieval_degraded=bool(result.get("retrieval_degraded", False)),
            validation_rewrite_count=int(result.get("validation_rewrite_count", 0)),
            model_version=(str(result["model_version"]) if result.get("model_version") else None),
            planner_model_version=(
                str(result["planner_model_version"])
                if result.get("planner_model_version")
                else None
            ),
            evidence_ids=tuple(
                str(item.get("chunk_id") or item.get("source"))
                for item in (
                    *result.get("knowledge_evidence", []),
                    *result.get("business_evidence", []),
                )
                if item.get("chunk_id") or item.get("source")
            )[:20],
        )


def build_customer_support_graph(
    *,
    response_service: SkeletonResponseService,
    handoff_service: CreateHandoffService,
    knowledge_answer_service: AnswerKnowledgeService,
    business_query_service: QueryBusinessDataService,
    response_repair_service: ResponseRepairService | None = None,
    response_renderer_service: RenderCustomerResponseService | None = None,
    turn_understanding_service: TurnUnderstandingService | None = None,
    tenant_experience_service: TenantExperienceService | None = None,
    metrics: RequestMetricsPort | None = None,
    deterministic_render_enabled: bool = True,
) -> Any:
    nodes = GraphNodes(
        response_service,
        handoff_service,
        knowledge_answer_service,
        business_query_service,
        response_repair_service=response_repair_service,
        response_renderer_service=response_renderer_service,
        turn_understanding_service=turn_understanding_service,
        tenant_experience_service=tenant_experience_service,
        deterministic_render_enabled=deterministic_render_enabled,
    )
    graph = StateGraph(GraphState)
    graph.add_node(
        "normalize_input", _instrument_node("normalize_input", nodes.normalize_input, metrics)
    )
    graph.add_node("assess_risk", _instrument_node("assess_risk", nodes.assess_risk, metrics))
    graph.add_node(
        "retrieve_tenant_experience",
        _instrument_node("retrieve_tenant_experience", nodes.retrieve_tenant_experience, metrics),
    )
    graph.add_node(
        "understand_turn", _instrument_node("understand_turn", nodes.understand_turn, metrics)
    )
    graph.add_node(
        "answer_knowledge", _instrument_node("answer_knowledge", nodes.answer_knowledge, metrics)
    )
    graph.add_node(
        "query_business_data",
        _instrument_node("query_business_data", nodes.query_business_data, metrics),
    )
    graph.add_node(
        "render_response", _instrument_node("render_response", nodes.render_response, metrics)
    )
    graph.add_node(
        "validate_response", _instrument_node("validate_response", nodes.validate_response, metrics)
    )
    graph.add_node(
        "repair_response", _instrument_node("repair_response", nodes.repair_response, metrics)
    )
    graph.add_node(
        "handoff_response", _instrument_node("handoff_response", nodes.handoff_response, metrics)
    )
    graph.add_edge(START, "normalize_input")
    graph.add_edge("normalize_input", "assess_risk")
    graph.add_conditional_edges(
        "assess_risk",
        nodes.route_after_risk,
        {
            "knowledge": "retrieve_tenant_experience",
            "business": "retrieve_tenant_experience",
            "handoff": "handoff_response",
            "respond": "render_response",
        },
    )
    graph.add_edge("retrieve_tenant_experience", "understand_turn")
    graph.add_conditional_edges(
        "understand_turn",
        nodes.route_after_risk,
        {
            "knowledge": "answer_knowledge",
            "business": "query_business_data",
            "handoff": "handoff_response",
            "respond": "render_response",
        },
    )
    graph.add_conditional_edges(
        "answer_knowledge",
        nodes.route_after_knowledge,
        {"respond": "render_response", "handoff": "handoff_response"},
    )
    graph.add_conditional_edges(
        "query_business_data",
        nodes.route_after_business,
        {"respond": "render_response", "handoff": "handoff_response"},
    )
    graph.add_edge("render_response", "validate_response")
    graph.add_conditional_edges(
        "validate_response",
        nodes.route_after_validation,
        {"respond": END, "rewrite": "repair_response", "handoff": "handoff_response"},
    )
    graph.add_edge("repair_response", "validate_response")
    graph.add_conditional_edges(
        "handoff_response",
        nodes.route_after_handoff,
        {"render": "render_response", "respond": END},
    )
    return graph.compile()


def _instrument_node(
    stage: str,
    handler: Callable[[GraphState], Awaitable[GraphState]],
    metrics: RequestMetricsPort | None,
) -> Callable[[GraphState], Awaitable[GraphState]]:
    if metrics is None:
        return handler

    async def instrumented(state: GraphState) -> GraphState:
        started = perf_counter()
        outcome = "success"
        try:
            return await handler(state)
        except Exception:
            outcome = "error"
            raise
        finally:
            metrics.record_agent_stage(
                stage=stage,
                outcome=outcome,
                duration_seconds=perf_counter() - started,
            )

    return instrumented


def _tool_execution(data: dict[str, Any]) -> ToolExecution:
    created_at = data.get("created_at")
    return ToolExecution(
        execution_id=str(data["execution_id"]),
        tool_name=str(data["tool_name"]),
        status=str(data["status"]),
        input_summary=dict(data.get("input_summary", {})),
        output_summary=dict(data.get("output_summary", {})),
        error_code=str(data["error_code"]) if data.get("error_code") else None,
        created_at=datetime.fromisoformat(str(created_at)) if created_at else None,
    )


def _approved_recommended_products(
    products: tuple[RecommendedProduct, ...],
    *,
    site_domain: str | None,
) -> tuple[RecommendedProduct, ...]:
    expected_domain = (site_domain or "").strip().casefold()
    approved: list[RecommendedProduct] = []
    seen_urls: set[str] = set()
    for product in products:
        url = (product.source_url or "").strip()
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or not hostname:
            continue
        if (
            expected_domain
            and hostname != expected_domain
            and not hostname.endswith(f".{expected_domain}")
        ):
            continue
        canonical = parsed.geturl()
        if canonical in seen_urls:
            continue
        approved.append(product)
        seen_urls.add(canonical)
    return tuple(approved[:3])


def _working_payload(working: Any) -> dict[str, Any]:
    return {
        "active_product_sku": working.active_product_sku,
        "pending_product_sku": working.pending_product_sku,
        "candidate_product_skus": list(working.candidate_product_skus),
        "candidate_product_labels": list(working.candidate_product_labels),
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
            for item in working.candidate_products
        ],
        "country_code": working.country_code,
        "currency": working.currency,
        "confirmed_fields": list(working.confirmed_fields),
        "missing_fields": list(working.missing_fields),
        "human_requested": working.human_requested,
        "last_intent": working.last_intent,
        "response_language": working.response_language,
        "unresolved_question": working.unresolved_question,
        "primary_goal": working.primary_goal,
        "preference_facts": [
            {
                "key": item.key,
                "value": item.value,
                "status": item.status.value,
                "source_revision": item.source_revision,
            }
            for item in working.preference_facts
        ],
        "objections": list(working.objections),
        "sales_stage": working.sales_stage,
        "next_best_action": working.next_best_action,
        "question_ledger": [
            {
                "key": item.key,
                "text": item.text,
                "status": item.status,
                "asked_revision": item.asked_revision,
            }
            for item in working.question_ledger
        ],
        "recent_response_phrases": list(working.recent_response_phrases),
        "interaction_preferences": list(working.interaction_preferences),
        "revision": working.revision,
    }


def _summary_payload(summary: Any) -> dict[str, Any]:
    return {
        "discussed_product_skus": list(summary.discussed_product_skus),
        "recent_intents": list(summary.recent_intents),
        "user_constraints": list(summary.user_constraints),
        "compact_text": summary.compact_text,
        "summarized_message_count": summary.summarized_message_count,
    }
