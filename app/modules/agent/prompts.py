"""T04: los dos prompts y el armado del contexto de cada paso.

El codigo es dueno del estado, el modelo es dueno de las palabras. Aca no se
decide ninguna transicion: se traduce el turno que el backend ya resolvio a
los bloques que ve cada llamada.

Son dos prompts porque el turno son dos pasos:

- CLASIFICADOR: ve el mensaje de ella, el modo y el historial enmascarado.
  No recibe ninguna cifra, asi que esta llamada no puede filtrar un monto.
- REDACTOR: ve los datos exactos del modo final y los movimientos ya
  resueltos. No evalua ninguna condicion de estado porque no le llega
  ninguna: donde antes el contexto decia "si esencial es false, ofrece dos
  opciones; si es true, no ofrezcas nada", ahora el codigo escribe una de las
  dos y la otra no existe.

Los dos archivos .txt traen una cabecera de documentacion que NO viaja al
modelo: `cargar()` recorta todo lo que esta antes del separador. Son tokens
que se pagarian en cada turno y que el modelo puede leer como instruccion.
"""

import os
import re

from app.modules.agent.interface import (
    CONDITIONAL_FIELDS,
    FIELDS_BY_MODE,
    INSTALLMENT_CALC_FIELDS,
    ConversationMode,
    Intent,
    calculations_allowed,
    fields_for_mode,
)

_AQUI = os.path.dirname(os.path.abspath(__file__))

SEPARADOR = "# --------------------------- PROMPT ---------------------------"


def cargar(nombre):
    """El prompt de un archivo, sin su cabecera de documentacion."""
    with open(os.path.join(_AQUI, nombre), encoding="utf-8") as f:
        texto = f.read()
    _, _, prompt = texto.partition(SEPARADOR)
    return (prompt or texto).lstrip("\n")


CLASIFICADOR = cargar("prompt_clasificador.txt")
REDACTOR = cargar("prompt_redactor.txt")

MODOS = tuple(m.value for m in ConversationMode)

# La tabla de campos por modo vive en interface.py: es contrato, porque el
# backend filtra con ella y el agente vuelve a filtrar como defensa en
# profundidad. Estos alias existen para no tener dos tablas.
CAMPOS_POR_MODO = {str(modo): campos for modo, campos in FIELDS_BY_MODE.items()}
CONDICIONALES = {
    etiqueta: (campo, tuple(str(m) for m in modos))
    for etiqueta, (campo, modos) in CONDITIONAL_FIELDS.items()
}
CAMPOS_PAUSA_COMPRA_CALCULO = INSTALLMENT_CALC_FIELDS

# --- movimientos --------------------------------------------------------
#
# Por modo, lo que el turno debe lograr. Ninguna linea empieza con "si" sobre
# el estado de la conversacion: lo que depende del estado lo resuelve
# `movimientos_para()`, que si lo conoce.

MOVIMIENTOS = {
    "E0": [
        "Acusar recibo de lo que ella dijo, en una frase.",
        "Las cifras que puedes decir son las de DATOS QUE PUEDES DECIR y ninguna "
        "otra. Si ese bloque dice 'ninguno', no menciones ninguna cifra.",
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
        "Explica un solo termino, el que ella pregunto.",
    ],
    "E4": [
        "Contarle el resultado que llega en DATOS QUE PUEDES DECIR para el monto "
        "que ella eligio. Los calculos ya vienen resueltos: no los rehagas.",
        "Si te pregunta que monto conviene, dile que esa decision la ve con "
        "el banco. No propongas tu ningun monto.",
    ],
    "PAUSA_RECHAZO": [
        "Acoger lo que dijo en una frase y dejar la puerta abierta.",
    ],
    "PAUSA_COMPRA": [],  # siempre resuelto por esencial, ver abajo
    "APOYO": [
        "Acompanar en 1 o 2 frases. Nada financiero: ninguna cifra, ninguna "
        "fecha, ninguna mencion de las cuentas.",
        "No preguntes como se siente ni le pongas nombre a lo que siente.",
        "No la invites a retomar.",
    ],
    "CIERRE": [
        "Despedirte en una o dos frases.",
        "No resumas cifras que ella no pidio.",
        "No prometas escribir primero ni avisarle nada.",
    ],
}

