from app.api.routes.admin_users import router as admin_users_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.chat import router as chat_router
from app.api.routes.customer_experience import router as customer_experience_router
from app.api.routes.governance import router as governance_router
from app.api.routes.handoff import router as handoff_router
from app.api.routes.health import router as health_router
from app.api.routes.identity import router as identity_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.onboarding import platform_router as platform_enrollment_router
from app.api.routes.onboarding import router as onboarding_router
from app.api.routes.operations import router as operations_router
from app.api.routes.platform_identity import router as platform_identity_router
from app.api.routes.presence import router as presence_router
from app.api.routes.public_widget import router as public_widget_router
from app.api.routes.realtime import router as realtime_router
from app.api.routes.site_admin import router as site_admin_router
from app.api.routes.system_status import router as system_status_router
from app.api.routes.widget import router as widget_router
from app.api.routes.widget_assets import router as widget_assets_router
from app.api.routes.widget_media import router as widget_media_router

__all__ = [
    "admin_users_router",
    "analytics_router",
    "chat_router",
    "customer_experience_router",
    "governance_router",
    "handoff_router",
    "identity_router",
    "health_router",
    "knowledge_router",
    "metrics_router",
    "operations_router",
    "onboarding_router",
    "platform_enrollment_router",
    "platform_identity_router",
    "public_widget_router",
    "presence_router",
    "realtime_router",
    "site_admin_router",
    "system_status_router",
    "widget_router",
    "widget_assets_router",
    "widget_media_router",
]
