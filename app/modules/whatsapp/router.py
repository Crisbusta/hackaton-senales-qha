import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status

from app.core.logging import mask_phone
from app.modules.whatsapp.outbound import handle_safely
from app.modules.whatsapp.security import verify_twilio_signature
from app.modules.whatsapp.service import (
    MessageHandler,
    get_message_handler,
    parse_incoming,
    to_twiml,
)
from app.shared.exceptions import InvalidRequestError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])


@router.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background: BackgroundTasks,
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

    # REPLY_MODE=async: se responde al instante y el turno sigue en segundo plano
    # (la respuesta sale por la API REST de Twilio, sin el límite de ~15 s).
    dispatcher = getattr(request.app.state, "reply_dispatcher", None)
    if dispatcher is not None:
        background.add_task(dispatcher.dispatch, message, ctx)
        return Response(content=to_twiml(None), media_type="application/xml")

    reply = await handle_safely(handler, message, ctx)
    return Response(content=to_twiml(reply), media_type="application/xml")
