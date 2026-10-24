"""El turno completo con un modelo simulado: sin red, sin costo, sin latencia.

Prueba lo que los chequeos individuales no pueden: que las piezas se
encadenen en el orden correcto y que la traducción desde/hacia el contrato
no pierda nada.
"""

import asyncio
import json
import pathlib

import pytest

from app.modules.agent import observability as obs
from app.modules.agent import service
from app.modules.agent.interface import (
    LIMIT_CRISIS_LINE,
    PREF_SHOW_FINANCIAL_FIGURES,
    Agent,
    Classification,
    ClassifyRequest,
    ConversationEvent,
    ConversationMessage,
    ConversationMode,
    FieldStatus,
    FinancialField,
    Intent,
    WriteRequest,
)
from app.modules.agent.service import LlmAgent, clasificar, redactar

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _financial_data(name="statement_complete.json"):
    """El fixture viene en el formato del documento; acá se traduce al
    contrato, que es como llegaría desde T07."""
    estado_a_status = {
        "confirmado": FieldStatus.CONFIRMED,
        "ausente": FieldStatus.ABSENT,
        "no confiable": FieldStatus.UNRELIABLE,
    }
    crudo = json.load(open(FIXTURES / name, encoding="utf-8"))
    return {
        nombre: FinancialField(
            value=campo["valor"], status=estado_a_status[campo["estado"]]
        )
        for nombre, campo in crudo.items()
        if isinstance(campo, dict) and "estado" in campo
    }


def _request(message, mode=ConversationMode.BALANCE_OVERVIEW, etiqueta=None, **kwargs):
    """Un `WriteRequest` como el que armaria el backend ya con el turno resuelto."""
    kwargs.setdefault("financial_data", _financial_data())
    kwargs.setdefault(
        "classification", Classification(intent=etiqueta or Intent.INFO_REQUEST)
    )
    return WriteRequest(user_id="demo-01", message=message, mode=mode, **kwargs)


def _classify_request(message, mode=ConversationMode.BALANCE_OVERVIEW, **kwargs):
    return ClassifyRequest(user_id="demo-01", message=message, mode=mode, **kwargs)


