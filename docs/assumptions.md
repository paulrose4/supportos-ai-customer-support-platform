# MVP Architecture Decisions and Assumptions

## 1. Document Status

- Status: Accepted for MVP architecture design.
- Scope: architecture and planning only; no production business operation is enabled.
- Blocking rule: only unresolved P0/P1 risks block implementation. Other gaps use conservative defaults recorded here.
- Change control: any change weakening authentication, tenant isolation, PII minimization, deterministic risk rules, evidence requirements, or mandatory handoff requires architecture and security review.

## 2. Confirmed Architecture Decisions

### AD-001 Authentication

- Development and tests use `MockPrincipal` generated only by a dedicated development authentication adapter.
- Identity must never come from message text, request-body `user_id`, model output, or untrusted client headers.
- Production uses a verified JWT, verified by the application or a trusted API Gateway.
- Gateway identity headers are accepted only from cryptographically trusted gateway requests or explicitly trusted internal network paths. Client-supplied `X-User-Id`, `X-Tenant-Id`, and equivalents are untrusted.
- JWT parsing terminates in the authentication adapter. Domain, application services, and LangGraph receive only `AuthenticatedPrincipal`.

`AuthenticatedPrincipal` contains `subject_id`, `tenant_id`, `roles`, `scopes`, `authentication_method`, `authenticated_at`, and `correlation_id`.

### AD-002 Strict Multi-Tenancy

- MVP serves one brand but all business, conversation, audit, knowledge, conflict, handoff, agent-run, and synchronization records contain `tenant_id`.
- Repository methods require tenant context by default. Unscoped business queries are prohibited.
- Qdrant payloads contain `tenant_id`; every online retrieval applies a mandatory tenant filter assembled by trusted application code.
- The LLM cannot generate, select, or mutate tenant identity. Tenant identity comes from `AuthenticatedPrincipal` or trusted service configuration for background jobs.
- Tenant-isolation tests are mandatory and use at least two tenants with deliberately overlapping identifiers.
- Tenant self-service and cross-tenant analytics are outside MVP. The only cross-tenant retrieval exception is reviewed company knowledge stored in the reserved `__global__` partition; ordinary tenant content is never shared.

### AD-002A Tenant Knowledge Source Partitioning

- Tenant source files live under `TENANT_KNOWLEDGE_ROOT/{tenant_id}/{source_type}/`; MVP Obsidian synchronization uses the `obsidian` source type.
- A tenant synchronization never scans sibling tenant directories. Global company knowledge uses a separate privileged source root and the reserved `__global__` partition.
- Directory partitioning is an operational optimization, not the authorization boundary. Trusted job tenant, resolved directory tenant, frontmatter `tenant_id`, PostgreSQL records, and Qdrant payload must agree.
- A document mismatch within the selected tenant directory is quarantined, counted as excluded, reported with a sanitized reason, and never staged, embedded, or indexed.

### AD-003 Vault Publication Lifecycle

Knowledge follows `draft -> review -> published -> archived`.

- Only `published` documents are eligible for production retrieval.
- `draft` and `review` are never returned to the customer-support agent.
- `archived` remains in PostgreSQL for audit and history but is excluded from online retrieval.
- Saving Markdown does not publish it. Publication changes activate or deactivate the corresponding index projection.
- Ordinary FAQs may be published by an authorized editor.
- Refund, payment, privacy, legal, compensation, and account-security policies require reviewer metadata before publication.
- MVP uses frontmatter for workflow state and does not include an approval UI.

### AD-004 Knowledge Authority

Required frontmatter: `document_id`, `tenant_id`, `title`, `category`, `audience`, `product`, `region`, `language`, `status`, `authority_level`, `priority`, `version`, `effective_from`, `effective_to`, `owner_role`, `reviewer`, and `updated_at`.

Authority order:

1. `status=published`.
2. Current time is inside the effective interval.
3. Tenant matches.
4. Product, region, and audience specificity.
5. Higher `authority_level`.
6. Higher `priority`.
7. Newer policy version.
8. `updated_at` only as final tie-breaker.

Default authority levels: 100 formal legal/privacy/security policy; 80 product and after-sales SOP; 60 operations handbook; 40 FAQ; 20 internal experience/reference. Directory location supplies defaults only and cannot override validated frontmatter.

