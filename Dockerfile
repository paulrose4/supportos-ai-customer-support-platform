FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/app/.local/bin:${PATH}"

RUN addgroup --system app && adduser --system --ingroup app --home /home/app app \
    && mkdir -p /home/app/.cache/fastembed \
    && mkdir -p /app/data/widget-assets \
    && chown -R app:app /home/app/.cache /app/data
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/__init__.py scripts/validate_production_env.py scripts/validate_database_schema.py scripts/run_retention.py scripts/sync_global_knowledge.py scripts/run_web_sync_worker.py scripts/enqueue_web_sync_job.py scripts/run_outbox_worker.py scripts/run_tenant_experience_worker.py ./scripts/

USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]

