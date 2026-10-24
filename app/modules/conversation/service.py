"""Coordinador del flujo conversacional.

Un turno son tres pasos, en este orden:

  1. CLASIFICAR: el agente etiqueta el mensaje. Esa llamada no recibe ninguna
     cifra, así que no puede filtrar un monto.
  2. DECIDIR: este servicio aplica sus reglas deterministas —evento, modo,
     anti-bucle, simulación— y queda con el modo FINAL del turno.
  3. REDACTAR: el agente escribe, con los datos exactos de ese modo final.

El orden importa y es el cambio de fondo. Mientras fue un solo paso, el
modelo redactaba con el modo de ENTRADA y el modo cambiaba después: de ahí
venían el turno de atraso, la precarga con su verificación posterior, la
detección previa de T10 como forma de cambiar el modo, y el re-llamado al
entrar a un modo. Los cuatro mecanismos existían para compensar el orden y
desaparecen con él.

El agente interpreta y redacta; el estado (modo, preferencias) lo decide
este servicio.
"""

import asyncio
import logging
import re
import time

from app.core.logging import mask_phone
from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    Agent,
    AgentReply,
    Classification,
    ClassifyRequest,
    ConversationEvent,
    ConversationMessage,
    ConversationMode,
    FinancialField,
    Intent,
    WriteRequest,
    calculations_allowed,
    fields_for_mode,
)
from app.modules.agent.validators import es_oferta_de_continuar
from app.modules.banking.provider import BankDataProvider
from app.modules.banking.schemas import BankSnapshot
from app.modules.banking.service import credit_card_fields
from app.modules.conversation import (
    figures,
    progress,
    simulation,
    statement_intake,
    transitions,
)
from app.modules.conversation.repository import ConversationRepository
from app.modules.conversation.schemas import ConversationState, RecordedEvent, utc_now
from app.modules.whatsapp.media import MediaFetcher
from app.modules.whatsapp.schemas import IncomingMessage
from app.shared.exceptions import IntegrationError

logger = logging.getLogger(__name__)

# Mensajes (no turnos) de historial que se envían al agente: 2 turnos.
RECENT_HISTORY_MESSAGES = 4

# Montos tipo "$74.350", "74.350" o "74350". Conservador a propósito: ante la
# duda, una respuesta sin cifras es preferible a mostrar un monto no deseado.
_AMOUNT_PATTERN = re.compile(r"\$\s*\d|\b\d{1,3}(?:\.\d{3})+\b|\b\d{4,}\b")

# Opciones numeradas en la respuesta, una por línea. AB8 las cuenta cuando
# salen de verdad, no cuando se habilitan.
RE_OPCIONES = re.compile(r"(?m)^\s*[123][.)]\s")

# Si la respuesta trae cifras que no corresponden al modo y no se pudo
# corregir a tiempo. Pendiente de validación de tono por C.
STEP_BY_STEP_REPLY = "Vamos de a poco. ¿Quieres que lo veamos paso a paso?"

# Tiempo mínimo que debe quedar del presupuesto para intentar un reintento.
MIN_RETRY_S = 3.0

RESET_COMMANDS = frozenset({"/reiniciar", "/reset"})
DEMO_RESET_REPLY = "Listo, la conversación empieza de cero. (modo demo)"

NO_FIGURES_REPLY = (
    "Dejemos los números de lado por ahora, como me pediste. Aquí estoy cuando quieras retomarlo."
)


def apply_event(state: ConversationState, event: ConversationEvent | None, sid: str) -> None:
    """Reglas deterministas evento → estado.

    La preferencia explícita más reciente prevalece sobre las anteriores.
    Las transiciones entre escalones E0-E4 quedan pendientes de la tabla de
    transiciones (T01/T06); aquí solo se aplican las que el contrato fija.
    """
    if event is None:
        return
    state.events.append(RecordedEvent(event=event, message_sid=sid))

    match event:
        case ConversationEvent.DECLINED_BALANCE_REVIEW:
            state.preferences[PREF_SHOW_FINANCIAL_FIGURES] = False
            state.mode = ConversationMode.PAUSE
        case ConversationEvent.REQUESTED_BALANCE:
            state.preferences[PREF_SHOW_FINANCIAL_FIGURES] = True
        case ConversationEvent.CRISIS_SUPPORT_NEEDED:
            state.mode = ConversationMode.SUPPORT


