import asyncio
import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from app.modules.whatsapp.schemas import IncomingMessage

PASSWORD = "s3cret-demo"
SECRET_BODY = "mi clave del banco es 998877"


def auth(password: str = PASSWORD) -> dict[str, str]:
    token = base64.b64encode(f"equipo:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def app() -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, dashboard_password=PASSWORD
    )
    return app


def talk(app: FastAPI, user: str, *bodies: str) -> None:
    for i, body in enumerate(bodies):
        message = IncomingMessage(
            message_sid=f"SM{user}{i}", from_number=f"whatsapp:{user}", body=body
        )
        asyncio.run(app.state.message_handler.handle(message))


def test_disabled_without_password() -> None:
    client = TestClient(create_app())  # dashboard_password vacío por defecto

    assert client.get("/dashboard").status_code == 404
    assert client.get("/dashboard/api/conversations").status_code == 404


def test_requires_the_password(app: FastAPI) -> None:
    client = TestClient(app)

    missing = client.get("/dashboard/api/conversations")
    wrong = client.get("/dashboard/api/conversations", headers=auth("otra"))

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"].startswith("Basic")
    assert wrong.status_code == 401


def test_page_is_served_without_cache(app: FastAPI) -> None:
    response = TestClient(app).get("/dashboard", headers=auth())

    assert response.status_code == 200
    assert "Contigo · Progreso" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_shows_progress_but_never_messages_or_full_phones(app: FastAPI) -> None:
    talk(app, "+56911112222", "Hola", SECRET_BODY, "Prefiero no verlo todavía")

    response = TestClient(app).get("/dashboard/api/conversations", headers=auth())

    assert response.status_code == 200
    assert SECRET_BODY not in response.text
    assert "998877" not in response.text
    assert "+56911112222" not in response.text
    [conversation] = response.json()["conversations"]
    assert conversation["user"] == "***2222"
    assert conversation["turns"] == 3
    assert conversation["mode"] == "PAUSA_RECHAZO"
    assert conversation["figures_hidden"] is True
    assert conversation["counters"]["pausas_respetadas"] == 1
    achieved = {m["key"] for m in conversation["milestones"] if m["at"]}
    assert {"primer_contacto", "pausa_respetada"} <= achieved
    assert conversation["timeline"][0]["mode_to"] == "PAUSA_RECHAZO"  # el más reciente primero


def test_lists_the_most_recent_conversation_first(app: FastAPI) -> None:
    talk(app, "+56900000001", "Hola")
    talk(app, "+56900000002", "Hola")

    users = [
        c["user"]
        for c in TestClient(app)
        .get("/dashboard/api/conversations", headers=auth())
        .json()["conversations"]
    ]

    assert users == ["***0002", "***0001"]
