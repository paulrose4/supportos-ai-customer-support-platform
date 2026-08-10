from datetime import datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import ManagedSupportSite
from app.integrations.postgres.models import (
    AuditEventModel,
    PublicWidgetRegistryModel,
    SupportQueueModel,
    SupportSiteModel,
    WidgetConfigVersionModel,
    WidgetSiteCredentialModel,
)
from app.integrations.postgres.models.site_web_source import SiteWebSourceConfigModel


class PostgreSQLSiteAdministrationAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_managed_site(self, *, tenant_id: str, site_id: str) -> ManagedSupportSite | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(SupportSiteModel, WidgetSiteCredentialModel)
                    .outerjoin(
                        WidgetSiteCredentialModel,
                        (WidgetSiteCredentialModel.tenant_id == SupportSiteModel.tenant_id)
                        & (WidgetSiteCredentialModel.site_id == SupportSiteModel.site_id),
                    )
                    .where(
                        SupportSiteModel.tenant_id == tenant_id,
                        SupportSiteModel.site_id == site_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        site, credential = row
        return _to_managed_site(site, credential)

    async def list_managed_sites(self, *, tenant_id: str) -> list[ManagedSupportSite]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(SupportSiteModel, WidgetSiteCredentialModel)
                    .outerjoin(
                        WidgetSiteCredentialModel,
                        (WidgetSiteCredentialModel.tenant_id == SupportSiteModel.tenant_id)
                        & (WidgetSiteCredentialModel.site_id == SupportSiteModel.site_id),
                    )
                    .where(SupportSiteModel.tenant_id == tenant_id)
                    .order_by(SupportSiteModel.name, SupportSiteModel.site_id)
                )
            ).all()
        return [_to_managed_site(site, credential) for site, credential in rows]

    async def create_managed_site(
        self,
        *,
        tenant_id: str,
        site_id: str,
        public_widget_id: str,
        name: str,
        base_url: str,
        allowed_origins: tuple[str, ...],
        widget_daily_message_limit: int,
        primary_language: str,
        key_hash: str | None,
        key_prefix: str | None,
        actor_subject_id: str,
        correlation_id: str,
        created_at: datetime,
    ) -> ManagedSupportSite | None:
        try:
            async with self._session_factory.begin() as session:
                existing_site = await session.scalar(
                    select(SupportSiteModel)
                    .where(
                        SupportSiteModel.tenant_id == tenant_id,
                        SupportSiteModel.site_id == site_id,
                    )
                    .with_for_update()
                )
                if existing_site is not None:
                    credential = await session.scalar(
                        select(WidgetSiteCredentialModel).where(
                            WidgetSiteCredentialModel.tenant_id == tenant_id,
                            WidgetSiteCredentialModel.site_id == site_id,
                        )
                    )
                    if (
                        existing_site.name == name
                        and existing_site.base_url == base_url
                        and tuple(existing_site.allowed_origins) == allowed_origins
                        and existing_site.widget_daily_message_limit == widget_daily_message_limit
                        and existing_site.primary_language == primary_language
                        and (
                            key_hash is None
                            or (
                                credential is not None
                                and credential.key_hash == key_hash
                                and credential.status == "active"
                            )
                        )
                    ):
                        return _to_managed_site(existing_site, credential)
                    return None
                site = SupportSiteModel(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    public_widget_id=public_widget_id,
                    name=name,
                    base_url=base_url,
                    allowed_origins=list(allowed_origins),
                    widget_daily_message_limit=widget_daily_message_limit,
                    primary_language=primary_language,
                    status="active",
                    verification_status="pending",
                    created_at=created_at,
                    updated_at=created_at,
                )
                session.add(site)
                await session.flush()
                session.add(
                    WidgetConfigVersionModel(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        version_id=f"initial-{site_id}",
                        version_number=1,
                        status="draft",
                        config={
                            "welcome_message": (
                                "您好！今天有什么可以帮您？"
                                if primary_language.startswith("zh")
                                else "Hello! How can I help you today?"
                            ),
                            "online_message": "客服在线",
                            "offline_message": "当前为非工作时间，请留言，我们会尽快回复。",
                            "business_timezone": "Asia/Shanghai",
                            "business_hours": {
                                day: "09:00-18:00" for day in ("mon", "tue", "wed", "thu", "fri")
                            },
                            "holidays": [],
                            "offline_form_enabled": True,
                            "primary_color": "#2563eb",
                            "position": "right",
                            "agent_name": "在线客服",
                            "agent_avatar_url": None,
                            "mobile_enabled": True,
                            "default_language": primary_language,
                            "handoff_timeout_seconds": 120,
                            "csat_enabled": True,
                            "customer_address_mode": "neutral",
                            "introduce_on_first_turn": True,
                        },
                        created_by=actor_subject_id,
                        created_at=created_at,
                        published_at=None,
                    )
                )
                default_queue = await session.scalar(
                    select(SupportQueueModel).where(
                        SupportQueueModel.tenant_id == tenant_id,
                        SupportQueueModel.queue_id == "general",
                    )
                )
                if default_queue is None:
                    session.add(
                        SupportQueueModel(
                            tenant_id=tenant_id,
                            queue_id="general",
                            name="通用客服",
                            description="默认客服队列",
                            is_default=True,
                            status="active",
                            created_at=created_at,
                            updated_at=created_at,
                        )
                    )
                credential = None
                if key_hash is not None and key_prefix is not None:
                    credential = WidgetSiteCredentialModel(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        key_hash=key_hash,
                        key_prefix=key_prefix,
                        status="active",
                        created_at=created_at,
                        rotated_at=None,
                    )
                    session.add(credential)
                session.add(
                    AuditEventModel(
                        tenant_id=tenant_id,
                        event_id=str(uuid5(NAMESPACE_URL, f"support-site:{tenant_id}:{site_id}")),
                        event_type="support_site.created",
                        actor_subject_id=actor_subject_id,
                        correlation_id=correlation_id,
                        resource_type="support_site",
                        resource_id=site_id,
                        details={
                            "name": name,
                            "base_url": base_url,
                            "public_widget_id": public_widget_id,
                            "allowed_origins": list(allowed_origins),
                            "widget_daily_message_limit": widget_daily_message_limit,
                            "primary_language": primary_language,
                            "verification_status": "pending",
                            "key_prefix": key_prefix,
                        },
                        created_at=created_at,
                    )
                )
                await _sync_public_registry(session, site, key_hash, created_at)
                await session.flush()
                return _to_managed_site(site, credential)
        except IntegrityError:
            existing = await self._get_site(tenant_id=tenant_id, site_id=site_id)
            if (
                existing is not None
                and existing.name == name
                and existing.base_url == base_url
                and (key_prefix is None or existing.credential_key_prefix == key_prefix)
            ):
                return existing
            return None

    async def update_managed_site(
        self,
        *,
        tenant_id: str,
        site_id: str,
        name: str,
        base_url: str,
        allowed_origins: tuple[str, ...],
        status: str,
        primary_language: str,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> ManagedSupportSite | None:
        async with self._session_factory.begin() as session:
            site = await session.scalar(
                select(SupportSiteModel)
                .where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
                .with_for_update()
            )
            if site is None:
                return None
            credential = await session.scalar(
                select(WidgetSiteCredentialModel)
                .where(
                    WidgetSiteCredentialModel.tenant_id == tenant_id,
                    WidgetSiteCredentialModel.site_id == site_id,
                )
                .with_for_update()
            )
            if (
                site.name == name
                and site.base_url == base_url
                and tuple(site.allowed_origins) == allowed_origins
                and site.status == status
                and site.primary_language == primary_language
            ):
                return _to_managed_site(site, credential)
            previous = {
                "name": site.name,
                "base_url": site.base_url,
                "allowed_origins": list(site.allowed_origins),
                "status": site.status,
                "primary_language": site.primary_language,
                "verification_status": site.verification_status,
            }
            base_url_changed = site.base_url != base_url
            site.name = name
            site.base_url = base_url
            site.allowed_origins = list(allowed_origins)
            site.status = status
            site.primary_language = primary_language
            site.updated_at = changed_at
            if base_url_changed:
                site.verification_status = "pending"
                site.verification_method = None
                site.verification_token_hash = None
                site.verification_token_prefix = None
                site.verification_expires_at = None
                site.verified_at = None
                await session.execute(
                    WidgetConfigVersionModel.__table__.update()
                    .where(
                        WidgetConfigVersionModel.tenant_id == tenant_id,
                        WidgetConfigVersionModel.site_id == site_id,
                        WidgetConfigVersionModel.status == "published",
                    )
                    .values(status="draft", published_at=None)
                )
                invalidated = await session.execute(
                    update(SiteWebSourceConfigModel)
                    .where(
                        SiteWebSourceConfigModel.tenant_id == tenant_id,
                        SiteWebSourceConfigModel.site_id == site_id,
                    )
                    .values(
                        config_version=SiteWebSourceConfigModel.config_version + 1,
                        validation_status="unvalidated",
                        validated_at=None,
                        updated_by=actor_subject_id,
                        updated_at=changed_at,
                    )
                )
                if invalidated.rowcount:
                    session.add(
                        AuditEventModel(
                            tenant_id=tenant_id,
                            event_id=str(uuid4()),
                            event_type="site_web_source_config.invalidated",
                            actor_subject_id=actor_subject_id,
                            correlation_id=correlation_id,
                            resource_type="site_web_source_config",
                            resource_id=site_id,
                            details={"reason": "site_base_url_changed"},
                            created_at=changed_at,
                        )
                    )
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="support_site.updated",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="support_site",
                    resource_id=site_id,
                    details={
                        "previous": previous,
                        "current": {
                            "name": name,
                            "base_url": base_url,
                            "allowed_origins": list(allowed_origins),
                            "status": status,
                            "primary_language": primary_language,
                            "verification_status": site.verification_status,
                        },
                    },
                    created_at=changed_at,
                )
            )
            await _sync_public_registry(
                session,
                site,
                credential.key_hash if credential is not None else None,
                changed_at,
            )
            await session.flush()
            return _to_managed_site(site, credential)

    async def rotate_site_key(
        self,
        *,
        tenant_id: str,
        site_id: str,
        key_hash: str,
        key_prefix: str,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> ManagedSupportSite | None:
        async with self._session_factory.begin() as session:
            site = await session.scalar(
                select(SupportSiteModel)
                .where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
                .with_for_update()
            )
            if site is None:
                return None
            credential = await session.scalar(
                select(WidgetSiteCredentialModel)
                .where(
                    WidgetSiteCredentialModel.tenant_id == tenant_id,
                    WidgetSiteCredentialModel.site_id == site_id,
                )
                .with_for_update()
            )
            if credential is not None and credential.key_hash == key_hash:
                return _to_managed_site(site, credential)
            previous_prefix = credential.key_prefix if credential is not None else None
            if credential is None:
                credential = WidgetSiteCredentialModel(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    key_hash=key_hash,
                    key_prefix=key_prefix,
                    status="active",
                    created_at=changed_at,
                    rotated_at=changed_at,
                )
                session.add(credential)
            else:
                credential.key_hash = key_hash
                credential.key_prefix = key_prefix
                credential.status = "active"
                credential.rotated_at = changed_at
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="support_site.key_rotated",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="support_site",
                    resource_id=site_id,
                    details={
                        "previous_key_prefix": previous_prefix,
                        "current_key_prefix": key_prefix,
                    },
                    created_at=changed_at,
                )
            )
            await _sync_public_registry(session, site, key_hash, changed_at)
            await session.flush()
            return _to_managed_site(site, credential)

    async def issue_verification_challenge(
        self,
        *,
        tenant_id: str,
        site_id: str,
        method: str,
        token_hash: str,
        token_prefix: str,
        expires_at: datetime,
        changed_at: datetime,
        actor_subject_id: str,
        correlation_id: str,
    ) -> ManagedSupportSite | None:
        async with self._session_factory.begin() as session:
            site = await session.scalar(
                select(SupportSiteModel)
                .where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
                .with_for_update()
            )
            if site is None:
                return None
            site.verification_status = "pending"
            site.verification_method = method
            site.verification_token_hash = token_hash
            site.verification_token_prefix = token_prefix
            site.verification_expires_at = expires_at
            site.verified_at = None
            site.updated_at = changed_at
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="support_site.verification_challenge_issued",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="support_site",
                    resource_id=site_id,
                    details={"method": method, "token_prefix": token_prefix},
                    created_at=changed_at,
                )
            )
            credential = await session.scalar(
                select(WidgetSiteCredentialModel).where(
                    WidgetSiteCredentialModel.tenant_id == tenant_id,
                    WidgetSiteCredentialModel.site_id == site_id,
                )
            )
            await _sync_public_registry(
                session,
                site,
                credential.key_hash if credential is not None else None,
                changed_at,
            )
            await session.flush()
            return _to_managed_site(site, credential)

    async def complete_verification(
        self,
        *,
        tenant_id: str,
        site_id: str,
        method: str,
        token_hash: str,
        verified_at: datetime,
        actor_subject_id: str,
        correlation_id: str,
    ) -> ManagedSupportSite | None:
        async with self._session_factory.begin() as session:
            site = await session.scalar(
                select(SupportSiteModel)
                .where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
                .with_for_update()
            )
            if site is None:
                return None
            if site.verification_status == "verified" and site.verification_method == method:
                credential = await session.scalar(
                    select(WidgetSiteCredentialModel).where(
                        WidgetSiteCredentialModel.tenant_id == tenant_id,
                        WidgetSiteCredentialModel.site_id == site_id,
                    )
                )
                return _to_managed_site(site, credential)
            if (
                site.verification_status != "pending"
                or site.verification_method != method
                or site.verification_token_hash != token_hash
                or site.verification_expires_at is None
                or site.verification_expires_at <= verified_at
            ):
                return None
            site.verification_status = "verified"
            site.verified_at = verified_at
            site.verification_token_hash = None
            site.verification_expires_at = None
            site.updated_at = verified_at
            initial_config = await session.scalar(
                select(WidgetConfigVersionModel)
                .where(
                    WidgetConfigVersionModel.tenant_id == tenant_id,
                    WidgetConfigVersionModel.site_id == site_id,
                    WidgetConfigVersionModel.version_id == f"initial-{site_id}",
                )
                .with_for_update()
            )
            if initial_config is not None:
                initial_config.status = "published"
                initial_config.published_at = verified_at
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="support_site.verified",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="support_site",
                    resource_id=site_id,
                    details={"method": method},
                    created_at=verified_at,
                )
            )
            credential = await session.scalar(
                select(WidgetSiteCredentialModel).where(
                    WidgetSiteCredentialModel.tenant_id == tenant_id,
                    WidgetSiteCredentialModel.site_id == site_id,
                )
            )
            await _sync_public_registry(
                session,
                site,
                credential.key_hash if credential is not None else None,
                verified_at,
            )
            await session.flush()
            return _to_managed_site(site, credential)

    async def _get_site(self, *, tenant_id: str, site_id: str) -> ManagedSupportSite | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(SupportSiteModel, WidgetSiteCredentialModel)
                    .outerjoin(
                        WidgetSiteCredentialModel,
                        (WidgetSiteCredentialModel.tenant_id == SupportSiteModel.tenant_id)
                        & (WidgetSiteCredentialModel.site_id == SupportSiteModel.site_id),
                    )
                    .where(
                        SupportSiteModel.tenant_id == tenant_id,
                        SupportSiteModel.site_id == site_id,
                    )
                )
            ).first()
        return _to_managed_site(*row) if row is not None else None


