import xml.etree.ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from twilio.request_validator import RequestValidator

from app.core.config import Settings, get_settings
from app.main import create_app
from app.modules.whatsapp.schemas import IncomingMessage
from app.modules.whatsapp.service import FALLBACK_REPLY, get_message_handler
from app.shared.exceptions import IntegrationError

TOKEN = "test-auth-token"
PUBLIC_BASE_URL = "https://contigo.example.com"
WEBHOOK_PATH = "/webhooks/whatsapp"
SIGNED_URL = PUBLIC_BASE_URL + WEBHOOK_PATH

VALID_PARAMS = {
    "MessageSid": "SM00000000000000000000000000000001",
    "From": "whatsapp:+56912345678",
    "To": "whatsapp:+14155238886",
    "Body": "Hola",
    "NumMedia": "0",
}


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "twilio_auth_token": TOKEN,
        "public_base_url": PUBLIC_BASE_URL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def sign(params: dict[str, str], url: str = SIGNED_URL, token: str = TOKEN) -> str:
    return RequestValidator(token).compute_signature(url, params)


@pytest.fixture
def app() -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: make_settings()
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def post_webhook(client: TestClient, params: dict[str, str], signature: str | None = None):
    headers = {"X-Twilio-Signature": sign(params) if signature is None else signature}
    return client.post(WEBHOOK_PATH, data=params, headers=headers)


class RecordingHandler:
    def __init__(self) -> None:
        self.messages: list[IncomingMessage] = []

    async def handle(self, message: IncomingMessage) -> str:
        self.messages.append(message)
        return "ok"


class FailingHandler:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def handle(self, message: IncomingMessage) -> str:
        raise self.exc


def twiml_messages(xml: str) -> list[str]:
    root = ET.fromstring(xml)
    assert root.tag == "Response"
    return [message.text or "" for message in root.findall("Message")]


# --- Firma ---------------------------------------------------------------


def test_valid_webhook_returns_twiml(client: TestClient) -> None:
    response = post_webhook(client, VALID_PARAMS)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert len(twiml_messages(response.text)) == 1


def test_invalid_signature_is_rejected(client: TestClient) -> None:
    response = post_webhook(client, VALID_PARAMS, signature="invalid")

    assert response.status_code == 403


def test_missing_signature_is_rejected(client: TestClient) -> None:
    response = client.post(WEBHOOK_PATH, data=VALID_PARAMS)

    assert response.status_code == 403


def test_tampered_body_is_rejected(client: TestClient) -> None:
    signature = sign(VALID_PARAMS)
    tampered = {**VALID_PARAMS, "Body": "otro mensaje"}

    response = post_webhook(client, tampered, signature=signature)

    assert response.status_code == 403


def test_signature_uses_public_base_url(client: TestClient) -> None:
    # Firmado contra la URL interna (como la vería la app detrás de un proxy).
    signature = sign(VALID_PARAMS, url="http://testserver" + WEBHOOK_PATH)

    response = post_webhook(client, VALID_PARAMS, signature=signature)

    assert response.status_code == 403


def test_missing_auth_token_fails_closed(app: FastAPI) -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings(twilio_auth_token="")
    client = TestClient(app)

    response = post_webhook(client, VALID_PARAMS, signature=sign(VALID_PARAMS, token=""))

    assert response.status_code == 403


def test_validation_can_be_disabled_outside_production(app: FastAPI) -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings(twilio_validate_signature=False)
    client = TestClient(app)

    response = client.post(WEBHOOK_PATH, data=VALID_PARAMS)

    assert response.status_code == 200


def test_validation_cannot_be_disabled_in_production() -> None:
    with pytest.raises(ValidationError):
        make_settings(app_env="production", twilio_validate_signature=False)


# --- Normalización -------------------------------------------------------


def test_extracts_from_and_body(app: FastAPI, client: TestClient) -> None:
    handler = RecordingHandler()
    app.dependency_overrides[get_message_handler] = lambda: handler

    post_webhook(client, {**VALID_PARAMS, "Body": "  Hola  "})

    [message] = handler.messages
    assert message.message_sid == VALID_PARAMS["MessageSid"]
    assert message.from_number == "whatsapp:+56912345678"
    assert message.user_id == "+56912345678"
    assert message.body == "Hola"
    assert message.num_media == 0
    assert message.media == []


def test_extracts_media_metadata(app: FastAPI, client: TestClient) -> None:
    handler = RecordingHandler()
    app.dependency_overrides[get_message_handler] = lambda: handler
    params = {
        **VALID_PARAMS,
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/SM1/Media/ME1",
        "MediaContentType0": "application/pdf",
    }

    response = post_webhook(client, params)

    assert response.status_code == 200
    [message] = handler.messages
    assert message.num_media == 1
    assert message.media[0].url == params["MediaUrl0"]
    assert message.media[0].content_type == "application/pdf"


def test_missing_message_sid_returns_400(client: TestClient) -> None:
    params = {k: v for k, v in VALID_PARAMS.items() if k != "MessageSid"}

    response = post_webhook(client, params)

    assert response.status_code == 400


def test_malformed_num_media_returns_400(client: TestClient) -> None:
    response = post_webhook(client, {**VALID_PARAMS, "NumMedia": "abc"})

    assert response.status_code == 400


# --- Errores -------------------------------------------------------------


@pytest.mark.parametrize(
    "exc", [IntegrationError("llm timeout"), RuntimeError("boom secret detail")]
)
def test_handler_errors_return_friendly_twiml(
    app: FastAPI, client: TestClient, exc: Exception
) -> None:
    app.dependency_overrides[get_message_handler] = lambda: FailingHandler(exc)

    response = post_webhook(client, VALID_PARAMS)

    assert response.status_code == 200
    assert twiml_messages(response.text) == [FALLBACK_REPLY]
    assert str(exc) not in response.text
    assert "Traceback" not in response.text


def test_body_is_not_logged(app: FastAPI, client: TestClient, caplog) -> None:
    secret_body = "debo 1.234.567 en la tarjeta"
    caplog.set_level("DEBUG")

    post_webhook(client, {**VALID_PARAMS, "Body": secret_body})

    assert secret_body not in caplog.text
    assert "+56912345678" not in caplog.text
    assert "***5678" in caplog.text
