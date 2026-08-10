# Repository Instructions

## Scope
These instructions apply to the entire repository.

## Architecture
- Preserve the dependency direction documented in `docs/architecture.md`.
- Domain and application code must not import FastAPI, LangGraph, SQLAlchemy, Qdrant, or provider SDKs.
- API, graph nodes, and tools call application services; they never access repositories or SDK clients directly.
- Infrastructure models never become domain models, API responses, or LangGraph state.
- Identity and `tenant_id` come only from trusted adapters or service configuration, never user text or model output.

## Development
- Target Python 3.12.
- Add type hints to public interfaces.
- Keep side effects behind ports and make writes idempotent and auditable.
- Use deterministic rules for authorization, risk, evidence, and business decisions.
- Do not add real secrets or real customer PII.

## Validation
Run before handoff:
- `python -m ruff check .`
- `python -m ruff format --check .`
- `python -m pytest`