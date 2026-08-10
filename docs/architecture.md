# Customer Support Agent Architecture

## 1. Architectural Goal

Build an evidence-grounded, deterministic-policy-controlled, auditable customer-support agent. LangGraph orchestrates work but does not own authorization, tenant isolation, business truth, publication policy, or irreversible decisions. When a product or material is unknown, low-risk care answers come only from approved global `product_care_general` RAG evidence and end with at most one useful clarification. Material- and model-specific treatment still requires an approved, applicable `product_care_sop`. Knowledge readiness counts full care coverage only when at least one approved, active global SOP exists; ordinary website care or guide pages do not satisfy that gate. Store, product, price, stock, delivery, legal, medical, and business claims always require trusted evidence. Other low-confidence, insufficient-evidence, conflict, stale-data, tool-failure, untrusted-identity, validation-failure, and Risk 2/3 conditions lead to clarification or human handoff rather than fabrication.

## 2. System Context

Primary flows:

1. A trusted channel adapter creates a channel-neutral request and authenticated principal.
2. The application service opens or loads a tenant-scoped conversation and invokes the graph.
3. The graph classifies and deterministically intercepts all order-related requests for human handling before any model or business tool. Other eligible requests retrieve knowledge or query the read-only support-ticket service, apply deterministic rules, draft, validate, persist trace, and respond or hand off.
4. A separate synchronization workflow scans the Obsidian Vault, validates frontmatter, records document versions, chunks published knowledge, embeds it, and projects it into Qdrant.
5. PostgreSQL remains authoritative; Qdrant remains rebuildable.

## 3. PostgreSQL and Qdrant Boundary

### 3.1 PostgreSQL Responsibilities

PostgreSQL stores authoritative and control-plane data:

- `customers`, `orders`, `order_items`, and `support_tickets` for MVP mock business data.
- `conversations`, `messages`, `agent_runs`, `node_executions`, and `tool_executions`.
- `audit_events`, `handoff_requests`, and queue/SLA state.
- `knowledge_documents`, immutable `knowledge_document_versions`, internal links, chunk manifests, sync jobs, index operations, and publication state.
- `knowledge_conflicts`, affected versions, resolution history, resolver identity, and status.
- idempotency records, outbox events, retention/purge jobs, prompt/rule/model/chunker/embedding version references.

All tenant-owned tables contain `tenant_id`. Uniqueness and foreign-key strategies include tenant context; repository APIs do not expose unscoped lookup methods to ordinary application flows.

### 3.2 Qdrant Responsibilities

Qdrant stores only the knowledge retrieval projection:

- named Dense and Sparse vectors plus bounded chunk text;
- `partition_id`, where an ordinary tenant uses its trusted `tenant_id` and reviewed shared company knowledge uses the reserved `__global__` partition;
- `tenant_id`, `document_id`, `document_version_id`, `chunk_id`, deterministic point ID;
- source path, title breadcrumb, sequence, content hash;
- category, audience, product, region, language, translation permission;
- publication/effective metadata, authority level, priority, policy version, risk category;
- embedding/chunker version and index generation.

Online retrieval searches exactly two partitions: the authenticated tenant and `__global__`. Each partition performs Dense/Sparse RRF fusion, then deterministic weighted fusion and reranking apply relevance, authority, priority, and scope policy. Other tenant partitions are never queried. Trusted filters also enforce publication eligibility, effective interval, audience, language policy, and applicable product/region where known.

Qdrant never stores customer, order, payment, refund, account, authentication, ticket, conversation, handoff, or audit facts.

### 3.3 Consistency Model

No distributed transaction spans PostgreSQL and Qdrant.

Knowledge version state progresses through `discovered`, `parsed`, `validated`, `chunked`, `embedding_pending`, `indexing`, `indexed`, `active`, with `failed`, `superseded`, and `archived` terminal/side states.

