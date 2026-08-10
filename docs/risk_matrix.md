# MVP Risk Matrix

## 1. Severity and Release Policy

- P0 Critical: active cross-tenant disclosure, credential/payment-secret exposure, unauthorized irreversible action, or systemic loss of audit/control. Stop processing and block release.
- P1 High: plausible identity bypass, unsupported high-impact commitment, missed Risk 3 handoff, material policy conflict answered automatically, or unrecoverable source-of-truth/index corruption. Block affected capability or release until mitigated.
- P2 Medium: degraded retrieval, unnecessary handoff, delayed synchronization, bounded trace loss, or operational inefficiency without security boundary failure. Track with owner and deadline.
- P3 Low: usability or maintainability issue with no material safety impact.

Residual risk is accepted only by the named architecture/security/business owner. The LLM cannot accept risk.

## 2. Risk Register

| ID | Risk | Inherent | Preventive controls | Detection / evidence | Safe response | Residual |
|---|---|---:|---|---|---|---:|
| R-001 | Identity taken from user text/body/model/untrusted header | P0 | dedicated auth adapter; verified JWT/gateway provenance; principal-only core boundary | spoofing tests; auth-decision audit | deny private access; Risk 3 on repeated/inconsistent attempts | P2 |
| R-002 | Cross-tenant repository, cache, vector, trace, or queue access | P0 | tenant fields/compound keys; mandatory scoped APIs/filters; tenant cache keys; post-read invariants | two-tenant overlapping-ID tests; tenant mismatch alerts | discard result, stop run, immediate security handoff/incident | P1 |
| R-003 | Cross-customer object access within tenant | P0 | ownership rule using authenticated subject/customer mapping; no model ownership assertion | enumeration/IDOR tests; authorization audit | deny, minimize response, Risk 3 handoff on suspicious pattern | P2 |
| R-004 | Secrets, payment data, or sensitive PII sent to model/log/state | P0 | minimum DTOs; `PiiRedactorPort`; allowlisted logs; prohibited state fields | redaction tests; DLP-like scanners; outbound validation | block call/response, redact, alert; security handoff if disclosed | P1 |
| R-005 | Prompt injection changes tools, filters, policy, or identity | P1 | documents/messages treated as data; deterministic routing and tool allowlist; trusted filter construction | adversarial message/Vault evals; tool-argument audit | ignore instruction; continue safe route or handoff | P2 |
| R-006 | LLM fabricates order status, refund, amount, deadline, eligibility, or SLA | P1 | claim-to-evidence map; deterministic validator; approved SLA source; no mutation tools | unsupported-claim and commitment evals | one constrained repair, then handoff | P2 |
| R-007 | Material published knowledge conflict answered automatically | P1 | conflict rule; authority/effective metadata; conflict record; no vector-score resolution | conflict fixtures/evals; conflict-rate metrics | create/update conflict and handoff | P2 |
| R-008 | Draft/review/archived/expired knowledge becomes retrievable | P1 | publication/effective filters; index activation state; post-retrieval invariant | lifecycle integration tests; reconciliation | discard evidence; prevent answer; repair index | P2 |
| R-009 | High-risk policy published without reviewer or reviewed target language | P1 | frontmatter schema; category/reviewer/language rules; publication service gate | publication tests and audit report | reject publication or handoff at query time | P2 |
| R-010 | PostgreSQL and Qdrant drift returns obsolete or missing evidence | P1 | outbox/index manifest; deterministic points; active-after-verify; reconciliation/rebuild | count/hash reconciliation; retrieval freshness metrics | mark unavailable, exclude stale version, handoff when needed | P2 |
| R-011 | Business tool/cache provides stale or unverifiable fact | P1 | freshness policy; max 30-second Risk 1 cache; source/version metadata; fresh read for Risk 2 | stale-cache/source-outage tests; age metrics | do not claim current status; retry safely or handoff | P2 |
| R-012 | Tool/provider/database failure is hidden and model continues | P1 | typed failure states; deterministic evidence evaluation; fail-closed edges | fault-injection graph tests; error-rate alerts | bounded retry if safe, otherwise handoff | P2 |
| R-013 | LangGraph replay duplicates handoff, message, conflict, or audit side effect | P1 | deterministic idempotency keys; unique constraints; idempotent adapters | replay/resume tests; duplicate-key metrics | return existing result and continue safely | P3 |
| R-014 | Risk 2 action executes without human approval | P0 | no irreversible tool registered; pending-request-only policy; deterministic risk floor | tool registry architecture test; end-to-end Risk 2 eval | block request, create handoff, security alert on attempted bypass | P3 |
| R-015 | Risk 3 case receives normal automated handling | P1 | deterministic account takeover/fraud/leak/legal/threat/cross-tenant rules | high-risk recall eval; production handoff metrics | stop automation, minimal response, security/human queue | P2 |
| R-016 | Anonymous user accesses authenticated/internal knowledge or business data | P1 | anonymous principal; mandatory audience/auth filters; business tools require authenticated principal | anonymous leakage evals; retriever/tool contract tests | refuse private lookup; request authentication or handoff | P3 |
| R-017 | Cross-language model translation changes high-risk terms, amount, or deadline | P1 | `allow_translation`; same-language preference; reviewed-language requirement | multilingual policy evals | no authoritative translation; handoff | P2 |
| R-018 | Vault path traversal, unsafe symlink, oversized/malformed content, or poisoned metadata | P1 | rooted reader; path/symlink/size checks; allowlisted schema; configured tenant match | ingestion security tests; rejected-document audit | quarantine version; do not index; alert knowledge owner | P2 |
| R-019 | Retention/purge deletes wrong tenant or required audit data | P1 | tenant-scoped purge; policy version; dry-run/count checks; bounded transactions; approval | purge integration tests; deletion audit/reconciliation | stop job, rollback current batch, incident review | P2 |
| R-020 | Retention exceeds policy or raw prompts accumulate | P1 | configurable TTL; purge scheduler; raw capture disabled by default | age/backlog dashboards; retention tests | stop capture, prioritize purge, notify privacy owner | P2 |
| R-021 | Mock authentication or mock data path enabled in production | P0 | environment guard; composition-root allowlist; startup failure | deployment/config tests; runtime mode metric | refuse startup | P3 |
| R-022 | Concrete SDK/framework leaks into domain/application and prevents safe substitution | P2 | dependency rules; explicit ports/mappers; composition root | architecture import tests | block merge/release until dependency removed | P3 |
| R-023 | Handoff creation fails and customer believes case is queued | P1 | transactional queue adapter; returned handoff ID required; health monitoring | fault injection; queue creation metrics | state that transfer could not be confirmed, provide safe fallback/correlation ID, alert operations | P2 |
| R-024 | Agent invents response SLA | P1 | SLA service/policy version; prohibited-language validator; fixed no-SLA template | SLA wording evals | reject draft and use approved fallback | P3 |
| R-025 | Audit persistence fails during high-risk processing | P1 | mandatory audit contract; transactional/outbox strategy; append-oriented store | audit-write fault tests; missing-event reconciliation | block high-risk completion and alert operations | P2 |

