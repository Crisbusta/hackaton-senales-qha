"""Capa 1: pre-filtro determinista, antes de llamar al modelo.

Solo frases INEQUIVOCAS. El riesgo abierto del diseno es el falso positivo:
en chileno "ya no doy mas" suele ser cansancio, no crisis, y disparar el
texto de crisis ahi se siente como sobrerreaccion. Criterio: ante duda, no
es angustia_fuerte y la decision se la deja al modelo (que ademas tiene el
contexto del turno anterior).

Lo que entra aca es solo lo que ninguna persona leeria como cansancio.
"""

import re

from app.modules.agent.validators import normalizar

# Ideacion explicita. Nada de esto es ambiguo en ningun registro.
# Negative lookahead: "me quiero morir de la verguenza/risa/susto" es una
# frase hecha, no ideacion. C (23 oct) pidio excluirla y dejar que decida
# el modelo, que si tiene el contexto completo del turno.
_EXCEPCION_IDIOMATICA = r"(?! de (la )?(verguenza|risa|susto)\b)"

PATRONES_CRISIS = [
    (r"\bme quiero morir\b" + _EXCEPCION_IDIOMATICA, "ideacion_morir"),
    (r"\bquiero morirme\b" + _EXCEPCION_IDIOMATICA, "ideacion_morir"),
    (r"\bno quiero (seguir )?vivi(r|endo)\b", "ideacion_vivir"),
    (r"\bno quiero estar (mas )?aca\b", "ideacion_vivir"),
    (r"\bquitarme la vida\b", "ideacion_vida"),
    (r"\bacabar con (todo|mi vida)\b", "ideacion_acabar"),
    (r"\bme voy a (hacer dano|matar)\b", "ideacion_dano"),
    (r"\bhacerme dano\b", "ideacion_dano"),
    (r"\bmatarme\b", "ideacion_matarme"),  # pedido por C, validacion de T01
    (r"\bya no quiero (vivir|estar)\b", "ideacion_vivir"),
]

# Lo que NO dispara el prefiltro, aunque se parezca. Queda documentado para
# que nadie lo agregue "por si acaso": estas las etiqueta el modelo.
NO_DISPARAN = (
    "ya no doy mas",
    "no puedo mas",
    "estoy agotada",
    "no aguanto mas",
    "estoy cansada de todo",
)

_COMPILADOS = [(re.compile(p), nombre) for p, nombre in PATRONES_CRISIS]


def revisar(mensaje):
    """-> (disparo: bool, patron: str|None). Sin llamada al modelo.

    Alguien en crisis no escribe con puntuacion prolija: "me... quiero...
    morir" tiene que disparar igual que "me quiero morir". Se colapsa toda
    puntuacion/espacio repetido a un solo espacio antes de buscar, sin
    tocar las letras.
    """
    plano = normalizar(mensaje)
    plano = re.sub(r"[^a-z]+", " ", plano).strip()
    for patron, nombre in _COMPILADOS:
        if patron.search(plano):
            return True, nombre
    return False, None


# --- la respuesta a "¿estas a salvo en este momento?" --------------------
#
# El texto de crisis termina con esa pregunta, asi que el turno siguiente es
# una respuesta a ella. "No, no estoy bien" aislado es cansancio y el prompt
# del clasificador lo manda a cuidadora_agotada con razon; dentro de APOYO es
# un "no" a si esta a salvo. La diferencia es de estado, no del texto, asi que
# vive en el codigo y no en el prompt (C10 d, medido el 24 oct: el turno salia
# sin el *4141 ni el 131).
PATRONES_NO_ESTOY_BIEN = [
    (r"^no$", "nego_estar_a_salvo"),
    (r"^no\b.*\bno estoy bien\b", "nego_estar_bien"),
    (r"\bno estoy (nada )?bien\b", "nego_estar_bien"),
    (r"\bno estoy a salvo\b", "nego_estar_a_salvo"),
    (r"\bno me siento (nada )?(bien|segura)\b", "nego_estar_bien"),
    (r"\bestoy muy mal\b", "nego_estar_bien"),
    (r"^nada bien$", "nego_estar_bien"),
]

_COMPILADOS_APOYO = [(re.compile(p), nombre) for p, nombre in PATRONES_NO_ESTOY_BIEN]


def revisar_en_apoyo(mensaje):
    """-> (disparo, patron). Solo se consulta estando ya en APOYO.

    Un falso positivo aca cuesta un texto de refuerzo que ofrece las lineas y
    se queda con ella; un falso negativo cuesta que no las reciba. Por eso el
    umbral es mas bajo que en `revisar`.
    """
    plano = normalizar(mensaje)
    plano = re.sub(r"[^a-z]+", " ", plano).strip()
    for patron, nombre in _COMPILADOS_APOYO:
        if patron.search(plano):
            return True, nombre
    return False, None
