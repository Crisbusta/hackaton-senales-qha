# Contigo — Tus finanzas, a tu ritmo

Asistente financiero conversacional vía WhatsApp. Backend en FastAPI.

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
   | `DECLINED_BALANCE_REVIEW` / `AVOID_FINANCIAL_REVIEW` | `show_financial_figures=False`, modo `PAUSA_RECHAZO` |
   | `REQUESTED_BALANCE` | `show_financial_figures=True` (la solicitud actual prevalece) |
   | `CRISIS_SUPPORT_NEEDED` | modo `APOYO` |

   Las transiciones entre escalones E0–E4 quedan pendientes de la tabla de T01/T06.
5. Si el usuario pidió no ver cifras y la respuesta contiene un monto, se reemplaza por un
   texto neutro (última barrera, independiente del agente).
6. Persiste historial, eventos y preferencias.

El agente se elige con `AGENT_IMPL` (`mock` por defecto). `MockAgent` responde por
palabras clave y nunca muestra cifras.

### Persistencia temporal (en memoria)

`InMemoryConversationRepository` implementa la interfaz `ConversationRepository`, que
luego se implementará con PostgreSQL. Limitaciones:

- Los datos desaparecen al reiniciar (también con cada recarga de `--reload`).
- No funciona con múltiples réplicas ni workers.
- No ofrece persistencia duradera.
- La deduplicación por `MessageSid` solo aplica durante la vida del proceso.

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

El cliente usa `OPENAI_TIMEOUT_S` (6 s) y sin reintentos internos: Twilio
corta el webhook a los 15 s, y un turno puede sumar modelo principal,
respaldo y reintento de validación.

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
