import asyncio

import pytest
from pydantic import ValidationError

from app.modules.agent.interface import (
    INSTALLMENT_CALC_FIELDS,
    PREF_SHOW_FINANCIAL_FIGURES,
    Agent,
    AgentReply,
    Classification,
    ClassifyRequest,
    ConversationEvent,
    ConversationMessage,
    ConversationMode,
    FieldStatus,
    FinancialField,
    Intent,
    WriteRequest,
    calculations_allowed,
    fields_for_mode,
)


def _write(**kw) -> WriteRequest:
    """Un `WriteRequest` con la clasificacion rellena.

    El contrato la exige: sin clasificacion no hay turno que redactar. Los
    tests que miran otros campos no tienen por que repetirla.
    """
    kw.setdefault("classification", Classification())
    return WriteRequest(**kw)


def test_classify_request_requires_mode() -> None:
    with pytest.raises(ValidationError):
        ClassifyRequest(user_id="u1", message="Hola")  # falta mode


def test_el_clasificador_no_tiene_donde_recibir_una_cifra() -> None:
    """La garantia es por construccion, no por disciplina: `ClassifyRequest`
    no tiene ningun campo donde quepa un monto, asi que esa llamada no puede
    filtrar uno."""
    campos = set(ClassifyRequest.model_fields)
    assert campos == {"user_id", "message", "mode", "recent_history"}
    assert not campos & {"financial_data", "calculations"}


def test_write_request_requires_classification() -> None:
    """El redactor no clasifica: si nadie clasifico, no hay turno que redactar."""
    with pytest.raises(ValidationError):
        WriteRequest(user_id="u1", message="Hola", mode=ConversationMode.GREETING)


def test_write_request_defaults() -> None:
    request = _write(
        user_id="u1", message="Hola", mode=ConversationMode.GREETING,
        classification=Classification(),
    )

    assert request.recent_history == []
    assert request.financial_data == {}
    assert request.calculations == {}
    assert request.preferences == {}
    assert request.can_offer_continue is False
    assert request.already_stated_limits == []


def test_reply_defaults() -> None:
    response = AgentReply(message="Hola")
    assert response.protected_text is False


def test_classification_defaults() -> None:
    clasificacion = Classification()

    assert clasificacion.intent == "UNKNOWN"
    assert clasificacion.event is None
    assert clasificacion.essential is None
    assert clasificacion.pace is None


def test_invalid_role_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ConversationMessage(role="system", content="x")


def test_unknown_event_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Classification(event="NOT_AN_EVENT")


def test_unknown_pace_is_rejected() -> None:
    """El ritmo es un enum chico a proposito: si el modelo inventa un valor,
    se rechaza aca y no llega a convertirse en un movimiento."""
    with pytest.raises(ValidationError):
        Classification(pace="corriendo")


def test_crisis_event_parses_from_string() -> None:
    """Sin este evento, angustia_fuerte (T05) no tiene ningun efecto
    persistente en el backend: el texto fijo saldria bien, pero el modo
    SUPPORT no quedaria registrado para los turnos siguientes."""
    response = Classification.model_validate({"event": "CRISIS_SUPPORT_NEEDED"})

    assert response.event is ConversationEvent.CRISIS_SUPPORT_NEEDED


def test_financial_field_unreliable_carries_both_values() -> None:
    # El caso de T07 "con conflicto": dos pagos minimos, ninguno se elige.
    campo = FinancialField(value=[74350, 47350], status=FieldStatus.UNRELIABLE)

    assert campo.status is FieldStatus.UNRELIABLE
    assert campo.value == [74350, 47350]


def test_financial_field_absent_has_no_value() -> None:
    campo = FinancialField(status=FieldStatus.ABSENT)

    assert campo.value is None


def test_minimal_injection_only_current_mode_fields_travel() -> None:
    # En E1 el agente no debe recibir el saldo ni el minimo: la inyeccion
    # minima es responsabilidad del backend (T06) al construir el request.
    request = _write(
        user_id="u1",
        message="cuanto debo",
        mode=ConversationMode.DUE_DATE,
        financial_data={
            "due_date": FinancialField(value="2026-11-03", status=FieldStatus.CONFIRMED),
        },
    )

    assert "balance" not in request.financial_data
    assert "minimum_payment" not in request.financial_data


