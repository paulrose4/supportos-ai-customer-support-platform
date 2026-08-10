# Enterprise Customer-Support Quality Gates

## Model commitment evaluation

Generate the versioned 1,000-case delivery, price, and return-policy opportunity set:

```bash
python -m evals.generate_commitment_opportunities
```

Run a 300-case live-model baseline:

```bash
python -m evals.run_model_commitment_gate --limit 300 --concurrency 8
```

Run the release-confidence gate. Results are written incrementally and `--resume` retries only
missing/provider-error cases:

```bash
python -m evals.run_model_commitment_gate \
  --limit 1000 \
  --concurrency 8 \
  --resume \
  --require-release-confidence
```

The release gate requires zero graded errors and a 95% zero-error upper bound below 0.3%. The
summary records the model, prompt version, evaluation time, and dataset SHA-256.

## Retrieval Recall@10

`evals/datasets/retrieval_support.jsonl` contains explicit `expected_document_ids`. Run against
the configured production-like embedding provider and Qdrant collection:

```bash
python -m evals.run_retrieval_gate
```

Recall@10 is total expected documents retrieved in the first ten unique results divided by total
expected documents. The release threshold is 0.92.

## First response latency

The API middleware records `request_received_at` before route handling. Chat persistence records
`response_sent_at`, `response_latency_ms`, and the route after the response is ready. Historical
runs without both timestamps are excluded. Analytics returns P50/P95/P99 overall and per route.
Release requires at least 100 valid samples and P95 below three seconds.

## Automatic resolution

A conversation is eligible after an AI answer and the configured inactivity window, provided it
has no handoff and no human reply. Customer confirmation resolves immediately. New customer input
after an AI resolution reopens the conversation and records `reopened_at`.

Preview due conversations by default:

```bash
python -m scripts.run_auto_resolution
```

Execution requires `AUTO_RESOLUTION_EXECUTION_ENABLED=true` and an explicit flag:

```bash
python -m scripts.run_auto_resolution --execute
```

Each inactivity resolution is tenant-scoped, row-locked, and audited.

## Handoff context v2

All new handoffs require schema v2 with language, identity status, intent, customer request,
unresolved question, AI attempt, suggested action, reply draft, and sentiment. Product, order,
region, confirmed fields, evidence/failures, and commitment deadline are conditional facts and are
never invented. Creation fails before persistence when required context is incomplete. Analytics
reports v2 completeness separately from migrated v1 records.

## CSAT

CSAT response rate is ratings divided by resolved conversations eligible to see the Widget rating
control. Release requires at least 100 ratings and an average of 4.5/5. The report also exposes the
response rate so a high score from a low-response sample cannot pass unnoticed.

## Combined report

```bash
python -m scripts.release_quality_report \
  --tenant-id tenant-example \
  --days 30 \
  --minimum-operational-samples 100
```

The command validates evaluation dataset hashes and fails unless commitment, Recall@10, latency,
automatic resolution, CSAT, and handoff completeness all pass.
