"""Vista del dashboard de progreso.

Solo datos de progreso: modos, etiquetas, eventos y avances. Nunca el texto de
los mensajes ni el teléfono completo.
"""

import hashlib
from datetime import datetime

from pydantic import BaseModel

from app.core.logging import mask_phone
from app.modules.agent.interface import PREF_SHOW_FINANCIAL_FIGURES, ConversationMode
from app.modules.conversation import progress
from app.modules.conversation.schemas import ConversationState

MODE_LABELS = {
    ConversationMode.GREETING: "Conversar",
    ConversationMode.DUE_DATE: "Fecha de vencimiento",
    ConversationMode.BALANCE_OVERVIEW: "Mínimo y saldo",
    ConversationMode.MINIMUM_BREAKDOWN: "Desglose del mínimo",
    ConversationMode.AMOUNT_SIMULATION: "Simulación de pago",
    ConversationMode.PAUSE: "Pausa (pidió parar)",
    ConversationMode.INSTALLMENT_PAUSE: "Pausa antes de comprar",
    ConversationMode.SUPPORT: "Apoyo",
    ConversationMode.CLOSING: "Cierre",
}

# La Escalera y el avance que marca cada escalón.
LADDER = (
    (ConversationMode.GREETING, progress.FIRST_CONTACT),
    (ConversationMode.DUE_DATE, progress.SAW_DUE_DATE),
    (ConversationMode.BALANCE_OVERVIEW, progress.SAW_BALANCE),
    (ConversationMode.MINIMUM_BREAKDOWN, progress.SAW_BREAKDOWN),
    (ConversationMode.AMOUNT_SIMULATION, progress.SIMULATED_PAYMENT),
)

TIMELINE_ITEMS = 25


class LadderStep(BaseModel):
    mode: str
    label: str
    reached: bool
    current: bool


class MilestoneView(BaseModel):
    key: str
    label: str
    at: datetime | None


class TimelineItem(BaseModel):
    at: datetime
    turn: int
    mode_from: str
    mode_to: str
    intent: str
    event: str | None
    milestones: list[str]


class ConversationView(BaseModel):
    id: str
    user: str
    mode: str
    mode_label: str
    turns: int
    updated_at: datetime
    figures_hidden: bool
    ladder: list[LadderStep]
    milestones: list[MilestoneView]
    counters: dict[str, int]
    timeline: list[TimelineItem]


class DashboardView(BaseModel):
    generated_at: datetime
    conversations: list[ConversationView]


def _entered(state: ConversationState, mode: ConversationMode) -> int:
    return sum(1 for e in state.timeline if e.mode_to == mode and e.mode_from != mode)


def conversation_view(state: ConversationState) -> ConversationView:
    return ConversationView(
        id=hashlib.sha256(state.user_id.encode()).hexdigest()[:8],
        user=mask_phone(state.user_id),
        mode=state.mode.value,
        mode_label=MODE_LABELS[state.mode],
        turns=state.turn_count,
        updated_at=state.updated_at,
        figures_hidden=state.preferences.get(PREF_SHOW_FINANCIAL_FIGURES) is False,
        ladder=[
            LadderStep(
                mode=mode.value,
                label=MODE_LABELS[mode],
                reached=milestone.key in state.milestones,
                current=state.mode == mode,
            )
            for mode, milestone in LADDER
        ],
        milestones=[
            MilestoneView(key=m.key, label=m.label, at=state.milestones.get(m.key))
            for m in progress.MILESTONES
        ],
        counters={
            "pausas_respetadas": _entered(state, ConversationMode.PAUSE),
            "pausas_antes_de_comprar": _entered(state, ConversationMode.INSTALLMENT_PAUSE),
            "veces_en_apoyo": _entered(state, ConversationMode.SUPPORT),
        },
        timeline=[
            TimelineItem(
                at=e.at,
                turn=e.turn,
                mode_from=e.mode_from.value,
                mode_to=e.mode_to.value,
                intent=e.intent,
                event=e.event.value if e.event else None,
                milestones=[progress.LABELS.get(k, k) for k in e.milestones],
            )
            for e in reversed(state.timeline[-TIMELINE_ITEMS:])
        ],
    )


def dashboard_view(states: list[ConversationState], now: datetime) -> DashboardView:
    return DashboardView(generated_at=now, conversations=[conversation_view(s) for s in states])
