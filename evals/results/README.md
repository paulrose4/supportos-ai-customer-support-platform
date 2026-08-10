# Evaluation Outputs

This directory intentionally does not track raw evaluation runs.

Run outputs may contain model responses, business identifiers, environment-specific metadata, and results tied to an older commit. CI should upload raw JSONL and detailed summaries as build artifacts instead of committing them to the source tree.

A result is suitable for public release only when it is sanitized and records at least:

- the Git commit SHA;
- the dataset hash and prompt version;
- the model and embedding configuration;
- the execution environment and timestamp;
- pass/fail status for every advertised gate.

The deterministic output-contract suite can be reproduced with:

```bash
python -m evals.run_production_gate
```

Historical raw runs are not evidence for the current release.
