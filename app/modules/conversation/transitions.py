"""Tabla de transiciones de modo (T06).

El código es dueño del estado: el agente devuelve una etiqueta y este módulo
decide el modo. Las reglas vienen de T01 y están documentadas en
`docs/maquina-de-estados.md`.

Reglas anti-bucle aplicadas acá:
- AB2: una oferta de "¿seguimos?" por escalón.
- AB6: los escalones solo suben. El único camino hacia abajo es
  PAUSA_RECHAZO o APOYO → E0, y solo si ella retoma.
- AB7: un límite se dice una vez.
- AB8: dos fallos de reconocimiento seguidos habilitan las opciones
  numeradas una vez; a un acuse vacío se responde una sola vez.

AB3 y AB4 no necesitan código: Contigo no escribe primero y no hay nada
fuera del webhook. Si alguna vez se agrega el recontacto (P1), vuelven acá.
"""

from app.modules.agent.interface import ConversationEvent, ConversationMode, Intent

# La Escalera: el recorrido que sube de a un dato por vez. E4 no está porque
# no se llega por avance, requiere que ella nombre un monto.
ESCALERA = (
    ConversationMode.GREETING,
    ConversationMode.DUE_DATE,
    ConversationMode.BALANCE_OVERVIEW,
    ConversationMode.MINIMUM_BREAKDOWN,
)

# Modos que son un desvío: se sale de ellos solo si ella retoma (AB6).
DESVIOS = (ConversationMode.PAUSE, ConversationMode.SUPPORT)

# Etiquetas que significan "sigamos mirando".
AVANCE = (Intent.INFO_REQUEST, Intent.WILLING_TO_EXPLORE)

# Modos donde AB2 permite ofrecer el cierre.
MODOS_CON_OFERTA = (
    ConversationMode.DUE_DATE,
    ConversationMode.BALANCE_OVERVIEW,
    ConversationMode.MINIMUM_BREAKDOWN,
)

LIMITE_LINEA_CRISIS = "linea_crisis"

# Acuses sin contenido: se responden una vez (AB8).
ACUSES = frozenset({
    "ok", "oka", "okey", "okay", "ya", "dale", "bueno", "listo",
    "gracias", "graciass", "gracias!", "ok gracias", "ya gracias",
    "si", "sí", "mm", "mmm", "aja", "ajá", "👍", "🙏", "ok 👍",
})


def es_acuse_vacio(texto: str) -> bool:
    """Un mensaje que acusa recibo y no pide nada."""
    limpio = texto.strip().lower().rstrip(".!")
    return limpio in ACUSES


def _siguiente_escalon(modo: ConversationMode) -> ConversationMode:
    """Un paso arriba en la Escalera, sin pasarse del último."""
    if modo not in ESCALERA:
        return ConversationMode.BALANCE_OVERVIEW
    i = ESCALERA.index(modo)
    return ESCALERA[min(i + 1, len(ESCALERA) - 1)]


def siguiente_modo(
    modo: ConversationMode,
    max_modo: ConversationMode,
    intent: str,
    event: ConversationEvent | None,
) -> ConversationMode:
    """El modo del próximo turno. No muta nada: decide y devuelve."""
    if event is ConversationEvent.CRISIS_SUPPORT_NEEDED:
        return ConversationMode.SUPPORT
    if intent == Intent.DECLINED:
        return ConversationMode.PAUSE
    if intent == Intent.INSTALLMENT_INTENT:
        return ConversationMode.INSTALLMENT_PAUSE
    if intent == Intent.CLOSING:
        return ConversationMode.CLOSING

    # AB6: el único camino hacia abajo, y solo si ella retoma.
    if intent == Intent.VOLUNTARY_RESUME:
        return ConversationMode.GREETING

    if modo in DESVIOS:
        # Pedir un dato es retomar: el texto de C le prometió "cuando
        # quieras verlo, me escribes". Sin esto, la pausa es una trampa.
        if intent in AVANCE:
            return _siguiente_escalon(max_modo)
        return modo

    if intent in AVANCE:
        return _siguiente_escalon(modo)
    return modo


def subir_maximo(max_modo: ConversationMode, modo: ConversationMode) -> ConversationMode:
    """AB6: monótono. Solo cuentan los escalones de la Escalera."""
    if modo not in ESCALERA:
        return max_modo
    if max_modo not in ESCALERA:
        return modo
    return max(max_modo, modo, key=ESCALERA.index)
