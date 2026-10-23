import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.logging import mask_phone
from app.modules.whatsapp.security import verify_twilio_signature
from app.modules.whatsapp.service import (
    FALLBACK_REPLY,
    MessageHandler,
    get_message_handler,
    parse_incoming,
    to_twiml,
)
from app.shared.exceptions import IntegrationError, InvalidRequestError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])


@router.post("/whatsapp")
async def whatsapp_webhook(
    params: Annotated[dict[str, str], Depends(verify_twilio_signature)],
    handler: Annotated[MessageHandler, Depends(get_message_handler)],
) -> Response:
    try:
        message = parse_incoming(params)
    except InvalidRequestError as exc:
        logger.warning("Invalid webhook payload: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload"
        ) from None

    # MessageSid funciona como id de correlación. Nunca se registra el Body.
    ctx = f"sid={message.message_sid} from={mask_phone(message.user_id)}"
    logger.info("Incoming WhatsApp message %s media=%d", ctx, message.num_media)

    try:
        reply = await handler.handle(message)
    except IntegrationError:
        logger.error("Integration error handling message %s", ctx, exc_info=True)
        reply = FALLBACK_REPLY
    except Exception:
        logger.exception("Unexpected error handling message %s", ctx)
        reply = FALLBACK_REPLY

    return Response(content=to_twiml(reply), media_type="application/xml")