## 3. Key Risk Indicators

Track per tenant and release version:

- cross-tenant invariant failures: target zero;
- unauthorized tool attempts: target zero successful, all attempts audited;
- unsupported business-claim rate;
- PII redaction/validation failure rate;
- Risk 2/3 missed-handoff rate;
- material conflict auto-answer rate: target zero;
- draft/review/archive retrieval rate: target zero;
- stale business-fact answer rate: target zero;
- handoff creation success and duplicate rate;
- knowledge sync/index reconciliation backlog;
- raw trace retention backlog;
- model/tool timeout and malformed-result rate;
- anonymous private-data leakage rate: target zero.

## 4. Mandatory Eval Categories

- Authentication and identity spoofing.
- Tenant and customer isolation across SQL, Qdrant, cache, graph, queue, audit, and purge.
- Publication, effective date, authority, audience, and language filtering.
- Harmless duplicate versus material conflict.
- Retrieval recall/precision and evidence sufficiency.
- Groundedness and claim-to-evidence correctness.
- PII minimization, prompt/log/state leakage, and outbound disclosure.
- Risk 0-3 classification with emphasis on Risk 2/3 recall.
- Tool/provider/database failure and stale-data behavior.
- Graph retry/checkpoint/replay idempotency.
- Handoff routing, context completeness, SLA correctness, and adapter failure.
- Architecture dependency and prohibited-tool registration.

## 5. Current Blocking Assessment

No unresolved P0/P1 architecture gap was found in the confirmed decisions. Production deployment remains gated on implementation evidence and approval of JWT/gateway trust, retention/legal-hold, provider data policy, PII classification, reviewer roles, and human/security queue ownership.