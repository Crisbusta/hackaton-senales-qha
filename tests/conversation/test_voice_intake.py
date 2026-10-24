"""Notas de voz: transcripción → turno normal, fallos con texto fijo, confirmación de montos."""

import asyncio
from pathlib import Path

import pytest

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    ConversationMode,
)
from app.modules.banking.provider import MockBankProvider
from app.modules.conversation import voice_intake
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.service import ConversationService
from app.modules.voice.schemas import AudioDecodeError, AudioTooLongError, Transcript
from app.modules.whatsapp.media import MediaTooLargeError
from app.modules.whatsapp.schemas import IncomingMedia, IncomingMessage
from app.shared.exceptions import IntegrationError
from tests.conftest import TwoStepAgent as SpyAgent

USER_ID = "+56912345678"
MEDIA_URL = "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1"
AUDIO = b"OggS-fake-audio"
PDF = (
    Path(__file__).parents[1] / "statements" / "fixtures" / "2026-10_estado_de_cuenta_ficticio.pdf"
).read_bytes()


class FakeFetcher:
    def __init__(self, content: bytes = AUDIO, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.urls: list[str] = []

    async def fetch(self, url: str, max_bytes: int) -> bytes:
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.content


class FakeTranscriber:
    """Devuelve las transcripciones en orden; `delay` simula un modelo lento."""

    def __init__(self, *texts: str, error: Exception | None = None, delay: float = 0) -> None:
        self.texts = list(texts)
        self.error = error
        self.delay = delay
        self.received: list[bytes] = []

    async def transcribe(self, audio_bytes: bytes) -> Transcript:
        self.received.append(audio_bytes)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return Transcript(text=self.texts.pop(0), duration_s=3.0, elapsed_s=0.5)


class Chat:
    def __init__(
        self,
        transcriber=None,
        fetcher=None,
        mode=ConversationMode.GREETING,
        voice_timeout_s=5.0,
        **state,
    ) -> None:
        self.agent = SpyAgent()
        self.repo = InMemoryConversationRepository()
        seeded = asyncio.run(self.repo.get(USER_ID))
        seeded.mode = mode
        seeded.max_mode_reached = mode
        for key, value in state.items():
            setattr(seeded, key, value)
        asyncio.run(self.repo.save(seeded))
        self.fetcher = fetcher or FakeFetcher()
        self.transcriber = transcriber
        self.service = ConversationService(
            agent=self.agent,
            repository=self.repo,
            agent_timeout_s=5,
            bank=MockBankProvider("patricia"),
            media_fetcher=self.fetcher,
            transcriber=transcriber,
            voice_timeout_s=voice_timeout_s,
        )
        self._sid = 0

    def send(self, body: str = "", content_type: str | None = None) -> str | None:
        self._sid += 1
        media = [IncomingMedia(url=MEDIA_URL, content_type=content_type)] if content_type else []
        incoming = IncomingMessage(
            message_sid=f"SM{self._sid}",
            from_number=f"whatsapp:{USER_ID}",
            body=body,
            num_media=len(media),
            media=media,
        )
        return asyncio.run(self.service.handle(incoming))

    def voice(self) -> str | None:
        return self.send(content_type="audio/ogg")

    @property
    def state(self):
        return asyncio.run(self.repo.get(USER_ID))


# --- Transcripción → turno normal ---------------------------------------------


@pytest.mark.parametrize(
    "content_type", ["audio/ogg", "audio/ogg; codecs=opus", "audio/mpeg", "audio/mp4", "audio/amr"]
)
def test_voice_note_transcript_becomes_the_agent_message(content_type):
    chat = Chat(FakeTranscriber("Hola, ¿cuándo tengo que pagar la tarjeta?"))

    reply = chat.send(content_type=content_type)

    assert reply == "ok"
    assert chat.fetcher.urls == [MEDIA_URL]
    assert chat.transcriber.received == [AUDIO]
    assert chat.agent.requests[0].message == "Hola, ¿cuándo tengo que pagar la tarjeta?"
    history = chat.state.history
    assert history[0].content == "(audio) Hola, ¿cuándo tengo que pagar la tarjeta?"
    assert history[1].content == "ok"
    assert chat.state.timeline[-1].intent != "voice_unheard"


def test_text_only_message_is_unaffected():
    chat = Chat(FakeTranscriber())

    chat.send("hola")

    assert chat.agent.requests[0].message == "hola"
    assert chat.fetcher.urls == []
    assert chat.state.history[0].content == "hola"


def test_audio_with_text_uses_the_text_and_ignores_the_audio():
    chat = Chat(FakeTranscriber("otra cosa"))

    chat.send("hola", content_type="audio/ogg")

    assert chat.agent.requests[0].message == "hola"
    assert chat.transcriber.received == []


def test_without_transcriber_audio_follows_the_old_flow():
    chat = Chat(transcriber=None)

    chat.voice()

    assert chat.agent.requests[0].message == ""
    assert chat.fetcher.urls == []


def test_pdf_is_still_handled_as_a_statement():
    chat = Chat(FakeTranscriber("nada"), fetcher=FakeFetcher(PDF))

    reply = chat.send(content_type="application/pdf")

    assert reply.startswith("Recibí tu estado de cuenta")
    assert chat.agent.requests == []
    assert chat.transcriber.received == []


# --- Fallos: texto fijo, sin agente -------------------------------------------


@pytest.mark.parametrize(
    ("transcriber", "fetcher", "reply", "outcome"),
    [
        (FakeTranscriber("   "), None, voice_intake.UNHEARD_REPLY, "voice_unheard"),
        (
            FakeTranscriber(error=AudioDecodeError("x")),
            None,
            voice_intake.UNHEARD_REPLY,
            "voice_unheard",
        ),
        (
            FakeTranscriber(error=AudioTooLongError("x")),
            None,
            voice_intake.TOO_LONG_REPLY,
            "voice_too_long",
        ),
        (
            FakeTranscriber(error=RuntimeError("modelo roto")),
            None,
            voice_intake.UNHEARD_REPLY,
            "voice_unheard",
        ),
        (
            FakeTranscriber("hola"),
            FakeFetcher(error=IntegrationError("caído")),
            voice_intake.UNHEARD_REPLY,
            "voice_download_failed",
        ),
        (
            FakeTranscriber("hola"),
            FakeFetcher(error=MediaTooLargeError("grande")),
            voice_intake.UNHEARD_REPLY,
            "voice_download_failed",
        ),
    ],
)
def test_failures_reply_with_fixed_text_and_skip_the_agent(transcriber, fetcher, reply, outcome):
    chat = Chat(transcriber, fetcher=fetcher)

    assert chat.voice() == reply

    assert chat.agent.requests == []
    state = chat.state
    assert state.history[0].content == voice_intake.UNHEARD_HISTORY
    assert state.history[1].content == reply
    assert state.timeline[-1].intent == outcome
    assert state.turn_count == 1


def test_timeout_replies_with_fixed_text():
    chat = Chat(FakeTranscriber("hola", delay=1.0), voice_timeout_s=0.05)

    assert chat.voice() == voice_intake.UNHEARD_REPLY
    assert chat.agent.requests == []
    assert chat.state.timeline[-1].intent == "voice_timeout"


def test_transcript_is_never_logged(caplog):
    chat = Chat(FakeTranscriber("me siento súper mal con la plata"))
    with caplog.at_level("DEBUG"):
        chat.voice()
    assert "súper mal" not in caplog.text


# --- Montos dichos por voz: confirmar antes de simular --------------------------


def test_spoken_payment_asks_for_confirmation_before_simulating():
    chat = Chat(FakeTranscriber("¿Y si pago 100 mil?"), mode=ConversationMode.BALANCE_OVERVIEW)

    reply = chat.voice()

    assert reply == "Te entendí $100.000. ¿Quieres que pruebe con $100.000?"
    assert chat.agent.requests == []
    state = chat.state
    assert state.mode is ConversationMode.BALANCE_OVERVIEW  # todavía no simula
    assert state.chosen_payment is None
    assert state.pending_voice_text == "¿Y si pago 100 mil?"
    assert state.history[0].content == "(audio) ¿Y si pago 100 mil?"
    assert state.timeline[-1].intent == "voice_confirm_amount"


@pytest.mark.parametrize("answer", ["sí", "Sí, dale", "dale", "ya po", "ok"])
def test_yes_after_confirmation_runs_the_simulation(answer):
    chat = Chat(FakeTranscriber("¿Y si pago 100 mil?"), mode=ConversationMode.BALANCE_OVERVIEW)
    chat.voice()

    chat.send(answer)

    request = chat.agent.requests[-1]
    assert request.message == "¿Y si pago 100 mil?"
    assert request.mode is ConversationMode.AMOUNT_SIMULATION
    assert request.financial_data["monto_elegido"].value == 100_000
    assert request.calculations["meses_pagando_monto_elegido"] == 13
    state = chat.state
    assert state.pending_voice_text is None
    assert state.history[-2].content == answer  # lo que ella respondió de verdad


def test_spoken_yes_also_confirms():
    chat = Chat(
        FakeTranscriber("¿Y si pago 100 mil?", "Sí."), mode=ConversationMode.BALANCE_OVERVIEW
    )
    chat.voice()

    chat.voice()

    request = chat.agent.requests[-1]
    assert request.mode is ConversationMode.AMOUNT_SIMULATION
    assert request.financial_data["monto_elegido"].value == 100_000
    assert chat.state.history[-2].content == "(audio) Sí."


@pytest.mark.parametrize("answer", ["no", "no, era 10 mil", "sí, pero no ahora", "mejor otro día"])
def test_anything_else_discards_the_pending_amount(answer):
    chat = Chat(FakeTranscriber("¿Y si pago 100 mil?"), mode=ConversationMode.BALANCE_OVERVIEW)
    chat.voice()

    chat.send(answer)

    request = chat.agent.requests[-1]
    assert request.message == answer
    assert request.mode is ConversationMode.BALANCE_OVERVIEW
    assert chat.state.pending_voice_text is None


def test_pending_amount_only_lasts_one_turn():
    chat = Chat(FakeTranscriber("¿Y si pago 100 mil?"), mode=ConversationMode.BALANCE_OVERVIEW)
    chat.voice()
    chat.send("mejor cuéntame otra cosa")

    chat.send("sí")

    assert chat.agent.requests[-1].message == "sí"


def test_typed_amount_still_simulates_without_confirmation():
    chat = Chat(FakeTranscriber(), mode=ConversationMode.BALANCE_OVERVIEW)

    chat.send("¿y si pago 100 mil?")

    assert chat.agent.requests[-1].mode is ConversationMode.AMOUNT_SIMULATION


def test_spoken_amount_before_e2_does_not_ask_confirmation():
    # Desde E0 el simulador no se activa, así que no hay nada que confirmar.
    chat = Chat(FakeTranscriber("¿Y si pago 100 mil?"))

    chat.voice()

    assert chat.agent.requests[-1].message == "¿Y si pago 100 mil?"
    assert chat.state.pending_voice_text is None


def test_spoken_amount_with_figures_declined_does_not_ask_confirmation():
    chat = Chat(
        FakeTranscriber("¿Y si pago 100 mil?"),
        mode=ConversationMode.BALANCE_OVERVIEW,
        preferences={PREF_SHOW_FINANCIAL_FIGURES: False},
    )

    chat.voice()

    assert len(chat.agent.requests) == 1
    assert chat.state.pending_voice_text is None


def test_spoken_installment_purchase_asks_for_confirmation():
    chat = Chat(
        FakeTranscriber("Eran 60 lucas en 6 cuotas"), mode=ConversationMode.INSTALLMENT_PAUSE
    )
    chat.send("2")  # eligió ver cuánto le suma la cuota
    calls = len(chat.agent.requests)

    reply = chat.voice()

    assert reply == "Te entendí $60.000 en 6 cuotas. ¿Quieres que lo calcule?"
    assert len(chat.agent.requests) == calls

    chat.send("dale")

    request = chat.agent.requests[-1]
    assert request.requested_installment_calculation is True
    assert "minimo_con_cuota" in request.financial_data
    assert "cuota_mensual" in request.calculations


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sí", True),
        ("Sí, dale.", True),
        ("SI PO", True),
        ("bueno ya", True),
        ("no", False),
        ("sí pero no", False),
        ("", False),
        ("si me pagaran más podría pagar más que eso", False),  # muy largo para ser un "sí"
    ],
)
def test_is_affirmative(text, expected):
    assert voice_intake.is_affirmative(text) is expected