- PostgreSQL transaction commits the immutable document version and an outbox/index operation.
- A worker performs deterministic Qdrant upserts using `tenant_id + document_version_id + chunk_id`-derived IDs.
- The version becomes `active` only after all expected points and required payload indexes are verified.
- Publication removal first makes the version ineligible in PostgreSQL/application policy, then removes or deactivates Qdrant points asynchronously.
- Reconciliation compares PostgreSQL manifests with Qdrant point metadata and repairs drift.

### 3.4 Tenant Experience Learning Boundary

Tenant experience remains in PostgreSQL. `experience_events` and issue-level
`issue_outcome_episodes` provide the auditable result ledger; redacted
`tenant_case_memories` support tenant-local similarity search through pgvector; and
`tenant_pattern_memories` aggregate repeated cases only within one tenant and site. Qdrant
continues to contain knowledge only.

The graph resolves same-conversation references before deterministic minimum-risk assessment.
Eligible low-risk branches then retrieve at most one negative and one positive tenant experience.
Experience is untrusted advisory input for clarification, retrieval planning, caution, and handoff
consideration. It never enters the knowledge evidence bundle, establishes a business fact, lowers
risk, or changes a production Prompt, policy, SOP, rule, or ranking weight.

The online context uses type quotas rather than mandatory slots: up to four recent messages, one
global conversation summary, one relevant earlier summary segment, one negative case, and one
positive case. Empty or irrelevant slots remain empty. Inactivity is censored rather than inferred
as success, and delayed CSAT, reopen, and human-resolution events can revise an outcome.

Case memories begin in `shadow`. Promotion to `operational_active` requires a completed evaluation
run, all deterministic release gates, and an explicit tenant approval. Pattern-derived improvement
candidates begin as drafts and must pass tenant review and the existing offline publication gates.
Releases have stable tenant/conversation cohorts and can be rolled back independently of knowledge.

Customer resolution and AI learning value are separate outcome dimensions. A positive CSAT can
confirm that the customer was satisfied without erasing a stronger human finding that the AI made
an avoidable handoff. Delayed signals merge through deterministic source precedence and record
conflicts rather than using last-write-wins. Experience usage records distinguish retrieval,
eligibility, selection, actual influence, and outcome attribution.

Every active release owns an immutable manifest of memory IDs, exclusions, eligible sites and
intents, maximum risk, and evaluation dataset version. The online adapter enforces that manifest in
addition to the application service. Repeated cases from one privacy-preserving actor cohort count
once toward pattern formation. Online treatment/control guardrails can quarantine one harmful case
or pause a release automatically; neither action changes authoritative knowledge or policy.

## 4. Layered Architecture

### 4.1 Dependency Direction

`Domain <- Application <- Agent Orchestration`

`Domain <- Application <- Infrastructure Adapters`

`Domain <- Application <- Interface Adapters`

`Composition Root -> all concrete implementations`

Rules:

- Domain imports no FastAPI, LangGraph, SQLAlchemy, Alembic, Qdrant, Pydantic API schema, or model-provider SDK.
- Application imports domain types and declares ports/DTOs; it imports no concrete adapter.
- Agent orchestration adapts graph state to application commands/results and calls application services/ports; nodes do not open SQLAlchemy sessions or instantiate SDK clients.
- Infrastructure implements ports and owns ORM models, Qdrant payloads, SDK responses, sessions, transactions, and provider error translation.
- Interface adapters own FastAPI schemas, HTTP status mapping, and trusted-auth extraction.
- Composition root is the only place that selects concrete adapters, configuration, graph builder, and lifespan resources.

### 4.2 Model Separation

The following are separate types with explicit mappers:

- Domain models: invariants and business meaning.
- Application DTOs: use-case input/output.
- API schemas: HTTP validation and representation.
- ORM models: relational persistence.
- Qdrant payload schemas: retrieval projection.
- LangGraph state schemas: serializable workflow state.
- Provider schemas: model/embedding/tool SDK contracts.

No type is reused merely to reduce mapping code.

## 5. Domain and Application Surface

### 5.1 Domain Models

