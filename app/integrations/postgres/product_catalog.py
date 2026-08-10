from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import (
    ProductDataStatus,
    ProductSnapshot,
    ProductSnapshotActivation,
    ProductSnapshotStatus,
)
from app.domain.ports import (
    ActiveProductCatalogSummary,
    ProductIdentityConflictError,
    ProductLookup,
)
from app.domain.rules import advance_missing_product_status, product_identity_conflicts
from app.domain.rules.duplicate_product import (
    PRODUCT_IDENTITY_NORMALIZATION_VERSION,
    normalize_product_identity,
)
from app.integrations.postgres.models import (
    AuditEventModel,
    ProductCatalogSnapshotModel,
    ProductFactSnapshotModel,
)


class PostgreSQLProductCatalogAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def begin_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        sync_job_id: str,
        started_at: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            existing = await _get_snapshot(session, tenant_id, site_id, snapshot_id)
            if existing is not None:
                return
            session.add(
                ProductCatalogSnapshotModel(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    snapshot_id=snapshot_id,
                    sync_job_id=sync_job_id,
                    status=ProductSnapshotStatus.STAGING.value,
                    started_at=started_at,
                )
            )
            _audit(
                session,
                tenant_id=tenant_id,
                event_type="product_snapshot.started",
                resource_id=snapshot_id,
                details={"site_id": site_id, "sync_job_id": sync_job_id},
                created_at=started_at,
            )

    async def stage_products(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        products: Sequence[ProductSnapshot],
    ) -> None:
        if not products:
            return
        async with self._session_factory.begin() as session:
            snapshot = await _require_snapshot(
                session,
                tenant_id,
                site_id,
                snapshot_id,
                for_update=True,
            )
            if snapshot.status != ProductSnapshotStatus.STAGING.value:
                raise RuntimeError("product snapshot is not staging")
            for product in products:
                _assert_scope(product, tenant_id, site_id, snapshot_id)
                normalized_product_key = _required_normalized_product_key(product)
                existing = await session.scalar(
                    select(ProductFactSnapshotModel).where(
                        ProductFactSnapshotModel.tenant_id == tenant_id,
                        ProductFactSnapshotModel.site_id == site_id,
                        ProductFactSnapshotModel.snapshot_id == snapshot_id,
                        ProductFactSnapshotModel.normalized_product_key == normalized_product_key,
                    )
                )
                values = _product_values(product)
                if existing is None:
                    session.add(ProductFactSnapshotModel(**values))
                else:
                    conflicts = product_identity_conflicts(_to_domain(existing), product)
                    if conflicts:
                        raise ProductIdentityConflictError(
                            product_key=product.product_key,
                            fields=conflicts,
                            existing_url=existing.canonical_url,
                            candidate_url=product.canonical_url,
                        )
                    for key, value in values.items():
                        if key not in {"tenant_id", "site_id", "snapshot_id", "product_key"}:
                            setattr(existing, key, value)
            await session.flush()
            snapshot.staged_count = int(
                await session.scalar(
                    select(func.count(ProductFactSnapshotModel.id)).where(
                        ProductFactSnapshotModel.tenant_id == tenant_id,
                        ProductFactSnapshotModel.site_id == site_id,
                        ProductFactSnapshotModel.snapshot_id == snapshot_id,
                    )
                )
                or 0
            )

    async def discard_staged_product(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        normalized_product_key: str,
        canonical_url: str,
    ) -> None:
        """Remove only the failed page's product artifact from a staging snapshot."""
        async with self._session_factory.begin() as session:
            await session.execute(
                delete(ProductFactSnapshotModel).where(
                    ProductFactSnapshotModel.tenant_id == tenant_id,
                    ProductFactSnapshotModel.site_id == site_id,
                    ProductFactSnapshotModel.snapshot_id == snapshot_id,
                    ProductFactSnapshotModel.normalized_product_key == normalized_product_key,
                    ProductFactSnapshotModel.canonical_url == canonical_url,
                )
            )
            snapshot = await _get_snapshot(session, tenant_id, site_id, snapshot_id)
            if snapshot is not None:
                snapshot.staged_count = int(
                    await session.scalar(
                        select(func.count(ProductFactSnapshotModel.id)).where(
                            ProductFactSnapshotModel.tenant_id == tenant_id,
                            ProductFactSnapshotModel.site_id == site_id,
                            ProductFactSnapshotModel.snapshot_id == snapshot_id,
                        )
                    )
                    or 0
                )

    async def count_staged_products(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> int:
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count(ProductFactSnapshotModel.id)).where(
                    ProductFactSnapshotModel.tenant_id == tenant_id,
                    ProductFactSnapshotModel.site_id == site_id,
                    ProductFactSnapshotModel.snapshot_id == snapshot_id,
                )
            )
        return int(count or 0)

    async def activate_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        completed_at: datetime,
        missing_confirmation_threshold: int = 2,
    ) -> ProductSnapshotActivation:
        async with self._session_factory.begin() as session:
            return await activate_product_snapshot_in_session(
                session,
                tenant_id=tenant_id,
                site_id=site_id,
                snapshot_id=snapshot_id,
                completed_at=completed_at,
                missing_confirmation_threshold=missing_confirmation_threshold,
            )

    async def restore_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        failed_snapshot_id: str,
        previous_snapshot_id: str | None,
        restored_at: datetime,
        error_summary: dict[str, str],
    ) -> None:
        """Restore the last product snapshot after a cross-store switch failure."""
        if previous_snapshot_id == failed_snapshot_id:
            raise ValueError("previous and failed product snapshots must be different")
        async with self._session_factory.begin() as session:
            failed = await _require_snapshot(
                session,
                tenant_id,
                site_id,
                failed_snapshot_id,
                for_update=True,
            )
            previous = None
            if previous_snapshot_id is not None:
                previous = await _require_snapshot(
                    session,
                    tenant_id,
                    site_id,
                    previous_snapshot_id,
                    for_update=True,
                )
                if previous.status not in {
                    ProductSnapshotStatus.ACTIVE.value,
                    ProductSnapshotStatus.SUPERSEDED.value,
                }:
                    raise RuntimeError("previous product snapshot cannot be restored")

            active_snapshots = list(
                await session.scalars(
                    select(ProductCatalogSnapshotModel)
                    .where(
                        ProductCatalogSnapshotModel.tenant_id == tenant_id,
                        ProductCatalogSnapshotModel.site_id == site_id,
                        ProductCatalogSnapshotModel.status == ProductSnapshotStatus.ACTIVE.value,
                    )
                    .with_for_update()
                )
            )
            for active in active_snapshots:
                if previous is None or active.snapshot_id != previous.snapshot_id:
                    active.status = ProductSnapshotStatus.SUPERSEDED.value
                    active.completed_at = restored_at
            # A finalization rollback keeps the candidate retryable. Explicit
            # abort/failure handling is responsible for changing it to failed.
            failed.status = ProductSnapshotStatus.STAGING.value
            failed.error_summary = dict(error_summary)
            failed.completed_at = restored_at
            # Release the partial unique index on the candidate before the
            # previous snapshot is promoted back to active in this transaction.
            await session.flush()
            if previous is not None:
                previous.status = ProductSnapshotStatus.ACTIVE.value
                previous.completed_at = restored_at
            _audit(
                session,
                tenant_id=tenant_id,
                event_type="product_snapshot.restored",
                resource_id=failed_snapshot_id,
                details={
                    "site_id": site_id,
                    "previous_snapshot_id": previous_snapshot_id,
                    "errors": dict(error_summary),
                },
                created_at=restored_at,
            )

    async def fail_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        error_summary: dict[str, str],
    ) -> None:
        async with self._session_factory.begin() as session:
            snapshot = await _require_snapshot(session, tenant_id, site_id, snapshot_id)
            if snapshot.status == ProductSnapshotStatus.ACTIVE.value:
                raise RuntimeError("an active product snapshot cannot be failed")
            failed_at = datetime.now(UTC)
            snapshot.status = ProductSnapshotStatus.FAILED.value
            snapshot.error_summary = dict(error_summary)
            snapshot.completed_at = failed_at
            _audit(
                session,
                tenant_id=tenant_id,
                event_type="product_snapshot.failed",
                resource_id=snapshot_id,
                details={"site_id": site_id, "errors": dict(error_summary)},
                created_at=failed_at,
            )

    async def find_exact(self, lookup: ProductLookup) -> ProductSnapshot | None:
        conditions = []
        identifiers = tuple(
            dict.fromkeys(
                value.strip().casefold()
                for value in (lookup.sku, lookup.mpn)
                if value and value.strip()
            )
        )
        if identifiers:
            conditions.extend(
                (
                    func.lower(ProductFactSnapshotModel.sku).in_(identifiers),
                    func.lower(ProductFactSnapshotModel.mpn).in_(identifiers),
                )
            )
        if lookup.canonical_url:
            conditions.append(ProductFactSnapshotModel.canonical_url == lookup.canonical_url)
        if lookup.page_path:
            conditions.append(ProductFactSnapshotModel.canonical_path == lookup.page_path)
        if not conditions:
            return None
        async with self._session_factory() as session:
            query = (
                select(ProductFactSnapshotModel)
                .join(
                    ProductCatalogSnapshotModel,
                    (ProductCatalogSnapshotModel.tenant_id == ProductFactSnapshotModel.tenant_id)
                    & (ProductCatalogSnapshotModel.site_id == ProductFactSnapshotModel.site_id)
                    & (
                        ProductCatalogSnapshotModel.snapshot_id
                        == ProductFactSnapshotModel.snapshot_id
                    ),
                )
                .where(
                    ProductFactSnapshotModel.tenant_id == lookup.tenant_id,
                    ProductCatalogSnapshotModel.status == ProductSnapshotStatus.ACTIVE.value,
                    or_(*conditions),
                )
                .order_by(ProductFactSnapshotModel.fetched_at.desc())
                .limit(1)
            )
            if lookup.site_id:
                query = query.where(ProductFactSnapshotModel.site_id == lookup.site_id)
            model = await session.scalar(query)
        return None if model is None else _to_domain(model)

    async def list_active_products(
        self,
        *,
        tenant_id: str,
        site_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ProductSnapshot, ...]:
        bounded_limit = max(1, min(limit, 10_000))
        async with self._session_factory() as session:
            query = (
                select(ProductFactSnapshotModel)
                .join(
                    ProductCatalogSnapshotModel,
                    (ProductCatalogSnapshotModel.tenant_id == ProductFactSnapshotModel.tenant_id)
                    & (ProductCatalogSnapshotModel.site_id == ProductFactSnapshotModel.site_id)
                    & (
                        ProductCatalogSnapshotModel.snapshot_id
                        == ProductFactSnapshotModel.snapshot_id
                    ),
                )
                .where(
                    ProductFactSnapshotModel.tenant_id == tenant_id,
                    ProductCatalogSnapshotModel.status == ProductSnapshotStatus.ACTIVE.value,
                    ProductFactSnapshotModel.data_status == ProductDataStatus.VALID.value,
                )
                .order_by(
                    ProductFactSnapshotModel.fetched_at.desc(),
                    ProductFactSnapshotModel.product_key,
                )
                .limit(bounded_limit)
            )
            if site_id:
                query = query.where(ProductFactSnapshotModel.site_id == site_id)
            models = list(await session.scalars(query))
        return tuple(_to_domain(model) for model in models)

    async def list_active_products_by_keys(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        product_keys: Sequence[str],
    ) -> tuple[ProductSnapshot, ...]:
        keys = tuple(dict.fromkeys(key for key in product_keys if key))[:500]
        if not keys:
            return ()
        async with self._session_factory() as session:
            query = (
                select(ProductFactSnapshotModel)
                .join(
                    ProductCatalogSnapshotModel,
                    (ProductCatalogSnapshotModel.tenant_id == ProductFactSnapshotModel.tenant_id)
                    & (ProductCatalogSnapshotModel.site_id == ProductFactSnapshotModel.site_id)
                    & (
                        ProductCatalogSnapshotModel.snapshot_id
                        == ProductFactSnapshotModel.snapshot_id
                    ),
                )
                .where(
                    ProductFactSnapshotModel.tenant_id == tenant_id,
                    ProductFactSnapshotModel.product_key.in_(keys),
                    ProductCatalogSnapshotModel.status == ProductSnapshotStatus.ACTIVE.value,
                    ProductFactSnapshotModel.data_status == ProductDataStatus.VALID.value,
                )
            )
            if site_id:
                query = query.where(ProductFactSnapshotModel.site_id == site_id)
            models = list(await session.scalars(query))
        by_key = {model.product_key: _to_domain(model) for model in models}
        return tuple(by_key[key] for key in keys if key in by_key)

    async def get_active_summary(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> ActiveProductCatalogSummary:
        async with self._session_factory() as session:
            snapshot = await session.scalar(
                select(ProductCatalogSnapshotModel)
                .where(
                    ProductCatalogSnapshotModel.tenant_id == tenant_id,
                    ProductCatalogSnapshotModel.site_id == site_id,
                    ProductCatalogSnapshotModel.status == ProductSnapshotStatus.ACTIVE.value,
                )
                .order_by(ProductCatalogSnapshotModel.completed_at.desc())
                .limit(1)
            )
        if snapshot is None:
            return ActiveProductCatalogSummary()
        return ActiveProductCatalogSummary(
            snapshot_id=snapshot.snapshot_id,
            product_count=snapshot.active_count,
            completed_at=snapshot.completed_at,
        )


def _product_values(product: ProductSnapshot) -> dict[str, object]:
    normalized_product_key = _required_normalized_product_key(product)
    return {
        "tenant_id": product.tenant_id,
        "site_id": product.site_id,
        "snapshot_id": product.snapshot_id,
        "product_key": product.product_key,
        "normalized_product_key": normalized_product_key,
        "normalization_version": (
            product.normalization_version or PRODUCT_IDENTITY_NORMALIZATION_VERSION
        ),
        "sku": product.sku,
        "mpn": product.mpn,
        "name": product.name,
        "canonical_url": product.canonical_url,
        "canonical_path": (urlsplit(product.canonical_url).path or "/")[:500],
        "brand": product.brand,
        "material": product.material,
        "attributes": dict(product.attributes),
        "dimensions": dict(product.dimensions),
        "weight": product.weight,
        "price": product.price,
        "currency": product.currency,
        "stock_status": product.stock_status,
        "shipping_warehouse": product.shipping_warehouse,
        "shipping_regions": list(product.shipping_regions),
        "fetched_at": product.fetched_at,
        "content_hash": product.content_hash,
        "source_url": product.source_url,
        "etag": product.etag,
        "last_modified": product.last_modified,
        "data_status": product.status.value,
        "missing_count": product.missing_count,
    }


def _cloned_product_values(
    product: ProductFactSnapshotModel,
    *,
    snapshot_id: str,
    data_status: str,
    missing_count: int,
) -> dict[str, object]:
    return {
        "tenant_id": product.tenant_id,
        "site_id": product.site_id,
        "snapshot_id": snapshot_id,
        "product_key": product.product_key,
        "normalized_product_key": product.normalized_product_key,
        "normalization_version": product.normalization_version,
        "sku": product.sku,
        "mpn": product.mpn,
        "name": product.name,
        "canonical_url": product.canonical_url,
        "canonical_path": product.canonical_path,
        "brand": product.brand,
        "material": product.material,
        "attributes": dict(product.attributes or {}),
        "dimensions": dict(product.dimensions or {}),
        "weight": product.weight,
        "price": product.price,
        "currency": product.currency,
        "stock_status": product.stock_status,
        "shipping_warehouse": product.shipping_warehouse,
        "shipping_regions": list(product.shipping_regions or []),
        "fetched_at": product.fetched_at,
        "content_hash": product.content_hash,
        "source_url": product.source_url,
        "etag": product.etag,
        "last_modified": product.last_modified,
        "data_status": data_status,
        "missing_count": missing_count,
    }


async def activate_product_snapshot_in_session(
    session: AsyncSession,
    *,
    tenant_id: str,
    site_id: str,
    snapshot_id: str,
    completed_at: datetime,
    missing_confirmation_threshold: int = 2,
) -> ProductSnapshotActivation:
    if missing_confirmation_threshold < 2:
        raise ValueError("missing confirmation threshold must be at least two")
    snapshot = await _require_snapshot(
        session,
        tenant_id,
        site_id,
        snapshot_id,
        for_update=True,
    )
    if snapshot.status == ProductSnapshotStatus.ACTIVE.value:
        return _activation_from_model(snapshot)
    if snapshot.status != ProductSnapshotStatus.STAGING.value:
        raise RuntimeError("product snapshot cannot be activated")
    previous = await session.scalar(
        select(ProductCatalogSnapshotModel)
        .where(
            ProductCatalogSnapshotModel.tenant_id == tenant_id,
            ProductCatalogSnapshotModel.site_id == site_id,
            ProductCatalogSnapshotModel.status == ProductSnapshotStatus.ACTIVE.value,
        )
        .with_for_update()
    )
    staged_products = list(
        await session.scalars(
            select(ProductFactSnapshotModel).where(
                ProductFactSnapshotModel.tenant_id == tenant_id,
                ProductFactSnapshotModel.site_id == site_id,
                ProductFactSnapshotModel.snapshot_id == snapshot_id,
            )
        )
    )
    staged_keys = {item.normalized_product_key for item in staged_products}
    if previous is not None and previous.snapshot_id != snapshot_id:
        previous_products = list(
            await session.scalars(
                select(ProductFactSnapshotModel).where(
                    ProductFactSnapshotModel.tenant_id == tenant_id,
                    ProductFactSnapshotModel.site_id == site_id,
                    ProductFactSnapshotModel.snapshot_id == previous.snapshot_id,
                )
            )
        )
        for product in previous_products:
            if product.normalized_product_key in staged_keys:
                continue
            status, missing_count = advance_missing_product_status(
                current_missing_count=product.missing_count,
                confirmation_threshold=missing_confirmation_threshold,
            )
            session.add(
                ProductFactSnapshotModel(
                    **_cloned_product_values(
                        product,
                        snapshot_id=snapshot_id,
                        data_status=status.value,
                        missing_count=missing_count,
                    )
                )
            )
        previous.status = ProductSnapshotStatus.SUPERSEDED.value
        previous.completed_at = completed_at
    await session.flush()
    counts = await _status_counts(session, tenant_id, site_id, snapshot_id)
    snapshot.status = ProductSnapshotStatus.ACTIVE.value
    snapshot.active_count = counts[ProductDataStatus.VALID.value]
    snapshot.pending_removal_count = counts[ProductDataStatus.PENDING_REMOVAL.value]
    snapshot.expired_count = counts[ProductDataStatus.EXPIRED.value]
    snapshot.completed_at = completed_at
    _audit(
        session,
        tenant_id=tenant_id,
        event_type="product_snapshot.activated",
        resource_id=snapshot_id,
        details={"site_id": site_id, **counts},
        created_at=completed_at,
    )
    return _activation_from_model(snapshot)


async def _status_counts(
    session: AsyncSession, tenant_id: str, site_id: str, snapshot_id: str
) -> dict[str, int]:
    rows = await session.execute(
        select(ProductFactSnapshotModel.data_status, func.count(ProductFactSnapshotModel.id))
        .where(
            ProductFactSnapshotModel.tenant_id == tenant_id,
            ProductFactSnapshotModel.site_id == site_id,
            ProductFactSnapshotModel.snapshot_id == snapshot_id,
        )
        .group_by(ProductFactSnapshotModel.data_status)
    )
    counts = {status.value: 0 for status in ProductDataStatus}
    counts.update({str(status): int(count) for status, count in rows})
    return counts


async def _get_snapshot(
    session: AsyncSession, tenant_id: str, site_id: str, snapshot_id: str
) -> ProductCatalogSnapshotModel | None:
    return await session.scalar(
        select(ProductCatalogSnapshotModel).where(
            ProductCatalogSnapshotModel.tenant_id == tenant_id,
            ProductCatalogSnapshotModel.site_id == site_id,
            ProductCatalogSnapshotModel.snapshot_id == snapshot_id,
        )
    )


async def _require_snapshot(
    session: AsyncSession,
    tenant_id: str,
    site_id: str,
    snapshot_id: str,
    *,
    for_update: bool = False,
) -> ProductCatalogSnapshotModel:
    statement = select(ProductCatalogSnapshotModel).where(
        ProductCatalogSnapshotModel.tenant_id == tenant_id,
        ProductCatalogSnapshotModel.site_id == site_id,
        ProductCatalogSnapshotModel.snapshot_id == snapshot_id,
    )
    if for_update:
        statement = statement.with_for_update()
    model = await session.scalar(statement)
    if model is None:
        raise RuntimeError("product snapshot not found")
    return model


def _activation_from_model(model: ProductCatalogSnapshotModel) -> ProductSnapshotActivation:
    return ProductSnapshotActivation(
        snapshot_id=model.snapshot_id,
        activated_count=model.active_count,
        pending_removal_count=model.pending_removal_count,
        expired_count=model.expired_count,
    )


def _to_domain(model: ProductFactSnapshotModel) -> ProductSnapshot:
    return ProductSnapshot(
        tenant_id=model.tenant_id,
        site_id=model.site_id,
        snapshot_id=model.snapshot_id,
        product_key=model.product_key,
        normalized_product_key=model.normalized_product_key,
        normalization_version=model.normalization_version,
        sku=model.sku,
        mpn=model.mpn,
        name=model.name,
        canonical_url=model.canonical_url,
        brand=model.brand,
        material=model.material,
        attributes={str(key): str(value) for key, value in (model.attributes or {}).items()},
        dimensions={str(key): str(value) for key, value in (model.dimensions or {}).items()},
        weight=model.weight,
        price=model.price,
        currency=model.currency,
        stock_status=model.stock_status,
        shipping_warehouse=model.shipping_warehouse,
        shipping_regions=tuple(str(value) for value in (model.shipping_regions or [])),
        fetched_at=model.fetched_at,
        content_hash=model.content_hash,
        source_url=model.source_url,
        etag=model.etag,
        last_modified=model.last_modified,
        status=ProductDataStatus(model.data_status),
        missing_count=model.missing_count,
    )


def _required_normalized_product_key(product: ProductSnapshot) -> str:
    normalized = product.normalized_product_key or normalize_product_identity(product.product_key)
    if normalized is None:
        raise ValueError("product snapshot requires a non-placeholder normalized identity")
    return normalized


def _assert_scope(product: ProductSnapshot, tenant_id: str, site_id: str, snapshot_id: str) -> None:
    if (product.tenant_id, product.site_id, product.snapshot_id) != (
        tenant_id,
        site_id,
        snapshot_id,
    ):
        raise ValueError("product snapshot scope mismatch")


def _audit(
    session: AsyncSession,
    *,
    tenant_id: str,
    event_type: str,
    resource_id: str,
    details: dict[str, object],
    created_at: datetime,
) -> None:
    session.add(
        AuditEventModel(
            tenant_id=tenant_id,
            event_id=str(uuid4()),
            event_type=event_type,
            resource_type="product_catalog_snapshot",
            resource_id=resource_id,
            details=details,
            created_at=created_at,
        )
    )
