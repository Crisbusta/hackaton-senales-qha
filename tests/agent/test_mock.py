import asyncio
import re

import pytest

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentRequest,
    AgentResponse,
    ConversationEvent,
    ConversationMode,
)
from app.modules.agent.mock import (
    DECLINE_REPLY,
    FEAR_REPLY,
    GREETING_AFTER_DECLINE_REPLY,
    GREETING_REPLY,
    REQUEST_BALANCE_REPLY,
    MockAgent,
)


def ask(message: str, **preferences: bool) -> AgentResponse:
    request = AgentRequest(
        user_id="+56912345678",
        message=message,
        mode=ConversationMode.GREETING,
        preferences=preferences,
    )
    return asyncio.run(MockAgent().process(request))


def test_greeting() -> None:
    response = ask("Hola")

    assert response.message == GREETING_REPLY
    assert response.intent == "saludo"
    assert response.event is None


def test_fear_is_acknowledged_without_changing_preferences() -> None:
    response = ask("Me da miedo revisar mi deuda")

    assert response.message == FEAR_REPLY
    assert response.event is None


def test_balance_request_emits_event() -> None:
    response = ask("Quiero saber cuánto debo")

    assert response.message == REQUEST_BALANCE_REPLY
    assert response.event is ConversationEvent.REQUESTED_BALANCE


def test_explicit_decline_emits_event() -> None:
    response = ask("Prefiero no verlo todavía")

    assert response.message == DECLINE_REPLY
    assert response.event is ConversationEvent.DECLINED_BALANCE_REVIEW


def test_decline_wins_over_balance_request() -> None:
    response = ask("No quiero saber cuánto debo")

    assert response.event is ConversationEvent.DECLINED_BALANCE_REVIEW


def test_greeting_after_decline_does_not_insist() -> None:
    response = ask("hola", **{PREF_SHOW_FINANCIAL_FIGURES: False})

    assert response.message == GREETING_AFTER_DECLINE_REPLY


@pytest.mark.parametrize(
    "message",
    ["Hola", "Me da miedo revisar mi deuda", "Quiero saber cuánto debo", "Prefiero no", "x"],
)
def test_mock_never_invents_figures(message: str) -> None:
    assert not re.search(r"\d", ask(message).message)