`AuthenticatedPrincipal`, `Customer`, `Order`, `SupportTicket`, `Conversation`, `Message`, `AgentRun`, `ToolExecution`, `AuditEvent`, `HandoffRequest`, `KnowledgeDocument`, `KnowledgeDocumentVersion`, `KnowledgeChunkManifest`, `KnowledgeSyncJob`, `KnowledgeConflict`, `EvidenceBundle`, `RuleDecision`, and `ResponseValidationResult`.

### 5.2 Ports

Authentication and channel:

- `AuthenticationPort`
- `ChannelAdapterPort`

Persistence and transactions:

- tenant-scoped customer/order/ticket/conversation/run/audit/knowledge/conflict/handoff repositories
- `UnitOfWorkPort`
- `OutboxPort`

Knowledge and models:

- `VaultReaderPort`
- `MarkdownParserPort`
- `ChunkerPort`
- `EmbeddingProviderPort`
- `KnowledgeVectorIndexPort`
- `KnowledgeRetrieverPort`
- `ChatModelPort`
- optional `RerankerPort`

Security and operations:

- `PiiRedactorPort`
- `AuthorizationPolicyPort`
- `RiskPolicyPort`
- `ClockPort`
- `IdGeneratorPort`
- `TelemetryPort`
- `HandoffPort`
- `SlaPolicyPort`
- `CachePort`

### 5.3 Services

- `HandleCustomerMessageService`
- `AuthenticatedContextService`
- `KnowledgeSyncService`
- `KnowledgePublicationService`
- `KnowledgeIndexingService`
- `KnowledgeRetrievalService`
- `KnowledgeConflictService`
- `BusinessDataQueryService`
- `EvidenceEvaluationService`
- `RiskAssessmentService`
- `ResponseValidationService`
- `HumanHandoffService`
- `RetentionPurgeService`
- `AuditService`

### 5.4 Deterministic Rules

- identity trust, authentication requirement, scope, tenant, customer ownership;
- publication/effective interval/audience/language/translation eligibility;
- knowledge authority, specificity, sufficiency, and material conflict;
- data freshness/cache eligibility and source health;
- Risk 0-3 minimum classification;
- refund/cancellation/payment/compensation/privacy/minor/account-takeover/fraud/legal/threat rules;
- response grounding, citation, PII, cross-customer disclosure, prohibited commitment, and SLA wording.

### 5.5 MVP Tools

Read-only tools: existing support-ticket status and knowledge search. Order, logistics, refund, cancellation, payment, address, and fulfillment tools are not registered in the agent path; those requests use the human-only order policy.

Controlled writes: create idempotent pending request/handoff and persist conversation/trace/audit. No refund, cancellation, address, payment, compensation, privacy execution, or other irreversible tool is registered.

## 6. LangGraph State Boundary

State contains JSON-safe, bounded, versioned data only:

- `state_schema_version`
- `request`: request/correlation IDs, channel, locale, normalized text
- `principal`: serialized minimum `AuthenticatedPrincipal` without token
- `conversation`: tenant/conversation/customer references and bounded summary
- `conversation_memory`: current product, unresolved product, last intent, language, unresolved question, and revision
- `conversation_summary`: bounded deterministic summary of messages older than the eight-turn raw context window
- `durable_memories`: consented customer preferences loaded only for a trusted non-anonymous customer identity
- `memory_usage`: bounded IDs of durable memories selected for the current grounded answer; used only for internal usage auditing
- `risk`: deterministic minimum, advisory signals, reason codes
- `intent`: structured classification, confidence, alternatives
- `entities`: candidate order/product/region identifiers with provenance
- `route`: selected route and reason codes
- `knowledge_query`: trusted filters and query variants
- `knowledge_evidence`: bounded citations, scores, authority metadata, effective dates, conflict references
- `citations`: internal grounding and audit references; never interpreted as customer-visible links
- `related_links`: bounded customer-visible HTTP links selected by deterministic intent rules
- `business_evidence`: minimum DTOs with `fetched_at`, `source`, and `version/etag`
- `tool_executions`: status/error summaries, never complete SDK objects
- `evidence_evaluation`: sufficient/insufficient/conflicting/stale/failed
- `rule_decision`: allow/deny/clarify/handoff, prohibited claims, rule-set version
- `draft`: bounded response and claim-to-evidence map
- `validation`: grounding/PII/tenant/SLA/safety results
- `handoff`: reason, queue, idempotency key, record ID, approved SLA text
- `trace`: run/node IDs and persisted markers
- `errors`: sanitized codes and retryability

