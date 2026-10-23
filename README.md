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
- **Lee el PDF del estado de cuenta:** si un dato no está, lo dice en vez de inventarlo.

## Qué dejamos fuera a propósito

Audios, conexión bancaria real, asesoría financiera, diagnóstico emocional y recordatorios de deuda.

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
| `agent/` (LLM) | IA | pendiente |
| `statements/`, `finance/` | Backend | posterior |

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
