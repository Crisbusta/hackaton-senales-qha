"""La tabla de transiciones y las reglas anti-bucle (T06).

Las reglas vienen de T01 (C) y de docs/maquina-de-estados.md. Lo que se
prueba acá es que el codigo no se repite y que una pausa no es una trampa.
"""

from app.modules.agent.interface import ConversationEvent, ConversationMode, Intent
from app.modules.conversation import transitions as t


def paso(modo, intent, max_modo=ConversationMode.GREETING, event=None):
    return t.siguiente_modo(modo, max_modo, intent, event)


# --- la Escalera sube de a un dato por vez ------------------------------

def test_the_ladder_climbs_one_step_at_a_time():
    modo = ConversationMode.GREETING
    for esperado in (ConversationMode.DUE_DATE, ConversationMode.BALANCE_OVERVIEW,
                     ConversationMode.MINIMUM_BREAKDOWN):
        modo = paso(modo, Intent.WILLING_TO_EXPLORE)
        assert modo is esperado


def test_the_ladder_does_not_overshoot_its_last_step():
    assert paso(ConversationMode.MINIMUM_BREAKDOWN,
                Intent.WILLING_TO_EXPLORE) is ConversationMode.MINIMUM_BREAKDOWN


def test_asking_for_a_figure_also_climbs():
    assert paso(ConversationMode.GREETING, Intent.INFO_REQUEST) is ConversationMode.DUE_DATE


# --- la pausa no es una trampa -----------------------------------------

def test_declining_pauses_the_financial_review():
    assert paso(ConversationMode.BALANCE_OVERVIEW, Intent.DECLINED) is ConversationMode.PAUSE


def test_asking_again_leaves_the_pause():
    """El fallo real: el modo se quedaba en PAUSA_RECHAZO para siempre y
    ella recibia el texto de pausa aunque pidiera su saldo. El texto de C
    le prometio "cuando quieras verlo, me escribes"."""
    nuevo = paso(ConversationMode.PAUSE, Intent.INFO_REQUEST,
                 max_modo=ConversationMode.DUE_DATE)
    assert nuevo is ConversationMode.BALANCE_OVERVIEW


def test_a_tangent_inside_the_pause_stays_in_the_pause():
    for intent in (Intent.OFF_TOPIC, Intent.SHAME_GUILT, Intent.EXHAUSTED_CAREGIVER):
        assert paso(ConversationMode.PAUSE, intent) is ConversationMode.PAUSE, intent


# --- AB5 y AB6 ----------------------------------------------------------

def test_support_is_only_left_when_she_resumes():
    for intent in (Intent.OFF_TOPIC, Intent.SHAME_GUILT, Intent.AMBIGUOUS):
        assert paso(ConversationMode.SUPPORT, intent) is ConversationMode.SUPPORT, intent
    assert paso(ConversationMode.SUPPORT,
                Intent.VOLUNTARY_RESUME) is ConversationMode.GREETING


def test_crisis_wins_over_everything():
    for modo in ConversationMode:
        assert paso(modo, Intent.INFO_REQUEST,
                    event=ConversationEvent.CRISIS_SUPPORT_NEEDED) is ConversationMode.SUPPORT


def test_the_maximum_step_only_goes_up():
    maximo = ConversationMode.GREETING
    for modo in (ConversationMode.DUE_DATE, ConversationMode.BALANCE_OVERVIEW,
                 ConversationMode.MINIMUM_BREAKDOWN):
        maximo = t.subir_maximo(maximo, modo)
    assert maximo is ConversationMode.MINIMUM_BREAKDOWN

    # bajar a un desvio o a E0 no lo mueve
    for modo in (ConversationMode.GREETING, ConversationMode.PAUSE,
                 ConversationMode.SUPPORT, ConversationMode.DUE_DATE):
        assert t.subir_maximo(maximo, modo) is ConversationMode.MINIMUM_BREAKDOWN, modo


def test_resuming_keeps_what_she_already_saw():
    """AB6: al retomar vuelve a E0, pero el maximo se conserva para que
    `retoma_voluntaria` reconozca lo visto sin repetir cifras."""
    maximo = t.subir_maximo(ConversationMode.GREETING, ConversationMode.BALANCE_OVERVIEW)
    nuevo = paso(ConversationMode.SUPPORT, Intent.VOLUNTARY_RESUME, max_modo=maximo)

    assert nuevo is ConversationMode.GREETING
    assert t.subir_maximo(maximo, nuevo) is ConversationMode.BALANCE_OVERVIEW


# --- desvios y cierre ---------------------------------------------------

def test_wanting_to_buy_in_instalments_pauses_the_ladder():
    assert paso(ConversationMode.BALANCE_OVERVIEW,
                Intent.INSTALLMENT_INTENT) is ConversationMode.INSTALLMENT_PAUSE


def test_saying_goodbye_closes():
    assert paso(ConversationMode.MINIMUM_BREAKDOWN,
                Intent.CLOSING) is ConversationMode.CLOSING


# --- AB8: acuses vacios -------------------------------------------------

def test_an_empty_acknowledgement_is_recognised():
    for texto in ("ok", "OK.", " gracias ", "ya", "dale", "listo", "👍", "mmm"):
        assert t.es_acuse_vacio(texto), texto


def test_a_message_with_content_is_not_an_acknowledgement():
    for texto in ("ok, cuanto debo?", "gracias, y el saldo?", "ya no quiero vivir",
                  "ok pero no entiendo"):
        assert not t.es_acuse_vacio(texto), texto