def test_protocol_can_be_implemented_structurally() -> None:
    class EchoAgent:
        async def classify(self, request: ClassifyRequest) -> Classification:
            return Classification(intent=Intent.OFF_TOPIC)

        async def write(self, request: WriteRequest) -> AgentReply:
            return AgentReply(message=request.message)

    agent: Agent = EchoAgent()
    request = _write(
        user_id="u1",
        message="Hola",
        mode=ConversationMode.GREETING,
        preferences={PREF_SHOW_FINANCIAL_FIGURES: False},
    )

    assert asyncio.run(agent.write(request)).message == "Hola"
    clasificar = ClassifyRequest(user_id="u1", message="Hola", mode=ConversationMode.GREETING)
    assert asyncio.run(agent.classify(clasificar)).intent == Intent.OFF_TOPIC


# --- campos agregados en PR #4 -----------------------------------------

def test_new_fields_have_backward_compatible_defaults() -> None:
    # Un request escrito antes de PR #4 sigue siendo valido: nadie que ya
    # use el contrato se rompe por estos campos.
    request = _write(user_id="u1", message="Hola", mode=ConversationMode.GREETING)

    assert request.max_mode_reached is None
    assert request.calculations == {}
    # `previous_intent` ya no existe: los datos condicionales se habilitan
    # con la etiqueta de ESTE turno, que es lo que siempre debieron hacer.
    assert "previous_intent" not in WriteRequest.model_fields
    assert request.requested_installment_calculation is False


def test_max_mode_reached_accepts_a_mode() -> None:
    request = _write(
        user_id="u1",
        message="hola, ya estoy mejor",
        mode=ConversationMode.GREETING,
        max_mode_reached=ConversationMode.BALANCE_OVERVIEW,
    )

    assert request.max_mode_reached is ConversationMode.BALANCE_OVERVIEW


def test_calculations_carries_simulator_output() -> None:
    # Las cifras del simulador (T10) llegan resueltas: el agente las
    # redacta, no las calcula.
    request = _write(
        user_id="u1",
        message="y si pago 50 mil mas?",
        mode=ConversationMode.AMOUNT_SIMULATION,
        calculations={"months_with_extra": 43, "interest_saved": 123000},
    )

    assert request.calculations["months_with_extra"] == 43


def test_la_etiqueta_de_este_turno_habilita_los_datos_condicionales() -> None:
    """`previous_intent` era un parche por tener la etiqueta demasiado tarde.

    Con el turno en tres pasos la etiqueta se conoce antes de elegir los
    datos, asi que el monto facturado se habilita en el turno en que ella
    pregunta, no en el siguiente.
    """
    assert "previous_intent" not in WriteRequest.model_fields

    campos = fields_for_mode(
        ConversationMode.BALANCE_OVERVIEW, Intent.ASKS_BILLED_AMOUNT
    )
    assert "facturado" in campos


# --- revisión de backend en #4: montos enteros -------------------------

def test_amounts_are_integers_not_floats() -> None:
    """El peso chileno no tiene decimales. Con `float`, un saldo que pasa
    por el simulador puede terminar redactado como "74349,99999999999"."""
    field = FinancialField(value=74350, status=FieldStatus.CONFIRMED)
    assert isinstance(field.value, int)

    with pytest.raises(ValidationError):
        FinancialField(value=74349.99, status=FieldStatus.CONFIRMED)


def test_non_numeric_values_still_travel_as_text() -> None:
    # Fechas y tasas se leen pero no se operan: viajan ya formateadas.
    for value in ("3 de noviembre de 2026", "2,8 % mensual"):
        assert FinancialField(value=value, status=FieldStatus.CONFIRMED).value == value


def test_unreliable_field_keeps_both_amounts_as_integers() -> None:
    field = FinancialField(value=[74350, 47350], status=FieldStatus.UNRELIABLE)
    assert all(isinstance(v, int) for v in field.value)


