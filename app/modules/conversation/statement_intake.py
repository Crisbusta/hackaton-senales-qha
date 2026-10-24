"""Recepción del estado de cuenta en PDF y conciliación con el banco (T07).

Cuando ella envía un PDF: se descarga, se lee y se valida, y se responde con un
texto fijo SIN cifras (exposición progresiva: las cifras llegan por la Escalera,
cuando ella las pide). No se llama al agente: es más rápido y predecible.

Conciliación: el PDF es lo que ella envió, así que manda. Si el banco dice otra
cosa, el dato queda UNRELIABLE con ambos valores (el agente dice que aparecen dos).
Si el PDF no trae un dato y el banco sí, se usa el del banco.
"""

import asyncio
import logging
from dataclasses import dataclass

from app.modules.agent.interface import FieldStatus, FinancialField
from app.modules.banking.service import format_date_es
from app.modules.conversation.schemas import StoredStatement
from app.modules.statements.parser import MAX_PDF_BYTES, parse_statement
from app.modules.statements.schemas import StatementError
from app.modules.statements.validation import validate_statement
from app.modules.whatsapp.media import MediaFetcher
from app.modules.whatsapp.schemas import IncomingMessage
from app.shared.exceptions import IntegrationError, InvalidRequestError

logger = logging.getLogger(__name__)

PDF_CONTENT_TYPES = frozenset({"application/pdf"})
DOWNLOAD_TIMEOUT_S = 5.0
PARSE_TIMEOUT_S = 4.0

# Textos al usuario. Pendientes de validación de tono por C.
RECEIVED_REPLY = (
    "Recibí tu estado de cuenta de {mes}. Cuando quieras, partimos por la fecha de pago."
)
RECEIVED_WITH_ISSUES_REPLY = (
    "Recibí tu estado de cuenta de {mes}. Algunos datos no se leen con claridad; "
    "te voy a contar solo los que puedo confirmar."
)
UNREADABLE_REPLY = (
    "No pude leer ese archivo. ¿Me lo puedes enviar de nuevo? "
    "Tiene que ser el PDF de tu estado de cuenta."
)
DOWNLOAD_FAILED_REPLY = "No alcancé a abrir tu archivo. ¿Me lo envías de nuevo en un momento?"


@dataclass(frozen=True)
class IntakeResult:
    reply: str
    statement: StoredStatement | None
    outcome: str  # "ok" | "ok_with_issues" | "unreadable" | "download_failed"


def pdf_attachment(message: IncomingMessage):
    """El primer adjunto PDF del mensaje, o None."""
    for media in message.media:
        if media.content_type.split(";")[0].strip().lower() in PDF_CONTENT_TYPES:
            return media
    return None


async def receive_statement(fetcher: MediaFetcher, url: str, ctx: str) -> IntakeResult:
    try:
        pdf = await asyncio.wait_for(fetcher.fetch(url, MAX_PDF_BYTES), DOWNLOAD_TIMEOUT_S)
    except (TimeoutError, IntegrationError) as exc:
        logger.warning("Statement download failed %s: %s", ctx, type(exc).__name__)
        return IntakeResult(DOWNLOAD_FAILED_REPLY, None, "download_failed")
    except InvalidRequestError as exc:
        logger.warning("Statement rejected %s: %s", ctx, type(exc).__name__)
        return IntakeResult(UNREADABLE_REPLY, None, "unreadable")

    try:
        parsed = await asyncio.wait_for(asyncio.to_thread(parse_statement, pdf), PARSE_TIMEOUT_S)
    except (StatementError, TimeoutError) as exc:
        logger.warning("Statement unreadable %s: %s", ctx, type(exc).__name__)
        return IntakeResult(UNREADABLE_REPLY, None, "unreadable")

    confirmed = [f for f in parsed.fields.values() if f.status is FieldStatus.CONFIRMED]
    if not confirmed:
        logger.warning("Statement has no readable fields %s", ctx)
        return IntakeResult(UNREADABLE_REPLY, None, "unreadable")

    issues = validate_statement(parsed) + parsed.warnings
    statement = StoredStatement(
        fields=parsed.fields,
        statement_date=parsed.statement_date,
        period_end=parsed.period_end,
        issues=issues,
    )
    reference = parsed.period_end or parsed.statement_date
    month = format_date_es(reference).split(" de ")[1] if reference else "este mes"
    unclear = issues or len(confirmed) < len(parsed.fields)
    logger.info(
        "Statement received %s fields=%d confirmed=%d issues=%s",
        ctx,
        len(parsed.fields),
        len(confirmed),
        ",".join(issues) or "-",
    )
    template = RECEIVED_WITH_ISSUES_REPLY if unclear else RECEIVED_REPLY
    return IntakeResult(
        template.format(mes=month), statement, "ok_with_issues" if unclear else "ok"
    )


def merge_sources(
    bank: dict[str, FinancialField], statement: StoredStatement | None
) -> dict[str, FinancialField]:
    """Une el estado de cuenta (manda) con el banco. Ver docstring del módulo."""
    if statement is None:
        return dict(bank)
    merged = dict(bank)
    for key, doc in statement.fields.items():
        other = bank.get(key)
        if doc.status is FieldStatus.ABSENT:
            if other is None or other.status is not FieldStatus.CONFIRMED:
                merged[key] = doc
            continue
        if (
            doc.status is FieldStatus.CONFIRMED
            and other is not None
            and other.status is FieldStatus.CONFIRMED
            and other.value != doc.value
        ):
            both_ints = isinstance(doc.value, int) and isinstance(other.value, int)
            merged[key] = FinancialField(
                value=[doc.value, other.value] if both_ints else None,
                status=FieldStatus.UNRELIABLE,
            )
            continue
        merged[key] = doc
    return merged
