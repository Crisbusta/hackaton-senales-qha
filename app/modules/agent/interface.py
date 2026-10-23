"""Contrato compartido entre backend e IA.

Cualquier cambio en este archivo debe acordarse entre ambos integrantes antes
de implementarse. El backend depende solo de este módulo, nunca de prompts ni
de detalles del proveedor LLM.

Responsabilidades:
- El agente interpreta el mensaje y EMITE un evento (`event`).
- El backend (ConversationService) traduce eventos a preferencias con reglas
  deterministas. El agente nunca modifica preferencias directamente.
"""

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class ConversationEvent(StrEnum):
    AVOID_FINANCIAL_REVIEW = "AVOID_FINANCIAL_REVIEW"
    DECLINED_BALANCE_REVIEW = "DECLINED_BALANCE_REVIEW"
    REQUESTED_BALANCE = "REQUESTED_BALANCE"
    REQUESTED_SUMMARY = "REQUESTED_SUMMARY"
    ACCEPTED_NEXT_STEP = "ACCEPTED_NEXT_STEP"


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
    history: list[ConversationMessage] = Field(default_factory=list)
    preferences: dict[str, bool] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    message: str
    intent: str = "UNKNOWN"
    event: ConversationEvent | None = None


class Agent(Protocol):
    async def process(self, request: AgentRequest) -> AgentResponse: ...
