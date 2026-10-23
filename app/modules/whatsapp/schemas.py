from pydantic import BaseModel, Field


class IncomingMedia(BaseModel):
    url: str
    content_type: str = ""


class IncomingMessage(BaseModel):
    """Mensaje entrante de WhatsApp normalizado desde el webhook de Twilio."""

    message_sid: str = Field(min_length=1)
    # Formato Twilio: "whatsapp:+56912345678"
    from_number: str = Field(min_length=1)
    body: str = ""
    num_media: int = Field(default=0, ge=0)
    media: list[IncomingMedia] = Field(default_factory=list)

    @property
    def user_id(self) -> str:
        """Identificador del usuario: el número en E.164, sin prefijo de canal."""
        return self.from_number.removeprefix("whatsapp:")