# E4 sin monto: el backend solo entra a E4 cuando ella nombro un monto, asi
# que esto cubre el caso en que el calculo no se pudo hacer.
MOVIMIENTO_E4_SIN_MONTO = (
    "Preguntarle cuanto quiere probar. No inventes un ejemplo."
)

# PAUSA_COMPRA: las dos ramas que antes el modelo tenia que elegir leyendo
# "si esencial es false". Ahora el codigo ya sabe y escribe una sola.
MOVIMIENTOS_COMPRA_NO_ESENCIAL = [
    "Agradecer que te lo contara antes de decidir.",
    "Ofrecerle estas dos opciones, numeradas, una por linea:",
    "  1. Esperar diez minutos y ver como se siente.",
    "  2. Ver cuanto le sumaria esa cuota a su pago minimo.",
    "Decir que ella decide. No empujar ninguna de las dos.",
    "No le digas que no compre ni la hagas sentir mal por querer comprar.",
]
MOVIMIENTOS_COMPRA_ESENCIAL = [
    "Reconocer lo que necesita, sin poner ninguna friccion.",
    "NO ofrecer esperar ni mostrar cuanto sumaria: eso no se posterga.",
    "No le digas que no compre ni la hagas sentir mal.",
]
# Ya eligio la opcion 2 y dio el monto: el calculo llega resuelto y lo unico
# que falta es contarlo. Sin esto el modelo volvia a ofrecer las dos opciones
# con la cifra ya en el contexto (C05 turno 3, medido el 23 oct).
MOVIMIENTOS_CUOTA_CALCULADA = [
    "Contarle cuanto le sumaria esa cuota a su pago minimo, con las cifras "
    "inyectadas: la cuota mensual y el minimo nuevo.",
    "NO volver a ofrecer las dos opciones: ella ya eligio y ya dio el monto.",
    "No le digas que no compre ni la hagas sentir mal. La decision es suya.",
]
# Eligio la opcion 2 pero todavia no dijo el monto (C05 turno 2).
MOVIMIENTOS_PEDIR_MONTO_CUOTAS = [
    "Preguntarle una sola vez el monto y en cuantas cuotas.",
    "NO volver a ofrecer las dos opciones: ella ya eligio esta.",
    "No le digas que no compre ni la hagas sentir mal por querer comprar.",
]

# Retoma: reconocer de que se hablo sin repetir cifras (C, 23 oct). Solo los
# escalones de la Escalera; las pausas, APOYO y CIERRE son desvios.
RESUMEN_POR_ESCALON = {
    "E1": "la fecha de vencimiento",
    "E2": "tu pago minimo y tu saldo",
    "E3": "tu pago minimo, tu saldo y en que se reparte el minimo",
    "E4": "un monto que probaste",
}
MOVIMIENTOS_RETOMA = [
    "Reconocer lo que ya vio sin repetir cifras: la vez pasada vieron {resumen}.",
    "Ofrecer seguir desde ahi o elegir algo mas simple para hoy.",
    "No le pongas 'logro' ni la felicites de mas.",
]

