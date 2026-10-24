"""Notas de voz: descargar, transcribir localmente y seguir el turno con el texto.

Una nota de voz sin texto se descarga desde Twilio, se transcribe en el servidor
(`voice.Transcriber`) y el turno sigue como si ella lo hubiera escrito: prefiltro,
Escalera y simulador funcionan igual. Si el audio trae texto (WhatsApp no lo
permite, pero la API sí), manda el texto y el audio se ignora.

Si algo falla (descarga, audio largo, timeout, transcripción vacía) se responde
con un texto fijo, sin llamar al agente.

Un monto dicho por voz se puede oír mal ("cien mil" / "cien"): si la transcripción
pediría una simulación, primero se pregunta "¿Quieres que pruebe con $100.000?" y
se guarda la transcripción. Si el turno siguiente es un "sí"/"dale", se procesa esa
transcripción (y ahí se simula); cualquier otra respuesta descarta la pendiente.
"""

import asyncio
import logging
import unicodedata
from dataclasses import dataclass

from app.modules.agent.interface import ConversationMode
from app.modules.conversation import simulation
from app.modules.conversation.schemas import ConversationState
from app.modules.voice.schemas import AudioTooLongError, VoiceError
from app.modules.voice.transcriber import MAX_AUDIO_BYTES, Transcriber
from app.modules.whatsapp.media import MediaFetcher
from app.modules.whatsapp.schemas import IncomingMedia, IncomingMessage
from app.shared.exceptions import IntegrationError, InvalidRequestError

logger = logging.getLogger(__name__)

AUDIO_CONTENT_TYPES = frozenset(
    {
        "audio/ogg",
        "audio/opus",
        "audio/mpeg",
        "audio/mp4",
        "audio/amr",
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
    }
)

# Textos al usuario. Pendientes de validación de tono por C.
UNHEARD_REPLY = "No alcancé a escuchar bien tu audio. ¿Me lo puedes escribir?"
TOO_LONG_REPLY = (
    "Tu audio es un poco largo para mí. ¿Me lo puedes contar en uno más corto o escribir?"
)
CONFIRM_PAYMENT_REPLY = "Te entendí {monto}. ¿Quieres que pruebe con {monto}?"
CONFIRM_INSTALLMENT_REPLY = "Te entendí {monto} en {cuotas} cuotas. ¿Quieres que lo calcule?"

# Cómo queda el turno de voz en el historial (lo lee el agente en recent_history).
HISTORY_PREFIX = "(audio) "
UNHEARD_HISTORY = "(envió un audio que no se pudo escuchar)"

_AFFIRMATIVE = frozenset(
    {
        "si",
        "dale",
        "ya",
        "ok",
        "okay",
        "oka",
        "bueno",
        "claro",
        "confirmo",
        "correcto",
        "exacto",
        "eso",
        "perfecto",
        "sipo",
        "obvio",
        "porfa",
    }
)
_NEGATIVE = frozenset({"no", "nop", "nada", "tampoco"})


@dataclass(frozen=True)
class VoiceResult:
    text: str | None  # la transcripción, o None si falló
    reply: str  # texto fijo si falló ("" si no)
    outcome: str  # "ok" | "download_failed" | "too_long" | "unheard" | "timeout"


def audio_attachment(message: IncomingMessage) -> IncomingMedia | None:
    """El primer adjunto de audio del mensaje, o None."""
    for media in message.media:
        if media.content_type.split(";")[0].strip().lower() in AUDIO_CONTENT_TYPES:
            return media
    return None


def history_entry(transcript: str) -> str:
    return HISTORY_PREFIX + transcript


async def transcribe_note(
    fetcher: MediaFetcher, transcriber: Transcriber, url: str, timeout_s: float, ctx: str
) -> VoiceResult:
    """Descarga + transcripción con un solo plazo. Nunca registra la transcripción."""

    async def run() -> str:
        audio = await fetcher.fetch(url, MAX_AUDIO_BYTES)
        return (await transcriber.transcribe(audio)).text.strip()

    try:
        text = await asyncio.wait_for(run(), timeout_s)
    except TimeoutError:
        logger.warning("Voice note timed out after %.1fs %s", timeout_s, ctx)
        return VoiceResult(None, UNHEARD_REPLY, "timeout")
    except AudioTooLongError:
        logger.warning("Voice note too long %s", ctx)
        return VoiceResult(None, TOO_LONG_REPLY, "too_long")
    except (IntegrationError, InvalidRequestError) as exc:
        logger.warning("Voice note download failed %s: %s", ctx, type(exc).__name__)
        return VoiceResult(None, UNHEARD_REPLY, "download_failed")
    except VoiceError as exc:
        logger.warning("Voice note unreadable %s: %s", ctx, type(exc).__name__)
        return VoiceResult(None, UNHEARD_REPLY, "unheard")
    except Exception:
        logger.exception("Voice note transcription failed %s", ctx)
        return VoiceResult(None, UNHEARD_REPLY, "unheard")
    if not text:
        logger.info("Voice note with empty transcript %s", ctx)
        return VoiceResult(None, UNHEARD_REPLY, "unheard")
    logger.info("Voice note ok %s chars=%d", ctx, len(text))
    return VoiceResult(text, "", "ok")


def _clp(amount: int) -> str:
    return f"${amount:,}".replace(",", ".")


def confirmation_question(state: ConversationState, transcript: str) -> str | None:
    """La pregunta de confirmación si la transcripción dispararía el simulador; si no, None.

    Se prueba `simulation.plan_turn` sobre una copia del estado: se confirma
    exactamente lo que el simulador haría, sin duplicar sus reglas.
    """
    probe = state.model_copy(deep=True)
    simulation.plan_turn(probe, transcript)
    payment = simulation.payment_amount(transcript)
    if (
        payment
        and probe.mode == ConversationMode.AMOUNT_SIMULATION
        and probe.chosen_payment == payment
    ):
        return CONFIRM_PAYMENT_REPLY.format(monto=_clp(payment))
    purchase = simulation.installment_purchase(transcript)
    if (
        purchase
        and probe.mode == ConversationMode.INSTALLMENT_PAUSE
        and probe.installment_calc_requested
    ):
        amount, count = purchase
        return CONFIRM_INSTALLMENT_REPLY.format(monto=_clp(amount), cuotas=count)
    return None


def _words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    cleaned = "".join(c if c.isalnum() else " " for c in plain)
    return cleaned.split()


def is_affirmative(text: str) -> bool:
    """ "sí", "Sí, dale.", "ya po", "ok porfa". Una negación en cualquier parte lo anula."""
    words = _words(text)
    if not words or len(words) > 5 or any(w in _NEGATIVE for w in words):
        return False
    return words[0] in _AFFIRMATIVE


def take_confirmation(state: ConversationState, body: str) -> str | None:
    """Si había un monto por confirmar y ella dijo que sí, la transcripción original.

    La pendiente vale solo para el turno siguiente: se descarta siempre.
    """
    pending, state.pending_voice_text = state.pending_voice_text, None
    if pending and is_affirmative(body):
        return pending
    return None
