# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Mantener este archivo al día.** Si un PR cambia el flujo de un turno, el contrato, una
> decisión de diseño o una limitación listada abajo, actualiza la sección correspondiente en el
> mismo PR. La sección "Limitaciones conocidas" es la lista de mejoras pendientes.

## Qué es Contigo

Asistente financiero por **WhatsApp** (hackathon, 3 integrantes) para personas que evitan mirar
sus deudas por miedo. Usuaria de referencia: **Patricia** (Talca, cuida a su mamá, ingresos
variables por ventas de catálogo, paga el mínimo, compra en cuotas bajo estrés). Todos los datos
son ficticios.

El objetivo no es mostrar cuánto debe, sino acompañarla **de a un dato por vez y a su ritmo**.
Principios que el código hace cumplir (no solo el prompt):

- **El LLM nunca calcula ni inventa cifras.** Las cifras salen del banco simulado y del motor
  financiero; el backend verifica cada respuesta antes de enviarla.
- **Exposición progresiva:** la "Escalera" E0→E4 (conversar → fecha → mínimo y saldo → desglose →
  simular un pago).
- **Respeta el "no":** tras una negativa no viaja ninguna cifra al modelo.
- **No diagnosticar** estados emocionales ni inferir nada del silencio.
- **No comprometer gastos esenciales** (el simulador conoce el gasto esencial del banco).
- **Gamificación discreta** basada en avances observables (dashboard).

## Comandos

```bash
uv sync                                              # Python 3.12 + dependencias (incluye dev)
uv run uvicorn app.main:app --reload --port 8000     # servidor local
uv run pytest                                        # toda la suite (sin red ni tokens)
uv run pytest tests/conversation/test_simulation.py -k e4   # un archivo / un test
uv run ruff check . && uv run ruff format --check .  # lint
uv run python -m scripts.p0_runner --agent mock      # casos P0 de docs/casos_p0.md, sin costo
uv run python -m scripts.p0_runner --agent llm --case C05 --report resultados.md   # con el LLM real
docker build -t contigo . && docker run -p 8000:8000 --env-file .env contigo
```

Para probar el webhook en local sin firma: `TWILIO_VALIDATE_SIGNATURE=false` (prohibido con
`APP_ENV=production`; la app no arranca).

## Flujo de un turno

```text
Twilio POST /webhooks/whatsapp          whatsapp/router.py, security.py
  → valida X-Twilio-Signature contra PUBLIC_BASE_URL + path (falla cerrado sin token)
  → normaliza a IncomingMessage → MessageHandler.handle()         (= ConversationService)

ConversationService.handle()            conversation/service.py
  1. dedup por MessageSid → duplicado: TwiML vacío
  2. /reiniciar desde DEMO_ADMIN_NUMBERS → borra el estado y responde
  3. PDF adjunto (T07) → statement_intake, se confirma sin cifras y termina el turno
  4. AB8: un acuse vacío ("ok", "gracias") se responde una sola vez

  --- 1. CLASIFICAR ---
  5. agent.classify(): etiqueta, esencial, monto mencionado, ritmo.
     Esta llamada NO recibe ninguna cifra: ni financial_data, ni calculations, ni
     el historial sin enmascarar. No puede filtrar un monto porque no tiene ninguno.

  --- 2. DECIDIR (reglas deterministas, sin modelo) ---
  6. apply_event() + transitions.siguiente_modo() → el modo FINAL del turno
  7. installment_essential / installment_calc_requested
  8. simulation.plan_turn(): "¿y si pago 100 mil?" → E4 con ese monto
  9. banco + PDF (si hay consentimiento y no pidió ocultar cifras) → financial_data
     con EXACTAMENTE los campos de fields_for_mode(modo_final, etiqueta)
 10. calculations del motor financiero (solo si calculations_allowed)

  --- 3. REDACTAR ---
 11. agent.write(): los datos exactos y los movimientos ya resueltos. El redactor
     no evalúa ninguna condición de estado porque no le llega ninguna.
 12. si pidió no ver cifras y hay un monto → NO_FIGURES_REPLY (salvo protected_text)
 13. red de seguridad: un monto que no viajó → STEP_BY_STEP_REPLY (sin reintento)
 14. progress.record_turn() (avances + timeline) → repository.save()
  → TwiML (un error del handler responde 200 con un texto amable, nunca 5xx)
```

**El "turno de atraso" ya no existe.** Mientras el turno fue una sola llamada, el modelo
redactaba con el modo de ENTRADA y el modo cambiaba después: de ahí venían la precarga con su
verificación posterior, la detección previa de T10 como forma de cambiar el modo, y el re-llamado
al entrar a un modo (`_reanswer_on_mode_entry`). Los tres existían para compensar el orden y se
borraron con él. Medido contra el modelo real: "sí, veamos" pasó de "¿Qué te gustaría saber
primero?" a "tu pago vence el 3 de noviembre de 2026", y las dos opciones de PAUSA_COMPRA llegan
en el mismo turno en vez de perderse por presupuesto.

Si aparece un bug de "respondió como si estuviera en otro modo", mira qué modo recibió `write()`
en la traza: ahí está el modo final del turno.

