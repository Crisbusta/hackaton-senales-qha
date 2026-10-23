"""Coordinador del flujo conversacional.

Un turno: deduplicar → recuperar estado → invocar al agente (con presupuesto
de tiempo) → aplicar reglas deterministas sobre el evento → proteger la
preferencia de no ver cifras → persistir → devolver la respuesta.

El agente interpreta y redacta; el estado (modo, preferencias) lo decide
este servicio con reglas deterministas.
"""

import asyncio
import logging
import re

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
    fields_for_mode,
)
from app.modules.banking.provider import BankDataProvider
from app.modules.banking.schemas import CreditCard
from app.modules.conversation import transitions
from app.modules.conversation.financial import build_financial_data
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

NO_FIGURES_REPLY = (
    "Dejemos los números de lado por ahora, como me pediste. Aquí estoy cuando quieras retomarlo."
)


def apply_event(state: ConversationState, event: ConversationEvent | None, sid: str) -> None:
    """Reglas deterministas evento → estado.

    La preferencia explícita más reciente prevalece sobre las anteriores.
    Las transiciones entre escalones las aplica `transitions.siguiente_modo`
    (T06); aquí solo se aplican los efectos que el contrato fija para cada
    evento.
    """
    if event is None:
        return
    state.events.append(RecordedEvent(event=event, message_sid=sid))

    match event:
        case ConversationEvent.DECLINED_BALANCE_REVIEW | ConversationEvent.AVOID_FINANCIAL_REVIEW:
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
            state.mode,
            state.max_mode_reached,
            response.intent,
            response.event,
            response.essential,
        )
        state.max_mode_reached = transitions.subir_maximo(
            state.max_mode_reached, state.mode
        )

    @staticmethod
    def _record_installment_context(
        state: ConversationState, response: AgentResponse, previous_mode: ConversationMode
    ) -> None:
        """Lo que hace falta para la pausa antes de comprar en cuotas.

        `mentioned_amount` es el monto que ella nombró; se guarda para que
        entre como `monto_elegido`. La solicitud del cálculo se habilita
        cuando, ya en PAUSA_COMPRA, ella pide ver el efecto de la cuota, y
        se apaga al salir de ese modo: no sobrevive al cambio de tema.
        """
        if response.mentioned_amount is not None:
            state.chosen_amount = response.mentioned_amount

        if (
            previous_mode is ConversationMode.INSTALLMENT_PAUSE
            and response.intent in transitions.AVANCE
        ):
            state.requested_installment_calculation = True
        elif state.mode is not ConversationMode.INSTALLMENT_PAUSE:
            state.requested_installment_calculation = False

    async def _credit_card(self, user_id: str, ctx: str) -> CreditCard | None:
        """La tarjeta del usuario, o None si no hay banco vinculado.

        Un proveedor caído no cancela el turno: la conversación sigue y los
        campos salen `ABSENT`, que es la verdad ("no tengo ese dato"). Perder
        el turno entero por el banco sería peor, y en PAUSA_RECHAZO o APOYO
        ni siquiera hacía falta consultarlo.
        """
        if self._bank_provider is None:
            return None
        try:
            snapshot = await self._bank_provider.get_snapshot(user_id)
        except Exception:
            logger.warning("Bank provider failed, turn continues without figures %s", ctx)
            return None
        if snapshot is None or not snapshot.credit_cards:
            return None
        return snapshot.credit_cards[0]

    async def _financial_data(
        self, state: ConversationState, user_id: str, ctx: str
    ) -> dict[str, FinancialField]:
        """Las cifras admisibles en este turno, o nada.

        Dos puertas antes de tocar al banco. La primera es el modo: si
        `fields_for_mode` no autoriza ningún campo (E0, las pausas, APOYO,
        CIERRE) no hay nada que pedir. La segunda es el consentimiento: si
        ella pidió no ver cifras, no se consultan sus datos, no solo se
        omiten de la respuesta.
        """
        admissible = fields_for_mode(
            state.mode, state.last_intent, state.requested_installment_calculation
        )
        if not admissible:
            return {}
        if state.preferences.get(PREF_SHOW_FINANCIAL_FIGURES) is False:
            logger.info("Figures declined by user, bank not consulted %s", ctx)
            return {}

        return build_financial_data(
            await self._credit_card(user_id, ctx),
            mode=state.mode,
            previous_intent=state.last_intent,
            requested_installment_calculation=state.requested_installment_calculation,
            chosen_amount=state.chosen_amount,
        )

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
        bank_provider: BankDataProvider | None = None,
    ) -> None:
        self._agent = agent
        self._repository = repository
        self._agent_timeout_s = agent_timeout_s
        # None: nadie tiene banco vinculado y el agente nunca recibe cifras.
        # Es el default a propósito, porque es el caso seguro.
        self._bank_provider = bank_provider

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

        request = AgentRequest(
            user_id=message.user_id,
            message=message.body,
            mode=state.mode,
            financial_data=await self._financial_data(state, message.user_id, ctx),
            recent_history=state.history[-RECENT_HISTORY_MESSAGES:],
            preferences=dict(state.preferences),
            max_mode_reached=state.max_mode_reached,
            previous_intent=state.last_intent,
            requested_installment_calculation=state.requested_installment_calculation,
            can_offer_continue=self._can_offer_continue(state),
            can_clarify_route=self._can_clarify_route(state),
            already_stated_limits=list(state.stated_limits),
            # `calculations` queda vacío: el simulador es T10 (de B) y no
            # existe todavía. El gate del contrato (`calculations_allowed`)
            # ya está escrito y el agente lo aplica, así que cuando T10
            # aterrice se llena acá y nada más cambia. Mientras tanto
            # `minimo_con_cuota` viaja ABSENT, que es la verdad: no lo
            # tenemos. Inventarlo sería justo lo que el caso prohíbe.
        )

        try:
            response = await asyncio.wait_for(
                self._agent.process(request), timeout=self._agent_timeout_s
            )
        except TimeoutError as exc:
            raise IntegrationError(f"Agent timed out after {self._agent_timeout_s}s") from exc
        except Exception as exc:
            raise IntegrationError("Agent failed") from exc

        previous_mode = state.mode
        apply_event(state, response.event, message.message_sid)
        self._apply_transition(state, response)

        reply = response.message
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
        self._record_installment_context(state, response, previous_mode)
        state.last_intent = response.intent
        state.turn_count += 1
        state.updated_at = utc_now()
        await self._repository.save(state)

        logger.info(
            "Turn processed %s turn=%d intent=%s event=%s mode=%s->%s",
            ctx,
            state.turn_count,
            response.intent,
            response.event,
            previous_mode,
            state.mode,
        )
        return reply
