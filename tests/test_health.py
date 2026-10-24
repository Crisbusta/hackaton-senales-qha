from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "contigo"}


# --- el presupuesto del turno no puede romperse en silencio -------------


def test_una_configuracion_sin_presupuesto_no_arranca():
    """Antes, una combinacion mala dejaba cada turno degradando al texto de
    respaldo sin ningun error: no habia forma de notarlo salvo leyendo las
    respuestas. Ahora es un fallo de arranque, que es donde se ve.
    """
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings

    # El turno son dos llamadas: 4 + 7 = 11 s, y el presupuesto es
    # AGENT_TIMEOUT_S menos el margen.
    assert Settings(_env_file=None).agent_turn_budget_s >= 11.0

    with pytest.raises(ValidationError, match="degrada al texto de respaldo"):
        Settings(_env_file=None, agent_timeout_s=8.0)


def test_los_dos_plazos_caben_en_la_ventana_de_twilio():
    """Twilio corta a los ~15 s. El peor caso del turno tiene que caber con
    holgura, porque despues hay que validar, registrar y armar el TwiML."""
    from app.core.config import Settings

    s = Settings(_env_file=None)

    # El techo de la redaccion es alto a proposito: `client.plazo()` lo acota
    # con lo que queda del turno, asi que el peor caso del turno es el
    # presupuesto, no la suma de los dos techos.
    assert s.openai_classify_timeout_s < s.agent_turn_budget_s
    assert s.agent_timeout_s <= 13.0
