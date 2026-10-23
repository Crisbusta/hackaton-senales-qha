"""Tests del armado de contexto. La propiedad que importa: lo que no entra
al contexto no se puede filtrar.
"""

import json
import pathlib

from app.modules.agent.prompts import MODOS, armar_contexto, campos_visibles

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

DATOS = json.load(open(FIXTURES / "statement_complete.json"))


def ctx(modo, **kw):
    estado = {"modo": modo, "puedes_ofrecer_continuar": kw.pop("oferta", True)}
    estado.update({k: v for k, v in kw.items() if k in
                   ("puedes_aclarar_ruta", "limites_ya_dichos")})
    extra = {k: v for k, v in kw.items() if k in
             ("pregunta_previa", "pidio_calculo_cuota")}
    return armar_contexto(estado, DATOS, **extra)


def test_e1_no_filtra_el_saldo_ni_el_minimo():
    salida = ctx("E1")
    assert "3 de noviembre" in salida
    assert "1.064.000" not in salida
    assert "74.350" not in salida


def test_apoyo_no_recibe_ningun_dato():
    salida = ctx("APOYO")
    assert "ninguno" in salida
    for cifra in ("74.350", "1.064.000", "noviembre", "868.000"):
        assert cifra not in salida, cifra


def test_pausa_y_cierre_sin_datos():
    for modo in ("PAUSA_RECHAZO", "CIERRE", "E0"):
        assert "ninguno" in ctx(modo), modo


def test_facturado_solo_si_pregunta():
    assert "868.000" not in ctx("E2")
    assert "868.000" in ctx("E2", pregunta_previa="pregunta_monto_facturado")


def test_facturado_no_entra_antes_de_e2():
    assert "868.000" not in ctx("E1", pregunta_previa="pregunta_monto_facturado")


def test_tasa_solo_desde_e3():
    assert "2,8" not in ctx("E2", pregunta_previa="pregunta_intereses")
    assert "2,8" in ctx("E3", pregunta_previa="pregunta_intereses")


def test_desglose_solo_en_e3():
    assert "23.100" not in ctx("E2")
    assert "23.100" in ctx("E3")


def test_pausa_activa_sin_cifras_hasta_que_ella_pida_el_calculo():
    assert "74.350" not in ctx("PAUSA_COMPRA")
    datos = dict(DATOS, minimo_con_cuota={"valor": 84350, "estado": "confirmado"})
    salida = armar_contexto({"modo": "PAUSA_COMPRA"}, datos,
                            pidio_calculo_cuota=True)
    assert "74.350" in salida and "84.350" in salida


def test_la_oferta_desaparece_cuando_ab2_la_consumio():
    assert "seguimos" in ctx("E2").lower()
    assert "seguimos" not in ctx("E2", oferta=False).lower()
    assert "puedes_ofrecer_continuar: no" in ctx("E2", oferta=False)


def test_la_linea_de_crisis_se_ofrece_una_vez():
    primera = armar_contexto({"modo": "APOYO", "limites_ya_dichos": []}, DATOS)
    segunda = armar_contexto(
        {"modo": "APOYO", "limites_ya_dichos": ["linea_crisis"]}, DATOS)
    assert "*4141" in primera
    assert "*4141" not in segunda


def test_los_tres_bloques_siempre_estan():
    for modo in MODOS:
        salida = ctx(modo)
        for bloque in ("ESTADO", "DATOS_DISPONIBLES",
                       "MOVIMIENTOS PERMITIDOS EN ESTE TURNO"):
            assert bloque in salida, (modo, bloque)


def test_dato_ausente_se_marca_como_ausente():
    datos = json.load(open(FIXTURES / "statement_incomplete.json"))
    salida = armar_contexto({"modo": "E1"}, datos)
    assert "vencimiento: (ausente)" in salida


def test_dato_en_conflicto_viaja_con_los_dos_valores():
    datos = json.load(open(FIXTURES / "statement_conflict.json"))
    salida = armar_contexto({"modo": "E2"}, datos)
    assert "74.350" in salida and "47.350" in salida
    assert "no confiable" in salida


def test_modo_desconocido_revienta():
    try:
        armar_contexto({"modo": "E9"}, DATOS)
    except ValueError:
        return
    raise AssertionError("un modo invalido tiene que fallar, no improvisar")


def test_campos_visibles_es_la_unica_fuente():
    # ningun modo ve mas campos que los de su escalon
    assert set(campos_visibles("E1")) == {"vencimiento"}
    assert "saldo" not in campos_visibles("E0")
    assert campos_visibles("APOYO") == []


# --- casos borde agregados en revision posterior -----------------------

def test_e4_sin_monto_elegido_ni_motor_muestra_ninguno():
    salida = armar_contexto({"modo": "E4", "puedes_ofrecer_continuar": False}, {})
    assert "ninguno" in salida
    assert "ninguno" in salida.split("MOVIMIENTOS")[0]  # el bloque DATOS_DISPONIBLES


def test_pausa_tiene_el_texto_literal_de_un_solo_movimiento():
    salida = armar_contexto({"modo": "PAUSA_RECHAZO"}, DATOS)
    assert "Ninguno: la respuesta es el texto fijo de rechazo_pausa." in salida


def test_puedes_ofrecer_continuar_solo_aparece_en_e1_e2_e3():
    for modo in ("E1", "E2", "E3"):
        salida = armar_contexto({"modo": modo, "puedes_ofrecer_continuar": True}, DATOS)
        assert "puedes_ofrecer_continuar:" in salida, modo
    for modo in ("E0", "E4", "PAUSA_RECHAZO", "PAUSA_COMPRA", "APOYO", "CIERRE"):
        salida = armar_contexto({"modo": modo}, DATOS)
        assert "puedes_ofrecer_continuar:" not in salida, modo


# --- cambios del 23 oct, segunda vuelta con C ---------------------------

def test_escalon_maximo_aparece_en_el_estado():
    salida = armar_contexto({"modo": "E2", "escalon_maximo": "E2"}, DATOS)
    assert "escalon_maximo: E2" in salida


def test_retoma_sin_avance_previo_mantiene_el_e0_normal():
    salida = armar_contexto({"modo": "E0", "escalon_maximo": "E0"}, DATOS)
    assert "Conversar y acusar recibo" in salida
    assert "la vez pasada" not in salida


def test_retoma_con_avance_previo_reconoce_sin_repetir_cifras():
    salida = armar_contexto({"modo": "E0", "escalon_maximo": "E2"}, DATOS)
    assert "tu pago minimo y tu saldo" in salida
    assert "74.350" not in salida and "1.064.000" not in salida
    assert "logro" in salida  # la instruccion de no usar la palabra "logro"
