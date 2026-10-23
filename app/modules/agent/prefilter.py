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
