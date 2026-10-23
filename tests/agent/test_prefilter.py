"""python3 test_prefiltro.py"""

from app.modules.agent.prefilter import NO_DISPARAN, revisar


def test_ideacion_explicita_dispara():
    for frase in ("ya no quiero vivir", "me quiero morir",
                  "estoy pensando en quitarme la vida",
                  "Me voy a hacer dano", "ya no quiero estar aca"):
        disparo, patron = revisar(frase)
        assert disparo, frase
        assert patron


def test_cansancio_no_dispara():
    # el riesgo abierto: un falso positivo aca arruina la demo
    for frase in NO_DISPARAN:
        disparo, _ = revisar(frase)
        assert not disparo, frase


def test_cansancio_con_contexto_de_cuidado_no_dispara():
    for frase in ("mi mama tuvo un mal dia y ya no doy mas",
                  "llevo siete meses asi, no puedo mas con esto",
                  "estoy muy cansada, me da lata verlo hoy"):
        disparo, _ = revisar(frase)
        assert not disparo, frase


def test_los_acentos_no_lo_esquivan():
    assert revisar("ya no quiero vivír")[0] is True
    assert revisar("ME QUIERO MORIR")[0] is True


def test_mensaje_normal_no_dispara():
    for frase in ("hola", "cuanto es el pago minimo?",
                  "ya, mandame que debo", "gracias, chao"):
        assert not revisar(frase)[0], frase


def test_puntuacion_rara_no_lo_esquiva():
    for frase in ("me... quiero... morir", "ME quiero MORIR ya",
                  "Ya NO Quiero Vivír mas asi"):
        assert revisar(frase)[0], frase


def test_los_4_patrones_pedidos_por_c_disparan():
    for frase in ("quiero morirme", "no quiero vivir", "matarme",
                  "acabar con mi vida"):
        disparo, patron = revisar(frase)
        assert disparo, frase
        assert patron
