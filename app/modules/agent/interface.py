"""Contrato compartido entre backend e IA.

Cualquier cambio en este archivo debe acordarse entre ambos integrantes antes
de implementarse. El backend depende solo de este módulo, nunca de prompts ni
de detalles del proveedor LLM.

Responsabilidades:
- El agente interpreta el mensaje y EMITE un evento (`event`).
- El backend (ConversationService) traduce eventos a preferencias con reglas
  deterministas. El agente nunca modifica preferencias directamente.

Historia de los cambios acordados sobre este contrato:
- PR #2: se agregaron el modo de conversación, los datos del PDF con su
  estado de confianza y el evento de crisis. Sin el evento, una etiqueta
  `angustia_fuerte` no tenía forma de persistir en el backend.
- PR #3: `PAUSA`/`PAUSA_ACTIVA` pasaron a `PAUSA_RECHAZO`/`PAUSA_COMPRA`,
  porque los dos nombres se usaban al revés entre el esquema y el código.
- PR #4: los cuatro campos que el agente necesita y no tenía dónde leer
  (`max_mode_reached`, `calculations`, `previous_intent`,
  `requested_installment_calculation`).
- PR #4 (revisión de backend): los montos en pesos pasaron de `float` a
  `int`; se publicaron las etiquetas (`Intent`) porque el backend necesita
  nombrarlas para las transiciones; y la tabla de campos por modo se movió
  acá, para que backend y agente filtren con la misma fuente de verdad.
- Se eliminaron `AVOID_FINANCIAL_REVIEW` y `REQUESTED_SUMMARY`: nunca tuvieron
  una semántica acordada y nadie los emitía (acordado en #5).
"""

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class ConversationMode(StrEnum):
    """El backend es dueño del modo: el agente nunca lo decide, solo lo lee.

    Corresponde a la máquina de estados E0-E4 / PAUSA_RECHAZO / PAUSA_COMPRA / APOYO
    / CIERRE acordada para el flujo de Patricia (T01/T06).
    """

    GREETING = "E0"  # conversar, sin datos financieros todavía
    DUE_DATE = "E1"  # ve la fecha de vencimiento
    BALANCE_OVERVIEW = "E2"  # ve pago mínimo y saldo
    MINIMUM_BREAKDOWN = "E3"  # ve en qué se reparte el mínimo
    AMOUNT_SIMULATION = "E4"  # prueba un monto que ella eligió
    PAUSE = "PAUSA_RECHAZO"  # rechazó seguir, se detiene lo financiero
    INSTALLMENT_PAUSE = "PAUSA_COMPRA"  # ganas de comprar en cuotas
    SUPPORT = "APOYO"  # tras angustia fuerte: nada financiero
    CLOSING = "CIERRE"


class FieldStatus(StrEnum):
    """Estado de confianza de un dato extraído del PDF (T07)."""

    CONFIRMED = "confirmed"  # aparece en el documento, se puede decir
    ABSENT = "absent"  # no está en el documento, no se deduce
    UNRELIABLE = "unreliable"  # el documento trae valores en conflicto


class FinancialField(BaseModel):
    """Un campo de datos_pdf con su estado. `value` es una lista solo cuando
    status es UNRELIABLE (dos valores en conflicto, ninguno se elige).

    Los montos son `int`: el peso chileno no tiene decimales y con `float`
    un saldo puede llegar a redactarse como "74349,99999999999". Lo que no
    es un monto viaja como texto ya formateado (`"3 de noviembre de 2026"`,
    `"2,8 % mensual"`), porque el agente lo lee pero nunca lo opera. Si
    T10 llega a necesitar una tasa numérica, el tipo es `Decimal`, no
    `float`.
    """

    value: int | str | list[int] | None = None
    status: FieldStatus


class ConversationEvent(StrEnum):
    DECLINED_BALANCE_REVIEW = "DECLINED_BALANCE_REVIEW"
    REQUESTED_BALANCE = "REQUESTED_BALANCE"
    ACCEPTED_NEXT_STEP = "ACCEPTED_NEXT_STEP"
    # Agregado en esta propuesta: sin esto, angustia_fuerte (T05) no tiene
    # ningún efecto persistente en el backend. Al emitirlo, el backend debe
    # fijar el modo en SUPPORT y no volver a lo financiero hasta que ella
    # retome voluntariamente.
    CRISIS_SUPPORT_NEEDED = "CRISIS_SUPPORT_NEEDED"


