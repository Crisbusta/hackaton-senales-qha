"""Llamada al proveedor LLM. Nada más vive acá.

El resto del módulo no sabe qué proveedor es: `service.py` recibe una
función con esta firma y la llama. Eso es lo que permite probar el flujo
completo sin red (ver tests/agent/test_service.py).

El modelo solo etiqueta y redacta: este archivo no decide ninguna
transición de estado.
"""

import json
import logging
import time

from openai import OpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Structured output: etiqueta y mensaje en campos separados (capa 2 de las
# cuatro de T05). Si el modelo devolviera un solo string, habría que
# parsearlo y ahí se cuelan las alucinaciones.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "etiqueta": {"type": "string"},
        "esencial": {"type": ["boolean", "null"]},
        "monto_mencionado": {"type": ["number", "null"]},
        "mensaje": {"type": "string"},
    },
    "required": ["etiqueta", "esencial", "monto_mencionado", "mensaje"],
    "additionalProperties": False,
}

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        _client = OpenAI(api_key=settings.openai_api_key or None)
    return _client


def _call_one(model: str, messages: list[dict], temperature: float):
    kwargs = {
        "model": model,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "contigo_turn",
                "schema": RESPONSE_SCHEMA,
                "strict": True,
            },
        },
        "temperature": temperature,
    }
    try:
        return _get_client().chat.completions.create(**kwargs)
    except Exception as exc:
        # Los modelos de razonamiento solo aceptan temperature=1 (el
        # default) y rechazan el parámetro. Se reintenta sin él.
        if "temperature" not in str(exc):
            raise
        kwargs.pop("temperature")
        return _get_client().chat.completions.create(**kwargs)


def call_model(static_prompt: str, context: str, correction: str | None = None):
    """-> (dict con los cuatro campos, metadata del turno).

    Si el modelo principal falla, se reintenta una vez con el de respaldo:
    los dos empataron en precisión (15/16), así que cambiar no degrada la
    clasificación.
    """
    settings = get_settings()
    messages = [
        {"role": "system", "content": static_prompt},
        {"role": "user", "content": context},
    ]
    if correction:
        messages.append({"role": "user", "content": correction})

    started = time.monotonic()
    model = settings.openai_model
    try:
        response = _call_one(model, messages, settings.openai_temperature)
    except Exception as exc:
        logger.warning("modelo principal %s fallo (%s); usando respaldo", model, exc)
        model = settings.openai_fallback_model
        response = _call_one(model, messages, settings.openai_temperature)

    latency_ms = int((time.monotonic() - started) * 1000)
    output = json.loads(response.choices[0].message.content)
    metadata = {
        "nombre": model,
        "temp": settings.openai_temperature,
        "latencia_ms": latency_ms,
        "tokens_in": response.usage.prompt_tokens,
        "tokens_out": response.usage.completion_tokens,
    }
    return output, metadata
