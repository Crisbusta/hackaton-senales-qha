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
from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    Agent,
    AgentRequest,
    ConversationEvent,
    ConversationMessage,
    ConversationMode,
    FieldStatus,
    FinancialField,
)
from app.modules.agent.service import LlmAgent, run_turn

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


def _request(message, mode=ConversationMode.BALANCE_OVERVIEW, **kwargs):
    kwargs.setdefault("financial_data", _financial_data())
    return AgentRequest(user_id="demo-01", message=message, mode=mode, **kwargs)


class FakeModel:
    """Devuelve respuestas en orden y cuenta las llamadas, para poder
    afirmar que el pre-filtro de verdad evita el viaje al proveedor."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.last_context = None
        self.last_correction = None

    def __call__(self, static_prompt, context, correction=None):
        self.calls += 1
        self.last_context = context
        self.last_correction = correction
        output = self.responses.pop(0)
        metadata = {
            "nombre": "fake",
            "temp": 0.3,
            "latencia_ms": 1,
            "tokens_in": 10,
            "tokens_out": 10,
        }
        return output, metadata


def _output(mensaje, etiqueta="solicitud_informacion", esencial=None, monto=None):
    return {
        "etiqueta": etiqueta,
        "esencial": esencial,
        "monto_mencionado": monto,
        "mensaje": mensaje,
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
    response = run_turn(
        _request("ya, mandame que debo", can_offer_continue=True),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 1
    assert "74.350" in response.message
    assert response.intent == "disposicion_explorar"
    assert response.event is ConversationEvent.ACCEPTED_NEXT_STEP


def test_invented_figure_is_fixed_on_retry(log_path):
    fake = FakeModel([
        _output("Te cobran como $30.000 de interes."),
        _output("El pago minimo es $74.350."),
    ])
    response = run_turn(
        _request("cuanto me cobran de interes"), call=fake, log_path=log_path
    )

    assert fake.calls == 2
    assert "lista_blanca_numerica" in fake.last_correction
    assert "74.350" in response.message
    assert "30.000" not in response.message


def test_two_failures_fall_back_to_the_mode_text(log_path):
    fake = FakeModel([
        _output("Son $30.000."),
        _output("Son $99.999."),
    ])
    response = run_turn(
        _request("cuanto me cobran", can_offer_continue=True),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 2
    assert "99.999" not in response.message
    assert "ordenarlo bien" in response.message


# --- seguridad ---------------------------------------------------------

def test_crisis_never_reaches_the_provider(log_path):
    fake = FakeModel([])  # si se llamara, revienta por cola vacia
    response = run_turn(
        _request("ya no quiero vivir", mode=ConversationMode.SUPPORT),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 0
    assert response.intent == "angustia_fuerte"
    assert response.event is ConversationEvent.CRISIS_SUPPORT_NEEDED
    assert "*4141" in response.message and "131" in response.message


def test_crisis_detected_by_the_model_also_uses_the_fixed_text(log_path):
    fake = FakeModel([
        _output("Tranquila, con $74.350 lo resuelves.", etiqueta="angustia_fuerte")
    ])
    response = run_turn(
        _request("siento que ya no puedo mas con nada"),
        call=fake,
        log_path=log_path,
    )

    assert fake.calls == 1
    assert "74.350" not in response.message
    assert "a salvo" in response.message.lower()
    assert response.event is ConversationEvent.CRISIS_SUPPORT_NEEDED


def test_declining_figures_empties_the_context(log_path):
    """Si ella pidió no ver cifras, no se inyecta ninguna: lo que no está
    en el contexto no se puede filtrar."""
    fake = FakeModel([_output("Esta bien, cuando quieras.", etiqueta="tangente")])
    run_turn(
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
    run_turn(
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
    run_turn(
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
    response = run_turn(
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
    run_turn(
        _request(
            "hola, ya estoy mejor",
            mode=ConversationMode.GREETING,
            max_mode_reached=ConversationMode.BALANCE_OVERVIEW,
            financial_data={},
        ),
        call=fake,
        log_path=log_path,
    )

    assert "escalon_maximo: E2" in fake.last_context
    assert "tu pago minimo y tu saldo" in fake.last_context


def test_calculations_authorise_their_figures(log_path):
    fake = FakeModel([
        _output("Con $50.000 extra serian 43 meses en vez de 49.",
                etiqueta="disposicion_explorar")
    ])
    response = run_turn(
        _request(
            "y si pago 50 mil mas",
            mode=ConversationMode.AMOUNT_SIMULATION,
            financial_data={
                "monto_elegido": FinancialField(
                    value=50000, status=FieldStatus.CONFIRMED
                ),
                "meses_solo_minimo": FinancialField(
                    value=49, status=FieldStatus.CONFIRMED
                ),
            },
            calculations={"meses_con_extra": 43},
        ),
        call=fake,
        log_path=log_path,
    )

    assert "43 meses" in response.message


def test_essential_and_amount_survive_the_round_trip(log_path):
    fake = FakeModel([
        _output(
            "Entiendo, eso no se posterga.",
            etiqueta="intencion_compra_cuotas",
            esencial=True,
            monto=60000,
        )
    ])
    response = run_turn(
        _request(
            "quiero comprar los remedios de mi mama en 6 cuotas",
            mode=ConversationMode.INSTALLMENT_PAUSE,
            financial_data={},
        ),
        call=fake,
        log_path=log_path,
    )

    assert response.essential is True
    assert response.mentioned_amount == 60000
    # Sin evento acordado para esta etiqueta: mejor None que adivinar.
    assert response.event is None


# --- observabilidad y protocolo ---------------------------------------

def test_every_turn_leaves_a_trace(log_path):
    fake = FakeModel([_output("Gracias por este rato.", etiqueta="cierre")])
    run_turn(_request("gracias, chao"), call=fake, log_path=log_path)

    lines = obs.leer(log_path)
    assert len(lines) == 1
    # El id de conversacion es el telefono: se guarda seudonimizado.
    assert lines[0]["conv"] == obs.seudonimo("demo-01")
    assert "demo-01" not in json.dumps(lines[0])
    assert lines[0]["entrada"] == "gracias, chao"
    assert lines[0]["validadores"]


def test_llm_agent_satisfies_the_protocol(log_path):
    fake = FakeModel([_output("El pago minimo es $74.350.")])
    agent: Agent = LlmAgent(call=fake, log_path=log_path)
    response = asyncio.run(agent.process(_request("cuanto es el minimo")))

    assert response.message.startswith("El pago minimo")


# --- revision de backend y Codex en #5 ---------------------------------

def test_calculations_do_not_escape_the_mode_filter(log_path):
    """P1: `calculations` llega sin pasar por CAMPOS_POR_MODO. Una cifra
    calculada en APOYO es la misma fuga que la inyeccion minima evita."""
    fake = FakeModel([_output("Te leo.", etiqueta="tangente")])
    run_turn(
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
    response = run_turn(
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
    run_turn(
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
    run_turn(
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
    assert "HISTORIAL RECIENTE" in fake.last_context


def test_history_figures_are_masked(log_path):
    """El historial sirve para entender la referencia, no para copiar una
    cifra: si una cifra vieja volviera, el validador la rechazaria."""
    fake = FakeModel([_output("Vence el 3 de noviembre de 2026.",
                              etiqueta="solicitud_informacion")])
    run_turn(
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
    response = run_turn(_request("cuanto es el minimo"), call=fake)  # sin log_path

    assert "74.350" in response.message  # el turno responde igual
    assert list(tmp_path.glob("*.jsonl")) == []  # pero no deja rastro en disco