### AD-005 Material Knowledge Conflict

A material conflict exists when two or more published documents apply to the same tenant, product, region, audience, and overlapping effective period, but give incompatible conclusions, amounts, deadlines, eligibility criteria, commitments, or steps.

- Equivalent wording or duplicate conclusions are not conflicts.
- Material conflict stops automatic definitive answering and creates or updates a `KnowledgeConflict` record.
- Every material conflict causes human handoff; high-risk conflicts receive elevated routing.
- Vector similarity cannot resolve policy conflict.
- Resolution retains affected document versions, resolution, resolver, and timestamps.

### AD-006 Human Handoff

- MVP creates a PostgreSQL-backed `HandoffRequest` and linked support-queue record.
- Application code depends on `HandoffPort`; `PostgreSQLQueueHandoffAdapter` is the MVP implementation.
- Future Zendesk, Salesforce, WeCom, Feishu, or other ticket systems are adapters only.
- `HandoffRequest` includes `handoff_id`, `tenant_id`, `customer_id`, `conversation_id`, `reason_code`, `risk_level`, `summary`, `verified_evidence`, `failed_tools`, `knowledge_sources`, `created_at`, `status`, `idempotency_key`, `trace_id`, `sla_policy_version`, and optional `expected_response_at`.

### AD-007 Anonymous Access

Anonymous users may retrieve only published, public-audience knowledge that contains no customer-specific information, requires no business-data lookup, and is not covered by mandatory authentication.

Allowed examples: public product information, pricing explanation, general shipping/return policy, and usage help.

Prohibited examples: order/refund/account/ticket lookup, ticket creation or modification, member-only policy, and `internal`, `agent_only`, or `authenticated` knowledge. Audience filtering is mandatory policy and cannot be bypassed by the model.

### AD-008 Retention Defaults

Engineering defaults pending privacy/legal approval:

- Conversation/message: 90 days.
- Raw model input and complete tool result: 30 days.
- Redacted trace: 90 days.
- Handoff/support ticket: 180 days.
- Audit event: 365 days.
- Knowledge synchronization job: 180 days.
- Temporary embedding request content: not separately persisted after processing.
- Authentication tokens: never persisted.
- Database passwords/API keys: never logged.

Retention is configurable by record class. Purge is tenant-scoped, authorized, idempotent, audited, and transactionally bounded. Customer deletion requires a documented cascade/anonymization workflow. Knowledge vectors must not contain customer data.

### AD-009 PII Policy

All model calls require data minimization and redaction through `PiiRedactorPort`.

Allowed when necessary: masked order identifier, product/SKU/specification, order/logistics status, policy region, cleaned minimum question text, and non-reversible correlation ID.

Prohibited by default: passwords, tokens/JWT/API keys, complete government IDs, card/CVV/payment proof, full phone/email/address, biometrics, another customer's data, secrets, and unnecessary legal names.

Tool results are converted to minimum DTOs before entering graph state. Outbound replies receive a second PII and cross-customer disclosure check. Logs use allowlists. Prompts and models never decide PII policy.

### AD-010 Risk Levels

- Risk 0: public low-risk information; automatic answer allowed with sufficient evidence.
- Risk 1: authenticated read-only order/logistics/existing-ticket query; automatic answer allowed only after tenant, identity, and ownership verification.
- Risk 2: refund, cancellation, address change, payment dispute, compensation, privacy request, or minor-related case; agent may collect minimal information and create a pending request but cannot complete an irreversible action. Human approval is mandatory.
- Risk 3: account takeover, fraud, sensitive-data leak, legal/regulatory complaint, threat, repeated auth failure, or cross-tenant/cross-customer access; automated processing stops and routes to human/security.

Deterministic policy establishes the minimum risk. LLM classification is advisory and cannot lower it.

### AD-011 Language

- Structures support multiple languages; MVP prioritizes `zh-CN`.
- Same-language reviewed evidence is preferred.
- Cross-language answering requires `allow_translation=true` and cites the original source.
- Amounts, deadlines, legal terms, and high-risk policy conclusions cannot be model-translated and treated as authoritative.
- A high-risk policy without a reviewed target-language version causes handoff.
- MVP does not write machine translations back to the Vault.

