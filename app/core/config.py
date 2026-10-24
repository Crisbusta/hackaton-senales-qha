from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Lo minimo que necesita la redaccion para intentar. Por debajo de esto el
# turno sale con el texto de respaldo del modo igual, asi que la llamada solo
# gastaria.
MIN_REDACCION_S = 3.0


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
    #
    # Son DOS llamadas por turno (clasificar y redactar), cada una con su
    # reserva: el peor caso del turno es la suma (5 + 7 = 12 s) y tiene que
    # caber aqui menos el margen. Quedan ~2 s de holgura contra la ventana de
    # Twilio, y despues de eso solo falta armar el TwiML.
    # Medido contra el proveedor real: 2,0-6,6 s por llamada, 5-9 s el turno.
    agent_timeout_s: float = 13.0

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
    # El clasificador puede usar un modelo distinto del redactor. Vacio = el
    # mismo. Medido: el barato da 0,72 s pero 9 de 12 etiquetas, y su unico
    # error grave fue no aplicar la prioridad del rechazo. Queda como perilla
    # para poder medirlo sin tocar codigo.
    openai_classify_model: str = ""
    # Plazo de la llamada de clasificacion. Es la corta: menos contexto y
    # cuatro campos de salida. Medido contra el proveedor real, 2,0-6,5 s, asi
    # que con 5 se cortaban clasificaciones validas.
    #
    # Funciona como RESERVA, no como limite suelto: la clasificacion no puede
    # gastar mas que esto ni con su modelo de respaldo, porque lo que sobra es
    # lo que necesita la redaccion. Medido en vivo con 4 s: el principal se
    # corto, el respaldo corrio con el presupuesto completo, tardo 6,4 s y la
    # redaccion de E4 se quedo sin plazo.
    openai_classify_timeout_s: float = 7.0
    openai_temperature: float = 0.3
    # Techo de la llamada de REDACCION, que es la cara: mas contexto y una
    # respuesta larga. Medido, 2,0 s en E1 y mas de 7 s en E2 y E4, asi que 7
    # cortaba respuestas validas.
    #
    # No es una reserva fija: `client.plazo()` lo acota con lo que queda del
    # turno, asi que el total sigue cerrado aunque este numero sea alto. En el
    # caso tipico la clasificacion gasta 3 s y la redaccion se queda con 9.
    openai_timeout_s: float = 9.0

    # Techo de tokens de la redaccion (incluye los de razonamiento, que son
    # la mayoria). Red de seguridad, no un limite de estilo: la latencia es
    # casi lineal en los tokens, y un turno que se desboca se come el
    # presupuesto entero antes de cortarse. Medido el 24 oct: el turno mas
    # largo y legitimo es E3 con 295 tokens y 6,5 s, asi que 450 deja la mitad
    # de aire y queda igual por debajo de lo que alcanza a caber en los 9 s de
    # `openai_timeout_s`. Pasarse rompe el JSON y el turno sale con el texto de
    # respaldo del modo: el mismo final que el timeout, pero al instante.
    openai_max_output_tokens: int = 450

    # Margen que el agente le deja al resto del turno (validar, registrar,
    # armar el TwiML) dentro del presupuesto de `agent_timeout_s`.
    agent_budget_margin_s: float = 1.0

    # Traza del agente (una línea JSONL por turno). Guarda el mensaje
    # completo de la usuaria y la respuesta con cifras, así que viene
    # apagada: se enciende en local, con datos de demo.
    agent_trace_enabled: bool = False
    agent_trace_path: str = "turnos.jsonl"

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
    def _turn_budget_fits_the_model_call(self) -> "Settings":
        """El presupuesto del turno tiene que alcanzar para una llamada.

        `agent_turn_budget_s` es `AGENT_TIMEOUT_S − AGENT_BUDGET_MARGIN_S`, y
        dentro de eso caben la clasificacion y la redaccion. Si no alcanza para
        las dos, cada turno degrada al texto de respaldo **en silencio**: no hay
        error, solo respuestas genericas. Esto lo convierte en un fallo de
        arranque, que es donde se ve.
        """
        # Lo que de verdad tiene que caber: la reserva de la clasificacion mas
        # lo minimo para que la redaccion intente. No se exigen los dos techos
        # enteros, porque no son simetricos y el de la redaccion se acota con lo
        # que queda del turno (`client.plazo`): pedirlos obligaria a un
        # presupuesto que no cabe en la ventana de Twilio.
        necesario = self.openai_classify_timeout_s + MIN_REDACCION_S
        if self.agent_turn_budget_s < necesario:
            raise ValueError(
                f"AGENT_TIMEOUT_S={self.agent_timeout_s} menos "
                f"AGENT_BUDGET_MARGIN_S={self.agent_budget_margin_s} deja "
                f"{self.agent_turn_budget_s:.1f}s de presupuesto, y hacen falta "
                f"{necesario:.1f}s: el turno son dos llamadas, "
                f"OPENAI_CLASSIFY_TIMEOUT_S={self.openai_classify_timeout_s} + "
                f"OPENAI_TIMEOUT_S={self.openai_timeout_s}. Sin esto el turno "
                f"degrada al texto de respaldo sin avisar."
            )
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
