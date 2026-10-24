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

def test_cuatro_frases_se_recortan_a_tres():
    """Si lo unico que sobra son frases, se recorta en vez de descartar.

    Descartar cambiaba una respuesta buena de mas por el texto de relleno del
    modo, que no dice nada y, en E1-E3, promete un mensaje que nunca llega.
    """
    r = validar(salida("Hola. Te leo. Aca estoy. Y tambien aca."), E2, DATOS_E2)

    assert r.validadores["forma"] == "recortado"
    assert r.enviado == "Hola. Te leo. Aca estoy."
    assert r.override["motivo"] == "recortado_a_tres_frases"


def test_el_recorte_no_rescata_otras_fallas():
    """Solo el largo. Una frase prohibida o una cifra inventada no se arreglan
    cortando: esas van al reintento."""
    r = validar(salida("Tranquila. Te leo. Aca estoy. Y tambien aca."), E2, DATOS_E2)
    assert r.validadores["frases_prohibidas"] == "falla"

    larga = "Debes $999. Te leo. Aca estoy. Y tambien aca."
    r2 = validar(salida(larga), E2, DATOS_E2)
    assert r2.validadores["lista_blanca_numerica"] == "falla"


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


def test_la_salida_de_las_opciones_no_cuenta_como_la_oferta_de_seguir():
    """MOVIMIENTO_ACLARAR pide la salida escrita "o lo dejamos aqui" porque C
    la pidio asi, y `RE_OFERTA` no la distingue de la oferta de seguir.
    Medido el 24 oct en C02 turno 5: el turno de aclaracion se rechazaba
    siempre --AB2 ya habia consumido la oferta en ese escalon-- y Patricia
    recibia el texto de respaldo en vez de las rutas."""
    estado = dict(E2_SIN_OFERTA, puedes_aclarar_ruta=True)
    r = validar(
        salida("Que te complica?\n1. El pago minimo\n2. El saldo\no lo dejamos aqui"),
        estado, DATOS_E2,
    )
    assert r.validadores["movimientos"] == "ok", r.validadores


# --- chequeo 5: coherencia etiqueta-modo ------------------------------

def test_cifra_en_apoyo_falla():
    """En APOYO el backend no inyecta nada, asi que la fecha no viaja.

    Y si apareciera, se rechaza: primero por la lista blanca (no hay ninguna
    cifra autorizada) y, para lo que la lista blanca no ve, por la coherencia
    del modo (el nombre de un mes).
    """
    r = validar(salida("Te leo. Tu vencimiento sigue siendo el 3 de noviembre.",
                       etiqueta="cuidadora_agotada"), APOYO, {})
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
# Regla: se autoriza lo que el contexto le MOSTRO.
#
# En los escalones eso incluye el segundo bloque de DATOS_DISPONIBLES
# ("solo si ella lo pide en este mensaje"), porque la precarga si se lo
# muestra. Quien verifica que la respuesta corresponda al modo en que
# termino el turno es el backend, que conoce el modo final; el agente no,
# porque ese modo se decide con la etiqueta que el agente acaba de emitir.
#
# Fuera de los escalones no hay segundo bloque y no se autoriza nada. Esa
# parte no se relajo: es la que sostiene C01 y C10.

E0 = {"modo": "E0", "puedes_ofrecer_continuar": False, "limites_ya_dichos": []}
E1 = {"modo": "E1", "puedes_ofrecer_continuar": True, "limites_ya_dichos": []}


def test_puede_contestar_una_pregunta_directa_con_un_dato_precargado():
    """C03, medido el 23 oct: a "cuando vence?" en E0 el modelo contestaba
    bien ("vence el 3 de noviembre de 2026") y la lista blanca lo descartaba,
    asi que salia el texto de respaldo. El dato estaba precargado y visible."""
    r = validar(salida("Vence el 3 de noviembre de 2026."), E0, DATOS_E2)

    assert r.ok, r.validadores


def test_un_dato_precargado_de_otro_escalon_esta_autorizado_en_los_escalones():
    r = validar(salida("Vence el 3 de noviembre y tu saldo es $1.064.000."), E1, DATOS_E2)

    assert r.validadores["lista_blanca_numerica"] == "ok"


def test_una_cifra_que_nadie_inyecto_sigue_sin_pasar():
    """Lo que el #15 vino a cerrar y no se relaja: una cifra inventada no
    esta en `datos`, asi que no se autoriza en ningun modo."""
    r = validar(salida("Tu saldo es $2.500.000."), E1, DATOS_E2)

    assert not r.ok
    assert r.validadores["lista_blanca_numerica"] == "falla"


def test_en_las_pausas_y_en_apoyo_no_se_autoriza_ninguna_cifra():
    """La garantia de C01 y C10, ahora en dos capas.

    El validador ya no re-filtra por modo: con el turno en tres pasos el
    backend inyecta exactamente los campos del modo final, y en estos modos
    `fields_for_mode` devuelve ninguno. Asi que lo que llega aqui es `{}`, y
    cualquier cifra en el mensaje es una alucinacion que la lista blanca
    atrapa.
    """
    from app.modules.agent.interface import ConversationMode as M
    from app.modules.agent.interface import fields_for_mode

    for modo in ("PAUSA_RECHAZO", "APOYO", "CIERRE", "PAUSA_COMPRA"):
        # Capa 1: el backend no tiene nada que mandar en estos modos.
        assert fields_for_mode(M(modo)) == [], modo
        # Capa 2: y si una cifra apareciera igual, el validador la rechaza.
        estado = {"modo": modo, "puedes_ofrecer_continuar": False, "limites_ya_dichos": []}
        r = validar(salida("Tu pago minimo es $74.350.", etiqueta="cuidadora_agotada"),
                    estado, {})
        assert not r.ok, modo
        assert r.validadores["lista_blanca_numerica"] == "falla", modo


