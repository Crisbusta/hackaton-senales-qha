"""Los cinco chequeos deterministas sobre cada salida del modelo (T05).

No depende del SDK, de la red ni del contrato de T02: se le pasa el dict ya
parseado. Por eso se puede probar completo antes de que exista el repo.

Orden de los chequeos, el primero que falla corta:
  1. lista_blanca_numerica
  2. frases_prohibidas
  3. forma (frases, preguntas, largo, markdown)
  4. movimientos (oferta de seguir cuando no corresponde)
  5. coherencia_etiqueta_modo

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
    """Toda cifra visible en DATOS_DISPONIBLES mas la salida del motor T10."""
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
    enmascarado = enmascarar_numeros(mensaje)
    partes = [p for p in re.split(r"[.?!…]+", enmascarado) if p.strip()]
    return len(partes)


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


def chequeo_movimientos(mensaje, estado):
    if estado.get("modo") not in MODOS_CON_OFERTA:
        return True, None
    ofrece = bool(RE_OFERTA.search(normalizar(mensaje)))
    if ofrece and not estado.get("puedes_ofrecer_continuar", False):
        return False, "ofrece seguir con la oferta ya usada en este escalon (AB2)"
    return True, None


def chequeo_coherencia_etiqueta_modo(mensaje, estado):
    if estado.get("modo") != "APOYO":
        return True, None
    if extraer_numeros(mensaje):
        return False, "cifra en modo APOYO"
    plano = normalizar(mensaje)
    if any(m in plano for m in MESES):
        return False, "fecha en modo APOYO"
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
    """salida: el dict de cuatro campos del modelo. estado/datos: lo inyectado."""
    etiqueta = salida.get("etiqueta")
    mensaje = salida.get("mensaje") or ""
    modo = estado.get("modo", "E0")

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
        lambda: chequeo_lista_blanca_numerica(mensaje, datos, motor),
        lambda: chequeo_frases_prohibidas(mensaje),
        lambda: chequeo_forma(mensaje),
        lambda: chequeo_movimientos(mensaje, estado),
        lambda: chequeo_coherencia_etiqueta_modo(mensaje, estado),
        lambda: chequeo_sin_4141_en_cuidadora_agotada(mensaje, etiqueta),
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
        if intento == 1:
            return Resultado(
                enviado=None,
                validadores=validadores,
                override={"aplicado": False, "motivo": None},
                correccion=f"Tu respuesta anterior falla en {nombre} ({detalle}). Reescribela "
                "respetando los MOVIMIENTOS PERMITIDOS.",
            )
        return Resultado(
            enviado=_respaldo(modo, estado),
            validadores=validadores,
            override={"aplicado": True, "motivo": f"respaldo_{modo}_{nombre}"},
        )

    return Resultado(enviado=mensaje, validadores=validadores)
