"""Comportamiento del repositorio, contra las dos implementaciones.

Las de PostgreSQL se saltan si no hay `TEST_DATABASE_URL`. Para correrlas:

    docker run -d --rm --name contigo-pg-test -p 55432:5432 \\
        -e POSTGRES_PASSWORD=test postgres:16
    TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres uv run pytest

Ojo: vacían las tablas `conversation_states` y `processed_messages` de esa base.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from app.modules.agent.interface import ConversationMessage, ConversationMode
from app.modules.conversation.postgres_repository import PostgresConversationRepository
from app.modules.conversation.repository import (
    MAX_HISTORY_MESSAGES,
    MAX_TIMELINE_ENTRIES,
    ConversationRepository,
    InMemoryConversationRepository,
    build_repository,
)
from app.modules.conversation.schemas import TimelineEntry, utc_now
from app.modules.whatsapp.schemas import IncomingMessage

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
requires_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL no está definida"
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _clean_postgres() -> None:
    repo = PostgresConversationRepository(TEST_DATABASE_URL)
    await repo.open()  # crea las tablas si no existen
    async with repo._connection as conn:
        await conn.execute("TRUNCATE conversation_states, processed_messages")
    await repo.close()


@pytest.fixture(params=["memory", pytest.param("postgres", marks=requires_postgres)])
async def repo(request: pytest.FixtureRequest) -> AsyncIterator[ConversationRepository]:
    if request.param == "memory":
        yield InMemoryConversationRepository()
        return
    await _clean_postgres()
    repository = PostgresConversationRepository(TEST_DATABASE_URL)
    await repository.open()
    try:
        yield repository
    finally:
        await repository.close()


async def test_unknown_user_gets_default_state(repo: ConversationRepository) -> None:
    state = await repo.get("+56912345678")

    assert state.user_id == "+56912345678"
    assert state.mode is ConversationMode.GREETING
    assert state.history == []
    assert state.preferences == {}


async def test_state_round_trip(repo: ConversationRepository) -> None:
    state = await repo.get("u1")
    state.mode = ConversationMode.BALANCE_OVERVIEW
    state.preferences["show_financial_figures"] = False
    state.history.append(ConversationMessage(role="user", content="hola"))
    state.milestones["primer_contacto"] = utc_now()
    state.timeline.append(
        TimelineEntry(
            turn=1,
            mode_from=ConversationMode.GREETING,
            mode_to=ConversationMode.BALANCE_OVERVIEW,
            intent="saludo",
        )
    )
    state.chosen_payment = 100_000

    await repo.save(state)
    loaded = await repo.get("u1")

    assert loaded == state
    assert loaded.mode is ConversationMode.BALANCE_OVERVIEW
    assert loaded.preferences == {"show_financial_figures": False}
    assert [m.content for m in loaded.history] == ["hola"]


async def test_save_overwrites(repo: ConversationRepository) -> None:
    state = await repo.get("u1")
    await repo.save(state)
    state.turn_count = 3
    await repo.save(state)

    assert (await repo.get("u1")).turn_count == 3


async def test_get_returns_a_copy(repo: ConversationRepository) -> None:
    await repo.save(await repo.get("u1"))

    state = await repo.get("u1")
    state.turn_count = 99

    assert (await repo.get("u1")).turn_count == 0


async def test_history_and_timeline_are_capped(repo: ConversationRepository) -> None:
    state = await repo.get("u1")
    state.history = [
        ConversationMessage(role="user", content=str(i)) for i in range(MAX_HISTORY_MESSAGES + 5)
    ]
    state.timeline = [
        TimelineEntry(
            turn=i,
            mode_from=ConversationMode.GREETING,
            mode_to=ConversationMode.GREETING,
            intent="saludo",
        )
        for i in range(MAX_TIMELINE_ENTRIES + 5)
    ]

    await repo.save(state)

    loaded = await repo.get("u1")
    assert len(loaded.history) == MAX_HISTORY_MESSAGES
    assert loaded.history[-1].content == str(MAX_HISTORY_MESSAGES + 4)
    assert len(loaded.timeline) == MAX_TIMELINE_ENTRIES
    assert loaded.timeline[-1].turn == MAX_TIMELINE_ENTRIES + 4


async def test_claim_message_deduplicates(repo: ConversationRepository) -> None:
    assert await repo.claim_message("SM1") is True
    assert await repo.claim_message("SM1") is False
    assert await repo.claim_message("SM2") is True


async def test_concurrent_claims_yield_exactly_one_winner(repo: ConversationRepository) -> None:
    results = await asyncio.gather(*(repo.claim_message("SM-race") for _ in range(5)))

    assert sorted(results) == [False, False, False, False, True]


async def test_reset_deletes_the_user_but_keeps_processed_messages(
    repo: ConversationRepository,
) -> None:
    for user in ("u1", "u2"):
        state = await repo.get(user)
        state.turn_count = 5
        await repo.save(state)
    assert await repo.claim_message("SM1") is True

    await repo.reset("u1")

    assert (await repo.get("u1")).turn_count == 0
    assert (await repo.get("u2")).turn_count == 5
    assert await repo.claim_message("SM1") is False


async def test_list_all_most_recent_first(repo: ConversationRepository) -> None:
    now = utc_now()
    for user, minutes_ago in (("old", 10), ("new", 1), ("mid", 5)):
        state = await repo.get(user)
        state.updated_at = now - timedelta(minutes=minutes_ago)
        await repo.save(state)

    assert [s.user_id for s in await repo.list_all()] == ["new", "mid", "old"]


def test_build_repository_defaults_to_memory() -> None:
    repository = build_repository(Settings(_env_file=None))

    assert isinstance(repository, InMemoryConversationRepository)


def test_build_repository_uses_postgres_with_a_url() -> None:
    url = "postgresql://user:secret@db:5432/contigo"

    repository = build_repository(Settings(_env_file=None, database_url=url))

    assert isinstance(repository, PostgresConversationRepository)
    assert "secret" not in repr(repository)


@requires_postgres
def test_conversations_survive_an_app_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_clean_postgres())
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    message = IncomingMessage(message_sid="SM1", from_number="whatsapp:+56900000001", body="Hola")

    # TestClient como context manager ejecuta el lifespan (abre y cierra el pool).
    with TestClient(create_app()) as client:
        assert client.portal.call(client.app.state.message_handler.handle, message)

    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        assert repository is client.app.state.message_handler._repository
        state = client.portal.call(repository.get, "+56900000001")
        assert state.turn_count == 1
        # El mismo MessageSid tras el "redeploy" sigue siendo duplicado.
        assert client.portal.call(client.app.state.message_handler.handle, message) is None
