"""Texto -> campos, sin PDF: ausencias, conflictos y validaciones."""

from app.modules.agent.interface import FieldStatus
from app.modules.statements import parse_statement_text, validate_statement
from app.modules.statements.parser import FIELD_KEYS

# Mismo formato que entrega pypdf para el estado de cuenta de Banco Austral,
# recortado a las líneas que el parser lee. Cifras inventadas y consistentes.
SAMPLE = """\
BANCO AUSTRAL (ficticio) Tarjeta Austral Clásica
Nombre del Titular PERSONA DE PRUEBA
RUT 22.222.222-2 (ficticio)
Nº de Tarjeta de
Crédito
XXXX XXXX XXXX 1234
Fecha del Estado de
Cuenta
15-03-2026
Periodo de Facturación 16-02-2026 al 15-03-2026
TOTAL A PAGAR PAGAR HASTA
$110.500 03-04-2026
El Pago Mínimo es de $21.000. Pagar el Mínimo NO exime del pago
Monto Pendiente de Pago del Periodo Anterior + $100.000
Operaciones realizadas con la Tarjeta Titular + $20.000
Operaciones realizadas con Tarjetas Adicionales + $0
Productos o Servicios Adicionales + $0
Intereses y Gastos de Cobranza¹ + $3.000
Comisiones + $2.000
Impuesto al Crédito + $500
Pagos y Abonos realizados − -$15.000
TOTAL A PAGAR = $110.500
Cupo tarjeta $500.000 $170.500 $329.500
2. TOTAL OPERACIONES REALIZADAS CON LA TARJETA TITULAR $20.000
Santiago 1/1/2026 111122223333 Tienda de hogar(lavadora) Tasa int.0,00% $120.000 $120.000 \
03/12 $10.000
Santiago 2/3/2026 444455556666 LiderSupermercado Tasa int.0,00% $10.000 $10.000 01/01 $10.000
Total compras nacionales a la cuenta $20.000
3. TOTAL OPERACIONES REALIZADAS CON TARJETAS ADICIONALES $0
5. INTERESES POR ATRASO O POR PAGAR MENOS DEL TOTAL/MINIMO $3.000
15/3/2026 999988887777 Intereses rotativos Tasa int.2,80% $3.000 $3.000 01/01 $3.000
9. TOTAL A PAGAR $110.500
Desglose del Pago Mínimo: intereses $3.000 + cuotas del mes $10.000 + abono a capital \
(1% del saldo rotativo) $8.000 = $21.000.
"""


def _status(text: str) -> dict[str, FieldStatus]:
    return {k: f.status for k, f in parse_statement_text(text).fields.items()}


def _drop(text: str, *prefixes: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith(prefixes))


def test_sample_is_complete_and_consistent():
    statement = parse_statement_text(SAMPLE)
    assert {k: f.value for k, f in statement.fields.items()} == {
        "vencimiento": "3 de abril de 2026",
        "facturado": 110500,
        "minimo": 21000,
        "saldo": 170500,
        "cupo": 500000,
        "tasa": "2,8 % mensual",
        "minimo_intereses": 3000,
        "minimo_cuotas": 10000,
        "minimo_abono": 8000,
    }
    assert statement.masked_card == "**** 1234"
    assert [i.description for i in statement.installments] == ["Tienda de hogar (lavadora)"]
    assert statement.purchases[1].description == "Lider Supermercado"
    assert statement.warnings == []
    assert validate_statement(statement) == []


def test_identity_is_not_in_the_output():
    dumped = parse_statement_text(SAMPLE).model_dump_json()
    assert "PERSONA DE PRUEBA" not in dumped
    assert "22.222.222" not in dumped


