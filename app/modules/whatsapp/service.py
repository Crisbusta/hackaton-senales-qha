from collections.abc import Mapping
from typing import Protocol

from pydantic import ValidationError
from twilio.twiml.messaging_response import MessagingResponse

from app.modules.whatsapp.schemas import IncomingMedia, IncomingMessage
from app.shared.exceptions import InvalidRequestError

FALLBACK_REPLY = "Perdona, tuve un problema para responderte. ¿Me escribes de nuevo en un momento?"


class MessageHandler(Protocol):
    """Lo que el módulo WhatsApp necesita para obtener una respuesta.

    ConversationService implementará este protocolo en la integración end-to-end.
    """

    async def handle(self, message: IncomingMessage) -> str: ...


class AcknowledgeHandler:
    """Handler provisorio: confirma la recepción sin lógica conversacional."""

    async def handle(self, message: IncomingMessage) -> str:
        if message.num_media:
            return "Recibí tu archivo 📎. Pronto podré revisarlo contigo."
        return "Hola 👋 Recibí tu mensaje. Estoy aquí contigo."


def get_message_handler() -> MessageHandler:
    return AcknowledgeHandler()


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


def to_twiml(text: str) -> str:
    response = MessagingResponse()
    response.message(text)
    return str(response)