### AD-012 Model Deployment

- `ChatModelPort` and `EmbeddingProviderPort` support external, private, region-specific, and fake providers.
- Domain, application services, and graph logic do not import provider SDKs.
- Model alias, region, timeout, retry, and data policy are configuration.
- External calls require minimum-data DTOs and PII redaction; credentials and unnecessary customer data are never sent.
- Provider replacement does not change business logic.

### AD-013 Handoff SLA

- The agent never invents a response time.
- SLA text or `expected_response_at` comes from configured or published policy returned by the handoff service.
- The record stores SLA policy and version.
- Without approved SLA, use only: “已为您转交人工处理，请留意后续通知。”
- Unverified promises such as “马上”, “很快”, or “24 小时内” are prohibited.

### AD-014 MVP Channel

- MVP exposes Web API only, initially `POST /v1/chat`.
- Input includes `conversation_id` and `message`; trusted authentication context is supplied by the authentication adapter, not accepted as user-controlled business data.
- Core input/output uses channel-neutral DTOs through `ChannelAdapterPort`; FastAPI is the first adapter.
- Domain, services, rules, and graph contain no Web-, email-, WeCom-, or platform-specific type.

### AD-015 Business Data Freshness

- Order, payment, refund eligibility, and account permission are high-impact facts.
- Any future write, eligibility decision, refund, cancellation, payment, or privacy operation must re-query the authoritative system immediately before decision/execution.
- Conversation history and graph state are not authoritative current state.
- Identity and authorization are never replaced by business caches.
- Ordinary read-only order status may use cache for at most 30 seconds; entries contain `fetched_at`, `source`, and `version` or `etag`.
- Expired, anomalous, or unavailable data produces no guess and normally causes handoff.
- Application policy decides cache eligibility. Future high-risk writes require optimistic concurrency/version checks.

### AD-016 Shared Knowledge and Hybrid Retrieval

- One Qdrant collection is used for one compatible Dense/Sparse schema; tenants are isolated by the indexed `partition_id` payload.
- Reviewed company knowledge uses the reserved `partition_id=__global__` and `tenant_id=__global__`. A tenant query searches only its own partition plus `__global__`.
- Each point stores named `dense` and `sparse` vectors. Qdrant RRF fuses those channels inside each partition; application-owned weighted fusion combines tenant and global candidates.
- Deterministic reranking applies lexical relevance before authority, priority, and scope boosts. Authority cannot make unrelated evidence eligible.
- `knowledge_index_schema_version` is part of the index namespace. Changing the collection or schema version forces an idempotent reprojection without inventing a new immutable content version.
- Tenant knowledge receives a default fusion weight of 0.65 and global knowledge 0.35. These values require retrieval-evaluation evidence before production tuning.
- Low rerank confidence, missing evidence, material conflict, or cross-tenant payload mismatch causes handoff.

## 3. Conservative Defaults

- PostgreSQL is the source of truth for business facts, publication, sync control, conflicts, audit, and handoff.
- Qdrant is a rebuildable knowledge projection and never stores customer, order, payment, authentication, or ticket facts.
- UTC is used at rest; IDs are opaque at external boundaries.
- Database constraints enforce idempotency where possible.
- External calls use bounded timeout/retry and fail closed for security, authorization, evidence, freshness, or validation failures.
- Graph checkpoints are orchestration state, not authoritative business state.
- PostgreSQL/Qdrant indexing is eventually consistent; only index-ready published versions are online.
- Model input/output is untrusted until schema and policy validation passes.
- No real secret or real customer PII is committed to the repository.

## 4. Deferred Capabilities

Real refund/cancellation/payment/compensation/address/fulfillment/privacy execution, external ticket integration, approval UI, tenant admin UI, unreviewed cross-tenant knowledge, cross-tenant analytics, translated-document publishing, additional channels, and autonomous long-term memory are outside MVP.

## 5. Production Review Gates

Named owners must approve JWT/gateway trust, final retention/deletion, PII/provider policy, risk routing, high-risk publisher/reviewer roles, handoff SLA, and model deployment region before launch.

## Support Operations MVP Decisions