State must not contain JWTs, secrets, SQLAlchemy sessions/ORM objects, domain aggregates, SDK responses, exception objects, file handles, full Vault documents, unbounded history, or authoritative mutable business state. Checkpoints support orchestration recovery only. Fresh high-risk facts are reloaded through application services.

## 7. Graph Nodes and Failure Branches

| Node | Input | Output | LLM | Fail-closed branch |
|---|---|---|---|---|
| `normalize_input` | channel request | cleaned text, locale, request metadata | No | reject invalid/oversized input |
| `load_authenticated_context` | trusted adapter result | principal and auth status | No | public-only route or handoff |
| `detect_security_signals` | text and auth events | injection/PII/account-takeover signals | Optional advisory | Risk 3 route when deterministic signal matches |
| `classify_intent` | minimum text/context | intent, confidence, advisory risk | Yes, structured | low confidence -> clarify |
| `resolve_entities` | text, intent, principal | candidate entities with provenance | Optional | ambiguity -> clarify; never infer ownership |
| `assess_risk` | intent, auth, entities, signals | deterministic Risk 0-3 | No | Risk 2/3 -> pending request/handoff |
| `choose_route` | risk, auth, intent, missing fields | public knowledge/business/mixed/clarify/handoff | No | unknown route -> handoff |
| `retrieve_knowledge` | trusted tenant/audience/language filters | bounded knowledge evidence | Embedding; optional rewrite/rerank | unavailable/empty -> evaluate as failed/insufficient |
| `query_business_data` | tenant, principal, owned entity | fresh minimum business DTO | No | unauthorized/not found/stale/failure -> clarify or handoff |
| `evaluate_evidence` | all evidence/tool statuses | sufficient/general-guidance/conflicting/stale/failed | Optional advisory only | insufficient claims -> clarify/handoff; care instructions require approved SOP |
| `apply_business_rules` | evidence, risk, auth, freshness | deterministic decision and allowed claims | No | deny or handoff |
| `evaluate_care_policy` | question, trusted page/product evidence, published SOP metadata | product/material match, risk tier, approved procedure/step IDs, clarification or handoff | No | unidentified product -> bounded clarification; identified product without approved SOP or dangerous repair/electronic case -> handoff |
| `ask_clarifying_question` | missing safe fields | one minimum question | Template preferred | unsafe/unresolvable -> handoff |
| `generate_draft` | approved evidence/claims | draft plus claim map | Yes | provider/schema failure -> handoff |
| `validate_response` | draft, evidence, policy | validation result | Deterministic required; optional judge | one repair maximum, then handoff |
| `prepare_handoff` | reasons/evidence/failures | redacted handoff command | Optional summarization after redaction | fixed minimum summary |
| `create_handoff` | idempotent command | handoff ID, queue, approved SLA | No | persistence failure -> safe fallback and operational alert |
| `persist_trace` | bounded run result | persisted markers | No | high-risk response blocked if mandatory audit fails |
| `respond` | validated answer/clarification/handoff | channel-neutral result | No | generic safe error with correlation ID |

## 8. Edges and Mandatory Handoff Paths

`START -> normalize_input -> load_authenticated_context -> detect_security_signals -> classify_intent -> resolve_entities -> assess_risk -> choose_route`

Route branches:

