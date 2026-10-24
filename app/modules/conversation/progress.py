"""Avances de la conversación (principio 8: progreso basado en acciones observables).

Reglas deterministas sobre lo que pasó en el turno: qué cifras aparecen en la
respuesta que ella recibió, en qué modo terminó y lo que eligió. Ningún avance se
infiere del tono ni del silencio. Un avance se registra la primera vez que ocurre.
"""

import re
from dataclasses import dataclass

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentRequest,
    ConversationEvent,
    ConversationMode,
    FieldStatus,
    FinancialField,
)
from app.modules.conversation.figures import amounts_in
from app.modules.conversation.schemas import ConversationState, TimelineEntry, utc_now


@dataclass(frozen=True)
class Milestone:
    key: str
    label: str


FIRST_CONTACT = Milestone("primer_contacto", "Primera conversación")
SHARED_STATEMENT = Milestone("compartio_estado", "Compartió su estado de cuenta")
SAW_DUE_DATE = Milestone("vio_vencimiento", "Vio su fecha de vencimiento")
SAW_BALANCE = Milestone("vio_minimo_y_saldo", "Vio su pago mínimo y su saldo")
SAW_BREAKDOWN = Milestone("vio_desglose", "Entendió en qué se reparte el mínimo")
SIMULATED_PAYMENT = Milestone("simulo_pago", "Simuló un pago")
PURCHASE_PAUSE = Milestone("pausa_antes_de_comprar", "Hizo una pausa antes de comprar en cuotas")
PAUSE_RESPECTED = Milestone("pausa_respetada", "Pidió parar y se respetó")
RESUMED = Milestone("retomo", "Volvió por su cuenta")
SUPPORT = Milestone("apoyo", "Se activó el modo de apoyo")
CLOSED = Milestone("cierre", "Cerró la conversación")

MILESTONES = (
    FIRST_CONTACT,
    SHARED_STATEMENT,
    SAW_DUE_DATE,
    SAW_BALANCE,
    SAW_BREAKDOWN,
    SIMULATED_PAYMENT,
    PURCHASE_PAUSE,
    PAUSE_RESPECTED,
    RESUMED,
    SUPPORT,
    CLOSED,
)
LABELS = {m.key: m.label for m in MILESTONES}

_DETOURS = (ConversationMode.PAUSE, ConversationMode.SUPPORT, ConversationMode.CLOSING)
_BREAKDOWN_FIELDS = ("minimo_intereses", "minimo_cuotas", "minimo_abono")


def _shown(field: str, data: dict[str, FinancialField], reply: str, amounts: set[int]) -> bool:
    """¿La respuesta que recibió le mostró este dato? Evidencia, no suposición."""
    item = data.get(field)
    if item is None or item.status is not FieldStatus.CONFIRMED or item.value is None:
        return False
    if isinstance(item.value, int):
        return item.value in amounts
    # Fechas: basta con día y mes ("3 de noviembre"), con o sin año.
    text = re.sub(r" de \d{4}$", "", str(item.value)).lower()
    return text in reply.lower()


def turn_milestones(
    state: ConversationState,
    previous_mode: ConversationMode,
    reply: str,
    data: dict[str, FinancialField],
    calculations: dict[str, int],
    answered_mode: ConversationMode,
) -> list[Milestone]:
    """Avances que ocurrieron en este turno (aunque ya se hubieran logrado antes).

    "Vio" significa que la cifra aparece en la respuesta que ella recibió.
    """
    reached: list[Milestone] = []
    if state.turn_count == 1:
        reached.append(FIRST_CONTACT)

    if state.preferences.get(PREF_SHOW_FINANCIAL_FIGURES) is not False:
        amounts = amounts_in(reply)
        if _shown("vencimiento", data, reply, amounts):
            reached.append(SAW_DUE_DATE)
        if _shown("minimo", data, reply, amounts) or _shown("saldo", data, reply, amounts):
            reached.append(SAW_BALANCE)
        if any(_shown(f, data, reply, amounts) for f in _BREAKDOWN_FIELDS):
            reached.append(SAW_BREAKDOWN)
        if (
            answered_mode == ConversationMode.AMOUNT_SIMULATION
            and state.chosen_payment
            and amounts & set(calculations.values())
        ):
            reached.append(SIMULATED_PAYMENT)

    entered = state.mode if state.mode != previous_mode else None
    if entered == ConversationMode.INSTALLMENT_PAUSE:
        reached.append(PURCHASE_PAUSE)
    if entered == ConversationMode.PAUSE:
        reached.append(PAUSE_RESPECTED)
    if entered == ConversationMode.SUPPORT:
        reached.append(SUPPORT)
    if entered == ConversationMode.CLOSING:
        reached.append(CLOSED)
    if previous_mode in _DETOURS and state.mode == ConversationMode.GREETING:
        reached.append(RESUMED)
    return reached


def record_turn(
    state: ConversationState,
    previous_mode: ConversationMode,
    reply: str,
    request: AgentRequest,
    intent: str,
    event: ConversationEvent | None,
) -> list[str]:
    """Agrega el turno a la línea de tiempo y marca los avances nuevos. Devuelve sus claves."""
    reached = turn_milestones(
        state, previous_mode, reply, request.financial_data, request.calculations, request.mode
    )
    new = [m.key for m in reached]
    new = [key for key in new if key not in state.milestones]
    now = utc_now()
    for key in new:
        state.milestones[key] = now
    state.timeline.append(
        TimelineEntry(
            at=now,
            turn=state.turn_count,
            mode_from=previous_mode,
            mode_to=state.mode,
            intent=intent,
            event=event,
            milestones=new,
        )
    )
    return new


def record_system_turn(
    state: ConversationState, intent: str, milestones: list[Milestone]
) -> list[str]:
    """Turno resuelto por el backend sin el agente (p. ej. recibir el PDF)."""
    reached = ([FIRST_CONTACT] if state.turn_count == 1 else []) + milestones
    new = [m.key for m in reached if m.key not in state.milestones]
    now = utc_now()
    for key in new:
        state.milestones[key] = now
    state.timeline.append(
        TimelineEntry(
            at=now,
            turn=state.turn_count,
            mode_from=state.mode,
            mode_to=state.mode,
            intent=intent,
            milestones=new,
        )
    )
    return new
