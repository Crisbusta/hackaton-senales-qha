"""T04 + T05: la implementacion del agente contra el contrato de interface.

El codigo es dueno del estado, el modelo es dueno de las palabras. Aca no se
decide ninguna transicion.

El turno son dos pasos y este modulo implementa los dos:

  classify(ClassifyRequest) -> Classification
    1. pre-filtro (capa 1): crisis explicita, sin llamar al modelo.
    2. una llamada que solo etiqueta. No recibe ninguna cifra.

  write(WriteRequest) -> AgentReply
    3. textos fijos (capa 3): crisis, rechazo y no_texto salen sin llamar al
       modelo. Antes se llamaba igual y la prosa se descartaba despues.
    4. los movimientos ya resueltos + los datos exactos del modo final, y una
       llamada que solo redacta.
    5. validar (capa 4): si falla, un reintento con la correccion; si vuelve a
       fallar, el texto de respaldo del modo.

El backend llama a los dos con su decision de estado en medio. Esa es toda la
diferencia con el orden anterior, donde el modelo redactaba con el modo de
entrada y el modo cambiaba despues.
"""

import asyncio
import logging
import time

from app.core.config import get_settings
from app.modules.agent import observability as obs
from app.modules.agent import prefilter, prompts, validators
from app.modules.agent.client import CLASSIFY_SCHEMA, WRITE_SCHEMA, call_model
from app.modules.agent.interface import (
    LIMIT_CRISIS_LINE,
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentReply,
    Classification,
    ClassifyRequest,
    ConversationEvent,
    ConversationMode,
    FieldStatus,
    Intent,
    WriteRequest,
)
from app.modules.agent.texts import ETIQUETAS_TEXTO_FIJO, TEXTOS_FIJOS
from app.shared.exceptions import IntegrationError

logger = logging.getLogger(__name__)

# El contrato nombra los estados en ingles; el motor de validacion los usa en
# espanol porque es el idioma del documento que lee Patricia.
FIELD_STATUS_ES = {
    FieldStatus.CONFIRMED: "confirmado",
    FieldStatus.ABSENT: "ausente",
    FieldStatus.UNRELIABLE: "no confiable",
}

# Etiqueta del modelo -> evento del contrato. Solo las cuatro inequivocas.
INTENT_TO_EVENT = {
    Intent.DISTRESS: ConversationEvent.CRISIS_SUPPORT_NEEDED,
    Intent.DECLINED: ConversationEvent.DECLINED_BALANCE_REVIEW,
    Intent.INFO_REQUEST: ConversationEvent.REQUESTED_BALANCE,
    Intent.WILLING_TO_EXPLORE: ConversationEvent.ACCEPTED_NEXT_STEP,
}

# Reservado para "no hubo clasificacion": el proveedor fallo. Una etiqueta que
# el modelo invente fuera de la lista viaja tal cual, para que la traza la
# registre; esta solo la pone este modulo.
SIN_CLASIFICAR = "UNKNOWN"


def _historial(mensajes):
    """Los ultimos turnos, para resolver referencias ("si", "la segunda")."""
    return [{"rol": m.role, "texto": m.content} for m in mensajes]


def _datos(request: WriteRequest) -> dict:
    """Traduce `financial_data` al formato del contexto y del validador.

    Si ella pidio explicitamente no ver cifras, no se inyecta ninguna: lo que
    no esta en el contexto no se puede filtrar, ni por error del modelo ni por
    un fallo del validador.
    """
    if request.preferences.get(PREF_SHOW_FINANCIAL_FIGURES, True) is False:
        return {}
    return {
        nombre: {"valor": campo.value, "estado": FIELD_STATUS_ES[campo.status]}
        for nombre, campo in request.financial_data.items()
    }


def _calculos(request: WriteRequest) -> dict:
    if request.preferences.get(PREF_SHOW_FINANCIAL_FIGURES, True) is False:
        return {}
    return prompts.calculos_visibles(
        request.mode.value, request.calculations,
        request.requested_installment_calculation,
    )


def _restante(started: float) -> float:
    """Lo que queda del presupuesto del turno. Acota cada llamada."""
    return get_settings().agent_turn_budget_s - (time.monotonic() - started)


def _presupuesto_para_otra_llamada(started: float) -> bool:
    return _restante(started) >= get_settings().openai_timeout_s


# --- paso 1: clasificar --------------------------------------------------


