from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración leída desde variables de entorno (o `.env` en local)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "contigo"
    app_env: Literal["local", "test", "production"] = "local"
    log_level: str = "INFO"
    port: int = 8000

    # Implementación del agente: "mock" (default) o "llm".
    agent_impl: Literal["mock", "llm"] = "mock"

    # Twilio (se usan desde feat/twilio-webhook).
    twilio_auth_token: str = ""
    # URL pública exacta que Twilio llama (p. ej. https://contigo.up.railway.app).
    # Necesaria para validar X-Twilio-Signature detrás de proxies.
    public_base_url: str = ""
    # Solo para pruebas manuales locales (curl). Prohibido en producción.
    twilio_validate_signature: bool = True

    @model_validator(mode="after")
    def _signature_validation_required_in_production(self) -> "Settings":
        if self.is_production and not self.twilio_validate_signature:
            raise ValueError("TWILIO_VALIDATE_SIGNATURE cannot be disabled in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
