import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from app.application.dto import EnqueueWebSyncJobCommand, ListManagedSitesQuery
from app.application.tenant_context import tenant_scope
from app.bootstrap.container import build_container
from app.config import get_settings
from app.domain.models import (
    AuthenticatedPrincipal,
    WebSyncMode,
    WebSyncPolicySnapshot,
    WebSyncTrigger,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enqueue one website synchronization job for the deployment scheduler."
    )
    parser.add_argument("--tenant-id")
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--manifest-id", required=True)
    parser.add_argument("--mode", choices=("shadow", "production"), default="shadow")
    parser.add_argument("--sample-size", choices=(20, 100, 200, 500), type=int, default=20)
    parser.add_argument("--idempotency-key")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.web_crawler_enabled:
        raise PermissionError("website synchronization is disabled; set WEB_CRAWLER_ENABLED=true")
    tenant_id = (args.tenant_id or settings.default_tenant_id).strip()
    site_id = args.site_id.strip()
    correlation_id = str(uuid4())
    now = datetime.now(UTC)
    principal = AuthenticatedPrincipal(
        subject_id="web-sync-scheduler",
        tenant_id=tenant_id,
        roles=frozenset({"service"}),
        scopes=frozenset({"sites:manage", "knowledge:read", "knowledge:sync"}),
        authentication_method="service_configuration",
        authenticated_at=now,
        correlation_id=correlation_id,
    )
    container = await build_container(settings)
    try:
        with tenant_scope(tenant_id):
            sites = await container.site_administration_service.list_sites(
                ListManagedSitesQuery(principal)
            )
            site = next((item for item in sites.items if item.site_id == site_id), None)
            if site is None:
                raise LookupError("site was not found in the trusted tenant registry")
            if site.status != "active" or not site.base_url:
                raise ValueError("site must be active and have a base URL")
            result = await container.web_sync_job_service.enqueue(
                EnqueueWebSyncJobCommand(
                    principal=principal,
                    site_id=site.site_id,
                    base_url=site.base_url,
                    primary_language=site.primary_language,
                    policy=WebSyncPolicySnapshot(
                        max_pages=settings.web_crawler_max_pages,
                        max_sitemaps=settings.web_crawler_max_sitemaps,
                        max_response_bytes=settings.web_crawler_max_response_bytes,
                        max_decompressed_response_bytes=(
                            settings.web_crawler_max_decompressed_response_bytes
                        ),
                        max_compression_ratio=settings.web_crawler_max_compression_ratio,
                        request_timeout_seconds=settings.web_crawler_request_timeout_seconds,
                        crawl_delay_seconds=settings.web_crawler_delay_seconds,
                        follow_internal_links=settings.web_crawler_follow_internal_links,
                        respect_robots_txt=settings.web_crawler_respect_robots_txt,
                        batch_size=settings.web_crawler_batch_size,
                    ),
                    manifest_id=args.manifest_id,
                    mode=WebSyncMode(args.mode),
                    sample_size=args.sample_size if args.mode == "shadow" else None,
                    idempotency_key=(
                        args.idempotency_key
                        or f"scheduled:{tenant_id}:{site_id}:{now.date().isoformat()}"
                    ),
                    correlation_id=correlation_id,
                    trigger=WebSyncTrigger.SCHEDULED,
                )
            )
        print(
            json.dumps(
                {
                    "created": result.created,
                    "job_id": result.job.job_id,
                    "site_id": result.job.site_id,
                    "status": result.job.status.value,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        await container.knowledge_adapter.close()
        await container.database.dispose()


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
