import logging

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.modules.whatsapp.router import router as whatsapp_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.twilio_validate_signature:
        logger.warning("Twilio signature validation is DISABLED (app_env=%s)", settings.app_env)

    app = FastAPI(title="Contigo API", version="0.1.0")

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    app.include_router(whatsapp_router)

    return app


app = create_app()
