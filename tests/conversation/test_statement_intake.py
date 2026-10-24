"""T07: recibir el PDF por WhatsApp, guardarlo y conciliarlo con el banco."""

import asyncio
from datetime import date
from pathlib import Path

import pytest

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentRequest,
    AgentResponse,
    FieldStatus,
    FinancialField,
)
from app.modules.banking.provider import MockBankProvider
from app.modules.conversation import statement_intake
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.schemas import StoredStatement
from app.modules.conversation.service import ConversationService
from app.modules.whatsapp.media import MediaTooLargeError
from app.modules.whatsapp.schemas import IncomingMedia, IncomingMessage
from app.shared.exceptions import IntegrationError

FIXTURES = Path(__file__).parents[1] / "statements" / "fixtures"
OCTOBER_PDF = (FIXTURES / "2026-10_estado_de_cuenta_ficticio.pdf").read_bytes()
AUGUST_PDF = (FIXTURES / "2026-08_estado_de_cuenta_ficticio.pdf").read_bytes()
USER_ID = "+56912345678"
MEDIA_URL = "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1"


class SpyAgent:
    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []

    async def process(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        return AgentResponse(message="ok")


class FakeFetcher:
    def __init__(self, content: bytes = OCTOBER_PDF, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.urls: list[str] = []

    async def fetch(self, url: str, max_bytes: int) -> bytes:
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.content


def setup(fetcher=None, bank=None):
    agent = SpyAgent()
    repo = InMemoryConversationRepository()
    service = ConversationService(
        agent=agent,
        repository=repo,
        agent_timeout_s=5,
        bank=bank,
        media_fetcher=fetcher or FakeFetcher(),
    )
    return agent, repo, service


def send(service, body="", content_type=None, sid="SM1"):
    media = [IncomingMedia(url=MEDIA_URL, content_type=content_type)] if content_type else []
    message = IncomingMessage(
        message_sid=sid,
        from_number=f"whatsapp:{USER_ID}",
        body=body,
        num_media=len(media),
        media=media,
    )
    return asyncio.run(service.handle(message))


def state_of(repo):
    return asyncio.run(repo.get(USER_ID))


# --- Recepción ---------------------------------------------------------------


def test_pdf_is_read_stored_and_acknowledged_without_figures() -> None:
    agent, repo, service = setup()

    reply = send(service, content_type="application/pdf")

    assert reply == statement_intake.RECEIVED_REPLY.format(mes="octubre")
    assert not any(ch.isdigit() for ch in reply)  # exposición progresiva: sin cifras
    assert agent.requests == []  # no se llama al agente
    state = state_of(repo)
    assert state.statement is not None
    assert state.statement.fields["minimo"].value == 74350
    assert state.statement.issues == []
    assert "compartio_estado" in state.milestones
    assert state.timeline[-1].intent == "pdf_ok"


def test_next_turn_uses_the_statement_as_a_source() -> None:
    agent, _, service = setup()  # sin banco: solo el PDF

    send(service, content_type="application/pdf", sid="SM1")
    send(service, body="¿cuándo vence?", sid="SM2")

    data = agent.requests[0].financial_data
    assert data["vencimiento"].value == "3 de noviembre de 2026"
    assert data["minimo"].value == 74350


def test_statement_and_bank_agree_for_patricia() -> None:
    agent, _, service = setup(bank=MockBankProvider("patricia"))

    send(service, content_type="application/pdf", sid="SM1")
    send(service, body="Hola", sid="SM2")

    data = agent.requests[0].financial_data
    assert all(field.status is FieldStatus.CONFIRMED for field in data.values())


def test_declined_figures_hides_the_statement_too() -> None:
    agent, repo, service = setup()
    send(service, content_type="application/pdf", sid="SM1")
    state = state_of(repo)
    state.preferences[PREF_SHOW_FINANCIAL_FIGURES] = False
    asyncio.run(repo.save(state))

    send(service, body="Hola", sid="SM2")

    assert agent.requests[0].financial_data == {}


def test_images_and_audio_follow_the_normal_flow() -> None:
    agent, repo, service = setup()

    send(service, content_type="image/jpeg")

    assert len(agent.requests) == 1
    assert state_of(repo).statement is None


@pytest.mark.parametrize(
    ("fetcher", "expected"),
    [
        (FakeFetcher(content=b"no es un pdf"), statement_intake.UNREADABLE_REPLY),
        (FakeFetcher(error=MediaTooLargeError("big")), statement_intake.UNREADABLE_REPLY),
        (
            FakeFetcher(error=IntegrationError("twilio down")),
            statement_intake.DOWNLOAD_FAILED_REPLY,
        ),
    ],
)
def test_failures_get_a_friendly_reply_and_store_nothing(fetcher, expected) -> None:
    agent, repo, service = setup(fetcher=fetcher)

    reply = send(service, content_type="application/pdf")

    assert reply == expected
    assert agent.requests == []
    assert state_of(repo).statement is None
    assert "compartio_estado" not in state_of(repo).milestones


# --- Conciliación banco ↔ PDF -----------------------------------------------


def field(value, status=FieldStatus.CONFIRMED) -> FinancialField:
    return FinancialField(value=value, status=status)


def doc(**fields) -> StoredStatement:
    return StoredStatement(fields=fields)


def test_merge_prefers_the_document_when_both_agree() -> None:
    merged = statement_intake.merge_sources({"minimo": field(74350)}, doc(minimo=field(74350)))

    assert merged["minimo"] == field(74350)


def test_merge_marks_disagreements_as_unreliable_with_both_values() -> None:
    merged = statement_intake.merge_sources(
        {"minimo": field(74350), "vencimiento": field("3 de noviembre de 2026")},
        doc(minimo=field(47350), vencimiento=field("5 de noviembre de 2026")),
    )

    assert merged["minimo"] == FinancialField(value=[47350, 74350], status=FieldStatus.UNRELIABLE)
    assert merged["vencimiento"] == FinancialField(
        value="5 de noviembre de 2026 / 3 de noviembre de 2026", status=FieldStatus.UNRELIABLE
    )


def test_merge_uses_the_bank_when_the_document_lacks_a_value() -> None:
    absent = field(None, FieldStatus.ABSENT)

    with_bank = statement_intake.merge_sources({"tasa": field("2,8 % mensual")}, doc(tasa=absent))
    without_bank = statement_intake.merge_sources({}, doc(tasa=absent))

    assert with_bank["tasa"] == field("2,8 % mensual")
    assert without_bank["tasa"].status is FieldStatus.ABSENT


def test_merge_keeps_document_conflicts() -> None:
    conflict = FinancialField(value=[74350, 47350], status=FieldStatus.UNRELIABLE)

    merged = statement_intake.merge_sources({"minimo": field(74350)}, doc(minimo=conflict))

    assert merged["minimo"] == conflict


def test_without_statement_the_bank_is_used_as_is() -> None:
    bank = {"minimo": field(74350)}

    assert statement_intake.merge_sources(bank, None) == bank


# --- Períodos distintos (bug visto en producción: PDF de septiembre + banco de octubre) ---


def test_an_older_statement_does_not_override_current_bank_data() -> None:
    agent, repo, service = setup(fetcher=FakeFetcher(AUGUST_PDF), bank=MockBankProvider("patricia"))

    reply = send(service, content_type="application/pdf", sid="SM1")
    send(service, body="¿cuándo vence?", sid="SM2")

    assert reply == statement_intake.RECEIVED_OLDER_REPLY.format(mes="agosto", mes_actual="octubre")
    data = agent.requests[0].financial_data
    assert data["vencimiento"] == field("3 de noviembre de 2026")  # el del banco, vigente
    assert data["minimo"] == field(74350)
    assert all(f.status is FieldStatus.CONFIRMED for f in data.values())
    assert state_of(repo).timeline[0].intent == "pdf_ok_older_than_bank"  # el turno del PDF


def test_a_newer_statement_replaces_the_bank() -> None:
    newer = StoredStatement(fields={"minimo": field(80000)}, period_end=date(2026, 11, 15))

    merged = statement_intake.merge_sources(
        {"minimo": field(74350), "saldo": field(1064000)}, newer, date(2026, 10, 15)
    )

    assert merged == {"minimo": field(80000)}


def test_same_period_is_compared_field_by_field() -> None:
    same = StoredStatement(fields={"minimo": field(47350)}, period_end=date(2026, 10, 15))

    merged = statement_intake.merge_sources({"minimo": field(74350)}, same, date(2026, 10, 15))

    assert merged["minimo"] == FinancialField(value=[47350, 74350], status=FieldStatus.UNRELIABLE)
