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

# Structured output, uno por paso (capa 2 de las cuatro de T05). Si el modelo
# devolviera un solo string habria que parsearlo, y ahi se cuelan las
# alucinaciones.
#
# El de clasificacion no tiene campo de prosa: esa llamada no redacta. El de
# redaccion no tiene etiqueta: la clasificacion ya ocurrio y pedirsela otra
# vez solo invita a contradecirse.
CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "etiqueta": {"type": "string"},
        "esencial": {"type": ["boolean", "null"]},
        "monto_mencionado": {"type": ["number", "null"]},
        "ritmo": {"type": ["string", "null"], "enum": ["mas_lento", "todo_de_una", None]},
    },
    "required": ["etiqueta", "esencial", "monto_mencionado", "ritmo"],
    "additionalProperties": False,
}

WRITE_SCHEMA = {
    "type": "object",
    "properties": {"mensaje": {"type": "string"}},
    "required": ["mensaje"],
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


# Lo minimo que vale la pena darle al modelo de respaldo. Por debajo de esto
# el turno se corta igual y la llamada solo gasta.
MIN_RESPALDO_S = 2.5

# El modelo de respaldo solo entra en la CLASIFICACION.
#
# Medido contra el proveedor real: en la redaccion, el principal se cortaba a
# los 7 s y el respaldo llevaba el turno a 9,7 s. Duplica el costo del paso mas
# caro justo cuando ya no queda tiempo, y el resultado igual llega tarde. Si la
# redaccion no alcanza, el texto de respaldo del modo sale al instante y es un
# texto que C valido.
#
# En la clasificacion si vale: es la llamada corta y sin etiqueta no hay turno
# que resolver.
RESPALDO_SOLO_EN_CLASIFICACION = True


def plazo(limite: float, restante: float | None) -> float:
    """El plazo real de una llamada: su limite, o lo que queda del turno.

    Las dos cosas tienen que acotarla. El limite evita que una llamada lenta
    se coma el turno; lo que queda evita que la suma de las dos se pase de la
    ventana de Twilio aunque cada una respete su limite.
    """
    if restante is None:
        return limite
    return max(min(limite, restante), 0.0)


def _call_one(model: str, messages: list[dict], temperature: float, schema=None,
              timeout: float | None = None):
    nombre = "contigo_classify" if schema is CLASSIFY_SCHEMA else "contigo_write"
    kwargs = {
        "model": model,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": nombre,
                "schema": schema or WRITE_SCHEMA,
                "strict": True,
            },
        },
    }
    if model not in _SIN_TEMPERATURE:
        kwargs["temperature"] = temperature

    if timeout is not None:
        kwargs["timeout"] = timeout

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


def call_model(static_prompt: str, context: str, correction: str | None = None,
               schema=None, model: str | None = None, restante: float | None = None):
    """-> (dict con los campos del esquema, metadata del turno).

    `schema` decide el paso: CLASSIFY_SCHEMA o WRITE_SCHEMA. `model` permite
    que la clasificación use un modelo distinto del de redacción (medido: el
    barato da 0,72 s pero 9 de 12, y su único error grave fue no aplicar la
    prioridad del rechazo, que el prompt ahora escribe como no negociable).

    Si el modelo principal falla, se reintenta una vez con el de respaldo.
    OJO: con los valores por defecto ese reintento es inalcanzable cuando el
    principal se cae por timeout; está documentado en CLAUDE.md.
    """
    settings = get_settings()
    messages = [
        {"role": "system", "content": static_prompt},
        {"role": "user", "content": context},
    ]
    if correction:
        messages.append({"role": "user", "content": correction})

    started = time.monotonic()
    es_clasificacion = schema is CLASSIFY_SCHEMA
    limite = (
        settings.openai_classify_timeout_s if es_clasificacion
        else settings.openai_timeout_s
    )
    model = model or settings.openai_model
    try:
        response = _call_one(
            model, messages, settings.openai_temperature, schema,
            timeout=plazo(limite, restante),
        )
    except OpenAIError as exc:
        if not es_clasificacion:
            # La redaccion no tiene segundo modelo: ver
            # RESPALDO_SOLO_EN_CLASIFICACION. Sale el texto de respaldo del modo.
            raise IntegrationError(f"write failed with {model}") from exc
        # El respaldo corre con lo que QUEDA del presupuesto, no con el timeout
        # completo. Pedirle los `openai_timeout_s` enteros lo volvia
        # inalcanzable justo en el caso mas comun: si el principal se cae por
        # timeout consume esos mismos segundos, asi que nunca sobraban. Medido:
        # con 9 de 11 s, el principal tenia que fallar en menos de 2 s, y su
        # propio timeout es de 9. Solo cubria los errores rapidos (401, 429).
        libre = settings.agent_turn_budget_s - (time.monotonic() - started)
        if restante is not None:
            libre = min(libre, restante - (time.monotonic() - started))
        if libre < MIN_RESPALDO_S:
            raise IntegrationError(
                f"no budget left for the fallback model after {model}"
            ) from exc
        logger.warning("modelo principal %s fallo (%s); usando respaldo con %.1fs",
                       model, exc, libre)
        model = settings.openai_fallback_model
        try:
            response = _call_one(
                model, messages, settings.openai_temperature, schema, timeout=libre
            )
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