- Risk 3 -> `prepare_handoff -> create_handoff -> persist_trace -> respond -> END`.
- Risk 2 -> collect only permitted missing information, then create pending request/handoff; never execute the requested action.
- Anonymous private/business request -> safe authentication requirement or handoff; no business tool call.
- Public knowledge -> `retrieve_knowledge`.
- Authenticated read-only business -> `query_business_data`.
- Mixed request -> retrieve knowledge and business data, initially sequential for correctness; parallelism may be added only with disjoint state updates.
- Missing safe entity -> `ask_clarifying_question`.
- A URL- or SKU-only follow-up deterministically inherits the most recent user question. The reference becomes a tenant/site-scoped exact retrieval filter and never selects tenant identity or bypasses authorization.
- Pronouns and short references bind to structured working memory before any model call. A pending unresolved SKU takes precedence over an older active product; ambiguous meaning questions return a deterministic clarification rather than allowing model inference.

Evidence branches:

- Material conflict -> persist `KnowledgeConflict` -> handoff.
- Insufficient/low-confidence evidence -> one safe clarification when it can materially resolve the gap, then handoff. Product-care steps are never generated without an approved applicable SOP.
- General-guidance mode -> no invented store/product facts; available tenant evidence may support internal citations, while customer-visible links are emitted only for explicit link, recommendation, or product-transaction intent. Deterministic response validation removes or rejects internal reasoning and unsupported claims.
- Actionable care mode -> current page/link/SKU matching trusted site evidence supplies product identity and material. Only an approved, applicable published SOP may supply care steps, which are rendered exactly. An identified product without such an SOP is handed to a human; no model fallback invents procedures.
- Unmatched care mode -> unrelated product pages are removed from the model context. The customer receives one product-link/model clarification plus bounded preservation precautions, not cleaning, powdering, oiling, repair, electrical, heating, or other procedural instructions.
- Care response validation -> every emitted procedure ID and step ID must be present in the retrieved SOP payload, every citation must match retrieved evidence, and high-risk care categories always terminate in human handoff.
- Tool/provider failure, stale source, ownership mismatch, tenant mismatch, invalid effective date, missing reviewed target-language high-risk policy -> handoff.
- Sufficient evidence -> deterministic rules -> draft -> validation.
- Repairable draft -> one constrained repair -> revalidation.
- Unsupported claim, PII leak, cross-customer data, prohibited SLA/commitment, or second validation failure -> handoff.

Side-effect nodes use idempotency keys because graph retry/resume may repeat execution.

## 9. Write Operation Policy

All writes define idempotency, authorization, audit, transaction, and approval:

- Conversation/message/run/trace: unique external message or run keys; tenant-scoped service authorization; audited metadata; short PostgreSQL transaction; no human approval.
- Handoff/pending request: key derived from tenant, conversation, triggering run, and reason; agent-service permission; full reason/evidence audit; handoff and queue state in one transaction; human approval required for any subsequent Risk 2 action.
- Knowledge version/sync job: content hash and batch key; knowledge-worker role; parser/version audit; immutable version transaction; high-risk publication requires reviewer metadata.
- Qdrant upsert/deactivation: deterministic point/index-operation ID; service account; PostgreSQL operation audit; eventual consistency, not cross-database transaction; large destructive rebuild requires operator approval.
- `KnowledgeConflict`: deterministic conflict fingerprint; knowledge service permission; affected versions and resolution history; creation in bounded transaction; resolution requires authorized reviewer.
- Purge: tenant plus policy/run key; privacy/admin permission; deletion counts and policy version audited; bounded batches; production customer deletion requires approved workflow.
- Real business mutation: absent from MVP. Future operations require fresh authoritative read, optimistic version check, explicit permission, audit, transaction, and human approval according to Risk 2/3 policy.

## 10. Decision Impact Matrix

