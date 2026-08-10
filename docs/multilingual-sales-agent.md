# Multilingual Sales Conversation Runtime

## Purpose

The customer-facing agent combines evidence-grounded product support with bounded consultative
sales behavior. Business rules decide what may be said, the sales response plan decides how to
help, and the chat model renders the approved plan naturally.

Order, payment, refund, cancellation, address-change, and fulfillment requests remain deterministic
human handoffs. Sales planning never enables an order tool or relaxes response validation.

## Trusted Site Identity

Each published Widget configuration supplies only the support display name, customer address mode,
and first-turn introduction preference. The trusted site registry supplies the domain. These values
are projected onto `AuthenticatedPrincipal` by the Widget authentication adapter; customer text,
retrieved pages, and model output cannot select them.

The Dashboard exposes:

- `agent_name`
- `customer_address_mode`: `formal`, `neutral`, or `friendly`
- `introduce_on_first_turn`

The site default language is a fallback for low-information first messages. It does not bind the
whole conversation to one language.

## Runtime Flow

1. Resolve the trusted site identity.
2. Resolve `ConversationLanguageContext` from an explicit customer request, the current substantive
   message, stable conversation language, recent user history, then the site fallback.
3. Resolve product references from bounded recent history and structured working memory.
4. Apply deterministic order and risk routing.
5. Retrieve product and policy evidence.
6. Build `AnswerPlan`, `SalesResponsePlan`, and the auditable `ResponseBrief` communication
   contract.
7. Render exactly one final response from the approved brief, identity, language context, and
   bounded evidence packet.
8. Validate evidence, domain, language, repeated questions, repeated introductions, sensitive data,
   and prohibited promises.
9. Persist the exchange, `ResponseBrief`, and updated working-memory JSON with an audit event.

The production graph does not cache customer-facing prose. Retrieval results, product snapshots,
and approved fact packets may be cached behind their ports, but every conversational turn is
rendered against its current context. When the graph renderer is configured, the knowledge service
runs in fact-packet mode so there is no hidden first model draft followed by a second rewrite.

## Sales Memory

`ConversationWorkingMemory` stores bounded, source-aware sales context:

- a primary goal and current sales stage;
- confirmed preference facts with their source revision;
- objections;
- a question ledger;
- the next best action;
- recent response phrases used to reduce repetition.

Only deterministic extraction from customer text creates a `confirmed` preference. Model inference
must not be persisted as a confirmed fact. Existing memory rows remain compatible because every new
JSON field has a conservative default and no relational migration is required.

## Sales Response Plan

The plan is structured application data. It includes the intent, stage, confirmed preferences,
primary objection, evidence requirements, up to three product identifiers, one optional follow-up,
the next action, language/formality controls, recent phrases to avoid, prohibited claims, and an
ordered set of response moves.

Response moves are claim-free communication strategies. For example, a trust objection asks the
renderer to acknowledge the specific concern and offer only verifiable trust evidence. The move
itself cannot assert company age, sales volume, security, legality, stock, delivery, or a discount.

## Response Brief And Natural Rendering

`ResponseBrief` separates deterministic communication planning from probabilistic expression. It
contains the dialogue act, customer goal, direct answer seed, approved facts and sources,
uncertainty, conversation delta, optional emotion acknowledgement, one optional follow-up, an
optional useful next step, target language, response length, recent phrases to avoid, and prohibited
claims.

The renderer may vary sentence structure, density, and tone, but it cannot select authorization,
risk, evidence sufficiency, citations, handoff status, or business actions. A simple factual question
does not automatically receive a greeting, sales pitch, question, or call to action. Rendering that
adds an unapproved number or URL is rejected and the deterministic answer seed is retained.

## Multilingual Rules

- A complete current-language message can switch the response language.
- A SKU, URL, model name, or isolated foreign term cannot switch it.
- Language switching preserves products, preferences, objections, and sales stage.
- Language never proves country, region, or currency.
- Brand names, SKUs, material names, and URLs remain unchanged.
- The response uses one language-native rendering rather than translating a Chinese template.

## Operations And Evaluation

Before staged rollout, evaluate multi-turn conversations by site and language. Required release
gates include zero cross-site identity leakage, zero fabricated confirmed memory, full forced-order
handoff coverage, full evidence coverage for business claims, no prohibited promises, and stable
memory across language switches. Human native-language review should cover naturalness, relevance,
and the clarity of the next action.

Run `python -m evals.run_conversation_experience_gate` before staged rollout. The deterministic
multi-turn gate measures direct-answer placement, context continuity, unnecessary follow-ups and
calls to action, repeated sentences, correction handling, handoff context, and tone violations.
Native-speaker blind review remains required because deterministic metrics cannot fully measure
human preference.

### Shadow And Blind Review

Shadow evaluation must never send the candidate response to the customer. Export only redacted
conversation turns, the deterministic response brief, the delivered baseline response, and the
candidate response to an access-controlled JSONL artifact. Do not export tenant secrets, private
business objects, raw prompts, or customer contact details.

Build a randomized review pack and separate answer key:

```bash
python -m evals.build_blind_review_pack \
  --input evals/results/shadow_candidates.jsonl \
  --output evals/results/blind_review.jsonl \
  --answer-key evals/results/blind_review.key.jsonl
```

Reviewers select `a`, `b`, or `tie` and score naturalness and helpfulness without seeing which option
is the candidate. Summarize the completed review with:

```bash
python -m evals.summarize_blind_review \
  --review evals/results/blind_review.completed.jsonl \
  --answer-key evals/results/blind_review.key.jsonl \
  --minimum-win-rate 0.60
```

Require at least 100 reviewed turns for each launch language and the major scenarios: factual FAQ,
recommendation, comparison, objection handling, clarification, correction, and handoff. No language
or site advances when safety gates regress, renderer fallback exceeds 2%, quality repair exceeds
10%, or the candidate loses to the delivered baseline. Fine-tuning starts only after there are at
least 1,000 redacted, human-approved turns with stable reviewer agreement and no unresolved safety
or grounding defects.

The recommended rollout is shadow evaluation, then 5%, 25%, 50%, and full traffic per site. Model
fine-tuning should be considered only after enough redacted, human-approved conversations exist.
