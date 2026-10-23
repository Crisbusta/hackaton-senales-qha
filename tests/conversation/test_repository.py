import asyncio

from app.modules.agent.interface import ConversationMessage, ConversationMode
from app.modules.conversation.repository import (
    MAX_HISTORY_MESSAGES,
    InMemoryConversationRepository,
)


def test_unknown_user_gets_default_state() -> None:
    repo = InMemoryConversationRepository()

    state = asyncio.run(repo.get("+56912345678"))

    assert state.user_id == "+56912345678"
    assert state.mode is ConversationMode.GREETING
    assert state.history == []
    assert state.preferences == {}


def test_state_round_trip() -> None:
    repo = InMemoryConversationRepository()
    state = asyncio.run(repo.get("u1"))
    state.preferences["show_financial_figures"] = False
    state.history.append(ConversationMessage(role="user", content="hola"))

    asyncio.run(repo.save(state))
    loaded = asyncio.run(repo.get("u1"))

    assert loaded.preferences == {"show_financial_figures": False}
    assert [m.content for m in loaded.history] == ["hola"]


def test_get_returns_a_copy() -> None:
    repo = InMemoryConversationRepository()
    asyncio.run(repo.save(asyncio.run(repo.get("u1"))))

    state = asyncio.run(repo.get("u1"))
    state.turn_count = 99

    assert asyncio.run(repo.get("u1")).turn_count == 0


def test_history_is_capped() -> None:
    repo = InMemoryConversationRepository()
    state = asyncio.run(repo.get("u1"))
    state.history = [
        ConversationMessage(role="user", content=str(i)) for i in range(MAX_HISTORY_MESSAGES + 5)
    ]

    asyncio.run(repo.save(state))

    history = asyncio.run(repo.get("u1")).history
    assert len(history) == MAX_HISTORY_MESSAGES
    assert history[-1].content == str(MAX_HISTORY_MESSAGES + 4)


def test_claim_message_deduplicates() -> None:
    repo = InMemoryConversationRepository()

    assert asyncio.run(repo.claim_message("SM1")) is True
    assert asyncio.run(repo.claim_message("SM1")) is False
    assert asyncio.run(repo.claim_message("SM2")) is True