- A tenant may own multiple sites. `tenant_id` and `site_id` are distinct identifiers.
- Until the trusted WordPress site registry is implemented, newly persisted MVP conversations
  use the seeded `default-site`; this is infrastructure configuration, never user/model input.
- Dashboard administration uses first-party PostgreSQL-backed opaque sessions and deterministic RBAC. Mock/disabled authentication remains available only for isolated development and tests; production rejects mock mode.
- Customer-memory writes are human-initiated in the MVP. LLM extraction may later create review
  candidates but cannot call the durable-memory write port directly.
- A support agent must take over a conversation before sending a human reply.
- Resolution is an explicit human operation. The AI graph may recommend resolution but cannot
  perform it in this phase.
- The independent Dashboard is served on local port `8090` because port `3000` is occupied on the current machine.
- MVP realtime delivery uses a tenant-isolated in-memory WebSocket hub and therefore supports one API process. Redis or NATS plus a transactional outbox is required before horizontal scaling.
- PostgreSQL remains durable truth; realtime events are invalidation/notification signals and clients reconcile through REST.




## AD-020 Signed Visitor Presence

- WordPress sends a best-effort heartbeat through its same-origin REST proxy every 20 seconds while the page is visible. Only the WordPress server adds the private site key.
- The site-key adapter supplies trusted `tenant_id` and `site_id`; neither value may come from browser JSON, visitor text, model output, or relative page data.
- Presence is keyed by `(tenant_id, site_id, visitor_id)`. Repeated heartbeats merge into the same key, preserve the first-seen time, and count path changes. The browser-controlled visitor identifier remains anonymous telemetry and is not authenticated customer identity.
- Active status uses a 45-second heartbeat window. Production presence is ephemeral Redis telemetry with a bounded TTL; it is not PostgreSQL or Qdrant business truth.
- The API request adapter supplies IP, Cloudflare country code, and user agent. A site-key-authenticated connector may forward those request facts in dedicated headers. Browser JSON cannot supply IP, tenant, or site identity; page title, referrer, language, and timezone remain untrusted display metadata.
- The write requires a valid site credential, uses an in-process atomic lock, has no database transaction, no durable audit event, and no human approval because it creates no business action. WordPress applies a separate heartbeat rate limit.
- Administrator reads require `support:inbox:read` and are always tenant-scoped. Dashboard site filtering is an additional presentation filter, not the security boundary.
- Production presence and realtime event delivery use Redis-backed adapters; in-memory adapters are limited to tests and single-process trials.

## AD-017 Single-Host Production Baseline

- The first production topology is one Linux Docker host fronted by Caddy. Only TCP 80 and TCP/UDP 443 are public; API, Dashboard, PostgreSQL, and Qdrant remain container-internal.
- The API runs exactly one worker while realtime delivery uses the in-memory tenant hub. Horizontal scaling is prohibited until a transactional outbox and Redis/NATS fan-out adapter are implemented.
- Alembic runs as a one-shot dependency before API startup. Migration failure prevents the API from starting.
- Production settings fail closed when mock authentication, mock seed data, fake model providers, insecure administrator cookies, wildcard/localhost origins, or non-HTTPS origins are configured.
- PostgreSQL uses online custom-format logical backups. Qdrant uses a short-downtime cold volume backup until collection-snapshot automation and off-host storage are configured.
- PostgreSQL restore and Qdrant restore are destructive operations requiring an explicit confirmation flag, operator approval, a maintenance window, and an audit/change record.
- Hostname, DNS, ACME email, deployment host, tenant identifier, provider API key, WordPress site keys, retention policy, and off-host backup destination remain external inputs. Their absence does not block implementation or local validation of the deployment baseline.

## AD-018 OpenAI Adapter Boundary

- `OpenAIChatModelAdapter` implements `ChatModelPort` through the Responses API and `OpenAIEmbeddingProvider` implements `EmbeddingProviderPort` through the Embeddings API.
- Provider SDK types remain inside `app/integrations/llm`; domain models, application DTOs, API schemas, Qdrant payloads, and graph state do not import or expose them.
- API keys are runtime secrets represented as `SecretStr`, never logged or committed. Adapter calls use bounded timeouts and SDK retries; failures propagate to existing fail-closed graph/handoff paths.
- Model names and embedding dimensions are configuration. A dimension or embedding-model change requires a new Qdrant index schema/collection projection before traffic switches.

