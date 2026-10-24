# Handoff — mejora del agente y sus respuestas

**Para quién es este documento.** Para quien retome este trabajo sin haber estado en la
conversación donde se hizo. Está escrito para que se lo puedas pasar a Claude Code directamente:

> «Lee `docs/handoff.md` y ejecuta el paso 4.»

Cada paso de abajo dice qué hay que hacer, con qué comando se verifica, quién decide lo que no
puede decidir Claude, y qué trampa tiene. **No ejecutes un paso sin leer antes "Reglas que no se
rompen"**, al final.

### El primer mensaje, si quieres copiarlo

> Lee `docs/handoff.md` completo y `CLAUDE.md`. Vamos 3 de 7 pasos. Antes de tocar nada, corre
> `uv run pytest` y `git log --oneline -5` para confirmar que el estado es el que dice el
> documento. Después dime qué paso conviene hacer primero y por qué, y espera mi ok antes de
> ejecutarlo.

Ese "espera mi ok" importa: dos de los cinco pasos que quedan **no son decisiones de quien
programa** (ver pasos 5 y 6).

---

## Cómo se trabajó esto

No es ceremonia: cada regla de acá salió de un error que costó trabajo rehecho.

**Medir antes de arreglar, y medir después.** El 40 % no es una impresión, son 52 turnos
clasificados a mano. Los siete defectos del paso 1 salieron de *correr* los casos contra el modelo
real, no de leer el código: la suite pasaba con los siete adentro. Si vas a afirmar que algo
mejoró, trae el número.

**La traza antes que el código.** Cuando un turno responde mal, la respuesta está en
`turnos.jsonl`: qué modo recibió `write()`, qué campos y movimientos se inyectaron, qué devolvió
el modelo y qué validador lo rechazó. Leer el código primero hace perder tiempo adivinando.

**Si el código puede decidirlo, no va en el prompt.** Esta es *la* lección del trabajo. Cada
condicional de un prompt es una decisión delegada al modelo, y el modelo la cumple a ojo. Tres de
los siete defectos eran un movimiento que ordenaba algo que los datos no permitían. Cuando dudes,
la pregunta es "¿el backend ya sabe esto?" — y casi siempre la respuesta es sí.

**Un test por defecto, con la medición en el docstring.** Los tests de este repo dicen *qué pasó
el día que esto se rompió* ("medido el 24 oct: 327 tokens, 8,2 s, timeout 2 de 3 veces"). Sin eso,
el test de arriba parece arbitrario y alguien lo borra en seis meses.

**Decir qué no hiciste.** El defecto de la pausa (paso 5) está documentado y sin arreglar porque
la decisión es de C. Eso es un resultado, no una omisión — pero solo si queda escrito.

**`CLAUDE.md` se actualiza en el mismo PR.** Si cambias el flujo del turno, el contrato o una
limitación y no lo escribes ahí, la próxima sesión trabaja con información falsa. Pasó en este
trabajo: una sección quedó referenciada y sin escribir, y hubo que cazarla después.

**Un commit por idea, con el por qué.** Los mensajes de `6827d13` y sus commits previos son la
documentación real de las decisiones. Si alguien tiene que preguntar "¿por qué está así?", el
commit debería responderlo.

---

## De qué se trata todo esto

El bot respondía mal en **40 % de los turnos** (medidos sobre 52 reales; esos 52 turnos **no
están en el repo**, ver paso 7). Tres diagnósticos independientes coincidían en el síntoma —respuestas vagas, "eso lo vemos en un momento",
preguntas en vez de datos— y la causa era una sola: **el prompt negaba datos que ya tenía**.

Debajo de eso había una causa de arquitectura: el turno era **una sola llamada al modelo**, así
que el modelo redactaba con el modo en que el turno *empezaba* y el modo cambiaba *después*. Tres
mecanismos del código existían solo para compensar ese desfase.

**El arreglo:** el turno pasó a ser **tres pasos — clasificar → decidir → redactar**. El
clasificador solo etiqueta y no recibe ninguna cifra. El código decide el modo final, los datos y
los movimientos. El redactor solo escribe y no evalúa ninguna condición de estado, porque no le
llega ninguna. Los tres mecanismos de compensación se borraron.

El detalle del flujo está en `CLAUDE.md` → "Flujo de un turno". **Léelo antes de tocar
`conversation/service.py` o `agent/`.**

---

## Estado verificable

Corre esto antes de empezar cualquier paso. Si algo no da lo que dice acá, el repo se movió y
este documento está viejo:

```bash
uv sync
uv run pytest                    # 498 passed, 11 skipped
uv run ruff check app/ tests/    # All checks passed
git log --oneline -5
```

Lo último medido contra el modelo real (24 oct): **6 de 6 casos P0 sin fallas automáticas**, sin
un solo turno con texto de respaldo. Peor turno punta a punta, 10,7 s (Twilio corta a ~15).

Para repetir esa medición:

```bash
uv run python -m scripts.p0_runner --agent llm --report resultados.md   # gasta tokens de verdad
uv run python -m scripts.p0_runner --agent mock                        # sin costo, sin modelo
```

