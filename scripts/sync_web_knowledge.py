import argparse
import asyncio
import json

from app.application.tenant_context import tenant_scope
from app.bootstrap.container import build_container
from app.config import get_settings
from app.knowledge.web import WebCrawlPolicy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a non-publishing website crawl and embedding validation."
    )
    parser.add_argument("--tenant-id")
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--sitemap-url", action="append", default=[])
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-sitemaps", type=int)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--skip-sitemap", action="store_true")
    parser.add_argument("--no-follow-internal-links", action="store_true")
    parser.add_argument("--primary-language", default="en")
    parser.add_argument("--translated-locale", action="append", default=[])
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.web_crawler_enabled:
        raise PermissionError("website synchronization is disabled; set WEB_CRAWLER_ENABLED=true")
    tenant_id = (args.tenant_id or settings.default_tenant_id).strip()
    site_id = args.site_id.strip()
    base_url = args.base_url.strip()
    if not tenant_id or not site_id or not base_url:
        raise ValueError("tenant ID, site ID, and base URL are required")

    container = await build_container(settings)
    try:
        await container.knowledge_adapter.initialize()
        with tenant_scope(tenant_id):
            report = await container.web_knowledge_sync_service.validate(
                WebCrawlPolicy(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    base_url=base_url,
                    seed_urls=tuple(args.url),
                    sitemap_urls=tuple(args.sitemap_url),
                    max_pages=args.max_pages or settings.web_crawler_max_pages,
                    max_sitemaps=args.max_sitemaps or settings.web_crawler_max_sitemaps,
                    max_response_bytes=settings.web_crawler_max_response_bytes,
                    max_decompressed_response_bytes=(
                        settings.web_crawler_max_decompressed_response_bytes
                    ),
                    max_compression_ratio=settings.web_crawler_max_compression_ratio,
                    request_timeout_seconds=settings.web_crawler_request_timeout_seconds,
                    crawl_delay_seconds=settings.web_crawler_delay_seconds,
                    follow_internal_links=(
                        settings.web_crawler_follow_internal_links
                        and not args.no_follow_internal_links
                    ),
                    respect_robots_txt=settings.web_crawler_respect_robots_txt,
                    batch_size=args.batch_size or settings.web_crawler_batch_size,
                    discover_sitemaps=not args.skip_sitemap,
                    language=args.primary_language,
                    translated_locales=tuple(args.translated_locale),
                    enforce_primary_language=True,
                )
            )
        print(json.dumps(_report_payload(report), ensure_ascii=False))
        return 0 if report.failed_count == 0 else 2
    finally:
        await container.knowledge_adapter.close()
        await container.database.dispose()


def _report_payload(report) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "sync_job_id": report.sync_job_id,
        "published": report.published,
        "discovered_count": report.discovered_count,
        "document_count": report.document_count,
        "changed_document_count": report.changed_document_count,
        "unchanged_document_count": report.skipped_count,
        "http_not_modified_count": report.http_not_modified_count,
        "duplicate_count": report.duplicate_count,
        "duplicate_product_count": report.duplicate_product_count,
        "product_count": report.product_count,
        "pending_removal_count": report.pending_removal_count,
        "expired_count": report.expired_count,
        "indexed_chunk_count": report.indexed_count,
        "excluded_count": report.excluded_count,
        "failed_count": report.failed_count,
        "errors": report.errors,
    }


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
