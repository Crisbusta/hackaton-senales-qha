"""Lectura determinista del estado de cuenta de tarjeta de crédito en PDF (T07).

Dos pasos separados para poder probar el segundo sin PDF:
1. `extract_text`: bytes -> texto, con los límites de seguridad. Todo en
   memoria; nada se escribe a disco ni se registra en logs.
2. `parse_statement_text`: texto -> `ParsedStatement`, solo con regex.

Reglas de los campos (`fields`):
- Un valor sale solo si su etiqueta está literalmente en el documento. Nada
  se deduce ni se calcula: si falta la etiqueta, el campo es ABSENT.
- Si el documento trae el mismo dato en varios lugares (por ejemplo el total
  a pagar en el encabezado, en el resumen y en el detalle) y los valores no
  coinciden, el campo es UNRELIABLE con todos los valores, y nadie elige.
- Las comprobaciones aritméticas (que el resumen sume, etc.) no tocan el
  estado de los campos: viven en `validation.py`.

Se usa `pypdf` (BSD) y no `pymupdf` (AGPL-3.0): sobre los 12 estados de
cuenta de prueba los dos extraen exactamente los mismos montos, fechas,
tasas y cuotas, y la AGPL obligaría a publicar el código de un servicio de red.
"""

import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from pypdf import PdfReader

from app.modules.agent.interface import FieldStatus, FinancialField
from app.modules.banking.service import format_date_es
from app.modules.statements.schemas import (
    AnnualRates,
    ParsedStatement,
    Purchase,
    ScheduledInstallment,
    StatementError,
    StatementSummary,
)

MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_PAGES = 10
# Un estado de cuenta real tiene unos pocos miles de caracteres por página.
# Más que esto es un documento que no es un estado de cuenta o uno hostil.
MAX_TEXT_CHARS = 200_000

FIELD_KEYS = (
    "vencimiento",
    "facturado",
    "minimo",
    "saldo",
    "cupo",
    "tasa",
    "minimo_intereses",
    "minimo_cuotas",
    "minimo_abono",
)

# --- extracción --------------------------------------------------------------


def extract_text(pdf_bytes: bytes) -> tuple[str, list[str]]:
    """Texto de todas las páginas, unido con saltos de línea, y warnings.

    Levanta `StatementError` si no es un PDF legible o excede los límites.
    """
    if not isinstance(pdf_bytes, bytes | bytearray):
        raise StatementError("no_es_pdf")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise StatementError("pdf_demasiado_grande")
    # La especificación permite basura antes del encabezado (primeros 1024 bytes).
    if b"%PDF-" not in bytes(pdf_bytes[:1024]):
        raise StatementError("no_es_pdf")

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise StatementError("pdf_protegido")
        page_count = len(reader.pages)
    except StatementError:
        raise
    except Exception as exc:  # pypdf falla de formas muy variadas con PDFs rotos
        raise StatementError("pdf_ilegible") from exc

    if page_count == 0:
        raise StatementError("pdf_ilegible")
    if page_count > MAX_PAGES:
        raise StatementError("pdf_demasiadas_paginas")

    warnings: list[str] = []
    chunks: list[str] = []
    total = 0
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # una página rota no invalida las demás
            warnings.append(f"pagina_ilegible:{number}")
            continue
        if total + len(text) > MAX_TEXT_CHARS:
            warnings.append("texto_truncado")
            break
        total += len(text)
        chunks.append(text)
    return "\n".join(chunks), warnings


def parse_statement(pdf_bytes: bytes) -> ParsedStatement:
    """Lee un estado de cuenta en PDF.

    Solo levanta `StatementError` (no es PDF, ilegible, > 5 MB o > 10
    páginas). Contenido inesperado -> campos ABSENT y `warnings`.
    """
    text, warnings = extract_text(pdf_bytes)
    statement = parse_statement_text(text)
    statement.warnings = warnings + statement.warnings
    return statement


# --- texto -> campos ---------------------------------------------------------

_AMOUNT = r"-?\$\s?\d{1,3}(?:\.\d{3})*"
_DATE_DASH = r"\d{1,2}-\d{1,2}-\d{4}"
_PCT = r"\d{1,3}(?:,\d+)?"

_MONTHS = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}  # fmt: skip

