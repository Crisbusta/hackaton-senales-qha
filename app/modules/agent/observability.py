"""Una linea JSONL por turno. No esta en el tablero: decision propia.

Doble uso. Primero, depurar: reconstruir por que respondio lo que respondio
sin volver a ejecutar nada. Segundo, el pitch de T12 — mostrar una traza
real es evidencia, no promesa.

DoD de T06: no se persisten datos sensibles innecesarios. Logs locales,
solo datos de demo, se borran al cerrar (`limpiar()`).
"""

import json
import os
import time

RUTA = os.environ.get("CONTIGO_LOG", "turnos.jsonl")


def registrar(conv, turno, entrada, prefiltro, estado_antes, contexto,
              modelo, salida_cruda, resultado, estado_despues,
              regla_aplicada=None, ruta=None):
    """Los tres campos que hacen que esto sirva: regla_aplicada, validadores
    y override."""
    linea = {
        "conv": conv,
        "turno": turno,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "entrada": entrada,
        "prefiltro": {"disparo": prefiltro[0], "patron": prefiltro[1]},
        "estado_antes": estado_antes,
        "contexto_inyectado": {
            "campos": _campos(contexto),
            "movimientos": _movimientos(contexto),
        },
        "modelo": modelo,
        "salida_cruda": salida_cruda,
        "validadores": resultado.validadores,
        "override": resultado.override,
        "enviado": resultado.enviado,
        "estado_despues": estado_despues,
        "regla_aplicada": regla_aplicada,
    }
    with open(ruta or RUTA, "a", encoding="utf-8") as f:
        f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    return linea


def _campos(contexto):
    """De 'minimo: $74.350  (confirmado)' a 'minimo:confirmado'. Se guarda el
    nombre y el estado, no el valor: el valor ya esta en el fixture."""
    dentro = False
    campos = []
    for linea in (contexto or "").split("\n"):
        if linea.startswith("DATOS_DISPONIBLES"):
            dentro = True
            continue
        if dentro:
            if not linea.strip():
                break
            if linea.strip() == "ninguno":
                break
            nombre = linea.split(":", 1)[0].strip()
            estado = "confirmado"
            if "(" in linea:
                estado = linea.rsplit("(", 1)[1].rstrip(")").strip()
            campos.append(f"{nombre}:{estado}")
    return campos


def _movimientos(contexto):
    dentro = False
    movs = []
    for linea in (contexto or "").split("\n"):
        if linea.startswith("MOVIMIENTOS PERMITIDOS"):
            dentro = True
            continue
        if dentro and linea.startswith("- "):
            movs.append(linea[2:])
    return movs


def leer(ruta=None):
    ruta = ruta or RUTA
    if not os.path.exists(ruta):
        return []
    with open(ruta, encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def traza(conv=None, ruta=None):
    """Resumen legible de una conversacion. Esto es lo que se muestra en el
    pitch: ella dijo esto, se etiqueto asi, se disparo esta regla."""
    salida = []
    for t in leer(ruta):
        if conv and t["conv"] != conv:
            continue
        fallas = [k for k, v in t["validadores"].items() if v == "falla"]
        salida.append(
            "t{:<2} {:<6} {:<24} {}{}".format(
                t["turno"],
                t["estado_antes"].get("modo", "?"),
                (t["salida_cruda"] or {}).get("etiqueta", "-"),
                t.get("regla_aplicada") or "-",
                ("  [override: {}]".format(t["override"]["motivo"]))
                if t["override"]["aplicado"]
                else ("  [falla: {}]".format(", ".join(fallas)) if fallas else ""),
            )
        )
    return "\n".join(salida)


def limpiar(ruta=None):
    ruta = ruta or RUTA
    if os.path.exists(ruta):
        os.remove(ruta)