## Módulos y dueños

| Módulo | Qué hace | Dueño |
|---|---|---|
| `whatsapp/` | Webhook, firma, TwiML. No conoce al agente | Backend (B) |
| `conversation/` | Orquesta el turno: estado, reglas, precarga, verificación de cifras, simulación, avances | Backend (B). `transitions.py` lo escribió IA (A) |
| `agent/` | `LlmAgent` (prefiltro de crisis → structured output → textos fijos → validadores), prompts, cliente OpenAI | IA (A). Backend solo toca `factory.py` y `mock.py` |
| `agent/interface.py` | **Contrato compartido** backend ↔ IA | Ambos: todo cambio se acuerda antes |
| `banking/` | `BankDataProvider` + `MockBankProvider` (fixture `patricia.json`, banco ficticio). El fixture coincide con los 12 estados de cuenta PDF de Patricia (2025-11 → 2026-10): tarjeta = estado de 2026-10, cuotas y pagos de cada período, y `statement_history` con los 12 meses | Backend |
| `statements/` | Parser de estados de cuenta en PDF (`pypdf`, BSD): `parse_statement(bytes)` → mismos campos y formatos que `credit_card_fields()`, con `ABSENT`/`UNRELIABLE`; `validate_statement()` revisa que el resumen sume, el cupo cuadre, etc. En memoria, nunca extrae nombre ni RUT | Backend |
| `finance/` | Motor determinista (`Decimal`, resultados `int` CLP) | Backend |
| `dashboard/` | `/dashboard` con autenticación básica; lee `repository.list_all()` | Backend |

`main.py` es la raíz de composición: arma agente (`build_agent`, importa `LlmAgent` de forma
diferida), repositorio, banco y servicio en `app.state`.

## Contrato (`agent/interface.py`) y documentos

- `Agent` (Protocol) tiene dos pasos: `classify(ClassifyRequest) -> Classification` y
  `write(WriteRequest) -> AgentReply`. El backend llama a los dos con su decisión de estado en
  medio. `ConversationMode` (E0–E4, PAUSA_RECHAZO, PAUSA_COMPRA, APOYO, CIERRE) lo decide **solo
  el backend**, y `WriteRequest.mode` es el modo FINAL del turno.
- Los cuatro modelos son `extra="forbid"`: un campo que no existe es un error, no algo que se
  descarte en silencio.
- `ClassifyRequest` no tiene ningún campo donde quepa un monto: es una garantía por construcción,
  no una regla que haya que vigilar.
- `FIELDS_BY_MODE` + `fields_for_mode()` es la **única** tabla de qué dato pertenece a qué modo;
  la usan backend y agente.
- `Intent` (17 etiquetas, StrEnum) pero `Classification.intent` es `str`: comparar siempre contra
  `Intent.X` sin asumir que el valor está en el enum. `UNKNOWN` está reservado para "no hubo
  clasificación" (el proveedor falló); una etiqueta que el modelo invente viaja tal cual.
- Montos `int` (CLP). Fechas y tasas viajan como texto formateado; si hace falta operar una tasa,
  `Decimal`.
- Claves del simulador en `calculations`: `meses_pagando_*`, `intereses_pagando_*`,
  `meses_ahorrados`, `intereses_ahorrados`, `intereses_del_mes`, `faltante_para_gastos_esenciales`,
  `cuota_mensual`.
- `docs/maquina-de-estados.md`: transiciones y reglas anti-bucle AB1–AB8.
- Las reglas anti-bucle viajan al modelo como **movimientos resueltos**, no como flags: el
  bloque `ESTADO` del contexto solo lleva `modo`. `puedes_ofrecer_continuar`,
  `puedes_aclarar_ruta`, `limites_ya_dichos` y `escalon_maximo` los evalúa `armar_contexto()` y
  se traducen en una instrucción que está o no está. Un flag en el contexto es una condicional
  que el modelo tiene que interpretar; una instrucción ausente no se interpreta.
- Si cambia una cifra de Patricia, debe cambiar en el fixture del banco, en los PDFs de prueba y en
  `docs/casos_p0.md` a la vez (hay tests que verifican que banco y estado de cuenta coincidan).
- `docs/casos_p0.md`: casos de prueba de C (los lee el runner; no cambiar el formato de las
  tablas sin ajustar `scripts/p0_runner.py`).
- `docs/guion-demo.md`: guion, preparativos y plan B de la demo.

## Invariantes de seguridad y privacidad

- Logs: teléfono enmascarado (`mask_phone` → `***3931`), `MessageSid` como id de correlación,
  **nunca** el `Body`. El dashboard no muestra mensajes ni teléfonos completos.
- La traza del agente (`AGENT_TRACE_ENABLED`) guarda mensajes completos: apagada por defecto y no
  se puede encender en producción.
- Consentimiento bancario: se otorga **fuera del chat** (`consent.granted` en el fixture); sin
  consentimiento el proveedor no entrega datos. Contigo nunca pide la conexión por WhatsApp.
- Banco ficticio: nunca usar el nombre o la marca de un banco real.

## Configuración clave

