import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.application.dto import BootstrapAdminCommand
from app.application.tenant_context import tenant_scope
from app.bootstrap.container import build_container
from app.config import get_settings
from app.domain.ports import ChatModelRequest
from app.observability import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    container = await build_container(settings)
    enrollment_stop = asyncio.Event()
    enrollment_task = None
    enrollment_worker = getattr(container, "enrollment_email_worker", None)
    if enrollment_worker is not None:
        enrollment_task = asyncio.create_task(
            enrollment_worker.run_forever(enrollment_stop),
            name="self-service-enrollment-email-worker",
        )
    await container.knowledge_adapter.initialize()
    product_recommendation_index = getattr(container, "product_recommendation_index", None)
    if product_recommendation_index is not None:
        await product_recommendation_index.initialize()
    if settings.embedding_provider == "fastembed" and settings.local_embedding_prewarm:
        await container.embedding_provider.embed(["local embedding startup probe"])
    if settings.llm_provider == "openai" and settings.openai_chat_prewarm:
        try:
            async with asyncio.timeout(min(settings.openai_timeout_seconds, 15.0)):
                await container.chat_model.generate(
                    ChatModelRequest(
                        messages=(
                            {
                                "role": "user",
                                "content": "Reply with exactly: OK",
                            },
                        ),
                        metadata={"task": "startup_probe"},
                        max_output_tokens=16,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            structlog.get_logger("model_prewarm").warning(
                "model_prewarm.failed",
                error_type=type(exc).__name__,
            )
    product_reranker = getattr(container, "product_reranker", None)
    if settings.product_recommendation_reranker_prewarm and product_reranker is not None:
        await product_reranker.prewarm()
    if (
        container.admin_session_service is not None
        and settings.bootstrap_admin_username
        and settings.bootstrap_admin_password is not None
    ):
        with tenant_scope(settings.default_tenant_id):
            bootstrap_user = await container.admin_session_service.bootstrap_admin(
                BootstrapAdminCommand(
                    tenant_id=settings.default_tenant_id,
                    username=settings.bootstrap_admin_username,
                    password=settings.bootstrap_admin_password.get_secret_value(),
                    display_name=settings.bootstrap_admin_display_name,
                )
            )
        if settings.bootstrap_platform_owner and container.platform_identity_service is not None:
            await container.platform_identity_service.bootstrap_platform_owner(
                user_id=bootstrap_user.user_id
            )
        if settings.bootstrap_admin_email and container.email_identity_service is not None:
            await container.email_identity_service.bootstrap_email_identity(
                user_id=bootstrap_user.user_id,
                email=settings.bootstrap_admin_email,
                password=settings.bootstrap_admin_password.get_secret_value(),
            )
    with tenant_scope(settings.default_tenant_id):
        await container.mock_business_seeder.ensure_support_queues(
            tenant_id=settings.default_tenant_id
        )
        if settings.seed_mock_business_data:
            await container.mock_business_seeder.seed(
                tenant_id=settings.default_tenant_id,
                customer_id=settings.mock_subject_id,
            )
    app.state.container = container
    start_realtime = getattr(container.realtime_hub, "start", None)
    if start_realtime is not None:
        await start_realtime()
    public_widget_events = getattr(container, "public_widget_event_hub", None)
    if public_widget_events is not None:
        await public_widget_events.start()
    yield
    if enrollment_task is not None:
        enrollment_stop.set()
        await enrollment_task
    close_realtime = getattr(container.realtime_hub, "close", None)
    if close_realtime is not None:
        await close_realtime()
    if public_widget_events is not None:
        await public_widget_events.close()
    redis_backend = getattr(container, "redis_backend", None)
    if redis_backend is not None:
        await redis_backend.close()
    await container.knowledge_adapter.close()
    if product_recommendation_index is not None:
        await product_recommendation_index.close()
    await container.database.dispose()
