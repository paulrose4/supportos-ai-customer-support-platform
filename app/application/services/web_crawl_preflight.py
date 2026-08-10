import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from app.application.dto.web_crawl_preflight import (
    GetLatestWebCrawlManifestQuery,
    RunWebCrawlPreflightCommand,
    WebCrawlPreflightResult,
    WebCrawlPreflightRuntimePolicy,
)
from app.domain.models.web_crawl_manifest import (
    WebCrawlManifest,
    WebCrawlManifestItem,
    WebCrawlManifestStatus,
    WebCrawlPageState,
)
from app.domain.ports.web_crawl_manifests import WebCrawlManifestStorePort
from app.domain.ports.web_crawl_preflight import (
    WebCrawlInspectionRequest,
    WebCrawlPreflightInspectorPort,
)


class WebCrawlPreflightService:
    def __init__(
        self,
        *,
        inspector: WebCrawlPreflightInspectorPort,
        store: WebCrawlManifestStorePort,
        runtime_policy: WebCrawlPreflightRuntimePolicy,
    ) -> None:
        self._inspector = inspector
        self._store = store
        self._runtime_policy = runtime_policy

    async def run(self, command: RunWebCrawlPreflightCommand) -> WebCrawlPreflightResult:
        _require_scope(command.principal.scopes, "knowledge:sync")
        if command.translation_provider != "gtranslate":
            raise ValueError("only the gtranslate translation provider is supported")
        page_states = await self._store.list_page_states(
            tenant_id=command.principal.tenant_id,
            site_id=command.site_id,
        )
        previous_manifest = await self._store.get_latest(
            tenant_id=command.principal.tenant_id,
            site_id=command.site_id,
        )
        previous_sitemap_urls = ()
        if (
            previous_manifest is not None
            and previous_manifest.status is WebCrawlManifestStatus.READY
            and previous_manifest.coverage_status == "declared_complete"
        ):
            previous_sitemap_urls = previous_manifest.root_sitemap_urls or (
                previous_manifest.root_sitemap_url,
            )
        inspection = await self._inspector.inspect(
            WebCrawlInspectionRequest(
                base_url=command.base_url,
                primary_language=command.primary_language,
                translation_provider=command.translation_provider,
                max_sitemaps=self._runtime_policy.max_sitemaps,
                max_response_bytes=self._runtime_policy.max_response_bytes,
                max_decompressed_response_bytes=(
                    self._runtime_policy.max_decompressed_response_bytes
                ),
                max_compression_ratio=self._runtime_policy.max_compression_ratio,
                request_timeout_seconds=self._runtime_policy.request_timeout_seconds,
                user_agent=self._runtime_policy.user_agent,
                discovery_mode=command.discovery_mode,
                explicit_sitemap_urls=command.explicit_sitemap_urls,
                allowed_sitemap_origins=command.allowed_sitemap_origins,
                previous_sitemap_urls=previous_sitemap_urls,
                max_urls=self._runtime_policy.max_urls,
            )
        )
        manifest_id = str(uuid4())
        fingerprint = _fingerprint(
            base_url=command.base_url,
            primary_language=command.primary_language,
            translated_locales=inspection.translated_locales,
            sitemap_urls=inspection.primary_sitemap_urls,
            root_sitemap_urls=inspection.root_sitemap_urls,
            discovery_method=inspection.discovery_method,
            source_config_version=command.source_config_version,
            urls=tuple(item.url for item in inspection.items),
        )
        items = _carry_forward_page_states(inspection.items, page_states)
        manifest = WebCrawlManifest(
            tenant_id=command.principal.tenant_id,
            site_id=command.site_id,
            manifest_id=manifest_id,
            base_url=command.base_url,
            root_sitemap_url=inspection.root_sitemap_url,
            primary_language=command.primary_language,
            translation_provider=command.translation_provider,
            status=(
                WebCrawlManifestStatus.BLOCKED
                if inspection.blocking_reasons
                else WebCrawlManifestStatus.READY
            ),
            fingerprint=fingerprint,
            primary_sitemap_urls=inspection.primary_sitemap_urls,
            translated_locales=inspection.translated_locales,
            excluded_sitemap_count=inspection.excluded_sitemap_count,
            excluded_url_count=inspection.excluded_url_count,
            blocking_reasons=inspection.blocking_reasons,
            created_by=command.principal.subject_id,
            created_at=datetime.now(UTC),
            policy_version="web-crawl-v2",
            source_config_version=command.source_config_version,
            root_sitemap_urls=inspection.root_sitemap_urls,
            discovery_method=inspection.discovery_method,
            warnings=inspection.warnings,
            coverage_status=inspection.coverage_status,
            discovery_attempts=inspection.discovery_attempts,
            items=items,
        )
        return WebCrawlPreflightResult(await self._store.save(manifest))

    async def get_latest(
        self,
        query: GetLatestWebCrawlManifestQuery,
    ) -> WebCrawlManifest | None:
        _require_scope(query.principal.scopes, "knowledge:read")
        return await self._store.get_latest(
            tenant_id=query.principal.tenant_id,
            site_id=query.site_id,
        )


def _fingerprint(
    *,
    base_url: str,
    primary_language: str,
    translated_locales: tuple[str, ...],
    sitemap_urls: tuple[str, ...],
    root_sitemap_urls: tuple[str, ...],
    discovery_method: str,
    source_config_version: int,
    urls: tuple[str, ...],
) -> str:
    payload = {
        "base_url": base_url,
        "primary_language": primary_language,
        "translated_locales": sorted(translated_locales),
        "sitemap_urls": sorted(sitemap_urls),
        "root_sitemap_urls": sorted(root_sitemap_urls),
        "discovery_method": discovery_method,
        "source_config_version": source_config_version,
        "urls": sorted(urls),
    }
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _carry_forward_page_states(
    items: tuple[WebCrawlManifestItem, ...],
    page_states: tuple[WebCrawlPageState, ...],
) -> tuple[WebCrawlManifestItem, ...]:
    states_by_url = {item.url: item for item in page_states}
    return tuple(
        WebCrawlManifestItem(
            url=item.url,
            source_sitemap_url=item.source_sitemap_url,
            content_kind=item.content_kind,
            last_modified=item.last_modified,
            document_id=state.document_id,
            version_id=state.version_id,
            canonical_url=state.canonical_url,
            final_url=state.final_url,
            etag=state.etag,
            response_last_modified=state.last_modified,
            product_key=state.product_key,
            normalized_product_key=state.normalized_product_key,
            normalization_version=state.normalization_version,
            artifact_status=state.artifact_status,
            validated_at=state.validated_at,
        )
        if (state := states_by_url.get(item.url)) is not None
        else item
        for item in items
    )


def _require_scope(scopes: frozenset[str], required: str) -> None:
    if required not in scopes:
        raise PermissionError(f"missing required scope: {required}")