class Intent(StrEnum):
    """Las 16 etiquetas que el modelo puede emitir (T04).

    Están acá y no solo en el agente porque el backend necesita nombrarlas:
    `previous_intent` habilita datos condicionales, `intencion_compra_cuotas`
    decide si se ofrece el cálculo de la cuota, y las transiciones de T06
    dependen de etiquetas concretas. Como strings sueltos, un typo rompería
    una transición en silencio.

    `AgentResponse.intent` sigue siendo `str` a propósito: si el modelo
    devuelve algo fuera de esta lista, el agente lo registra tal cual en la
    traza en vez de reventar. Comparar siempre contra estos nombres.
    """

    # Disparan texto fijo (capa 3): la prosa del modelo se descarta.
    DISTRESS = "angustia_fuerte"
    DECLINED = "rechazo_pausa"
    # Avance y retoma.
    VOLUNTARY_RESUME = "retoma_voluntaria"
    WILLING_TO_EXPLORE = "disposicion_explorar"
    INFO_REQUEST = "solicitud_informacion"
    CLOSING = "cierre"
    # Desvíos con tratamiento propio.
    INSTALLMENT_INTENT = "intencion_compra_cuotas"
    SHAME_GUILT = "verguenza_culpa"
    EXHAUSTED_CAREGIVER = "cuidadora_agotada"
    ASKS_ADVICE = "pide_consejo"
    # Preguntas que habilitan datos condicionales o límites.
    ASKS_BILLED_AMOUNT = "pregunta_monto_facturado"
    ASKS_DICOM = "pregunta_dicom"
    ASKS_IDENTITY = "pregunta_identidad"
    # Entrada que no se puede procesar.
    NOT_TEXT = "no_texto"
    OFF_TOPIC = "tangente"
    AMBIGUOUS = "ambiguo"


# Qué campos de datos_pdf corresponden a cada modo. Fuente de verdad única:
# el backend filtra con esta tabla antes de llamar al agente (es su
# responsabilidad) y el agente vuelve a filtrar con la misma tabla como
# defensa en profundidad. Lo que no sale de acá no entra al contexto del
# modelo, y lo que no entra al contexto no se puede filtrar.
FIELDS_BY_MODE: dict[ConversationMode, tuple[str, ...]] = {
    ConversationMode.GREETING: (),
    ConversationMode.DUE_DATE: ("vencimiento",),
    ConversationMode.BALANCE_OVERVIEW: ("vencimiento", "minimo", "saldo"),
    ConversationMode.MINIMUM_BREAKDOWN: (
        "vencimiento", "minimo", "saldo",
        "minimo_intereses", "minimo_cuotas", "minimo_abono",
    ),
    ConversationMode.AMOUNT_SIMULATION: (
        "vencimiento", "minimo", "saldo", "monto_elegido",
    ),
    ConversationMode.PAUSE: (),
    ConversationMode.INSTALLMENT_PAUSE: (),
    ConversationMode.SUPPORT: (),
    ConversationMode.CLOSING: (),
}

# Datos que no pertenecen a ningún escalón: entran solo si ella acaba de
# preguntar por ellos, y solo desde el escalón donde tienen sentido.
CONDITIONAL_FIELDS: dict[str, tuple[str, tuple[ConversationMode, ...]]] = {
    Intent.ASKS_BILLED_AMOUNT: (
        "facturado",
        (
            ConversationMode.BALANCE_OVERVIEW,
            ConversationMode.MINIMUM_BREAKDOWN,
            ConversationMode.AMOUNT_SIMULATION,
        ),
    ),
    "pregunta_intereses": (
        "tasa",
        (ConversationMode.MINIMUM_BREAKDOWN, ConversationMode.AMOUNT_SIMULATION),
    ),
}

# Si ella elige ver cuánto le sumaría la cuota al mínimo (opción 2 de
# PAUSA_COMPRA), el código agrega estos dos y ningún otro.
INSTALLMENT_CALC_FIELDS = ("minimo", "minimo_con_cuota")


