"""Entrar a un modo se responde en el MISMO turno, con una sola redacción.

Este archivo probaba `_reanswer_on_mode_entry`: un segundo llamado al modelo
que existía porque el modo cambiaba después de redactar. Si ella decía "me dan
ganas de comprar en cuotas" estando en E0, el agente redactaba en E0 y las dos
opciones de PAUSA_COMPRA llegaban un turno tarde; el parche era volver a
pedirle la respuesta con el modo nuevo.

Medido contra el modelo real el 23 oct, ese parche fallaba: las 2 entradas de
la muestra terminaron degradadas en ~12 s, y en una de ellas el segundo
llamado produjo la respuesta correcta —las dos opciones, los seis chequeos en
`ok`— y el backend la descartó porque el plazo quedó 0,4 s corto.

Con el turno en tres pasos no hay nada que compensar: la transición corre
entre clasificar y redactar, así que cuando el redactor escribe el modo ya es
el final. Lo que estos tests protegen ahora es eso, y que no haya una segunda
llamada.
"""

import asyncio

from app.modules.agent.interface import ConversationEvent, ConversationMode, Intent
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.service import ConversationService
from app.modules.whatsapp.schemas import IncomingMessage
from tests.conftest import TwoStepAgent, turno

USER_ID = "+56912345678"
OPTIONS = "Podemos esperar diez minutos, o te cuento cuánto le sumaría la cuota. Tú eliges."


def turn(agent, mode=ConversationMode.GREETING, timeout=5.0,
         body="Me dan ganas de comprar en cuotas."):
    repo = InMemoryConversationRepository()
    state = asyncio.run(repo.get(USER_ID))
    state.mode = mode
    asyncio.run(repo.save(state))
    service = ConversationService(agent=agent, repository=repo, agent_timeout_s=timeout)
    incoming = IncomingMessage(
        message_sid="SM1", from_number=f"whatsapp:{USER_ID}", body=body,
    )
    reply = asyncio.run(service.handle(incoming))
    return reply, asyncio.run(repo.get(USER_ID))


def test_el_redactor_recibe_el_modo_final_no_el_de_entrada() -> None:
    """Es la cura del turno de atraso, en una línea."""
    agent = TwoStepAgent(turno(OPTIONS, intent=Intent.INSTALLMENT_INTENT, essential=False))

    reply, state = turn(agent, mode=ConversationMode.GREETING)

    assert reply == OPTIONS
    assert state.mode is ConversationMode.INSTALLMENT_PAUSE
    # El turno entró estando en E0 y el redactor ya vio PAUSA_COMPRA.
    assert agent.last_classify.mode is ConversationMode.GREETING
    assert agent.last_write.mode is ConversationMode.INSTALLMENT_PAUSE


def test_entrar_a_un_modo_no_gasta_una_segunda_redaccion() -> None:
    """Antes eran dos llamadas y ~12 s; ahora es una."""
    agent = TwoStepAgent(turno(OPTIONS, intent=Intent.INSTALLMENT_INTENT, essential=False))

    turn(agent, mode=ConversationMode.GREETING)

    assert len(agent.classify_requests) == 1
    assert len(agent.write_requests) == 1


def test_lo_esencial_de_la_compra_llega_al_redactor() -> None:
    """Si es un remedio no se ofrece esperar, y eso lo decide el clasificador:
    el redactor recibe la rama ya elegida."""
    agent = TwoStepAgent(
        turno("Eso no se posterga.", intent=Intent.INSTALLMENT_INTENT, essential=True)
    )

    _, state = turn(agent, body="tengo que comprar los remedios de mi mamá en cuotas")

    assert state.installment_essential is True
    assert agent.last_write.classification.essential is True


def test_retomar_desde_una_pausa_se_responde_como_bienvenida_en_el_mismo_turno() -> None:
    """C01 turno 4: "hola, ya estoy mejor" recibía el texto de la pausa."""
    agent = TwoStepAgent(turno("Qué bueno que volviste.", intent=Intent.VOLUNTARY_RESUME))

    reply, state = turn(agent, mode=ConversationMode.PAUSE, body="hola, ya estoy mejor")

    assert reply == "Qué bueno que volviste."
    assert state.mode is ConversationMode.GREETING
    assert agent.last_write.mode is ConversationMode.GREETING


def test_salir_de_la_pausa_pidiendo_un_dato_sube_el_escalon_en_el_mismo_turno() -> None:
    """Era una limitación conocida: pedir un dato estando en pausa recibía otra
    vez el texto de la pausa, porque el modo cambiaba después de redactar."""
    agent = TwoStepAgent(
        turno("Tu pago vence el 3 de noviembre de 2026.",
              intent=Intent.INFO_REQUEST,
              event=ConversationEvent.REQUESTED_BALANCE)
    )

    _, state = turn(agent, mode=ConversationMode.PAUSE, body="ya, ¿cuándo vence?")

    assert state.mode is ConversationMode.DUE_DATE
    assert agent.last_write.mode is ConversationMode.DUE_DATE


def test_la_crisis_no_pasa_por_el_redactor() -> None:
    """El texto fijo lo decide la clasificación: no hay prosa que descartar."""
    agent = TwoStepAgent(
        turno("texto de crisis", intent=Intent.DISTRESS,
              event=ConversationEvent.CRISIS_SUPPORT_NEEDED, protected_text=True)
    )

    reply, state = turn(agent, body="no quiero seguir viviendo")

    assert state.mode is ConversationMode.SUPPORT
    assert reply == "texto de crisis"
    assert agent.last_write.mode is ConversationMode.SUPPORT
