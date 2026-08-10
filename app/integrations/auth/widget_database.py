from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.tenant_context import bind_tenant
from app.domain.models import AuthenticatedPrincipal
from app.domain.ports import WidgetSiteAuthenticationPort
from app.integrations.postgres.customer_experience import widget_config_from_payload
from app.integrations.postgres.models import (
    PublicWidgetRegistryModel,
    SupportSiteModel,
    WidgetConfigVersionModel,
)


class PostgreSQLWidgetSiteAuthenticationAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def authenticate_site(
        self,
        *,
        site_key: str,
        correlation_id: str,
    ) -> AuthenticatedPrincipal:
        key_hash = sha256(site_key.encode("utf-8")).hexdigest()
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(
                        PublicWidgetRegistryModel.tenant_id,
                        PublicWidgetRegistryModel.site_id,
                        PublicWidgetRegistryModel.primary_language,
                    ).where(
                        PublicWidgetRegistryModel.key_hash == key_hash,
                        PublicWidgetRegistryModel.status == "active",
                    )
                )
            ).first()
        if row is None:
            raise PermissionError("invalid widget site credential")
        tenant_id, site_id, primary_language = row
        bind_tenant(tenant_id)
        async with self._session_factory() as session:
            site = await session.scalar(
                select(SupportSiteModel).where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
            )
            config_model = await session.scalar(
                select(WidgetConfigVersionModel)
                .where(
                    WidgetConfigVersionModel.tenant_id == tenant_id,
                    WidgetConfigVersionModel.site_id == site_id,
                    WidgetConfigVersionModel.status == "published",
                )
                .order_by(WidgetConfigVersionModel.version_number.desc())
                .limit(1)
            )
        config = (
            widget_config_from_payload(config_model.config) if config_model is not None else None
        )
        return AuthenticatedPrincipal(
            subject_id="anonymous-widget-visitor",
            tenant_id=tenant_id,
            roles=frozenset({"anonymous"}),
            scopes=frozenset({"knowledge:read"}),
            authentication_method="widget_site_key",
            authenticated_at=datetime.now(UTC),
            correlation_id=correlation_id,
            site_id=site_id,
            preferred_language=primary_language,
            site_domain=site.base_url if site is not None else None,
            agent_display_name=config.agent_name if config is not None else None,
            agent_identity_type="team",
            customer_address_mode=(
                config.customer_address_mode if config is not None else "neutral"
            ),
            introduce_on_first_turn=(
                config.introduce_on_first_turn if config is not None else True
            ),
            site_identity_version=(
                config_model.version_id if config_model is not None else "site-identity-v1"
            ),
        )


class CompositeWidgetSiteAuthenticationAdapter:
    def __init__(self, adapters: tuple[WidgetSiteAuthenticationPort, ...]) -> None:
        self._adapters = adapters

    async def authenticate_site(
        self,
        *,
        site_key: str,
        correlation_id: str,
    ) -> AuthenticatedPrincipal:
        for adapter in self._adapters:
            try:
                return await adapter.authenticate_site(
                    site_key=site_key,
                    correlation_id=correlation_id,
                )
            except PermissionError:
                continue
        raise PermissionError("invalid widget site credential")