def clasificar(request: ClassifyRequest, call=call_model, log_path=None) -> Classification:
    """La etiqueta del turno. Esta llamada no ve ninguna cifra."""
    started = time.monotonic()
    disparo, patron = prefilter.revisar(request.message)
    if not disparo and request.mode is ConversationMode.SUPPORT:
        # Ya esta en APOYO: el texto de crisis le pregunto si esta a salvo y
        # este mensaje responde a eso. El umbral de ahi es mas bajo, y es una
        # regla de estado, asi que no puede vivir en el prompt.
        disparo, patron = prefilter.revisar_en_apoyo(request.message)
    if disparo:
        # Capa 1: la crisis explicita no pasa por el modelo.
        obs.registrar_clasificacion(
            conv=request.user_id, entrada=request.message, prefiltro=(True, patron),
            modo=request.mode.value, contexto=None,
            modelo={"nombre": "prefiltro", "latencia_ms": 0, "tokens_in": 0, "tokens_out": 0},
            salida_cruda=None, etiqueta=Intent.DISTRESS, ruta=log_path,
        )
        return Classification(
            intent=Intent.DISTRESS, event=ConversationEvent.CRISIS_SUPPORT_NEEDED
        )

    settings = get_settings()
    contexto = prompts.contexto_clasificacion(
        request.mode.value, request.message, _historial(request.recent_history)
    )
    try:
        crudo, metadata = call(
            prompts.CLASIFICADOR, contexto,
            schema=CLASSIFY_SCHEMA,
            model=settings.openai_classify_model or None,
            # Su reserva, no el presupuesto entero: el respaldo de este paso
            # no puede gastar lo que necesita la redaccion.
            restante=min(_restante(started), settings.openai_classify_timeout_s),
        )
    except IntegrationError as exc:
        # Sin clasificacion no hay turno que resolver. El backend deja el modo
        # como esta y `write` responde con el texto de respaldo del modo, que
        # es mucho mejor que dejarla sin respuesta. Esta es la capa 4.
        logger.warning("sin clasificacion (%s); el turno va al texto de respaldo", exc)
        return Classification(intent=SIN_CLASIFICAR)

    etiqueta = (crudo or {}).get("etiqueta") or SIN_CLASIFICAR
    obs.registrar_clasificacion(
        conv=request.user_id, entrada=request.message, prefiltro=(False, None),
        modo=request.mode.value, contexto=contexto, modelo=metadata,
        salida_cruda=crudo, etiqueta=etiqueta, ruta=log_path,
        latencia_total_ms=int((time.monotonic() - started) * 1000),
    )
    return Classification(
        intent=etiqueta,
        essential=(crudo or {}).get("esencial"),
        mentioned_amount=(crudo or {}).get("monto_mencionado"),
        pace=(crudo or {}).get("ritmo"),
        event=INTENT_TO_EVENT.get(etiqueta),
    )


# --- paso 2: redactar ----------------------------------------------------


def _fijo(texto, motivo, request, log_path):
    obs.registrar_redaccion(
        conv=request.user_id, entrada=request.message, modo=request.mode.value,
        etiqueta=request.classification.intent, contexto=None, modelo=None,
        salida_cruda=None, enviado=texto, validadores={}, regla_aplicada=motivo,
        ruta=log_path,
    )
    return AgentReply(message=texto, protected_text=True)


