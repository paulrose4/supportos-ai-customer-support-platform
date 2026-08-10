# Order Requests: Human-Only Policy

## Launch Boundary

The AI agent does not query, infer, modify, or make eligibility decisions about customer orders.
This is a deterministic application invariant, not a prompt instruction.

The following requests create a durable human handoff before any model or business-data tool is
called:

- order status and tracking;
- cancellation and returns;
- refunds and refund status;
- shipping-address changes;
- payment failures, duplicate charges, and disputed charges;
- missing, incorrect, damaged, or undelivered items.

Public policy questions remain eligible for evidence-grounded knowledge answers. Examples include
published shipping times, accepted payment methods, and the general return policy. A question
about a specific customer's order is always human-only.

## Data Minimization

The Widget may invite the customer to provide an optional order reference and a short issue
description after the handoff already exists. It must never request a password, full payment-card
details, identity documents, or complete payment data. Email, address, identity, and authoritative
order facts are verified by the human agent in the trusted store console.

No order state is written into long-term customer memory. An order reference in the handoff is an
unverified routing hint and is never evidence of identity or ownership.

## Routing And SLA

All deterministic order handoffs use queue `orders` and SLA policy `order-human-v1`:

| Category | Priority | Internal response deadline |
| --- | --- | --- |
| payment issue, address change, cancellation | urgent | 5 minutes |
| refund, return, damaged/missing/wrong item | high | 10 minutes |
| status, tracking, other order support | normal | 15 minutes |

The deadline is an internal operating target. The Widget does not promise it to the customer
because business hours and staffing may differ by site. The Dashboard sorts overdue work first and
exposes an `SLA 超时` view.

Production must configure at least one external handoff notification path. SMTP is supported by
the current application. If SMTP is not used, an equivalent audited Feishu, DingTalk, or enterprise
messaging adapter is required before unattended traffic is enabled.

## Metrics

Forced order handoffs are excluded from the AI-eligible run denominator. Report these separately:

- forced order handoff count;
- order-routing accuracy;
- percentage accepted before the internal deadline;
- first human response time;
- unresolved and unread order conversations;
- avoidable handoff rate for AI-eligible questions.

An order handoff is expected safe behavior and must not be counted as an AI failure.

## Regression Requirements

Every release must verify that:

1. order requests produce `ResponseKind.HANDOFF`;
2. no order repository or order tool is called;
3. the handoff contains queue, priority, SLA policy, deadline, and complete v2 context;
4. public return, shipping, and payment-method policy questions remain knowledge eligible;
5. sensitive data is not requested or placed into model-visible handoff instructions.
