"""ConversationService arma `financial_data` desde el banco, filtrado por modo."""

import asyncio
import json
from pathlib import Path

import pytest

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentRequest,
    AgentResponse,
    ConversationMode,
    Intent,
)
from app.modules.banking.provider import FIXTURES_DIR, MockBankProvider
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.service import ConversationService
from app.modules.whatsapp.schemas import IncomingMessage

USER_ID = "+56912345678"


class SpyAgent:
    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []

    async def process(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        return AgentResponse(message="ok")


class BrokenBank:
    async def get_snapshot(self, user_id: str):
        raise ConnectionError("bank down")


def turn(bank, *, mode=ConversationMode.GREETING, last_intent=None, preferences=None):
    """Ejecuta un turno con el estado sembrado y devuelve (request al agente, respuesta)."""
    agent = SpyAgent()
    repo = InMemoryConversationRepository()
    state = asyncio.run(repo.get(USER_ID))
    state.mode = mode
    state.last_intent = last_intent
    state.preferences = preferences or {}
    asyncio.run(repo.save(state))

    service = ConversationService(agent=agent, repository=repo, agent_timeout_s=5, bank=bank)
    reply = asyncio.run(
        service.handle(IncomingMessage(message_sid="SM1", from_number=f"whatsapp:{USER_ID}"))
    )
    return agent.requests[0], reply


@pytest.fixture
def bank() -> MockBankProvider:
    return MockBankProvider("patricia")


def values(request: AgentRequest) -> dict:
    return {name: field.value for name, field in request.financial_data.items()}


def test_greeting_carries_no_figures(bank) -> None:
    request, _ = turn(bank, mode=ConversationMode.GREETING)

    assert request.financial_data == {}


def test_due_date_mode_carries_only_the_due_date(bank) -> None:
    request, _ = turn(bank, mode=ConversationMode.DUE_DATE)

    assert values(request) == {"vencimiento": "3 de noviembre de 2026"}


def test_balance_overview_carries_minimum_and_balance(bank) -> None:
    request, _ = turn(bank, mode=ConversationMode.BALANCE_OVERVIEW)

    assert values(request) == {
        "vencimiento": "3 de noviembre de 2026",
        "minimo": 74350,
        "saldo": 1064000,
    }


def test_minimum_breakdown_adds_its_parts(bank) -> None:
    request, _ = turn(bank, mode=ConversationMode.MINIMUM_BREAKDOWN)

    assert values(request)["minimo_intereses"] == 23100
    assert values(request)["minimo_cuotas"] == 43000
    assert values(request)["minimo_abono"] == 8250
    assert "facturado" not in request.financial_data


def test_billed_amount_only_after_she_asks(bank) -> None:
    request, _ = turn(
        bank, mode=ConversationMode.BALANCE_OVERVIEW, last_intent=Intent.ASKS_BILLED_AMOUNT
    )

    assert values(request)["facturado"] == 868000


def test_declined_figures_empties_financial_data(bank) -> None:
    request, _ = turn(
        bank,
        mode=ConversationMode.BALANCE_OVERVIEW,
        preferences={PREF_SHOW_FINANCIAL_FIGURES: False},
    )

    assert request.financial_data == {}


def test_support_mode_never_carries_figures(bank) -> None:
    request, _ = turn(bank, mode=ConversationMode.SUPPORT)

    assert request.financial_data == {}


def test_without_consent_no_bank_data_is_used(tmp_path: Path) -> None:
    data = json.loads((FIXTURES_DIR / "patricia.json").read_text(encoding="utf-8"))
    data["consent"]["granted"] = False
    (tmp_path / "sin_consentimiento.json").write_text(json.dumps(data), encoding="utf-8")

    request, _ = turn(
        MockBankProvider("sin_consentimiento", fixtures_dir=tmp_path),
        mode=ConversationMode.BALANCE_OVERVIEW,
    )

    assert request.financial_data == {}


def test_bank_failure_does_not_break_the_turn() -> None:
    request, reply = turn(BrokenBank(), mode=ConversationMode.BALANCE_OVERVIEW)

    assert request.financial_data == {}
    assert reply == "ok"


def test_without_bank_provider_there_is_no_data() -> None:
    request, _ = turn(None, mode=ConversationMode.BALANCE_OVERVIEW)

    assert request.financial_data == {}
