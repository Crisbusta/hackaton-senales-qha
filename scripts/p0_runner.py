"""Ejecuta los casos P0 de `docs/casos_p0.md` contra el flujo completo.

Los casos se leen del documento (C es su dueña): si se edita una tabla, el
runner la toma tal cual. Para cada paso muestra la respuesta junto a lo que
"Contigo debe" hacer, y revisa automáticamente las reglas objetivas (cifras,
frases prohibidas, línea de crisis). El tono lo sigue juzgando una persona.

Uso:
    uv run python -m scripts.p0_runner                  # en proceso, AGENT_IMPL del .env
    uv run python -m scripts.p0_runner --agent mock     # sin costo
    uv run python -m scripts.p0_runner --case C05       # un solo caso
    uv run python -m scripts.p0_runner --url https://api-production-0f5fb.up.railway.app
    uv run python -m scripts.p0_runner --report resultados.md

Con --url las solicitudes van firmadas (requiere TWILIO_AUTH_TOKEN en el entorno)
y cada conversación usa un número ficticio distinto, así no toca a usuarios reales.
"""

import argparse
import asyncio
import os
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from twilio.request_validator import RequestValidator

from app.core.config import get_settings
from app.modules.agent.factory import build_agent
from app.modules.banking.provider import build_bank_provider
from app.modules.conversation.figures import amounts_in
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.service import ConversationService
from app.modules.whatsapp.schemas import IncomingMessage

CASES_PATH = Path(__file__).resolve().parent.parent / "docs" / "casos_p0.md"

# --- Lectura del documento ------------------------------------------------

_CASE = re.compile(r"^## (C\d+) · (.+)$")
_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$")
_QUOTED = re.compile(r"[\"“](.+?)[\"”]")
_NOTE = re.compile(r"\*\((.+?)\)\*")
_APPROVE = re.compile(r"^\*\*Aprueba si\*\*\s*(.+)$")


@dataclass
class Step:
    label: str
    message: str | None  # None: el paso no es texto (p. ej. enviar un PDF)
    expected: str
    note: str = ""


@dataclass
class Case:
    id: str
    title: str
    steps: list[Step] = field(default_factory=list)
    approve: str = ""


def parse_cases(text: str) -> list[Case]:
    cases: list[Case] = []
    current: Case | None = None
    for line in text.splitlines():
        if heading := _CASE.match(line):
            current = Case(id=heading.group(1), title=heading.group(2))
            cases.append(current)
            continue
        if line.startswith("## "):  # otra sección (p. ej. "Registro de resultados")
            current = None
            continue
        if current is None:
            continue
        if approve := _APPROVE.match(line):
            current.approve = approve.group(1)
            continue
        row = _ROW.match(line)
        if not row:
            continue
        label, message_cell, expected = row.groups()
        if label in ("Turno", "Sub") or set(label) <= {"-"}:
            continue
        quoted = _QUOTED.search(message_cell)
        # Notas de contexto: "*(sigue a a)*" en el mensaje, "4 (otro día)" en el turno.
        notes = _NOTE.findall(message_cell) + re.findall(r"\((.+?)\)", label)
        current.steps.append(
            Step(
                label=re.sub(r"\s*\(.+?\)", "", label).strip(),
                message=quoted.group(1) if quoted else None,
                expected=expected,
                note="; ".join(notes),
            )
        )
    return [case for case in cases if case.steps]


# --- Conversaciones -------------------------------------------------------

# Para "(en medio de la escalera)": la lleva hasta el pago mínimo antes del paso.
LADDER_WARMUP = ("Quiero ver lo de la tarjeta, pero poquito.", "Veamos.", "Veamos.")


@dataclass
class Conversation:
    user_id: str
    steps: list[Step]
    warmup: tuple[str, ...] = ()


