"""Agente simulado: respuestas fijas por palabras clave, sin APIs externas.

Permite probar backend y WhatsApp mientras se desarrolla el agente real.
Nunca muestra cifras: no tiene datos verificables de dónde sacarlas.
"""

import unicodedata

from app.modules.agent.interface import (
    PREF_SHOW_FINANCIAL_FIGURES,
    AgentRequest,
    AgentResponse,
    ConversationEvent,
)

# El orden importa: una negativa explícita gana sobre una solicitud
# ("no quiero saber cuánto debo" es una negativa).
_DECLINE = ("prefiero no", "no quiero", "todavia no", "ahora no", "no estoy lista")
_REQUEST_BALANCE = ("cuanto debo", "cuanto es mi deuda", "mi saldo", "cuanto tengo que pagar")
_FEAR = ("miedo", "me asusta", "me angustia", "me da susto", "ansiedad")
_GREETING = ("hola", "buenas", "buenos dias", "buenas tardes", "buenas noches")

DECLINE_REPLY = (
    "Está bien, no vamos a mirar números ahora. Tú decides el ritmo. "
    "Cuando quieras retomarlo, solo dímelo."
)
FEAR_REPLY = (
    "Es muy comprensible sentir eso; a mucha gente le pasa. "
    "Podemos ir de a poco, sin mirar cifras si no quieres. "
    "¿Te gustaría que partamos por algo pequeño, como saber cuándo vence tu tarjeta?"
)
REQUEST_BALANCE_REPLY = (
    "Claro. Para decirte el monto exacto necesito tu estado de cuenta: "
    "puedes enviármelo aquí como PDF y lo revisamos juntas, paso a paso."
)
GREETING_REPLY = (
    "Hola 👋 Soy Contigo. Estoy aquí para acompañarte con tus finanzas, a tu ritmo. "
    "¿Cómo te sientes hoy con tus cuentas?"
)
GREETING_AFTER_DECLINE_REPLY = (
    "Hola de nuevo 👋 Aquí estoy. No tenemos que mirar números hoy; "
    "conversemos de lo que tú quieras."
)
DEFAULT_REPLY = "Te leo. ¿Me cuentas un poco más para poder ayudarte mejor?"


def _normalize(text: str) -> str:
    without_accents = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(without_accents.lower().split())


def _contains(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


class MockAgent:
    """Implementa el protocolo `Agent` de interface.py."""

    async def process(self, request: AgentRequest) -> AgentResponse:
        text = _normalize(request.message)
        declined_before = request.preferences.get(PREF_SHOW_FINANCIAL_FIGURES) is False

        if _contains(text, _DECLINE):
            return AgentResponse(
                message=DECLINE_REPLY,
                intent="rechazo_pausa",
                event=ConversationEvent.DECLINED_BALANCE_REVIEW,
            )
        if _contains(text, _REQUEST_BALANCE):
            # Solicitud explícita actual: prevalece sobre una negativa anterior.
            return AgentResponse(
                message=REQUEST_BALANCE_REPLY,
                intent="solicitud_informacion",
                event=ConversationEvent.REQUESTED_BALANCE,
            )
        if _contains(text, _FEAR):
            return AgentResponse(message=FEAR_REPLY, intent="expresa_temor")
        if _contains(text, _GREETING):
            reply = GREETING_AFTER_DECLINE_REPLY if declined_before else GREETING_REPLY
            return AgentResponse(message=reply, intent="saludo")
        return AgentResponse(message=DEFAULT_REPLY)