# AB2: una oferta por escalon.
#
# Las instrucciones de este archivo van sin acentos, pero una frase que el
# modelo tiene que reproducir TEXTUAL si los lleva: medido contra el modelo
# real, con la frase sin tildes contesto "¿seguimos o lo dejamos aqui?" y se
# lo mando asi a Patricia.
MOVIMIENTO_OFERTA = (
    'Cerrar ofreciendo seguir, una sola vez, con esta frase tal cual: '
    '"¿Seguimos o lo dejamos aquí?"'
)
MODOS_CON_OFERTA = ("E1", "E2", "E3")
# AB7: la linea de crisis se ofrece una vez.
MOVIMIENTO_LINEA = "Ofrecer el *4141."
# AB1/AB8: cuando ella ya se perdio una vez.
MOVIMIENTO_ACLARAR = (
    "Preguntarle UNA vez que lo complica y ofrecerle hasta tres caminos "
    'numerados, uno por linea, con la salida escrita "o lo dejamos aquí".'
)
# El ritmo que ella pidio en este mensaje. Solo tiene sentido donde hay datos
# que dosificar.
MOVIMIENTO_RITMO = {
    "mas_lento": "Ella pidio ir mas despacio: dale un solo dato y nada mas.",
    "todo_de_una": "Ella pidio verlo todo junto: dale los datos de arriba sin "
                   "fraccionarlos mas.",
}
# Instrucciones que dependen de que clave del simulador llego.
INSTRUCCION_POR_CALCULO = {
    "intereses_del_mes": (
        "Decirle, sin alarmar, que con ese monto la deuda no alcanza a bajar "
        "porque los intereses del mes son mayores."
    ),
}
INSTRUCCION_CALCULO_ILUSTRATIVO = (
    "Decir una vez, en pocas palabras, que es un calculo aproximado y no una "
    "oferta del banco."
)
# Claves que NO son un dato decible: disparan una instruccion. Si entraran al
# bloque de datos, el contexto se contradiria consigo mismo.
# `faltante_para_gastos_esenciales` sigue sin texto validado por C, asi que
# hoy su instruccion es callarlo (ver CLAUDE.md, limitaciones conocidas).
CALCULOS_NO_DECIBLES = ("faltante_para_gastos_esenciales",)


def movimientos_para(
    modo,
    clasificacion=None,
    puede_ofrecer=False,
    puede_aclarar=False,
    limites=(),
    escalon_maximo=None,
    pidio_calculo_cuota=False,
    datos=None,
    calculos=None,
):
    """Los movimientos de este turno, ya resueltos. Ninguno es condicional.

    Todo lo que antes el prompt le pedia evaluar al modelo se evalua aca: el
    modo final, si la compra es esencial, que claves del simulador llegaron,
    si AB2 permite la oferta, si AB7 ya dijo la linea, el ritmo que ella
    pidio. La lista que sale es lo que hay que hacer, sin excepciones
    escritas.
    """
    modo = str(modo)
    datos = datos or {}
    calculos = calculos or {}
    etiqueta = (clasificacion.intent if clasificacion else None) or ""
    esencial = clasificacion.essential if clasificacion else None
    ritmo = clasificacion.pace if clasificacion else None

    if modo == "E0" and str(escalon_maximo or "E0") in RESUMEN_POR_ESCALON:
        resumen = RESUMEN_POR_ESCALON[str(escalon_maximo)]
        movimientos = [m.format(resumen=resumen) for m in MOVIMIENTOS_RETOMA]
    elif modo == "PAUSA_COMPRA":
        if _tiene_valor(datos, "minimo_con_cuota"):
            movimientos = list(MOVIMIENTOS_CUOTA_CALCULADA)
        elif pidio_calculo_cuota:
            movimientos = list(MOVIMIENTOS_PEDIR_MONTO_CUOTAS)
        elif esencial:
            movimientos = list(MOVIMIENTOS_COMPRA_ESENCIAL)
        else:
            movimientos = list(MOVIMIENTOS_COMPRA_NO_ESENCIAL)
    elif modo == "E4" and not _tiene_valor(datos, "monto_elegido"):
        movimientos = [MOVIMIENTO_E4_SIN_MONTO]
    else:
        movimientos = list(MOVIMIENTOS[modo])

    if modo in MODOS_CON_OFERTA and puede_ofrecer:
        movimientos.append(MOVIMIENTO_OFERTA)
    if modo == "APOYO" and "linea_crisis" not in (limites or ()):
        movimientos.append(MOVIMIENTO_LINEA)
    if puede_aclarar and etiqueta == Intent.AMBIGUOUS:
        movimientos.append(MOVIMIENTO_ACLARAR)
    decibles = {k: v for k, v in calculos.items() if k not in CALCULOS_NO_DECIBLES}
    if decibles:
        movimientos.append(INSTRUCCION_CALCULO_ILUSTRATIVO)
    for clave in calculos:
        if clave in INSTRUCCION_POR_CALCULO:
            movimientos.append(INSTRUCCION_POR_CALCULO[clave])
    if (datos or decibles) and ritmo in MOVIMIENTO_RITMO:
        movimientos.append(MOVIMIENTO_RITMO[ritmo])
    return movimientos