`app/core/config.py` (pydantic-settings, lee `.env`). Las que importan para entender el sistema:
`APP_ENV`, `AGENT_IMPL` (`mock`|`llm`), `AGENT_TIMEOUT_S` (12; el presupuesto interno del agente
es `AGENT_TIMEOUT_S − agent_budget_margin_s`; Twilio corta a ~15 s), `OPENAI_TIMEOUT_S` (9),
`PUBLIC_BASE_URL` (solo el origen, sin path ni `/` final), `TWILIO_AUTH_TOKEN` (el **primario** de
Twilio), `TWILIO_ACCOUNT_SID` (para descargar el PDF), `MOCK_BANK_SCENARIO`, `DEMO_ADMIN_NUMBERS`,
`DASHBOARD_PASSWORD`, `DATABASE_URL` (vacío = en memoria; con valor = PostgreSQL). En Railway los valores
van sin comillas en el campo de valor.

## Tests

- `tests/conftest.py` aísla los tests del `.env` del desarrollador (autouse). Ningún test llama al
  LLM ni sale a la red: el agente se reemplaza por dobles (`SpyAgent`) o `FakeModel`.
- `app.dependency_overrides[get_settings]` para variar configuración; FastAPI inspecciona la
  firma del override, así que usa `lambda: Settings(...)`, no una función con `**kwargs`.
- Para comportamiento del modelo real, el runner P0 (`--agent llm`), no pytest.

## Despliegue

Railway, proyecto `contigo`, servicio `api`, conectado a `main` del repo: **cada merge a `main`
redespliega**; sin `DATABASE_URL` eso borra las conversaciones en memoria (no mergear durante la
demo). Con `DATABASE_URL = ${{Postgres.DATABASE_URL}}` sobreviven.
`railway.json`: healthcheck `/health`, 1 réplica. Webhook de Twilio: el sender de WhatsApp
(Messaging → Senders) → *Webhook URL for incoming messages* = `PUBLIC_BASE_URL/webhooks/whatsapp`,
POST; *Status callback* vacío (si no, llegan como mensajes sin `Body`). Error 11200 en Twilio sin
requests en `railway logs` = Twilio llama a otra URL; 403 en los logs = token o `PUBLIC_BASE_URL`
no coinciden.

## Colaboración

- GitHub Flow, ramas `<tipo>/<ticket>-<descripcion>`, Conventional Commits en inglés, **Squash and
  merge**, plantilla en `.github/pull_request_template.md`.
- **No apilar PRs** sobre ramas de otros PRs: con squash, el PR de arriba termina mergeado en la
  rama vieja y nunca llega a `main` (pasó con #5).
- Un responsable por ticket antes de empezar (T09 se implementó dos veces en paralelo: #12 y #13).
- `ruff format` solo sobre los archivos que tocas: `ruff format .` reformatea código de otros
  dueños. Agrega archivos a git de forma explícita (no `git add -A`): hay archivos locales de
  herramientas (`.codex/`, `.claude/`, `.playwright-mcp/`) que no deben subirse.

## Limitaciones conocidas (mejoras pendientes)

- **Persistencia**: en memoria salvo que haya `DATABASE_URL` (`PostgresConversationRepository`:
  sobrevive a los deploys y deduplica entre procesos). Con PostgreSQL: dos turnos simultáneos del
  mismo usuario → gana la última escritura; `processed_messages` crece sin límite; sin migraciones
  (el estado es un `jsonb` por usuario).
- **PDF (T07)**: solo la plantilla de "Banco Austral" (texto, sin OCR para escaneos). Los textos de
  recepción (`statement_intake.*_REPLY`) están pendientes de validación de C. Conciliación: el PDF
  manda; si difiere del banco → `UNRELIABLE`; si le falta un dato y el banco lo tiene, se usa el del
  banco. El simulador (T10) todavía usa solo el banco. Los pasos con PDF del runner se omiten.
- **Detección de T10 por reglas**: solo con montos explícitos ("100 mil", "$100.000", "80 lucas").
- **`faltante_para_gastos_esenciales`**: el texto lo define C; el agente no debe mencionarlo hasta
  que esté validado.
- **El modelo de respaldo es inalcanzable si el principal se cae por timeout**: el respaldo
  necesita `OPENAI_TIMEOUT_S` libres del presupuesto (`AGENT_TIMEOUT_S − AGENT_BUDGET_MARGIN_S`),
  y un timeout del principal consume justo ese tiempo. Con los valores por defecto (9 de 11 s) solo
  cubre los errores rápidos (401, 429 inmediato), nunca los lentos.
- **Dos llamadas en serie**: medido de punta a punta, 5,5–9,4 s por turno con los plazos de
  "Los plazos del turno", dentro de la ventana de Twilio. Lo que queda sin red es el peor caso
  absoluto: si las dos se van al máximo, el turno sale con el texto de respaldo del modo. La salida
  de fondo es responder por la API REST de Twilio en vez de TwiML, que elimina el techo de los
  15 s; `OPENAI_CLASSIFY_MODEL` permite probar un clasificador más rápido sin tocar código.
- `railway.json` (Config as Code) está deprecado por Railway a partir del 2026-12-01.