## AD-019 Administrator Credential Hardening

- Administrator login failures are persisted by a one-way trusted-source fingerprint. Raw source IP values and passwords are not stored.
- The default deterministic policy is 10 failures per source within 900 seconds, then a 900-second lockout. Locked requests are read-only and return generic HTTP 429 responses without revealing account existence.
- Each counted failure is a distinct, non-idempotent security event; the counter mutation and audit event are transactional. Successful login transactionally clears an existing throttle before issuing a separately audited opaque session.
- Self-service password changes require a trusted active session plus the current password. Tenant/user identity is session-derived. The write uses the old password hash as an optimistic concurrency guard and atomically revokes every active session.
- Password reset for another administrator, MFA, SSO, recovery codes, and distributed edge rate limiting remain separate future capabilities; none may be simulated by LLM decisions.

## AD-021 Tenant Team Administration

- Only principals with `users:manage` may list, create, update, disable, or reset passwords for administrator users. The MVP grants this scope only to `tenant_owner`.
- User creation is idempotent by normalized tenant username and deterministic user ID. An exact retry returns the existing user; conflicting settings fail with HTTP 409.
- Role and status updates are desired-state idempotent. PostgreSQL locks tenant administrator rows, prevents removal of the last active tenant owner, and revokes target sessions when authorization changes.
- Password reset is retry-safe: submitting the already-active password is a no-op. A real reset uses the previous password hash as an optimistic guard and revokes all target sessions.
- Creation, actual profile/security changes, and actual password resets are transactional and audited. They require an authenticated tenant owner but no separate human approval because the authenticated owner is the approving operator.

## AD-022 Dynamic Site Credentials

- Tenant owners manage WordPress and static sites through the Dashboard. Static `WIDGET_SITE_KEYS` remains an optional bootstrap fallback, while `WORDPRESS_SITE_KEYS` is retained only for compatibility; neither is the primary production control plane.
- Site-key plaintext is generated in the administrator browser with 256 bits of randomness and shown only for immediate copy. PostgreSQL stores only SHA-256 plus an eight-character display prefix.
- Site creation is idempotent by tenant/site identifier and desired key hash. Site profile changes and key rotation are desired-state idempotent; only actual changes create audit events.
- Create, update, and rotate operations are tenant-scoped, transactional, permission-controlled by `sites:manage`, audited, and initiated by the authenticated tenant owner. Key rotation intentionally invalidates the previous key immediately and therefore requires an explicit UI confirmation.
- Disabling a site immediately blocks database-backed Widget authentication without deleting conversations, messages, audit history, or knowledge metadata.

## AD-023 Browser Security And Operational Status

- Cookie-authenticated unsafe HTTP methods require an exact trusted `Origin`; cross-site Fetch Metadata is rejected. Server-to-server WordPress calls without the administrator cookie remain outside this CSRF rule.
- Cookie-authenticated WebSockets require an exact trusted Origin before session authentication. Production configuration cannot disable browser-origin enforcement.
- API, Caddy, and Dashboard nginx emit frame, MIME-sniffing, referrer, permissions, CSP, and sensitive-response cache controls.
- In-process request metrics expose aggregate counts, 5xx rate, and latency only to administrators with `audit:read`. No request body, credential, site key, customer message, or PII enters metric labels.
- Runtime status explicitly reports that in-memory WebSocket and presence backends are not horizontally scalable. One API process remains mandatory until shared Redis/NATS adapters exist.

## AD-024 Governance Operations

- Tenant-scoped audit logs are queryable only with `audit:read`, use cursor pagination, and recursively redact sensitive fields before they cross the application boundary.
- Administrators may list and selectively revoke only their own sessions. Session source information is stored as a one-way fingerprint and exposed only as a prefix. Revocation is desired-state idempotent, transactional, audited on actual change, and confirmed in the Dashboard.
- Audit reads and backup-status reads are side-effect free and do not produce recursive audit events.

## AD-025 Backup Visibility And Guarded Retention

