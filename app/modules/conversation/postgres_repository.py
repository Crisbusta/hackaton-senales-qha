"""Persistencia del estado conversacional en PostgreSQL.

Se activa con `DATABASE_URL`. El estado completo de cada usuario se guarda
como un documento `jsonb` (una fila por usuario): el esquema del estado
cambia seguido y así no hacen falta migraciones. Las tablas se crean al
arrancar si no existen.

Cada operación es una sola sentencia (un viaje a la base) para no comerse
el presupuesto del turno, que ya lo domina el LLM.

Dos turnos simultáneos del mismo usuario: gana la última escritura, igual
que en memoria. La deduplicación por MessageSid sí es atómica entre
procesos (`INSERT ... ON CONFLICT DO NOTHING`).
"""

import logging

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.modules.conversation.repository import MAX_HISTORY_MESSAGES, MAX_TIMELINE_ENTRIES
from app.modules.conversation.schemas import ConversationState

logger = logging.getLogger(__name__)

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS conversation_states (
        user_id text PRIMARY KEY,
        state jsonb NOT NULL,
        updated_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS processed_messages (
        message_sid text PRIMARY KEY,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
)


class PostgresConversationRepository:
    """`ConversationRepository` sobre PostgreSQL. Hay que llamar `open()` antes de usarlo."""

    def __init__(self, conninfo: str, *, min_size: int = 1, max_size: int = 5) -> None:
        # El pool se crea en `open()`, dentro del event loop de la app.
        self._conninfo = conninfo
        self._min_size = min_size
        self._max_size = max_size
        self._pool: AsyncConnectionPool | None = None

    def __repr__(self) -> str:
        # Nunca exponer la URL (trae la contraseña).
        return f"{type(self).__name__}()"

    async def open(self) -> None:
        if self._pool is not None:
            return
        pool = AsyncConnectionPool(
            self._conninfo,
            min_size=self._min_size,
            max_size=self._max_size,
            open=False,
            # Descarta conexiones muertas (p. ej. tras un reinicio de la base)
            # antes de entregarlas, en vez de fallar el turno.
            check=AsyncConnectionPool.check_connection,
            name="conversation",
        )
        await pool.open(wait=True, timeout=10)
        try:
            async with pool.connection() as conn:
                for statement in SCHEMA:
                    await conn.execute(statement)
        except BaseException:
            await pool.close()
            raise
        self._pool = pool
        logger.info(
            "Conversation repository: postgres (pool %d-%d)", self._min_size, self._max_size
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def _connection(self):
        if self._pool is None:
            raise RuntimeError("PostgresConversationRepository.open() was not called")
        return self._pool.connection()

    async def get(self, user_id: str) -> ConversationState:
        async with self._connection as conn:
            cur = await conn.execute(
                "SELECT state FROM conversation_states WHERE user_id = %s", (user_id,)
            )
            row = await cur.fetchone()
        if row is None:
            return ConversationState(user_id=user_id)
        return ConversationState.model_validate(row[0])

    async def save(self, state: ConversationState) -> None:
        state.history = state.history[-MAX_HISTORY_MESSAGES:]
        state.timeline = state.timeline[-MAX_TIMELINE_ENTRIES:]
        async with self._connection as conn:
            await conn.execute(
                """
                INSERT INTO conversation_states (user_id, state, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET state = EXCLUDED.state, updated_at = EXCLUDED.updated_at
                """,
                (state.user_id, Jsonb(state.model_dump(mode="json")), state.updated_at),
            )

    async def claim_message(self, message_sid: str) -> bool:
        async with self._connection as conn:
            cur = await conn.execute(
                "INSERT INTO processed_messages (message_sid) VALUES (%s) ON CONFLICT DO NOTHING",
                (message_sid,),
            )
            return cur.rowcount == 1

    async def reset(self, user_id: str) -> None:
        # Los MessageSid procesados se conservan: un reenvío de Twilio sigue siendo duplicado.
        async with self._connection as conn:
            await conn.execute("DELETE FROM conversation_states WHERE user_id = %s", (user_id,))

    async def list_all(self) -> list[ConversationState]:
        async with self._connection as conn:
            cur = await conn.execute(
                "SELECT state FROM conversation_states ORDER BY updated_at DESC"
            )
            rows = await cur.fetchall()
        return [ConversationState.model_validate(row[0]) for row in rows]