---

## El roadmap: 7 pasos, 3 cerrados

### ✅ 1. Correr los casos P0 contra el modelo real

Hecho. Sacó **siete defectos** que la suite no veía, porque ninguno es un bug aislado: son
contradicciones entre lo que el código manda y lo que el prompt ordena, o restos del orden viejo
del turno. Los dos peores:

- El texto de crisis que pregunta **"¿estás a salvo?" era inalcanzable**. La condición era
  `modo == APOYO`, y con el turno en tres pasos el modo que llega al redactor es el FINAL: una
  crisis *siempre* lo pone en APOYO. Lo que distingue la primera detección del refuerzo no es el
  modo, es si las líneas ya se dijeron.
- **AB2 contaba la oferta en el escalón equivocado**: la oferta del turno que subía de E1 a E2
  quedaba anotada en E1, así que E2 volvía a preguntar "¿Seguimos o lo dejamos aquí?".

Los siete están en el mensaje del commit `0cee54a`, cada uno con su medición.

### ✅ 2. Probar fuera del guion

Hecho, por el webhook firmado. Escalera completa, pausa y retomar, compra en cuotas (aritmética
verificada: 250.000/12 = 20.834 → mínimo 95.184), crisis, audio, identidad, vergüenza, cansancio.

Herramientas para repetirlo, en `scripts/`. **Hablan con el webhook local firmando como lo hace
Twilio**, así que ejercitan el camino completo —firma, dedup, estado, los dos pasos del agente,
TwiML— y no un atajo:

```bash
# 1. el servidor, con la traza encendida (sin ella estás depurando a ciegas)
AGENT_TRACE_ENABLED=true AGENT_TRACE_PATH=turnos.jsonl uv run uvicorn app.main:app --port 8000

# 2. una conversación completa: cada argumento es un mensaje de ella, en orden
uv run python -m scripts.conv "/reiniciar" "hola" "cuanto debo" "y si pago 100 mil"

# un solo mensaje
uv run python -m scripts.wa "cuanto debo?"

# el proveedor a secas, sin Twilio ni webhook: para probar la llave y el esquema
uv run python scripts/smoke_openai.py "cuanto es mi pago minimo?" E2
```

`/reiniciar` borra tu estado y solo funciona desde un número de `DEMO_ADMIN_NUMBERS`; empieza
siempre con eso, o arrastras la conversación anterior y los modos no son los que crees.

La traza (`turnos.jsonl`) es **la herramienta de depuración**: una línea JSON por paso, con el
modo, la etiqueta, los campos inyectados, los movimientos resueltos, la salida cruda del modelo y
qué validador falló. Si un turno responde mal, la respuesta está ahí y no en el código.

### ✅ 3. Mergear el PR

