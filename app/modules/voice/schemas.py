from pydantic import BaseModel


class Transcript(BaseModel):
    """Resultado de transcribir una nota de voz. `text` nunca va a los logs."""

    text: str
    duration_s: float
    elapsed_s: float


class VoiceError(Exception):
    """El audio no se pudo transcribir (base de los errores de este módulo)."""


class AudioTooLargeError(VoiceError):
    """El archivo supera el tamaño permitido."""


class AudioTooLongError(VoiceError):
    """El audio dura más que VOICE_MAX_SECONDS."""


class AudioDecodeError(VoiceError):
    """El archivo no es un audio que se pueda decodificar."""
