"""T04 + T05: la implementación del agente contra el contrato de interface.

El código es dueño del estado, el modelo es dueño de las palabras. Acá no
se decide ninguna transición: se traduce el `AgentRequest` que el backend
ya calculó a los tres bloques que ve el modelo, y la salida del modelo a
un `AgentResponse`.

Orden de un turno:
  1. pre-filtro (capa 1): crisis explícita, sin llamar al modelo.
  2. armar el contexto (T04) y llamar al modelo.
  3. validar (capas 3-4): si falla, un reintento con la corrección.
  4. registrar la traza (una línea JSONL por turno).
"""

import asyncio
import logging
import time

from app.core.config import get_settings
from app.modules.agent import observability as obs
from app.modules.agent import prefilter, prompts, validators
from app.modules.agent.client import call_model
from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentRequest,
    AgentResponse,
    ConversationEvent,
    ConversationMode,
    FieldStatus,
)
from app.shared.exceptions import IntegrationError

logger = logging.getLogger(__name__)

# El contrato nombra los estados en inglés; el motor de validación los usa
# en español porque es el idioma del documento que lee Patricia.
FIELD_STATUS_ES = {
    FieldStatus.CONFIRMED: "confirmado",
    FieldStatus.ABSENT: "ausente",
    FieldStatus.UNRELIABLE: "no confiable",
}

# Etiqueta del modelo -> evento del contrato. Solo se mapean las cuatro
# inequívocas. `REQUESTED_SUMMARY` y `AVOID_FINANCIAL_REVIEW` quedan sin
# usar a propósito: los definió el backend y su semántica exacta no está
# acordada todavía, así que inventarla acá sería adivinar.
INTENT_TO_EVENT = {
    "angustia_fuerte": ConversationEvent.CRISIS_SUPPORT_NEEDED,
    "rechazo_pausa": ConversationEvent.DECLINED_BALANCE_REVIEW,
    "solicitud_informacion": ConversationEvent.REQUESTED_BALANCE,
    "disposicion_explorar": ConversationEvent.ACCEPTED_NEXT_STEP,
}


def _build_state(request: AgentRequest) -> dict:
    max_mode = request.max_mode_reached or ConversationMode.GREETING
    return {
        "modo": request.mode.value,
        "puedes_ofrecer_continuar": request.can_offer_continue,
        "puedes_aclarar_ruta": request.can_clarify_route,
        "limites_ya_dichos": list(request.already_stated_limits),
        "escalon_maximo": max_mode.value,
    }


def _build_data(request: AgentRequest) -> dict:
    """Traduce `financial_data` al formato del validador.

    Si ella pidió explícitamente no ver cifras, no se inyecta ninguna: lo
    que no está en el contexto no se puede filtrar, ni por error del
    modelo ni por un fallo del validador.
    """
    if request.preferences.get(PREF_SHOW_FINANCIAL_FIGURES, True) is False:
        return {}
    return {
        name: {"valor": field.value, "estado": FIELD_STATUS_ES[field.status]}
        for name, field in request.financial_data.items()
    }


def _build_calculations(request: AgentRequest) -> dict:
    """Los cálculos admisibles en este turno.

    `calculations` llega del backend sin pasar por el filtro por modo, así
    que se filtra acá por el modo y por la preferencia. Lo que esta función
    descarta no entra al contexto ni autoriza ninguna cifra en el
    validador: las dos cosas leen el mismo diccionario.
    """
    if request.preferences.get(PREF_SHOW_FINANCIAL_FIGURES, True) is False:
        return {}
    return prompts.calculos_visibles(
        request.mode.value,
        request.calculations,
        request.requested_installment_calculation,
    )


def _backup_response(request, state, motivo, started, log_path):
    """Respuesta determinista cuando el modelo no dio nada."""
    result = validators.respaldo_sin_modelo(state, motivo)
    obs.registrar(
        conv=request.user_id,
        intentos=1,
        latencia_total_ms=int((time.monotonic() - started) * 1000),
        turno=0,
        entrada=request.message,
        prefiltro=(False, None),
        estado_antes=state,
        contexto=None,
        modelo={"nombre": motivo, "temp": None, "latencia_ms": None,
                "tokens_in": 0, "tokens_out": 0},
        salida_cruda=None,
        resultado=result,
        estado_despues=state,
        regla_aplicada=motivo,
        ruta=log_path,
    )
    return AgentResponse(
        message=result.enviado, intent="UNKNOWN", event=None, protected_text=True
    )


def _budget_left_for_one_more_call(started: float) -> bool:
    """¿Alcanza el presupuesto del turno para otra llamada al modelo?"""
    settings = get_settings()
    elapsed = time.monotonic() - started
    return settings.agent_turn_budget_s - elapsed >= settings.openai_timeout_s


