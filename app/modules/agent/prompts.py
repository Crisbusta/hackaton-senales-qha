"""T04: la parte estatica y el armado del contexto por turno.

El codigo es dueno del estado, el modelo es dueno de las palabras. Aca no
se decide ninguna transicion: se traduce el estado que ya calculo T06 a los
tres bloques que ve el modelo.

La parte estatica vive en prompt_estatico.txt (identica en cada llamada,
cacheable). El mapa de modos es la fuente de verdad de la inyeccion: lo que
no esta aca no entra al contexto y por lo tanto el modelo no lo puede
filtrar.
"""

import json
import os

_AQUI = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_AQUI, "static_prompt.txt"), encoding="utf-8") as f:
    ESTATICO = f.read()

MODOS = ("E0", "E1", "E2", "E3", "E4", "PAUSA_RECHAZO", "PAUSA_COMPRA", "APOYO", "CIERRE")

# Nada fuera de esta tabla entra al contexto.
CAMPOS_POR_MODO = {
    "E0": (),
    "E1": ("vencimiento",),
    "E2": ("vencimiento", "minimo", "saldo"),
    "E3": ("vencimiento", "minimo", "saldo",
           "minimo_intereses", "minimo_cuotas", "minimo_abono"),
    "E4": ("vencimiento", "minimo", "saldo", "monto_elegido"),
    "PAUSA_RECHAZO": (),
    "PAUSA_COMPRA": (),
    "APOYO": (),
    "CIERRE": (),
}

# Condicionales fuera de escalon: solo si ella pregunto en el turno anterior.
CONDICIONALES = {
    "pregunta_monto_facturado": ("facturado", ("E2", "E3", "E4")),
    "pregunta_intereses": ("tasa", ("E3", "E4")),
}

# Si ella elige ver cuanto suma la cuota, el codigo agrega estos dos.
CAMPOS_PAUSA_COMPRA_CALCULO = ("minimo", "minimo_con_cuota")

MOVIMIENTOS = {
    "E0": [
        "Conversar y acusar recibo de lo que ella dijo.",
        "Contarle una sola vez que puedes mirar el estado de cuenta por ella, "
        "de a un dato por vez, y que ella no tiene que abrirlo.",
        "No menciones ninguna cifra: en este turno no tienes ningun dato.",
    ],
    "E1": [
        "Contarle la fecha de vencimiento.",
        "No nombres el pago minimo ni el saldo.",
    ],
    "E2": [
        "Contarle el pago minimo y el saldo adeudado, en 1 a 3 frases.",
        "No expliques todavia que hay detras del minimo.",
        "No digas que deberia pagar.",
    ],
    "E3": [
        "Contarle en que se reparte el pago minimo, con las cifras inyectadas.",
        "Puedes decir que solo una parte baja la deuda. Dilo como dato, sin "
        "juzgar y sin agregar que convendria hacer.",
    ],
    "E4": [
        "Contarle el resultado que llega en DATOS_DISPONIBLES para el monto "
        "que ella eligio. Los calculos ya vienen resueltos: no los rehagas.",
        "Si te pregunta que monto conviene, dile que esa decision la ve con "
        "el banco. No propongas tu ningun monto.",
        "Si ella no ha dicho ningun monto, preguntale cuanto quiere probar y "
        "no inventes un ejemplo.",
    ],
    "PAUSA_RECHAZO": [
        "Ninguno: la respuesta es el texto fijo de rechazo_pausa.",
    ],
    "PAUSA_COMPRA": [
        "Si esencial es false: ofrecerle dos opciones numeradas, una sola vez.",
        "  1. Esperar diez minutos y ver como se siente.",
        "  2. Ver cuanto le sumaria esa cuota a su pago minimo.",
        "Ella decide. No empujes ninguna de las dos.",
        "Si esencial es true: NO ofrezcas esperar ni mostrar cuanto suma. "
        "Reconoce lo que necesita y no pongas friccion.",
        "No le digas que no compre. No la hagas sentir mal por querer comprar.",
    ],
    "APOYO": [
        "Acompanar en 1 o 2 frases. Nada financiero: ninguna cifra, ninguna "
        "fecha, ninguna mencion de las cuentas.",
        "No preguntes como se siente ni le pongas nombre a lo que siente.",
        "No la invites a retomar. Si ella retoma, el codigo cambia el modo.",
    ],
    "CIERRE": [
        "Despedirte en una o dos frases.",
        "No resumas cifras que ella no pidio.",
        "No prometas escribir primero ni avisarle nada.",
    ],
}

# Para retoma_voluntaria (C, 23 oct): reconocer de que se hablo sin
# repetir cifras. Solo cubre los escalones de la Escalera (E1-E4); PAUSA_RECHAZO,
# PAUSA_COMPRA, APOYO y CIERRE son desvios temporales, no cuentan.
RESUMEN_POR_ESCALON = {
    "E1": "la fecha de vencimiento",
    "E2": "tu pago minimo y tu saldo",
    "E3": "tu pago minimo, tu saldo y en que se reparte el minimo",
    "E4": "un monto que probaste",
}

