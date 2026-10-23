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
    updated_at: datetime = Field(default_factory=utc_now)
