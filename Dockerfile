FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencias primero: se cachean mientras no cambie el lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Modelo de voz horneado en la imagen (~480 MB): los contenedores nunca lo bajan de
# Hugging Face en runtime y el audio no sale del servidor.
ARG VOICE_MODEL=small
ENV VOICE_MODEL_DIR=/app/models/faster-whisper-${VOICE_MODEL} \
    HF_HUB_DISABLE_TELEMETRY=1
RUN /app/.venv/bin/python -c "import os, faster_whisper; faster_whisper.download_model('${VOICE_MODEL}', output_dir=os.environ['VOICE_MODEL_DIR'])" \
    && rm -rf /root/.cache/huggingface

COPY app ./app

RUN useradd --create-home --uid 10001 appuser
USER appuser

# Con el modelo en la imagen, las notas de voz vienen encendidas (VOICE_ENABLED=false
# en Railway las apaga). HF_HUB_OFFLINE: ni por error se consulta Hugging Face.
ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8000 \
    VOICE_ENABLED=true \
    VOICE_MODEL=${VOICE_MODEL} \
    HF_HUB_OFFLINE=1
EXPOSE 8000

# PORT lo define Railway. --proxy-headers: la app corre detrás del proxy HTTPS
# de Railway (la firma de Twilio igual se valida contra PUBLIC_BASE_URL).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
