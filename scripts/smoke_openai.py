"""Un turno contra el proveedor real, sin Twilio y sin el webhook.

Para verificar que la llave, el modelo y el esquema funcionan antes de gastar
tiempo en el runner completo. Lee el .env como la app.

    uv run python scripts/smoke_openai.py
    uv run python scripts/smoke_openai.py "cuanto es mi pago minimo?" E2
"""

import asyncio
import sys
import time

from app.core.config import get_settings
from app.modules.agent.factory import build_agent
from app.modules.agent.interface import AgentRequest, ConversationMode
from app.modules.banking.provider import build_bank_provider
from app.modules.banking.service import credit_card_fields
from app.modules.conversation.figures import fields_to_send


async def main() -> int:
    mensaje = sys.argv[1] if len(sys.argv) > 1 else "hola, quiero ver lo de la tarjeta"
    modo = ConversationMode(sys.argv[2]) if len(sys.argv) > 2 else ConversationMode.GREETING

    settings = get_settings()
    print(f"AGENT_IMPL={settings.agent_impl}  modelo={settings.openai_model}  "
          f"respaldo={settings.openai_fallback_model}  temp={settings.openai_temperature}")
    print(f"timeout proveedor={settings.openai_timeout_s}s  presupuesto del turno="
          f"{settings.agent_turn_budget_s}s  llave={'puesta' if settings.openai_api_key else
              'FALTA'}")
    if settings.agent_impl != "llm":
        print("\nAGENT_IMPL no es 'llm': esto va a responder con el agente simulado, "
              "sin tocar OpenAI.")
    print()

    # Los mismos datos que le llegarian en un turno real de ese modo.
    datos = {}
    bank = build_bank_provider(settings)
    if bank is not None:
        snap = await bank.get_snapshot("+56900000000")
        if snap and snap.credit_cards:
            disponibles = credit_card_fields(snap.credit_cards[0])
            datos = {n: disponibles[n] for n in fields_to_send(modo, None) if n in disponibles}

    request = AgentRequest(
        user_id="+56900000000", message=mensaje, mode=modo,
        financial_data=datos, can_offer_continue=True,
    )
    print(f'modo={modo.value}  campos inyectados={len(datos)}')
    print(f'ella: "{mensaje}"')

    agente = build_agent(settings)
    inicio = time.monotonic()
    try:
        respuesta = await agente.process(request)
    except Exception as exc:
        print(f"\nFALLO: {type(exc).__name__}: {exc}")
        print("\nSi dice 'model_not_found', el id de OPENAI_MODEL no existe en tu cuenta.")
        print("Si dice 401, la llave esta mal. Si dice 'response_format', el modelo no "
              "soporta structured output estricto.")
        return 1
    ms = int((time.monotonic() - inicio) * 1000)

    print(f'\nContigo: "{respuesta.message}"')
    print(f"\netiqueta={respuesta.intent}  evento={respuesta.event}  "
          f"esencial={respuesta.essential}  monto={respuesta.mentioned_amount}  "
          f"texto_protegido={respuesta.protected_text}")
    print(f"latencia={ms} ms")
    if respuesta.protected_text:
        print("\nOjo: texto_protegido=True significa que esto NO lo redacto el modelo; "
              "es un texto fijo o el respaldo del modo. Revisa la traza para ver por que.")
    if settings.agent_trace_enabled:
        print(f"traza: {settings.agent_trace_path}")
    else:
        print("traza apagada (AGENT_TRACE_ENABLED=false): enciendela para ver el contexto "
              "exacto y que chequeo fallo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
