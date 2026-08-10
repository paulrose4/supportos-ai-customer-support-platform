from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.web_crawl_manifest import (
    WebCrawlDiscoveryAttempt,
    WebCrawlManifest,
    WebCrawlManifestItem,
    WebCrawlManifestStatus,
    WebCrawlPageState,
)
from app.domain.rules.duplicate_product import (
    PRODUCT_IDENTITY_NORMALIZATION_VERSION,
    normalize_product_identity,
)
from app.integrations.postgres.models import (
    WebCrawlManifestItemModel,
    WebCrawlManifestModel,
    WebCrawlPageStateModel,
)


class PostgreSQLWebCrawlManifestStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, manifest: WebCrawlManifest) -> WebCrawlManifest:
        async with self._session_factory.begin() as session:
            model = WebCrawlManifestModel(
                tenant_id=manifest.tenant_id,
                site_id=manifest.site_id,
                manifest_id=manifest.manifest_id,
                base_url=manifest.base_url,
                root_sitemap_url=manifest.root_sitemap_url,
                root_sitemap_urls=list(manifest.root_sitemap_urls),
                discovery_method=manifest.discovery_method,
                warnings=list(manifest.warnings),
                coverage_status=manifest.coverage_status,
                discovery_attempts=[
                    {
                        "url": attempt.url,
                        "source": attempt.source,
                        "outcome": attempt.outcome,
                        "final_url": attempt.final_url,
                    }
                    for attempt in manifest.discovery_attempts
                ],
                primary_language=manifest.primary_language,
                translation_provider=manifest.translation_provider,
                status=manifest.status.value,
                fingerprint=manifest.fingerprint,
                policy_version=manifest.policy_version,
                source_config_version=manifest.source_config_version,
                url_count=manifest.url_count,
                content_kind_counts=manifest.content_kind_counts,
                primary_sitemap_urls=list(manifest.primary_sitemap_urls),
                translated_locales=list(manifest.translated_locales),
                excluded_sitemap_count=manifest.excluded_sitemap_count,
                excluded_url_count=manifest.excluded_url_count,
                blocking_reasons=list(manifest.blocking_reasons),
                created_by=manifest.created_by,
                created_at=manifest.created_at,
            )
            session.add(model)
            await session.flush()
            persisted = replace(
                manifest,
                version=model.version,
                item_count=manifest.url_count,
                item_kind_counts=tuple(sorted(manifest.content_kind_counts.items())),
            )
            session.add_all(
                [
                    WebCrawlManifestItemModel(
                        tenant_id=manifest.tenant_id,
                        site_id=manifest.site_id,
                        manifest_id=manifest.manifest_id,
                        url=item.url,
                        source_sitemap_url=item.source_sitemap_url,
                        content_kind=item.content_kind,
                        last_modified=item.last_modified,
                        document_id=item.document_id,
                        version_id=item.version_id,
                        canonical_url=item.canonical_url,
                        final_url=item.final_url,
                        etag=item.etag,
                        response_last_modified=item.response_last_modified,
                        product_key=item.product_key,
                        normalized_product_key=(
                            item.normalized_product_key
                            or normalize_product_identity(item.product_key)
                        ),
                        normalization_version=(
                            item.normalization_version
                            or (
                                PRODUCT_IDENTITY_NORMALIZATION_VERSION
                                if normalize_product_identity(item.product_key) is not None
                                else None
                            )
                        ),
                        artifact_status=item.artifact_status,
                        validated_at=item.validated_at,
                    )
                    for item in manifest.items
                ]
            )
        return persisted

    async def get(
        self,
        *,
        tenant_id: str,
        site_id: str,
        manifest_id: str,
    ) -> WebCrawlManifest | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(WebCrawlManifestModel).where(
                    WebCrawlManifestModel.tenant_id == tenant_id,
                    WebCrawlManifestModel.site_id == site_id,
                    WebCrawlManifestModel.manifest_id == manifest_id,
                )
            )
            if model is None:
                return None
            items = tuple(
                (
                    await session.scalars(
                        select(WebCrawlManifestItemModel)
                        .where(
                            WebCrawlManifestItemModel.tenant_id == tenant_id,
                            WebCrawlManifestItemModel.site_id == site_id,
                            WebCrawlManifestItemModel.manifest_id == manifest_id,
                        )
                        .order_by(WebCrawlManifestItemModel.url)
                    )
                ).all()
            )
        return _to_domain(model, items)

    async def get_metadata(
        self,
        *,
        tenant_id: str,
        site_id: str,
        manifest_id: str,
    ) -> WebCrawlManifest | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(WebCrawlManifestModel).where(
                    WebCrawlManifestModel.tenant_id == tenant_id,
                    WebCrawlManifestModel.site_id == site_id,
                    WebCrawlManifestModel.manifest_id == manifest_id,
                )
            )
            if model is None:
                return None
        return _to_domain(model, (), item_count=model.url_count)

    async def get_latest(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> WebCrawlManifest | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(WebCrawlManifestModel)
                .where(
                    WebCrawlManifestModel.tenant_id == tenant_id,
                    WebCrawlManifestModel.site_id == site_id,
                )
                .order_by(WebCrawlManifestModel.created_at.desc())
                .limit(1)
            )
            if model is None:
                return None
        return _to_domain(model, (), item_count=model.url_count)

    async def list_items(
        self,
        *,
        tenant_id: str,
        site_id: str,
        manifest_id: str,
        offset: int,
        limit: int,
        deterministic_sample: bool = False,
    ) -> tuple[WebCrawlManifestItem, ...]:
        ordering = (
            (func.md5(WebCrawlManifestItemModel.url), WebCrawlManifestItemModel.url)
            if deterministic_sample
            else (WebCrawlManifestItemModel.url,)
        )
        async with self._session_factory() as session:
            models = tuple(
                (
                    await session.scalars(
                        select(WebCrawlManifestItemModel)
                        .where(
                            WebCrawlManifestItemModel.tenant_id == tenant_id,
                            WebCrawlManifestItemModel.site_id == site_id,
                            WebCrawlManifestItemModel.manifest_id == manifest_id,
                        )
                        .order_by(*ordering)
                        .offset(max(0, offset))
                        .limit(max(1, limit))
                    )
                ).all()
            )
        return tuple(_item_to_domain(item) for item in models)

    async def list_page_states(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> tuple[WebCrawlPageState, ...]:
        async with self._session_factory() as session:
            models = tuple(
                (
                    await session.scalars(
                        select(WebCrawlPageStateModel)
                        .where(
                            WebCrawlPageStateModel.tenant_id == tenant_id,
                            WebCrawlPageStateModel.site_id == site_id,
                            WebCrawlPageStateModel.artifact_status == "published",
                        )
                        .order_by(WebCrawlPageStateModel.url)
                    )
                ).all()
            )
        return tuple(_page_state_to_domain(item) for item in models)

    async def replace_page_states(
        self,
        *,
        tenant_id: str,
        site_id: str,
        states: tuple[WebCrawlPageState, ...],
    ) -> None:
        if any(state.tenant_id != tenant_id or state.site_id != site_id for state in states):
            raise ValueError("page states must belong to the requested tenant and site")
        now = datetime.now(UTC)
        async with self._session_factory.begin() as session:
            await session.execute(
                delete(WebCrawlPageStateModel).where(
                    WebCrawlPageStateModel.tenant_id == tenant_id,
                    WebCrawlPageStateModel.site_id == site_id,
                )
            )
            for start in range(0, len(states), 250):
                values = [
                    {
                        "tenant_id": state.tenant_id,
                        "site_id": state.site_id,
                        "url": state.url,
                        "document_id": state.document_id,
                        "version_id": state.version_id,
                        "canonical_url": state.canonical_url,
                        "final_url": state.final_url,
                        "etag": state.etag,
                        "last_modified": state.last_modified,
                        "product_key": state.product_key,
                        "normalized_product_key": (
                            state.normalized_product_key
                            or normalize_product_identity(state.product_key)
                        ),
                        "normalization_version": (
                            state.normalization_version
                            or (
                                PRODUCT_IDENTITY_NORMALIZATION_VERSION
                                if normalize_product_identity(state.product_key) is not None
                                else None
                            )
                        ),
                        "artifact_status": state.artifact_status,
                        "validated_at": state.validated_at,
                        "updated_at": now,
                    }
                    for state in states[start : start + 250]
                ]
                statement = insert(WebCrawlPageStateModel).values(values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=("tenant_id", "site_id", "url"),
                        set_={
                            "document_id": statement.excluded.document_id,
                            "version_id": statement.excluded.version_id,
                            "canonical_url": statement.excluded.canonical_url,
                            "final_url": statement.excluded.final_url,
                            "etag": statement.excluded.etag,
                            "last_modified": statement.excluded.last_modified,
                            "product_key": statement.excluded.product_key,
                            "normalized_product_key": statement.excluded.normalized_product_key,
                            "normalization_version": statement.excluded.normalization_version,
                            "artifact_status": statement.excluded.artifact_status,
                            "validated_at": statement.excluded.validated_at,
                            "updated_at": now,
                        },
                    )
                )


