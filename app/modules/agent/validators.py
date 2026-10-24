"""Los siete chequeos deterministas sobre cada salida del modelo (T05).

No depende del SDK, de la red ni del contrato de T02: se le pasa el dict ya
parseado. Por eso se puede probar completo antes de que exista el repo.

Orden de los chequeos, el primero que falla corta:
  1. lista_blanca_numerica
  2. frases_prohibidas
  3. forma (frases, preguntas, largo, markdown)
  4. movimientos (oferta de seguir cuando no corresponde)
  5. coherencia_etiqueta_modo
  6. sin_4141_en_cuidadora_agotada
  7. fuga_de_andamiaje (nombres de bloque o de campo en la respuesta)

Politica de fallo: un reintento nombrando el chequeo. Si el segundo intento
tambien falla, sale el texto de respaldo del modo. En angustia_fuerte y
rechazo_pausa no hay reintento: el texto fijo sale directo (capa 3).
"""

import re
import unicodedata
from dataclasses import dataclass, field

from app.modules.agent.texts import (
    CIERRE_OFERTA,
    ETIQUETAS_TEXTO_FIJO,
    FRASES_PALABRA_COMPLETA,
    FRASES_PROHIBIDAS,
    TEXTOS_FIJOS,
    TEXTOS_RESPALDO,
)

MAX_FRASES = 3
MAX_PREGUNTAS = 1
MAX_CARACTERES = 450

# Cifras escritas en palabras: esquivan el chequeo numerico. Solo las de
# magnitud, para no falsear "diez minutos" ni "de a un dato por vez".
PALABRAS_DE_MAGNITUD = ("mil", "miles", "millon", "millones", "luca", "lucas")

MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

# Numeros que siempre puede escribir: las lineas de ayuda.
NUMEROS_SIEMPRE_PERMITIDOS = {"4141", "131"}

RE_NUMERO = re.compile(r"\d{1,3}(?:[.\s]\d{3})+|\d+(?:,\d+)?")
RE_OPCION = re.compile(r"(?m)^\s*[123][.)]\s")
RE_VINETA = re.compile(r"(?m)^\s*[-*•]\s")
RE_OFERTA = re.compile(r"seguimos|lo dejamos (aqui|aca)")
# Una opcion numerada no es una frase: la linea entera sale antes de contar.
# Quitar solo el prefijo "1." no alcanza, porque el texto de cada opcion
# tambien termina en punto: medido el 23 oct, las dos opciones de PAUSA_COMPRA
# dan 6 frases, 4 sin el prefijo y 2 sin la linea.
RE_LINEA_OPCION = re.compile(r"(?m)^\s*[123][.)].*$")


