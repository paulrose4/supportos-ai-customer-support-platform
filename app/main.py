from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.middleware import BrowserSecurityMiddleware, PublicWidgetCorsIsolationMiddleware
from app.api.routes import (
    admin_users_router,
    analytics_router,
    chat_router,
    customer_experience_router,
    governance_router,
    handoff_router,
    health_router,
    identity_router,
    knowledge_router,
    metrics_router,
    onboarding_router,
    operations_router,
    platform_enrollment_router,
    platform_identity_router,
    presence_router,
    public_widget_router,
    realtime_router,
    site_admin_router,
    system_status_router,
    widget_assets_router,
    widget_media_router,
    widget_router,
)
from app.bootstrap.lifespan import lifespan
from app.config import get_settings
from app.observability import CorrelationIdMiddleware, RequestMetricsMiddleware


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(
        BrowserSecurityMiddleware,
        admin_cookie_name=settings.admin_session_cookie_name,
        login_completion_cookie_name=settings.login_completion_cookie_name,
        allowed_origins=settings.allowed_origins,
        enforce_origin=settings.enforce_browser_origin,
    )
    application.add_middleware(CorrelationIdMiddleware)
    application.add_middleware(RequestMetricsMiddleware)
    application.add_middleware(PublicWidgetCorsIsolationMiddleware)
    application.include_router(health_router)
    application.include_router(admin_users_router)
    application.include_router(analytics_router)
    application.include_router(identity_router)
    application.include_router(chat_router)
    application.include_router(customer_experience_router)
    application.include_router(governance_router)
    application.include_router(handoff_router)
    application.include_router(knowledge_router)
    application.include_router(metrics_router)
    application.include_router(operations_router)
    application.include_router(onboarding_router)
    application.include_router(platform_enrollment_router)
    application.include_router(platform_identity_router)
    application.include_router(public_widget_router)
    application.include_router(presence_router)
    application.include_router(realtime_router)
    application.include_router(site_admin_router)
    application.include_router(system_status_router)
    application.include_router(widget_router)
    application.include_router(widget_assets_router)
    application.include_router(widget_media_router)

    @application.exception_handler(ValueError)
    async def value_error_handler(_request, exception: ValueError):  # type: ignore[no-untyped-def]
        return JSONResponse(status_code=422, content={"detail": str(exception)})

    return application


app = create_app()