- Backup scripts create immutable artifacts and atomically publish checksum metadata. The application mounts only the status metadata read-only; backup contents remain outside the application trust boundary.
- Retention defaults are draft implementation values, not legal approval. Production deletion remains disabled until privacy/legal owners confirm policy version, durations and legal-hold requirements.
- Retention execution is tenant-scoped, transactionally locked, idempotent by run key, and requires a trusted operator plus human approval reference. A new append-oriented audit event records policy and deletion counts.
- Automated retention never deletes `retention.executed` events, preserving replay and approval evidence.

## AD-026 Automated Launch Acceptance And WordPress Diagnostics

- Launch acceptance is non-destructive by default and never sends model prompts. Production mode requires HTTPS, session authentication, real model providers and healthy dependencies; backup freshness can be required explicitly.
- WordPress connection diagnostics validate the site key through an idempotent in-memory presence heartbeat. The key remains server-side and the diagnostic consumes no model tokens.


## AD-027 Connector-Neutral Widget Delivery

- The Agent API authenticates a generic `widget_site_key`; historical `wordpress_site_key` principals remain accepted during migration. Connector type never changes tenant authorization or knowledge filters.
- `site-connectors/shared-widget` is the canonical browser asset source. WordPress and static/PHP packages contain synchronized projections checked by contract tests.
- Static sites require a same-origin server-side connector. The PHP connector holds the site key outside browser-visible assets, validates exact Origin, applies keyed-hash rate limits, validates bounded payloads, verifies upstream TLS, and returns whitelisted fields.
- No live website, DNS, tunnel, external credential, or public deployment is modified during connector development. Deployment remains a separate, explicitly reviewed final phase.

## AD-028 Public Widget SaaS Onboarding

- The default tenant onboarding path uses a public, non-secret Widget ID and a single script tag. Tenant operators do not configure Cloudflare Workers, Wrangler, PHP secrets, or browser credentials.
- PostgreSQL maps the public Widget ID to trusted tenant and site identity. Browser text, request fields, model output, URL parameters, and token payloads cannot select a tenant.
- Bootstrap requires an exact registered HTTP(S) Origin and source rate limit, then issues a 15-minute HMAC token. The token is kept in memory, contains no tenant ID or site ID, and grants only public chat, presence, and handoff capabilities.
- Public chat uses a browser-generated request ID. PostgreSQL admission is idempotent by tenant, site, and request ID and enforces the daily site limit under a row lock. Conversation persistence and handoff auditing remain owned by their existing application services.
- Public Widget endpoints do not use cookies or `Access-Control-Allow-Credentials`. Private site-key connectors remain optional for authenticated customer identity, order access, or higher-assurance deployments.
- Source rate limiting remains in process while the API is constrained to one instance. Redis or another shared limiter is required before horizontal scaling.


## AD-028 Deterministic Website Knowledge Ingestion

- Website crawling is disabled by default and is triggered only by an authenticated administrator with `knowledge:sync`; tenant identity comes from the principal and the crawl base URL comes from the tenant-scoped PostgreSQL site registry.
- An administrator may provide up to 20 explicit seed URLs for a web sync. Seed pages are crawled before sitemap pages, remain restricted to the registered site's allowed host, obey robots.txt and response limits, and use the same idempotent version/index/audit workflow.
- The MVP crawler accesses public HTTP(S) pages with bounded read-only GET requests, respects robots policy by default, blocks private/non-global network targets and cross-host redirects, and never logs in, submits forms, or changes a source website.
- HTML relevance filtering, canonicalization, product extraction, deduplication, authorization, and indexing decisions are deterministic and do not call an LLM. Raw HTML is not retained in Qdrant.
- Website documents use site-scoped Qdrant metadata and PostgreSQL control-plane records. Unchanged content is skipped; changed content is replaced using deterministic document/chunk identities. Cross-store atomicity is not assumed, so failed projections remain an evidence-unavailable/human-handoff condition.
- Live production sites are not crawled or modified until an explicit deployment-phase review approves ownership, crawl scope, rate limits, robots behavior, and activation.


## AD-029 Local Multilingual Embeddings

