"""Persistencia del estado conversacional.

`ConversationRepository` es la interfaz; la implementación en memoria sirve
para la primera vertical y se reemplazará por PostgreSQL (Supabase).

Limitaciones de InMemoryConversationRepository:
- Los datos desaparecen al reiniciar el proceso.
- No funciona con múltiples réplicas ni workers: cada proceso tiene su copia.
- No ofrece persistencia duradera.
- La deduplicación por MessageSid solo aplica durante la vida del proceso.
"""

from typing import Protocol

from app.modules.conversation.schemas import ConversationState

# Historial máximo guardado por usuario (mensajes, no turnos).
MAX_HISTORY_MESSAGES = 20


class ConversationRepository(Protocol):
    async def get(self, user_id: str) -> ConversationState: ...

    async def save(self, state: ConversationState) -> None: ...

    async def claim_message(self, message_sid: str) -> bool:
        """Marca el mensaje como procesado. False si ya lo estaba."""
        ...


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self._states: dict[str, ConversationState] = {}
        self._processed_sids: set[str] = set()

    async def get(self, user_id: str) -> ConversationState:
        state = self._states.get(user_id)
        # Copia: el llamador modifica el estado y lo guarda explícitamente.
        return state.model_copy(deep=True) if state else ConversationState(user_id=user_id)

    async def save(self, state: ConversationState) -> None:
        state.history = state.history[-MAX_HISTORY_MESSAGES:]
        self._states[state.user_id] = state.model_copy(deep=True)

    async def claim_message(self, message_sid: str) -> bool:
        # Sin `await` entre la lectura y la escritura: atómico en el event loop.
        if message_sid in self._processed_sids:
            return False
        self._processed_sids.add(message_sid)
        return True
