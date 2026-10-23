from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.modules.agent.interface import (
    ConversationEvent,
    ConversationMessage,
    ConversationMode,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class RecordedEvent(BaseModel):
    event: ConversationEvent
    message_sid: str
    at: datetime = Field(default_factory=utc_now)


class ConversationState(BaseModel):
    """Estado conversacional de un usuario. El backend es su único dueño."""

    user_id: str
    mode: ConversationMode = ConversationMode.GREETING
    preferences: dict[str, bool] = Field(default_factory=dict)
    history: list[ConversationMessage] = Field(default_factory=list)
    events: list[RecordedEvent] = Field(default_factory=list)
    last_intent: str | None = None
    turn_count: int = 0

    # AB6: el escalón más alto alcanzado. Monótono, nunca baja. Permite que
    # `retoma_voluntaria` reconozca lo que ella ya vio sin repetir cifras.
    max_mode_reached: ConversationMode = ConversationMode.GREETING

    # AB2: una oferta de "¿seguimos?" por escalón. Cuenta por modo.
    offers_by_mode: dict[str, int] = Field(default_factory=dict)

    # AB7: un límite se dice una vez (p. ej. la línea *4141).
    stated_limits: list[str] = Field(default_factory=list)

    # AB8: dos fallos de reconocimiento seguidos habilitan las opciones
    # numeradas, una sola vez. El contador vuelve a 0 cuando entiende.
    recognition_failures: int = 0
    numbered_options_offered: bool = False

    # AB8: a un "ok" o "gracias" sin contenido se responde una vez; el
    # siguiente no recibe respuesta. Se reinicia cuando ella escribe algo.
    acknowledgement_answered: bool = False
    updated_at: datetime = Field(default_factory=utc_now)