- Dense embeddings use the CPU-only FastEmbed adapter with `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` at 384 dimensions; GPT-5.4 remains the only paid model dependency.
- The initial model prioritizes multilingual coverage and bounded memory on the current 16 GB host. Higher-quality larger models require retrieval eval evidence and an explicit capacity review.
- Model cache writes are local, idempotent provider-managed artifacts and contain no customer text. Model caches are excluded from version control.
- Every model or dimension change creates a new Qdrant collection and index schema namespace. Existing collections remain available for rollback and are never mutated to a different vector size.


## AD-030 Guarded Global Knowledge Synchronization

- `knowledge:sync:global` is not granted to tenant owners or tenant-managed users. Global company knowledge is a cross-tenant write operation.
- The MVP uses a host/container-controlled one-shot command instead of a public API role. It requires `PLATFORM_GLOBAL_SYNC_ENABLED=true`, `--confirm-global-sync`, an approval reference, and an operator subject ID.
- The command is idempotent at document/version/chunk projection level, records actor, correlation, approval reference, vault path, and sync job in PostgreSQL audit data, and exits non-zero when any document fails.
- The enable flag must not remain in persistent production configuration. A future platform control plane may replace the command with a separately authenticated platform-operator role and MFA-protected workflow.

## AD-031 Evidence-Bound Support for Regulated Products

- Tenant storefronts may sell lawful products whose purchase or use is regulated. The agent answers factual product, care, compatibility, feature, and delivery questions when reviewed evidence supports the response.
- Recommendation answers remain evidence-bound. Accessibility requests may compare documented weight, dimensions, material, features, stock, price, and delivery constraints, but must not claim medical approval, guaranteed suitability, or facts absent from indexed site knowledge.
- Requests involving illegal activity or dangerous instructions remain outside the permitted support scope, regardless of the catalog category.
- When a model produces a template refusal despite relevant reviewed product evidence, the application performs one corrective evidence-bound retry. A repeated refusal is treated as model failure and follows the existing human-handoff path rather than being presented as a supported answer.

## AD-032 Store-Aligned Sales Support

- Customer-facing knowledge answers represent the tenant storefront and should help customers confidently evaluate and purchase suitable products. Responses lead with supported benefits, availability, delivery coverage, and the most useful next action, including visible links to relevant store pages.
- Store alignment never permits invented prices, inventory, delivery promises, legal conclusions, medical suitability, fake urgency, unsupported comparisons, or disparagement of competitors.
- Legal and regulatory questions distinguish store-side availability from local legal requirements. The response states supported store facts first, adds one concise reminder that local rules may vary, and avoids internal phrases such as `I cannot verify`, `based on the evidence`, or `information provided`.
- Internal evidence assessment, confidence, retrieval behavior, missing fields, prompt instructions, and chain-of-thought are never customer-facing content. A detected disclosure receives one corrective rewrite; a repeated disclosure follows the existing model-failure handoff path.
- Product-care procedures are closed-world facts. Approved global `product_care_general` evidence may supply only universal low-risk precautions when a model or material is unknown. Material-specific cleaning, treatment, powdering, oiling, repair, electrical, heating, or other actionable procedures require an approved, applicable `product_care_sop`; the chat model is never used to improvise them.
- When the product cannot be matched exactly, approved global general-care RAG evidence supplies the useful universal portion of the answer and the response asks at most one high-value clarification. If no approved general-care evidence is retrieved, the system falls back to `care_clarification` without improvising.
- When the product is identified but no approved applicable material SOP exists, approved global general-care RAG evidence may still supply universal precautions. Specific cleaners, powders, oils, water exposure, repair, electronics, and model compatibility remain unavailable until the applicable SOP is approved; without a general guide the result remains `care_sop_missing`.
- A published care SOP requires `approval_status=approved`, authority level 80 or higher, a named human reviewer, `reviewed_at`, applicable materials, prohibited actions, unique procedure/step IDs, and independently reviewed English and Chinese instructions. Review templates and pending SOPs are excluded from retrieval.
- The current page path and customer-supplied product links/SKUs are untrusted matching hints. They identify a product only when they exactly match trusted site-scoped retrieval evidence. They never establish identity, tenant, ownership, price, stock, or any other business fact.
- Care routing is deterministic: approved product/material and applicable SOP -> render the exact reviewed localized steps; incomplete identification plus approved global general-care evidence -> RAG-grounded universal answer plus one clarification; no approved evidence -> bounded clarification or handoff. Tenant website fragments cannot authorize procedures. Severe staining, mold, tears, self-repair, heating, electronics, motors, and wiring remain human-handoff cases. SOP-backed answers carry validated procedure IDs, step IDs, and source citations in graph state and audit data.