def redactar(request: WriteRequest, call=call_model, log_path=None) -> AgentReply:
    """La respuesta para Patricia, con el turno ya resuelto por el backend."""
    started = time.monotonic()
    etiqueta = request.classification.intent
    modo = request.mode.value

    # Capa 3: estos textos no pasan por el modelo. Antes se llamaba igual y la
    # prosa se descartaba despues: una llamada entera por nada.
    if etiqueta == Intent.DISTRESS and LIMIT_CRISIS_LINE in request.already_stated_limits:
        # Las lineas ya salieron en un turno anterior: esto responde a "¿estas
        # a salvo?", no es una primera deteccion. Repetir el texto de entrada
        # completo le dice el mismo limite dos veces (AB7).
        #
        # La condicion era `modo == APOYO`, y con el orden nuevo del turno el
        # modo que llega aca es el FINAL: una crisis siempre lo pone en APOYO,
        # asi que el texto de entrada --el unico que pregunta si esta a salvo--
        # quedo inalcanzable. Medido el 24 oct en C10 (a). Lo que distingue los
        # dos casos no es el modo sino si las lineas ya se dijeron.
        return _fijo(TEXTOS_FIJOS["angustia_fuerte_en_apoyo"],
                     "texto_fijo_angustia_fuerte_en_apoyo", request, log_path)
    if etiqueta in ETIQUETAS_TEXTO_FIJO:
        return _fijo(TEXTOS_FIJOS[etiqueta], "texto_fijo_" + etiqueta, request, log_path)
    if etiqueta == Intent.NOT_TEXT:
        return _fijo(TEXTOS_FIJOS["no_texto"], "texto_fijo_no_texto", request, log_path)
    if etiqueta == SIN_CLASIFICAR:
        # El proveedor fallo en el paso 1: no se le pide una redaccion que
        # tampoco va a llegar.
        resultado = validators.respaldo_sin_modelo({"modo": modo}, "sin_clasificacion")
        return _fijo(resultado.enviado, "sin_clasificacion", request, log_path)

    datos = _datos(request)
    calculos = _calculos(request)
    # La misma condicion con la que se inyecta MOVIMIENTO_ACLARAR: si la
    # instruccion pide la salida escrita, el validador no puede rechazarla.
    aclara_ruta = bool(request.can_clarify_route and etiqueta == Intent.AMBIGUOUS)
    movimientos = prompts.movimientos_para(
        modo,
        clasificacion=request.classification,
        puede_ofrecer=request.can_offer_continue,
        puede_aclarar=request.can_clarify_route,
        oferta_inicial_hecha=request.opening_offer_made,
        limites=request.already_stated_limits,
        escalon_maximo=(request.max_mode_reached or ConversationMode.GREETING).value,
        pidio_calculo_cuota=request.requested_installment_calculation,
        calculo_ya_mostrado=request.installment_calc_shown,
        datos=datos,
        calculos=calculos,
    )
    contexto = prompts.contexto_redaccion(
        datos, calculos, movimientos, request.message, _historial(request.recent_history)
    )

    try:
        crudo, metadata = call(prompts.REDACTOR, contexto, schema=WRITE_SCHEMA,
                               restante=_restante(started))
    except IntegrationError as exc:
        logger.warning("sin redaccion (%s); va el texto de respaldo", exc)
        estado = {"modo": modo, "puedes_ofrecer_continuar": request.can_offer_continue,
                  "puedes_aclarar_ruta": aclara_ruta}
        resultado = validators.respaldo_sin_modelo(estado, "sin_salida_del_modelo")
        return _fijo(resultado.enviado, "sin_salida_del_modelo", request, log_path)

    estado = {"modo": modo, "puedes_ofrecer_continuar": request.can_offer_continue,
              "puedes_aclarar_ruta": aclara_ruta}
    resultado = validators.validar(
        {"etiqueta": etiqueta, "mensaje": (crudo or {}).get("mensaje")},
        estado, datos, calculos or None,
    )
    intentos = 1
    if resultado.enviado is None:
        # El primer intento fallo un chequeo. Un reintento nombrandolo, pero
        # solo si cabe en el presupuesto del turno.
        if _presupuesto_para_otra_llamada(started):
            try:
                crudo, metadata = call(
                    prompts.REDACTOR, contexto, resultado.correccion,
                    schema=WRITE_SCHEMA, restante=_restante(started),
                )
                intentos = 2
            except IntegrationError as exc:
                logger.warning("el reintento fallo (%s); va el texto de respaldo", exc)
        else:
            logger.warning("sin presupuesto para el reintento; va el texto de respaldo")
        resultado = validators.validar(
            {"etiqueta": etiqueta, "mensaje": (crudo or {}).get("mensaje")},
            estado, datos, calculos or None, intento=2,
        )

    obs.registrar_redaccion(
        conv=request.user_id, entrada=request.message, modo=modo, etiqueta=etiqueta,
        contexto=contexto, modelo=metadata, salida_cruda=crudo,
        enviado=resultado.enviado, validadores=resultado.validadores,
        regla_aplicada=resultado.override["motivo"], ruta=log_path, intentos=intentos,
        movimientos=movimientos, campos=sorted(datos), calculos=sorted(calculos),
        latencia_total_ms=int((time.monotonic() - started) * 1000),
    )
    return AgentReply(
        message=resultado.enviado, protected_text=resultado.override["aplicado"]
    )


class LlmAgent:
    """Implementa el protocolo `Agent` de interface.py."""

    def __init__(self, call=call_model, log_path: str | None = None):
        self._call = call
        self._log_path = log_path

    async def classify(self, request: ClassifyRequest) -> Classification:
        # La llamada al proveedor es bloqueante: va a un hilo para no frenar el
        # event loop mientras responde.
        return await asyncio.to_thread(clasificar, request, self._call, self._log_path)

    async def write(self, request: WriteRequest) -> AgentReply:
        return await asyncio.to_thread(redactar, request, self._call, self._log_path)