_RE_BANK = re.compile(r"^(BANCO(?:\s+[A-ZÁÉÍÓÚÑ]+)+(?:\s+\([^)\n]{1,30}\))?)", re.M)
_RE_CARD = re.compile(r"N[º°o]\s*de\s+Tarjeta\s+de\s+Crédito\s+([X\d*](?:[X\d* ]{10,24})\d)")
_RE_STATEMENT_DATE = re.compile(rf"Fecha\s+del\s+Estado\s+de\s+Cuenta\s+({_DATE_DASH})")
_RE_PERIOD = re.compile(rf"Periodo\s+de\s+Facturación\s+({_DATE_DASH})\s+al\s+({_DATE_DASH})")
_RE_HEADER_TOTAL = re.compile(rf"PAGAR\s+HASTA\s+({_AMOUNT})\s+({_DATE_DASH})")
_RE_MINIMUM = re.compile(rf"El\s+Pago\s+Mínimo\s+es\s+de\s+({_AMOUNT})")
_RE_MINIMUM_SENTENCE = re.compile(rf"Pago\s+Mínimo\s+\(({_AMOUNT})\)")
_RE_TOTAL_SENTENCE = re.compile(rf"Monto\s+Total\s+a\s+Pagar\s+\(({_AMOUNT})\)")
_RE_CUPO = re.compile(rf"^Cupo\s+tarjeta\s+({_AMOUNT})\s+({_AMOUNT})\s+({_AMOUNT})\s*$", re.M)
_RE_PAYOFF = re.compile(rf"anticipadamente\s+Ud\.\s+deberá\s+pagar\*?:\s*({_AMOUNT})")
_RE_ROTATIVO = re.compile(rf"Intereses\s+rotativos.*?Tasa\s+int\.\s*({_PCT})%")
_ANNUAL_NOTE = r"\s*\(tasa de interés en base anual\)\s*"
_RE_ANNUAL_P1 = re.compile(rf"({_PCT})%{_ANNUAL_NOTE}({_PCT})%{_ANNUAL_NOTE}")
_RE_ANNUAL_P3 = re.compile(
    rf"^Tasa\s+de\s+Interés\s+Anual\s+({_PCT})%\s+({_PCT})%.*?\s({_PCT})%\s*$", re.M
)
_RE_CAE = re.compile(rf"Equivalente\)\s+({_PCT})%\s+(?:no aplica|{_PCT}%)\s+({_PCT})%")
_RE_SCHEDULE = re.compile(
    r"^((?:(?:ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEPT?|OCT|NOV|DIC)\s+\d{2}\s*){1,12})\n"
    rf"((?:{_AMOUNT}\s*){{1,12}})$",
    re.M,
)
_RE_BREAKDOWN = re.compile(
    rf"Desglose\s+del\s+Pago\s+Mínimo:\s*intereses\s+({_AMOUNT})\s*\+\s*cuotas\s+del\s+mes\s+"
    rf"({_AMOUNT})\s*\+\s*abono\s+a\s+capital[^$]*?({_AMOUNT})\s*=\s*({_AMOUNT})"
)
_RE_DETAIL_HEADER = re.compile(rf"^([1-9])\.\s+[A-ZÁÉÍÓÚÑ/ ]+?\s+({_AMOUNT})\s*$", re.M)
_RE_PURCHASE = re.compile(
    r"^(?:[^\d\n]+?\s+)?(\d{1,2}/\d{1,2}/\d{4})\s+\d+\s+(.+?)\s+Tasa\s+int\.\s*" + _PCT + r"%\s+"
    rf"({_AMOUNT})\s+({_AMOUNT})\s+(\d{{1,2}})/(\d{{1,2}})\s+({_AMOUNT})\s*$",
    re.M,
)
_RE_SECTION_2 = re.compile(r"^2\.\s+TOTAL\s+OPERACIONES.*?(?=^3\.\s|\Z)", re.M | re.S)

# (clave en StatementSummary, etiqueta en el resumen, número de sección en el detalle)
_SUMMARY_LINES = (
    ("previous_balance", r"Monto\s+Pendiente\s+de\s+Pago\s+del\s+Periodo\s+Anterior", 1),
    ("holder_operations", r"Operaciones\s+realizadas\s+con\s+la\s+Tarjeta\s+Titular", 2),
    ("additional_card_operations", r"Operaciones\s+realizadas\s+con\s+Tarjetas\s+Adicionales", 3),
    ("additional_products", r"Productos\s+o\s+Servicios\s+Adicionales", 4),
    ("interest_and_collection", r"Intereses\s+y\s+Gastos\s+de\s+Cobranza", 5),
    ("commissions", r"Comisiones", 6),
    ("credit_tax", r"Impuesto\s+al\s+Crédito", 7),
    ("payments", r"Pagos\s+y\s+Abonos\s+realizados", 8),
    ("total", r"TOTAL\s+A\s+PAGAR", 9),
)


def _amount(raw: str) -> int:
    """'$1.064.000' -> 1064000, '-$75.000' -> -75000."""
    negative = raw.strip().startswith("-")
    value = int(re.sub(r"\D", "", raw))
    return -value if negative else value


