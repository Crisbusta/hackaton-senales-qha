"""Tests de los cinco chequeos. Corren sin pytest: python3 test_validadores.py

No llaman al modelo ni a la red: se le pasa a validar() el dict que el
modelo habria devuelto.
"""

import json
import pathlib

from app.modules.agent.validators import (
    chequeo_forma,
    chequeo_frases_prohibidas,
    contar_frases,
    extraer_numeros,
    validar,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

DATOS_E2 = json.load(open(FIXTURES / "statement_complete.json"))
E2 = {"modo": "E2", "puedes_ofrecer_continuar": True, "limites_ya_dichos": []}
E2_SIN_OFERTA = dict(E2, puedes_ofrecer_continuar=False)
APOYO = {"modo": "APOYO", "puedes_ofrecer_continuar": False, "limites_ya_dichos": []}


def salida(mensaje, etiqueta="solicitud_informacion", **extra):
    base = {"etiqueta": etiqueta, "esencial": None, "monto_mencionado": None,
            "mensaje": mensaje}
    base.update(extra)
    return base


# --- lo que debe pasar -------------------------------------------------

def test_mensaje_valido_pasa():
    r = validar(salida(
        "Tu pago minimo es de $74.350 y el saldo es de $1.064.000. "
        "Seguimos o lo dejamos aqui?"
    ), E2, DATOS_E2)
    assert r.ok, r.validadores
    assert r.enviado.startswith("Tu pago minimo")
    assert r.override["aplicado"] is False


def test_puntos_de_miles_no_cuentan_como_frases():
    # $1.064.000 tiene dos puntos: sin enmascarar serian tres frases de mas
    assert contar_frases("El saldo es de $1.064.000.") == 1
    ok, _ = chequeo_forma("El saldo es de $1.064.000 y el minimo $74.350.")
    assert ok


def test_la_linea_de_crisis_no_es_una_cifra():
    assert extraer_numeros("el *4141 es gratis y atiende a toda hora") == []
    ok, detalle = chequeo_forma("Si quieres hablar con alguien, el *4141 es gratis.")
    assert ok, detalle


def test_opciones_numeradas_no_son_cifras():
    mensaje = "Tu eliges.\n1. Esperar diez minutos.\n2. Ver cuanto sumaria."
    assert extraer_numeros(mensaje) == []


def test_conflicto_admite_los_dos_valores():
    datos = json.load(open(FIXTURES / "statement_conflict.json"))
    r = validar(salida(
        "En el documento aparece mas de un pago minimo, $74.350 y $47.350, "
        "asi que no puedo confirmarte cual es."
    ), E2, datos)
    assert r.ok, r.validadores


# --- chequeo 1: lista blanca numerica ----------------------------------

def test_cifra_inventada_falla():
    r = validar(salida("Tu pago minimo es de $99.999."), E2, DATOS_E2)
    assert r.validadores["lista_blanca_numerica"] == "falla"
    assert r.enviado is None and r.correccion


def test_dato_no_inyectado_falla_aunque_exista_en_el_fixture():
    # en E1 el saldo no se inyecta: el modelo no puede nombrarlo
    datos_e1 = {"vencimiento": DATOS_E2["vencimiento"]}
    e1 = {"modo": "E1", "puedes_ofrecer_continuar": True}
    r = validar(salida("Vence el 3 de noviembre y debes $1.064.000."), e1, datos_e1)
    assert r.validadores["lista_blanca_numerica"] == "falla"


def test_cifra_en_palabras_falla():
    r = validar(salida("Tu pago minimo es de setenta y cuatro mil pesos."),
                E2, DATOS_E2)
    assert r.validadores["lista_blanca_numerica"] == "falla"


def test_diez_minutos_no_es_una_cifra_en_palabras():
    estado = {"modo": "PAUSA_COMPRA", "puedes_ofrecer_continuar": False}
    r = validar(salida(
        "Si quieres podemos esperar diez minutos y ves como te sientes.",
        etiqueta="intencion_compra_cuotas",
    ), estado, {})
    assert r.ok, r.validadores


def test_motor_de_calculo_habilita_sus_cifras():
    # Los plazos en meses son salida del simulador, no campos del estado de
    # cuenta: el contrato los pone en `calculations` ("montos en pesos y
    # plazos en meses, los dos int"). Antes `meses_solo_minimo` viajaba en
    # datos_pdf y pasaba porque la lista blanca tomaba datos completo.
    motor = {"meses": 43, "meses_solo_minimo": 49, "intereses_ahorrados": 123000}
    r = validar(salida(
        "Con ese pago extra de $50.000 serian 43 meses en vez de 49.",
    ), {"modo": "E4", "puedes_ofrecer_continuar": False},
        {"monto_elegido": {"valor": 50000, "estado": "confirmado"}}, motor=motor)
    assert r.ok, r.validadores


# --- chequeo 2: frases prohibidas --------------------------------------

def test_frase_prohibida_falla():
    for frase in ("Tranquila, no es tanto.", "Deberias pagar mas.",
                  "Te aviso manana.", "Veo que estas angustiada.",
                  "Es normal que te pase.", "Revisa la pagina 2."):
        r = validar(salida(frase), E2, DATOS_E2)
        assert "falla" in r.validadores.values(), frase


# --- chequeo 3: forma --------------------------------------------------

def test_cuatro_frases_falla():
    r = validar(salida("Hola. Te leo. Aca estoy. Y tambien aca."), E2, DATOS_E2)
    assert r.validadores["forma"] == "falla"


def test_dos_preguntas_falla():
    r = validar(salida("Seguimos o lo dejamos aqui? Te parece?"), E2, DATOS_E2)
    assert r.validadores["forma"] == "falla"


def test_markdown_falla():
    r = validar(salida("Tu pago minimo es *$74.350*."), E2, DATOS_E2)
    assert r.validadores["forma"] == "falla"


# --- chequeo 4: movimientos -------------------------------------------

def test_oferta_sin_permiso_falla():
    r = validar(salida("Tu saldo es de $1.064.000. Seguimos o lo dejamos aqui?"),
                E2_SIN_OFERTA, DATOS_E2)
    assert r.validadores["movimientos"] == "falla"


# --- chequeo 5: coherencia etiqueta-modo ------------------------------

def test_cifra_en_apoyo_falla():
    r = validar(salida("Te leo. Tu vencimiento sigue siendo el 3 de noviembre.",
                       etiqueta="cuidadora_agotada"), APOYO,
                {"vencimiento": DATOS_E2["vencimiento"]})
    # APOYO no autoriza ningun campo, asi que ahora la ataja la lista blanca
    # antes de llegar a coherencia_etiqueta_modo. Lo que importa es que no
    # sale: una cifra en APOYO se rechaza, y cuanto antes, mejor.
    assert not r.ok
    assert r.validadores["lista_blanca_numerica"] == "falla"


def test_apoyo_sin_cifras_pasa():
    r = validar(salida("Te leo. Aca estoy.", etiqueta="cuidadora_agotada"),
                APOYO, {})
    assert r.ok, r.validadores


# --- capa 3 y reintento ------------------------------------------------

def test_angustia_descarta_la_prosa():
    r = validar(salida("Tranquila, con $74.350 lo resuelves.",
                       etiqueta="angustia_fuerte"), E2, DATOS_E2)
    assert r.override["aplicado"]
    assert "*4141" in r.enviado
    assert "74.350" not in r.enviado
    assert r.correccion is None  # sin reintento


def test_rechazo_pausa_descarta_la_prosa():
    r = validar(salida("Segura? Te aviso manana.", etiqueta="rechazo_pausa"),
                E2, DATOS_E2)
    assert r.override["aplicado"]
    assert "segura" not in r.enviado.lower()


def test_segundo_intento_cae_al_respaldo():
    r = validar(salida("Tu pago minimo es de $99.999."), E2, DATOS_E2, intento=2)
    assert r.override["aplicado"]
    assert r.enviado and r.correccion is None
    assert r.override["motivo"].startswith("respaldo_E2")


def test_el_primero_que_falla_corta():
    r = validar(salida("Tranquila, son $99.999."), E2, DATOS_E2)
    assert r.validadores["lista_blanca_numerica"] == "falla"
    assert r.validadores["frases_prohibidas"] == "no_evaluado"


# --- meta: los textos propios tienen que pasar sus propios chequeos ------

def test_todo_texto_de_respaldo_pasa_sus_validadores():
    from app.modules.agent.validators import _respaldo
    modos = {"E0": E2, "E1": E2, "E2": E2, "E3": E2,
             "E4": {"modo": "E4", "puedes_ofrecer_continuar": False},
             "PAUSA_COMPRA": {"modo": "PAUSA_COMPRA",
                              "puedes_ofrecer_continuar": False},
             "APOYO": APOYO, "CIERRE": {"modo": "CIERRE"}}
    for modo, estado in modos.items():
        for oferta in (True, False):
            est = dict(estado, puedes_ofrecer_continuar=oferta)
            texto = _respaldo(modo, est)
            r = validar(salida(texto, etiqueta="ambiguo"), est, DATOS_E2)
            assert r.ok, (modo, oferta, texto, r.validadores)


def test_el_respaldo_no_ofrece_seguir_si_ab2_la_consumio():
    r = validar(salida("Son $99.999."), E2_SIN_OFERTA, DATOS_E2, intento=2)
    assert "seguimos" not in r.enviado.lower()
    r2 = validar(salida("Son $99.999."), E2, DATOS_E2, intento=2)
    assert "seguimos" in r2.enviado.lower()


def test_los_textos_fijos_no_tienen_cifras_inventadas_ni_markdown():
    # No se les aplica chequeo_forma completo: en produccion la capa 3 los
    # saca directo, sin pasar por el validador. El limite de 3 frases / 1
    # pregunta esta pensado para turnos casuales del modelo, no para un
    # texto de crisis real que necesita preguntar "estas a salvo?" y
    # escalar a otra linea si corresponde (asi lo valido C en T01).
    from app.modules.agent.texts import TEXTOS_FIJOS
    for nombre, texto in TEXTOS_FIJOS.items():
        assert extraer_numeros(texto) == [], nombre
        assert not any(c in texto for c in ("*", "_", "#", "`")) or "*4141" in texto, nombre
        ok, _ = chequeo_frases_prohibidas(texto)
        assert ok, nombre


def test_angustia_fuerte_permite_mas_de_tres_frases_a_proposito():
    # Es el unico texto fijo que necesita espacio extra: pregunta si esta a
    # salvo y escala al 131 si hay peligro inmediato. chequeo_forma seguiria
    # fallando aca (4 frases), y esta bien: ese chequeo no se le aplica.
    from app.modules.agent.texts import TEXTOS_FIJOS
    ok, detalle = chequeo_forma(TEXTOS_FIJOS["angustia_fuerte"])
    assert not ok and "frases" in detalle


# --- casos borde agregados en revision posterior -----------------------

def test_cifra_sin_separador_de_miles_tambien_se_detecta():
    # "$500" no trae puntos de miles, pero igual debe caer en la lista blanca
    r = validar(salida("Son como $500 de diferencia."), E2, DATOS_E2)
    assert r.validadores["lista_blanca_numerica"] == "falla"


def test_cifra_sin_separador_pasa_si_esta_en_los_datos():
    datos = {"monto_elegido": {"valor": 500, "estado": "confirmado"}}
    e4 = {"modo": "E4", "puedes_ofrecer_continuar": False}
    r = validar(salida("Con $500 el resultado cambia.", etiqueta="disposicion_explorar"),
                e4, datos)
    assert r.ok, r.validadores


def test_largo_maximo_450_pasa_451_falla():
    ok, _ = chequeo_forma("a" * 450)
    assert ok
    falla, detalle = chequeo_forma("a" * 451)
    assert not falla and "451" in detalle


def test_revisa_y_calma_no_disparan_con_otras_palabras():
    # C (23 oct): corrigio lo que yo habia dejado documentado como "a
    # proposito". "revisaremos" y "calmar" no deben caer por contener
    # "revisa"/"calma" como subcadena.
    ok, _ = chequeo_frases_prohibidas("Lo revisaremos juntos con el banco.")
    assert ok
    ok, _ = chequeo_frases_prohibidas("Vamos a calmar las cosas con el banco.")
    assert ok


def test_revisa_y_calma_si_disparan_como_palabra_completa():
    ok, detalle = chequeo_frases_prohibidas("Revisa tu correo.")
    assert not ok and "revisa" in detalle
    ok, detalle = chequeo_frases_prohibidas("Tranquila, calma.")
    assert not ok


def test_otras_frases_siguen_por_subcadena():
    # Solo "revisa" y "calma" cambiaron a palabra completa; el resto sigue
    # siendo subcadena (mas estricto que permisivo), sin que C lo pidiera.
    ok, detalle = chequeo_frases_prohibidas("No te preocupes demasiado por esto.")
    assert not ok and "no te preocupes" in detalle


def test_pausa_activa_no_dispara_ab2_por_la_palabra_aqui():
    # Bug encontrado al escribir este test: "lo dejamos aqui" en
    # PAUSA_COMPRA es parte del texto normal de la pausa, no una oferta de
    # seguir escalon (AB2 solo aplica en E1-E3). Corregido en
    # chequeo_movimientos: ahora ignora el modo si no es de la Escalera.
    estado_pa = {"modo": "PAUSA_COMPRA", "puedes_ofrecer_continuar": False}
    r = validar(salida(
        "Tu eliges: esperamos un poco o vemos la cuota. No hay apuro, lo "
        "dejamos aqui si prefieres.", etiqueta="intencion_compra_cuotas",
    ), estado_pa, {})
    assert r.validadores["movimientos"] == "ok", r.validadores


def test_apoyo_tampoco_dispara_ab2():
    r = validar(salida("Te leo. Lo dejamos aqui, sin apuro.",
                       etiqueta="cuidadora_agotada"), APOYO, {})
    assert r.validadores["movimientos"] == "ok", r.validadores


def test_essential_true_sobrevive_el_validador():
    estado_pa = {"modo": "PAUSA_COMPRA", "puedes_ofrecer_continuar": False}
    s = salida("Entiendo, eso no se posterga.", etiqueta="intencion_compra_cuotas",
                esencial=True)
    r = validar(s, estado_pa, {})
    assert r.ok, r.validadores


def test_etiqueta_desconocida_no_revienta_el_validador():
    # defensivo: si T02 cambia el set de etiquetas, esto no debe explotar
    # en produccion, debe caer como cualquier salida normal.
    r = validar(salida("Hola, como estas.", etiqueta="algo_nuevo_no_contemplado"),
                E2, DATOS_E2)
    assert r.validadores["lista_blanca_numerica"] == "ok"


# --- cambios del 23 oct, segunda vuelta con C ---------------------------

def test_cuidadora_agotada_con_4141_falla():
    r = validar(salida(
        "Te leo. Si alguna vez sientes que es mas que cansancio, el *4141 "
        "es gratis.", etiqueta="cuidadora_agotada"), E2, {})
    assert r.validadores["sin_4141_en_cuidadora_agotada"] == "falla"


def test_cuidadora_agotada_sin_4141_pasa():
    r = validar(salida("Te leo. Como estas con todo esto?",
                       etiqueta="cuidadora_agotada"), E2, {})
    assert r.ok, r.validadores


def test_angustia_fuerte_en_apoyo_usa_el_texto_de_refuerzo_no_el_de_entrada():
    # Ya estaba en APOYO (ya se le pregunto "estas a salvo?"). Repetir el
    # texto de entrada completo rompe AB7 y suena a bucle.
    apoyo = {"modo": "APOYO", "limites_ya_dichos": ["linea_crisis"]}
    r = validar(salida("No, no estoy bien.", etiqueta="angustia_fuerte"),
                apoyo, {})
    assert r.override["motivo"] == "texto_fijo_angustia_fuerte_en_apoyo"
    assert "131" in r.enviado and "estas a salvo" not in r.enviado.lower()


def test_angustia_fuerte_primera_vez_sigue_usando_el_texto_completo():
    # Fuera de APOYO (primera deteccion), el texto de entrada normal.
    r = validar(salida("Cualquier cosa.", etiqueta="angustia_fuerte"), E2, {})
    assert r.override["motivo"] == "texto_fijo_angustia_fuerte"
    assert "a salvo" in r.enviado.lower()


# --- la lista blanca con la precarga del #12 ----------------------------
#
# Desde el #12 el backend precarga todos los campos de la Escalera en E0-E4,
# para que ella no espere un turno cuando pide un dato del escalon siguiente.
# `prompts.py` los vuelve a filtrar por modo, asi que el modelo solo ve los
# del escalon actual; la lista blanca tiene que filtrar igual. Cuando tomaba
# `datos` completo, una cifra que el modelo nunca vio igual pasaba la capa 4.

E0 = {"modo": "E0", "puedes_ofrecer_continuar": False, "limites_ya_dichos": []}
E1 = {"modo": "E1", "puedes_ofrecer_continuar": True, "limites_ya_dichos": []}


def test_una_cifra_precargada_de_otro_escalon_no_pasa_en_e1():
    """En E1 el modelo solo ve el vencimiento. El saldo viene precargado pero
    no se le muestra, asi que si aparece es una alucinacion y se descarta."""
    r = validar(salida("Vence el 3 de noviembre y debes $1.064.000."), E1, DATOS_E2)

    assert not r.ok
    assert r.validadores["lista_blanca_numerica"] == "falla"


def test_el_dato_del_escalon_actual_si_pasa_con_la_precarga():
    """El otro lado de la moneda: filtrar no puede romper el turno legitimo."""
    r = validar(salida("Vence el 3 de noviembre."), E1, DATOS_E2)

    assert r.ok, r.validadores


def test_en_e0_ninguna_cifra_precargada_esta_autorizada():
    r = validar(salida("Tu pago minimo es $74.350."), E0, DATOS_E2)

    assert not r.ok
    assert r.validadores["lista_blanca_numerica"] == "falla"


def test_el_condicional_se_autoriza_con_la_etiqueta_de_este_turno():
    """`facturado` esta precargado en E2, pero solo se autoriza si ella
    pregunto por el en ESTE mensaje. La etiqueta es la que lo habilita."""
    preguntando = validar(
        salida("El monto facturado es $868.000.", etiqueta="pregunta_monto_facturado"),
        E2_SIN_OFERTA,
        DATOS_E2,
    )
    assert preguntando.ok, preguntando.validadores


def test_el_condicional_no_se_autoriza_si_ella_no_pregunto():
    sin_preguntar = validar(
        salida("El monto facturado es $868.000.", etiqueta="solicitud_informacion"),
        E2_SIN_OFERTA,
        DATOS_E2,
    )

    assert not sin_preguntar.ok
    assert sin_preguntar.validadores["lista_blanca_numerica"] == "falla"
