"""Decodificación y límites del transcriptor local, con audio sintético (sin voz real).

Los tests que cargan el modelo Whisper se saltan salvo con VOICE_TEST_MODEL=1
(y el modelo ya descargado o VOICE_MODEL_DIR apuntando a él).
"""

import asyncio
import io
import os

import av
import numpy as np
import pytest

from app.core.config import Settings
from app.modules.voice.schemas import AudioDecodeError, AudioTooLargeError, AudioTooLongError
from app.modules.voice.transcriber import (
    SAMPLE_RATE,
    LocalWhisperTranscriber,
    build_transcriber,
    decode,
)


def make_audio(seconds: float, codec: str = "libopus", fmt: str = "ogg", rate: int = 48_000):
    """Un tono de 440 Hz codificado como lo manda WhatsApp (OGG/Opus, mono, 48 kHz)."""
    t = np.arange(int(seconds * rate)) / rate
    pcm = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format=fmt) as container:
        stream = container.add_stream(codec, rate=rate, layout="mono")
        frame_size = 960
        for start in range(0, pcm.size, frame_size):
            chunk = pcm[start : start + frame_size].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(chunk, format="s16", layout="mono")
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


def test_decode_ogg_opus_to_16k_mono_float():
    samples = decode(make_audio(1.0))
    assert samples.dtype == np.float32
    assert samples.ndim == 1
    assert abs(samples.size / SAMPLE_RATE - 1.0) < 0.1
    assert 0.1 < np.abs(samples).max() <= 1.0


def test_decode_wav():
    samples = decode(make_audio(0.5, codec="pcm_s16le", fmt="wav", rate=16_000))
    assert abs(samples.size / SAMPLE_RATE - 0.5) < 0.05


def test_decode_rejects_audio_longer_than_limit():
    with pytest.raises(AudioTooLongError):
        decode(make_audio(3.0), max_seconds=2)


@pytest.mark.parametrize("data", [b"", b"esto no es un audio", b"%PDF-1.7\n" + b"0" * 2000])
def test_decode_rejects_non_audio(data):
    with pytest.raises(AudioDecodeError):
        decode(data)


class ExplodingModel:
    def transcribe(self, *args, **kwargs):
        raise AssertionError("el modelo no debe correr si el audio no pasa los límites")


def test_transcriber_rejects_large_file_before_decoding():
    transcriber = LocalWhisperTranscriber(max_bytes=1000)
    transcriber._model = ExplodingModel()
    with pytest.raises(AudioTooLargeError):
        asyncio.run(transcriber.transcribe(b"0" * 1001))


def test_transcriber_rejects_long_audio_without_running_the_model():
    transcriber = LocalWhisperTranscriber(max_seconds=1)
    transcriber._model = ExplodingModel()
    with pytest.raises(AudioTooLongError):
        asyncio.run(transcriber.transcribe(make_audio(2.0)))


class FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def transcribe(self, samples, **kwargs):
        self.calls.append({"samples": samples, **kwargs})
        return iter([FakeSegment(" ¿Y si pago "), FakeSegment("100 mil? ")]), None


def test_transcriber_passes_decoded_samples_and_joins_segments(caplog):
    transcriber = LocalWhisperTranscriber()
    model = FakeModel()
    transcriber._model = model
    with caplog.at_level("INFO"):
        transcript = asyncio.run(transcriber.transcribe(make_audio(1.0)))
    assert transcript.text == "¿Y si pago 100 mil?"
    assert abs(transcript.duration_s - 1.0) < 0.1
    call = model.calls[0]
    assert isinstance(call["samples"], np.ndarray)
    assert call["language"] == "es" and call["beam_size"] == 1 and call["vad_filter"] is True
    # Nunca se registra lo que dijo.
    assert "100 mil" not in caplog.text and "pago" not in caplog.text


def test_build_transcriber_disabled_by_default():
    assert build_transcriber(Settings()) is None


def test_build_transcriber_does_not_load_the_model():
    transcriber = build_transcriber(Settings(voice_enabled=True, voice_model_dir="/no/existe"))
    assert isinstance(transcriber, LocalWhisperTranscriber)
    assert transcriber._model is None  # carga diferida


@pytest.mark.skipif(os.environ.get("VOICE_TEST_MODEL") != "1", reason="carga el modelo Whisper")
def test_real_model_on_a_tone_returns_quickly():
    transcriber = LocalWhisperTranscriber(
        model=os.environ.get("VOICE_MODEL", "small"),
        model_dir=os.environ.get("VOICE_MODEL_DIR", ""),
    )
    transcript = asyncio.run(transcriber.transcribe(make_audio(2.0)))
    assert transcript.duration_s > 1.5
    assert isinstance(transcript.text, str)
