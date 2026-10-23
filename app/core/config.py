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
    # Presupuesto total por turno del agente. Twilio espera ~15 s la respuesta
    # TwiML; se deja margen para el resto del procesamiento.
    agent_timeout_s: float = 12.0

    # OpenAI (se usan cuando agent_impl="llm").
    openai_api_key: str = ""
    # gpt-6.1-sol principal y gpt-6-luna de respaldo: 15/16 aciertos cada
    # uno en los 16 casos de clasificación difíciles, 3,4-4,9 s de latencia.
    openai_model: str = "gpt-6.1-sol"
    openai_fallback_model: str = "gpt-6-luna"
    # Los modelos de razonamiento solo aceptan el default; el cliente omite
    # el parámetro cuando el modelo lo rechaza.
    openai_temperature: float = 0.3
    # Medido contra el proveedor real: p50 ~5 s, máximo observado 6,4 s en
    # una respuesta válida. Con 6 s se cortaban respuestas buenas y el turno
    # se iba al modelo de respaldo sin necesidad; 7 s cubre la cola.
    openai_timeout_s: float = 7.0

    # Presupuesto total del turno. Twilio corta el webhook a los 15 s y un
    # turno NO alcanza para llamada + respaldo + reintento de validación:
    # medido contra el proveedor real, el peor caso fueron 16 s.
    # Con 13 s y un timeout de 7, el caso normal (p50 ~4,5 s) todavía deja
    # entrar el reintento de validación, que es el que atrapa una cifra
    # inventada; y el peor caso cierra en ~12 s, con margen.
    # Cuando lo que queda no alcanza para otro intento se responde con el
    # texto de respaldo del modo: determinista, validado por C, instantáneo.
    agent_turn_budget_s: float = 13.0

    # Traza del agente (una línea JSONL por turno). Guarda el mensaje
    # completo de la usuaria y la respuesta con cifras, así que viene
    # apagada: se enciende en local, con datos de demo.
    agent_trace_enabled: bool = False
    agent_trace_path: str = "turnos.jsonl"

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

    @model_validator(mode="after")
    def _trace_disabled_in_production(self) -> "Settings":
        # La traza guarda mensajes completos: en producción no se escribe a
        # disco ni por error de configuración.
        if self.is_production and self.agent_trace_enabled:
            raise ValueError("AGENT_TRACE_ENABLED cannot be enabled in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
