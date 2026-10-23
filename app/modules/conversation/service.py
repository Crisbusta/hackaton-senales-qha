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
    ConversationEvent,
    ConversationMessage,
    ConversationMode,
)
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
    Las transiciones entre escalones E0-E4 quedan pendientes de la tabla de
    transiciones (T01/T06); aquí solo se aplican las que el contrato fija.
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

    def __init__(
        self,
        agent: Agent,
        repository: ConversationRepository,
        agent_timeout_s: float,
    ) -> None:
        self._agent = agent
        self._repository = repository
        self._agent_timeout_s = agent_timeout_s

    async def handle(self, message: IncomingMessage) -> str | None:
        """Devuelve la respuesta, o None si el mensaje ya fue procesado."""
        ctx = f"sid={message.message_sid} user={mask_phone(message.user_id)}"

        if not await self._repository.claim_message(message.message_sid):
            logger.info("Duplicate message ignored %s", ctx)
            return None

        state = await self._repository.get(message.user_id)
        request = AgentRequest(
            user_id=message.user_id,
            message=message.body,
            mode=state.mode,
            recent_history=state.history[-RECENT_HISTORY_MESSAGES:],
            preferences=dict(state.preferences),
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
