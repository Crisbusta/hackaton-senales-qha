import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch):
    """Los tests no leen el `.env` del desarrollador.

    Si lo leyeran, el resultado dependería de qué tenga cada uno en su
    máquina: con `AGENT_TRACE_ENABLED=true` en local, el test que verifica
    que la traza viene apagada falla sin que nada esté mal en el código.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
