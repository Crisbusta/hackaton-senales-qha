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