def _to_domain(
    model: WebCrawlManifestModel,
    items: tuple[WebCrawlManifestItemModel, ...],
    *,
    item_count: int | None = None,
) -> WebCrawlManifest:
    return WebCrawlManifest(
        tenant_id=model.tenant_id,
        site_id=model.site_id,
        manifest_id=model.manifest_id,
        base_url=model.base_url,
        root_sitemap_url=model.root_sitemap_url,
        root_sitemap_urls=tuple(str(value) for value in model.root_sitemap_urls or ()),
        discovery_method=model.discovery_method,
        warnings=tuple(str(value) for value in model.warnings or ()),
        coverage_status=model.coverage_status,
        discovery_attempts=tuple(
            WebCrawlDiscoveryAttempt(
                url=str(value.get("url", "")),
                source=str(value.get("source", "unknown")),
                outcome=str(value.get("outcome", "unknown")),
                final_url=(str(value["final_url"]) if value.get("final_url") is not None else None),
            )
            for value in model.discovery_attempts or ()
        ),
        primary_language=model.primary_language,
        translation_provider=model.translation_provider,
        status=WebCrawlManifestStatus(model.status),
        fingerprint=model.fingerprint,
        primary_sitemap_urls=tuple(str(value) for value in model.primary_sitemap_urls or ()),
        translated_locales=tuple(str(value) for value in model.translated_locales or ()),
        excluded_sitemap_count=model.excluded_sitemap_count,
        excluded_url_count=model.excluded_url_count,
        blocking_reasons=tuple(str(value) for value in model.blocking_reasons or ()),
        created_by=model.created_by,
        created_at=model.created_at,
        version=model.version,
        policy_version=model.policy_version,
        source_config_version=model.source_config_version,
        item_count=item_count,
        item_kind_counts=tuple(
            sorted(
                (str(key), int(value)) for key, value in (model.content_kind_counts or {}).items()
            )
        ),
        items=tuple(_item_to_domain(item) for item in items),
    )


