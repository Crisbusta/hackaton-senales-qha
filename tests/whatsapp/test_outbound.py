import asyncio
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.core.config import Settings, get_settings
from app.main import create_app
from app.modules.whatsapp import outbound
from app.modules.whatsapp.outbound import AsyncReplyDispatcher, TwilioRestSender, build_dispatcher
from app.modules.whatsapp.schemas import IncomingMessage
from app.modules.whatsapp.service import FALLBACK_REPLY
from app.shared.exceptions import IntegrationError

OURS = "whatsapp:+14155238886"
USER = "whatsapp:+56912345678"


class FakeSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[dict] = []
        self.error = error

    async def send(self, to: str, from_: str, body: str) -> None:
        if self.error:
            raise self.error
        self.sent.append({"to": to, "from_": from_, "body": body})


class Handler:
    def __init__(self, reply="ok", error: Exception | None = None, delay=0.0) -> None:
        self.reply, self.error, self.delay = reply, error, delay
        self.active = 0
        self.max_active = 0
        self.order: list[str] = []

    async def handle(self, message: IncomingMessage):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(self.delay)
        self.order.append(message.body)
        self.active -= 1
        if self.error:
            raise self.error
        return self.reply


def msg(body="Hola", sid="SM1", user=USER, to=OURS) -> IncomingMessage:
    return IncomingMessage(message_sid=sid, from_number=user, to_number=to, body=body)


def dispatch(dispatcher, *messages):
    async def run():
        await asyncio.gather(*(dispatcher.dispatch(m, "ctx") for m in messages))

    asyncio.run(run())


def test_reply_is_sent_back_from_our_number() -> None:
    sender = FakeSender()

    dispatch(AsyncReplyDispatcher(Handler("Hola, Patricia"), sender), msg())

    assert sender.sent == [{"to": USER, "from_": OURS, "body": "Hola, Patricia"}]


def test_nothing_is_sent_when_there_is_no_reply() -> None:
    sender = FakeSender()

    dispatch(AsyncReplyDispatcher(Handler(None), sender), msg())

    assert sender.sent == []


def test_handler_errors_send_the_friendly_text() -> None:
    sender = FakeSender()

    dispatch(AsyncReplyDispatcher(Handler(error=RuntimeError("boom")), sender), msg())

    assert sender.sent[0]["body"] == FALLBACK_REPLY


def test_send_failures_do_not_raise() -> None:
    dispatch(AsyncReplyDispatcher(Handler(), FakeSender(IntegrationError("down"))), msg())


def test_without_our_number_nothing_is_sent() -> None:
    sender = FakeSender()

    dispatch(AsyncReplyDispatcher(Handler(), sender), msg(to=""))

    assert sender.sent == []


def test_messages_from_the_same_user_are_processed_in_order() -> None:
    handler = Handler(delay=0.05)

    dispatch(
        AsyncReplyDispatcher(handler, FakeSender()),
        msg("uno", "SM1"),
        msg("dos", "SM2"),
        msg("tres", "SM3"),
    )

    assert handler.max_active == 1
    assert handler.order == ["uno", "dos", "tres"]


def test_different_users_run_concurrently() -> None:
    handler = Handler(delay=0.05)

    dispatch(
        AsyncReplyDispatcher(handler, FakeSender()),
        msg(sid="SM1", user="whatsapp:+56900000001"),
        msg(sid="SM2", user="whatsapp:+56900000002"),
    )

    assert handler.max_active == 2


def test_rest_sender_requires_credentials() -> None:
    with pytest.raises(IntegrationError):
        asyncio.run(TwilioRestSender("", "token").send(USER, OURS, "x"))


def test_rest_sender_uses_twilio_messages_api(monkeypatch) -> None:
    created: list[dict] = []

    class FakeClient:
        def __init__(self, sid, token) -> None:
            self.messages = self

        def create(self, **kwargs) -> None:
            created.append(kwargs)

    monkeypatch.setattr(outbound, "Client", FakeClient)

    asyncio.run(TwilioRestSender("AC1", "token").send(USER, OURS, "Hola"))

    assert created == [{"to": USER, "from_": OURS, "body": "Hola"}]


def test_dispatcher_only_in_async_mode() -> None:
    assert build_dispatcher(Settings(_env_file=None), Handler()) is None
    assert build_dispatcher(Settings(_env_file=None, reply_mode="async"), Handler()) is not None


def test_webhook_answers_immediately_and_replies_out_of_band() -> None:
    token, base, path = "test-token", "https://contigo.example.com", "/webhooks/whatsapp"
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, twilio_auth_token=token, public_base_url=base
    )
    sender = FakeSender()
    app.state.reply_dispatcher = AsyncReplyDispatcher(app.state.message_handler, sender)
    params = {"MessageSid": "SMx", "From": USER, "To": OURS, "Body": "Hola", "NumMedia": "0"}
    signature = RequestValidator(token).compute_signature(base + path, params)

    response = TestClient(app).post(path, data=params, headers={"X-Twilio-Signature": signature})

    assert response.status_code == 200
    assert ET.fromstring(response.text).findall("Message") == []  # TwiML vacío
    assert len(sender.sent) == 1  # la respuesta salió por la API REST
    assert sender.sent[0]["to"] == USER and sender.sent[0]["from_"] == OURS
