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

from app.modules.agent.interface import (
    LIMIT_CRISIS_LINE,
    ConversationEvent,
    ConversationMode,
    Intent,
)

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

LIMITE_LINEA_CRISIS = LIMIT_CRISIS_LINE

# Acuses sin contenido: se responden una vez (AB8).
# Solo cortesia. "si", "ya", "dale", "bueno" y "listo" NO estan: en esta
# conversacion casi siempre son la respuesta a una pregunta ("¿seguimos?"),
# no un acuse. Con ellas aca, dos afirmaciones seguidas dejaban a Patricia
# sin ninguna respuesta, y para ella el bot se habia caido.
ACUSES = frozenset({
    "ok", "oka", "okey", "okay",
    "gracias", "graciass", "gracias!", "ok gracias", "ya gracias",
    "mm", "mmm", "aja", "ajá", "👍", "🙏", "ok 👍",
})


def es_acuse_vacio(texto: str) -> bool:
    """Un mensaje que acusa recibo y no pide nada."""
    limpio = texto.strip().lower().rstrip(".!")
    return limpio in ACUSES


def _siguiente_escalon(modo: ConversationMode) -> ConversationMode:
    """Un paso arriba en la Escalera, sin pasarse del último y sin bajar.

    E4 no está en la Escalera (no se llega por avance, requiere que ella
    nombre un monto), así que caía en el default y un avance desde E4 la
    devolvía a E2: medido el 23 oct, "¿y si pago 100 mil?" desde E3 ponía el
    modo en E4 y la transición del final del turno lo bajaba a E2. Desde un
    modo fuera de la Escalera, avanzar es quedarse.
    """
    if modo is ConversationMode.AMOUNT_SIMULATION:
        return modo
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

    if modo is ConversationMode.INSTALLMENT_PAUSE:
        # "El escalón donde vive el dato que pidió": estando en PAUSA_COMPRA,
        # lo que ella pide es el efecto de la cuota en su mínimo, y ese dato
        # vive en este modo (INSTALLMENT_CALC_FIELDS). Sin esta rama caía en
        # `_siguiente_escalon`, que para un modo fuera de la Escalera devuelve
        # E2: en C05, el "muéstrame" del turno 2 la movía a ver saldo y mínimo
        # —que no es lo que preguntó— y al salir de PAUSA_COMPRA el servicio
        # apagaba `installment_calc_requested`, así que el turno 3, donde va
        # la cifra de la cuota, llegaba sin el cálculo y sin el hilo de la
        # compra.
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
