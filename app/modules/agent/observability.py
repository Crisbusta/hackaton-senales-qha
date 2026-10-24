"""Una linea JSONL por turno. No esta en el tablero: decision propia.

Doble uso. Primero, depurar: reconstruir por que respondio lo que respondio
sin volver a ejecutar nada. Segundo, el pitch de T12 — mostrar una traza
real es evidencia, no promesa.

La linea guarda el mensaje completo de la usuaria y la respuesta con
cifras, asi que la traza viene **apagada** (`AGENT_TRACE_ENABLED=false`) y
no se puede encender con `APP_ENV=production`. El identificador de
conversacion se guarda seudonimizado: `user_id` es el telefono completo y
no tiene por que quedar en disco. Para el pitch la traza sirve igual con un
id opaco, porque lo que se muestra es la regla que se aplico, no quien
escribio.
"""

import hashlib
import json
import os
import time

RUTA = "turnos.jsonl"


def _destino(ruta=None):
    """La ruta donde escribir, o None si la traza esta apagada.

    Una ruta explicita manda (es lo que hacen los tests y el uso local);
    si no hay ninguna, decide la configuracion.
    """
    if ruta:
        return ruta
    try:
        from app.core.config import get_settings

        ajustes = get_settings()
    except Exception:  # pragma: no cover - sin configuracion no se traza
        return None
    return ajustes.agent_trace_path if ajustes.agent_trace_enabled else None


def seudonimo(conv):
    """Un id estable y opaco. No reversible a numero de telefono."""
    if conv is None:
        return None
    return "c-" + hashlib.sha256(str(conv).encode("utf-8")).hexdigest()[:12]


def _escribir(linea, ruta=None):
    destino = _destino(ruta)
    if not destino:
        return None
    linea = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **linea}
    with open(destino, "a", encoding="utf-8") as f:
        f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    return destino


def registrar_clasificacion(conv, entrada, prefiltro, modo, contexto, modelo,
                            salida_cruda, etiqueta, ruta=None,
                            latencia_total_ms=None):
    """El paso 1. No lleva ninguna cifra porque la llamada no recibe ninguna."""
    disparo, patron = prefiltro
    return _escribir({
        "paso": "clasificacion",
        "conv": seudonimo(conv),
        "entrada": entrada,
        "modo": modo,
        "prefiltro": {"disparo": disparo, "patron": patron},
        "modelo": modelo,
        "salida_cruda": salida_cruda,
        "etiqueta": etiqueta,
        "contexto": contexto,
        "latencia_total_ms": latencia_total_ms,
    }, ruta)


def registrar_redaccion(conv, entrada, modo, etiqueta, contexto, modelo,
                        salida_cruda, enviado, validadores, regla_aplicada=None,
                        ruta=None, intentos=1, movimientos=None, campos=None,
                        calculos=None, latencia_total_ms=None):
    """El paso 2.

    `campos`, `calculos` y `movimientos` llegan explicitos, no se deducen del
    texto del contexto. Antes se parseaban, y el parseo omitia el bloque "solo
    si ella lo pide" (en E0 viajaban 9 campos y la traza anotaba `[]`) y tomaba
    su encabezado como si fuera un campo. Quien leyera la traza concluia que el
    modelo no tenia el dato, y era falso.
    """
    return _escribir({
        "paso": "redaccion",
        "conv": seudonimo(conv),
        "entrada": entrada,
        "modo": modo,
        "etiqueta": etiqueta,
        "inyectado": {
            "campos": list(campos or []),
            "calculos": list(calculos or []),
            "movimientos": list(movimientos or []),
        },
        "modelo": modelo,
        "intentos": intentos,
        "salida_cruda": salida_cruda,
        "validadores": validadores,
        "regla_aplicada": regla_aplicada,
        "enviado": enviado,
        "contexto": contexto,
        "latencia_total_ms": latencia_total_ms,
    }, ruta)


def leer(ruta=None):
    ruta = _destino(ruta) or RUTA
    if not os.path.exists(ruta):
        return []
    with open(ruta, encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def traza(conv=None, ruta=None):
    """Resumen legible de una conversacion.

    Esto es lo que se muestra en el pitch: ella dijo esto, se etiqueto asi, se
    disparo esta regla. Con el turno en dos pasos, cada turno son dos lineas y
    se muestran juntas.
    """
    buscado = seudonimo(conv) if conv else None
    salida = []
    for t in leer(ruta):
        if buscado and t.get("conv") != buscado:
            continue
        if t.get("paso") == "clasificacion":
            modelo = (t["modelo"] or {}).get("nombre", "-")
            marca = "prefiltro" if t["prefiltro"]["disparo"] else modelo
            salida.append("  clasifica {:<6} -> {:<24} [{}]".format(
                t.get("modo", "?"), t.get("etiqueta", "-"), marca))
            continue
        fallas = [k for k, v in (t.get("validadores") or {}).items() if v == "falla"]
        detalle = t.get("regla_aplicada") or "-"
        if fallas:
            detalle += "  [falla: {}]".format(", ".join(fallas))
        salida.append("  redacta  {:<6}    {:<24} {}".format(
            t.get("modo", "?"), t.get("etiqueta", "-"), detalle))
    return "\n".join(salida)


def limpiar(ruta=None):
    ruta = _destino(ruta) or RUTA
    if os.path.exists(ruta):
        os.remove(ruta)