MOVIMIENTOS_RETOMA = [
    "Reconoce lo que ya vio sin repetir cifras: la vez pasada vieron {resumen}.",
    "Ofrece seguir desde ahi o elegir algo mas simple para hoy.",
    "No le pongas 'logro' ni la felicites de mas.",
]

# El cierre con la oferta lo permite AB2, una vez por escalon.
MOVIMIENTO_OFERTA = ('Cerrar con "seguimos o lo dejamos aqui?" una sola vez.')
MODOS_CON_OFERTA = ("E1", "E2", "E3")

# La linea de crisis se ofrece una vez (AB7).
MOVIMIENTO_LINEA = "Ofrecer el *4141."


def campos_visibles(modo, pregunta_previa=None, pidio_calculo_cuota=False):
    """Que campos de datos_pdf puede ver el modelo en este turno."""
    campos = list(CAMPOS_POR_MODO.get(modo, ()))
    if modo == "PAUSA_COMPRA" and pidio_calculo_cuota:
        campos += [c for c in CAMPOS_PAUSA_COMPRA_CALCULO if c not in campos]
    condicional = CONDICIONALES.get(pregunta_previa)
    if condicional:
        campo, modos_validos = condicional
        if modo in modos_validos and campo not in campos:
            campos.append(campo)
    return campos


def _formatear_valor(campo):
    valor = campo.get("valor")
    estado = campo.get("estado", "confirmado")
    if estado == "ausente" or valor is None:
        return "(ausente)"
    if isinstance(valor, list):
        return " / ".join(_monto(v) for v in valor) + "  (no confiable)"
    return f"{_monto(valor)}  ({estado})"


def _monto(valor):
    if isinstance(valor, int):
        return "$" + format(valor, ",d").replace(",", ".")
    return str(valor)


def armar_contexto(estado, datos, motor=None, pregunta_previa=None,
                   pidio_calculo_cuota=False):
    """Los tres bloques que el codigo le pasa al modelo cada turno.

    Un bloque vacio se escribe igual con 'ninguno': su ausencia haria que el
    modelo improvise.
    """
    modo = estado.get("modo", "E0")
    if modo not in MODOS:
        raise ValueError(f"modo desconocido: {modo!r}")

    lineas = ["ESTADO", f"modo: {modo}"]
    if modo in MODOS_CON_OFERTA:
        puede_ofrecer = _si_no(estado.get("puedes_ofrecer_continuar", False))
        lineas.append(f"puedes_ofrecer_continuar: {puede_ofrecer}")
    puede_aclarar = _si_no(estado.get("puedes_aclarar_ruta", False))
    lineas.append(f"puedes_aclarar_ruta: {puede_aclarar}")
    limites = estado.get("limites_ya_dichos") or []
    lineas.append("limites_ya_dichos: [{}]".format(", ".join(limites)))
    escalon_maximo = estado.get("escalon_maximo", "E0")
    lineas.append(f"escalon_maximo: {escalon_maximo}")

    lineas += ["", "DATOS_DISPONIBLES"]
    visibles = campos_visibles(modo, pregunta_previa, pidio_calculo_cuota)
    presentes = [c for c in visibles if c in (datos or {})]
    if not presentes and not motor:
        lineas.append("ninguno")
    for campo in presentes:
        lineas.append(f"{campo}: {_formatear_valor(datos[campo])}")
    for clave, valor in (motor or {}).items():
        lineas.append(f"{clave}: {_monto(valor)}  (calculado)")

    lineas += ["", "MOVIMIENTOS PERMITIDOS EN ESTE TURNO"]
    if modo == "E0" and escalon_maximo in RESUMEN_POR_ESCALON:
        resumen = RESUMEN_POR_ESCALON[escalon_maximo]
        movimientos = [m.format(resumen=resumen) for m in MOVIMIENTOS_RETOMA]
    else:
        movimientos = list(MOVIMIENTOS[modo])
    if modo in MODOS_CON_OFERTA and estado.get("puedes_ofrecer_continuar"):
        movimientos.append(MOVIMIENTO_OFERTA)
    if modo == "APOYO" and "linea_crisis" not in limites:
        movimientos.append(MOVIMIENTO_LINEA)
    for m in movimientos:
        lineas.append(m if m.startswith("  ") else "- " + m)

    return "\n".join(lineas)


def _si_no(valor):
    return "si" if valor else "no"


if __name__ == "__main__":
    datos = json.load(open(os.path.join(_AQUI, "fixture.json")))
    for modo in ("E1", "E2", "APOYO"):
        print("=" * 60)
        print(armar_contexto({"modo": modo, "puedes_ofrecer_continuar": True},
                             datos))
