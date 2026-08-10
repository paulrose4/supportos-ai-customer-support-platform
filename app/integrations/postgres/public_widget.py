import hashlib
from datetime import datetime, timedelta
from secrets import token_urlsafe
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.tenant_context import bind_tenant
from app.domain.models.widget import IssuedPublicWidgetSession, PublicWidgetSite
from app.integrations.postgres.customer_experience import widget_config_from_payload
from app.integrations.postgres.models import (
    AuditEventModel,
    PublicWidgetRegistryModel,
    SiteUsageDailyModel,
    SupportSiteModel,
    WidgetConfigVersionModel,
    WidgetMessageAdmissionModel,
    WidgetVisitorSessionModel,
)


class PostgreSQLPublicWidgetAccessAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_public_site(self, *, public_widget_id: str) -> PublicWidgetSite | None:
        async with self._session_factory() as session:
            registry = await session.scalar(
                select(PublicWidgetRegistryModel).where(
                    PublicWidgetRegistryModel.public_widget_id == public_widget_id,
                    PublicWidgetRegistryModel.status == "active",
                    PublicWidgetRegistryModel.verification_status == "verified",
                )
            )
        if registry is None:
            return None
        bind_tenant(registry.tenant_id)
        async with self._session_factory() as session:
            config = await session.scalar(
                select(WidgetConfigVersionModel)
                .where(
                    WidgetConfigVersionModel.tenant_id == registry.tenant_id,
                    WidgetConfigVersionModel.site_id == registry.site_id,
                    WidgetConfigVersionModel.status == "published",
                )
                .order_by(WidgetConfigVersionModel.version_number.desc())
                .limit(1)
            )
        return _to_public_site(registry, config)

    async def admit_message(
        self,
        *,
        tenant_id: str,
        site_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            site = await session.scalar(
                select(SupportSiteModel)
                .where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
                .with_for_update()
            )
            if site is None or site.status != "active" or site.verification_status != "verified":
                return False
            existing = await session.scalar(
                select(WidgetMessageAdmissionModel.id).where(
                    WidgetMessageAdmissionModel.tenant_id == tenant_id,
                    WidgetMessageAdmissionModel.site_id == site_id,
                    WidgetMessageAdmissionModel.request_id == request_id,
                )
            )
            if existing is not None:
                return True
            day_start = occurred_at.replace(hour=0, minute=0, second=0, microsecond=0)
            usage_date = day_start.date()
            await session.execute(
                insert(SiteUsageDailyModel)
                .values(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    usage_date=usage_date,
                    admitted_count=0,
                    input_tokens=0,
                    output_tokens=0,
                    cost_amount=0.0,
                    version=1,
                    updated_at=occurred_at,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "site_id", "usage_date"])
            )
            usage = await session.scalar(
                select(SiteUsageDailyModel)
                .where(
                    SiteUsageDailyModel.tenant_id == tenant_id,
                    SiteUsageDailyModel.site_id == site_id,
                    SiteUsageDailyModel.usage_date == usage_date,
                )
                .with_for_update()
            )
            if usage is None or usage.admitted_count >= site.widget_daily_message_limit:
                event_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"widget-volume:{tenant_id}:{site_id}:{usage_date.isoformat()}",
                    )
                )
                await session.execute(
                    insert(AuditEventModel)
                    .values(
                        tenant_id=tenant_id,
                        event_id=event_id,
                        event_type="public_widget.volume_anomaly",
                        actor_subject_id="system",
                        resource_type="support_site",
                        resource_id=site_id,
                        details={
                            "observed_count": int(usage.admitted_count if usage else 0),
                            "configured_threshold": site.widget_daily_message_limit,
                            "admission_rejected": True,
                        },
                        created_at=occurred_at,
                    )
                    .on_conflict_do_nothing(index_elements=["tenant_id", "event_id"])
                )
                return False
            usage.admitted_count += 1
            usage.updated_at = occurred_at
            session.add(
                WidgetMessageAdmissionModel(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    request_id=request_id,
                    admitted_at=occurred_at,
                )
            )
        return True

    async def create_visitor_session(
        self,
        *,
        site: PublicWidgetSite,
        origin: str,
        occurred_at: datetime,
        expires_at: datetime,
        preferred_session_id: str | None = None,
    ) -> IssuedPublicWidgetSession:
        session_id = preferred_session_id or token_urlsafe(24)
        resume_token = token_urlsafe(32)
        async with self._session_factory.begin() as session:
            existing = None
            if preferred_session_id:
                existing = await session.scalar(
                    select(WidgetVisitorSessionModel)
                    .where(
                        WidgetVisitorSessionModel.tenant_id == site.tenant_id,
                        WidgetVisitorSessionModel.site_id == site.site_id,
                        WidgetVisitorSessionModel.session_id == session_id,
                    )
                    .with_for_update()
                )
            if existing is not None:
                if existing.origin != origin or existing.revoked_at is not None:
                    raise PermissionError("public widget session cannot be upgraded")
                existing.previous_resume_token_hash = existing.resume_token_hash
                existing.previous_valid_until = occurred_at + timedelta(seconds=30)
                existing.resume_token_hash = _hash_resume_token(resume_token)
                existing.token_revision += 1
                existing.expires_at = expires_at
                existing.last_seen_at = occurred_at
                return IssuedPublicWidgetSession(session_id, resume_token, expires_at)
            session.add(
                WidgetVisitorSessionModel(
                    tenant_id=site.tenant_id,
                    site_id=site.site_id,
                    public_widget_id=site.public_widget_id,
                    session_id=session_id,
                    origin=origin,
                    resume_token_hash=_hash_resume_token(resume_token),
                    token_revision=1,
                    expires_at=expires_at,
                    created_at=occurred_at,
                    last_seen_at=occurred_at,
                )
            )
        return IssuedPublicWidgetSession(session_id, resume_token, expires_at)

    async def rotate_visitor_session(
        self,
        *,
        site: PublicWidgetSite,
        origin: str,
        resume_token: str,
        occurred_at: datetime,
        expires_at: datetime,
    ) -> IssuedPublicWidgetSession | None:
        token_hash = _hash_resume_token(resume_token)
        new_resume_token = token_urlsafe(32)
        new_hash = _hash_resume_token(new_resume_token)
        async with self._session_factory.begin() as session:
            record = await session.scalar(
                select(WidgetVisitorSessionModel)
                .where(
                    WidgetVisitorSessionModel.tenant_id == site.tenant_id,
                    WidgetVisitorSessionModel.site_id == site.site_id,
                    WidgetVisitorSessionModel.origin == origin,
                    WidgetVisitorSessionModel.revoked_at.is_(None),
                    WidgetVisitorSessionModel.expires_at > occurred_at,
                    (
                        (WidgetVisitorSessionModel.resume_token_hash == token_hash)
                        | (
                            (WidgetVisitorSessionModel.previous_resume_token_hash == token_hash)
                            & (WidgetVisitorSessionModel.previous_valid_until > occurred_at)
                        )
                    ),
                )
                .with_for_update()
            )
            if record is None:
                return None
            record.previous_resume_token_hash = record.resume_token_hash
            record.previous_valid_until = occurred_at + timedelta(seconds=30)
            record.resume_token_hash = new_hash
            record.token_revision += 1
            record.expires_at = expires_at
            record.last_seen_at = occurred_at
            return IssuedPublicWidgetSession(record.session_id, new_resume_token, expires_at)

    async def visitor_session_is_active(
        self,
        *,
        tenant_id: str,
        site_id: str,
        session_id: str,
        occurred_at: datetime,
    ) -> bool:
        async with self._session_factory() as session:
            return bool(
                await session.scalar(
                    select(WidgetVisitorSessionModel.id).where(
                        WidgetVisitorSessionModel.tenant_id == tenant_id,
                        WidgetVisitorSessionModel.site_id == site_id,
                        WidgetVisitorSessionModel.session_id == session_id,
                        WidgetVisitorSessionModel.revoked_at.is_(None),
                        WidgetVisitorSessionModel.expires_at > occurred_at,
                    )
                )
            )


def _to_public_site(
    model: PublicWidgetRegistryModel, config: WidgetConfigVersionModel | None
) -> PublicWidgetSite:
    return PublicWidgetSite(
        public_widget_id=model.public_widget_id,
        tenant_id=model.tenant_id,
        site_id=model.site_id,
        allowed_origins=tuple(str(value) for value in model.allowed_origins),
        status=model.status,
        daily_message_limit=model.daily_message_limit,
        primary_language=model.primary_language,
        widget_config=(widget_config_from_payload(config.config) if config is not None else None),
        base_url=(str(model.allowed_origins[0]) if model.allowed_origins else ""),
        widget_config_version=(config.version_id if config is not None else "site-identity-v1"),
        verification_status=model.verification_status,
        auth_version=model.auth_version,
    )


def _hash_resume_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
