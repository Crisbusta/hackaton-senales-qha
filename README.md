# Contigo — Tus finanzas, a tu ritmo

Asistente financiero conversacional vía WhatsApp. Backend en FastAPI.

## El problema

Patricia tiene una deuda en su tarjeta y le da miedo mirarla. Las apps financieras le muestran cifras de golpe y la hacen sentir peor, así que evita abrirlas.

## Qué hace Contigo

Contigo es un acompañante por WhatsApp. No empuja a pagar: va al ritmo de la persona.

- **Escalera:** muestra un dato a la vez (fecha, pago mínimo, saldo) y siempre ofrece parar.
- **Respeta el "no":** si ella no quiere ver nada, no muestra cifras ni insiste.
- **Pausa antes de comprar en cuotas:** muestra cuánto subiría su pago mínimo, sin juzgar.
- **Apoyo ante angustia fuerte:** deja las cuentas de lado y ofrece ayuda (\*4141 y 131).
- **Lee su estado de cuenta** (hoy desde un banco simulado; la lectura de PDF está pendiente): si un dato no está, lo dice en vez de inventarlo.

## Qué dejamos fuera a propósito

Conexión bancaria real, asesoría financiera, diagnóstico emocional y recordatorios de deuda.

## Notas de voz

Si ella manda un audio, Contigo lo transcribe **en el mismo servidor** (faster-whisper, modelo
`small`, sin APIs pagadas: el audio no sale de ahí) y responde como si lo hubiera escrito. Si no
se entiende, pide que lo escriba. Si en el audio dice un monto para simular ("¿y si pago 100
mil?"), primero confirma: "¿Quieres que pruebe con $100.000?".

- La imagen Docker trae el modelo y viene con `VOICE_ENABLED=true`.
- En local: `VOICE_ENABLED=true` en `.env` (la primera vez baja el modelo, ~480 MB).
- Audios de más de 60 s (`VOICE_MAX_SECONDS`) o 5 MB no se transcriben.

## Pruebas manuales

Los casos P0 de la demo están en [docs/casos_p0.md](docs/casos_p0.md).

## Equipo

- **A · Javier Barahona:** agente y modelo de lenguaje.
- **B · Crisbusta:** WhatsApp con Twilio, lectura del PDF y simulador.
- **C · Jairo Soto:** producto, pruebas y pitch.

Todos los datos de este proyecto son ficticios.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (`brew install uv`). Instala Python 3.12 automáticamente.

## Setup

```bash
uv sync                      # crea .venv e instala dependencias (incluye dev)
cp .env.example .env         # completar valores
```

## Ejecutar

```bash
uv run uvicorn app.main:app --reload --port 8000
curl localhost:8000/health   # {"status":"ok","service":"contigo"}
```

Docs interactivas (Swagger): http://localhost:8000/docs

## Webhook de WhatsApp (Twilio)

`POST /webhooks/whatsapp` recibe mensajes de Twilio (`application/x-www-form-urlencoded`),
valida `X-Twilio-Signature` y responde con TwiML.

### Probar con el WhatsApp Sandbox

1. Exponer la app local: `ngrok http 8000` → copiar la URL `https://xxxx.ngrok-free.app`.
2. En `.env`:
   ```bash
   TWILIO_AUTH_TOKEN=<Auth Token de la consola Twilio>
   PUBLIC_BASE_URL=https://xxxx.ngrok-free.app
   ```
3. Reiniciar la app.
4. Twilio Console → Messaging → Try it out → WhatsApp Sandbox → *When a message comes in*:
   `https://xxxx.ngrok-free.app/webhooks/whatsapp` (método `POST`).
5. Unirse al sandbox desde WhatsApp (`join <palabra>`) y enviar un mensaje.

### Firma y proxies

Twilio firma la **URL pública exacta** que llama. Detrás de ngrok o Railway la app recibe
otra URL (http, host interno), por eso la firma se valida contra `PUBLIC_BASE_URL` + path.
Si `PUBLIC_BASE_URL` no coincide exactamente con lo configurado en Twilio, todas las
solicitudes serán rechazadas con `403`.

- Sin `TWILIO_AUTH_TOKEN` la app **rechaza todo** (falla cerrada).
- `TWILIO_VALIDATE_SIGNATURE=false` permite probar con `curl` en local; la app no arranca
  si se combina con `APP_ENV=production`.

### Respuestas de error

| Caso | Respuesta |
|---|---|
| Firma inválida o ausente | `403` |
| Payload sin `MessageSid`/`From` o malformado | `400` |
| Error al procesar el mensaje | `200` + TwiML con mensaje amable (sin detalles internos) |

Los logs usan `MessageSid` como id de correlación, enmascaran el número (`***5678`) y
nunca registran el contenido del mensaje.

## Conversación

`ConversationService` (`app/modules/conversation/`) coordina cada turno:

1. **Deduplica** por `MessageSid`: un reenvío de Twilio recibe TwiML vacío.
2. Recupera el estado del usuario (modo, preferencias, historial).
3. Invoca al agente con un **presupuesto de tiempo** (`AGENT_TIMEOUT_S`, 12 s por defecto).
   Si se excede o falla, el usuario recibe un mensaje amable y el turno no se guarda.
4. Aplica **reglas deterministas** sobre el evento del agente:

   | Evento | Efecto |
   |---|---|
   | `DECLINED_BALANCE_REVIEW` | `show_financial_figures=False`, modo `PAUSA_RECHAZO` |
   | `REQUESTED_BALANCE` | `show_financial_figures=True` (la solicitud actual prevalece) |
   | `CRISIS_SUPPORT_NEEDED` | modo `APOYO` |

   Las transiciones entre escalones E0–E4 quedan pendientes de la tabla de T01/T06.
5. Si el usuario pidió no ver cifras y la respuesta contiene un monto, se reemplaza por un
   texto neutro (última barrera, independiente del agente).
6. Persiste historial, eventos y preferencias.

El agente se elige con `AGENT_IMPL` (`mock` por defecto). `MockAgent` responde por
palabras clave y nunca muestra cifras.

### Persistencia (en memoria o PostgreSQL)

`build_repository()` elige la implementación de `ConversationRepository` según `DATABASE_URL`:

- **Vacía (default): `InMemoryConversationRepository`.** Los datos desaparecen al reiniciar
  (también con cada recarga de `--reload` y con cada deploy), no funciona con múltiples réplicas
  ni workers y la deduplicación por `MessageSid` solo aplica durante la vida del proceso.
- **Con valor: `PostgresConversationRepository`** (`psycopg` 3 asíncrono con pool). Las
  conversaciones y los `MessageSid` procesados sobreviven a los redeploys. El pool se abre al
  arrancar la app y las tablas se crean solas si no existen (`conversation_states`, con el estado
  completo como `jsonb`, y `processed_messages`); no hay migraciones. Cada operación es una sola
  sentencia. La deduplicación es atómica entre procesos (`INSERT ... ON CONFLICT DO NOTHING`);
  si llegan dos turnos del mismo usuario a la vez, gana la última escritura. Si la base no
  responde al arrancar, la app no levanta.

`/reiniciar` borra solo el estado del usuario, en ambas implementaciones: un reenvío de Twilio de
un mensaje ya procesado sigue siendo duplicado.

Tests contra PostgreSQL (se saltan sin `TEST_DATABASE_URL`; vacían esas dos tablas):

```bash
docker run -d --rm --name contigo-pg-test -p 55432:5432 -e POSTGRES_PASSWORD=test postgres:16
TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres uv run pytest
docker stop contigo-pg-test
```

## Banco simulado

`app/modules/banking/` entrega los datos bancarios del usuario a través de la interfaz
`BankDataProvider`. Hoy la implementa `MockBankProvider`, que lee un escenario **100 %
ficticio** desde `fixtures/` (`MOCK_BANK_SCENARIO=patricia`). Un proveedor regulado de
Open Finance implementaría la misma interfaz sin cambiar el resto del backend.

| Producto | Datos |
|---|---|
| Cuenta vista | Saldo disponible y movimientos categorizados (ingresos, salud, alimentación, servicios básicos…) |
| Tarjeta de débito | Asociada a la cuenta |
| Tarjeta de crédito | Cupo, deuda, facturado, pago mínimo y su desglose, vencimiento, tasa, compras en cuotas, historial de 12 estados de cuenta (`statement_history`) |
| Crédito de consumo | Saldo, cuota, cuotas restantes, próximo vencimiento, CAE |

`banking/service.py` traduce la tarjeta a `financial_data` con las mismas claves del estado
de cuenta en PDF, y calcula resúmenes deterministas: flujo de caja mensual (ingresos variables,
gastos esenciales) y panorama de deudas. Las cifras del escenario de Patricia coinciden con
`docs/casos_p0.md`.

El escenario de Patricia cuenta la misma historia que sus 12 estados de cuenta ficticios
(noviembre 2025 a octubre 2026, tarjeta **** 6612): la tarjeta vigente es el estado de octubre
2026, las compras en cuotas son las del PDF (`paid_installments` = cuotas ya facturadas, "05/12"
→ 5) y los pagos de tarjeta de la cuenta vista (julio a octubre) son los "Pagos y Abonos" de cada
periodo. Sus ingresos por ventas por catálogo varían y no cubren todo: paga cerca del mínimo.
Los tests de `tests/banking/` verifican que todo cuadre.

Convenciones: montos en `int` (CLP), tasas en `Decimal`, banco ficticio (nunca una marca real).

**Consentimiento.** Como en Open Finance, se otorga **fuera del chat**, en el portal del banco
(`consent.granted` en el escenario). Sin un consentimiento vigente, el proveedor no entrega
datos. Contigo nunca pide la conexión por WhatsApp.

**Uso en la conversación: precarga + verificación posterior** (`conversation/figures.py`).

1. En los escalones (E0–E4), el agente recibe **todos los datos de la Escalera**, para poder
   responder en el mismo turno lo que ella pide, sin un turno de atraso.
2. Después de la transición, el backend verifica que la respuesta **solo traiga montos del modo
   en que terminó el turno** (`fields_for_mode()`) o montos que ella misma escribió.
3. Si trae una cifra de más, se reintenta una vez con solo los datos permitidos. Si no queda
   tiempo o el reintento tampoco cumple, sale un texto sin cifras.

En las pausas, APOYO y CIERRE no se precarga nada. Si el usuario pidió no ver cifras, sin
consentimiento o si el banco falla, no viaja ninguna cifra. Una cifra sin fuente verificable
nunca se envía.

## Estado de cuenta en PDF (T07)

Patricia puede enviar el PDF de su estado de cuenta por WhatsApp:

1. **Descarga** (`whatsapp/media.py`): desde la API de Twilio con `TWILIO_ACCOUNT_SID` +
   `TWILIO_AUTH_TOKEN`. Solo se aceptan URLs de `api.twilio.com`, máximo 5 MB, todo en memoria.
2. **Lectura** (`statements/`): `parse_statement()` obtiene los mismos campos que el banco
   (vencimiento, mínimo, saldo, facturado, cupo, tasa y desglose del mínimo) y `validate_statement()`
   revisa la coherencia. Nunca se extraen el nombre ni el RUT.
3. **Respuesta** (`conversation/statement_intake.py`): un texto fijo **sin cifras**, por ejemplo
   "Recibí tu estado de cuenta de octubre. Cuando quieras, partimos por la fecha de pago."
   (pendiente de validación de C). Las cifras llegan después por la Escalera, cuando ella las pide.
4. **Conciliación con el banco:** el PDF manda. Si un dato difiere del banco, se marca como
   `UNRELIABLE` y el agente dice que aparecen dos valores. Si el PDF no trae un dato y el banco sí,
   se usa el del banco. Sin banco, el PDF basta.

Las imágenes y los audios siguen el flujo normal. Solo se reconoce la plantilla de "Banco Austral"
(PDF con texto; sin OCR para documentos escaneados).

## Motor financiero (T10)

`app/modules/finance/calculator.py` hace todos los cálculos de forma determinista, con
`Decimal`. Los resultados se entregan en pesos enteros. **El LLM nunca calcula**: solo redacta
lo que llega en `calculations`.

| Función | Qué responde |
|---|---|
| `compare_with_minimum` | Meses e intereses pagando un monto fijo frente a pagar solo el mínimo |
| `affordability` | Si el pago deja cubiertos los gastos esenciales del último mes (principio 7) |
| `installment_effect` | Cuánto sube el mínimo con una compra en cuotas (caso C05) |

**Supuestos (los resultados son ilustrativos):** interés mensual compuesto a la tasa del estado
de cuenta, pago fijo, sin compras nuevas ni comisiones, y cuotas sin interés. "Pagar solo el
mínimo" supone un mínimo fijo; en la realidad el mínimo baja junto con el saldo, así que el plazo
real es todavía más largo.

**Cómo se activa en la conversación** (`conversation/simulation.py`, antes de llamar al agente):

- **Simular un pago:** desde E2 en adelante, "¿y si pago 100 mil?" (también "$100.000" o
  "80 lucas") pasa a **E4** y envía `monto_elegido` más los resultados en el mismo turno. "Me
  pagaron 100 mil" no cuenta como pago.
- **Cuánto suma la cuota:** en PAUSA_COMPRA, si elige la opción 2 y luego dice "$60.000 en 6
  cuotas", se envía `minimo_con_cuota` y `cuota_mensual`. Si la compra es esencial, no se calcula.

Patricia, pagando $100.000: 13 meses frente a 19 pagando solo el mínimo, con $97.662 menos de
intereses.

## Dashboard de progreso

`/dashboard` muestra, para cada conversación, la **Escalera** (E0–E4), los **avances logrados**
con su hora, contadores (turnos, pausas respetadas) y una **línea de tiempo** por turno (modo,
etiqueta del agente y evento). Se actualiza cada 3 s, así que se puede proyectar junto a
WhatsApp en la demo.

- **Avances basados en evidencia** (`conversation/progress.py`, principio 8): "vio su pago
  mínimo" se marca solo si la cifra **aparece en la respuesta que ella recibió**, no por haber
  llegado a un modo. Si pidió no ver cifras, no se marca ningún avance de cifras.
- **Privacidad:** nunca muestra el texto de los mensajes ni el teléfono completo (`***3931`).
- **Acceso:** autenticación básica del navegador con `DASHBOARD_PASSWORD` (cualquier usuario).
  Sin contraseña configurada, responde 404.
- Usa la interfaz del repositorio: hoy se vacía con cada deploy o `/reiniciar`; con PostgreSQL
  conservará el historial sin cambios en el dashboard.

## Casos P0 automáticos

`scripts/p0_runner.py` ejecuta los casos de `docs/casos_p0.md` contra el flujo completo. Los
casos **se leen del documento**: si C edita una tabla, el runner la toma tal cual.

```bash
uv run python -m scripts.p0_runner --agent mock          # sin costo
uv run python -m scripts.p0_runner                       # agente del .env (LLM real)
uv run python -m scripts.p0_runner --case C05 --case C10 # algunos casos
uv run python -m scripts.p0_runner --url https://api-production-0f5fb.up.railway.app
uv run python -m scripts.p0_runner --report resultados.md
```

- Para cada paso muestra **lo que respondió Contigo junto a lo que "debe" hacer**, para que una
  persona juzgue el tono.
- Revisa automáticamente las reglas objetivas: cifras después de un rechazo, frases prohibidas,
  la línea de crisis (\*4141 y 131) en C10 y los montos de C02 y C05. Termina con código 1 si
  alguna falla.
- Los pasos que requieren un PDF se omiten (T07 está pendiente).
- Con `--url`, las solicitudes van firmadas con `TWILIO_AUTH_TOKEN` y cada conversación usa un
  número ficticio distinto, así que no afecta a usuarios reales. **Cada paso llama al LLM del
  despliegue.**

## Despliegue (Docker + Railway)

```bash
docker build -t contigo .
docker run -p 8000:8000 --env-file .env contigo
```

La imagen usa `uv sync --frozen --no-dev`, corre con un usuario sin privilegios y escucha en
`$PORT` (Railway lo inyecta). `railway.json` define el healthcheck (`/health`) y **una sola
réplica**: sin `DATABASE_URL` el estado de la conversación está en memoria, así que con dos
réplicas cada mensaje podría caer en una distinta.

### Variables en Railway

| Variable | Valor |
|---|---|
| `APP_ENV` | `production` (obliga a validar la firma y apaga la traza del agente) |
| `PUBLIC_BASE_URL` | `https://<servicio>.up.railway.app`, sin slash final |
| `TWILIO_AUTH_TOKEN` | 🔒 Auth Token de Twilio |
| `TWILIO_ACCOUNT_SID` | Account SID de Twilio (empieza con `AC`), para descargar el PDF que envía el usuario |
| `AGENT_IMPL` | `llm` (o `mock` para probar sin costo) |
| `OPENAI_API_KEY` | 🔒 solo si `AGENT_IMPL=llm` |
| `MOCK_BANK_SCENARIO` | `patricia` |
| `DASHBOARD_PASSWORD` | 🔒 Contraseña de `/dashboard`. Vacía: desactivado |
| `DATABASE_URL` | 🔒 Referencia a la base: `${{Postgres.DATABASE_URL}}` (ver abajo). Vacía: en memoria |
| `DEMO_ADMIN_NUMBERS` | Números E.164 separados por coma que pueden escribir `/reiniciar` para borrar **su propia** conversación (ensayo de la demo). Vacío: desactivado |

Los secretos (🔒) se cargan en el panel de Railway, nunca en el repositorio.

### Twilio

En la configuración del sender de WhatsApp: **Webhook URL for incoming messages** =
`https://<servicio>.up.railway.app/webhooks/whatsapp` (POST). El Status callback va vacío.
Mientras el webhook apunte a Railway, ngrok local no recibe mensajes.

**Demo:** el guion, los preparativos y el plan B están en [`docs/guion-demo.md`](docs/guion-demo.md).

**Limitaciones:** sin `DATABASE_URL`, cada nuevo deploy reinicia el proceso y se pierden las
conversaciones en memoria (en la demo, Patricia vuelve a empezar desde "Hola").

### PostgreSQL en Railway

1. En el proyecto: **+ New → Database → PostgreSQL** (crea el servicio `Postgres`).
2. En el servicio `api` → **Variables**: `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (la URL
   de la red privada; no hace falta exponer la base a internet).
3. Redeploy. En los logs debe aparecer `Conversation repository: postgres`. Las tablas se crean
   en el primer arranque.
4. Para volver a memoria, borrar la variable (los datos quedan en la base).

## Tests y lint

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Estructura

Arquitectura feature-based: cada módulo en `app/modules/<feature>/`.

| Módulo | Responsable | Estado |
|---|---|---|
| `agent/interface.py` | Backend + IA (contrato compartido) | ✅ |
| `whatsapp/` | Backend | pendiente |
| `conversation/` | Backend | pendiente |
| `agent/` (LLM) | IA | ✅ |
| `statements/`, `finance/` | Backend | posterior |

## Agente (IA)

`agent/service.py` expone `LlmAgent`, que implementa el protocolo `Agent`.
Un turno pasa por cuatro capas antes de responder:

1. **Pre-filtro** (`prefilter.py`): la ideación explícita se ataja sin
   llamar al modelo.
2. **Structured output** (`client.py`): etiqueta y mensaje en campos
   separados, nunca un string que haya que parsear.
3. **Texto fijo** (`texts.py`): en `angustia_fuerte`, `rechazo_pausa` y
   `no_texto` la prosa del modelo se descarta.
4. **Validadores** (`validators.py`): seis chequeos deterministas, entre
   ellos la lista blanca numérica — toda cifra de la respuesta tiene que
   venir de `financial_data` o de `calculations`. Si falla, un reintento;
   si vuelve a fallar, sale el texto de respaldo del modo.

Para correrlo de verdad hace falta `AGENT_IMPL=llm` y `OPENAI_API_KEY` en
el `.env`. Los tests usan un modelo simulado: no gastan tokens ni salen a
la red.

**Presupuesto de tiempo.** Twilio corta el webhook a los 15 s. Medido
contra el proveedor real: una llamada tarda p50 ~4,5 s y hasta 7,3 s, y el
peor caso observado (principal + respaldo + reintento de validación) fueron
16 s. Por eso el cliente usa `OPENAI_TIMEOUT_S=9` sin reintentos internos y
el turno tiene un presupuesto total derivado del backend (`AGENT_TIMEOUT_S`,
12 s, menos 1 s de margen): cuando lo que queda no alcanza para otra llamada,
sale el texto de respaldo del modo en vez de arriesgar que Twilio corte. Si el proveedor falla del todo
también sale ese texto: nunca una excepción.

`observability.py` deja una línea JSONL por turno, para depurar y para el
pitch. Guarda el mensaje completo y la respuesta con cifras, así que viene
**apagada**: se enciende con `AGENT_TRACE_ENABLED=true` en local y no se
puede encender con `APP_ENV=production`. El id de conversación se guarda
seudonimizado, porque `user_id` es el teléfono.

Las transiciones de modo y las reglas anti-bucle están en
[`docs/maquina-de-estados.md`](docs/maquina-de-estados.md).

## Contrato backend ↔ IA

`app/modules/agent/interface.py` define `AgentRequest`, `AgentResponse`,
`ConversationEvent` y el protocolo `Agent`. **Cualquier cambio debe acordarse
entre ambos integrantes.** El agente emite eventos; el backend los traduce a
preferencias con reglas deterministas. El LLM nunca calcula cifras financieras.

## 🤝 Flujo de colaboración

Usamos **GitHub Flow**: una rama estable y ramas cortas que se integran por Pull Request.

1. `main` es la rama principal y debe estar siempre funcional.
2. Cada funcionalidad se desarrolla en una rama independiente creada desde `main` actualizado.
3. Las ramas siguen la convención descrita abajo.
4. Los cambios se integran mediante Pull Requests hacia `main`.
5. Cada PR completa la plantilla (`.github/pull_request_template.md`).
6. Antes de abrir un PR se ejecutan las pruebas relevantes (`uv run pytest`, `uv run ruff check .`).
7. Los PRs son pequeños y enfocados en un solo objetivo.
8. Se recomienda la revisión de otro integrante cuando sea posible.
9. Los PRs se integran con **Squash and merge** (un commit por PR en `main`).
10. Los cambios en contratos compartidos backend ↔ IA (`app/modules/agent/interface.py`)
    se coordinan entre ambos integrantes **antes** de implementarse.

> **Excepción de inicialización:** el primer commit (estructura, configuración, documentación
> y contrato compartido) se hizo directo en `main`. Desde ahí, todo cambio va por rama + PR.

### Ramas

Formato: `<tipo>/<id-ticket>-<descripcion-en-kebab-case>` (el id se omite si no hay ticket).

| Prefijo | Uso |
|---|---|
| `feat/` | Nuevas funcionalidades |
| `fix/` | Corrección de errores |
| `refactor/` | Refactorizaciones sin cambios funcionales |
| `test/` | Pruebas automatizadas |
| `chore/` | Configuración y mantenimiento |
| `docs/` | Documentación |

Ejemplos: `feat/t03-twilio-webhook`, `feat/t04-llm-agent`, `feat/t06-conversation-state`,
`feat/t07-pdf-parser`, `fix/t03-twilio-signature`, `docs/t12-demo-guide`.

### Commits y títulos de PR

[Conventional Commits](https://www.conventionalcommits.org/): `<tipo>: <descripción breve en inglés>`.

```text
feat: implement Twilio WhatsApp webhook
feat: add conversation state management
fix: validate Twilio request signature
test: add agent classification tests
chore: configure Docker environment
docs: update local setup instructions
```

Con Squash and merge, el **título del PR** se convierte en el commit de `main`, así que debe
seguir esta convención.

### Flujo típico

```bash
git switch main && git pull
git switch -c feat/t03-twilio-webhook
# ... cambios + commits ...
uv run pytest && uv run ruff check .
git push -u origin feat/t03-twilio-webhook
gh pr create --base main   # completar la plantilla
```

### Definition of Done

- Código implementado.
- Pruebas relevantes aprobadas.
- PR revisado cuando sea posible.
- Cambios integrados en `main`.
- Funcionalidad verificable.

### Protección de `main` (recomendada)

Configuración mínima sugerida en GitHub → Settings → Branches:

- Requerir Pull Request antes de integrar a `main` (sin push directo).
- Revisiones: recomendadas, **no obligatorias** durante la hackathon (0 aprobaciones requeridas).
- Status checks obligatorios: solo cuando exista un CI estable.
- Permitir únicamente Squash and merge.
- Evitar restricciones adicionales que bloqueen al equipo.
