import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from twilio.request_validator import RequestValidator

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def build_signed_url(request: Request, public_base_url: str) -> str:
    """Reconstruye la URL exacta con la que Twilio firmó la solicitud.

    Detrás de un proxy (Railway, ngrok) la app recibe http://host-interno/...,
    pero Twilio firmó https://dominio-publico/..., por eso se usa PUBLIC_BASE_URL.
    """
    if not public_base_url:
        return str(request.url)
    url = public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    return url


async def verify_twilio_signature(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    """Dependencia que valida X-Twilio-Signature y devuelve los parámetros del form.

    Falla cerrado: sin token configurado, se rechaza toda solicitud.
    """
    form = await request.form()
    params = {key: value for key, value in form.items() if isinstance(value, str)}

    if not settings.twilio_validate_signature:
        return params

    if not settings.twilio_auth_token:
        logger.error("TWILIO_AUTH_TOKEN is not configured; rejecting webhook request")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(settings.twilio_auth_token)
    url = build_signed_url(request, settings.public_base_url)

    if not signature or not validator.validate(url, params, signature):
        logger.warning("Rejected webhook with invalid Twilio signature path=%s", request.url.path)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    return params
