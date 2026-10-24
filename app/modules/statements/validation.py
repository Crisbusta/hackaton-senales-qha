"""Comprobaciones aritméticas deterministas sobre un estado de cuenta leído.

No cambian el estado de los campos: un PDF que no cuadra se reporta, no se
corrige. Cada problema es un código estable (sin texto del documento) que el
backend puede registrar o usar para decidir si confía en el PDF.

Una comprobación que necesita un dato ausente o no confiable no se ejecuta;
el problema ya queda reportado como `campo_ausente:*` o `campo_no_confiable:*`.
"""

from app.modules.agent.interface import FieldStatus
from app.modules.statements.schemas import ParsedStatement


def _confirmed(statement: ParsedStatement, key: str) -> int | None:
    field = statement.fields.get(key)
    if field is None or field.status is not FieldStatus.CONFIRMED:
        return None
    return field.value if isinstance(field.value, int) else None


def validate_statement(statement: ParsedStatement) -> list[str]:
    """Lista de códigos de problema; vacía si el documento es consistente."""
    issues: list[str] = []

    for key, field in statement.fields.items():
        if field.status is FieldStatus.ABSENT:
            issues.append(f"campo_ausente:{key}")
        elif field.status is FieldStatus.UNRELIABLE:
            issues.append(f"campo_no_confiable:{key}")

    summary = statement.summary
    addends = summary.addends()
    if summary.total is None or any(v is None for v in addends):
        issues.append("resumen_incompleto")
    elif sum(v for v in addends if v is not None) != summary.total:
        issues.append("resumen_no_suma")

    facturado = _confirmed(statement, "facturado")
    minimo = _confirmed(statement, "minimo")
    if facturado is not None and minimo is not None and minimo > facturado:
        issues.append("minimo_mayor_que_facturado")

    cupo = _confirmed(statement, "cupo")
    saldo = _confirmed(statement, "saldo")
    available = statement.credit_available
    if cupo is not None and saldo is not None and available is not None:
        if saldo + available != cupo:
            issues.append("cupo_no_cuadra")

    cuotas = _confirmed(statement, "minimo_cuotas")
    if cuotas is not None:
        if sum(p.installment_amount for p in statement.installments) != cuotas:
            issues.append("cuotas_no_suman")

    if summary.holder_operations is not None and statement.purchases:
        if sum(p.installment_amount for p in statement.purchases) != summary.holder_operations:
            issues.append("operaciones_no_suman")

    parts = [_confirmed(statement, k) for k in ("minimo_intereses", "minimo_cuotas")]
    parts.append(_confirmed(statement, "minimo_abono"))
    if minimo is not None and all(p is not None for p in parts):
        if sum(p for p in parts if p is not None) != minimo:
            issues.append("desglose_minimo_no_suma")

    if statement.due_date is None or statement.statement_date is None:
        issues.append("fechas_incompletas")
    elif statement.due_date <= statement.statement_date:
        issues.append("vencimiento_no_posterior")

    return issues
