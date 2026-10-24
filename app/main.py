import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.modules.agent.factory import build_agent
from app.modules.banking.provider import build_bank_provider
from app.modules.conversation.repository import build_repository
from app.modules.conversation.service import ConversationService
from app.modules.dashboard.router import router as dashboard_router
from app.modules.whatsapp.media import build_media_fetcher
from app.modules.whatsapp.router import router as whatsapp_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.twilio_validate_signature:
        logger.warning("Twilio signature validation is DISABLED (app_env=%s)", settings.app_env)

    # Composición: aquí se eligen las implementaciones concretas. Un repositorio
    # por instancia de app, compartido por el servicio y el dashboard (en
    # memoria si no hay DATABASE_URL: se pierde al reiniciar).
    repository = build_repository(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Solo PostgreSQL tiene pool que abrir (y tablas que crear).
        open_ = getattr(repository, "open", None)
        if open_ is not None:
            await open_()
        try:
            yield
        finally:
            close = getattr(repository, "close", None)
            if close is not None:
                await close()

    app = FastAPI(title="Contigo API", version="0.1.0", lifespan=lifespan)
    app.state.repository = repository
    app.state.message_handler = ConversationService(
        agent=build_agent(settings),
        repository=repository,
        agent_timeout_s=settings.agent_timeout_s,
        classify_timeout_s=settings.openai_classify_timeout_s,
        bank=build_bank_provider(settings),
        demo_admins=settings.demo_admins,
        media_fetcher=build_media_fetcher(settings),
    )

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    app.include_router(whatsapp_router)
    app.include_router(dashboard_router)

    return app


app = create_app()
