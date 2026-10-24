# Handoff — mejora del agente y sus respuestas

**Para quién es este documento.** Para quien retome este trabajo sin haber estado en la
conversación donde se hizo. Está escrito para que se lo puedas pasar a Claude Code directamente:

> «Lee `docs/handoff.md` y ejecuta el paso 4.»

Cada paso de abajo dice qué hay que hacer, con qué comando se verifica, quién decide lo que no
puede decidir Claude, y qué trampa tiene. **No ejecutes un paso sin leer antes "Reglas que no se
rompen"**, al final.

---

## De qué se trata todo esto

El bot respondía mal en **40 % de los turnos** (medidos sobre 52 reales). Tres diagnósticos
independientes coincidían en el síntoma —respuestas vagas, "eso lo vemos en un momento",
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

## El roadmap: 7 pasos, 2 cerrados

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

Herramientas para repetirlo (no versionadas a propósito; si las quieres, están en el historial):

```bash
uv run uvicorn app.main:app --reload --port 8000
uv run python -m scripts.conv "/reiniciar" "hola" "cuanto debo" "y si pago 100 mil"
AGENT_TRACE_ENABLED=true AGENT_TRACE_PATH=turnos.jsonl uv run uvicorn app.main:app --port 8000
```

La traza (`turnos.jsonl`) es **la herramienta de depuración**: una línea JSON por paso, con el
modo, la etiqueta, los campos inyectados, los movimientos resueltos, la salida cruda del modelo y
qué validador falló. Si un turno responde mal, la respuesta está ahí y no en el código.

### ⬜ 3. Mergear este PR

El PR está abierto con cinco commits. **Antes de mergear hay que avisarle a B**: el cambio toca
`agent/interface.py` (el contrato: `process()` → `classify()` + `write()`) y `conversation/`, que
son suyos según `CLAUDE.md`.

- **Squash and merge**, como todo en este repo.
- **No mergear durante la demo**: cada merge a `main` redespliega Railway, y sin `DATABASE_URL`
  eso borra las conversaciones en memoria. Ver paso 4.

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

### ⬜ 7. Volver a medir

El 40 % de respuestas problemáticas es el número de **antes**. Nadie ha medido el después sobre
los mismos 52 turnos. Sin esa pasada no se puede afirmar que mejoró — solo que los seis casos P0
pasan.

```bash
uv run python -m scripts.p0_runner --agent llm --report resultados_despues.md
```

Y repetir la clasificación manual de los 52 turnos con la traza encendida, igual que la primera
vez. Es lo último de la lista, y lo que cierra el trabajo.

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