| Decision | Schema impact | Ports/services | Rules/tools | Required evals |
|---|---|---|---|---|
| Authentication | `AuthenticatedPrincipal`, auth metadata | `AuthenticationPort`, context service | identity trust; no identity tool from text | spoofed body/header/model identity |
| Multi-tenancy | `tenant_id` everywhere, tenant compound keys | tenant-scoped repositories/retriever | tenant filter/ownership | cross-tenant retrieval/query/write |
| Publication lifecycle | status/reviewer/version/index readiness | publication/index services | publication eligibility | draft/review/archive exclusion |
| Authority | full frontmatter and authority projection | parser/retrieval/evidence services | authority/specificity ordering | authority precedence and effective dates |
| Conflict | conflict and resolution schemas | conflict repository/service | conflict detection; handoff | harmless duplicate vs material conflict |
| Handoff | handoff/queue/SLA schemas | `HandoffPort`, handoff service | create-handoff tool | idempotency, routing, context completeness |
| Anonymous scope | audience/auth requirements | retrieval/context services | audience and auth rules | anonymous leakage and filter bypass |
| Retention | retention class/purge job/audit | purge service/clock/audit | deletion policy | expiry, legal-hold placeholder, tenant purge |
| PII | redaction result/minimum DTO | `PiiRedactorPort` | inbound/outbound leak rules | prompt/log/response leakage |
| Risk levels | risk/reason/pending request | risk service/policy port | Risk 0-3 rules; no mutation tools | high-risk recall and over/under-handoff |
| Language | language/translation metadata | retriever/translation policy | same-language/high-risk rule | language filter and prohibited translation |
| Model deployment | provider config/result metadata | chat/embedding ports | timeout/data policy | fake/contract/provider failure |
| Handoff SLA | SLA policy/version/time | `SlaPolicyPort`, handoff service | prohibited promise rule | absent/expired/mismatched SLA |
| Web API | API schemas only | `ChannelAdapterPort` | no channel-specific domain tool | API contract and auth boundary |
| Freshness | fetched/source/version/etag | cache/business query services | freshness and optimistic concurrency | stale cache/source outage/requery |

## 11. Architecture Tests

Automated tests reject:

- framework/SDK imports in domain or application;
- infrastructure imports from domain/application;
- API schema reused as application DTO;
- ORM/Qdrant/provider objects crossing their adapters;
- graph nodes opening database sessions or constructing concrete clients;
- repository methods that omit tenant context except explicitly audited system-maintenance APIs;
- retriever calls without tenant and audience policy filters;
- registered irreversible business tools in MVP;
- prompts as the sole implementation of a business/security rule;
- graph state fields containing tokens, sessions, ORM objects, or unrestricted dictionaries.

## 12. Revised Implementation Plan

### Phase 0 — Documentation and Quality Gates

Deliver: these architecture documents, ADR index, threat boundaries, dependency rules, initial eval taxonomy. Validate with Markdown/link checks and architecture review. Human review: architecture, security, knowledge owner.

### Phase 1 — Project Skeleton and Domain

Deliver: Python packaging, domain models/value objects/errors/rules, application DTOs/ports, fakes, architecture tests. Validate: `ruff check .`, `ruff format --check .`, `pytest tests/unit/domain tests/unit/rules tests/architecture -q`. Review: framework isolation and risk rules.

### Phase 2 — PostgreSQL Foundation

Deliver: tenant-scoped ORM schemas, mappers, repositories, unit of work, audit/idempotency/outbox, migrations, two-tenant fixtures. Validate: `docker compose up -d postgres`, `alembic upgrade head`, repository/integration tests, downgrade/upgrade smoke test. Review: constraints, indexes, PII, deletion, transaction scope.

### Phase 3 — Vault Governance and Parsing

Deliver: Vault reader, frontmatter validation, lifecycle/authority/effective-date rules, links, immutable versions, conflicts, sync jobs. Validate: parser/property tests and Vault integration tests. Review: publication/reviewer policy and conflict definition.

### Phase 4 — Qdrant Projection and Retrieval

Deliver: embedding/vector adapters, deterministic points, payload indexes, mandatory tenant/audience/language filters, activation/deactivation, reconciliation/rebuild. Validate: Qdrant integration and retrieval evals. Review: payload privacy and filter enforcement.

### Phase 5 — Authenticated Read-Only Business Services

Deliver: mock principal adapter, production auth port shape, tenant/ownership rules, mock customer/order/ticket repositories, freshness/cache policy, minimum tool DTOs. Validate: spoofing, cross-tenant, stale cache, source outage, contract tests. Review: trusted-auth boundary.

