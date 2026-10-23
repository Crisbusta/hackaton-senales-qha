"""Persistencia del estado conversacional.

`ConversationRepository` es la interfaz. Hay dos implementaciones y
`build_repository()` elige según `DATABASE_URL`: vacía, en memoria (el
default); con valor, PostgreSQL (`postgres_repository.py`), que sobrevive a
los redeploys.

Limitaciones de InMemoryConversationRepository:
- Los datos desaparecen al reiniciar el proceso.
- No funciona con múltiples réplicas ni workers: cada proceso tiene su copia.
- No ofrece persistencia duradera.
- La deduplicación por MessageSid solo aplica durante la vida del proceso.
"""

from typing import TYPE_CHECKING, Protocol

from app.modules.conversation.schemas import ConversationState

if TYPE_CHECKING:
    from app.core.config import Settings

# Historial máximo guardado por usuario (mensajes, no turnos).
MAX_HISTORY_MESSAGES = 20
# Turnos que se conservan en la línea de tiempo del dashboard.
MAX_TIMELINE_ENTRIES = 200


class ConversationRepository(Protocol):
    async def get(self, user_id: str) -> ConversationState: ...

    async def save(self, state: ConversationState) -> None: ...

    async def claim_message(self, message_sid: str) -> bool:
        """Marca el mensaje como procesado. False si ya lo estaba."""
        ...

    async def reset(self, user_id: str) -> None:
        """Borra el estado del usuario (la conversación vuelve a empezar)."""
        ...

    async def list_all(self) -> list[ConversationState]:
        """Todas las conversaciones, la más reciente primero (para el dashboard)."""
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
        state.timeline = state.timeline[-MAX_TIMELINE_ENTRIES:]
        self._states[state.user_id] = state.model_copy(deep=True)

    async def claim_message(self, message_sid: str) -> bool:
        # Sin `await` entre la lectura y la escritura: atómico en el event loop.
        if message_sid in self._processed_sids:
            return False
        self._processed_sids.add(message_sid)
        return True

    async def reset(self, user_id: str) -> None:
        # Los MessageSid procesados se conservan: un reenvío de Twilio sigue siendo duplicado.
        self._states.pop(user_id, None)

    async def list_all(self) -> list[ConversationState]:
        states = sorted(self._states.values(), key=lambda s: s.updated_at, reverse=True)
        return [state.model_copy(deep=True) for state in states]


def build_repository(settings: "Settings") -> ConversationRepository:
    """Con `DATABASE_URL`, PostgreSQL (hay que llamar `open()` al arrancar); si no, en memoria."""
    if not settings.database_url:
        return InMemoryConversationRepository()
    # Import diferido: el modo en memoria no carga el driver.
    from app.modules.conversation.postgres_repository import PostgresConversationRepository

    return PostgresConversationRepository(settings.database_url)