def test_missing_label_is_absent_not_derived():
    # Sin la línea del desglose no hay abono ni cuotas del mes: aunque se
    # podrían calcular a partir del resto, no se calculan.
    statement = parse_statement_text(_drop(SAMPLE, "Desglose", "(1% del"))
    for key in ("minimo_cuotas", "minimo_abono"):
        assert statement.fields[key].status is FieldStatus.ABSENT
        assert statement.fields[key].value is None
    assert statement.fields["minimo"].status is FieldStatus.CONFIRMED
    assert "campo_ausente:minimo_abono" in validate_statement(statement)


def test_missing_rotativo_detail_leaves_tasa_absent():
    statement = parse_statement_text(_drop(SAMPLE, "15/3/2026"))
    assert statement.fields["tasa"].status is FieldStatus.ABSENT
    assert statement.fields["tasa"].value is None


def test_missing_cupo_line():
    status = _status(_drop(SAMPLE, "Cupo tarjeta"))
    assert status["saldo"] is FieldStatus.ABSENT
    assert status["cupo"] is FieldStatus.ABSENT


def test_conflicting_values_are_unreliable_with_both():
    text = SAMPLE.replace("El Pago Mínimo es de $21.000", "El Pago Mínimo es de $25.000")
    field = parse_statement_text(text).fields["minimo"]
    assert field.status is FieldStatus.UNRELIABLE
    assert field.value == [25000, 21000]


def test_conflicting_total_between_header_and_summary():
    text = SAMPLE.replace("TOTAL A PAGAR = $110.500", "TOTAL A PAGAR = $111.500")
    statement = parse_statement_text(text)
    assert statement.fields["facturado"].status is FieldStatus.UNRELIABLE
    assert statement.fields["facturado"].value == [110500, 111500]
    assert "campo_no_confiable:facturado" in validate_statement(statement)


def test_conflicting_text_field_has_no_value():
    text = SAMPLE + "16/3/2026 1 Intereses rotativos Tasa int.3,10% $1 $1 01/01 $1\n"
    field = parse_statement_text(text).fields["tasa"]
    assert field.status is FieldStatus.UNRELIABLE
    assert field.value is None


def test_failed_arithmetic_does_not_mark_fields_unreliable():
    text = SAMPLE.replace("Comisiones + $2.000", "Comisiones + $2.100")
    statement = parse_statement_text(text)
    assert all(f.status is FieldStatus.CONFIRMED for f in statement.fields.values())
    assert validate_statement(statement) == ["resumen_no_suma"]


def test_validation_codes():
    text = (
        SAMPLE.replace("$329.500", "$300.000")  # cupo no cuadra
        .replace("03/12 $10.000", "03/12 $9.000")  # cuotas no suman
        .replace("$110.500 03-04-2026", "$110.500 10-03-2026")  # vence antes del estado
    )
    issues = validate_statement(parse_statement_text(text))
    assert "cupo_no_cuadra" in issues
    assert "cuotas_no_suman" in issues
    assert "operaciones_no_suman" in issues
    assert "vencimiento_no_posterior" in issues


def test_minimum_above_billed():
    text = SAMPLE.replace("$21.000", "$200.000").replace("$8.000 = ", "$187.000 = ")
    issues = validate_statement(parse_statement_text(text))
    assert issues == ["minimo_mayor_que_facturado"]


def test_garbage_text_never_raises():
    for text in ("", "hola", "$$$ 1.2.3 --", "Cupo tarjeta $1 $2", "\x00" * 50):
        statement = parse_statement_text(text)
        assert set(statement.fields) == set(FIELD_KEYS)
        assert all(f.status is FieldStatus.ABSENT for f in statement.fields.values())
    assert parse_statement_text("").warnings == ["pdf_sin_texto"]


def test_invalid_date_becomes_warning():
    text = SAMPLE.replace("15-03-2026\n", "31-02-2026\n")
    statement = parse_statement_text(text)
    assert statement.statement_date is None
    assert "fecha_invalida:statement_date" in statement.warnings
    assert "fechas_incompletas" in validate_statement(statement)