# --- datos --------------------------------------------------------------


def campos_visibles(modo, pregunta_previa=None, pidio_calculo_cuota=False):
    """Que campos puede ver el modelo en este turno."""
    return fields_for_mode(modo, pregunta_previa, pidio_calculo_cuota)


def calculos_visibles(modo, calculos, pidio_calculo_cuota=False):
    """Los resultados del simulador admisibles en este turno.

    Se filtra aca porque en E0 o en APOYO una cifra calculada seria
    exactamente la fuga que la inyeccion minima evita. Lo que devuelve esta
    funcion es tambien lo que autoriza la lista blanca numerica.
    """
    if not calculos:
        return {}
    if not calculations_allowed(modo, pidio_calculo_cuota):
        return {}
    return dict(calculos)


_DIGITOS = re.compile(r"\d[\d.,]{2,}")


def _enmascarar_montos(texto):
    """Toda cifra de tres digitos o mas pasa a una elipsis.

    El historial esta para resolver referencias ("si", "la segunda"), nunca
    para que el modelo copie una cifra de ahi.
    """
    return _DIGITOS.sub("...", texto or "")


def _tiene_valor(datos, campo):
    d = (datos or {}).get(campo)
    if not isinstance(d, dict):
        return False
    return d.get("valor") is not None and d.get("estado") != "ausente"


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


# Claves del simulador que NO son pesos. `meses_ahorrados: 19` salia como
# "$19" y el modelo lo repetia como dinero.
_PREFIJOS_NO_MONTO = ("meses_",)


def _formatear_calculo(clave, valor):
    if clave.startswith(_PREFIJOS_NO_MONTO):
        return f"{valor} meses"
    return _monto(valor)


def _historial(historial):
    lineas = []
    for turno in list(historial or [])[-2:]:
        quien = "ella" if turno.get("rol") == "user" else "tu"
        lineas.append(f"{quien}: {_enmascarar_montos(turno.get('texto'))}")
    return lineas or ["ninguno"]


# --- los dos contextos ---------------------------------------------------


def contexto_clasificacion(modo, mensaje, historial=None):
    """Lo que ve el clasificador. Sin una sola cifra."""
    if str(modo) not in MODOS:
        raise ValueError(f"modo desconocido: {modo!r}")
    lineas = [f"MODO: {modo}", "", "LOS DOS ULTIMOS TURNOS (las cifras van enmascaradas)"]
    lineas += _historial(historial)
    lineas += ["", "MENSAJE DE PATRICIA: " + mensaje]
    return "\n".join(lineas)


def contexto_redaccion(datos, calculos, movimientos, mensaje, historial=None):
    """Lo que ve el redactor. Tres bloques, todos en firme.

    Un bloque vacio se escribe igual con 'ninguno': su ausencia haria que el
    modelo improvise.
    """
    datos = datos or {}
    calculos = calculos or {}
    decibles = [c for c in calculos if c not in CALCULOS_NO_DECIBLES]

    lineas = ["DATOS QUE PUEDES DECIR"]
    if not datos and not decibles:
        lineas.append("ninguno")
    for campo in datos:
        lineas.append(f"{campo}: {_formatear_valor(datos[campo])}")
    for clave in decibles:
        lineas.append(f"{clave}: {_formatear_calculo(clave, calculos[clave])}  (calculado)")

    lineas += ["", "LO QUE TIENES QUE HACER"]
    if not movimientos:
        lineas.append("- Acoger lo que dijo en una frase y nada mas.")
    for m in movimientos:
        lineas.append(m if m.startswith("  ") else "- " + m)

    lineas += ["", "LOS DOS ULTIMOS TURNOS (para reconocer lo que dijo y no repetirte)"]
    lineas += _historial(historial)
    lineas += ["", "MENSAJE DE PATRICIA: " + mensaje]
    return "\n".join(lineas)
