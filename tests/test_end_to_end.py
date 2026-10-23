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