def contains_amount(text: str) -> bool:
    return bool(_AMOUNT_PATTERN.search(text))


class ConversationService:
    """Implementa `MessageHandler` del módulo WhatsApp."""

    @staticmethod
    def _can_offer_continue(state: ConversationState) -> bool:
        """AB2: una oferta de "¿seguimos?" por escalón."""
        if state.mode not in transitions.MODOS_CON_OFERTA:
            return False
        return state.offers_by_mode.get(state.mode.value, 0) == 0

    @staticmethod
    def _can_clarify_route(state: ConversationState) -> bool:
        """AB1 y AB8: una aclaración por tema, y las opciones numeradas una
        vez cuando el reconocimiento falló dos veces seguidas.

        Las dos reglas comparten el campo porque las dos son "aclarar una
        sola vez"; si alguna vez necesitan comportamientos distintos, hace
        falta un campo propio en el contrato.
        """
        if state.recognition_failures >= 2:
            return not state.numbered_options_offered
        return state.recognition_failures > 0

    @staticmethod
    def _apply_transition(state: ConversationState, classification: Classification) -> None:
        """El modo de ESTE turno, más AB6.

        Antes era "el modo del próximo turno", porque el agente ya había
        redactado cuando esto corría. Ahora corre entre clasificar y redactar:
        el modo que decide es el que el redactor va a usar.
        """
        state.mode = transitions.siguiente_modo(
            state.mode, state.max_mode_reached, classification.intent, classification.event
        )
        state.max_mode_reached = transitions.subir_maximo(state.max_mode_reached, state.mode)

    @staticmethod
    def _record_anti_loop(
        state: ConversationState,
        classification: Classification,
        reply: str,
    ) -> None:
        """Lo que hay que recordar para no repetirse en el próximo turno."""
        # AB2: si la oferta salió, se consume en el escalón donde salió. El
        # reconocimiento es el mismo que usa el validador para rechazarla: con
        # dos patrones distintos, ampliar uno solo deja la asimetría peor.
        #
        # El escalón es el FINAL del turno, que es el modo con el que el
        # redactor escribió. Con el modo de ENTRADA el contador se acreditaba
        # al escalón anterior: medido el 24 oct en C02, la oferta del turno 3
        # (E1 → E2) quedó anotada en E1, E2 nunca registró ninguna y el turno
        # 4 volvió a ofrecer "¿Seguimos o lo dejamos aquí?" en el mismo
        # escalón. Es el ultimo resto del turno de atraso: mientras el modelo
        # redactaba con el modo de entrada, `previous_mode` era el correcto.
        if state.mode in transitions.MODOS_CON_OFERTA and es_oferta_de_continuar(reply):
            key = state.mode.value
            state.offers_by_mode[key] = state.offers_by_mode.get(key, 0) + 1

        # AB7: la línea de crisis se ofrece una vez.
        if "*4141" in reply and transitions.LIMITE_LINEA_CRISIS not in state.stated_limits:
            state.stated_limits.append(transitions.LIMITE_LINEA_CRISIS)

        # AB7: y la oferta de mirar el estado de cuenta por ella, también.
        if "estado de cuenta" in reply.lower():
            state.opening_offer_made = True

        # AB8: el contador vuelve a 0 cuando entiende.
        if classification.intent == Intent.AMBIGUOUS:
            state.recognition_failures += 1
            # El flag se marca cuando las opciones SALEN, no cuando se
            # habilitan. Se marcaba al llegar al segundo fallo, antes de
            # ofrecerlas: desde el tercer mensaje confuso se le decía "no
            # puedes aclarar" y nunca las recibía.
            if RE_OPCIONES.search(reply):
                state.numbered_options_offered = True
        else:
            state.recognition_failures = 0
            state.numbered_options_offered = False

    def __init__(
        self,
        agent: Agent,
        repository: ConversationRepository,
        agent_timeout_s: float,
        bank: BankDataProvider | None = None,
        demo_admins: frozenset[str] = frozenset(),
        media_fetcher: MediaFetcher | None = None,
        classify_timeout_s: float | None = None,
    ) -> None:
        self._agent = agent
        self._repository = repository
        self._agent_timeout_s = agent_timeout_s
        # La reserva del paso de clasificación. Sin valor, se le da el turno
        # completo: es lo que hacen los tests que no miden tiempos.
        self._classify_timeout_s = (
            classify_timeout_s if classify_timeout_s is not None else agent_timeout_s
        )
        self._bank = bank
        self._demo_admins = demo_admins
        self._media_fetcher = media_fetcher

    async def _snapshot(self, state: ConversationState, ctx: str) -> BankSnapshot | None:
        """Datos del banco para este turno, o None si no corresponde usarlos.

        Si el usuario pidió no ver cifras, ni se consultan. Si el banco falla,
        el turno sigue sin cifras en vez de caerse.
        """
        if self._bank is None or state.preferences.get(PREF_SHOW_FINANCIAL_FIGURES) is False:
            return None
        try:
            return await self._bank.get_snapshot(state.user_id)
        except Exception:
            logger.warning(
                "Bank data unavailable, continuing without figures %s", ctx, exc_info=True
            )
            return None

    @staticmethod
    def _financial_data(
        state: ConversationState,
        snapshot: BankSnapshot | None,
        simulated: dict[str, FinancialField],
        intent: str | None,
    ) -> dict[str, FinancialField]:
        """Exactamente los campos del modo final. Sin precarga.

        Fuentes: el banco (con consentimiento) y el estado de cuenta en PDF que ella
        envió, unidos por `statement_intake.merge_sources` (el PDF manda).

        La precarga existía porque el modo se decidía después de redactar: se
        mandaba toda la Escalera y se verificaba la respuesta a posteriori. Con
        el modo final ya resuelto no hace falta, y con ella se va el bloque
        "solo si ella lo pide", que era la causa 1 de las respuestas vagas: el
        modelo tenía la cifra y una instrucción ambigua sobre cuándo decirla.

        `intent` es la etiqueta de ESTE turno, no la del anterior. Los datos
        condicionales (el monto facturado, la tasa) se habilitan en el turno en
        que ella pregunta, que es lo que siempre debieron hacer: `previous_intent`
        era un parche por tener la etiqueta demasiado tarde.
        """
        if state.preferences.get(PREF_SHOW_FINANCIAL_FIGURES) is False:
            return {}
        bank = (
            credit_card_fields(snapshot.credit_cards[0])
            if snapshot and snapshot.credit_cards
            else {}
        )
        available = statement_intake.merge_sources(bank, state.statement) | simulated
        if not available:
            return {}
        sent = fields_for_mode(state.mode, intent, state.installment_calc_requested)
        return {name: available[name] for name in sent if name in available}

    async def _receive_statement(
        self, state: ConversationState, message: IncomingMessage, url: str, ctx: str
    ) -> str:
        assert self._media_fetcher is not None
        result = await statement_intake.receive_statement(self._media_fetcher, url, ctx)
        if result.statement is not None:
            state.statement = result.statement
        state.history += [
            ConversationMessage(role="user", content="(envió su estado de cuenta en PDF)"),
            ConversationMessage(role="assistant", content=result.reply),
        ]
        state.turn_count += 1
        reached = [progress.SHARED_STATEMENT] if result.statement is not None else []
        progress.record_system_turn(state, f"pdf_{result.outcome}", reached)
        state.updated_at = utc_now()
        await self._repository.save(state)
        return result.reply

    async def _classify(self, request: ClassifyRequest, timeout_s: float) -> Classification:
        try:
            return await asyncio.wait_for(self._agent.classify(request), timeout=timeout_s)
        except TimeoutError as exc:
            raise IntegrationError(f"Classify timed out after {timeout_s:.1f}s") from exc
        except Exception as exc:
            raise IntegrationError("Classify failed") from exc

    async def _write(self, request: WriteRequest, timeout_s: float) -> AgentReply:
        try:
            return await asyncio.wait_for(self._agent.write(request), timeout=timeout_s)
        except TimeoutError as exc:
            raise IntegrationError(f"Write timed out after {timeout_s:.1f}s") from exc
        except Exception as exc:
            raise IntegrationError("Write failed") from exc

    @staticmethod
    def _unexpected_figures(
        state: ConversationState,
        request: WriteRequest,
        reply: AgentReply,
        own_message: str,
    ) -> set[int]:
        """Red de seguridad: montos que la respuesta no podía mencionar.

        Con el modo final resuelto, el redactor recibe exactamente los campos
        que puede decir, así que esto ya no corrige un desajuste de orden: es
        defensa en profundidad. Sin reintento — si salta, algo está mal arriba
        y la respuesta no se envía.
        """
        if reply.protected_text:
            return set()
        own = [own_message]
        if request.classification.mentioned_amount is not None:
            own.append(str(request.classification.mentioned_amount))
        permitidos = figures.allowed_amounts(
            request.financial_data, list(request.financial_data), own, request.calculations
        )
        return figures.unexpected_amounts(reply.message, permitidos)

    async def handle(self, message: IncomingMessage) -> str | None:
        """Devuelve la respuesta, o None si el mensaje ya fue procesado."""
        ctx = f"sid={message.message_sid} user={mask_phone(message.user_id)}"

        if not await self._repository.claim_message(message.message_sid):
            logger.info("Duplicate message ignored %s", ctx)
            return None

        # Modo demo: un número autorizado puede reiniciar su propia conversación.
        # Para cualquier otro número, "/reiniciar" es un mensaje más.
        if message.user_id in self._demo_admins and message.body.strip().lower() in RESET_COMMANDS:
            await self._repository.reset(message.user_id)
            logger.info("Demo reset %s", ctx)
            return DEMO_RESET_REPLY

        state = await self._repository.get(message.user_id)

        # T07: si envió el PDF de su estado de cuenta, se lee y se confirma sin cifras.
        attachment = statement_intake.pdf_attachment(message)
        if attachment is not None and self._media_fetcher is not None:
            return await self._receive_statement(state, message, attachment.url, ctx)

        # AB8: a un acuse vacío se responde una vez; el siguiente no recibe
        # respuesta. Ahorra las dos llamadas al modelo y evita el bucle de cortesía.
        if transitions.es_acuse_vacio(message.body):
            if state.acknowledgement_answered:
                logger.info("Empty acknowledgement, no reply (AB8) %s", ctx)
                return None
            state.acknowledgement_answered = True
        else:
            state.acknowledgement_answered = False

        started = time.monotonic()
        previous_mode = state.mode

        # --- 1. CLASIFICAR -------------------------------------------------
        # Esta llamada no recibe ninguna cifra. El historial va enmascarado por
        # el agente antes de entrar al contexto.
        # El plazo de este paso es su reserva, no el presupuesto del turno: lo
        # que sobra es lo que necesita la redacción. El agente aplica la misma
        # reserva hacia dentro (también a su modelo de respaldo); esto es el
        # corte de afuera, para que una llamada colgada no se lleve el turno.
        try:
            classification = await self._classify(
                ClassifyRequest(
                    user_id=message.user_id,
                    message=message.body,
                    mode=state.mode,
                    recent_history=state.history[-RECENT_HISTORY_MESSAGES:],
                ),
                min(self._classify_timeout_s, self._agent_timeout_s),
            )
        except IntegrationError:
            # Sin etiqueta el turno no se puede resolver, pero sí se puede
            # responder: el modo sigue como está y el redactor corta al texto
            # de respaldo, que C validó. Antes esto subía al webhook y ella
            # recibía "Perdona, tuve un problema para responderte", que no es
            # una respuesta, es una disculpa.
            logger.warning("Classify failed, answering with the mode text %s", ctx)
            classification = Classification()

        # --- 2. DECIDIR ----------------------------------------------------
        apply_event(state, classification.event, message.message_sid)
        self._apply_transition(state, classification)
        if classification.intent == Intent.INSTALLMENT_INTENT:
            state.installment_essential = classification.essential
        if state.mode != ConversationMode.INSTALLMENT_PAUSE:
            state.installment_calc_requested = False
            state.installment_essential = None
            state.installment_calc_shown = False

        # T10: la simulación corre DESPUÉS de la transición, no antes. Antes
        # tenía que adelantarse para que el cálculo viajara en el mismo turno,
        # y eso la convertía en una segunda forma de cambiar el modo. El banco
        # se consulta primero porque el mínimo vive ahí y "50 mil más" lo
        # necesita para saber sobre qué suma.
        snapshot = await self._snapshot(state, ctx)
        simulation.plan_turn(state, message.body, snapshot)
        state.max_mode_reached = transitions.subir_maximo(state.max_mode_reached, state.mode)
        simulated, calculations = simulation.build_turn_figures(state, snapshot, message.body)
        if not calculations_allowed(state.mode, state.installment_calc_requested):
            calculations = {}
        financial_data = self._financial_data(
            state, snapshot, simulated, classification.intent
        )

        # --- 3. REDACTAR ---------------------------------------------------
        write_request = WriteRequest(
            user_id=message.user_id,
            message=message.body,
            mode=state.mode,
            financial_data=financial_data,
            calculations=calculations,
            classification=classification,
            recent_history=state.history[-RECENT_HISTORY_MESSAGES:],
            can_offer_continue=self._can_offer_continue(state),
            can_clarify_route=self._can_clarify_route(state),
            already_stated_limits=list(state.stated_limits),
            max_mode_reached=state.max_mode_reached,
            requested_installment_calculation=state.installment_calc_requested,
            installment_calc_shown=state.installment_calc_shown,
            opening_offer_made=state.opening_offer_made,
            preferences=dict(state.preferences),
        )
        # El cálculo de la cuota sale una vez, con el monto que ella dio. Desde
        # el turno siguiente lo que falta es respetar lo que decida, no pedirle
        # los mismos datos otra vez; una compra nueva vuelve a encender el
        # pedido en `simulation.plan_turn`.
        #
        # Va después de armar el request, no antes: apagarlo antes se lo quita
        # a ESTE turno, y `calculos_visibles` descartaba `cuota_mensual`
        # justo en el turno que tenía que contarla.
        if "minimo_con_cuota" in financial_data:
            state.installment_calc_shown = True
            state.installment_calc_requested = False
        # Lo que queda del presupuesto del turno, sin piso: si clasificar se lo
        # comió, redactar no alcanza y es mejor fallar ahora —el webhook tiene
        # su texto de respaldo— que pasarse de la ventana de Twilio.
        remaining = self._agent_timeout_s - (time.monotonic() - started)
        reply_obj = await self._write(write_request, remaining)

        reply = reply_obj.message
        # La negativa explícita va primero: si ella pidió no ver cifras, la
        # respuesta tiene que nombrar su pedido, no decirle "vamos de a poco".
        #
        # `protected_text` marca los textos fijos que validó C: la línea de
        # crisis trae "*4141" y "131", que `contains_amount` lee como montos.
        # Sin esta guarda, un mensaje de ideación suicida recibía
        # NO_FIGURES_REPLY en vez de la línea de ayuda.
        if (
            not reply_obj.protected_text
            and state.preferences.get(PREF_SHOW_FINANCIAL_FIGURES) is False
            and contains_amount(reply)
        ):
            logger.warning("Reply with figures blocked by user preference %s", ctx)
            reply = NO_FIGURES_REPLY
        elif intrusos := self._unexpected_figures(
            state, write_request, reply_obj, message.body
        ):
            logger.warning(
                "Reply has figures outside mode=%s (%s) %s", state.mode, sorted(intrusos), ctx
            )
            reply = STEP_BY_STEP_REPLY

        state.history += [
            ConversationMessage(role="user", content=message.body),
            ConversationMessage(role="assistant", content=reply),
        ]
        self._record_anti_loop(state, classification, reply)
        state.last_intent = classification.intent
        state.turn_count += 1
        progress.record_turn(
            state, previous_mode, reply, write_request, classification.intent,
            classification.event,
        )
        state.updated_at = utc_now()
        await self._repository.save(state)

        logger.info(
            "Turn processed %s turn=%d intent=%s event=%s mode=%s->%s "
            "financial_fields=%d calculations=%d elapsed_ms=%d",
            ctx,
            state.turn_count,
            classification.intent,
            classification.event,
            previous_mode,
            state.mode,
            len(financial_data),
            len(calculations),
            int((time.monotonic() - started) * 1000),
        )
        return reply