class FakeModel:
    """Devuelve respuestas en orden y cuenta las llamadas, para poder
    afirmar que el pre-filtro de verdad evita el viaje al proveedor."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.last_context = None
        self.last_correction = None

    def __call__(self, static_prompt, context, correction=None, schema=None,
                 model=None, restante=None):
        self.calls += 1
        self.last_context = context
        self.last_correction = correction
        self.last_schema = schema
        self.last_restante = restante
        output = self.responses.pop(0)
        metadata = {
            "nombre": "fake",
            "temp": 0.3,
            "latencia_ms": 1,
            "tokens_in": 10,
            "tokens_out": 10,
        }
        return output, metadata


def _output(mensaje, etiqueta=None, esencial=None, monto=None):
    """Lo que devuelve la llamada de REDACCION: un solo campo.

    `etiqueta` y los demas se ignoran: con el turno en dos pasos la
    clasificacion ya ocurrio y el redactor no la vuelve a emitir. Se aceptan
    para que los tests sigan leyendose igual.
    """
    return {"mensaje": mensaje}


def _clasificado(etiqueta="solicitud_informacion", esencial=None, monto=None, ritmo=None):
    """Lo que devuelve la llamada de CLASIFICACION."""
    return {
        "etiqueta": etiqueta,
        "esencial": esencial,
        "monto_mencionado": monto,
        "ritmo": ritmo,
    }


@pytest.fixture
def log_path(tmp_path):
    return str(tmp_path / "turns.jsonl")


# --- el flujo normal ---------------------------------------------------

def test_clean_turn_passes_on_first_try(log_path):
    fake = FakeModel([
        _output(
            "El pago minimo es $74.350 y el saldo es $1.064.000. "
            "Seguimos o lo dejamos aqui?",
            etiqueta="disposicion_explorar",
        )
    ])
    response = redactar(
        _request("ya, mandame que debo", can_offer_continue=True),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 1
    assert "74.350" in response.message
    # La etiqueta y el evento los emite el clasificador; el redactor solo
    # escribe, y `protected_text` en False dice que el texto es suyo.
    assert response.protected_text is False

    evento = clasificar(
        _classify_request("ya, mandame que debo"),
        call=FakeModel([_clasificado("disposicion_explorar")]),
        log_path=log_path,
    ).event
    assert evento is ConversationEvent.ACCEPTED_NEXT_STEP


def test_invented_figure_is_fixed_on_retry(log_path):
    fake = FakeModel([
        _output("Te cobran como $30.000 de interes."),
        _output("El pago minimo es $74.350."),
    ])
    response = redactar(
        _request("cuanto me cobran de interes"), call=fake, log_path=log_path
    )

    assert fake.calls == 2
    # La correccion NO le repite la cifra que alucino: el detalle del chequeo
    # dice "cifras no inyectadas: 30000", y devolverselo es ponerle el numero
    # prohibido otra vez delante.
    assert "30.000" not in fake.last_correction
    assert "30000" not in fake.last_correction
    assert "DATOS QUE PUEDES DECIR" in fake.last_correction
    assert "74.350" in response.message
    assert "30.000" not in response.message


def test_two_failures_fall_back_to_the_mode_text(log_path):
    fake = FakeModel([
        _output("Son $30.000."),
        _output("Son $99.999."),
    ])
    response = redactar(
        _request("cuanto me cobran", can_offer_continue=True),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 2
    assert "99.999" not in response.message
    assert "ordenarlo bien" in response.message


# --- seguridad ---------------------------------------------------------

def test_crisis_never_reaches_the_provider(log_path):
    """Capa 1: el pre-filtro clasifica la crisis explicita sin salir a la red."""
    fake = FakeModel([])  # si se llamara, revienta por cola vacia
    clasificacion = clasificar(
        _classify_request("ya no quiero vivir", mode=ConversationMode.SUPPORT),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 0
    assert clasificacion.intent == "angustia_fuerte"
    assert clasificacion.event is ConversationEvent.CRISIS_SUPPORT_NEEDED


def test_un_no_en_apoyo_se_clasifica_como_crisis_sin_llamar_al_modelo(log_path):
    """C10 (d): el texto de crisis termina preguntando si esta a salvo, asi
    que el turno siguiente responde a eso. Fuera de APOYO el mismo "no, no
    estoy bien" es cuidadora_agotada y lo decide el modelo."""
    fake = FakeModel([])  # si se llamara, revienta por cola vacia
    clasificacion = clasificar(
        _classify_request("no, no estoy bien", mode=ConversationMode.SUPPORT),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 0
    assert clasificacion.intent == "angustia_fuerte"
    assert clasificacion.event is ConversationEvent.CRISIS_SUPPORT_NEEDED


def test_fuera_de_apoyo_un_no_estoy_bien_lo_etiqueta_el_modelo(log_path):
    fake = FakeModel([_clasificado("cuidadora_agotada")])
    clasificacion = clasificar(
        _classify_request("no, no estoy bien", mode=ConversationMode.GREETING),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 1
    assert clasificacion.intent == "cuidadora_agotada"


def test_la_crisis_no_gasta_una_llamada_de_redaccion(log_path):
    """Capa 3: el texto fijo sale sin llamar al modelo.

    Antes se llamaba igual y la prosa se descartaba despues: una llamada
    entera, con su latencia y sus tokens, para tirar el resultado. Con la
    clasificacion ya hecha, el redactor corta antes de salir a la red.
    """
    fake = FakeModel([])
    respuesta = redactar(
        _request(
            "siento que ya no puedo mas con nada",
            etiqueta="angustia_fuerte",
            financial_data=_financial_data(),
        ),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 0
    assert respuesta.protected_text
    assert "74.350" not in respuesta.message
    assert "a salvo" in respuesta.message.lower()
    assert "*4141" in respuesta.message and "131" in respuesta.message


def test_la_primera_crisis_es_el_texto_que_pregunta_si_esta_a_salvo(log_path):
    """El modo que llega al redactor es el FINAL del turno, y una crisis
    siempre lo pone en APOYO. Mientras la condicion fue `modo == APOYO`, el
    texto de entrada --el unico que pregunta "¿estas a salvo?"-- quedo
    inalcanzable: medido el 24 oct, en C10 (a) salia el de refuerzo."""
    respuesta = redactar(
        _request("ya no quiero vivir", mode=ConversationMode.SUPPORT,
                 etiqueta="angustia_fuerte", financial_data={}),
        call=FakeModel([]),
        log_path=log_path,
    )

    assert respuesta.protected_text
    assert "a salvo" in respuesta.message
    assert "Gracias por decirme" not in respuesta.message


def test_en_apoyo_la_crisis_refuerza_sin_repetir_el_texto_de_entrada(log_path):
    """AB7: si las lineas ya salieron, esto responde a "¿estas a salvo?", no
    es una primera deteccion. Repetir el texto completo le dice el mismo
    limite dos veces y suena a bucle."""
    respuesta = redactar(
        _request("no, no estoy bien", mode=ConversationMode.SUPPORT,
                 etiqueta="angustia_fuerte", financial_data={},
                 already_stated_limits=[LIMIT_CRISIS_LINE]),
        call=FakeModel([]),
        log_path=log_path,
    )

    assert respuesta.protected_text
    assert "Gracias por decirme" in respuesta.message
    assert "Dejemos las cuentas de lado" not in respuesta.message


def test_declining_figures_empties_the_context(log_path):
    """Si ella pidió no ver cifras, no se inyecta ninguna: lo que no está
    en el contexto no se puede filtrar."""
    fake = FakeModel([_output("Esta bien, cuando quieras.", etiqueta="tangente")])
    redactar(
        _request(
            "no quiero ver numeros",
            preferences={PREF_SHOW_FINANCIAL_FIGURES: False},
        ),
        call=fake,
        log_path=log_path,
    )

    assert "74.350" not in fake.last_context
    assert "1.064.000" not in fake.last_context
    assert "ninguno" in fake.last_context


def test_minimal_injection_holds_through_the_adapter(log_path):
    """En E1 el backend solo manda el vencimiento; el contexto no debe
    traer el saldo ni aunque el fixture lo tenga."""
    fake = FakeModel([_output("Vence el 3 de noviembre de 2026.")])
    redactar(
        _request(
            "cuando vence",
            mode=ConversationMode.DUE_DATE,
            financial_data={
                "vencimiento": FinancialField(
                    value="3 de noviembre de 2026", status=FieldStatus.CONFIRMED
                )
            },
        ),
        call=fake,
        log_path=log_path,
    )

    assert "3 de noviembre" in fake.last_context
    assert "1.064.000" not in fake.last_context


# --- traduccion del contrato -------------------------------------------

def test_absent_field_travels_as_absent(log_path):
    fake = FakeModel([_output("Esa fecha no aparece en el documento.",
                              etiqueta="solicitud_informacion")])
    redactar(
        _request(
            "cuando vence",
            mode=ConversationMode.DUE_DATE,
            financial_data=_financial_data("statement_incomplete.json"),
        ),
        call=fake,
        log_path=log_path,
    )

    assert "(ausente)" in fake.last_context


def test_unreliable_field_travels_with_both_values(log_path):
    fake = FakeModel([
        _output(
            "Aparece mas de un pago minimo, $74.350 y $47.350, "
            "asi que no puedo confirmarte cual es."
        )
    ])
    response = redactar(
        _request("cuanto es el minimo",
                 financial_data=_financial_data("statement_conflict.json")),
        call=fake,
        log_path=log_path,
    )

    assert "no confiable" in fake.last_context
    assert "47.350" in response.message


def test_max_mode_reached_feeds_the_resume(log_path):
    fake = FakeModel([
        _output("Que bueno que volviste. Seguimos desde ahi o algo mas simple?",
                etiqueta="retoma_voluntaria")
    ])
    redactar(
        _request(
            "hola, ya estoy mejor",
            mode=ConversationMode.GREETING,
            max_mode_reached=ConversationMode.BALANCE_OVERVIEW,
            financial_data={},
        ),
        call=fake,
        log_path=log_path,
    )

    # El escalon maximo ya no viaja como flag: alimenta el movimiento de
    # retoma, que es lo que el modelo tiene que hacer con el.
    assert "escalon_maximo" not in fake.last_context
    assert "tu pago minimo y tu saldo" in fake.last_context


def test_calculations_authorise_their_figures(log_path):
    fake = FakeModel([
        _output("Con $50.000 extra serian 43 meses en vez de 49.",
                etiqueta="disposicion_explorar")
    ])
    response = redactar(
        _request(
            "y si pago 50 mil mas",
            mode=ConversationMode.AMOUNT_SIMULATION,
            financial_data={
                "monto_elegido": FinancialField(
                    value=50000, status=FieldStatus.CONFIRMED
                ),
            },
            # Los plazos en meses van en `calculations`, donde el contrato los
            # pone: son salida del simulador, no campos del estado de cuenta.
            calculations={"meses_con_extra": 43, "meses_solo_minimo": 49},
        ),
        call=fake,
        log_path=log_path,
    )

    assert "43 meses" in response.message


def test_essential_and_amount_come_from_the_classification(log_path):
    """`esencial` y `monto_mencionado` los emite el clasificador, no el
    redactor: son decisiones sobre el mensaje, no sobre la redaccion."""
    fake = FakeModel([_clasificado("intencion_compra_cuotas", esencial=True, monto=60000)])
    clasificacion = clasificar(
        _classify_request(
            "quiero comprar los remedios de mi mama en 6 cuotas",
            mode=ConversationMode.INSTALLMENT_PAUSE,
        ),
        call=fake,
        log_path=log_path,
    )

    assert clasificacion.intent == "intencion_compra_cuotas"
    assert clasificacion.essential is True
    assert clasificacion.mentioned_amount == 60000

    # Y con esa clasificacion, el redactor recibe la rama esencial y no la otra.
    escribe = FakeModel([_output("Entiendo, eso no se posterga.")])
    redactar(
        _request(
            "quiero comprar los remedios de mi mama en 6 cuotas",
            mode=ConversationMode.INSTALLMENT_PAUSE,
            financial_data={},
            classification=clasificacion,
        ),
        call=escribe,
        log_path=log_path,
    )

    assert "no se posterga" in escribe.last_context
    assert "Esperar diez minutos" not in escribe.last_context


def test_llm_agent_satisfies_the_protocol(log_path):
    fake = FakeModel([_output("El pago minimo es $74.350.")])
    agent: Agent = LlmAgent(call=fake, log_path=log_path)

    assert asyncio.run(agent.write(_request("cuanto es el minimo"))).message.startswith(
        "El pago minimo"
    )


# --- revision de backend y Codex en #5 ---------------------------------

def test_calculations_do_not_escape_the_mode_filter(log_path):
    """P1: `calculations` llega sin pasar por CAMPOS_POR_MODO. Una cifra
    calculada en APOYO es la misma fuga que la inyeccion minima evita."""
    fake = FakeModel([_output("Te leo.", etiqueta="tangente")])
    redactar(
        _request(
            "no doy mas",
            mode=ConversationMode.SUPPORT,
            financial_data={},
            calculations={"meses_con_extra": 43},
        ),
        call=fake,
        log_path=log_path,
    )

    assert "43" not in fake.last_context
    assert "ninguno" in fake.last_context


def test_an_unauthorised_calculation_is_not_whitelisted_either(log_path):
    """Si el contexto no mostro la cifra, el validador tampoco la acepta:
    las dos cosas leen el mismo diccionario filtrado."""
    fake = FakeModel([
        _output("Serian 43 meses.", etiqueta="disposicion_explorar"),
        _output("Te leo, sin numeros por ahora.", etiqueta="tangente"),
    ])
    response = redactar(
        _request(
            "cuantos meses",
            mode=ConversationMode.SUPPORT,
            financial_data={},
            calculations={"meses_con_extra": 43},
        ),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 2  # el primer intento fallo la lista blanca
    assert "43" not in response.message


def test_installment_pause_gets_its_calculation_once_she_asks(log_path):
    fake = FakeModel([
        _output("Con esa cuota, el minimo pasaria a $84.350.",
                etiqueta="intencion_compra_cuotas")
    ])
    redactar(
        _request(
            "cuanto me subiria",
            mode=ConversationMode.INSTALLMENT_PAUSE,
            financial_data={
                "minimo": FinancialField(value=74350, status=FieldStatus.CONFIRMED),
                "minimo_con_cuota": FinancialField(
                    value=84350, status=FieldStatus.CONFIRMED),
            },
            requested_installment_calculation=True,
            calculations={"cuota_mensual": 10000},
        ),
        call=fake,
        log_path=log_path,
    )

    assert "84.350" in fake.last_context
    assert "10.000" in fake.last_context


def test_recent_history_reaches_the_prompt(log_path):
    """P2: sin historial, un "si" o "la segunda" no se puede interpretar."""
    fake = FakeModel([_output("El pago minimo es $74.350.")])
    redactar(
        _request(
            "si, la segunda",
            recent_history=[
                ConversationMessage(role="assistant",
                                    content="Quieres ver la fecha o el monto?"),
                ConversationMessage(role="user", content="mmm"),
            ],
        ),
        call=fake,
        log_path=log_path,
    )

    assert "la fecha o el monto" in fake.last_context
    assert "LOS DOS ULTIMOS TURNOS" in fake.last_context


def test_history_figures_are_masked(log_path):
    """El historial sirve para entender la referencia, no para copiar una
    cifra: si una cifra vieja volviera, el validador la rechazaria."""
    fake = FakeModel([_output("Vence el 3 de noviembre de 2026.",
                              etiqueta="solicitud_informacion")])
    redactar(
        _request(
            "y cuando vence",
            mode=ConversationMode.DUE_DATE,
            financial_data={
                "vencimiento": FinancialField(
                    value="3 de noviembre de 2026", status=FieldStatus.CONFIRMED)
            },
            recent_history=[
                ConversationMessage(role="assistant",
                                    content="Tu saldo es $1.064.000."),
            ],
        ),
        call=fake,
        log_path=log_path,
    )

    assert "1.064.000" not in fake.last_context
    assert "Tu saldo es" in fake.last_context


def test_the_trace_can_be_switched_off(monkeypatch, tmp_path):
    """Guarda el mensaje completo y la respuesta con cifras: apagada por
    defecto, y el disco no se toca."""
    monkeypatch.chdir(tmp_path)
    fake = FakeModel([_output("El pago minimo es $74.350.")])
    response = redactar(_request("cuanto es el minimo"), call=fake)  # sin log_path

    assert "74.350" in response.message  # el turno responde igual
    assert list(tmp_path.glob("*.jsonl")) == []  # pero no deja rastro en disco


# --- presupuesto del turno (medido contra el proveedor real) -----------

def test_no_budget_means_no_retry(monkeypatch, log_path):
    """Twilio corta a los 15 s. Medido, un turno con respaldo y reintento
    llego a 16: si no cabe otra llamada, sale el texto de respaldo del modo,
    que es determinista y no necesita al modelo."""
    # Una configuracion sin presupuesto ya no existe: `Settings` la rechaza al
    # arrancar. Lo que sigue siendo real es que la primera llamada se alargue y
    # no quede tiempo para otra, asi que eso es lo que se simula, con el reloj.
    reloj = iter([0.0, 100.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(reloj))
    fake = FakeModel([_output("Te cobran como $30.000 de interes.")])

    response = redactar(
        _request("cuanto me cobran", can_offer_continue=True),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 1  # no se gasto un segundo intento
    assert "30.000" not in response.message
    assert "ordenarlo bien" in response.message


def test_the_trace_shows_that_a_turn_needed_two_calls(log_path):
    """`modelo.latencia_ms` es solo la ultima llamada: sin `intentos`, un
    turno de 16 s se veia como uno de 6."""
    fake = FakeModel([
        _output("Te cobran como $30.000."),
        _output("El pago minimo es $74.350."),
    ])
    redactar(_request("cuanto me cobran"), call=fake, log_path=log_path)

    linea = obs.leer(log_path)[0]
    assert linea["intentos"] == 2
    assert linea["latencia_total_ms"] >= 0