## AD-033 Owner-Approved US Drone Sales Guidance

- The tenant owner approved the customer-facing position that consumer drones are generally legal to purchase and own in the United States, including Texas, while flight rules may vary by location and intended use. This is stored as tenant-scoped reviewed sales guidance and is not a substitute for external legal advice.
- Answers may lead with the positive conclusion when the reviewed guidance is retrieved, then present USA-stock products and supported delivery facts. This guidance does not authorize claims about jurisdictions outside its scope.
- The system must not state `completely legal`, `100% legal`, or guarantee that no legal issue can ever occur. Such absolute claims are unverifiable across product variants, customer conduct, and changing state, county, or city rules and are automatically rewritten.
- Guidance excludes restricted flight zones, unsafe operation, privacy violations, and explicitly prohibited activity. Any future stronger or jurisdiction-specific claim requires a new tenant-scoped reviewed knowledge version with an effective date and named owner approval.

## AD-034 Intent-Gated Customer Links

- Retrieval citations and customer-visible links are separate data contracts. `citations` remain complete for grounding validation, handoff context, message metadata, and audit; `related_links` contain only HTTP pages intentionally shown to the visitor.
- Care, cleaning, storage, material explanation, and ordinary FAQ answers do not show links merely because product evidence was retrieved. An explicit request for a link may still show a matching page.
- Product recommendations may show at most three reranked product pages. Price, stock, availability, shipping, delivery, and other product-transaction questions show at most the best matching product page. An exact SKU or model identifier must match the selected evidence when one is present in the message.
- The visitor's current page and non-HTTP knowledge sources are excluded. Clarification, tool failure, validation failure, and human-handoff responses expose no related links.
- The shared Widget renders only `related_links`; it never derives links from `citations`. Infrastructure connectors validate and forward both fields without deciding intent.

## AD-035 Product-Reference Follow-Ups

- A message consisting only of a product URL, SKU, or short deictic phrase plus a reference inherits the most recent substantive user question. Assistant text never defines the inherited intent.
- URL and SKU references are matched only against trusted tenant/site Qdrant payload aliases (`canonical`, `requested`, and `final` URL/path plus structured SKU/MPN). A customer-provided reference cannot fetch arbitrary URLs, choose a tenant, or relax authorization.
- When no exact product match exists, product-page evidence is excluded from care generation so the model cannot borrow dimensions, inventory, price, shipping, or other facts from a similar item. Relevant care RAG and approved SOP evidence remain eligible.
- If the customer already supplied a URL or SKU, the response must not ask for the same reference again. A missing exact match receives one non-repetitive clarification and no procedural care guidance.

## AD-036 Layered Conversation Memory

- Raw prompt history is limited to the latest eight chat messages. Older messages are incrementally reduced into a deterministic structured summary containing discussed product SKUs and recent intents; complete messages remain in PostgreSQL under the normal retention policy.
- Working memory is stored per `(tenant_id, conversation_id)` and includes the active product SKU, pending unresolved SKU, last intent, response language, unresolved question, and revision. Anonymous memory is additionally bound to the trusted site ID and cannot cross sites or conversations.
- Memory updates occur only after the exchange is persisted. The agent trace ID is the idempotency key, the conversation row is locked during updates, and each successful update writes an audit event containing state identifiers and counts but no full conversation text.
- A pending unresolved product takes precedence over an older active product during reference resolution. Meaning questions such as `what does it mean` receive deterministic clarification containing the pending SKU and never fall back to another product selected by the model.
- Durable customer memory is loaded only for authenticated, trusted customer identity when `consent_status=granted` and the item is unexpired. Existing controlled memory APIs remain the only write path; anonymous visitor text cannot create cross-session memory or establish identity.
- Conversation summaries and durable preferences may resolve continuity and preferences but are never accepted as evidence for product facts, identity, orders, price, inventory, policy, or delivery claims.