def test_las_pausas_y_el_cierre_tampoco_admiten_una_fecha():
    """La lista blanca numerica no ve el nombre de un mes.

    `chequeo_coherencia_etiqueta_modo` solo miraba APOYO, asi que "vence en
    noviembre" pasaba en una pausa.
    """
    for modo in ("APOYO", "PAUSA_RECHAZO", "CIERRE"):
        estado = {"modo": modo, "puedes_ofrecer_continuar": False, "limites_ya_dichos": []}
        r = validar(salida("Eso vence en noviembre.", etiqueta="cuidadora_agotada"),
                    estado, {})
        assert not r.ok, modo
        assert r.validadores["coherencia_etiqueta_modo"] == "falla", modo


def test_pausa_compra_con_el_calculo_si_autoriza_sus_dos_cifras():
    """Es la excepcion: si ella pidio ver cuanto le suma la cuota, el backend
    inyecta el minimo y el minimo nuevo, y esas dos se pueden decir."""
    datos = {"minimo": {"valor": 74350, "estado": "confirmado"},
             "minimo_con_cuota": {"valor": 84350, "estado": "confirmado"}}
    estado = {"modo": "PAUSA_COMPRA", "puedes_ofrecer_continuar": False,
              "limites_ya_dichos": []}
    r = validar(salida("Tu minimo pasaria de $74.350 a $84.350."), estado, datos)

    assert r.ok
    # Pero una cifra que no viajo sigue siendo una alucinacion.
    r2 = validar(salida("Tu saldo es $1.064.000."), estado, datos)
    assert not r2.ok


def test_no_es_para_tanto_es_frase_prohibida():
    """Del prompt del Playground (23 oct): minimiza lo que ella siente y es
    literal, asi que se puede chequear."""
    ok, detalle = chequeo_frases_prohibidas("No es para tanto, Patricia.")
    assert not ok
    assert "no es para tanto" in detalle


def test_las_opciones_numeradas_no_cuentan_como_frases():
    """Medido el 23 oct: las dos opciones de PAUSA_COMPRA daban 6 frases
    contra un maximo de 3, asi que toda respuesta con opciones se caia al
    texto de respaldo. Y quitar solo el prefijo "1." no alcanza: el texto de
    cada opcion tambien termina en punto (6 -> 4, sigue sobre el limite).
    """
    mensaje = (
        "Gracias por contarmelo antes de decidir. Tu eliges.\n"
        "1. Esperar diez minutos y ver como te sientes.\n"
        "2. Ver cuanto le sumaria esa cuota a tu pago minimo."
    )
    assert contar_frases(mensaje) == 2
    assert chequeo_forma(mensaje)[0]


def test_tres_caminos_con_salida_pasan_la_forma():
    """El rescate de "no te entendi" (AB1/AB8) ofrece hasta tres caminos."""
    mensaje = (
        "Dime que lo complica.\n"
        "1. No saber cuanto es.\n"
        "2. El susto de verlo.\n"
        "3. O lo dejamos aqui."
    )
    assert chequeo_forma(mensaje)[0]


def test_las_frases_de_verdad_siguen_contando():
    """El arreglo no puede relajar el limite para el texto normal."""
    assert contar_frases("Una. Dos. Tres. Cuatro.") == 4
    assert not chequeo_forma("Una. Dos. Tres. Cuatro.")[0]


# --- chequeo 7: fuga de andamiaje ---------------------------------------


def test_un_nombre_de_bloque_en_la_respuesta_falla():
    """Patricia no tiene por que saber que existe un bloque llamado "DATOS QUE
    PUEDES DECIR". Con el contexto resuelto hay mas encabezados en texto plano
    que antes, asi que el riesgo de que los repita sube, no baja."""
    for intruso in ("Segun DATOS QUE PUEDES DECIR, tu minimo es $74.350.",
                    "Mis MOVIMIENTOS PERMITIDOS no incluyen eso.",
                    "Eso viene en LOS DOS ULTIMOS TURNOS."):
        r = validar(salida(intruso), E2, DATOS_E2)
        assert not r.ok, intruso
        assert r.validadores["fuga_de_andamiaje"] == "falla", intruso


def test_otros_nombres_del_andamiaje_caen_antes_y_tambien_sirve():
    """Dos nombres del contexto los atajan chequeos anteriores, y esta bien:

    - `minimo_con_cuota` cae en la forma, porque el guion bajo no existe en el
      texto plano de WhatsApp.
    - "LO QUE TIENES QUE HACER" cae en frases prohibidas, porque contiene
      "tienes que".
    """
    r = validar(salida("El campo minimo_con_cuota no esta disponible."), E2, DATOS_E2)
    assert r.validadores["forma"] == "falla"

    r2 = validar(salida("Eso no esta en LO QUE TIENES QUE HACER."), E2, DATOS_E2)
    assert r2.validadores["frases_prohibidas"] == "falla"


def test_una_respuesta_normal_no_dispara_el_chequeo_de_andamiaje():
    r = validar(salida("Tu pago minimo es $74.350. Es lo que pide el banco."),
                E2, DATOS_E2)
    assert r.validadores["fuga_de_andamiaje"] == "ok"
