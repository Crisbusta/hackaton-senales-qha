"""Flujo completo: webhook firmado → ConversationService → MockAgent → TwiML."""

import xml.etree.ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.core.config import Settings, get_settings
from app.main import create_app
from app.modules.agent.mock import DECLINE_REPLY, GREETING_AFTER_DECLINE_REPLY, GREETING_REPLY

TOKEN = "test-auth-token"
BASE = "https://contigo.example.com"
PATH = "/webhooks/whatsapp"


@pytest.fixture
def client() -> TestClient:
    app: FastAPI = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, app_env="test", twilio_auth_token=TOKEN, public_base_url=BASE
    )
    return TestClient(app)


def send(client: TestClient, body: str, sid: str) -> list[str]:
    params = {"MessageSid": sid, "From": "whatsapp:+56912345678", "Body": body, "NumMedia": "0"}
    signature = RequestValidator(TOKEN).compute_signature(BASE + PATH, params)
    response = client.post(PATH, data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    return [m.text or "" for m in ET.fromstring(response.text).findall("Message")]


def test_greeting_reply(client: TestClient) -> None:
    assert send(client, "Hola", "SM1") == [GREETING_REPLY]


def test_duplicate_delivery_gets_empty_twiml(client: TestClient) -> None:
    send(client, "Hola", "SM1")

    assert send(client, "Hola", "SM1") == []


def test_decline_is_remembered_across_messages(client: TestClient) -> None:
    assert send(client, "Prefiero no verlo todavía", "SM1") == [DECLINE_REPLY]
    assert send(client, "Hola", "SM2") == [GREETING_AFTER_DECLINE_REPLY]


def test_statement_pdf_through_the_signed_webhook(client: TestClient) -> None:
    from pathlib import Path

    from app.modules.conversation.statement_intake import RECEIVED_REPLY

    pdf = (
        Path(__file__).parent / "statements/fixtures/2026-10_estado_de_cuenta_ficticio.pdf"
    ).read_bytes()

    class FakeFetcher:
        async def fetch(self, url: str, max_bytes: int) -> bytes:
            return pdf

    client.app.state.message_handler._media_fetcher = FakeFetcher()
    params = {
        "MessageSid": "SMpdf",
        "From": "whatsapp:+56912345678",
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1",
        "MediaContentType0": "application/pdf",
    }
    signature = RequestValidator(TOKEN).compute_signature(BASE + PATH, params)

    response = client.post(PATH, data=params, headers={"X-Twilio-Signature": signature})

    assert response.status_code == 200
    assert [m.text for m in ET.fromstring(response.text).findall("Message")] == [
        RECEIVED_REPLY.format(mes="octubre")
    ]
