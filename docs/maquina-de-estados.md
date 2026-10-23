# Máquina de estados: transiciones y reglas anti-bucle

Documento de trabajo para T06. Responde a la pregunta de la revisión de
#4: *¿qué etiqueta lleva a qué modo, y dónde están las reglas AB?*

**El código es dueño del estado, el modelo es dueño de las palabras.** El
agente nunca decide una transición: lee `mode` y devuelve una etiqueta.
Quien aplica esta tabla es `ConversationService` (T06).

Las etiquetas están publicadas en `interface.Intent`. Los modos, en
`interface.ConversationMode`.

---

## 1. Transiciones acordadas

| Etiqueta recibida | Modo resultante | Nota |
|---|---|---|
| `angustia_fuerte` | `APOYO` | También si la dispara el pre-filtro, sin pasar por el modelo. |
| `rechazo_pausa` | `PAUSA_RECHAZO` | |
| `intencion_compra_cuotas` | `PAUSA_COMPRA` | Si `essential` es `true`, no se ofrece pausa ni cálculo. |
| `retoma_voluntaria` | `E0` | Única salida de `APOYO` y de las dos pausas. Conserva `max_mode_reached`. |
| `disposicion_explorar` | el escalón siguiente | `E0→E1→E2→E3`. |
| `solicitud_informacion` | el escalón donde vive el dato que pidió | Ver el orden de operaciones, abajo. |
| `cierre` | `CIERRE` | |

`E4` es el único escalón al que no se llega por avance: requiere que ella
haya nombrado un monto (`mentioned_amount`), que entra como
`monto_elegido`.

**No cambian el modo** — se responden dentro del modo actual:
`verguenza_culpa`, `cuidadora_agotada`, `tangente`, `pide_consejo`,
`pregunta_dicom`, `pregunta_identidad`, `pregunta_monto_facturado`,
`no_texto`, `ambiguo`.

### Orden de operaciones (importa)

El escalón se sube **antes** de llamar al agente, no después. Si ella
pregunta por un dato del escalón siguiente, T06 sube el modo y recién
entonces llama: así el dato ya viaja en `financial_data` y el modelo nunca
tiene que esquivar la pregunta. Al revés, el turno sale evasivo y el
escalón se rompe.

---

## 2. Reglas anti-bucle

Cada una es un campo del `AgentRequest` que T06 calcula. El agente las
obedece porque las ve en el contexto; no las deduce.

| Regla | Qué impide | Campo del contrato |
|---|---|---|
| AB1 | Más de una aclaración de ruta por tema | `can_clarify_route` |
| AB2 | Más de una oferta de continuar por escalón | `can_offer_continue` |
| AB5 | Salir de `APOYO` por cualquier vía que no sea `retoma_voluntaria` | el modo, que T06 no mueve |
| AB7 | Repetir un límite ya dicho (p. ej. ofrecer el *4141 dos veces) | `already_stated_limits` |

Hay dos comportamientos más ya implementados a los que **no les puedo
poner número con certeza**, y prefiero decirlo antes que inventarlo:

- `max_mode_reached` **solo sube**. Permite que `retoma_voluntaria`
  reconozca lo que ella ya vio sin repetir cifras. En `interface.py` lo
  anoté como AB6, pero ese número es mi suposición.
- Las cifras de un escalón no reaparecen en otro: lo garantiza el filtro
  `fields_for_mode()`, no una regla AB.

**AB3, AB4, AB6 y AB8 no están escritas en ninguna parte a la que yo tenga
acceso.** C es dueño de la lista (T01). Hasta que estén, T06 no debería
asumir un comportamiento para ellas.

---

## 3. Qué lado hace qué

| Responsabilidad | Dueño |
|---|---|
| `mode` y sus transiciones | T06 |
| `max_mode_reached` | T06 |
| `can_offer_continue`, `can_clarify_route` | T06 |
| `already_stated_limits` | T06 |
| `previous_intent`, `requested_installment_calculation` | T06 |
| Filtrar `financial_data` por modo | T06, con `interface.fields_for_mode()` |
| Volver a filtrar como defensa en profundidad | el agente, con la misma función |
| Emitir la etiqueta y redactar | el agente |
| Calcular cualquier cifra | T10, nunca el modelo |
