"""Contrato compartido entre backend e IA.

Cualquier cambio en este archivo debe acordarse entre ambos integrantes antes
de implementarse. El backend depende solo de este módulo, nunca de prompts ni
de detalles del proveedor LLM.

Responsabilidades:
- El agente interpreta el mensaje y EMITE un evento (`event`).
- El backend (ConversationService) traduce eventos a preferencias con reglas
  deterministas. El agente nunca modifica preferencias directamente.

PROPUESTA (rama feat/t02-agent-contract-safety, pendiente de acuerdo con B):
la versión anterior de este contrato no tenía forma de representar el modo
de conversación (E0-E4/PAUSA/PAUSA_ACTIVA/APOYO/CIERRE), los datos del PDF
con su estado de confianza, ni una señal de crisis. Sin eso, el backend no
tiene cómo saber que debe entrar en modo de apoyo y quedarse ahí los
siguientes turnos: el texto saldría bien redactado pero el sistema no lo
sostendría. Ver el comentario en el PR para el detalle completo.
"""

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class ConversationMode(StrEnum):
    """El backend es dueño del modo: el agente nunca lo decide, solo lo lee.

    Corresponde a la máquina de estados E0-E4 / PAUSA / PAUSA_ACTIVA / APOYO
    / CIERRE acordada para el flujo de Patricia (T01/T06).
    """

    GREETING = "E0"  # conversar, sin datos financieros todavía
    DUE_DATE = "E1"  # ve la fecha de vencimiento
    BALANCE_OVERVIEW = "E2"  # ve pago mínimo y saldo
    MINIMUM_BREAKDOWN = "E3"  # ve en qué se reparte el mínimo
    AMOUNT_SIMULATION = "E4"  # prueba un monto que ella eligió
    PAUSE = "PAUSA"  # rechazó seguir, se detiene lo financiero
    INSTALLMENT_PAUSE = "PAUSA_ACTIVA"  # ganas de comprar en cuotas
    SUPPORT = "APOYO"  # tras angustia fuerte: nada financiero
    CLOSING = "CIERRE"


class FieldStatus(StrEnum):
    """Estado de confianza de un dato extraído del PDF (T07)."""

    CONFIRMED = "confirmed"  # aparece en el documento, se puede decir
    ABSENT = "absent"  # no está en el documento, no se deduce
    UNRELIABLE = "unreliable"  # el documento trae valores en conflicto


class FinancialField(BaseModel):
    """Un campo de datos_pdf con su estado. `value` es una lista solo cuando
    status es UNRELIABLE (dos valores en conflicto, ninguno se elige)."""

    value: float | str | list[float] | None = None
    status: FieldStatus


class ConversationEvent(StrEnum):
    AVOID_FINANCIAL_REVIEW = "AVOID_FINANCIAL_REVIEW"
    DECLINED_BALANCE_REVIEW = "DECLINED_BALANCE_REVIEW"
    REQUESTED_BALANCE = "REQUESTED_BALANCE"
    REQUESTED_SUMMARY = "REQUESTED_SUMMARY"
    ACCEPTED_NEXT_STEP = "ACCEPTED_NEXT_STEP"
    # Agregado en esta propuesta: sin esto, angustia_fuerte (T05) no tiene
    # ningún efecto persistente en el backend. Al emitirlo, el backend debe
    # fijar el modo en SUPPORT y no volver a lo financiero hasta que ella
    # retome voluntariamente.
    CRISIS_SUPPORT_NEEDED = "CRISIS_SUPPORT_NEEDED"


# Claves de preferencias conocidas. Usar estas constantes, no strings sueltos.
# False => el usuario pidió explícitamente no ver cifras; el agente no debe
# mostrar montos ni insistir.
PREF_SHOW_FINANCIAL_FIGURES = "show_financial_figures"


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AgentRequest(BaseModel):
    user_id: str
    message: str
    mode: ConversationMode

    # Solo los campos de datos_pdf que corresponden al modo actual (T06 los
    # filtra antes de llamar al agente). Lo que no está aquí, el agente no
    # lo puede mencionar: es la inyección mínima de datos.
    financial_data: dict[str, FinancialField] = Field(default_factory=dict)

    # Ventana corta (1-2 turnos) solo para que la redacción suene natural.
    # NUNCA es la fuente de una cifra: las cifras solo salen de
    # financial_data. Si el validador encuentra un número que no está en
    # financial_data, se descarta sin mirar de dónde lo sacó.
    recent_history: list[ConversationMessage] = Field(default_factory=list)

    # Estado anti-bucle que el backend ya calculó para este turno.
    can_offer_continue: bool = False  # AB2: una oferta por escalón
    can_clarify_route: bool = False  # AB1: una aclaración por tema
    already_stated_limits: list[str] = Field(default_factory=list)  # AB7

    preferences: dict[str, bool] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    message: str
    intent: str = "UNKNOWN"
    event: ConversationEvent | None = None
    # Solo relevante si intent == "intencion_compra_cuotas": si es esencial
    # (remedios o similar), el backend no debe ofrecer pausa ni cálculo.
    essential: bool | None = None
    # Solo si ella mencionó una cifra explícita; nunca calculada por el agente.
    mentioned_amount: float | None = None


class Agent(Protocol):
    async def process(self, request: AgentRequest) -> AgentResponse: ...
