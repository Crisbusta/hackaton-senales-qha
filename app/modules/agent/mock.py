"""Agente simulado: el contrato sin proveedor LLM.

Sirve para que el backend y sus tests no dependan de la red ni de tokens, y
para la demo sin costo. Clasifica con reglas obvias y redacta con los textos
que el modo permite.

Las etiquetas que emite tienen que existir en `Intent`: un doble que emita
una etiqueta imposible hace pasar tests que el modelo real no podria pasar.
"""

from app.modules.agent.interface import (
    AgentReply,
    Classification,
    ClassifyRequest,
    ConversationEvent,
    ConversationMode,
    Intent,
    WriteRequest,
)
from app.modules.agent.texts import TEXTOS_FIJOS, TEXTOS_RESPALDO

_SALUDOS = ("hola", "buenas", "buenos dias", "buenas tardes", "buenas noches")
_RECHAZO = ("no quiero", "no puedo verlo", "prefiero no", "mejor no",
            "despues", "mas tarde")
_CRISIS = ("no quiero seguir viviendo", "me quiero morir", "matarme")
_CUOTAS = ("cuotas", "comprar")
_INFO = ("cuanto", "cuando", "vence", "minimo", "saldo", "debo")
_CIERRE = ("gracias, chao", "chao", "nos vemos")


def _normalizar(texto: str) -> str:
    return " ".join(texto.lower().split())


class MockAgent:
    """Implementa el protocolo `Agent` sin salir a la red."""

    async def classify(self, request: ClassifyRequest) -> Classification:
        texto = _normalizar(request.message)
        if any(f in texto for f in _CRISIS):
            return Classification(
                intent=Intent.DISTRESS,
                event=ConversationEvent.CRISIS_SUPPORT_NEEDED,
            )
        if any(f in texto for f in _RECHAZO):
            return Classification(
                intent=Intent.DECLINED,
                event=ConversationEvent.DECLINED_BALANCE_REVIEW,
            )
        if any(f in texto for f in _CUOTAS):
            return Classification(intent=Intent.INSTALLMENT_INTENT, essential=False)
        if any(texto.startswith(s) for s in _SALUDOS):
            return Classification(intent=Intent.GREETING_ONLY)
        if any(f in texto for f in _CIERRE):
            return Classification(intent=Intent.CLOSING)
        if any(f in texto for f in _INFO):
            return Classification(
                intent=Intent.INFO_REQUEST,
                event=ConversationEvent.REQUESTED_BALANCE,
            )
        return Classification(intent=Intent.AMBIGUOUS)

    async def write(self, request: WriteRequest) -> AgentReply:
        etiqueta = request.classification.intent
        if etiqueta == Intent.DISTRESS:
            return AgentReply(message=TEXTOS_FIJOS["angustia_fuerte"], protected_text=True)
        if etiqueta == Intent.DECLINED:
            return AgentReply(message=TEXTOS_FIJOS["rechazo_pausa"], protected_text=True)
        if etiqueta == Intent.NOT_TEXT:
            return AgentReply(message=TEXTOS_FIJOS["no_texto"], protected_text=True)

        modo = request.mode
        if modo in (ConversationMode.PAUSE, ConversationMode.INSTALLMENT_PAUSE,
                    ConversationMode.SUPPORT):
            # En un desvio no se saluda ni se ofrece nada: manda el modo.
            return AgentReply(message=TEXTOS_RESPALDO[modo.value])
        if etiqueta == Intent.GREETING_ONLY:
            return AgentReply(message="Hola, Patricia. Acá estoy, sin apuro.")
        if etiqueta == Intent.CLOSING:
            return AgentReply(message=TEXTOS_RESPALDO["CIERRE"])
        if modo in (ConversationMode.DUE_DATE, ConversationMode.BALANCE_OVERVIEW,
                    ConversationMode.MINIMUM_BREAKDOWN, ConversationMode.AMOUNT_SIMULATION):
            partes = [
                f"{nombre}: {campo.value}"
                for nombre, campo in request.financial_data.items()
                if campo.value is not None
            ]
            if partes:
                return AgentReply(message="Te cuento. " + "; ".join(partes) + ".")
        return AgentReply(
            message=TEXTOS_RESPALDO.get(modo.value, TEXTOS_RESPALDO["E0"])
        )