### Phase 6 — LangGraph Orchestration

Deliver: versioned state, nodes, conditional edges, checkpoint/replay idempotency, clarification and mandatory handoff paths using fake models. Validate: full graph path suite and repeated-side-effect tests. Review: every failure/risk path reaches a safe terminal state.

### Phase 7 — Model, Redaction, Draft Validation

Deliver: chat/embedding provider adapters, PII redaction, structured outputs, claim-to-evidence mapping, deterministic validator, one-repair limit. Validate: grounding, PII, injection, unsupported claim, provider failure evals. Review: external-provider data policy.

### Phase 8 — FastAPI and Local Runtime

Deliver: `POST /v1/chat`, schemas/mappers, dependency injection, exception handling, health/readiness, Docker Compose, no real secrets. Validate: API integration tests, migrations, full lint/test suite, `docker compose config`. Review: authentication context cannot be client-forged.

### Phase 9 — Retention, Operations, and Release Gate

Deliver: purge jobs, reconciliation, metrics, runbooks, eval report, release checklist, handoff queue operations. Validate: full `pytest -q`, lint/format, restore/rebuild drills, offline eval thresholds. Review: security/privacy/legal/customer-support sign-off before external traffic.

## 13. Stop Condition

No project skeleton or business code is created until this architecture is explicitly approved.

## 10. Support Operations Dashboard Boundary

The product consists of three independently deployable surfaces: the visitor Widget, the AI
Agent Runtime, and the Support Operations Dashboard. The dashboard calls only administrative
HTTP/WebSocket APIs. Those APIs map trusted identity and invoke application services; they do
not access SQLAlchemy sessions, repositories, Qdrant, or model clients directly.

Conversation lifecycle, AI/human ownership, assignment, handoff queue state, and ticket state
are separate concepts. PostgreSQL is authoritative for all of them. See
`docs/support-operations.md` for the state model and write-governance matrix.

## Product Snapshot Boundary

PostgreSQL is the only authority for exact product identity and structured storefront facts.
Application services resolve SKU, MPN, canonical URL, and trusted current-page path through the
`ProductCatalogPort` before semantic retrieval. An explicit product reference that has no exact
active snapshot match cannot fall back to a similar Qdrant result. Price, availability, material,
dimensions, weight, warehouse, and shipping-region answers use deterministic snapshot templates.

Qdrant stores explanatory website content and product identity filter fields only. Website
vectors are staged inactive and activated by site/version only after the complete PostgreSQL
product snapshot and all knowledge projections pass. Failed synchronization leaves the previous
complete product snapshot and Qdrant version set active.

## Dashboard, Administrator Identity, and Realtime Boundaries

- `dashboard/` is an independent React/Vite adapter. It consumes HTTP and WebSocket contracts and contains no domain or persistence logic.
- `app/api/routes/identity.py` maps login/session HTTP contracts to `AdminSessionService`; it never queries ORM models directly.
- `AdminSessionService` depends only on identity and password-hashing ports. PostgreSQL and scrypt are replaceable adapters.
- RBAC roles map to scopes through deterministic domain rules. Tenant and subject identity are derived only from the authenticated database session.
- Application services publish domain realtime events through `EventPublisherPort`; they do not import WebSocket or broker implementations.
- `InMemoryRealtimeHub` is an infrastructure adapter for the single-process MVP. PostgreSQL remains durable truth and Dashboard clients reconcile through REST.
- Infrastructure identity models, API schemas, authenticated principals, Dashboard types, and realtime payloads remain separate representations.

## Operational governance adapters

Audit querying, session revocation, backup-status reading and retention follow the same dependency direction as the rest of the system. Domain/application define `AuditLogPort`, `BackupStatusPort` and `RetentionStorePort`; PostgreSQL and filesystem adapters implement them. API routes use application services, while maintenance CLI modules are composition roots. Backup content never enters domain models or API schemas, and retention cannot be initiated by a graph node, tool or LLM output.
