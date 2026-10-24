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


# --- dobles del agente, para el turno en tres pasos ---------------------
#
# El contrato pasó de `process()` a `classify()` + `write()`, así que un doble
# ya no puede devolver "la respuesta del turno": devuelve una clasificación y
# una redacción. Vive acá porque lo usan los tests de conversation/ y los de
# end-to-end, y así la forma del doble es una sola.


class TwoStepAgent:
    """Agente de prueba: clasificaciones y redacciones preprogramadas.

    `turns` son pares (Classification, AgentReply). Si se agotan, clasifica
    como `tangente` y responde "ok": un test que no le importa el contenido no
    tiene que enumerar todos los turnos.
    """

    def __init__(self, *turns):
        from app.modules.agent.interface import AgentReply, Classification, Intent

        self._Classification = Classification
        self._AgentReply = AgentReply
        self._Intent = Intent
        self.turns = list(turns)
        self.classify_requests = []
        self.write_requests = []
        self._pending = None

    async def classify(self, request):
        self.classify_requests.append(request)
        if self.turns:
            clasificacion, respuesta = self.turns.pop(0)
            self._pending = respuesta
            return clasificacion
        self._pending = None
        return self._Classification(intent=self._Intent.OFF_TOPIC)

    async def write(self, request):
        self.write_requests.append(request)
        respuesta, self._pending = self._pending, None
        return respuesta if respuesta is not None else self._AgentReply(message="ok")

    @property
    def requests(self):
        """Los `WriteRequest` recibidos, uno por turno.

        Los tests del backend miran el modo final, las preferencias y los
        flags anti-bucle, y todo eso vive en el request de redacción: es el
        que lleva el turno ya resuelto. Para lo que llega al clasificador,
        `classify_requests`.
        """
        return self.write_requests

    # Atajos de lectura, para no repetir el índice en cada test.
    @property
    def last_classify(self):
        return self.classify_requests[-1]

    @property
    def last_write(self):
        return self.write_requests[-1]


def turno(mensaje="ok", intent=None, event=None, essential=None,
          mentioned_amount=None, pace=None, protected_text=False):
    """Un par (Classification, AgentReply) para `TwoStepAgent`."""
    from app.modules.agent.interface import AgentReply, Classification, Intent

    return (
        Classification(
            intent=intent or Intent.OFF_TOPIC,
            event=event,
            essential=essential,
            mentioned_amount=mentioned_amount,
            pace=pace,
        ),
        AgentReply(message=mensaje, protected_text=protected_text),
    )