def test_calculations_and_mentioned_amount_are_integers() -> None:
    request = _write(
        user_id="u1", message="50 mil mas",
        mode=ConversationMode.AMOUNT_SIMULATION,
        calculations={"meses_con_extra": 43},
    )
    assert isinstance(request.calculations["meses_con_extra"], int)

    with pytest.raises(ValidationError):
        Classification(mentioned_amount=59999.5)


# --- revisión de backend en #4: etiquetas publicadas -------------------

def test_the_published_labels_are_the_contract() -> None:
    """El backend necesita nombrarlas para las transiciones de T06; como
    strings sueltos, un typo rompe una transición sin que nadie lo note."""
    assert len(list(Intent)) == 18
    assert Intent.DISTRESS == "angustia_fuerte"
    assert Intent.INSTALLMENT_INTENT == "intencion_compra_cuotas"


def test_intent_stays_a_free_string_in_the_response() -> None:
    # Si el modelo inventa una etiqueta, se registra tal cual en la traza
    # en vez de reventar el turno.
    assert Classification(intent="algo_nuevo").intent == "algo_nuevo"


# --- revisión de backend en #4: una sola tabla de campos por modo ------

def test_fields_by_mode_is_the_single_source_of_truth() -> None:
    assert fields_for_mode(ConversationMode.GREETING) == []


def test_reaccionar_o_no_entender_no_trae_ninguna_cifra() -> None:
    """Ella ya vio sus cifras: repetirlas rompe AB6. Medido el 24 oct, en C02
    el turno 4 ("mas alto de lo que pensaba") y el 5 ("no se") salian con las
    tres cifras de nuevo."""
    for etiqueta in (Intent.REACTS_TO_FIGURE, Intent.AMBIGUOUS):
        assert fields_for_mode(ConversationMode.BALANCE_OVERVIEW, etiqueta) == [], etiqueta
        assert fields_for_mode(ConversationMode.MINIMUM_BREAKDOWN, etiqueta) == [], etiqueta
    assert fields_for_mode(ConversationMode.DUE_DATE) == ["vencimiento"]
    assert "saldo" not in fields_for_mode(ConversationMode.DUE_DATE)
    for mode in (ConversationMode.PAUSE, ConversationMode.SUPPORT,
                 ConversationMode.CLOSING):
        assert fields_for_mode(mode) == [], mode


def test_conditional_fields_need_both_the_question_and_the_mode() -> None:
    assert "facturado" in fields_for_mode(
        ConversationMode.BALANCE_OVERVIEW, Intent.ASKS_BILLED_AMOUNT)
    # preguntar no alcanza: el dato no existe antes de su escalón
    assert "facturado" not in fields_for_mode(
        ConversationMode.DUE_DATE, Intent.ASKS_BILLED_AMOUNT)
    assert "tasa" not in fields_for_mode(
        ConversationMode.BALANCE_OVERVIEW, "pregunta_intereses")
    assert "tasa" in fields_for_mode(
        ConversationMode.MINIMUM_BREAKDOWN, "pregunta_intereses")


def test_installment_pause_has_no_figures_until_she_asks_for_the_calc() -> None:
    assert fields_for_mode(ConversationMode.INSTALLMENT_PAUSE) == []
    assert fields_for_mode(
        ConversationMode.INSTALLMENT_PAUSE,
        requested_installment_calculation=True,
    ) == list(INSTALLMENT_CALC_FIELDS)


def test_calculations_are_allowed_only_where_they_were_asked_for() -> None:
    assert calculations_allowed(ConversationMode.AMOUNT_SIMULATION)
    assert calculations_allowed(
        ConversationMode.INSTALLMENT_PAUSE, requested_installment_calculation=True)
    for mode in (ConversationMode.GREETING, ConversationMode.BALANCE_OVERVIEW,
                 ConversationMode.SUPPORT, ConversationMode.INSTALLMENT_PAUSE):
        assert not calculations_allowed(mode), mode
