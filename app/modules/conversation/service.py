"""Coordinador del flujo conversacional.

Un turno: deduplicar → recuperar estado → detectar si pidió una simulación
(T10) → armar `financial_data` y `calculations` desde el banco → invocar al
agente (con presupuesto de tiempo) → aplicar reglas deterministas sobre el
evento → verificar las cifras de la respuesta → persistir → responder.

El agente interpreta y redacta; el estado (modo, preferencias) lo decide
este servicio con reglas deterministas.
"""

import asyncio
import logging
import re
import time

from app.core.logging import mask_phone
from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    Agent,
    AgentRequest,
    AgentResponse,
    ConversationEvent,
    ConversationMessage,
    ConversationMode,
    FinancialField,
    Intent,
    calculations_allowed,
)
from app.modules.banking.provider import BankDataProvider
from app.modules.banking.schemas import BankSnapshot
from app.modules.banking.service import credit_card_fields
from app.modules.conversation import figures, simulation, transitions
from app.modules.conversation.repository import ConversationRepository
from app.modules.conversation.schemas import ConversationState, RecordedEvent, utc_now
from app.modules.whatsapp.schemas import IncomingMessage
from app.shared.exceptions import IntegrationError

logger = logging.getLogger(__name__)

# Mensajes (no turnos) de historial que se envían al agente: 2 turnos.
RECENT_HISTORY_MESSAGES = 4

# Montos tipo "$74.350", "74.350" o "74350". Conservador a propósito: ante la
# duda, una respuesta sin cifras es preferible a mostrar un monto no deseado.
_AMOUNT_PATTERN = re.compile(r"\$\s*\d|\b\d{1,3}(?:\.\d{3})+\b|\b\d{4,}\b")

# Si la respuesta trae cifras que no corresponden al modo y no se pudo
# corregir a tiempo. Pendiente de validación de tono por C.
STEP_BY_STEP_REPLY = "Vamos de a poco. ¿Quieres que lo veamos paso a paso?"

# Tiempo mínimo que debe quedar del presupuesto para intentar un reintento.
MIN_RETRY_S = 3.0

