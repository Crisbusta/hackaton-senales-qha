"""T07: recibir el PDF por WhatsApp, guardarlo y conciliarlo con el banco."""

import asyncio
from pathlib import Path

import pytest

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentReply,
    Classification,
    ConversationEvent,
    FieldStatus,
    FinancialField,
    Intent,
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
USER_ID = "+56912345678"
MEDIA_URL = "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1"


class SpyAgent:
    def __init__(self) -> None:
        self.requests = []

    async def classify(self, request):
        # Pedir un dato sube el escalon, que es lo que estos tests necesitan
        # para ver datos en el contexto.
        if "?" in request.message:
            return Classification(
                intent=Intent.INFO_REQUEST,
                event=ConversationEvent.REQUESTED_BALANCE,
            )
        return Classification(intent=Intent.OFF_TOPIC)

    async def write(self, request):
        self.requests.append(request)
        return AgentReply(message="ok")


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

    # "¿cuándo vence?" sube a E1, y E1 manda exactamente el vencimiento: el
    # resto de la Escalera ya no viaja de arrastre (se fue la precarga).
    data = agent.requests[0].financial_data
    assert set(data) == {"vencimiento"}
    assert data["vencimiento"].value == "3 de noviembre de 2026"


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
    assert merged["vencimiento"].status is FieldStatus.UNRELIABLE
    assert merged["vencimiento"].value is None


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


# --- B10: el motor financiero usa el PDF, no solo el banco --------------


def test_el_simulador_calcula_con_la_cifra_del_pdf() -> None:
    """El PDF manda en la conciliacion, pero el motor calculaba solo con el
    banco: si el PDF corrigio el minimo, la simulacion se hacia con el viejo."""
    from app.modules.agent.interface import FieldStatus, FinancialField
    from app.modules.conversation.schemas import ConversationState, StoredStatement
    from app.modules.conversation.simulation import _cifra_del_estado_de_cuenta

    state = ConversationState(user_id=USER_ID)
    assert _cifra_del_estado_de_cuenta(state, "minimo") is None  # sin PDF, manda el banco

    state.statement = StoredStatement(
        fields={"minimo": FinancialField(value=80000, status=FieldStatus.CONFIRMED)}
    )
    assert _cifra_del_estado_de_cuenta(state, "minimo") == 80000


def test_una_cifra_en_conflicto_no_la_usa_el_simulador() -> None:
    """`UNRELIABLE` son dos valores que no coinciden y no se elige ninguno:
    calcular con uno de ellos seria inventar."""
    from app.modules.agent.interface import FieldStatus, FinancialField
    from app.modules.conversation.schemas import ConversationState, StoredStatement
    from app.modules.conversation.simulation import _cifra_del_estado_de_cuenta

    state = ConversationState(user_id=USER_ID)
    state.statement = StoredStatement(
        fields={"minimo": FinancialField(value=[80000, 74350], status=FieldStatus.UNRELIABLE)}
    )
    assert _cifra_del_estado_de_cuenta(state, "minimo") is None