def _item_to_domain(model: WebCrawlManifestItemModel) -> WebCrawlManifestItem:
    return WebCrawlManifestItem(
        url=model.url,
        source_sitemap_url=model.source_sitemap_url,
        content_kind=model.content_kind,
        last_modified=model.last_modified,
        document_id=model.document_id,
        version_id=model.version_id,
        canonical_url=model.canonical_url,
        final_url=model.final_url,
        etag=model.etag,
        response_last_modified=model.response_last_modified,
        product_key=model.product_key,
        normalized_product_key=(
            model.normalized_product_key or normalize_product_identity(model.product_key)
        ),
        normalization_version=(
            model.normalization_version
            or (
                PRODUCT_IDENTITY_NORMALIZATION_VERSION
                if normalize_product_identity(model.product_key) is not None
                else None
            )
        ),
        artifact_status=model.artifact_status,
        validated_at=model.validated_at,
    )


def _page_state_to_domain(model: WebCrawlPageStateModel) -> WebCrawlPageState:
    return WebCrawlPageState(
        tenant_id=model.tenant_id,
        site_id=model.site_id,
        url=model.url,
        document_id=model.document_id,
        version_id=model.version_id,
        canonical_url=model.canonical_url,
        final_url=model.final_url,
        etag=model.etag,
        last_modified=model.last_modified,
        product_key=model.product_key,
        normalized_product_key=(
            model.normalized_product_key or normalize_product_identity(model.product_key)
        ),
        normalization_version=(
            model.normalization_version
            or (
                PRODUCT_IDENTITY_NORMALIZATION_VERSION
                if normalize_product_identity(model.product_key) is not None
                else None
            )
        ),
        artifact_status=model.artifact_status,
        validated_at=model.validated_at,
    )