# Modos cuya respuesta depende del modo y que se pueden ESTRENAR en el mismo turno:
# al entrar, se vuelve a pedir la respuesta con el modo nuevo. PAUSA_RECHAZO y
# APOYO no lo necesitan: responden con textos fijos según la etiqueta.
REANSWER_ON_ENTRY = (ConversationMode.INSTALLMENT_PAUSE,)

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
    def _apply_transition(state: ConversationState, response: AgentResponse) -> None:
        """El modo del próximo turno, más AB6."""
        state.mode = transitions.siguiente_modo(
            state.mode, state.max_mode_reached, response.intent, response.event
        )
        state.max_mode_reached = transitions.subir_maximo(state.max_mode_reached, state.mode)

    @staticmethod
    def _record_anti_loop(
        state: ConversationState,
        response: AgentResponse,
        reply: str,
        previous_mode: ConversationMode,
    ) -> None:
        """Lo que hay que recordar para no repetirse en el próximo turno."""
        # AB2: si la oferta salió, se consume en el escalón donde salió.
        if previous_mode in transitions.MODOS_CON_OFERTA and "seguimos" in reply.lower():
            key = previous_mode.value
            state.offers_by_mode[key] = state.offers_by_mode.get(key, 0) + 1

        # AB7: la línea de crisis se ofrece una vez.
        if "*4141" in reply and transitions.LIMITE_LINEA_CRISIS not in state.stated_limits:
            state.stated_limits.append(transitions.LIMITE_LINEA_CRISIS)

        # AB8: el contador vuelve a 0 cuando entiende.
        if response.intent == Intent.AMBIGUOUS:
            state.recognition_failures += 1
            if state.recognition_failures >= 2:
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
    ) -> None:
        self._agent = agent
        self._repository = repository
        self._agent_timeout_s = agent_timeout_s
        self._bank = bank

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
    ) -> dict[str, FinancialField]:
        """Los campos que viajan al agente: precarga en los escalones, filtro por modo fuera."""
        if snapshot is None or not snapshot.credit_cards:
            return {}
        available = credit_card_fields(snapshot.credit_cards[0]) | simulated
        sent = figures.fields_to_send(
            state.mode, state.last_intent, state.installment_calc_requested
        )
        return {name: available[name] for name in sent if name in available}

    async def _call_agent(self, request: AgentRequest, timeout_s: float) -> AgentResponse:
        try:
            return await asyncio.wait_for(self._agent.process(request), timeout=timeout_s)
        except TimeoutError as exc:
            raise IntegrationError(f"Agent timed out after {timeout_s:.1f}s") from exc
        except Exception as exc:
            raise IntegrationError("Agent failed") from exc

    async def _reanswer_on_mode_entry(
        self,
        state: ConversationState,
        request: AgentRequest,
        response: AgentResponse,
        previous_mode: ConversationMode,
        started: float,
        ctx: str,
    ) -> tuple[AgentRequest, AgentResponse]:
        """Si el turno ENTRÓ a un modo cuya respuesta depende del modo, se pide de nuevo.

        La transición ocurre después de que el agente responde: si ella dice
        "me dan ganas de comprar en cuotas" en E0, el agente redactó en E0 y
        las dos opciones de PAUSA_COMPRA llegarían un turno tarde. Se le vuelve
        a preguntar una sola vez con el modo nuevo. La clasificación del turno
        (etiqueta, evento, esencial) es la del primer llamado; del segundo solo
        se usa la redacción. Si no queda tiempo o falla, queda la primera.
        """
        if (
            state.mode not in REANSWER_ON_ENTRY
            or state.mode == previous_mode
            or response.protected_text
        ):
            return request, response
        remaining = self._agent_timeout_s - (time.monotonic() - started)
        if remaining < MIN_RETRY_S:
            logger.info("No time to re-answer after entering mode=%s %s", state.mode, ctx)
            return request, response

        reentry = request.model_copy(
            update={
                "mode": state.mode,
                "max_mode_reached": state.max_mode_reached,
                "previous_intent": response.intent,
                "financial_data": {},
                "calculations": {},
                "requested_installment_calculation": False,
                "can_offer_continue": False,
            }
        )
        try:
            second = await self._call_agent(reentry, remaining)
        except IntegrationError:
            logger.warning("Re-answer failed, keeping first reply %s", ctx, exc_info=True)
            return request, response
        logger.info("Re-answered after entering mode=%s %s", state.mode, ctx)
        return reentry, response.model_copy(
            update={"message": second.message, "protected_text": second.protected_text}
        )

    async def _enforce_mode_figures(
        self,
        state: ConversationState,
        request: AgentRequest,
        response: AgentResponse,
        started: float,
        ctx: str,
    ) -> str:
        """Verificación posterior a la precarga.

        La respuesta solo puede traer montos de los campos del modo en que
        terminó el turno, o montos que ella misma escribió. Si trae otros, se
        reintenta una vez con solo los datos permitidos; si no hay tiempo o el
        reintento tampoco cumple, sale un texto sin cifras.
        """
        requested = request.requested_installment_calculation
        fields = figures.allowed_fields(
            [request.mode, state.mode], [response.intent, request.previous_intent], requested
        )
        own_amounts = [request.message] + (
            [str(response.mentioned_amount)] if response.mentioned_amount is not None else []
        )
        allowed = figures.allowed_amounts(
            request.financial_data, fields, own_amounts, request.calculations
        )
        if not figures.unexpected_amounts(response.message, allowed):
            return response.message

        logger.warning("Reply has figures outside mode=%s, retrying %s", state.mode, ctx)
        remaining = self._agent_timeout_s - (time.monotonic() - started)
        if remaining >= MIN_RETRY_S:
            retry_request = request.model_copy(
                update={
                    "mode": state.mode,
                    "max_mode_reached": state.max_mode_reached,
                    "previous_intent": response.intent,
                    "financial_data": {
                        k: v for k, v in request.financial_data.items() if k in fields
                    },
                }
            )
            try:
                retry = await self._call_agent(retry_request, remaining)
            except IntegrationError:
                logger.warning("Retry failed, sending step-by-step reply %s", ctx, exc_info=True)
            else:
                if retry.protected_text or not figures.unexpected_amounts(retry.message, allowed):
                    return retry.message
        logger.warning("Figures outside mode not resolved, sending step-by-step reply %s", ctx)
        return STEP_BY_STEP_REPLY

    async def handle(self, message: IncomingMessage) -> str | None:
        """Devuelve la respuesta, o None si el mensaje ya fue procesado."""
        ctx = f"sid={message.message_sid} user={mask_phone(message.user_id)}"

        if not await self._repository.claim_message(message.message_sid):
            logger.info("Duplicate message ignored %s", ctx)
            return None

        state = await self._repository.get(message.user_id)

        # AB8: a un acuse vacio se responde una vez; el siguiente no recibe
        # respuesta. Ahorra una llamada al modelo y evita el bucle de cortesia.
        if transitions.es_acuse_vacio(message.body):
            if state.acknowledgement_answered:
                logger.info("Empty acknowledgement, no reply (AB8) %s", ctx)
                return None
            state.acknowledgement_answered = True
        else:
            state.acknowledgement_answered = False

        # T10: si pidió simular un pago o el cálculo de una cuota, el modo se
        # ajusta antes de llamar al agente, para que el resultado viaje ya.
        simulation.plan_turn(state, message.body)
        snapshot = await self._snapshot(state, ctx)
        simulated, calculations = simulation.build_turn_figures(state, snapshot, message.body)
        if not calculations_allowed(state.mode, state.installment_calc_requested):
            calculations = {}
        financial_data = self._financial_data(state, snapshot, simulated)
        request = AgentRequest(
            user_id=message.user_id,
            message=message.body,
            mode=state.mode,
            financial_data=financial_data,
            recent_history=state.history[-RECENT_HISTORY_MESSAGES:],
            preferences=dict(state.preferences),
            max_mode_reached=state.max_mode_reached,
            previous_intent=state.last_intent,
            can_offer_continue=self._can_offer_continue(state),
            can_clarify_route=self._can_clarify_route(state),
            already_stated_limits=list(state.stated_limits),
            calculations=calculations,
            requested_installment_calculation=state.installment_calc_requested,
        )

        started = time.monotonic()
        response = await self._call_agent(request, self._agent_timeout_s)

        previous_mode = state.mode
        apply_event(state, response.event, message.message_sid)
        self._apply_transition(state, response)
        if response.intent == Intent.INSTALLMENT_INTENT:
            state.installment_essential = response.essential
        if state.mode != ConversationMode.INSTALLMENT_PAUSE:
            state.installment_calc_requested = False
            state.installment_essential = None
        request, response = await self._reanswer_on_mode_entry(
            state, request, response, previous_mode, started, ctx
        )

        reply = response.message
        # Con la negativa activa no hay datos que verificar: la barrera de
        # abajo reemplaza cualquier cifra.
        if (
            not response.protected_text
            and state.preferences.get(PREF_SHOW_FINANCIAL_FIGURES) is not False
        ):
            reply = await self._enforce_mode_figures(state, request, response, started, ctx)
        # `protected_text` marca los textos fijos que validó C: la línea de
        # crisis trae "*4141" y "131", que `contains_amount` lee como montos.
        # Sin esta guarda, un mensaje de ideación suicida recibía
        # NO_FIGURES_REPLY en vez de la línea de ayuda.
        if (
            not response.protected_text
            and state.preferences.get(PREF_SHOW_FINANCIAL_FIGURES) is False
            and contains_amount(reply)
        ):
            logger.warning("Reply with figures blocked by user preference %s", ctx)
            reply = NO_FIGURES_REPLY

        state.history += [
            ConversationMessage(role="user", content=message.body),
            ConversationMessage(role="assistant", content=reply),
        ]
        self._record_anti_loop(state, response, reply, previous_mode)
        state.last_intent = response.intent
        state.turn_count += 1
        state.updated_at = utc_now()
        await self._repository.save(state)

        logger.info(
            "Turn processed %s turn=%d intent=%s event=%s mode=%s->%s "
            "financial_fields=%d calculations=%d",
            ctx,
            state.turn_count,
            response.intent,
            response.event,
            previous_mode,
            state.mode,
            len(financial_data),
            len(calculations),
        )
        return reply
