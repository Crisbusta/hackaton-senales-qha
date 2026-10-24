"""Descarga de adjuntos de WhatsApp desde Twilio (MediaUrl).

Twilio exige autenticación (Account SID + Auth Token) para bajar un adjunto.
Solo se aceptan URLs de la API de Twilio (defensa contra SSRF aunque la
solicitud venga firmada), todo se descarga en memoria y con tope de tamaño.
"""

import asyncio
from typing import Protocol

import requests

from app.core.config import Settings
from app.shared.exceptions import IntegrationError, InvalidRequestError

ALLOWED_MEDIA_PREFIX = "https://api.twilio.com/"
_CHUNK_BYTES = 64 * 1024


class MediaTooLargeError(InvalidRequestError):
    """El adjunto supera el tamaño permitido."""


class MediaFetcher(Protocol):
    async def fetch(self, url: str, max_bytes: int) -> bytes: ...


class TwilioMediaFetcher:
    def __init__(self, account_sid: str, auth_token: str, timeout_s: float = 4.0) -> None:
        self._auth = (account_sid, auth_token)
        self._timeout_s = timeout_s

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"  # nunca exponer credenciales

    async def fetch(self, url: str, max_bytes: int) -> bytes:
        if not url.startswith(ALLOWED_MEDIA_PREFIX):
            raise InvalidRequestError("Media URL is not a Twilio API URL")
        if not all(self._auth):
            raise IntegrationError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not configured")
        return await asyncio.to_thread(self._download, url, max_bytes)

    def _download(self, url: str, max_bytes: int) -> bytes:
        try:
            # requests no reenvía la autenticación al redirigir a otro host (el CDN de Twilio).
            with requests.get(url, auth=self._auth, timeout=self._timeout_s, stream=True) as r:
                r.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in r.iter_content(_CHUNK_BYTES):
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaTooLargeError(f"Media larger than {max_bytes} bytes")
                    chunks.append(chunk)
        except requests.RequestException as exc:
            raise IntegrationError(f"Media download failed: {type(exc).__name__}") from exc
        return b"".join(chunks)


def build_media_fetcher(settings: Settings) -> MediaFetcher:
    return TwilioMediaFetcher(settings.twilio_account_sid, settings.twilio_auth_token)