def fields_for_mode(
    mode: ConversationMode,
    previous_intent: str | None = None,
    requested_installment_calculation: bool = False,
) -> list[str]:
    """Los campos de datos_pdf admisibles en este turno, en orden.

    La usan los dos lados: el backend para armar `financial_data`, el agente
    para volver a filtrar lo que recibió.
    """
    fields = list(FIELDS_BY_MODE.get(ConversationMode(mode), ()))
    if mode == ConversationMode.INSTALLMENT_PAUSE and requested_installment_calculation:
        fields += [f for f in INSTALLMENT_CALC_FIELDS if f not in fields]
    conditional = CONDITIONAL_FIELDS.get(previous_intent or "")
    if conditional:
        field, valid_modes = conditional
        if mode in valid_modes and field not in fields:
            fields.append(field)
    return fields


# Modos en los que el agente puede ver resultados del simulador. En los
# demás, `calculations` se descarta: si no, una cifra calculada podría
# aparecer en E0 o en PAUSA_COMPRA, que es exactamente la fuga que el
# filtro por modo evita.
MODES_WITH_CALCULATIONS = (ConversationMode.AMOUNT_SIMULATION,)


def calculations_allowed(
    mode: ConversationMode, requested_installment_calculation: bool = False
) -> bool:
    if mode in MODES_WITH_CALCULATIONS:
        return True
    return (
        mode == ConversationMode.INSTALLMENT_PAUSE
        and requested_installment_calculation
    )


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

    # Ventana corta (1-2 turnos) solo para que la redacción suene natural y
    # para resolver referencias ("sí", "la segunda").
    # NUNCA es la fuente de una cifra: las únicas cifras autorizadas son las
    # de `financial_data` y `calculations`. Si el validador encuentra un
    # número que no está en ninguna de las dos, se descarta sin mirar de
    # dónde lo sacó; por eso el agente inyecta este historial con los montos
    # enmascarados.
    recent_history: list[ConversationMessage] = Field(default_factory=list)

    # Estado anti-bucle que el backend ya calculó para este turno.
    can_offer_continue: bool = False  # AB2: una oferta por escalón
    can_clarify_route: bool = False  # AB1: una aclaración por tema
    already_stated_limits: list[str] = Field(default_factory=list)  # AB7

    # El escalón más alto que ella alcanzó en la conversación (AB6: solo
    # sube). Permite que `retoma_voluntaria` reconozca lo que ya vio
    # ("la vez pasada miramos tu pago mínimo") sin repetir las cifras.
    # None equivale a GREETING: nunca pasó de conversar.
    max_mode_reached: ConversationMode | None = None

    # Resultados del simulador (T10), ya calculados. El agente los redacta
    # pero nunca los recalcula: las cifras financieras no pasan por el LLM.
    # Montos en pesos y plazos en meses, los dos `int`.
    # Las claves son libres porque T10 todavía no está implementado; cuando
    # exista, se cierran aquí.
    calculations: dict[str, int] = Field(default_factory=dict)

    # Etiqueta del turno anterior. Habilita los datos condicionales que no
    # pertenecen a ningún escalón: `monto_facturado` solo entra si ella
    # acaba de preguntar por él, y la tasa solo si preguntó por intereses.
    previous_intent: str | None = None

    # True cuando ella eligió ver cuánto le sumaría la cuota al mínimo
    # (opción 2 de PAUSA_COMPRA). Sin esto, PAUSA_COMPRA no recibe ninguna
    # cifra y el agente no puede responder a lo que ella pidió.
    requested_installment_calculation: bool = False

    preferences: dict[str, bool] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    message: str
    intent: str = "UNKNOWN"
    event: ConversationEvent | None = None
    # Solo relevante si intent == "intencion_compra_cuotas": si es esencial
    # (remedios o similar), el backend no debe ofrecer pausa ni cálculo.
    essential: bool | None = None
    # Solo si ella mencionó una cifra explícita; nunca calculada por el
    # agente. En pesos, sin decimales.
    mentioned_amount: int | None = None

    # True cuando `message` NO lo redactó el modelo: es uno de los textos
    # fijos que validó C (crisis, rechazo, no_texto) o el respaldo del modo.
    # El backend no debe reescribirlo ni filtrarlo.
    #
    # Existe por un fallo real encontrado en el recorrido end-to-end: el
    # texto de crisis contiene "*4141" y "131", el filtro de cifras de
    # ConversationService los leyó como montos, y un mensaje de ideación
    # suicida recibió "Dejemos los números de lado por ahora" en vez de la
    # línea de ayuda.
    protected_text: bool = False


class Agent(Protocol):
    async def process(self, request: AgentRequest) -> AgentResponse: ...
