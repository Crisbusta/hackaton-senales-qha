import asyncio

import pytest
from pydantic import ValidationError

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    Agent,
    AgentRequest,
    AgentResponse,
    ConversationEvent,
    ConversationMessage,
)


def test_request_defaults() -> None:
    request = AgentRequest(user_id="u1", message="Hola")

    assert request.history == []
    assert request.preferences == {}


def test_response_defaults() -> None:
    response = AgentResponse(message="Hola")

    assert response.intent == "UNKNOWN"
    assert response.event is None


def test_invalid_role_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ConversationMessage(role="system", content="x")


def test_unknown_event_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentResponse(message="x", event="NOT_AN_EVENT")


def test_event_parses_from_string() -> None:
    response = AgentResponse.model_validate({"message": "x", "event": "DECLINED_BALANCE_REVIEW"})

    assert response.event is ConversationEvent.DECLINED_BALANCE_REVIEW


def test_protocol_can_be_implemented_structurally() -> None:
    class EchoAgent:
        async def process(self, request: AgentRequest) -> AgentResponse:
            return AgentResponse(message=request.message)

    agent: Agent = EchoAgent()
    request = AgentRequest(
        user_id="u1",
        message="Hola",
        preferences={PREF_SHOW_FINANCIAL_FIGURES: False},
    )

    assert asyncio.run(agent.process(request)).message == "Hola"