def normalizar(texto):
    """Minusculas y sin acentos, para comparar por subcadena."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _sin_excepciones(texto):
    """Quita lo que no cuenta como cifra: *4141, 131 y los numerales de opcion."""
    t = RE_OPCION.sub("", texto)
    t = t.replace("*4141", "").replace("4141", "")
    t = re.sub(r"\b131\b", "", t)
    return t


def canonico(token):
    """'74.350' y '74350' son el mismo numero. '2,8' se queda como esta."""
    t = token.strip().replace(".", "").replace(" ", "")
    if "," in t:
        return t
    return str(int(t)) if t.isdigit() else t


def extraer_numeros(texto):
    return [canonico(m) for m in RE_NUMERO.findall(_sin_excepciones(texto))]


def enmascarar_numeros(texto):
    """Reemplaza cada numero por 'N'. Sin esto los puntos de miles cuentan
    como fin de frase y '$1.064.000' se lee como tres frases."""
    return RE_NUMERO.sub("N", texto.replace("*4141", "N"))


def numeros_permitidos(datos, motor=None):
    """Toda cifra de los datos inyectados mas la salida del motor T10.

    Autoriza todo lo que recibe, y eso ahora es correcto: con el turno en tres
    pasos, el redactor recibe exactamente los campos del modo final. Antes
    hacia falta `campos_autorizados()` para volver a filtrar por modo y por
    etiqueta, porque la precarga mandaba toda la Escalera y el modo se decidia
    despues. Ese filtro desaparecio con el orden.
    """
    permitidos = set(NUMEROS_SIEMPRE_PERMITIDOS)
    for campo in (datos or {}).values():
        if not isinstance(campo, dict):
            continue
        valor = campo.get("valor")
        if valor is None:
            continue
        valores = valor if isinstance(valor, list) else [valor]
        for v in valores:
            permitidos.update(canonico(m) for m in RE_NUMERO.findall(str(v)))
    for v in (motor or {}).values():
        permitidos.update(canonico(m) for m in RE_NUMERO.findall(str(v)))
    return permitidos


# --- los cinco chequeos: (ok, detalle) ---------------------------------


def chequeo_lista_blanca_numerica(mensaje, datos, motor=None):
    permitidos = numeros_permitidos(datos, motor)
    intrusos = [n for n in extraer_numeros(mensaje) if n not in permitidos]
    if intrusos:
        return False, "cifras no inyectadas: " + ", ".join(sorted(set(intrusos)))
    plano = normalizar(mensaje)
    palabras = re.findall(r"[a-z]+", plano)
    magnitudes = [p for p in palabras if p in PALABRAS_DE_MAGNITUD]
    if magnitudes:
        return False, "cifra escrita en palabras: " + ", ".join(sorted(set(magnitudes)))
    return True, None


def _frase_presente(frase, plano):
    if frase in FRASES_PALABRA_COMPLETA:
        # C (23 oct): "revisa" y "calma" no deben disparar con
        # "revisaremos" ni "calmar".
        return re.search(r"\b" + re.escape(frase) + r"\b", plano) is not None
    return frase in plano


def chequeo_frases_prohibidas(mensaje):
    plano = normalizar(mensaje)
    encontradas = [f for f in FRASES_PROHIBIDAS if _frase_presente(f, plano)]
    if encontradas:
        return False, "frase prohibida: " + ", ".join(encontradas)
    return True, None


def contar_frases(mensaje):
    enmascarado = enmascarar_numeros(RE_LINEA_OPCION.sub("", mensaje))
    partes = [p for p in re.split(r"[.?!…]+", enmascarado) if p.strip()]
    return len(partes)


def _enmascarar_misma_longitud(texto):
    """Como `enmascarar_numeros`, pero sin mover ninguna posicion."""
    return RE_NUMERO.sub(lambda m: "N" * len(m.group(0)), texto)


def recortar_a_tres_frases(mensaje):
    """Las primeras MAX_FRASES frases, con sus opciones numeradas.

    Cuando la respuesta falla SOLO por largo, descartarla entera cambia una
    respuesta buena de mas por un texto de relleno que no dice nada ("Dejame
    ordenarlo bien antes de contartelo"). Recortar conserva lo que ella
    pidio. Las lineas de opcion no se cortan: no son frases y van completas.
    """
    lineas = mensaje.splitlines()
    opciones = [ln for ln in lineas if RE_LINEA_OPCION.match(ln)]
    prosa = " ".join(ln for ln in lineas if not RE_LINEA_OPCION.match(ln)).strip()

    partes, resto = [], prosa
    for _ in range(MAX_FRASES):
        # Se enmascara preservando el largo: los puntos de miles no son fin de
        # frase, y los indices tienen que calzar con el texto original.
        corte = re.search(r"[.?!…]", _enmascarar_misma_longitud(resto))
        if not corte:
            break
        partes.append(resto[: corte.end()].strip())
        resto = resto[corte.end():].strip()
    if not partes:
        partes = [resto]
    recortado = " ".join(p for p in partes if p).strip()
    if opciones:
        recortado = "\n".join([recortado, *opciones])
    return recortado[:MAX_CARACTERES].strip()


def chequeo_forma(mensaje):
    if len(mensaje) > MAX_CARACTERES:
        return False, f"largo {len(mensaje)} > {MAX_CARACTERES} caracteres"
    frases = contar_frases(mensaje)
    if frases > MAX_FRASES:
        return False, f"{frases} frases > {MAX_FRASES}"
    preguntas = mensaje.count("?")
    if preguntas > MAX_PREGUNTAS:
        return False, f"{preguntas} preguntas > {MAX_PREGUNTAS}"
    if RE_VINETA.search(mensaje):
        return False, "vineta: WhatsApp va en texto plano"
    sin_linea = mensaje.replace("*4141", "")
    if any(c in sin_linea for c in ("*", "_", "#", "`")):
        return False, "markdown: WhatsApp va en texto plano"
    return True, None


# AB2 solo existe en los escalones de la Escalera. En PAUSA_COMPRA, APOYO,
# etc., "lo dejamos aqui" es parte del texto normal, no una oferta de seguir.
MODOS_CON_OFERTA = ("E1", "E2", "E3")


def es_oferta_de_continuar(mensaje):
    """Si la respuesta ofrece seguir mirando.

    La usan los dos lados: el validador para rechazar la oferta cuando AB2 ya
    la consumio, y el backend para consumir el contador. Estaban separados —el
    backend miraba `"seguimos" in reply.lower()`— y ampliar uno solo deja la
    asimetria peor: rechaza mas formas sin consumir el contador.
    """
    return bool(RE_OFERTA.search(normalizar(mensaje)))


def chequeo_movimientos(mensaje, estado):
    if estado.get("modo") not in MODOS_CON_OFERTA:
        return True, None
    if estado.get("puedes_aclarar_ruta"):
        # Las opciones numeradas llevan la salida escrita "o lo dejamos aqui"
        # porque C la pidio asi, y `RE_OFERTA` no distingue esa salida de la
        # oferta de seguir. Medido el 24 oct en C02 turno 5: el turno de
        # aclaracion se rechazaba siempre y salia el texto de respaldo.
        return True, None
    if es_oferta_de_continuar(mensaje) and not estado.get("puedes_ofrecer_continuar", False):
        return False, "ofrece seguir con la oferta ya usada en este escalon (AB2)"
    return True, None


# Modos sin ninguna cifra ni fecha. Las dos pausas y el cierre estaban fuera:
# la lista blanca numerica ya atrapaba los montos (con datos vacios no autoriza
# ninguno), pero el nombre de un mes pasaba. PAUSA_COMPRA no esta porque si
# ella pidio el calculo de la cuota, ahi si viajan dos cifras.
MODOS_SIN_CIFRAS = ("APOYO", "PAUSA_RECHAZO", "CIERRE")


def chequeo_coherencia_etiqueta_modo(mensaje, estado):
    modo = estado.get("modo")
    if modo not in MODOS_SIN_CIFRAS:
        return True, None
    if extraer_numeros(mensaje):
        return False, f"cifra en modo {modo}"
    plano = normalizar(mensaje)
    if any(m in plano for m in MESES):
        return False, f"fecha en modo {modo}"
    return True, None


# Nombres de bloque y de campo del contexto. Si aparecen en la respuesta, el
# modelo esta repitiendo el andamiaje en vez de hablarle a Patricia.
ANDAMIAJE = (
    "datos que puedes decir",
    "lo que tienes que hacer",
    "los dos ultimos turnos",
    "mensaje de patricia",
    "datos_disponibles",
    "movimientos permitidos",
    "monto_elegido",
    "minimo_con_cuota",
    "minimo_intereses",
    "minimo_cuotas",
    "minimo_abono",
    "meses_ahorrados",
    "intereses_ahorrados",
    "intereses_del_mes",
    "faltante_para_gastos_esenciales",
    "cuota_mensual",
)

# Los tres estados de un campo ("confirmado", "no confiable", "ausente") NO
# estan en la lista. Son espanol corriente: medido el 24 oct, el modelo
# escribio "No tengo confirmado el monto de la cuota mensual", una frase
# correcta que el chequeo rechazaba. La fuga que si existia --"Tu pago minimo
# confirmado es $74.350"-- se corrigio en el origen: el bloque de datos ya no
# anota el estado cuando es el normal (ver `prompts._formatear_valor`).


def chequeo_fuga_de_andamiaje(mensaje):
    """Que el modelo no le lea el contexto a Patricia.

    Ella no tiene por que saber que existe un bloque llamado "DATOS QUE
    PUEDES DECIR" ni un campo llamado `minimo_con_cuota`. Un nombre tecnico en
    la respuesta es una fuga del andamiaje, y con el contexto resuelto hay mas
    encabezados en texto plano que antes.
    """
    plano = normalizar(mensaje)
    encontrados = [n for n in ANDAMIAJE if n in plano]
    if encontrados:
        return False, "andamiaje en la respuesta: " + ", ".join(encontrados)
    return True, None


def chequeo_sin_4141_en_cuidadora_agotada(mensaje, etiqueta):
    # C (23 oct): el *4141 es solo para angustia_fuerte. En el caso
    # ambiguo de cuidadora_agotada no se ofrece automatico, para que no se
    # sienta como sobrerreaccion.
    if etiqueta != "cuidadora_agotada":
        return True, None
    if "4141" in mensaje:
        return False, "4141 no corresponde en cuidadora_agotada"
    return True, None


# --- orquestacion ------------------------------------------------------

NOMBRES = (
    "lista_blanca_numerica",
    "frases_prohibidas",
    "forma",
    "movimientos",
    "coherencia_etiqueta_modo",
    "sin_4141_en_cuidadora_agotada",
    "fuga_de_andamiaje",
)


# El detalle de la lista blanca nombra las cifras intrusas ("cifras no
# inyectadas: 84350"), asi que devolverselo al modelo le repite el numero que
# no debia usar. Para ese chequeo la correccion va sin el detalle.
_SIN_DETALLE = ("lista_blanca_numerica",)


def _correccion(nombre, detalle):
    """El texto del reintento, nombrando el chequeo que fallo."""
    if nombre in _SIN_DETALLE:
        motivo = (
            "usaste una cifra que no esta en DATOS QUE PUEDES DECIR. Reescribela "
            "con las cifras de ese bloque y ninguna otra"
        )
    else:
        motivo = f"falla en {nombre} ({detalle})"
    return (
        f"Tu respuesta anterior {motivo}. Reescribela respetando LO QUE TIENES "
        "QUE HACER."
    )


@dataclass
class Resultado:
    enviado: str
    validadores: dict = field(default_factory=dict)
    override: dict = field(default_factory=lambda: {"aplicado": False, "motivo": None})
    correccion: str = None  # texto para el reintento; None si no hay reintento

    @property
    def ok(self):
        return all(v == "ok" for v in self.validadores.values())


def _respaldo(modo, estado=None):
    """El respaldo no puede violar los chequeos: la oferta de seguir se pega
    solo si AB2 todavia la permite en este turno."""
    texto = TEXTOS_RESPALDO.get(modo, TEXTOS_RESPALDO["APOYO"])
    if modo in ("E1", "E2", "E3") and (estado or {}).get(
            "puedes_ofrecer_continuar"):
        texto += CIERRE_OFERTA
    return texto


def respaldo_sin_modelo(estado, motivo):
    """El texto de respaldo del modo cuando el modelo no dio ninguna salida.

    Pasa cuando el proveedor falla o cuando el presupuesto del turno se
    agoto. Es la razon de existir de la capa 4: siempre hay algo que
    decirle, y es un texto que C validó, no un error.
    """
    modo = estado.get("modo", "E0")
    return Resultado(
        enviado=_respaldo(modo, estado),
        validadores={n: "n/a" for n in NOMBRES},
        override={"aplicado": True, "motivo": motivo},
    )


def validar(salida, estado, datos, motor=None, intento=1):
    """salida: la etiqueta del turno y el mensaje redactado. datos: lo inyectado.

    `datos` llega exacto —son los campos del modo final—, asi que la lista
    blanca numerica se arma con todos ellos y no hace falta volver a filtrar.
    """
    etiqueta = salida.get("etiqueta")
    mensaje = salida.get("mensaje") or ""
    modo = estado.get("modo", "E0")
    autorizados = dict(datos or {})

    # Capa 3: la prosa no se mira, se descarta.
    if etiqueta == "angustia_fuerte" and modo == "APOYO":
        # Ya estaba en APOYO: esto es su respuesta a "¿estas a salvo?", no
        # una primera deteccion. Repetir el texto de entrada completo
        # rompe AB7 (le repite el mismo limite dos veces) y suena a bucle.
        return Resultado(
            enviado=TEXTOS_FIJOS["angustia_fuerte_en_apoyo"],
            validadores={n: "n/a" for n in NOMBRES},
            override={"aplicado": True, "motivo": "texto_fijo_angustia_fuerte_en_apoyo"},
        )

    if etiqueta in ETIQUETAS_TEXTO_FIJO:
        return Resultado(
            enviado=TEXTOS_FIJOS[etiqueta],
            validadores={n: "n/a" for n in NOMBRES},
            override={"aplicado": True, "motivo": "texto_fijo_" + etiqueta},
        )

    if etiqueta == "no_texto":
        return Resultado(
            enviado=TEXTOS_FIJOS["no_texto"],
            validadores={n: "n/a" for n in NOMBRES},
            override={"aplicado": True, "motivo": "texto_fijo_no_texto"},
        )

    chequeos = (
        lambda: chequeo_lista_blanca_numerica(mensaje, autorizados, motor),
        lambda: chequeo_frases_prohibidas(mensaje),
        lambda: chequeo_forma(mensaje),
        lambda: chequeo_movimientos(mensaje, estado),
        lambda: chequeo_coherencia_etiqueta_modo(mensaje, estado),
        lambda: chequeo_sin_4141_en_cuidadora_agotada(mensaje, etiqueta),
        lambda: chequeo_fuga_de_andamiaje(mensaje),
    )

    validadores = {}
    for nombre, chequeo in zip(NOMBRES, chequeos, strict=False):
        ok, detalle = chequeo()
        if ok:
            validadores[nombre] = "ok"
            continue
        # el primero que falla corta: los siguientes no se evaluan
        validadores[nombre] = "falla"
        for pendiente in NOMBRES[len(validadores):]:
            validadores[pendiente] = "no_evaluado"

        # Si lo unico que sobra son frases, las primeras tres suelen ser una
        # respuesta valida: recortarlas conserva lo que ella pidio, y el texto
        # de respaldo no dice nada. Se re-valida el recorte, porque recortar no
        # puede introducir otra falla.
        if nombre == "forma" and "frases >" in (detalle or ""):
            recorte = recortar_a_tres_frases(mensaje)
            ok_recorte, _ = chequeo_forma(recorte)
            if ok_recorte and chequeo_frases_prohibidas(recorte)[0]:
                validadores[nombre] = "recortado"
                return Resultado(
                    enviado=recorte,
                    validadores=validadores,
                    override={"aplicado": False, "motivo": "recortado_a_tres_frases"},
                )

        if intento == 1:
            return Resultado(
                enviado=None,
                validadores=validadores,
                override={"aplicado": False, "motivo": None},
                correccion=_correccion(nombre, detalle),
            )
        return Resultado(
            enviado=_respaldo(modo, estado),
            validadores=validadores,
            override={"aplicado": True, "motivo": f"respaldo_{modo}_{nombre}"},
        )

    return Resultado(enviado=mensaje, validadores=validadores)
