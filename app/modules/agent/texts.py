"""Textos que no los escribe el modelo.

Pendiente de validacion de C (dueno de la voz, T01). Si cambia una palabra,
manda el. Aca estan como borrador para que el codigo no espere.
"""

# Capa 3: salen tal cual y descartan la prosa del modelo.
# Definitivos, validados por C en T01 (23 oct). No son borrador: cualquier
# cambio de palabra pasa por C, no se edita aca sin avisarle.
TEXTOS_FIJOS = {
    "angustia_fuerte": (
        "Te leo. Dejemos las cuentas de lado por ahora, no hay apuro con eso. "
        "¿Estás a salvo en este momento? Si quieres hablar con alguien, el "
        "*4141 es gratis y atiende a toda hora; si sientes que estás en "
        "peligro ahora, llama al 131."
    ),
    "rechazo_pausa": (
        "Está bien, lo dejamos acá. No hay apuro con esto. Si quieres "
        "contarme cómo estás, te leo; y cuando quieras verlo, me escribes y "
        "partimos por donde tú quieras."
    ),
    "no_texto": (
        "Por ahora solo puedo leer texto: todavía no escucho audios ni veo "
        "imágenes. ¿Me lo escribes?"
    ),
    # Borrador propio, pendiente de validar con C: cuando ya esta en APOYO y
    # reafirma que no esta a salvo (responde a la pregunta del texto de
    # angustia_fuerte). No repite el texto de entrada completo (rompe AB7),
    # solo refuerza las lineas y se queda.
    "angustia_fuerte_en_apoyo": (
        "Gracias por decirme. El *4141 es gratis y atiende a toda hora, y "
        "si es una emergencia ahora, el 131. Me quedo aquí contigo."
    ),
}

# Salen solo si el validador rechaza dos veces la salida del modelo.
TEXTOS_RESPALDO = {
    "E0": "Te leo. Acá estoy, sin apuro. Cuéntame lo que quieras.",
    # Sin la oferta de seguir: el respaldo puede caer en un turno donde AB2
    # ya la consumio. Si corresponde ofrecerla, la agrega _respaldo().
    "E1": "Déjame ordenarlo bien antes de contártelo.",
    "E2": "Déjame ordenarlo bien antes de contártelo.",
    "E3": "Déjame ordenarlo bien antes de contártelo.",
    "E4": "Eso lo tengo que calcular con más cuidado. ¿Qué monto quieres probar?",
    # Pendiente de validar con C (la voz es T01). Antes este respaldo
    # ofrecia las dos opciones, y el respaldo salta sin saber en que punto
    # de la conversacion estamos: medido el 23 oct, en C05 turno 4 ella
    # dijo "igual lo voy a comprar" y le volvio a ofrecer esperar diez
    # minutos. Un texto neutro nunca insiste; la oferta la hace el modelo,
    # que si sabe si ella ya eligio.
    "PAUSA_COMPRA": (
        "Te leo, Patricia. La decisión es tuya y no voy a insistir. "
        "Aquí estoy si quieres mirar algo."
    ),
    "APOYO": "Te leo. Acá estoy.",
    "CIERRE": "Gracias por este rato, Patricia. Aquí estoy cuando quieras volver.",
    "PAUSA_RECHAZO": TEXTOS_FIJOS["rechazo_pausa"],
}

# Se le pega al respaldo de E1-E3 solo si AB2 todavia lo permite.
CIERRE_OFERTA = " ¿Seguimos o lo dejamos aquí?"

# Las lineas de ayuda. No se promete que ella califique para nada.
LINEA_CRISIS = "*4141"
LINEA_SAMU = "131"

# Chequeo 2 del validador. Sin acentos y en minusculas: se comparan
# contra el mensaje normalizado.
# "revisa" y "calma" se comparan como palabra completa (ver
# validadores.chequeo_frases_prohibidas): C pidio que "revisaremos" y
# "calmar" no caigan sin querer.
FRASES_PALABRA_COMPLETA = ("revisa", "calma")

FRASES_PROHIBIDAS = [
    # de la ficha de C
    "tranquila",
    # minimiza lo que ella siente; del prompt del Playground (23 oct)
    "no es para tanto",
    "no te preocupes",
    "deberias",
    "deberia",
    "tienes que",
    "tendrias que",
    # nunca prometer escribir primero
    "te aviso",
    "te recuerdo",
    "te escribo",
    "te voy a escribir",
    # nunca pedirle mirar el documento
    "revisa",
    "fijate",
    "mira donde",
    "abre el",
    # consuelo: nombrar el sentimiento, dar animo, declarar que es normal
    "veo que estas",
    "se lo que sientes",
    "entiendo como te sientes",
    "es normal que",
    "calma",
    "animo",
    "no estas sola",
    "todo va a estar bien",
]

ETIQUETAS = [
    "angustia_fuerte",
    "rechazo_pausa",
    "retoma_voluntaria",
    "intencion_compra_cuotas",
    "solicitud_informacion",
    "disposicion_explorar",
    "pregunta_monto_facturado",
    "verguenza_culpa",
    "cuidadora_agotada",
    "tangente",
    "pide_consejo",
    "pregunta_dicom",
    "pregunta_identidad",
    "saludo",
    "no_texto",
    "ambiguo",
    "cierre",
]

# Capa 3: en estas la prosa del modelo se descarta sin mirarla.
ETIQUETAS_TEXTO_FIJO = ("angustia_fuerte", "rechazo_pausa")
