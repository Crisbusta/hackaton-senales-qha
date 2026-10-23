"""Qué cifras entran al contexto del agente en este turno (T09).

Une dos piezas que ya existían y que nadie había conectado: el adaptador de
datos bancarios (`banking.credit_card_fields`, de B) y el filtro por modo
del contrato (`interface.fields_for_mode`, acordado en #4). Estaban escritas
para encajar; esto es la junta.

Tres invariantes:

- Lo que no autoriza `fields_for_mode` no entra. Lo que no entra al contexto
  del modelo no se puede filtrar después.
- Un dato que no tenemos viaja como `ABSENT`, nunca omitido del diccionario.
  Es lo que hace posible C03: el agente puede decir "esa fecha no aparece en
  el documento" porque recibió la clave con su estado, en vez de no recibir
  nada y tener que adivinar si existe.
- Las cifras las produce el código. Acá no se calcula nada: se eligen y se
  copian. El único valor que este módulo agrega es `monto_elegido`, que es
  el monto que ella misma nombró.
"""

from app.modules.agent.interface import (
    ConversationMode,
    FieldStatus,
    FinancialField,
    fields_for_mode,
)
from app.modules.banking.schemas import CreditCard
from app.modules.banking.service import credit_card_fields

ABSENT_FIELD = FinancialField(value=None, status=FieldStatus.ABSENT)


def build_financial_data(
    card: CreditCard | None,
    *,
    mode: ConversationMode,
    previous_intent: str | None = None,
    requested_installment_calculation: bool = False,
    chosen_amount: int | None = None,
) -> dict[str, FinancialField]:
    """Los campos admisibles en este turno, cada uno con su estado.

    `card` es None cuando ella no tiene banco vinculado: entonces todo lo
    admisible sale `ABSENT`. No tener datos y tener datos que no se pueden
    mostrar son cosas distintas, y esta función solo responde la primera;
    el consentimiento lo verifica `ConversationService` antes de llamar.
    """
    allowed = fields_for_mode(mode, previous_intent, requested_installment_calculation)
    if not allowed:
        return {}

    available = credit_card_fields(card) if card is not None else {}
    if chosen_amount is not None:
        # No es un cálculo: es su propio monto, de vuelta. E4 lo necesita
        # para poder hablar de "los $60.000 que me dijiste".
        available["monto_elegido"] = FinancialField(
            value=chosen_amount, status=FieldStatus.CONFIRMED
        )

    data: dict[str, FinancialField] = {}
    for name in allowed:
        field = available.get(name)
        data[name] = field if field is not None else ABSENT_FIELD
    return data