def _pct(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None


def _date(raw: str, warnings: list[str], name: str) -> date | None:
    try:
        day, month, year = (int(part) for part in re.split(r"[-/]", raw))
        return date(year, month, day)
    except ValueError:
        warnings.append(f"fecha_invalida:{name}")
        return None


def _distinct(values: list[int]) -> list[int]:
    return list(dict.fromkeys(values))


def _int_field(values: list[int]) -> FinancialField:
    distinct = _distinct(values)
    if not distinct:
        return FinancialField(value=None, status=FieldStatus.ABSENT)
    if len(distinct) == 1:
        return FinancialField(value=distinct[0], status=FieldStatus.CONFIRMED)
    return FinancialField(value=distinct, status=FieldStatus.UNRELIABLE)


def _str_field(values: list[str], key: str, warnings: list[str]) -> FinancialField:
    """Campos de texto: un conflicto no cabe en `list[int]`, va sin valor."""
    distinct = list(dict.fromkeys(values))
    if not distinct:
        return FinancialField(value=None, status=FieldStatus.ABSENT)
    if len(distinct) == 1:
        return FinancialField(value=distinct[0], status=FieldStatus.CONFIRMED)
    warnings.append(f"conflicto_texto:{key}")
    return FinancialField(value=None, status=FieldStatus.UNRELIABLE)


def _format_rate(rate: Decimal) -> str:
    """Decimal('2.80') -> '2,8 % mensual' (igual que banking.service)."""
    return f"{f'{rate.normalize():f}'.replace('.', ',')} % mensual"


def _clean_description(raw: str) -> str:
    """pypdf une sin espacio las celdas que el PDF parte en dos líneas
    ("Farmacia CruzVerde", "hogar(refrigerador)"). Se repara lo evidente;
    "lentespara" no se puede distinguir de una palabra real y queda igual."""
    text = re.sub(r"(?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ])", " ", raw)
    text = re.sub(r"(?<=\S)\(", " (", text)
    return re.sub(r"\s+", " ", text).strip()


def _summary(
    text: str, warnings: list[str]
) -> tuple[StatementSummary, dict[str, list[int]], dict[int, list[int]]]:
    """Resumen (página 1) y totales de sección del detalle (página 2)."""
    from_summary: dict[str, list[int]] = {}
    for key, label, _ in _SUMMARY_LINES:
        pattern = rf"^{label}\S*\s*[+=−\-]\s*({_AMOUNT})\s*$"
        values = [_amount(v) for v in re.findall(pattern, text, re.M)]
        if key == "payments":
            # Se imprime "− -$75.000" o "− $0": siempre es un descuento.
            values = [-abs(v) for v in values]
        from_summary[key] = values

    from_detail: dict[int, list[int]] = {}
    for number, raw in _RE_DETAIL_HEADER.findall(text):
        from_detail.setdefault(int(number), []).append(_amount(raw))

    values: dict[str, int | None] = {}
    for key, _, section in _SUMMARY_LINES:
        own = _distinct(from_summary[key])
        if len(own) > 1:
            warnings.append(f"resumen_conflicto:{key}")
        values[key] = own[0] if len(own) == 1 else None
        detail = from_detail.get(section, [])
        if key == "payments":
            detail = [-abs(v) for v in detail]
        if values[key] is not None and detail and set(detail) != {values[key]}:
            warnings.append(f"resumen_distinto_de_detalle:{key}")
    return StatementSummary(**values), from_summary, from_detail


def _purchases(text: str, warnings: list[str]) -> list[Purchase]:
    section = _RE_SECTION_2.search(text)
    if not section:
        return []
    rows: list[Purchase] = []
    for raw_date, desc, _amount_op, total, number, count, monthly in _RE_PURCHASE.findall(
        section.group(0)
    ):
        purchased = _date(raw_date, warnings, "compra")
        if purchased is None:
            continue
        rows.append(
            Purchase(
                description=_clean_description(desc),
                purchase_date=purchased,
                total_amount=_amount(total),
                installment_number=int(number),
                total_installments=int(count),
                installment_amount=_amount(monthly),
            )
        )
    return rows


def _schedule(text: str) -> list[ScheduledInstallment]:
    match = _RE_SCHEDULE.search(text)
    if not match:
        return []
    labels = re.findall(r"([A-Z]{3,4})\s+(\d{2})", match.group(1))
    amounts = [_amount(v) for v in re.findall(_AMOUNT, match.group(2))]
    if len(labels) != len(amounts):
        return []
    return [
        ScheduledInstallment(month=f"20{year}-{_MONTHS[name]:02d}", amount=amount)
        for (name, year), amount in zip(labels, amounts, strict=True)
    ]


def _annual_rates(text: str, warnings: list[str]) -> AnnualRates:
    rates = AnnualRates()
    if match := _RE_ANNUAL_P1.search(text):
        rates.revolving, rates.late_payment = _pct(match.group(1)), _pct(match.group(2))
    if match := _RE_ANNUAL_P3.search(text):
        revolving = _pct(match.group(1))
        if rates.revolving is None:
            rates.revolving = revolving
        elif revolving != rates.revolving:
            warnings.append("tasa_anual_conflicto:revolving")
        rates.installment_purchase = _pct(match.group(2))
        rates.cash_advance = _pct(match.group(3))
    if match := _RE_CAE.search(text):
        rates.cae_revolving, rates.cae_cash_advance = _pct(match.group(1)), _pct(match.group(2))
    return rates


def parse_statement_text(text: str) -> ParsedStatement:
    """Texto extraído -> `ParsedStatement`. Nunca levanta excepciones."""
    try:
        return _parse(text)
    except Exception:  # contrato: contenido inesperado nunca es una excepción
        empty = {key: FinancialField(status=FieldStatus.ABSENT) for key in FIELD_KEYS}
        return ParsedStatement(fields=empty, warnings=["error_interno_parser"])


def _parse(text: str) -> ParsedStatement:
    warnings: list[str] = []
    if not text.strip():
        warnings.append("pdf_sin_texto")

    bank = _RE_BANK.search(text)
    card = _RE_CARD.search(text)
    masked = None
    if card:
        digits = re.sub(r"\D", "", card.group(1))
        masked = f"**** {digits[-4:]}" if len(digits) >= 4 else None

    statement_date = None
    if match := _RE_STATEMENT_DATE.search(text):
        statement_date = _date(match.group(1), warnings, "statement_date")
    period_start = period_end = None
    if match := _RE_PERIOD.search(text):
        period_start = _date(match.group(1), warnings, "period_start")
        period_end = _date(match.group(2), warnings, "period_end")

    summary, from_summary, from_detail = _summary(text, warnings)

    header = _RE_HEADER_TOTAL.findall(text)
    due_dates = [d for _, raw in header if (d := _date(raw, warnings, "due_date"))]
    distinct_due = list(dict.fromkeys(due_dates))
    due_date = distinct_due[0] if len(distinct_due) == 1 else None

    facturado = (
        [_amount(v) for v, _ in header]
        + from_summary["total"]
        + from_detail.get(9, [])
        + [_amount(v) for v in _RE_TOTAL_SENTENCE.findall(text)]
    )
    breakdown = _RE_BREAKDOWN.findall(text)
    minimo = (
        [_amount(v) for v in _RE_MINIMUM.findall(text)]
        + [_amount(b[3]) for b in breakdown]
        + [_amount(v) for v in _RE_MINIMUM_SENTENCE.findall(text)]
    )
    intereses = (
        from_summary["interest_and_collection"]
        + from_detail.get(5, [])
        + [_amount(b[0]) for b in breakdown]
    )
    cupo_rows = [tuple(_amount(v) for v in row) for row in _RE_CUPO.findall(text)]
    rotativo = [r for raw in _RE_ROTATIVO.findall(text) if (r := _pct(raw)) is not None]
    distinct_rates = list(dict.fromkeys(rotativo))

    fields = {
        "vencimiento": _str_field([format_date_es(d) for d in due_dates], "vencimiento", warnings),
        "facturado": _int_field(facturado),
        "minimo": _int_field(minimo),
        "saldo": _int_field([row[1] for row in cupo_rows]),
        "cupo": _int_field([row[0] for row in cupo_rows]),
        "tasa": _str_field([_format_rate(r) for r in rotativo], "tasa", warnings),
        "minimo_intereses": _int_field(intereses),
        "minimo_cuotas": _int_field([_amount(b[1]) for b in breakdown]),
        "minimo_abono": _int_field([_amount(b[2]) for b in breakdown]),
    }

    available = _distinct([row[2] for row in cupo_rows])
    payoff = _distinct([_amount(v) for v in _RE_PAYOFF.findall(text)])
    purchases = _purchases(text, warnings)

    return ParsedStatement(
        fields=fields,
        bank_name=bank.group(1) if bank else None,
        masked_card=masked,
        statement_date=statement_date,
        period_start=period_start,
        period_end=period_end,
        due_date=due_date,
        purchases=purchases,
        installments=[p for p in purchases if p.total_installments > 1],
        summary=summary,
        annual_rates=_annual_rates(text, warnings),
        monthly_revolving_rate=distinct_rates[0] if len(distinct_rates) == 1 else None,
        credit_available=available[0] if len(available) == 1 else None,
        payoff_amount=payoff[0] if len(payoff) == 1 else None,
        upcoming_installments=_schedule(text),
        warnings=warnings,
    )
