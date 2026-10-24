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

    # Escenario ficticio del banco simulado (app/modules/banking/fixtures/<nombre>.json).
    # Vacío: ningún usuario tiene banco vinculado.
    mock_bank_scenario: str = "patricia"

    # Números (E.164, separados por coma) que pueden escribir "/reiniciar" para
    # borrar su propia conversación. Para ensayar la demo. Vacío: desactivado.
    demo_admin_numbers: str = ""

    # Contraseña del dashboard de progreso (/dashboard, autenticación básica).
    # Vacía: el dashboard está desactivado (responde 404).
    dashboard_password: str = ""

    # PostgreSQL para el estado de las conversaciones (sobrevive a los redeploys).
    # Vacía: en memoria, se pierde con cada reinicio. Nunca se escribe en los logs.
    database_url: str = ""

    # OpenAI (se usan cuando agent_impl="llm").
    openai_api_key: str = ""
    # gpt-6.1-sol principal y gpt-6-luna de respaldo: 15/16 aciertos cada
    # uno en los 16 casos de clasificación difíciles, 3,4-4,9 s de latencia.
    openai_model: str = "gpt-6.1-sol"
    openai_fallback_model: str = "gpt-6-luna"
    # Los modelos de razonamiento solo aceptan el default; el cliente omite
    # el parámetro cuando el modelo lo rechaza.
    openai_temperature: float = 0.3
    # Medido contra el proveedor real: p50 ~4,5 s, pero la cola llega a 6,8 s
    # en respuestas perfectamente válidas, así que 7 s cortaba 2 de cada 8.
    # Cada corte degrada el turno al texto de respaldo del modo. Conviene un
    # timeout holgado: lo que se sacrifica es el reintento de validación, que
    # de todos modos falla hacia ese mismo texto.
    # Tiene que seguir cabiendo en `agent_turn_budget_s`.
    openai_timeout_s: float = 9.0

    # Margen que el agente le deja al resto del turno (validar, registrar,
    # armar el TwiML) dentro del presupuesto de `agent_timeout_s`.
    agent_budget_margin_s: float = 1.0

    # Traza del agente (una línea JSONL por turno). Guarda el mensaje
    # completo de la usuaria y la respuesta con cifras, así que viene
    # apagada: se enciende en local, con datos de demo.
    agent_trace_enabled: bool = False
    agent_trace_path: str = "turnos.jsonl"

    # Notas de voz: transcripción local con faster-whisper (el audio no sale del
    # servidor). Apagadas por defecto en el código: sin el modelo en disco, el
    # primer audio lo bajaría de Hugging Face. La imagen Docker trae el modelo y
    # las enciende (VOICE_ENABLED=true, VOICE_MODEL_DIR=/app/models/...).
    voice_enabled: bool = False
    voice_model: str = "small"
    # Carpeta del modelo ya descargado. Con valor, nunca se consulta Hugging Face.
    voice_model_dir: str = ""
    voice_max_seconds: float = 60.0
    # Descarga + transcripción. Holgado: la respuesta irá fuera de banda (~30 s);
    # con la respuesta TwiML síncrona Twilio corta a ~15 s.
    voice_timeout_s: float = 20.0
    # Hilos de CPU para Whisper (0 = los que decida CTranslate2).
    voice_cpu_threads: int = 0

    # Twilio (se usan desde feat/twilio-webhook).
    twilio_auth_token: str = ""
    # Necesario para descargar adjuntos (el PDF del estado de cuenta). No es secreto.
    twilio_account_sid: str = ""
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
    def agent_turn_budget_s(self) -> float:
        """Lo que el agente puede gastar antes de que el backend lo corte.

        Deriva de `agent_timeout_s`, que es el `asyncio.wait_for` de
        ConversationService: si fueran dos variables independientes y la del
        backend quedara más baja, este presupuesto no se aplicaría nunca.
        El dueño del plazo es el backend; acá solo se le resta el margen.
        """
        return self.agent_timeout_s - self.agent_budget_margin_s

    @property
    def demo_admins(self) -> frozenset[str]:
        return frozenset(n.strip() for n in self.demo_admin_numbers.split(",") if n.strip())

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
