from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.postgres.models import (
    AuditEventModel,
    CustomerModel,
    OrderModel,
    PublicWidgetRegistryModel,
    SupportQueueModel,
    SupportSiteModel,
    SupportTicketModel,
    TenantModel,
)


class PostgreSQLMockBusinessDataSeeder:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure_tenant(self, *, tenant_id: str) -> None:
        """Create the development tenant required by mock business records."""

        now = datetime.now(UTC)
        async with self._session_factory.begin() as session:
            await session.execute(
                pg_insert(TenantModel)
                .values(
                    tenant_id=tenant_id,
                    name="演示工作区",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[TenantModel.tenant_id])
            )

    async def ensure_support_queues(self, *, tenant_id: str) -> None:
        now = datetime.now(UTC)
        queue_definitions = (
            ("general", "通用客服", "默认客服队列", True),
            ("orders", "订单人工客服", "订单、物流、退款、取消、支付和地址问题", False),
        )
        created: list[str] = []
        async with self._session_factory.begin() as session:
            for queue_id, name, description, is_default in queue_definitions:
                existing = await session.scalar(
                    select(SupportQueueModel).where(
                        SupportQueueModel.tenant_id == tenant_id,
                        SupportQueueModel.queue_id == queue_id,
                    )
                )
                if existing is not None:
                    continue
                session.add(
                    SupportQueueModel(
                        tenant_id=tenant_id,
                        queue_id=queue_id,
                        name=name,
                        description=description,
                        is_default=is_default,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                )
                created.append(queue_id)
            if created:
                event_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:support-queues-v1"))
                existing_event = await session.scalar(
                    select(AuditEventModel).where(
                        AuditEventModel.tenant_id == tenant_id,
                        AuditEventModel.event_id == event_id,
                    )
                )
                if existing_event is None:
                    session.add(
                        AuditEventModel(
                            tenant_id=tenant_id,
                            event_id=event_id,
                            event_type="support_queues.bootstrapped",
                            actor_subject_id="system",
                            resource_type="tenant",
                            resource_id=tenant_id,
                            details={"queue_ids": created},
                            created_at=now,
                        )
                    )

    async def seed(self, *, tenant_id: str, customer_id: str) -> None:
        await self.ensure_tenant(tenant_id=tenant_id)
        await self.ensure_support_queues(tenant_id=tenant_id)
        now = datetime.now(UTC)
        created_resources: list[str] = []
        async with self._session_factory.begin() as session:
            site = await session.scalar(
                select(SupportSiteModel).where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == "default-site",
                )
            )
            if site is None:
                site = SupportSiteModel(
                    tenant_id=tenant_id,
                    site_id="default-site",
                    public_widget_id=f"site_pub_{uuid5(NAMESPACE_URL, tenant_id).hex[:24]}",
                    name="演示站点",
                    base_url="https://example.invalid",
                    allowed_origins=["https://example.invalid"],
                    widget_daily_message_limit=500,
                    status="active",
                    verification_status="verified",
                    created_at=now,
                    updated_at=now,
                )
                session.add(site)
                created_resources.append("support_site")
                await session.flush()
            registry = await session.scalar(
                select(PublicWidgetRegistryModel).where(
                    PublicWidgetRegistryModel.tenant_id == tenant_id,
                    PublicWidgetRegistryModel.site_id == "default-site",
                )
            )
            if registry is None:
                session.add(
                    PublicWidgetRegistryModel(
                        public_widget_id=site.public_widget_id,
                        tenant_id=tenant_id,
                        site_id=site.site_id,
                        key_hash=None,
                        allowed_origins=list(site.allowed_origins),
                        daily_message_limit=site.widget_daily_message_limit,
                        primary_language=site.primary_language,
                        status=site.status,
                        verification_status="verified",
                        created_at=site.created_at,
                        updated_at=site.updated_at,
                    )
                )
            customer = await session.scalar(
                select(CustomerModel).where(
                    CustomerModel.tenant_id == tenant_id,
                    CustomerModel.customer_id == customer_id,
                )
            )
            if customer is None:
                session.add(
                    CustomerModel(
                        tenant_id=tenant_id,
                        customer_id=customer_id,
                        display_name="演示客户",
                        created_at=now,
                    )
                )
                created_resources.append("customer")

            order = await session.scalar(
                select(OrderModel).where(
                    OrderModel.tenant_id == tenant_id,
                    OrderModel.order_id == "DEMO-ORDER-1001",
                )
            )
            if order is None:
                session.add(
                    OrderModel(
                        tenant_id=tenant_id,
                        order_id="DEMO-ORDER-1001",
                        customer_id=customer_id,
                        status="shipped",
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                created_resources.append("order")

            ticket = await session.scalar(
                select(SupportTicketModel).where(
                    SupportTicketModel.tenant_id == tenant_id,
                    SupportTicketModel.ticket_id == "DEMO-TICKET-2001",
                )
            )
            if ticket is None:
                session.add(
                    SupportTicketModel(
                        tenant_id=tenant_id,
                        ticket_id="DEMO-TICKET-2001",
                        customer_id=customer_id,
                        conversation_id=None,
                        status="open",
                        subject="演示支持请求",
                        source="mock_seed",
                        created_at=now,
                        updated_at=now,
                    )
                )
                created_resources.append("support_ticket")

            if created_resources:
                event_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:{customer_id}:mock-seed-v1"))
                existing_event = await session.scalar(
                    select(AuditEventModel).where(
                        AuditEventModel.tenant_id == tenant_id,
                        AuditEventModel.event_id == event_id,
                    )
                )
                if existing_event is None:
                    session.add(
                        AuditEventModel(
                            tenant_id=tenant_id,
                            event_id=event_id,
                            event_type="mock_business_data.seeded",
                            actor_subject_id="system",
                            resource_type="tenant",
                            resource_id=tenant_id,
                            details={"resources": created_resources, "data_class": "synthetic"},
                            created_at=now,
                        )
                    )
