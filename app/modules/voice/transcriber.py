"""Transcripción local de notas de voz con faster-whisper (MIT), solo CPU.

El audio se decodifica en memoria con PyAV y se le pasa a Whisper como arreglo
numpy: el `decode_audio` de faster-whisper no funciona con PyAV 19
(`open() got an unexpected keyword argument 'metadata_errors'`).

Nada sale del servidor: el modelo se carga desde `VOICE_MODEL_DIR` (horneado en
la imagen Docker). Los logs registran duración y tiempo, nunca el texto.
"""

import asyncio
import io
import logging
import threading
import time
from typing import Any, Protocol

import av
import numpy as np

from app.core.config import Settings
from app.modules.voice.schemas import (
    AudioDecodeError,
    AudioTooLargeError,
    AudioTooLongError,
    Transcript,
)

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
# Una nota de 60 s en Opus pesa ~100-500 KB; 5 MB deja margen para AMR/WAV cortos.
MAX_AUDIO_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_SECONDS = 60.0


class Transcriber(Protocol):
    async def transcribe(self, audio_bytes: bytes) -> Transcript: ...


def decode(data: bytes, max_seconds: float = DEFAULT_MAX_SECONDS) -> np.ndarray:
    """Audio en memoria (OGG/Opus, MP3, M4A, AMR, WAV) → float32 mono a 16 kHz.

    Corta apenas el audio supera `max_seconds`, sin decodificar el resto.
    """
    max_samples = int(max_seconds * SAMPLE_RATE)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks: list[np.ndarray] = []
    total = 0

    def add(frames) -> None:
        nonlocal total
        for out in frames:
            samples = out.to_ndarray().reshape(-1)
            total += samples.size
            if total > max_samples:
                raise AudioTooLongError(f"Audio longer than {max_seconds:.0f}s")
            chunks.append(samples)

    try:
        with av.open(io.BytesIO(data)) as container:
            if not container.streams.audio:
                raise AudioDecodeError("No audio stream")
            for frame in container.decode(audio=0):
                add(resampler.resample(frame))
            add(resampler.resample(None))
    except AudioTooLongError:
        raise
    except (av.FFmpegError, ValueError, IndexError) as exc:
        raise AudioDecodeError(f"Undecodable audio: {type(exc).__name__}") from exc
    if not chunks:
        raise AudioDecodeError("Empty audio")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


class LocalWhisperTranscriber:
    """Whisper local. El modelo se carga una sola vez (al primer uso o en `warm_up`)."""

    def __init__(
        self,
        model: str = "small",
        model_dir: str = "",
        max_seconds: float = DEFAULT_MAX_SECONDS,
        max_bytes: int = MAX_AUDIO_BYTES,
        cpu_threads: int = 0,
        language: str = "es",
    ) -> None:
        # Con `model_dir` se carga desde disco y nunca se consulta Hugging Face.
        self._model_ref = model_dir or model
        self._local_only = bool(model_dir)
        self._max_seconds = max_seconds
        self._max_bytes = max_bytes
        self._cpu_threads = cpu_threads
        self._language = language
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def _load(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from faster_whisper import WhisperModel  # import pesado: diferido

                    started = time.monotonic()
                    self._model = WhisperModel(
                        self._model_ref,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=self._cpu_threads,
                        local_files_only=self._local_only,
                    )
                    logger.info("Whisper model loaded in %.1fs", time.monotonic() - started)
        return self._model

    def warm_up(self) -> None:
        """Carga el modelo antes del primer audio (para que no cuente en su timeout)."""
        self._load()

    def _transcribe_sync(self, audio_bytes: bytes) -> Transcript:
        started = time.monotonic()
        if len(audio_bytes) > self._max_bytes:
            raise AudioTooLargeError(f"Audio larger than {self._max_bytes} bytes")
        samples = decode(audio_bytes, self._max_seconds)
        duration = samples.size / SAMPLE_RATE
        segments, _ = self._load().transcribe(
            samples, language=self._language, beam_size=1, vad_filter=True
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        elapsed = time.monotonic() - started
        logger.info("Voice note transcribed duration=%.1fs elapsed=%.2fs", duration, elapsed)
        return Transcript(text=text, duration_s=duration, elapsed_s=elapsed)

    async def transcribe(self, audio_bytes: bytes) -> Transcript:
        return await asyncio.to_thread(self._transcribe_sync, audio_bytes)


def build_transcriber(settings: Settings) -> LocalWhisperTranscriber | None:
    """None si las notas de voz están apagadas (el audio sigue el flujo de siempre)."""
    if not settings.voice_enabled:
        return None
    return LocalWhisperTranscriber(
        model=settings.voice_model,
        model_dir=settings.voice_model_dir,
        max_seconds=settings.voice_max_seconds,
        cpu_threads=settings.voice_cpu_threads,
    )