def _build_history(request: AgentRequest) -> list[dict]:
    """Los últimos turnos, para resolver referencias ("sí", "la segunda").

    No es fuente de cifras: `prompts` las enmascara antes de inyectarlas.
    """
    return [
        {"rol": message.role, "texto": message.content}
        for message in request.recent_history
    ]


def run_turn(request: AgentRequest, call=call_model, log_path: str | None = None):
    """Un turno completo, sincrónico. `call` se inyecta en los tests."""
    started = time.monotonic()
    state = _build_state(request)
    data = _build_data(request)
    calculations = _build_calculations(request)
    history = _build_history(request)

    triggered, pattern = prefilter.revisar(request.message)
    context = None
    metadata = {
        "nombre": "prefiltro",
        "temp": None,
        "latencia_ms": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }

    if triggered:
        # Capa 1: la crisis explícita no pasa por el modelo.
        raw = {
            "etiqueta": "angustia_fuerte",
            "esencial": None,
            "monto_mencionado": None,
            "mensaje": None,
        }
    else:
        context = prompts.armar_contexto(
            state,
            data,
            calculations or None,
            request.previous_intent,
            request.requested_installment_calculation,
            history,
        )
        try:
            raw, metadata = call(
                prompts.ESTATICO,
                context + "\n\nMENSAJE DE PATRICIA: " + request.message,
            )
        except IntegrationError as exc:
            # El proveedor fallo o el presupuesto se agoto. No se propaga:
            # hay un texto de respaldo del modo, validado por C, y es mucho
            # mejor que dejarla sin respuesta. Esta es la capa 4.
            logger.warning("sin salida del modelo (%s); va el texto de respaldo", exc)
            return _backup_response(
                request, state, "sin_salida_del_modelo", started, log_path
            )

    result = validators.validar(
        raw, state, data, calculations or None,
        pidio_calculo_cuota=request.requested_installment_calculation,
    )
    attempts = 1

    if result.enviado is None:
        # El primer intento falló un chequeo. Un reintento nombrándolo, pero
        # solo si cabe en el presupuesto: medido contra el proveedor real, un
        # turno con respaldo y reintento llegó a 16 s y Twilio corta a los
        # 15. Si no cabe, se re-valida como segundo intento, que devuelve el
        # texto de respaldo del modo sin llamar a nadie.
        if _budget_left_for_one_more_call(started):
            try:
                raw, metadata = call(
                    prompts.ESTATICO,
                    context + "\n\nMENSAJE DE PATRICIA: " + request.message,
                    result.correccion,
                )
                attempts = 2
            except IntegrationError as exc:
                logger.warning("el reintento fallo (%s); va el texto de respaldo", exc)
                return _backup_response(
                    request, state, "reintento_sin_salida", started, log_path
                )
        else:
            logger.warning(
                "sin presupuesto para el reintento de validacion; va el texto de respaldo"
            )
        result = validators.validar(
            raw, state, data, calculations or None, intento=2,
            pidio_calculo_cuota=request.requested_installment_calculation,
        )

    if triggered:
        applied_rule = "prefiltro_" + pattern
    elif result.override["aplicado"]:
        applied_rule = result.override["motivo"]
    else:
        applied_rule = None

    obs.registrar(
        conv=request.user_id,
        intentos=attempts,
        latencia_total_ms=int((time.monotonic() - started) * 1000),
        turno=0,  # el backend es dueño del contador; acá no se infiere
        entrada=request.message,
        prefiltro=(triggered, pattern),
        estado_antes=state,
        contexto=context,
        modelo=metadata,
        salida_cruda=raw,
        resultado=result,
        estado_despues=state,
        regla_aplicada=applied_rule,
        ruta=log_path,
    )

    intent = (raw or {}).get("etiqueta") or "UNKNOWN"
    return AgentResponse(
        message=result.enviado,
        intent=intent,
        event=INTENT_TO_EVENT.get(intent),
        essential=(raw or {}).get("esencial"),
        mentioned_amount=(raw or {}).get("monto_mencionado"),
        protected_text=result.override["aplicado"],
    )


class LlmAgent:
    """Implementa el protocolo `Agent` de interface.py."""

    def __init__(self, call=call_model, log_path: str | None = None):
        self._call = call
        self._log_path = log_path

    async def process(self, request: AgentRequest) -> AgentResponse:
        # La llamada al proveedor es bloqueante: va a un hilo para no
        # frenar el event loop mientras responde (3-5 s por turno).
        return await asyncio.to_thread(
            run_turn, request, self._call, self._log_path
        )