Mergeado con squash en `6827d13` (PR #32). `main` corre con el turno en tres pasos.

**Pendiente humano:** avisarle a B que el contrato cambió de forma (`process()` →
`classify()` + `write()`). No es burocracia: si tiene algo a medio hacer sobre `conversation/` o
`interface.py`, se le rompe al hacer `git pull` y no va a saber por qué.

### ⬜ 4. Dos variables de consola (5 minutos, lo más urgente de la lista)

No es código y no espera a nadie:

1. **Railway** → proyecto `contigo`, servicio `api` → Variables → `DATABASE_URL` =
   `${{Postgres.DATABASE_URL}}`. Sin esto el estado vive en memoria y **cada deploy borra las
   conversaciones**. El repositorio PostgreSQL ya está mergeado (#25); solo falta la variable.
   Los valores van **sin comillas** en el campo de valor.
2. **Twilio** → Messaging → Senders → el sender de WhatsApp → ***Status callback* vacío**. Si
   tiene URL, llegan avisos de estado al webhook como mensajes sin `Body`.

Verificación: manda un mensaje por WhatsApp, redespliega, manda otro y comprueba que el bot
recuerda la conversación.

### ⬜ 5. Los textos de C (ella decide, no Claude)

C es la dueña de la voz (T01). **Ningún texto fijo se cambia sin ella**; están en
`app/modules/agent/texts.py`, `conversation/statement_intake.py` y `conversation/voice_intake.py`.
Cuatro decisiones pendientes:

1. **El respaldo de E1–E3** (`TEXTOS_RESPALDO`): hoy dice "Déjame ordenarlo bien antes de
   contártelo" — le promete un mensaje que nunca llega.
2. **Los textos del PDF y de voz** (`statement_intake.*_REPLY`, `voice_intake.*_REPLY`): son
   borradores escritos por el equipo, sin validar.
3. **`FALLBACK_REPLY`** y **`faltante_para_gastos_esenciales`**: `CLAUDE.md` dice que el agente no
   debe mencionar el faltante hasta que esté validado; un diagnóstico dice que ya está aprobado.
   **Alguien tiene que decidir cuál de los dos vale** y dejarlo escrito en `CLAUDE.md`.
4. **Qué responde el turno en que ella sale de una pausa.** Es el único defecto que se encontró y
   se dejó sin arreglar a propósito: hoy pregunta una cosa ("¿cuánto era el mínimo?") y recibe el
   mínimo, el saldo, el vencimiento y el desglose completo. Repite cifras ya vistas —justo lo que
   AB6 nombra— y va contra "de a un dato por vez". Está documentado en `CLAUDE.md` →
   "Limitaciones conocidas". **Arreglarlo bien necesita que C decida qué debe responder ese
   turno**, y probablemente etiquetas más finas que `solicitud_informacion`.

Cuando lleguen los textos: son constantes, se pegan y se corre `uv run pytest`. Media hora.

### ⬜ 6. El onboarding (solo si el equipo lo quiere)

**No necesita nada de arquitectura**: el redactor ya está listo para recibir el trato como
movimientos en firme. Falta:

- Los **siete textos** (de C).
- Un modo con un **contador de posición P1–P6** en `ConversationState`.
- Guardar las respuestas en el estado (el perfil acumulado).

Es una feature nueva, en su propia rama y su propio PR.

### ⬜ 7. Volver a medir (parcial)

**Avance:** los casos P0 se corrieron contra el modelo real en `04c50a9` y en `main`, con el mismo
criterio de abajo: 7 de 21 turnos problemáticos antes, 2 de 21 después. No es comparable con el 40 %
y el detalle (incluido un defecto nuevo en E2) está en [`medicion-despues.md`](medicion-despues.md).
**Sigue faltando** el PDF "Respuestas vagas de Contigo" con los 52 turnos. El HTML que sí se subió
(`docs/diagnostico_contigo.html`) es el diagnóstico del código, no los turnos.

El 40 % de respuestas problemáticas es el número de **antes**. Nadie ha medido el después sobre
los mismos 52 turnos. Sin esa pasada no se puede afirmar que mejoró — solo que los seis casos P0
pasan.

```bash
uv run python -m scripts.p0_runner --agent llm --report resultados_despues.md
```

Y repetir la clasificación manual de los 52 turnos con la traza encendida, igual que la primera
vez.

**Ojo: los 52 turnos del "antes" no están en este repo.** Vienen del diagnóstico que hizo el
equipo (un PDF, "Respuestas vagas de Contigo", y un HTML, "Diagnóstico de Contigo") que estaban en
la máquina de quien hizo este trabajo. **Antes de empezar el paso 7, pídeselos al equipo y
súbelos a `docs/`** — si no, no hay con qué comparar y el paso no se puede hacer.

El criterio de aquella clasificación: un turno cuenta como problemático si posterga un dato que sí
estaba en el contexto, repite una cifra que ella ya vio, ofrece lo mismo dos veces, o responde con
una pregunta en vez del dato que pidió. Si vuelves a clasificar, usa el mismo criterio o el número
no es comparable.

Es lo último de la lista, y lo que cierra el trabajo.

---

## Reglas que no se rompen

Están en `CLAUDE.md` y se repiten acá porque romperlas ya costó trabajo rehecho:

- **El LLM nunca calcula ni inventa cifras.** Las cifras salen del banco simulado y del motor
  financiero, y el backend verifica cada respuesta antes de enviarla. Si una cifra no viajó al
  modelo y aparece en la respuesta, el turno sale con otro texto.
- **Ninguna condicional de estado en los prompts.** Ni una línea que empiece con "si ella…". Todo
  lo que depende del estado lo resuelve `prompts.movimientos_para()`, que sí lo conoce. Hay tests
  que recorren la matriz completa (modo × etiqueta) y fallan si alguna celda queda vacía o trae
  una condicional. **Si agregas una, el bug de las respuestas vagas vuelve.**
- **Un movimiento no puede contradecir los datos.** Si el bloque de datos llega vacío, ninguna
  instrucción puede ordenar contar cifras. Tres de los siete defectos del paso 1 eran esto.
- **La voz es de C.** Los textos fijos no se editan sin ella.
- **`agent/interface.py` es el contrato compartido**: todo cambio se acuerda con B antes.
- **`ruff format` solo sobre los archivos que tocas.** `ruff format .` reformatea código de otros
  dueños y ensucia el diff. Agrega archivos a git de forma explícita, **nunca `git add -A`**: hay
  carpetas locales de herramientas que no deben subirse.
- **No apilar PRs** sobre ramas de otros PRs: con *squash and merge*, el PR de arriba termina
  mergeado en la rama vieja y nunca llega a `main` (ya pasó, con #5).
- **Un responsable por ticket antes de empezar** (T09 se implementó dos veces en paralelo).

## Si algo responde mal

1. Enciende la traza y reproduce el turno (`AGENT_TRACE_ENABLED=true`).
2. Mira **qué modo recibió `write()`**: ahí está el modo final del turno. La mitad de los bugs de
   "respondió como si estuviera en otro modo" se explican en esa línea.
3. Mira `inyectado.campos` y `inyectado.movimientos`. Si los movimientos piden algo que los campos
   no permiten, ese es el bug —y va arreglado en el código, no en el prompt.
4. Mira `validadores` y `regla_aplicada`: si dice `respaldo_*`, el modelo respondió y un validador
   lo rechazó; el texto que recibió Patricia es el de respaldo del modo.
