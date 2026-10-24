"""El agente simulado, contra el contrato de dos pasos.

Los tests miran la etiqueta, el evento y que nunca invente una cifra. No
miran la prosa exacta: las palabras del mock no son la voz del producto (esa
es T01, de C) y fijarlas en un assert solo hace que cambiarlas rompa tests.
"""

import asyncio
import re

import pytest

from app.modules.agent.interface import (
    AgentReply,
    Classification,
    ClassifyRequest,
    ConversationEvent,
    ConversationMode,
    Intent,
)
from app.modules.agent.mock import MockAgent


def clasificar(mensaje: str, modo=ConversationMode.GREETING) -> Classification:
    peticion = ClassifyRequest(user_id="+56912345678", message=mensaje, mode=modo)
    return asyncio.run(MockAgent().classify(peticion))


def redactar(mensaje: str, modo=ConversationMode.GREETING, **extra) -> AgentReply:
    from app.modules.agent.interface import WriteRequest

    clasificacion = clasificar(mensaje, modo)
    peticion = WriteRequest(
        user_id="+56912345678", message=mensaje, mode=modo,
        classification=clasificacion, **extra,
    )
    return asyncio.run(MockAgent().write(peticion))


def test_un_saludo_tiene_su_propia_etiqueta() -> None:
    """No es `ambiguo`: esa incrementa `recognition_failures` y dos saludos
    disparaban las opciones de rescate para alguien que solo saludo."""
    clasificacion = clasificar("Hola")

    assert clasificacion.intent == Intent.GREETING_ONLY
    assert clasificacion.event is None


def test_pedir_el_saldo_emite_su_evento() -> None:
    clasificacion = clasificar("Quiero saber cuánto debo")

    assert clasificacion.intent == Intent.INFO_REQUEST
    assert clasificacion.event is ConversationEvent.REQUESTED_BALANCE


def test_la_negativa_emite_su_evento() -> None:
    clasificacion = clasificar("Prefiero no verlo todavía")

    assert clasificacion.intent == Intent.DECLINED
    assert clasificacion.event is ConversationEvent.DECLINED_BALANCE_REVIEW


def test_la_negativa_gana_sobre_el_pedido_de_saldo() -> None:
    """Es la prioridad del contrato: el rechazo manda."""
    clasificacion = clasificar("No quiero saber cuánto debo")

    assert clasificacion.event is ConversationEvent.DECLINED_BALANCE_REVIEW


def test_la_crisis_emite_su_evento() -> None:
    clasificacion = clasificar("no quiero seguir viviendo")

    assert clasificacion.intent == Intent.DISTRESS
    assert clasificacion.event is ConversationEvent.CRISIS_SUPPORT_NEEDED


def test_toda_etiqueta_del_mock_existe_en_el_contrato() -> None:
    """Un doble que emita una etiqueta imposible hace pasar tests que el
    modelo real no podria pasar. Paso de verdad: `mock.py` emitia "saludo",
    que no esta en `Intent`."""
    etiquetas = {i.value for i in Intent}
    mensajes = [
        "Hola", "no quiero verlo", "cuanto es mi minimo", "comprar en cuotas",
        "gracias, chao", "no quiero seguir viviendo", "asdfgh", "me da miedo",
    ]
    for mensaje in mensajes:
        assert clasificar(mensaje).intent in etiquetas, mensaje


def test_la_crisis_y_el_rechazo_salen_con_su_texto_protegido() -> None:
    for mensaje in ("no quiero seguir viviendo", "no quiero verlo ahora"):
        respuesta = redactar(mensaje)
        assert respuesta.protected_text, mensaje


@pytest.mark.parametrize(
    "mensaje",
    ["Hola", "Me da miedo revisar mi deuda", "Prefiero no", "x", "gracias, chao"],
)
def test_el_mock_no_inventa_cifras(mensaje: str) -> None:
    """Sin datos inyectados no puede aparecer ningun numero, salvo las lineas
    de ayuda del texto de crisis."""
    enviado = redactar(mensaje).message.replace("*4141", "").replace("131", "")
    assert not re.search(r"\d", enviado), enviado
