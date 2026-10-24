"""Respuestas fuera de banda (REPLY_MODE=async).

El webhook responde al instante con un TwiML vacío y el mensaje se procesa en
segundo plano; la respuesta se envía con la API REST de Twilio. Así el turno
deja de estar limitado por los ~15 s que Twilio espera al webhook.

Los mensajes de un mismo usuario se procesan en orden (un candado por usuario),
para que dos mensajes seguidos no pisen el estado de la conversación. El candado
es por proceso: alcanza con una réplica (ver railway.json).
"""

import asyncio
import logging
from collections import defaultdict
from typing import Protocol

from twilio.base.exceptions import TwilioException
from twilio.rest import Client

from app.core.config import Settings
from app.core.logging import mask_phone
from app.modules.whatsapp.schemas import IncomingMessage
from app.modules.whatsapp.service import FALLBACK_REPLY, MessageHandler
from app.shared.exceptions import IntegrationError

logger = logging.getLogger(__name__)


class MessageSender(Protocol):
    async def send(self, to: str, from_: str, body: str) -> None: ...


class TwilioRestSender:
    def __init__(self, account_sid: str, auth_token: str) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._client: Client | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"  # nunca exponer credenciales

    async def send(self, to: str, from_: str, body: str) -> None:
        if not (self._account_sid and self._auth_token):
            raise IntegrationError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not configured")
        await asyncio.to_thread(self._send, to, from_, body)

    def _send(self, to: str, from_: str, body: str) -> None:
        if self._client is None:
            self._client = Client(self._account_sid, self._auth_token)
        try:
            self._client.messages.create(to=to, from_=from_, body=body)
        except TwilioException as exc:
            raise IntegrationError(f"Twilio send failed: {type(exc).__name__}") from exc


async def handle_safely(handler: MessageHandler, message: IncomingMessage, ctx: str) -> str | None:
    """El handler nunca deja al usuario sin respuesta: un error se vuelve el texto amable."""
    try:
        return await handler.handle(message)
    except IntegrationError:
        logger.error("Integration error handling message %s", ctx, exc_info=True)
    except Exception:
        logger.exception("Unexpected error handling message %s", ctx)
    return FALLBACK_REPLY


class AsyncReplyDispatcher:
    def __init__(self, handler: MessageHandler, sender: MessageSender) -> None:
        self._handler = handler
        self._sender = sender
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def dispatch(self, message: IncomingMessage, ctx: str) -> None:
        async with self._locks[message.user_id]:
            reply = await handle_safely(self._handler, message, ctx)
            if not reply:
                return
            if not message.to_number:
                logger.error("Cannot reply without the destination number %s", ctx)
                return
            try:
                await self._sender.send(to=message.from_number, from_=message.to_number, body=reply)
            except IntegrationError:
                logger.error("Reply could not be sent %s", ctx, exc_info=True)
                return
            logger.info("Reply sent out of band %s to=%s", ctx, mask_phone(message.user_id))


def build_dispatcher(settings: Settings, handler: MessageHandler) -> AsyncReplyDispatcher | None:
    if settings.reply_mode != "async":
        return None
    return AsyncReplyDispatcher(
        handler, TwilioRestSender(settings.twilio_account_sid, settings.twilio_auth_token)
    )
