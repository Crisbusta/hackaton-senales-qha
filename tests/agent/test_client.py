"""El unico archivo que habla con el proveedor. Lo que se prueba aca es el
contrato de fallas, no la red: un turno que no responde deja a Patricia sin
nada, porque Twilio corta el webhook a los 15 s.
"""

import json

import pytest
from openai import APITimeoutError

from app.core.config import Settings
from app.modules.agent import client
from app.shared.exceptions import IntegrationError


class FakeResponse:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})})]
        self.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})


def _timeout(*args, **kwargs):
    raise APITimeoutError(request=None)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    ajustes = Settings(openai_api_key="test", openai_timeout_s=6.0)
    monkeypatch.setattr(client, "get_settings", lambda: ajustes)
    return ajustes


def test_the_fallback_model_gets_one_try(monkeypatch):
    usados = []

    def falla_el_principal(model, messages, temperature):
        usados.append(model)
        if len(usados) == 1:
            raise APITimeoutError(request=None)
        return FakeResponse(json.dumps(
            {"etiqueta": "cierre", "esencial": None,
             "monto_mencionado": None, "mensaje": "ok"}))

    monkeypatch.setattr(client, "_call_one", falla_el_principal)
    salida, metadata = client.call_model("sys", "ctx")

    assert usados == ["gpt-6.1-sol", "gpt-6-luna"]
    assert metadata["nombre"] == "gpt-6-luna"  # la traza dice cual respondio
    assert salida["etiqueta"] == "cierre"


def test_both_models_failing_is_an_integration_error(monkeypatch):
    """El webhook responde con su texto de respaldo igual; esto hace que el
    log lo diga como fallo de integracion y no como error interno."""
    monkeypatch.setattr(client, "_call_one", _timeout)

    with pytest.raises(IntegrationError):
        client.call_model("sys", "ctx")


def test_a_response_that_is_not_json_is_an_integration_error(monkeypatch):
    monkeypatch.setattr(
        client, "_call_one", lambda *a, **k: FakeResponse("no soy json"))

    with pytest.raises(IntegrationError):
        client.call_model("sys", "ctx")


def test_the_client_has_a_short_timeout_and_no_internal_retries(monkeypatch):
    """Los defaults del SDK son 600 s y 2 reintentos: con eso Twilio corta
    antes de que el turno termine."""
    creados = {}

    def fake_openai(**kwargs):
        creados.update(kwargs)
        return object()

    monkeypatch.setattr(client, "OpenAI", fake_openai)
    monkeypatch.setattr(client, "_client", None)
    client._get_client()

    assert creados["timeout"] == 6.0
    assert creados["max_retries"] == 0