def plan_conversations(case: Case) -> list[Conversation]:
    """Turnos numerados: una conversación. Sub-casos con letra: una cada uno,
    salvo "(sigue a X)", que continúa la conversación de X."""
    if all(step.label.isdigit() for step in case.steps):
        return [Conversation(_new_user(), case.steps)]
    by_label: dict[str, Conversation] = {}
    conversations: list[Conversation] = []
    for step in case.steps:
        follows = re.search(r"sigue a (\w)", step.note)
        if follows and follows.group(1) in by_label:
            by_label[follows.group(1)].steps.append(step)
            by_label[step.label] = by_label[follows.group(1)]
            continue
        warmup = LADDER_WARMUP if "escalera" in step.note else ()
        conversation = Conversation(_new_user(), [step], warmup)
        by_label[step.label] = conversation
        conversations.append(conversation)
    return conversations


def _new_user() -> str:
    # Número ficticio y único por conversación (+56 9 00 ...).
    return "+56900" + str(uuid.uuid4().int)[:6]


# --- Envío: en proceso o contra una URL -----------------------------------

Sender = Callable[[str, str], "asyncio.Future[str | None]"]


def local_sender(agent_impl: str | None) -> Sender:
    settings = get_settings()
    if agent_impl:
        settings = settings.model_copy(update={"agent_impl": agent_impl})
    service = ConversationService(
        agent=build_agent(settings),
        repository=InMemoryConversationRepository(),
        agent_timeout_s=settings.agent_timeout_s,
        bank=build_bank_provider(settings),
    )

    async def send(user_id: str, body: str) -> str | None:
        message = IncomingMessage(
            message_sid="SM" + uuid.uuid4().hex, from_number=f"whatsapp:{user_id}", body=body
        )
        return await service.handle(message)

    return send


def remote_sender(base_url: str, auth_token: str) -> Sender:
    import httpx2  # dependencia de desarrollo: solo se necesita para --url

    url = base_url.rstrip("/") + "/webhooks/whatsapp"
    validator = RequestValidator(auth_token)
    client = httpx2.AsyncClient(timeout=20)

    async def send(user_id: str, body: str) -> str | None:
        params = {
            "MessageSid": "SM" + uuid.uuid4().hex,
            "From": f"whatsapp:{user_id}",
            "Body": body,
            "NumMedia": "0",
        }
        headers = {"X-Twilio-Signature": validator.compute_signature(url, params)}
        response = await client.post(url, data=params, headers=headers)
        response.raise_for_status()
        messages = ET.fromstring(response.text).findall("Message")
        return messages[0].text if messages else None

    return send


# --- Revisiones automáticas -----------------------------------------------

Check = Callable[[str | None], str | None]  # devuelve el motivo de la falla, o None

FORBIDDEN = (
    "deberías",
    "deberias",
    "todo va a estar bien",
    "ánimo",
    "tranquila",
    "no te preocupes",
)


def no_amounts(reply: str | None) -> str | None:
    found = amounts_in(reply or "") - {4141, 131}
    return f"muestra cifras {sorted(found)}" if found else None


def no_question(reply: str | None) -> str | None:
    return "hace una pregunta" if reply and "?" in reply else None


def contains(*needles: str) -> Check:
    def check(reply: str | None) -> str | None:
        missing = [n for n in needles if n not in (reply or "")]
        return f"falta {', '.join(missing)}" if missing else None

    return check


def lacks(needle: str) -> Check:
    return lambda reply: f"no debería incluir {needle}" if needle in (reply or "") else None


def no_forbidden_phrases(reply: str | None) -> str | None:
    hits = [phrase for phrase in FORBIDDEN if phrase in (reply or "").lower()]
    return f"frase prohibida: {', '.join(hits)}" if hits else None


GLOBAL_CHECKS: tuple[Check, ...] = (no_forbidden_phrases,)

