from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.web_sync_job import (
    WebSyncJob,
    WebSyncJobItem,
    WebSyncJobItemStatus,
    WebSyncJobPhase,
    WebSyncJobReport,
    WebSyncJobStatus,
    WebSyncMode,
    WebSyncPolicySnapshot,
    WebSyncPublicationStatus,
    WebSyncTrigger,
    WebSyncValidatorSnapshot,
)
from app.domain.ports.web_sync_jobs import WebSyncSourceConfigVersionConflictError
from app.domain.rules.duplicate_product import (
    PRODUCT_IDENTITY_NORMALIZATION_VERSION,
    duplicate_product_policy_is_supported,
    normalize_product_identity,
)
from app.integrations.postgres.models import (
    AuditEventModel,
    SiteWebSourceConfigModel,
    SupportSiteModel,
    WebSyncJobItemModel,
    WebSyncJobModel,
    WebSyncProductIdentityDecisionModel,
)

_DUPLICATE_POLICY_VERSION = "duplicate-product-v2"


class PostgreSQLWebSyncJobStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def enqueue(
        self,
        job: WebSyncJob,
        items: tuple[WebSyncJobItem, ...] = (),
        *,
        expected_source_config_version: int = 0,
    ) -> tuple[WebSyncJob, bool]:
        async with self._session_factory.begin() as session:
            site = await session.scalar(
                select(SupportSiteModel)
                .where(
                    SupportSiteModel.tenant_id == job.tenant_id,
                    SupportSiteModel.site_id == job.site_id,
                )
                .with_for_update()
            )
            if site is None:
                raise WebSyncSourceConfigVersionConflictError(
                    "site no longer exists while validating sitemap source configuration"
                )
            config_version = await session.scalar(
                select(SiteWebSourceConfigModel.config_version).where(
                    SiteWebSourceConfigModel.tenant_id == job.tenant_id,
                    SiteWebSourceConfigModel.site_id == job.site_id,
                )
            )
            current_source_config_version = int(config_version or 0)
            if current_source_config_version != expected_source_config_version:
                raise WebSyncSourceConfigVersionConflictError(
                    "sitemap source configuration changed after preflight"
                )
            result = await session.execute(
                insert(WebSyncJobModel).values(**_model_values(job)).on_conflict_do_nothing()
            )
            if result.rowcount:
                session.add_all(_item_model(item) for item in items)
                session.add(
                    _audit(
                        job,
                        event_type="knowledge.web_sync.queued",
                        details={
                            "site_id": job.site_id,
                            "trigger": job.trigger.value,
                            "mode": job.mode.value,
                            "manifest_id": job.manifest_id,
                            "expected_count": job.expected_count,
                        },
                    )
                )
                return job, True
            existing = await session.scalar(
                select(WebSyncJobModel).where(
                    WebSyncJobModel.tenant_id == job.tenant_id,
                    or_(
                        WebSyncJobModel.idempotency_key == job.idempotency_key,
                        WebSyncJobModel.active_site_key == _active_site_key(job),
                    ),
                )
            )
            if existing is None:
                raise RuntimeError("website sync job conflict could not be resolved")
            return _to_domain(existing), False

    async def append_prepared_items(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        items: tuple[WebSyncJobItem, ...],
    ) -> WebSyncJob:
        async with self._session_factory.begin() as session:
            job = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.PREPARING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                raise RuntimeError("website sync preparation lease is no longer owned")
            if items:
                statement = insert(WebSyncJobItemModel).values(
                    [_item_values(item) for item in items]
                )
                await session.execute(
                    statement.on_conflict_do_nothing(index_elements=("tenant_id", "job_id", "url"))
                )
            prepared_count = await session.scalar(
                select(func.count())
                .select_from(WebSyncJobItemModel)
                .where(
                    WebSyncJobItemModel.tenant_id == tenant_id,
                    WebSyncJobItemModel.job_id == job_id,
                )
            )
            job.prepared_count = int(prepared_count or 0)
            if job.prepared_count > job.expected_count:
                raise RuntimeError("prepared page count exceeds the immutable manifest scope")
            job.prepare_stage = "copy_manifest_items"
            job.prepare_cursor = job.prepared_count
            _touch_job(job, progressed_at=datetime.now(UTC))
            await session.flush()
            return _to_domain(job)

    async def finish_preparation(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        manifest_fingerprint: str,
    ) -> WebSyncJob:
        async with self._session_factory.begin() as session:
            job = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.PREPARING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                raise RuntimeError("website sync preparation lease is no longer owned")
            if job.manifest_fingerprint != manifest_fingerprint:
                raise RuntimeError("immutable manifest fingerprint changed during preparation")
            if job.prepared_count != job.expected_count:
                raise RuntimeError(
                    "prepared page count does not match the immutable manifest scope"
                )
            job.status = WebSyncJobStatus.QUEUED.value
            job.phase = WebSyncJobPhase.QUEUED.value
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = datetime.now(UTC)
            job.attempt_count = 0
            job.failure_attempt_count = 0
            job.prepare_stage = "ready_for_processing"
            job.prepare_cursor = job.expected_count
            job.available_at = job.heartbeat_at
            _touch_job(job, progressed_at=job.heartbeat_at)
            session.add(
                _audit(
                    _to_domain(job),
                    event_type="knowledge.web_sync.prepared",
                    details={"prepared_count": job.prepared_count},
                )
            )
            await session.flush()
            return _to_domain(job)

    async def yield_preparation(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        stage: str,
        cursor: int,
        yielded_at: datetime,
    ) -> WebSyncJob:
        if stage not in {
            "copy_manifest_items",
            "extract_missing_identities",
            "apply_duplicate_policy",
        }:
            raise ValueError("unsupported website sync preparation stage")
        if cursor < 0:
            raise ValueError("website sync preparation cursor cannot be negative")
        async with self._session_factory.begin() as session:
            job = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.PREPARING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                raise RuntimeError("website sync preparation lease is no longer owned")
            job.phase = WebSyncJobPhase.PREPARING.value
            job.prepare_stage = stage
            job.prepare_cursor = cursor
            job.heartbeat_at = yielded_at
            job.available_at = yielded_at
            job.lease_owner = None
            job.lease_expires_at = None
            job.yield_count += 1
            _touch_job(job, progressed_at=yielded_at)
            session.add(
                _audit(
                    _to_domain(job),
                    event_type="knowledge.web_sync.yielded",
                    details={"stage": stage, "cursor": cursor, "yield": job.yield_count},
                )
            )
            await session.flush()
            return _to_domain(job)

    async def apply_duplicate_policy(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        policy: WebSyncPolicySnapshot,
    ) -> int:
        """Elect deterministic product winners before any page can write staging data."""
        cursor = 0
        excluded = 0
        complete = False
        while not complete:
            batch_excluded, cursor, complete = await self.apply_duplicate_policy_batch(
                tenant_id=tenant_id,
                job_id=job_id,
                worker_id=worker_id,
                policy=policy,
                cursor=cursor,
                limit=500,
            )
            excluded += batch_excluded
        return excluded

    async def apply_duplicate_policy_batch(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        policy: WebSyncPolicySnapshot,
        cursor: int,
        limit: int,
    ) -> tuple[int, int, bool]:
        """Apply first-wins to bounded identity groups with durable decisions."""

        if not duplicate_product_policy_is_supported(policy.duplicate_product_policy):
            raise ValueError("unsupported duplicate product policy")
        if policy.duplicate_product_order != "manifest_ordinal":
            raise ValueError("unsupported duplicate product ordering")
        if cursor < 0 or limit < 1:
            raise ValueError("duplicate policy cursor and limit must be positive")
        if policy.duplicate_product_policy != "first_wins":
            return 0, cursor, True

        async with self._session_factory.begin() as session:
            job = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.PREPARING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                raise RuntimeError("website sync preparation lease is no longer owned")
            keys = tuple(
                (
                    await session.scalars(
                        select(WebSyncJobItemModel.normalized_product_key)
                        .where(
                            WebSyncJobItemModel.tenant_id == tenant_id,
                            WebSyncJobItemModel.job_id == job_id,
                            WebSyncJobItemModel.normalized_product_key.is_not(None),
                            or_(
                                WebSyncJobItemModel.policy_version.is_(None),
                                WebSyncJobItemModel.policy_version != _DUPLICATE_POLICY_VERSION,
                            ),
                        )
                        .distinct()
                        .order_by(WebSyncJobItemModel.normalized_product_key)
                        .limit(limit + 1)
                    )
                ).all()
            )
            if not keys:
                await session.execute(
                    update(WebSyncJobItemModel)
                    .where(
                        WebSyncJobItemModel.tenant_id == tenant_id,
                        WebSyncJobItemModel.job_id == job_id,
                        WebSyncJobItemModel.normalized_product_key.is_(None),
                        WebSyncJobItemModel.policy_version.is_(None),
                    )
                    .values(policy_version=_DUPLICATE_POLICY_VERSION)
                )
                return 0, cursor, True

            has_more = len(keys) > limit
            process_keys = keys[:limit]
            excluded = 0
            for key in process_keys:
                candidates = list(
                    (
                        await session.scalars(
                            select(WebSyncJobItemModel)
                            .where(
                                WebSyncJobItemModel.tenant_id == tenant_id,
                                WebSyncJobItemModel.job_id == job_id,
                                WebSyncJobItemModel.normalized_product_key == key,
                            )
                            .order_by(
                                WebSyncJobItemModel.ordinal,
                                WebSyncJobItemModel.url,
                                WebSyncJobItemModel.item_id,
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                if not candidates:
                    raise RuntimeError("product identity candidate group disappeared")
                normalization_versions = {
                    candidate.normalization_version for candidate in candidates
                }
                if None in normalization_versions or len(normalization_versions) != 1:
                    raise RuntimeError("product_identity_normalization_version_mismatch")
                normalization_version = next(iter(normalization_versions))
                if normalization_version is None:
                    raise RuntimeError("product_identity_normalization_version_mismatch")
                decision = await session.scalar(
                    select(WebSyncProductIdentityDecisionModel)
                    .where(
                        WebSyncProductIdentityDecisionModel.tenant_id == tenant_id,
                        WebSyncProductIdentityDecisionModel.job_id == job_id,
                        WebSyncProductIdentityDecisionModel.normalized_product_key == key,
                    )
                    .with_for_update()
                )
                if decision is None:
                    candidate_urls = {candidate.url for candidate in candidates}
                    prior = await session.scalar(
                        select(WebSyncJobItemModel)
                        .join(
                            WebSyncJobModel,
                            and_(
                                WebSyncJobModel.tenant_id == WebSyncJobItemModel.tenant_id,
                                WebSyncJobModel.job_id == WebSyncJobItemModel.job_id,
                            ),
                        )
                        .where(
                            WebSyncJobItemModel.tenant_id == tenant_id,
                            WebSyncJobItemModel.site_id == job.site_id,
                            WebSyncJobItemModel.job_id != job_id,
                            WebSyncJobItemModel.normalized_product_key == key,
                            WebSyncJobItemModel.normalization_version == normalization_version,
                            WebSyncJobItemModel.url.in_(candidate_urls),
                            WebSyncJobModel.mode == WebSyncMode.PRODUCTION.value,
                            WebSyncJobModel.status == WebSyncJobStatus.SUCCEEDED.value,
                            WebSyncJobModel.publication_status
                            == WebSyncPublicationStatus.PUBLISHED.value,
                            WebSyncJobItemModel.status.in_(
                                (
                                    WebSyncJobItemStatus.SUCCEEDED.value,
                                    WebSyncJobItemStatus.NOT_MODIFIED.value,
                                )
                            ),
                        )
                        .order_by(
                            WebSyncJobModel.completed_at.desc(),
                            WebSyncJobItemModel.ordinal,
                            WebSyncJobItemModel.url,
                            WebSyncJobItemModel.item_id,
                        )
                        .limit(1)
                    )
                    winner = next(
                        (
                            candidate
                            for candidate in candidates
                            if prior and candidate.url == prior.url
                        ),
                        candidates[0],
                    )
                    now = datetime.now(UTC)
                    decision = WebSyncProductIdentityDecisionModel(
                        tenant_id=tenant_id,
                        job_id=job_id,
                        normalized_product_key=key,
                        winner_item_id=winner.item_id,
                        winner_url=winner.url,
                        state="selected",
                        policy_version=_DUPLICATE_POLICY_VERSION,
                        normalization_version=normalization_version,
                        decision_revision=1,
                        decision_reason=(
                            "prior_published_winner" if prior is not None else "manifest_ordinal"
                        ),
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(decision)
                winner = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.item_id == decision.winner_item_id
                    ),
                    None,
                )
                if (
                    winner is None
                    or winner.url != decision.winner_url
                    or decision.normalization_version != normalization_version
                    or decision.policy_version != _DUPLICATE_POLICY_VERSION
                ):
                    raise RuntimeError("duplicate_winner_invariant_violation")
                for candidate in candidates:
                    candidate.policy_version = _DUPLICATE_POLICY_VERSION
                    candidate.identity_source = "manifest_product_key"
                    candidate.winner_item_id = decision.winner_item_id
                    candidate.winner_url = decision.winner_url
                    if candidate.item_id == decision.winner_item_id:
                        continue
                    if candidate.status != WebSyncJobItemStatus.PENDING.value:
                        continue
                    candidate.status = WebSyncJobItemStatus.EXCLUDED.value
                    candidate.completed_at = datetime.now(UTC)
                    candidate.outcome_reason = "duplicate_product_first_wins"
                    candidate.error_code = candidate.outcome_reason
                    candidate.error_message = (
                        f"duplicate product excluded; winner={decision.winner_url}"
                    )
                    candidate.report_payload = _duplicate_exclusion_payload(
                        job_id=job_id,
                        product_key=candidate.product_key,
                        winner_url=decision.winner_url,
                    )
                    excluded += 1
            job.completed_count += excluded
            job.excluded_item_count += excluded
            _touch_job(job, progressed_at=datetime.now(UTC))
            await session.flush()
            next_cursor = cursor + len(process_keys)
            return excluded, next_cursor, not has_more

    async def promote_duplicate_fallbacks(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
    ) -> int:
        """Promote the next deterministic candidate when a winner reaches a terminal failure."""

        async with self._session_factory.begin() as session:
            job = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.RUNNING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                raise RuntimeError("website sync job lease is no longer owned")
            models = tuple(
                (
                    await session.scalars(
                        select(WebSyncJobItemModel)
                        .where(
                            WebSyncJobItemModel.tenant_id == tenant_id,
                            WebSyncJobItemModel.job_id == job_id,
                        )
                        .order_by(
                            WebSyncJobItemModel.ordinal,
                            WebSyncJobItemModel.url,
                            WebSyncJobItemModel.item_id,
                        )
                        .with_for_update()
                    )
                ).all()
            )
            groups: dict[str, list[WebSyncJobItemModel]] = {}
            for model in models:
                key = model.normalized_product_key or normalize_product_identity(model.product_key)
                if key is not None:
                    groups.setdefault(key, []).append(model)
            promoted = 0
            for key, candidates in groups.items():
                if len(candidates) < 2:
                    continue
                decision = await session.scalar(
                    select(WebSyncProductIdentityDecisionModel)
                    .where(
                        WebSyncProductIdentityDecisionModel.tenant_id == tenant_id,
                        WebSyncProductIdentityDecisionModel.job_id == job_id,
                        WebSyncProductIdentityDecisionModel.normalized_product_key == key,
                    )
                    .with_for_update()
                )
                if decision is None:
                    raise RuntimeError("duplicate_winner_invariant_violation")
                candidate_normalization_versions = {
                    item.normalization_version for item in candidates
                }
                if (
                    decision.policy_version != _DUPLICATE_POLICY_VERSION
                    or candidate_normalization_versions != {decision.normalization_version}
                ):
                    raise RuntimeError("duplicate_winner_invariant_violation")
                winner_id = decision.winner_item_id
                winner = next((item for item in candidates if item.item_id == winner_id), None)
                if winner is None or winner.status not in {
                    WebSyncJobItemStatus.FAILED.value,
                    WebSyncJobItemStatus.EXCLUDED.value,
                }:
                    continue
                if winner.outcome_reason == "duplicate_product_first_wins":
                    continue
                if any(
                    item.status
                    in {
                        WebSyncJobItemStatus.SUCCEEDED.value,
                        WebSyncJobItemStatus.NOT_MODIFIED.value,
                    }
                    and item.item_id != winner.item_id
                    for item in candidates
                ):
                    continue
                candidate = next(
                    (
                        item
                        for item in candidates
                        if item.status == WebSyncJobItemStatus.EXCLUDED.value
                        and item.outcome_reason == "duplicate_product_first_wins"
                    ),
                    None,
                )
                if candidate is None:
                    continue
                candidate.status = WebSyncJobItemStatus.PENDING.value
                candidate.completed_at = None
                candidate.error_code = None
                candidate.error_message = None
                candidate.outcome_reason = None
                candidate.report_payload = {}
                decision.winner_item_id = candidate.item_id
                decision.winner_url = candidate.url
                decision.state = "promoted"
                decision.decision_revision += 1
                decision.decision_reason = "winner_terminal_failure"
                decision.updated_at = datetime.now(UTC)
                for item in candidates:
                    item.winner_item_id = candidate.item_id
                    item.winner_url = candidate.url
                candidate.policy_version = _DUPLICATE_POLICY_VERSION
                job.completed_count = max(0, job.completed_count - 1)
                job.excluded_item_count = max(0, job.excluded_item_count - 1)
                session.add(
                    _audit(
                        _to_domain(job),
                        event_type="knowledge.web_sync.product_identity_promoted",
                        details={
                            "normalized_product_key": key,
                            "previous_winner_item_id": winner_id,
                            "winner_item_id": candidate.item_id,
                            "decision_revision": decision.decision_revision,
                            "decision_reason": decision.decision_reason,
                        },
                    )
                )
                promoted += 1
            if promoted:
                _touch_job(job, progressed_at=datetime.now(UTC))
            await session.flush()
            return promoted

    async def update_item_identity(
        self,
        *,
        tenant_id: str,
        job_id: str,
        item_id: str,
        worker_id: str,
        validator: WebSyncJobItem,
    ) -> None:
        async with self._session_factory.begin() as session:
            job = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.PREPARING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                raise RuntimeError("website sync preparation lease is no longer owned")
            model = await session.scalar(
                select(WebSyncJobItemModel)
                .where(
                    WebSyncJobItemModel.tenant_id == tenant_id,
                    WebSyncJobItemModel.job_id == job_id,
                    WebSyncJobItemModel.item_id == item_id,
                    WebSyncJobItemModel.status == WebSyncJobItemStatus.PENDING.value,
                )
                .with_for_update()
            )
            if model is None:
                raise RuntimeError("website sync identity item is no longer pending")
            model.document_id = validator.document_id
            model.canonical_url = validator.canonical_url
            model.final_url = validator.final_url
            model.etag = validator.etag
            model.last_modified = validator.last_modified
            model.product_key = validator.product_key
            model.normalized_product_key = (
                validator.normalized_product_key
                or normalize_product_identity(validator.product_key)
            )
            model.normalization_version = validator.normalization_version or (
                PRODUCT_IDENTITY_NORMALIZATION_VERSION
                if model.normalized_product_key is not None
                else None
            )
            _touch_job(job, progressed_at=datetime.now(UTC))
            await session.flush()

    async def requeue_incomplete(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        requeued_at: datetime,
    ) -> WebSyncJob:
        async with self._session_factory.begin() as session:
            job = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.RUNNING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                raise RuntimeError("website sync job lease is no longer owned by this worker")
            if job.completed_count >= job.expected_count:
                raise RuntimeError("completed website sync job cannot be requeued")
            job.status = WebSyncJobStatus.QUEUED.value
            job.phase = WebSyncJobPhase.QUEUED.value
            job.heartbeat_at = requeued_at
            job.available_at = requeued_at
            job.lease_owner = None
            job.lease_expires_at = None
            job.yield_count += 1
            _touch_job(job, progressed_at=requeued_at)
            await session.flush()
            return _to_domain(job)

    async def get(self, *, tenant_id: str, job_id: str) -> WebSyncJob | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(WebSyncJobModel).where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                )
            )
        return None if model is None else _to_domain(model)

    async def list_recent(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        limit: int,
    ) -> tuple[WebSyncJob, ...]:
        statement = select(WebSyncJobModel).where(WebSyncJobModel.tenant_id == tenant_id)
        if site_id:
            statement = statement.where(WebSyncJobModel.site_id == site_id)
        statement = statement.order_by(WebSyncJobModel.requested_at.desc()).limit(limit)
        async with self._session_factory() as session:
            models = tuple((await session.scalars(statement)).all())
        return tuple(_to_domain(model) for model in models)

    async def claim_next(
        self,
        *,
        tenant_id: str,
        worker_id: str,
        claimed_at: datetime,
        lease_expires_at: datetime,
    ) -> WebSyncJob | None:
        async with self._session_factory.begin() as session:
            expired_preparation = and_(
                WebSyncJobModel.status == WebSyncJobStatus.PREPARING.value,
                WebSyncJobModel.lease_expires_at < claimed_at,
                WebSyncJobModel.failure_attempt_count >= WebSyncJobModel.max_attempts,
            )
            expired_running = and_(
                WebSyncJobModel.status == WebSyncJobStatus.RUNNING.value,
                WebSyncJobModel.lease_expires_at < claimed_at,
                WebSyncJobModel.failure_attempt_count >= WebSyncJobModel.max_attempts,
            )
            await session.execute(
                update(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.mode == WebSyncMode.PRODUCTION.value,
                    or_(expired_preparation, expired_running),
                )
                .values(
                    status=WebSyncJobStatus.CLEANUP_PENDING.value,
                    phase=WebSyncJobPhase.AWAITING_REMEDIATION.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    available_at=claimed_at,
                    error_code="staging_cleanup_required",
                    error_message=(
                        "worker lease expired after retry limit; external staging must be "
                        "aborted before the task can be finalized"
                    ),
                    updated_at=claimed_at,
                    last_progress_at=claimed_at,
                    state_version=WebSyncJobModel.state_version + 1,
                )
            )
            await session.execute(
                update(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.mode != WebSyncMode.PRODUCTION.value,
                    or_(expired_preparation, expired_running),
                )
                .values(
                    status=WebSyncJobStatus.FAILED.value,
                    phase=WebSyncJobPhase.COMPLETED.value,
                    active_site_key=None,
                    lease_owner=None,
                    lease_expires_at=None,
                    completed_at=claimed_at,
                    error_code="worker_lease_exhausted",
                    error_message="worker lease expired and the retry limit was reached",
                    updated_at=claimed_at,
                    last_progress_at=claimed_at,
                    state_version=WebSyncJobModel.state_version + 1,
                )
            )
            due_item_exists = exists().where(
                WebSyncJobItemModel.tenant_id == WebSyncJobModel.tenant_id,
                WebSyncJobItemModel.job_id == WebSyncJobModel.job_id,
                or_(
                    and_(
                        WebSyncJobItemModel.status == WebSyncJobItemStatus.PENDING.value,
                        or_(
                            WebSyncJobItemModel.next_attempt_at.is_(None),
                            WebSyncJobItemModel.next_attempt_at <= claimed_at,
                        ),
                    ),
                    and_(
                        WebSyncJobItemModel.status == WebSyncJobItemStatus.FETCHING.value,
                        WebSyncJobItemModel.lease_expires_at < claimed_at,
                        WebSyncJobItemModel.attempt_count < WebSyncJobItemModel.max_attempts,
                    ),
                ),
            )
            candidate = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    or_(
                        and_(
                            WebSyncJobModel.status == WebSyncJobStatus.PREPARING.value,
                            or_(
                                WebSyncJobModel.lease_expires_at.is_(None),
                                WebSyncJobModel.lease_expires_at < claimed_at,
                            ),
                            or_(
                                WebSyncJobModel.available_at.is_(None),
                                WebSyncJobModel.available_at <= claimed_at,
                            ),
                            WebSyncJobModel.failure_attempt_count < WebSyncJobModel.max_attempts,
                        ),
                        and_(
                            WebSyncJobModel.status == WebSyncJobStatus.QUEUED.value,
                            or_(
                                WebSyncJobModel.available_at.is_(None),
                                WebSyncJobModel.available_at <= claimed_at,
                            ),
                            or_(
                                WebSyncJobModel.phase == WebSyncJobPhase.FINALIZING.value,
                                WebSyncJobModel.completed_count >= WebSyncJobModel.expected_count,
                                due_item_exists,
                            ),
                        ),
                        and_(
                            WebSyncJobModel.status == WebSyncJobStatus.RUNNING.value,
                            WebSyncJobModel.lease_expires_at < claimed_at,
                            or_(
                                WebSyncJobModel.failure_attempt_count
                                < WebSyncJobModel.max_attempts,
                                WebSyncJobModel.phase == WebSyncJobPhase.FINALIZING.value,
                            ),
                        ),
                        and_(
                            WebSyncJobModel.status == WebSyncJobStatus.BLOCKED.value,
                            or_(
                                WebSyncJobModel.cancel_requested_at.is_not(None),
                                WebSyncJobModel.retention_expires_at < claimed_at,
                            ),
                        ),
                        and_(
                            WebSyncJobModel.status == WebSyncJobStatus.CLEANUP_PENDING.value,
                            or_(
                                WebSyncJobModel.available_at.is_(None),
                                WebSyncJobModel.available_at <= claimed_at,
                            ),
                        ),
                    ),
                )
                .order_by(
                    func.coalesce(
                        WebSyncJobModel.available_at,
                        WebSyncJobModel.requested_at,
                    ),
                    WebSyncJobModel.requested_at,
                    WebSyncJobModel.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if candidate is None:
                return None
            if (
                candidate.status == WebSyncJobStatus.BLOCKED.value
                and candidate.cancel_requested_at is None
            ):
                candidate.cancel_requested_at = claimed_at
            recovering_expired_lease = (
                candidate.status
                in {
                    WebSyncJobStatus.PREPARING.value,
                    WebSyncJobStatus.RUNNING.value,
                }
                and candidate.lease_expires_at is not None
                and candidate.lease_expires_at < claimed_at
            )
            cleanup_pending = candidate.status == WebSyncJobStatus.CLEANUP_PENDING.value
            preparing = candidate.status == WebSyncJobStatus.PREPARING.value
            if cleanup_pending:
                candidate.phase = WebSyncJobPhase.AWAITING_REMEDIATION.value
            elif not preparing:
                candidate.status = WebSyncJobStatus.RUNNING.value
            if preparing:
                candidate.phase = WebSyncJobPhase.PREPARING.value
            elif candidate.cancel_requested_at is not None:
                candidate.phase = WebSyncJobPhase.AWAITING_REMEDIATION.value
            elif candidate.phase != WebSyncJobPhase.FINALIZING.value:
                candidate.phase = WebSyncJobPhase.PROCESSING.value
            candidate.attempt_count += 1
            candidate.claim_count += 1
            if recovering_expired_lease:
                candidate.failure_attempt_count += 1
            candidate.started_at = candidate.started_at or claimed_at
            candidate.heartbeat_at = claimed_at
            candidate.available_at = None
            candidate.lease_owner = worker_id
            candidate.lease_expires_at = lease_expires_at
            if not cleanup_pending:
                candidate.error_code = None
                candidate.error_message = None
            _touch_job(candidate)
            session.add(
                _audit(
                    _to_domain(candidate),
                    event_type=(
                        "knowledge.web_sync.cleanup_pending"
                        if cleanup_pending
                        else "knowledge.web_sync.preparing"
                        if preparing
                        else "knowledge.web_sync.running"
                    ),
                    details={
                        "worker_id": worker_id,
                        "claim": candidate.claim_count,
                        "failure_attempt": candidate.failure_attempt_count,
                    },
                )
            )
            await session.flush()
            return _to_domain(candidate)

    async def heartbeat(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                update(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status.in_(
                        (
                            WebSyncJobStatus.PREPARING.value,
                            WebSyncJobStatus.RUNNING.value,
                            WebSyncJobStatus.CLEANUP_PENDING.value,
                        )
                    ),
                    WebSyncJobModel.lease_owner == worker_id,
                    # A paused or partitioned worker cannot resurrect a lease
                    # after another scheduler is entitled to reclaim it.
                    WebSyncJobModel.lease_expires_at > heartbeat_at,
                )
                .values(
                    heartbeat_at=heartbeat_at,
                    lease_expires_at=lease_expires_at,
                    updated_at=heartbeat_at,
                    state_version=WebSyncJobModel.state_version + 1,
                )
            )
        return bool(result.rowcount)

    async def request_cancel(
        self,
        *,
        tenant_id: str,
        job_id: str,
        requested_by: str,
        requested_at: datetime,
    ) -> WebSyncJob:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                )
                .with_for_update()
            )
            if model is None:
                raise LookupError("website sync job was not found")
            if model.status in {
                WebSyncJobStatus.SUCCEEDED.value,
                WebSyncJobStatus.FAILED.value,
                WebSyncJobStatus.CANCELED.value,
            }:
                return _to_domain(model)
            if (
                model.mode == WebSyncMode.PRODUCTION.value
                and model.phase == WebSyncJobPhase.FINALIZING.value
            ):
                raise ValueError("production synchronization cannot be canceled while finalizing")
            model.cancel_requested_at = model.cancel_requested_at or requested_at
            if model.status in {
                WebSyncJobStatus.PREPARING.value,
                WebSyncJobStatus.QUEUED.value,
            }:
                canceled = await session.execute(
                    update(WebSyncJobItemModel)
                    .where(
                        WebSyncJobItemModel.tenant_id == tenant_id,
                        WebSyncJobItemModel.job_id == job_id,
                        WebSyncJobItemModel.status == WebSyncJobItemStatus.PENDING.value,
                    )
                    .values(
                        status=WebSyncJobItemStatus.CANCELED.value,
                        completed_at=requested_at,
                    )
                )
                model.status = WebSyncJobStatus.CANCELED.value
                model.phase = WebSyncJobPhase.COMPLETED.value
                model.completed_at = requested_at
                model.completed_count += int(canceled.rowcount or 0)
                model.canceled_item_count += int(canceled.rowcount or 0)
                model.active_site_key = None
                model.publication_status = _canceled_publication_status(model.mode)
            _touch_job(model, progressed_at=requested_at)
            session.add(
                _audit(
                    _to_domain(model),
                    event_type="knowledge.web_sync.cancel_requested",
                    details={"requested_by": requested_by},
                )
            )
            await session.flush()
            return _to_domain(model)

    async def cancel(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        canceled_at: datetime,
    ) -> WebSyncJob:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status.in_(
                        (
                            WebSyncJobStatus.PREPARING.value,
                            WebSyncJobStatus.RUNNING.value,
                        )
                    ),
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if model is None:
                raise RuntimeError("website sync job lease is no longer owned by this worker")
            canceled = await session.execute(
                update(WebSyncJobItemModel)
                .where(
                    WebSyncJobItemModel.tenant_id == tenant_id,
                    WebSyncJobItemModel.job_id == job_id,
                    WebSyncJobItemModel.status.in_(
                        (
                            WebSyncJobItemStatus.PENDING.value,
                            WebSyncJobItemStatus.FETCHING.value,
                        )
                    ),
                )
                .values(
                    status=WebSyncJobItemStatus.CANCELED.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    completed_at=canceled_at,
                )
            )
            count = int(canceled.rowcount or 0)
            model.status = WebSyncJobStatus.CANCELED.value
            model.phase = WebSyncJobPhase.COMPLETED.value
            model.completed_at = canceled_at
            model.heartbeat_at = canceled_at
            model.completed_count += count
            model.canceled_item_count += count
            model.lease_owner = None
            model.lease_expires_at = None
            model.active_site_key = None
            model.publication_status = _canceled_publication_status(model.mode)
            _touch_job(model, progressed_at=canceled_at)
            session.add(
                _audit(
                    _to_domain(model),
                    event_type="knowledge.web_sync.canceled",
                    details={"canceled_items": count},
                )
            )
            await session.flush()
            return _to_domain(model)

    async def block(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        report: WebSyncJobReport,
        blocked_at: datetime,
        retention_expires_at: datetime,
    ) -> WebSyncJob:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.RUNNING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if model is None:
                raise RuntimeError("website sync job lease is no longer owned by this worker")
            model.status = WebSyncJobStatus.BLOCKED.value
            model.phase = WebSyncJobPhase.AWAITING_REMEDIATION.value
            model.publication_status = WebSyncPublicationStatus.PENDING.value
            model.report_payload = _report_payload(report)
            model.error_code = "remediation_required"
            model.error_message = "staged snapshot is retained for failed-page remediation"
            model.blocked_at = blocked_at
            model.retention_expires_at = retention_expires_at
            model.heartbeat_at = blocked_at
            model.lease_owner = None
            model.lease_expires_at = None
            _touch_job(model, progressed_at=blocked_at)
            session.add(
                _audit(
                    _to_domain(model),
                    event_type="knowledge.web_sync.blocked",
                    details={
                        "failed_count": report.failed_count,
                        "retention_expires_at": retention_expires_at.isoformat(),
                    },
                )
            )
            await session.flush()
            return _to_domain(model)

    async def retry_blocked(
        self,
        *,
        tenant_id: str,
        job_id: str,
        requested_by: str,
        requested_at: datetime,
    ) -> WebSyncJob:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                )
                .with_for_update()
            )
            if model is None:
                raise LookupError("website sync job was not found")
            if model.status != WebSyncJobStatus.BLOCKED.value:
                raise ValueError("only blocked website sync jobs can be retried")
            if model.retention_expires_at is None or model.retention_expires_at <= requested_at:
                raise ValueError("staged snapshot retention has expired")
            report = _to_report(model.report_payload)
            identity_conflict_failure = bool(
                await session.scalar(
                    select(func.count(WebSyncJobItemModel.id)).where(
                        WebSyncJobItemModel.tenant_id == tenant_id,
                        WebSyncJobItemModel.job_id == job_id,
                        WebSyncJobItemModel.status == WebSyncJobItemStatus.FAILED.value,
                        WebSyncJobItemModel.error_code == "ProductIdentityConflictError",
                    )
                )
            )
            identity_reconciliation_required = (
                bool(
                    report
                    and any(
                        reason
                        in {
                            "unresolved_product_identity",
                            "duplicate_winner_invariant_violation",
                        }
                        for reason in report.publication_block_reasons
                    )
                )
                or identity_conflict_failure
            )
            if identity_reconciliation_required:
                raise ValueError(
                    "product identity reconciliation is required; abandon the retained staging "
                    "snapshot and enqueue a fresh manifest-bound job"
                )
            failed_count = model.failed_item_count
            retryable = await session.execute(
                update(WebSyncJobItemModel)
                .where(
                    WebSyncJobItemModel.tenant_id == tenant_id,
                    WebSyncJobItemModel.job_id == job_id,
                    WebSyncJobItemModel.status == WebSyncJobItemStatus.FAILED.value,
                )
                .values(
                    status=WebSyncJobItemStatus.PENDING.value,
                    attempt_count=0,
                    lease_owner=None,
                    lease_expires_at=None,
                    completed_at=None,
                    duration_ms=None,
                    report_payload={},
                    error_code=None,
                    error_message=None,
                    next_attempt_at=None,
                    outcome_reason=None,
                )
            )
            retry_count = int(retryable.rowcount or 0)
            finalization_only = (
                retry_count == 0
                and report is not None
                and (report.failed_count == 0 and "finalization" in report.errors)
            )
            if retry_count == 0 and not finalization_only:
                raise ValueError("blocked website sync job has no retryable pages")
            model.completed_count = max(0, model.completed_count - retry_count)
            model.failed_item_count = max(0, model.failed_item_count - failed_count)
            model.status = WebSyncJobStatus.QUEUED.value
            model.phase = (
                WebSyncJobPhase.FINALIZING.value
                if finalization_only
                else WebSyncJobPhase.QUEUED.value
            )
            model.publication_status = WebSyncPublicationStatus.PENDING.value
            model.report_payload = {}
            model.error_code = None
            model.error_message = None
            model.cancel_requested_at = None
            model.blocked_at = None
            model.retention_expires_at = None
            model.attempt_count = 0
            model.failure_attempt_count = 0
            model.available_at = requested_at
            _touch_job(model, progressed_at=requested_at)
            session.add(
                _audit(
                    _to_domain(model),
                    event_type="knowledge.web_sync.retry_requested",
                    details={
                        "requested_by": requested_by,
                        "retryable_items": retry_count,
                        "finalization_only": finalization_only,
                    },
                )
            )
            await session.flush()
            return _to_domain(model)

    async def list_referenced_version_ids(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> tuple[str, ...]:
        async with self._session_factory() as session:
            return tuple(
                dict.fromkeys(
                    str(value)
                    for value in (
                        await session.scalars(
                            select(WebSyncJobItemModel.version_id).where(
                                WebSyncJobItemModel.tenant_id == tenant_id,
                                WebSyncJobItemModel.job_id == job_id,
                                WebSyncJobItemModel.version_id.is_not(None),
                            )
                        )
                    ).all()
                    if value
                )
            )

    async def reconcile_stale_versions(
        self,
        *,
        tenant_id: str,
        job_id: str,
        version_ids: tuple[str, ...],
        requested_by: str,
        requested_at: datetime,
    ) -> WebSyncJob:
        target_ids = tuple(dict.fromkeys(version_ids))
        if not target_ids:
            raise ValueError("stale version reconciliation requires at least one version")
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                )
                .with_for_update()
            )
            if model is None:
                raise LookupError("website sync job was not found")
            if model.status != WebSyncJobStatus.BLOCKED.value:
                raise ValueError("only blocked website sync jobs can be reconciled")
            if model.retention_expires_at is None or model.retention_expires_at <= requested_at:
                raise ValueError("staged snapshot retention has expired")
            report = _to_report(model.report_payload)
            if report is None or "stale_version_reference" not in report.publication_block_reasons:
                raise ValueError("website sync job does not require stale version reconciliation")
            items = tuple(
                (
                    await session.scalars(
                        select(WebSyncJobItemModel)
                        .where(
                            WebSyncJobItemModel.tenant_id == tenant_id,
                            WebSyncJobItemModel.job_id == job_id,
                            WebSyncJobItemModel.version_id.in_(target_ids),
                            WebSyncJobItemModel.status.in_(
                                (
                                    WebSyncJobItemStatus.SUCCEEDED.value,
                                    WebSyncJobItemStatus.NOT_MODIFIED.value,
                                )
                            ),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            if not items:
                raise ValueError("stale version references are no longer attached to the job")
            succeeded_count = sum(
                item.status == WebSyncJobItemStatus.SUCCEEDED.value for item in items
            )
            not_modified_count = len(items) - succeeded_count
            reconciled_ids: list[str] = []
            for item in items:
                if item.version_id:
                    reconciled_ids.append(item.version_id)
                item.status = WebSyncJobItemStatus.PENDING.value
                item.attempt_count = 0
                item.lease_owner = None
                item.lease_expires_at = None
                item.next_attempt_at = None
                item.started_at = None
                item.completed_at = None
                item.duration_ms = None
                item.version_id = None
                item.etag = None
                item.last_modified = None
                item.report_payload = {}
                item.error_code = None
                item.error_message = None
                item.outcome_reason = None
            model.completed_count = max(0, model.completed_count - len(items))
            model.succeeded_count = max(0, model.succeeded_count - succeeded_count)
            model.not_modified_count = max(0, model.not_modified_count - not_modified_count)
            model.status = WebSyncJobStatus.QUEUED.value
            model.phase = WebSyncJobPhase.QUEUED.value
            model.publication_status = WebSyncPublicationStatus.PENDING.value
            model.report_payload = {}
            model.error_code = None
            model.error_message = None
            model.cancel_requested_at = None
            model.blocked_at = None
            model.retention_expires_at = None
            model.attempt_count = 0
            model.failure_attempt_count = 0
            model.available_at = requested_at
            _touch_job(model, progressed_at=requested_at)
            session.add(
                _audit(
                    _to_domain(model),
                    event_type="knowledge.web_sync.stale_versions_reconciled",
                    details={
                        "requested_by": requested_by,
                        "requeued_items": len(items),
                        "version_ids": list(dict.fromkeys(reconciled_ids))[:20],
                    },
                )
            )
            await session.flush()
            return _to_domain(model)

    async def claim_next_item(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        claimed_at: datetime,
        lease_expires_at: datetime,
    ) -> WebSyncJobItem | None:
        async with self._session_factory.begin() as session:
            job = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.RUNNING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                raise RuntimeError("website sync job lease is no longer owned by this worker")
            exhausted = await session.execute(
                update(WebSyncJobItemModel)
                .where(
                    WebSyncJobItemModel.tenant_id == tenant_id,
                    WebSyncJobItemModel.job_id == job_id,
                    WebSyncJobItemModel.status == WebSyncJobItemStatus.FETCHING.value,
                    WebSyncJobItemModel.lease_expires_at < claimed_at,
                    WebSyncJobItemModel.attempt_count >= WebSyncJobItemModel.max_attempts,
                )
                .values(
                    status=WebSyncJobItemStatus.FAILED.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    completed_at=claimed_at,
                    error_code="item_lease_exhausted",
                    error_message="page lease expired and the retry limit was reached",
                )
            )
            exhausted_count = int(exhausted.rowcount or 0)
            if exhausted_count:
                job.completed_count += exhausted_count
                job.failed_item_count += exhausted_count
                _touch_job(job, progressed_at=claimed_at)
            candidate = await session.scalar(
                select(WebSyncJobItemModel)
                .where(
                    WebSyncJobItemModel.tenant_id == tenant_id,
                    WebSyncJobItemModel.job_id == job_id,
                    or_(
                        and_(
                            WebSyncJobItemModel.status == WebSyncJobItemStatus.PENDING.value,
                            or_(
                                WebSyncJobItemModel.next_attempt_at.is_(None),
                                WebSyncJobItemModel.next_attempt_at <= claimed_at,
                            ),
                        ),
                        and_(
                            WebSyncJobItemModel.status == WebSyncJobItemStatus.FETCHING.value,
                            WebSyncJobItemModel.lease_expires_at < claimed_at,
                            WebSyncJobItemModel.attempt_count < WebSyncJobItemModel.max_attempts,
                        ),
                    ),
                )
                .order_by(WebSyncJobItemModel.ordinal)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if candidate is None:
                return None
            candidate.status = WebSyncJobItemStatus.FETCHING.value
            candidate.attempt_count += 1
            candidate.started_at = candidate.started_at or claimed_at
            candidate.lease_owner = worker_id
            candidate.lease_expires_at = lease_expires_at
            candidate.error_code = None
            candidate.error_message = None
            candidate.next_attempt_at = None
            _touch_job(job)
            await session.flush()
            return _item_to_domain(candidate)

    async def complete_item(
        self,
        *,
        tenant_id: str,
        job_id: str,
        item_id: str,
        worker_id: str,
        status: WebSyncJobItemStatus,
        report: WebSyncJobReport,
        validator: WebSyncJobItem | None,
        duration_ms: int,
        completed_at: datetime,
    ) -> WebSyncJobItem:
        if status not in {
            WebSyncJobItemStatus.SUCCEEDED,
            WebSyncJobItemStatus.NOT_MODIFIED,
            WebSyncJobItemStatus.EXCLUDED,
        }:
            raise ValueError("item completion requires a successful terminal status")
        async with self._session_factory.begin() as session:
            # Every job/item mutation uses the same lock order. The current
            # per-claim owner is the fencing token after lease recovery.
            job = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.RUNNING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                raise RuntimeError("website sync job lease is no longer owned by this worker")
            model = await session.scalar(
                select(WebSyncJobItemModel)
                .where(
                    WebSyncJobItemModel.tenant_id == tenant_id,
                    WebSyncJobItemModel.job_id == job_id,
                    WebSyncJobItemModel.item_id == item_id,
                    WebSyncJobItemModel.status == WebSyncJobItemStatus.FETCHING.value,
                    WebSyncJobItemModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if model is None:
                raise RuntimeError("website sync item lease is no longer owned by this worker")
            # The job lock serializes canonical completion checks across pages.
            effective_status = status
            if status is WebSyncJobItemStatus.SUCCEEDED and validator is not None:
                duplicate = await session.scalar(
                    select(WebSyncJobItemModel.item_id)
                    .where(
                        WebSyncJobItemModel.tenant_id == tenant_id,
                        WebSyncJobItemModel.job_id == job_id,
                        WebSyncJobItemModel.item_id != item_id,
                        WebSyncJobItemModel.canonical_url == validator.canonical_url,
                        WebSyncJobItemModel.status.in_(
                            (
                                WebSyncJobItemStatus.SUCCEEDED.value,
                                WebSyncJobItemStatus.NOT_MODIFIED.value,
                            )
                        ),
                    )
                    .limit(1)
                )
                if duplicate is not None:
                    effective_status = WebSyncJobItemStatus.EXCLUDED
                    report = replace(
                        report,
                        document_count=0,
                        changed_document_count=0,
                        indexed_chunk_count=0,
                        product_count=0,
                        excluded_count=report.excluded_count + 1,
                        duplicate_count=report.duplicate_count + 1,
                        errors={
                            **report.errors,
                            model.url: "excluded: canonical_duplicate",
                        },
                    )
            model.status = effective_status.value
            model.report_payload = _report_payload(report)
            model.duration_ms = max(0, duration_ms)
            model.completed_at = completed_at
            model.lease_owner = None
            model.lease_expires_at = None
            model.next_attempt_at = None
            model.outcome_reason = _outcome_reason(effective_status, report)
            if effective_status is WebSyncJobItemStatus.EXCLUDED:
                model.error_code = model.outcome_reason
                model.error_message = next(
                    iter(report.errors.values()),
                    "page was excluded by the crawl policy",
                )
            if validator is not None:
                model.document_id = validator.document_id
                model.version_id = validator.version_id
                model.canonical_url = validator.canonical_url
                model.final_url = validator.final_url
                model.etag = validator.etag
                model.last_modified = validator.last_modified
                model.product_key = validator.product_key
            job.completed_count += 1
            if effective_status is WebSyncJobItemStatus.SUCCEEDED:
                job.succeeded_count += 1
            elif effective_status is WebSyncJobItemStatus.NOT_MODIFIED:
                job.not_modified_count += 1
            else:
                job.excluded_item_count += 1
            _touch_job(job, progressed_at=completed_at)
            await session.flush()
            return _item_to_domain(model)

    async def fail_item(
        self,
        *,
        tenant_id: str,
        job_id: str,
        item_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        duration_ms: int,
        failed_at: datetime,
        next_attempt_at: datetime | None = None,
        terminal: bool | None = None,
    ) -> WebSyncJobItem:
        async with self._session_factory.begin() as session:
            job = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.RUNNING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                raise RuntimeError("website sync job lease is no longer owned by this worker")
            model = await session.scalar(
                select(WebSyncJobItemModel)
                .where(
                    WebSyncJobItemModel.tenant_id == tenant_id,
                    WebSyncJobItemModel.job_id == job_id,
                    WebSyncJobItemModel.item_id == item_id,
                    WebSyncJobItemModel.status == WebSyncJobItemStatus.FETCHING.value,
                    WebSyncJobItemModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if model is None:
                raise RuntimeError("website sync item lease is no longer owned by this worker")
            is_terminal = (
                model.attempt_count >= model.max_attempts if terminal is None else terminal
            )
            model.status = (
                WebSyncJobItemStatus.FAILED.value
                if is_terminal
                else WebSyncJobItemStatus.PENDING.value
            )
            model.duration_ms = max(0, duration_ms)
            model.completed_at = failed_at if is_terminal else None
            model.lease_owner = None
            model.lease_expires_at = None
            model.next_attempt_at = None if is_terminal else next_attempt_at
            model.error_code = error_code
            model.error_message = error_message
            model.outcome_reason = "failed" if is_terminal else None
            if is_terminal:
                job.completed_count += 1
                job.failed_item_count += 1
            _touch_job(job, progressed_at=failed_at)
            await session.flush()
            return _item_to_domain(model)

    async def list_items(
        self,
        *,
        tenant_id: str,
        job_id: str,
        status: WebSyncJobItemStatus | None,
        limit: int,
        offset: int = 0,
    ) -> tuple[WebSyncJobItem, ...]:
        statement = select(WebSyncJobItemModel).where(
            WebSyncJobItemModel.tenant_id == tenant_id,
            WebSyncJobItemModel.job_id == job_id,
        )
        if status is not None:
            statement = statement.where(WebSyncJobItemModel.status == status.value)
        statement = (
            statement.order_by(WebSyncJobItemModel.ordinal).offset(max(0, offset)).limit(limit)
        )
        async with self._session_factory() as session:
            models = tuple((await session.scalars(statement)).all())
        return tuple(_item_to_domain(model) for model in models)

    async def aggregate_report(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> WebSyncJobReport:
        metric_names = (
            "discovered_count",
            "document_count",
            "changed_document_count",
            "unchanged_document_count",
            "http_not_modified_count",
            "duplicate_count",
            "pending_removal_count",
            "expired_count",
            "indexed_chunk_count",
            "excluded_count",
        )
        filters = (
            WebSyncJobItemModel.tenant_id == tenant_id,
            WebSyncJobItemModel.job_id == job_id,
        )
        async with self._session_factory() as session:
            totals = (
                await session.execute(
                    select(
                        *(
                            func.coalesce(
                                func.sum(WebSyncJobItemModel.report_payload[name].as_integer()),
                                0,
                            ).label(name)
                            for name in metric_names
                        ),
                        func.count()
                        .filter(WebSyncJobItemModel.status == WebSyncJobItemStatus.FAILED.value)
                        .label("failed_count"),
                    ).where(*filters)
                )
            ).one()
            all_models = tuple(
                (
                    await session.scalars(
                        select(WebSyncJobItemModel)
                        .where(*filters)
                        .order_by(WebSyncJobItemModel.ordinal)
                    )
                ).all()
            )
            decisions = tuple(
                (
                    await session.scalars(
                        select(WebSyncProductIdentityDecisionModel).where(
                            WebSyncProductIdentityDecisionModel.tenant_id == tenant_id,
                            WebSyncProductIdentityDecisionModel.job_id == job_id,
                        )
                    )
                ).all()
            )
            decisions_by_key = {item.normalized_product_key: item for item in decisions}
            product_groups: dict[str, list[WebSyncJobItemModel]] = {}
            for model in all_models:
                key = model.normalized_product_key or normalize_product_identity(model.product_key)
                if key is not None:
                    product_groups.setdefault(key, []).append(model)
            duplicate_groups = {
                key: items for key, items in product_groups.items() if len(items) > 1
            }
            duplicate_excluded_count = sum(
                1
                for items in duplicate_groups.values()
                for item in items
                if item.outcome_reason == "duplicate_product_first_wins"
            )
            uses_authoritative_decisions = any(
                item.policy_version == _DUPLICATE_POLICY_VERSION for item in all_models
            )
            identity_invariant_violations = {
                key
                for key, items in product_groups.items()
                if uses_authoritative_decisions
                and not _identity_decision_is_consistent(items, decisions_by_key.get(key))
            }
            if uses_authoritative_decisions:
                identity_invariant_violations.update(
                    key for key in decisions_by_key if key not in product_groups
                )
            unresolved_count = sum(
                1
                for key, items in duplicate_groups.items()
                if key in identity_invariant_violations
                or (
                    not uses_authoritative_decisions
                    and not any(
                        item.outcome_reason == "duplicate_product_first_wins" for item in items
                    )
                )
            )
            product_count = len(
                {
                    key
                    for key, items in product_groups.items()
                    if any(
                        item.report_payload.get("product_count", 0) > 0
                        and item.status
                        in {
                            WebSyncJobItemStatus.SUCCEEDED.value,
                            WebSyncJobItemStatus.NOT_MODIFIED.value,
                        }
                        for item in items
                    )
                }
            )
            failed_models = tuple(
                (
                    await session.scalars(
                        select(WebSyncJobItemModel)
                        .where(
                            *filters,
                            WebSyncJobItemModel.status == WebSyncJobItemStatus.FAILED.value,
                        )
                        .order_by(WebSyncJobItemModel.ordinal)
                        .limit(500)
                    )
                ).all()
            )
        errors = {
            model.url: model.error_message or model.error_code or "page validation failed"
            for model in failed_models
        }
        metric = {name: int(getattr(totals, name) or 0) for name in metric_names}
        return WebSyncJobReport(
            pipeline_sync_job_id=job_id,
            published=False,
            discovered_count=metric["discovered_count"],
            document_count=metric["document_count"],
            changed_document_count=metric["changed_document_count"],
            unchanged_document_count=metric["unchanged_document_count"],
            http_not_modified_count=metric["http_not_modified_count"],
            duplicate_count=metric["duplicate_count"],
            duplicate_product_count=unresolved_count,
            duplicate_product_total=len(duplicate_groups),
            duplicate_product_excluded_count=duplicate_excluded_count,
            duplicate_product_conflict_warning_count=0,
            duplicate_product_unresolved_count=unresolved_count,
            winner_product_count=product_count,
            product_count=product_count,
            processed_page_count=sum(
                item.status
                not in {
                    WebSyncJobItemStatus.PENDING.value,
                    WebSyncJobItemStatus.FETCHING.value,
                }
                for item in all_models
            ),
            produced_document_count=metric["document_count"],
            failed_page_count=int(totals.failed_count or 0),
            unresolved_product_identity_count=unresolved_count,
            pending_removal_count=metric["pending_removal_count"],
            expired_count=metric["expired_count"],
            indexed_chunk_count=metric["indexed_chunk_count"],
            excluded_count=metric["excluded_count"],
            failed_count=int(totals.failed_count or 0),
            errors=errors,
            blocking_issue_count=len(identity_invariant_violations),
            publication_block_reasons=(
                ("duplicate_winner_invariant_violation",) if identity_invariant_violations else ()
            ),
        )

    async def start_finalization(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        started_at: datetime,
    ) -> WebSyncJob:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.RUNNING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if model is None:
                raise RuntimeError("website sync job lease is no longer owned by this worker")
            model.phase = WebSyncJobPhase.FINALIZING.value
            model.heartbeat_at = started_at
            _touch_job(model, progressed_at=started_at)
            await session.flush()
            return _to_domain(model)

    async def complete(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        report: WebSyncJobReport,
        completed_at: datetime,
    ) -> WebSyncJob:
        return await self._finish(
            tenant_id=tenant_id,
            job_id=job_id,
            worker_id=worker_id,
            status=None,
            report=report,
            error_code=None,
            error_message=None,
            completed_at=completed_at,
        )

    async def fail(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        failed_at: datetime,
    ) -> WebSyncJob:
        return await self._finish(
            tenant_id=tenant_id,
            job_id=job_id,
            worker_id=worker_id,
            status=WebSyncJobStatus.FAILED,
            report=None,
            error_code=error_code,
            error_message=error_message,
            completed_at=failed_at,
        )

    async def release_cleanup(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        available_at: datetime,
        error_message: str,
    ) -> WebSyncJob:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status == WebSyncJobStatus.CLEANUP_PENDING.value,
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if model is None:
                raise RuntimeError("website sync cleanup lease is no longer owned")
            model.lease_owner = None
            model.lease_expires_at = None
            model.available_at = available_at
            model.error_code = "staging_cleanup_retry"
            model.error_message = error_message
            _touch_job(model, progressed_at=datetime.now(UTC))
            await session.flush()
            return _to_domain(model)

    async def _finish(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        status: WebSyncJobStatus | None,
        report: WebSyncJobReport | None,
        error_code: str | None,
        error_message: str | None,
        completed_at: datetime,
    ) -> WebSyncJob:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id == job_id,
                    WebSyncJobModel.status.in_(
                        (
                            WebSyncJobStatus.PREPARING.value,
                            WebSyncJobStatus.RUNNING.value,
                            WebSyncJobStatus.CLEANUP_PENDING.value,
                        )
                    ),
                    WebSyncJobModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if model is None:
                raise RuntimeError("website sync job lease is no longer owned by this worker")
            if status is None:
                shadow_succeeded = (
                    model.mode == WebSyncMode.SHADOW.value
                    and report is not None
                    and report.failed_count == 0
                )
                status = (
                    WebSyncJobStatus.SUCCEEDED
                    if shadow_succeeded or bool(report and report.published)
                    else WebSyncJobStatus.FAILED
                )
                if status is WebSyncJobStatus.FAILED:
                    if model.mode == WebSyncMode.SHADOW.value:
                        error_code = "shadow_validation_failed"
                        error_message = "shadow validation did not complete successfully"
                    else:
                        error_code = "snapshot_not_published"
                        error_message = "the previous complete snapshot remains active"
            model.status = status.value
            model.phase = WebSyncJobPhase.COMPLETED.value
            if model.mode == WebSyncMode.SHADOW.value:
                model.publication_status = WebSyncPublicationStatus.NOT_REQUESTED.value
            elif report and report.published:
                model.publication_status = WebSyncPublicationStatus.PUBLISHED.value
            elif status is WebSyncJobStatus.FAILED:
                model.publication_status = WebSyncPublicationStatus.REFUSED.value
            model.report_payload = {} if report is None else _report_payload(report)
            model.error_code = error_code
            model.error_message = error_message
            model.completed_at = completed_at
            model.heartbeat_at = completed_at
            model.lease_owner = None
            model.lease_expires_at = None
            model.active_site_key = None
            _touch_job(model, progressed_at=completed_at)
            session.add(
                _audit(
                    _to_domain(model),
                    event_type=(
                        "knowledge.web_sync.completed"
                        if status == WebSyncJobStatus.SUCCEEDED
                        else "knowledge.web_sync.failed"
                    ),
                    details={
                        "site_id": model.site_id,
                        "published": bool(report and report.published),
                        "error_code": error_code,
                    },
                )
            )
            await session.flush()
            return _to_domain(model)


def _model_values(job: WebSyncJob) -> dict[str, object]:
    return {
        "tenant_id": job.tenant_id,
        "job_id": job.job_id,
        "site_id": job.site_id,
        "base_url": job.base_url,
        "status": job.status.value,
        "trigger": job.trigger.value,
        "mode": job.mode.value,
        "publication_status": job.publication_status.value,
        "manifest_id": job.manifest_id or None,
        "sample_size": job.sample_size,
        "phase": job.phase.value,
        "expected_count": job.expected_count,
        "prepared_count": job.prepared_count,
        "manifest_version": job.manifest_version,
        "manifest_fingerprint": job.manifest_fingerprint,
        "completed_count": job.completed_count,
        "succeeded_count": job.succeeded_count,
        "not_modified_count": job.not_modified_count,
        "excluded_item_count": job.excluded_item_count,
        "failed_item_count": job.failed_item_count,
        "canceled_item_count": job.canceled_item_count,
        "request_payload": _policy_payload(job.policy),
        "requested_by": job.requested_by,
        "correlation_id": job.correlation_id,
        "idempotency_key": job.idempotency_key,
        "active_site_key": _active_site_key(job),
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "claim_count": job.claim_count,
        "failure_attempt_count": job.failure_attempt_count,
        "yield_count": job.yield_count,
        "state_version": job.state_version,
        "prepare_stage": job.prepare_stage,
        "prepare_cursor": job.prepare_cursor,
        "available_at": job.available_at or job.requested_at,
        "requested_at": job.requested_at,
        "updated_at": job.updated_at or job.requested_at,
        "last_progress_at": job.last_progress_at or job.requested_at,
        "cancel_requested_at": job.cancel_requested_at,
        "blocked_at": job.blocked_at,
        "retention_expires_at": job.retention_expires_at,
    }


def _item_model(item: WebSyncJobItem) -> WebSyncJobItemModel:
    return WebSyncJobItemModel(**_item_values(item))


def _item_values(item: WebSyncJobItem) -> dict[str, object]:
    return {
        "tenant_id": item.tenant_id,
        "job_id": item.job_id,
        "site_id": item.site_id,
        "manifest_id": item.manifest_id,
        "item_id": item.item_id,
        "ordinal": item.ordinal,
        "url": item.url,
        "source_sitemap_url": item.source_sitemap_url,
        "content_kind": item.content_kind,
        "status": item.status.value,
        "attempt_count": item.attempt_count,
        "max_attempts": item.max_attempts,
        "lease_owner": item.lease_owner,
        "lease_expires_at": item.lease_expires_at,
        "next_attempt_at": item.next_attempt_at,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
        "duration_ms": item.duration_ms,
        "document_id": item.document_id,
        "version_id": item.version_id,
        "canonical_url": item.canonical_url,
        "final_url": item.final_url,
        "etag": item.etag,
        "last_modified": item.last_modified,
        "product_key": item.product_key,
        "normalized_product_key": (
            item.normalized_product_key or normalize_product_identity(item.product_key)
        ),
        "normalization_version": (
            item.normalization_version
            or (
                PRODUCT_IDENTITY_NORMALIZATION_VERSION
                if normalize_product_identity(item.product_key) is not None
                else None
            )
        ),
        "winner_item_id": item.winner_item_id,
        "winner_url": item.winner_url,
        "identity_source": item.identity_source,
        "policy_version": item.policy_version,
        "report_payload": {} if item.report is None else _report_payload(item.report),
        "error_code": item.error_code,
        "error_message": item.error_message,
        "outcome_reason": item.outcome_reason,
    }


def _active_site_key(job: WebSyncJob) -> str:
    return f"{job.tenant_id}:{job.site_id}"


def _identity_decision_is_consistent(
    items: list[WebSyncJobItemModel],
    decision: WebSyncProductIdentityDecisionModel | None,
) -> bool:
    if decision is None:
        return False
    winners = [item for item in items if item.item_id == decision.winner_item_id]
    return (
        len(winners) == 1
        and winners[0].url == decision.winner_url
        and decision.state in {"selected", "promoted"}
        and decision.policy_version == _DUPLICATE_POLICY_VERSION
        and all(item.normalization_version == decision.normalization_version for item in items)
        and all(
            item.item_id == decision.winner_item_id
            or item.status
            not in {
                WebSyncJobItemStatus.SUCCEEDED.value,
                WebSyncJobItemStatus.NOT_MODIFIED.value,
            }
            for item in items
        )
        and all(
            item.winner_item_id == decision.winner_item_id
            and item.winner_url == decision.winner_url
            for item in items
        )
    )


def _touch_job(
    job: WebSyncJobModel,
    *,
    progressed_at: datetime | None = None,
) -> None:
    now = progressed_at or datetime.now(UTC)
    job.state_version += 1
    job.updated_at = now
    if progressed_at is not None:
        job.last_progress_at = progressed_at


def _canceled_publication_status(mode: str) -> str:
    if mode == WebSyncMode.SHADOW.value:
        return WebSyncPublicationStatus.NOT_REQUESTED.value
    return WebSyncPublicationStatus.REFUSED.value


def _policy_payload(policy: WebSyncPolicySnapshot) -> dict[str, object]:
    return {
        "seed_urls": list(policy.seed_urls),
        "sitemap_urls": list(policy.sitemap_urls),
        "max_pages": policy.max_pages,
        "max_sitemaps": policy.max_sitemaps,
        "max_response_bytes": policy.max_response_bytes,
        "max_decompressed_response_bytes": policy.max_decompressed_response_bytes,
        "max_compression_ratio": policy.max_compression_ratio,
        "request_timeout_seconds": policy.request_timeout_seconds,
        "crawl_delay_seconds": policy.crawl_delay_seconds,
        "follow_internal_links": policy.follow_internal_links,
        "respect_robots_txt": policy.respect_robots_txt,
        "batch_size": policy.batch_size,
        "discover_sitemaps": policy.discover_sitemaps,
        "primary_language": policy.primary_language,
        "translated_locales": list(policy.translated_locales),
        "manifest_fingerprint": policy.manifest_fingerprint,
        "validators": [
            {
                "document_id": item.document_id,
                "version_id": item.version_id,
                "canonical_url": item.canonical_url,
                "requested_url": item.requested_url,
                "final_url": item.final_url,
                "etag": item.etag,
                "last_modified": item.last_modified,
                "product_key": item.product_key,
                "normalized_product_key": item.normalized_product_key,
                "normalization_version": item.normalization_version,
            }
            for item in policy.validators
        ],
        "duplicate_product_policy": policy.duplicate_product_policy,
        "duplicate_product_order": policy.duplicate_product_order,
    }


def _to_policy(payload: dict) -> WebSyncPolicySnapshot:
    return WebSyncPolicySnapshot(
        seed_urls=tuple(str(value) for value in payload.get("seed_urls", [])),
        sitemap_urls=tuple(str(value) for value in payload.get("sitemap_urls", [])),
        max_pages=int(payload.get("max_pages", 50_000)),
        max_sitemaps=int(payload.get("max_sitemaps", 10)),
        max_response_bytes=int(payload.get("max_response_bytes", 2_000_000)),
        max_decompressed_response_bytes=int(
            payload.get("max_decompressed_response_bytes", 4_000_000)
        ),
        max_compression_ratio=float(payload.get("max_compression_ratio", 50.0)),
        request_timeout_seconds=float(payload.get("request_timeout_seconds", 15.0)),
        crawl_delay_seconds=float(payload.get("crawl_delay_seconds", 0.25)),
        follow_internal_links=bool(payload.get("follow_internal_links", True)),
        respect_robots_txt=bool(payload.get("respect_robots_txt", True)),
        batch_size=int(payload.get("batch_size", 250)),
        discover_sitemaps=bool(payload.get("discover_sitemaps", True)),
        primary_language=str(payload.get("primary_language", "en")),
        translated_locales=tuple(str(value) for value in payload.get("translated_locales", [])),
        manifest_fingerprint=(
            str(payload["manifest_fingerprint"])
            if payload.get("manifest_fingerprint") is not None
            else None
        ),
        validators=tuple(
            WebSyncValidatorSnapshot(
                document_id=str(item["document_id"]),
                version_id=str(item["version_id"]),
                canonical_url=str(item["canonical_url"]),
                requested_url=str(item["requested_url"]),
                final_url=str(item["final_url"]),
                etag=str(item["etag"]) if item.get("etag") is not None else None,
                last_modified=(
                    str(item["last_modified"]) if item.get("last_modified") is not None else None
                ),
                product_key=(
                    str(item["product_key"]) if item.get("product_key") is not None else None
                ),
                normalized_product_key=(
                    str(item["normalized_product_key"])
                    if item.get("normalized_product_key") is not None
                    else normalize_product_identity(
                        str(item["product_key"]) if item.get("product_key") is not None else None
                    )
                ),
                normalization_version=(
                    str(item["normalization_version"])
                    if item.get("normalization_version") is not None
                    else PRODUCT_IDENTITY_NORMALIZATION_VERSION
                    if normalize_product_identity(
                        str(item["product_key"]) if item.get("product_key") is not None else None
                    )
                    is not None
                    else None
                ),
            )
            for item in payload.get("validators", [])
            if isinstance(item, dict)
        ),
        duplicate_product_policy=str(payload.get("duplicate_product_policy", "first_wins")),
        duplicate_product_order=str(payload.get("duplicate_product_order", "manifest_ordinal")),
    )


def _report_payload(report: WebSyncJobReport) -> dict[str, object]:
    return {
        "pipeline_sync_job_id": report.pipeline_sync_job_id,
        "published": report.published,
        "discovered_count": report.discovered_count,
        "document_count": report.document_count,
        "changed_document_count": report.changed_document_count,
        "unchanged_document_count": report.unchanged_document_count,
        "http_not_modified_count": report.http_not_modified_count,
        "duplicate_count": report.duplicate_count,
        "duplicate_product_count": report.duplicate_product_count,
        "duplicate_product_total": report.duplicate_product_total,
        "duplicate_product_excluded_count": report.duplicate_product_excluded_count,
        "duplicate_product_conflict_warning_count": report.duplicate_product_conflict_warning_count,
        "duplicate_product_unresolved_count": report.duplicate_product_unresolved_count,
        "winner_product_count": report.winner_product_count,
        "product_count": report.product_count,
        "processed_page_count": report.processed_page_count,
        "produced_document_count": report.produced_document_count,
        "failed_page_count": report.failed_page_count,
        "unresolved_product_identity_count": report.unresolved_product_identity_count,
        "pending_removal_count": report.pending_removal_count,
        "expired_count": report.expired_count,
        "indexed_chunk_count": report.indexed_chunk_count,
        "excluded_count": report.excluded_count,
        "failed_count": report.failed_count,
        "errors": report.errors,
        "blocking_issue_count": report.blocking_issue_count,
        "publication_block_reasons": list(report.publication_block_reasons),
    }


def _duplicate_exclusion_payload(
    *,
    job_id: str,
    product_key: str | None,
    winner_url: str,
) -> dict[str, object]:
    return {
        "pipeline_sync_job_id": job_id,
        "published": False,
        "discovered_count": 1,
        "document_count": 0,
        "changed_document_count": 0,
        "unchanged_document_count": 0,
        "http_not_modified_count": 0,
        "duplicate_count": 0,
        "duplicate_product_count": 0,
        "duplicate_product_total": 1,
        "duplicate_product_excluded_count": 1,
        "duplicate_product_unresolved_count": 0,
        "winner_product_count": 0,
        "product_count": 0,
        "processed_page_count": 0,
        "produced_document_count": 0,
        "failed_page_count": 0,
        "unresolved_product_identity_count": 0,
        "pending_removal_count": 0,
        "expired_count": 0,
        "indexed_chunk_count": 0,
        "excluded_count": 1,
        "failed_count": 0,
        "errors": {
            str(product_key or ""): f"excluded: duplicate_product_first_wins winner={winner_url}"
        },
    }


def _to_report(payload: dict) -> WebSyncJobReport | None:
    if not payload:
        return None
    errors = payload.get("errors", {})
    return WebSyncJobReport(
        pipeline_sync_job_id=str(payload.get("pipeline_sync_job_id", "")),
        published=bool(payload.get("published", False)),
        discovered_count=int(payload.get("discovered_count", 0)),
        document_count=int(payload.get("document_count", 0)),
        changed_document_count=int(payload.get("changed_document_count", 0)),
        unchanged_document_count=int(payload.get("unchanged_document_count", 0)),
        http_not_modified_count=int(payload.get("http_not_modified_count", 0)),
        duplicate_count=int(payload.get("duplicate_count", 0)),
        duplicate_product_count=int(payload.get("duplicate_product_count", 0)),
        duplicate_product_total=int(payload.get("duplicate_product_total", 0)),
        duplicate_product_excluded_count=int(payload.get("duplicate_product_excluded_count", 0)),
        duplicate_product_conflict_warning_count=int(
            payload.get("duplicate_product_conflict_warning_count", 0)
        ),
        duplicate_product_unresolved_count=int(
            payload.get("duplicate_product_unresolved_count", 0)
        ),
        winner_product_count=int(payload.get("winner_product_count", 0)),
        product_count=int(payload.get("product_count", 0)),
        processed_page_count=int(payload.get("processed_page_count", 0)),
        produced_document_count=int(
            payload.get("produced_document_count", payload.get("document_count", 0))
        ),
        failed_page_count=int(payload.get("failed_page_count", payload.get("failed_count", 0))),
        unresolved_product_identity_count=int(
            payload.get(
                "unresolved_product_identity_count",
                payload.get("duplicate_product_unresolved_count", 0),
            )
        ),
        pending_removal_count=int(payload.get("pending_removal_count", 0)),
        expired_count=int(payload.get("expired_count", 0)),
        indexed_chunk_count=int(payload.get("indexed_chunk_count", 0)),
        excluded_count=int(payload.get("excluded_count", 0)),
        failed_count=int(payload.get("failed_count", 0)),
        errors={
            str(key): str(value)
            for key, value in (errors.items() if isinstance(errors, dict) else ())
        },
        blocking_issue_count=int(payload.get("blocking_issue_count", 0)),
        publication_block_reasons=tuple(
            str(value) for value in payload.get("publication_block_reasons", [])
        ),
    )


def _to_domain(model: WebSyncJobModel) -> WebSyncJob:
    return WebSyncJob(
        tenant_id=model.tenant_id,
        job_id=model.job_id,
        site_id=model.site_id,
        base_url=model.base_url,
        status=WebSyncJobStatus(model.status),
        trigger=WebSyncTrigger(model.trigger),
        mode=WebSyncMode(model.mode),
        publication_status=WebSyncPublicationStatus(model.publication_status),
        manifest_id=model.manifest_id or "",
        sample_size=model.sample_size,
        policy=_to_policy(model.request_payload or {}),
        requested_by=model.requested_by,
        correlation_id=model.correlation_id,
        idempotency_key=model.idempotency_key,
        requested_at=model.requested_at,
        manifest_version=model.manifest_version,
        manifest_fingerprint=model.manifest_fingerprint,
        prepared_count=model.prepared_count,
        phase=WebSyncJobPhase(model.phase),
        expected_count=model.expected_count,
        completed_count=model.completed_count,
        succeeded_count=model.succeeded_count,
        not_modified_count=model.not_modified_count,
        excluded_item_count=model.excluded_item_count,
        failed_item_count=model.failed_item_count,
        canceled_item_count=model.canceled_item_count,
        attempt_count=model.attempt_count,
        max_attempts=model.max_attempts,
        started_at=model.started_at,
        heartbeat_at=model.heartbeat_at,
        completed_at=model.completed_at,
        lease_owner=model.lease_owner,
        lease_expires_at=model.lease_expires_at,
        cancel_requested_at=model.cancel_requested_at,
        blocked_at=model.blocked_at,
        retention_expires_at=model.retention_expires_at,
        report=_to_report(model.report_payload or {}),
        error_code=model.error_code,
        error_message=model.error_message,
        state_version=model.state_version,
        updated_at=model.updated_at,
        last_progress_at=model.last_progress_at,
        available_at=model.available_at,
        prepare_stage=model.prepare_stage,
        prepare_cursor=model.prepare_cursor,
        claim_count=model.claim_count,
        failure_attempt_count=model.failure_attempt_count,
        yield_count=model.yield_count,
    )


def _outcome_reason(status: WebSyncJobItemStatus, report: WebSyncJobReport) -> str:
    if status is WebSyncJobItemStatus.SUCCEEDED:
        return "indexed"
    if status is WebSyncJobItemStatus.NOT_MODIFIED:
        return "not_modified"
    reasons = " ".join(report.errors.values()).casefold()
    if "robots_txt_disallow" in reasons:
        return "robots_excluded"
    if "canonical_duplicate" in reasons or "duplicate" in reasons:
        return "canonical_duplicate"
    if "noindex" in reasons:
        return "noindex"
    if any(marker in reasons for marker in ("http 404", "http 410", "http_404", "http_410")):
        return "gone"
    if "approved_" in reasons:
        return "approved_exclusion"
    return "policy_excluded"


def _item_to_domain(model: WebSyncJobItemModel) -> WebSyncJobItem:
    return WebSyncJobItem(
        tenant_id=model.tenant_id,
        job_id=model.job_id,
        site_id=model.site_id,
        manifest_id=model.manifest_id,
        item_id=model.item_id,
        ordinal=model.ordinal,
        url=model.url,
        source_sitemap_url=model.source_sitemap_url,
        content_kind=model.content_kind,
        status=WebSyncJobItemStatus(model.status),
        attempt_count=model.attempt_count,
        max_attempts=model.max_attempts,
        lease_owner=model.lease_owner,
        lease_expires_at=model.lease_expires_at,
        next_attempt_at=model.next_attempt_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
        duration_ms=model.duration_ms,
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
        winner_item_id=model.winner_item_id,
        winner_url=model.winner_url,
        identity_source=model.identity_source,
        policy_version=model.policy_version,
        report=_to_report(model.report_payload or {}),
        error_code=model.error_code,
        error_message=model.error_message,
        outcome_reason=model.outcome_reason,
    )


def _audit(job: WebSyncJob, *, event_type: str, details: dict[str, object]) -> AuditEventModel:
    return AuditEventModel(
        tenant_id=job.tenant_id,
        event_id=str(uuid4()),
        event_type=event_type,
        resource_type="web_sync_job",
        resource_id=job.job_id,
        actor_subject_id=job.requested_by,
        correlation_id=job.correlation_id,
        details=details,
        created_at=datetime.now(UTC),
    )
