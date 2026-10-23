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

from openai import OpenAI, OpenAIError

from app.core.config import get_settings
from app.shared.exceptions import IntegrationError

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

# Modelos que rechazan `temperature`. Se aprende en la primera llamada y no
# se vuelve a pagar: sin esto, cada turno gasta un 400 antes del 200, que
# medido son 300-800 ms de los 15 s que da Twilio.
_SIN_TEMPERATURE: set[str] = set()


def _get_client() -> OpenAI:
    """Cliente con timeout corto y sin reintentos internos.

    Los defaults del SDK son 600 s y 2 reintentos: con eso, un turno lento
    deja a Twilio sin respuesta (corta a los 15 s) y Patricia no recibe
    nada. Los reintentos los decide este módulo, que sabe cuántos quedan
    dentro del presupuesto: uno al modelo de respaldo y, más arriba, uno de
    validación.
    """
    global _client
    if _client is None:
        settings = get_settings()
        _client = OpenAI(
            api_key=settings.openai_api_key or None,
            timeout=settings.openai_timeout_s,
            max_retries=0,
        )
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
    }
    if model not in _SIN_TEMPERATURE:
        kwargs["temperature"] = temperature

    try:
        return _get_client().chat.completions.create(**kwargs)
    except Exception as exc:
        # Los modelos de razonamiento solo aceptan temperature=1 (el
        # default) y rechazan el parámetro. Se reintenta sin él y se anota
        # el modelo, para que el resto de la conversación no repita el 400.
        if "temperature" not in str(exc) or "temperature" not in kwargs:
            raise
        logger.info("%s rechaza temperature; se omite de aqui en adelante", model)
        _SIN_TEMPERATURE.add(model)
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
    except OpenAIError as exc:
        restante = settings.agent_turn_budget_s - (time.monotonic() - started)
        if restante < settings.openai_timeout_s:
            # Intentar el respaldo dejaria el turno fuera del presupuesto y
            # Twilio cortaria antes de que llegue. Mejor fallar ahora: el
            # turno responde con su texto de respaldo, que no necesita al
            # modelo.
            raise IntegrationError(
                f"no budget left for the fallback model after {model}"
            ) from exc
        logger.warning("modelo principal %s fallo (%s); usando respaldo", model, exc)
        model = settings.openai_fallback_model
        try:
            response = _call_one(model, messages, settings.openai_temperature)
        except OpenAIError as fallback_exc:
            # Los dos modelos fallaron: es un fallo de integración, no un
            # error interno. El webhook ya responde con su texto de
            # respaldo; esto hace que el log lo diga bien.
            raise IntegrationError(
                f"both LLM models failed ({settings.openai_model}, {model})"
            ) from fallback_exc

    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        output = json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, TypeError, AttributeError, IndexError) as exc:
        # El structured output no deberia dejar pasar esto, pero si el
        # proveedor devuelve algo que no es JSON, es su problema, no un bug
        # de este modulo.
        raise IntegrationError(f"malformed response from {model}") from exc
    metadata = {
        "nombre": model,
        "temp": settings.openai_temperature,
        "latencia_ms": latency_ms,
        "tokens_in": response.usage.prompt_tokens,
        "tokens_out": response.usage.completion_tokens,
    }
    return output, metadata
