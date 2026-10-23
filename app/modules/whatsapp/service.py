from collections.abc import Mapping
from typing import Protocol

from fastapi import Request
from pydantic import ValidationError
from twilio.twiml.messaging_response import MessagingResponse

from app.modules.whatsapp.schemas import IncomingMedia, IncomingMessage
from app.shared.exceptions import InvalidRequestError

FALLBACK_REPLY = "Perdona, tuve un problema para responderte. ¿Me escribes de nuevo en un momento?"


class MessageHandler(Protocol):
    """Lo que el módulo WhatsApp necesita para obtener una respuesta.

    Devuelve None cuando no corresponde responder (p. ej. mensaje duplicado).
    """

    async def handle(self, message: IncomingMessage) -> str | None: ...


def get_message_handler(request: Request) -> MessageHandler:
    """El handler concreto se registra en `app.state` al crear la app (main.py)."""
    return request.app.state.message_handler


def parse_incoming(params: Mapping[str, str]) -> IncomingMessage:
    """Normaliza los parámetros del webhook de Twilio."""
    try:
        num_media = int(params.get("NumMedia") or 0)
        media = [
            IncomingMedia(
                url=params[f"MediaUrl{i}"],
                content_type=params.get(f"MediaContentType{i}", ""),
            )
            for i in range(num_media)
            if f"MediaUrl{i}" in params
        ]
        return IncomingMessage(
            message_sid=params.get("MessageSid", ""),
            from_number=params.get("From", ""),
            body=params.get("Body", "").strip(),
            num_media=num_media,
            media=media,
        )
    except (ValueError, ValidationError) as exc:
        # No incluir el contenido del payload en el mensaje de error.
        raise InvalidRequestError("Malformed Twilio webhook payload") from exc


def to_twiml(text: str | None) -> str:
    """TwiML con un mensaje, o un <Response/> vacío (Twilio no envía nada)."""
    response = MessagingResponse()
    if text:
        response.message(text)
    return str(response)
