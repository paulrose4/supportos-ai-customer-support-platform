# Model Relay, Email Handoff, and Reply Analytics

## OpenAI-Compatible Relay

The chat adapter supports both the Responses API and OpenAI-compatible Chat Completions relays. Configure the relay without committing credentials:

```env
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://relay.example.com/v1
OPENAI_API_KEY=replace-locally
OPENAI_CHAT_MODEL=gpt-5.4
OPENAI_CHAT_API_MODE=chat_completions
```

Embedding can use the same compatible endpoint when it implements `/embeddings`. If it does not, configure a separate embedding provider before production; chat model compatibility does not prove embedding compatibility. Changing embedding dimensions requires a new Qdrant collection and full reindex.

## Per-Site Handoff Email

Recipients are mapped by trusted WordPress `site_id`:

```env
HANDOFF_EMAIL_ENABLED=true
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=support@example.com
SMTP_PASSWORD=replace-locally
SMTP_FROM_ADDRESS=support@example.com
SMTP_USE_STARTTLS=true
HANDOFF_EMAIL_RECIPIENTS={"wordpress-site-001":["agent-one@example.com"]}
```

The notification is sent only after the handoff is durably created. Repeated idempotent creation of the same handoff does not send another email. Email failure never rolls back the durable handoff. The email contains a redacted summary, site, conversation, reason, and risk level; it contains no site key or model credential.

## Reply Analytics

`GET /v1/admin/analytics/overview` reads deterministic PostgreSQL records and supports `days` and `site_id` filters. The Dashboard Reports page displays:

- AI answer rate = answer agent runs / all agent runs.
- Handoff rate = handoff agent runs / all agent runs.
- Human reply rate = conversations containing an agent message / conversations.
- Resolution rate = resolved conversations / conversations.

These metrics are operational indicators, not LLM judgments. They can later be extended with first-response time, satisfaction, and daily aggregate tables.