def _to_managed_site(
    site: SupportSiteModel,
    credential: WidgetSiteCredentialModel | None,
) -> ManagedSupportSite:
    return ManagedSupportSite(
        site_id=site.site_id,
        tenant_id=site.tenant_id,
        public_widget_id=site.public_widget_id,
        name=site.name,
        base_url=site.base_url,
        allowed_origins=tuple(str(value) for value in site.allowed_origins),
        widget_daily_message_limit=site.widget_daily_message_limit,
        status=site.status,
        verification_status=site.verification_status,
        verification_method=site.verification_method,
        verification_token_prefix=site.verification_token_prefix,
        verification_expires_at=site.verification_expires_at,
        verified_at=site.verified_at,
        credential_key_prefix=credential.key_prefix if credential is not None else None,
        credential_status=credential.status if credential is not None else None,
        created_at=site.created_at,
        updated_at=site.updated_at,
        primary_language=site.primary_language,
    )


async def _sync_public_registry(
    session: AsyncSession,
    site: SupportSiteModel,
    key_hash: str | None,
    changed_at: datetime,
) -> None:
    registry = await session.scalar(
        select(PublicWidgetRegistryModel)
        .where(
            PublicWidgetRegistryModel.tenant_id == site.tenant_id,
            PublicWidgetRegistryModel.site_id == site.site_id,
        )
        .with_for_update()
    )
    if registry is None:
        session.add(
            PublicWidgetRegistryModel(
                public_widget_id=site.public_widget_id,
                tenant_id=site.tenant_id,
                site_id=site.site_id,
                key_hash=key_hash,
                allowed_origins=list(site.allowed_origins),
                daily_message_limit=site.widget_daily_message_limit,
                primary_language=site.primary_language,
                status=site.status,
                verification_status=site.verification_status,
                auth_version=1,
                created_at=site.created_at,
                updated_at=changed_at,
            )
        )
        return
    registry.public_widget_id = site.public_widget_id
    registry.key_hash = key_hash
    registry.allowed_origins = list(site.allowed_origins)
    registry.daily_message_limit = site.widget_daily_message_limit
    registry.primary_language = site.primary_language
    registry.status = site.status
    registry.verification_status = site.verification_status
    registry.auth_version += 1
    registry.updated_at = changed_at