# Reglas objetivas que se desprenden de "Contigo debe" y "Aprueba si".
STEP_CHECKS: dict[tuple[str, str], tuple[Check, ...]] = {
    ("C01", "1"): (no_amounts,),
    ("C01", "2"): (no_amounts, no_question),
    ("C01", "3"): (no_amounts, no_question),
    ("C01", "4"): (no_amounts, no_question),
    ("C02", "3"): (contains("74.350"),),
    ("C05", "3"): (contains("10.000", "84.350"),),
    ("C06", "b"): (no_amounts,),
    ("C06", "c"): (no_amounts,),
    ("C10", "a"): (contains("4141", "131"), no_amounts),
    ("C10", "b"): (contains("4141", "131"), no_amounts),
    ("C10", "c"): (lacks("4141"),),
    ("C10", "d"): (contains("4141", "131"), no_amounts),
}


# --- Ejecución y reporte --------------------------------------------------


@dataclass
class StepResult:
    case_id: str
    step: Step
    reply: str | None = None
    failures: list[str] = field(default_factory=list)
    skipped: str = ""


async def run_case(case: Case, send: Sender) -> list[StepResult]:
    results: list[StepResult] = []
    for conversation in plan_conversations(case):
        for warmup in conversation.warmup:
            await send(conversation.user_id, warmup)
        for step in conversation.steps:
            result = StepResult(case.id, step)
            if step.message is None:
                result.skipped = "no es texto (requiere PDF, T07)"
            else:
                try:
                    result.reply = await send(conversation.user_id, step.message)
                except Exception as exc:  # noqa: BLE001 - se reporta, no se interrumpe la corrida
                    result.failures.append(f"error: {exc}")
                checks = GLOBAL_CHECKS + STEP_CHECKS.get((case.id, step.label), ())
                result.failures += [f for check in checks if (f := check(result.reply))]
            results.append(result)
    return results


def render(cases: list[Case], results: dict[str, list[StepResult]]) -> str:
    lines = ["# Resultados P0", ""]
    for case in cases:
        case_results = results[case.id]
        failed = any(r.failures for r in case_results)
        verdict = "❌ FALLA (automático)" if failed else "✅ sin fallas automáticas"
        lines += [f"## {case.id} · {case.title} — {verdict}", ""]
        lines += [
            "| Paso | Patricia | Contigo respondió | Contigo debe | Revisión |",
            "|---|---|---|---|---|",
        ]
        for r in case_results:
            reply = "—" if r.skipped else (r.reply or "*(sin respuesta)*")
            review = r.skipped or ("; ".join(r.failures) if r.failures else "ok")
            mark = "⏭" if r.skipped else ("❌" if r.failures else "✅")
            message = r.step.message or f"*({r.step.note or 'no texto'})*"
            cells = [r.step.label, message, reply, r.step.expected, f"{mark} {review}"]
            lines.append(
                "| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |"
            )
        lines += ["", f"**Revisión humana — aprueba si** {case.approve}", ""]
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--case", action="append", help="Solo estos casos (p. ej. --case C05)")
    parser.add_argument("--agent", choices=["mock", "llm"], help="Forzar el agente (en proceso)")
    parser.add_argument("--url", help="Probar contra un despliegue (requiere TWILIO_AUTH_TOKEN)")
    parser.add_argument("--report", type=Path, help="Guardar el reporte en un archivo .md")
    args = parser.parse_args(argv)

    cases = parse_cases(CASES_PATH.read_text(encoding="utf-8"))
    if args.case:
        cases = [c for c in cases if c.id in set(args.case)]

    if args.url:
        token = os.environ.get("TWILIO_AUTH_TOKEN") or get_settings().twilio_auth_token
        if not token:
            print("Falta TWILIO_AUTH_TOKEN para firmar las solicitudes.", file=sys.stderr)
            return 2
        send = remote_sender(args.url, token)
    else:
        send = local_sender(args.agent)

    results = {case.id: await run_case(case, send) for case in cases}
    report = render(cases, results)
    print(report)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
        print(f"\nReporte guardado en {args.report}", file=sys.stderr)
    return 1 if any(r.failures for rs in results.values() for r in rs) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
