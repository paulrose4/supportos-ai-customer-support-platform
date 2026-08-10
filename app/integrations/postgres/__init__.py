from app.integrations.postgres.analytics import PostgreSQLSupportAnalyticsAdapter
from app.integrations.postgres.business import (
    PostgreSQLBusinessReadAdapter,
    PostgreSQLOrderReadAdapter,
    PostgreSQLSupportTicketReadAdapter,
)
from app.integrations.postgres.conversations import PostgreSQLConversationPersistenceAdapter
from app.integrations.postgres.customer_experience import PostgreSQLCustomerExperienceAdapter
from app.integrations.postgres.email_identity import PostgreSQLEmailIdentityStore
from app.integrations.postgres.experience import PostgreSQLTenantExperienceAdapter
from app.integrations.postgres.governance import PostgreSQLAuditLogAdapter
from app.integrations.postgres.handoff import PostgreSQLQueueHandoffAdapter
from app.integrations.postgres.identity import PostgreSQLAdminIdentityStore
from app.integrations.postgres.knowledge import PostgreSQLKnowledgeControlPlaneAdapter
from app.integrations.postgres.operations import PostgreSQLSupportOperationsAdapter
from app.integrations.postgres.outbox import PostgreSQLOutboxDispatcher
from app.integrations.postgres.product_catalog import PostgreSQLProductCatalogAdapter
from app.integrations.postgres.public_widget import PostgreSQLPublicWidgetAccessAdapter
from app.integrations.postgres.resolution import PostgreSQLAutoResolutionAdapter
from app.integrations.postgres.retention import PostgreSQLRetentionAdapter
from app.integrations.postgres.schema_contract import PostgreSQLKnowledgeSchemaDependency
from app.integrations.postgres.seed import PostgreSQLMockBusinessDataSeeder
from app.integrations.postgres.self_service_onboarding import PostgreSQLSelfServiceOnboardingStore
from app.integrations.postgres.session import DatabaseSessionManager
from app.integrations.postgres.site_admin import PostgreSQLSiteAdministrationAdapter
from app.integrations.postgres.site_web_source import PostgreSQLSiteWebSourceConfigStore
from app.integrations.postgres.unit_of_work import SqlAlchemyUnitOfWork
from app.integrations.postgres.web_crawl_manifests import PostgreSQLWebCrawlManifestStore
from app.integrations.postgres.web_sync_jobs import PostgreSQLWebSyncJobStore
from app.integrations.postgres.web_sync_workers import (
    PostgreSQLActiveTenantCatalog,
    PostgreSQLWebSyncWorkerRegistry,
)
from app.integrations.postgres.widget_assets import PostgreSQLWidgetAssetRepository
from app.integrations.postgres.workforce_identity import PostgreSQLWorkforceIdentityStore

__all__ = [
    "DatabaseSessionManager",
    "PostgreSQLAdminIdentityStore",
    "PostgreSQLAutoResolutionAdapter",
    "PostgreSQLAuditLogAdapter",
    "PostgreSQLBusinessReadAdapter",
    "PostgreSQLConversationPersistenceAdapter",
    "PostgreSQLEmailIdentityStore",
    "PostgreSQLTenantExperienceAdapter",
    "PostgreSQLKnowledgeControlPlaneAdapter",
    "PostgreSQLMockBusinessDataSeeder",
    "PostgreSQLOrderReadAdapter",
    "PostgreSQLOutboxDispatcher",
    "PostgreSQLQueueHandoffAdapter",
    "PostgreSQLPublicWidgetAccessAdapter",
    "PostgreSQLProductCatalogAdapter",
    "PostgreSQLRetentionAdapter",
    "PostgreSQLSiteAdministrationAdapter",
    "PostgreSQLSiteWebSourceConfigStore",
    "PostgreSQLSupportAnalyticsAdapter",
    "PostgreSQLSupportOperationsAdapter",
    "PostgreSQLSupportTicketReadAdapter",
    "PostgreSQLWorkforceIdentityStore",
    "PostgreSQLSelfServiceOnboardingStore",
    "PostgreSQLKnowledgeSchemaDependency",
    "SqlAlchemyUnitOfWork",
    "PostgreSQLCustomerExperienceAdapter",
    "PostgreSQLWebSyncJobStore",
    "PostgreSQLActiveTenantCatalog",
    "PostgreSQLWebSyncWorkerRegistry",
    "PostgreSQLWebCrawlManifestStore",
    "PostgreSQLWidgetAssetRepository",
]
