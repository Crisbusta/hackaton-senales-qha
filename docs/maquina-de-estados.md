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
`monto_elegido`. **La regla de entrada a `E4` todavía no está escrita**: el
monto ya se guarda y ya viaja al contexto (T09), pero falta que C defina
desde qué escalones y con qué condición se abre. Hasta entonces nada lleva a
`E4`.

### Estando en `PAUSA_COMPRA`

Pedir un dato dentro de `PAUSA_COMPRA` **no sube la Escalera**: lo que ella
pide ahí es el efecto de la cuota en el mínimo, y ese dato vive en el propio
modo (`INSTALLMENT_CALC_FIELDS`). Es el caso de "el escalón donde vive el
dato que pidió" de la tabla de arriba, y lo habilita
`requested_installment_calculation`.

Se sale de `PAUSA_COMPRA` por `rechazo_pausa`, `cierre`, `retoma_voluntaria`
o una crisis; nunca por avanzar. Al salir, la solicitud del cálculo se apaga:
no sobrevive al cambio de tema (C06c).

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

| AB6 | Repetir cifras ya vistas o retroceder en la Escalera | `max_mode_reached` (monótono) |
| AB8 | El bucle de "no te entendí" y responder a cada "ok" | `recognition_failures`, `numbered_options_offered`, `acknowledgement_answered` |

C confirmó (T01, 23 oct) que **AB6 es la regla que ya estaba implementada**
—los escalones solo suben— y que no hay que renumerar nada.

**AB3 y AB4 no necesitan código hoy.** AB3 impide que Contigo ofrezca
escribir después; AB4, que escriba primero. Como no hay nada fuera del
webhook ni temporizador alguno, se cumplen por construcción. Si entra el
recontacto (P1), vuelven acá: necesitan `recontacto_solicitado`,
`recontacto_enviado` y `ultimo_mensaje_usuaria_at`, y la única salida
permitida es un mensaje, pedido por ella, dentro de las 24 h de WhatsApp y
sin deuda, montos ni fechas.

**Una nota sobre AB1 y AB8:** las dos comparten `can_clarify_route`, porque
las dos son "aclarar una sola vez". Si alguna vez necesitan
comportamientos distintos, hace falta un campo propio en el contrato.

Lo que **no** es una regla AB: que las cifras de un escalón no reaparezcan
en otro. Eso lo garantiza el filtro `fields_for_mode()`.

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

---

## 4. Estado de la integración (T09)

Lo que ya corre de punta a punta: `conversation/financial.py` arma
`financial_data` desde el banco simulado, filtrado por modo y con el
consentimiento verificado antes de consultar. `requested_installment_calculation`,
`chosen_amount`, `essential` y `mentioned_amount` están conectados.

Lo que falta, y de quién depende:

| Falta | Dueño | Efecto hoy |
|---|---|---|
| El simulador de cuotas | T10, B | `minimo_con_cuota` viaja `ABSENT`: el agente dice que no lo tiene en vez de inventarlo. **C05 no pasa hasta que exista.** |
| El número de cuotas en el contrato | A + B | `AgentResponse` trae `mentioned_amount` pero no en cuántas cuotas. Sin eso, T10 no puede calcular la cuota mensual. Es un campo y hay que acordarlo. |
| La regla de entrada a `E4` | C | Nada lleva a `E4` todavía. |
