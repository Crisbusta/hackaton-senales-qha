"""Cifras de un turno: qué datos se precargan y qué montos puede contener la respuesta.

Precarga + verificación posterior:
- En los escalones (E0-E4) el agente recibe todos los datos de la Escalera,
  para poder responder en el mismo turno lo que ella pide.
- Después de la transición, el backend verifica que la respuesta solo traiga
  montos del modo en que terminó el turno. Una cifra de más no llega a ella.
Fuera de los escalones (pausas, APOYO, CIERRE) no hay precarga: rige
`fields_for_mode()` tal cual.
"""

import re
from collections.abc import Iterable

from app.modules.agent.interface import (
    CONDITIONAL_FIELDS,
    FIELDS_BY_MODE,
    ConversationMode,
    FinancialField,
    fields_for_mode,
)

LADDER_MODES = (
    ConversationMode.GREETING,
    ConversationMode.DUE_DATE,
    ConversationMode.BALANCE_OVERVIEW,
    ConversationMode.MINIMUM_BREAKDOWN,
    ConversationMode.AMOUNT_SIMULATION,
)

# Todo lo que algún escalón (o una pregunta condicional) puede llegar a mostrar.
PRELOAD_FIELDS: tuple[str, ...] = tuple(
    dict.fromkeys(
        [field for mode in LADDER_MODES for field in FIELDS_BY_MODE[mode]]
        + [field for field, _ in CONDITIONAL_FIELDS.values()]
    )
)

# Montos tipo "$74.350", "$ 6", "74.350" o "74350". Conservador a propósito:
# ante la duda, se trata como monto.
_AMOUNT = re.compile(r"\$\s*(\d{1,3}(?:\.\d{3})+|\d+)|(\d{1,3}(?:\.\d{3})+)|(\d{4,})")


def amounts_in(text: str) -> set[int]:
    return {int((a or b or c).replace(".", "")) for a, b, c in _AMOUNT.findall(text)}


def fields_to_send(
    mode: ConversationMode, previous_intent: str | None, requested_installment: bool = False
) -> tuple[str, ...]:
    """Campos que viajan al agente en este turno."""
    if mode in LADDER_MODES:
        return PRELOAD_FIELDS
    return tuple(fields_for_mode(mode, previous_intent, requested_installment))


def allowed_fields(
    modes: Iterable[ConversationMode],
    intents: Iterable[str | None],
    requested_installment: bool = False,
) -> set[str]:
    """Campos que la respuesta puede mencionar.

    `modes`: el modo con el que se llamó al agente (el backend pudo subirlo
    antes, p. ej. a E4) y el modo en que terminó el turno. `intents`: la
    etiqueta de este turno y la del anterior, porque una pregunta condicional
    (facturado, tasa) habilita su dato en el turno en que ella la hace.
    """
    return {
        field
        for mode in modes
        for intent in intents
        for field in fields_for_mode(mode, intent, requested_installment)
    }


def allowed_amounts(
    data: dict[str, FinancialField],
    fields: Iterable[str],
    extra_texts: Iterable[str] = (),
    calculations: dict[str, int] | None = None,
) -> set[int]:
    """Montos permitidos: los de los campos autorizados y los que ella misma escribió.

    Incluye los números dentro de textos ya formateados (el año de
    "3 de noviembre de 2026"), para no confundirlos con montos.
    """
    amounts: set[int] = set()
    for name in fields:
        field = data.get(name)
        if field is None or field.value is None:
            continue
        values = field.value if isinstance(field.value, list) else [field.value]
        for value in values:
            amounts |= {int(value)} if isinstance(value, int | float) else amounts_in(str(value))
    for text in extra_texts:
        amounts |= amounts_in(text)
    amounts |= set((calculations or {}).values())
    return amounts


def unexpected_amounts(reply: str, allowed: set[int]) -> set[int]:
    return amounts_in(reply) - allowed
