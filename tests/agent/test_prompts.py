"""Tests del armado de contexto.

Desde la precarga del #12 la propiedad cambio de naturaleza: el modelo SI ve
los datos de otros escalones, en un segundo bloque, y la garantia es que
solo los dice si ella los pide en este mensaje. Donde no cambio nada es en
las pausas, APOYO y CIERRE: ahi no hay ninguna cifra en el contexto, y eso
es lo que sostiene C01 y C10.
"""

import json
import pathlib

from app.modules.agent.prompts import (
    ESTATICO,
    MODOS,
    MOVIMIENTOS,
    armar_contexto,
    campos_visibles,
)
from app.modules.agent.validators import normalizar


def plano(texto):
    """Minusculas, sin acentos y en una sola linea.

    El prompt va envuelto a 76 columnas, asi que una frase puede partirse en
    dos lineas: comparar sin colapsar los saltos da falsos negativos.
    """
    return " ".join(normalizar(texto).split())

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

DATOS = json.load(open(FIXTURES / "statement_complete.json"))


def ctx(modo, **kw):
    estado = {"modo": modo, "puedes_ofrecer_continuar": kw.pop("oferta", True)}
    estado.update({k: v for k, v in kw.items() if k in
                   ("puedes_aclarar_ruta", "limites_ya_dichos")})
    extra = {k: v for k, v in kw.items() if k in
             ("pregunta_previa", "pidio_calculo_cuota")}
    return armar_contexto(estado, DATOS, **extra)


# Desde la precarga del #12 el modelo SI ve los datos de otros escalones,
# pero en un segundo bloque y con la condicion escrita. La garantia dejo de
# ser "no puede verlo" y paso a ser "solo lo dice si ella lo pide en este
# mensaje", con la lista blanca del #15 descartando lo demas. Estos tests
# verifican en QUE bloque cae cada dato.


def escalon(salida):
    """Lo que el modelo puede decir por iniciativa propia."""
    bloque = salida.split("DE ESTE ESCALON")[1] if "DE ESTE ESCALON" in salida else ""
    return bloque.split("SOLO SI ELLA LO PIDE")[0]


def reservado(salida):
    """Lo que solo puede decir si ella lo pide en este mensaje."""
    if "SOLO SI ELLA LO PIDE" not in salida:
        return ""
    return salida.split("SOLO SI ELLA LO PIDE")[1].split("HISTORIAL")[0]


def test_e1_tiene_el_vencimiento_en_su_escalon_y_el_resto_reservado():
    salida = ctx("E1")

    assert "3 de noviembre" in escalon(salida)
    # precargados, pero no para ofrecerlos por iniciativa propia
    assert "1.064.000" in reservado(salida)
    assert "74.350" in reservado(salida)
    assert "1.064.000" not in escalon(salida)


def test_apoyo_no_recibe_ningun_dato():
    """Sin cambios y es lo mas importante de este archivo: en APOYO no hay
    segundo bloque ni aunque el backend mande datos. Es lo que sostiene C10."""
    salida = ctx("APOYO")

    assert "ninguno" in salida
    assert "SOLO SI ELLA LO PIDE" not in salida
    for cifra in ("74.350", "1.064.000", "noviembre", "868.000"):
        assert cifra not in salida, cifra


def test_las_pausas_y_el_cierre_tampoco_reciben_el_segundo_bloque():
    for modo in ("PAUSA_RECHAZO", "CIERRE"):
        salida = ctx(modo)
        assert "ninguno" in salida, modo
        assert "SOLO SI ELLA LO PIDE" not in salida, modo
        for cifra in ("74.350", "1.064.000", "noviembre"):
            assert cifra not in salida, (modo, cifra)


def test_e0_ve_la_escalera_reservada_pero_nada_de_su_escalon():
    """C03: a "cuando vence?" en E0 el modelo tenia que contestar "la podemos
    ver en un momento" porque no tenia la fecha. Ahora la tiene, reservada."""
    salida = ctx("E0")

    assert escalon(salida).strip() == ""
    assert "3 de noviembre" in reservado(salida)


def test_facturado_pasa_al_escalon_cuando_ella_pregunta():
    assert "868.000" in reservado(ctx("E2"))
    assert "868.000" in escalon(ctx("E2", pregunta_previa="pregunta_monto_facturado"))


def test_la_tasa_pasa_al_escalon_desde_e3():
    assert "2,8" in reservado(ctx("E2", pregunta_previa="pregunta_intereses"))
    assert "2,8" in escalon(ctx("E3", pregunta_previa="pregunta_intereses"))


def test_el_desglose_es_del_escalon_solo_en_e3():
    assert "23.100" in reservado(ctx("E2"))
    assert "23.100" in escalon(ctx("E3"))


def test_pausa_activa_sin_cifras_hasta_que_ella_pida_el_calculo():
    salida_sin_calculo = ctx("PAUSA_COMPRA")
    assert "74.350" not in salida_sin_calculo
    assert "SOLO SI ELLA LO PIDE" not in salida_sin_calculo

    datos = dict(DATOS, minimo_con_cuota={"valor": 84350, "estado": "confirmado"})
    salida = armar_contexto({"modo": "PAUSA_COMPRA"}, datos,
                            pidio_calculo_cuota=True)
    assert "74.350" in salida and "84.350" in salida


def test_la_oferta_desaparece_cuando_ab2_la_consumio():
    """AB2 se expresa como movimiento, no como flag en ESTADO.

    Antes el contexto traia `puedes_ofrecer_continuar: no` y ademas omitia el
    movimiento: dos senales para la misma regla, y una de ellas obligaba al
    modelo a evaluar una condicion que el codigo ya habia evaluado. Ahora
    viaja solo el movimiento.
    """
    assert "seguimos" in ctx("E2").lower()
    assert "seguimos" not in ctx("E2", oferta=False).lower()
    assert "puedes_ofrecer_continuar" not in ctx("E2", oferta=False)


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


def test_ningun_flag_de_estado_viaja_al_contexto():
    """ESTADO es solo el modo.

    `puedes_ofrecer_continuar`, `puedes_aclarar_ruta`, `limites_ya_dichos` y
    `escalon_maximo` ya estan resueltos en los movimientos. Imprimirlos
    ademas le pide al modelo que evalue condiciones que el codigo evaluo, y
    es el hallazgo 6 del diagnostico de codigo (el prompt nunca decia que
    hacer con ellos).
    """
    flags = ("puedes_ofrecer_continuar", "puedes_aclarar_ruta",
             "limites_ya_dichos", "escalon_maximo")
    for modo in MODOS:
        salida = armar_contexto(
            {"modo": modo, "puedes_ofrecer_continuar": True,
             "puedes_aclarar_ruta": True, "limites_ya_dichos": [],
             "escalon_maximo": "E2"},
            DATOS,
        )
        for flag in flags:
            assert flag not in salida, f"{modo}: {flag}"


def test_aclarar_ruta_es_un_movimiento_no_un_flag():
    """AB1/AB8: o el movimiento esta, o la regla no existe en este turno."""
    con = armar_contexto({"modo": "E2", "puedes_aclarar_ruta": True}, DATOS)
    sin = armar_contexto({"modo": "E2", "puedes_aclarar_ruta": False}, DATOS)
    assert "que lo complica" in con
    assert "o lo dejamos aqui" in con
    assert "que lo complica" not in sin


# --- cambios del 23 oct, segunda vuelta con C ---------------------------

def test_escalon_maximo_alimenta_la_retoma_sin_viajar_como_flag():
    """El escalon maximo sigue decidiendo, pero desde el codigo.

    Viajaba como `escalon_maximo: E2` y el prompt no decia que hacer con el.
    Lo que de verdad lo usa es MOVIMIENTOS_RETOMA, que el codigo ya resuelve.
    """
    salida = armar_contexto({"modo": "E0", "escalon_maximo": "E2"}, DATOS)
    assert "escalon_maximo" not in salida
    assert "tu pago minimo y tu saldo" in salida


def test_retoma_sin_avance_previo_mantiene_el_e0_normal():
    salida = armar_contexto({"modo": "E0", "escalon_maximo": "E0"}, DATOS)
    assert "Conversar y acusar recibo" in salida
    assert "la vez pasada" not in salida


def test_retoma_con_avance_previo_reconoce_sin_repetir_cifras():
    salida = armar_contexto({"modo": "E0", "escalon_maximo": "E2"}, DATOS)
    assert "tu pago minimo y tu saldo" in salida
    assert "logro" in salida  # la instruccion de no usar la palabra "logro"
    # Con la precarga las cifras existen en el contexto, pero en el bloque
    # reservado: el modelo no puede ofrecerlas por iniciativa propia, que es
    # lo que esta regla protege (AB6, no repetir lo ya visto).
    assert "74.350" not in escalon(salida)
    assert "1.064.000" not in escalon(salida)


# --- movimientos medidos contra el modelo real (23 oct) -----------------


def test_pausa_compra_con_el_calculo_listo_manda_contarlo():
    """C05 turno 3: con `minimo_con_cuota` en el contexto, el modelo volvia a
    ofrecer las dos opciones en vez de dar la cifra. Medido contra el modelo
    real: "$60.000 en 6 cuotas" recibio "cual de las opciones prefieres?"."""
    datos = dict(DATOS, minimo_con_cuota={"valor": 84350, "estado": "confirmado"})
    salida = armar_contexto({"modo": "PAUSA_COMPRA"}, datos, pidio_calculo_cuota=True)

    assert "cuanto le sumaria esa cuota" in salida
    assert "NO vuelvas a ofrecer las dos opciones" in salida
    # la oferta numerada no debe aparecer: ella ya eligio
    assert "Esperar diez minutos" not in salida


def test_pausa_compra_sin_el_calculo_sigue_ofreciendo_las_dos_opciones():
    salida = armar_contexto({"modo": "PAUSA_COMPRA"}, {})

    assert "Esperar diez minutos" in salida


def test_con_el_calculo_pedido_pero_sin_monto_pregunta_el_monto():
    """C05 turno 2: ella eligio la opcion 2 y todavia no dijo cuanto. No hay
    nada que calcular, pero tampoco se vuelven a ofrecer las opciones."""
    datos = dict(DATOS, minimo_con_cuota={"valor": None, "estado": "ausente"})
    salida = armar_contexto({"modo": "PAUSA_COMPRA"}, datos, pidio_calculo_cuota=True)

    assert "el monto y en cuantas cuotas" in salida
    assert "Esperar diez minutos" not in salida


def test_en_cierre_puede_no_despedirse_si_ella_retoma():
    """C01 turno 4: el modo se actualiza despues del turno, asi que con
    modo=CIERRE el modelo se despedia de alguien que volvia. Medido: "hola,
    ya estoy mejor" recibio "que estes bien"."""
    salida = armar_contexto({"modo": "CIERRE"}, {})

    assert "NO te" in salida and "despidas" in salida


def test_los_otros_modos_no_reciben_el_movimiento_de_retoma():
    for modo in ("E0", "E2", "APOYO", "PAUSA_RECHAZO"):
        assert "despidas" not in armar_contexto({"modo": modo}, {})


# --- lo que la parte estatica tiene que decir ---------------------------


def test_el_prompt_prohibe_pedir_credenciales():
    """Nada en el prompt lo prohibia. Patricia esta asustada y el canal es
    WhatsApp: es el escenario de phishing mas obvio del proyecto."""
    for frase in ("claves", "RUT", "códigos de verificación"):
        assert frase in ESTATICO, frase
    assert "no los repitas" in ESTATICO
    assert "no los comparta por chat" in ESTATICO


def test_el_prompt_niega_hacer_pagos_y_tramites():
    assert "No puedes hacer pagos, transferencias ni trámites" in ESTATICO


def test_el_prompt_resiste_que_le_pidan_ignorar_las_instrucciones():
    assert "ignorar estas instrucciones" in ESTATICO


def test_el_prompt_no_habla_de_un_documento():
    """Las cifras vienen del banco simulado, no de un PDF. "documento" le
    sugiere al modelo que ella tiene un papel que podria mirar."""
    assert "documento" not in ESTATICO
    assert "su estado de cuenta" in ESTATICO


def test_el_prompt_explica_los_meses_y_calla_el_faltante():
    assert "meses_ son meses" in ESTATICO
    assert "faltante_para_gastos_esenciales, NO lo menciones" in ESTATICO


def test_el_prompt_pide_no_mencionar_ingresos_ni_gastos():
    assert "tampoco los" in ESTATICO and "menciones por iniciativa propia" in ESTATICO


def test_el_prompt_pide_no_repetir_cifras_cuando_ella_duda():
    assert "no repitas cifras que ya dijiste" in ESTATICO


# --- los meses no son pesos ---------------------------------------------


def test_los_meses_no_se_formatean_como_dinero():
    """`meses_ahorrados: 19` salia como "$19" y el modelo lo repetia como
    dinero."""
    motor = {"meses_ahorrados": 19, "intereses_ahorrados": 123000}
    salida = armar_contexto({"modo": "E4"}, DATOS, motor)

    assert "meses_ahorrados: 19 meses" in salida
    assert "$19" not in salida
    assert "intereses_ahorrados: $123.000" in salida


# --- lo que el prompt dejo de decir (causa 1, medida el 23 oct) ----------


def test_el_prompt_no_trae_la_frase_de_escape():
    """"eso lo pueden ver en un momento" venia escrita en el prompt.

    Medido contra el modelo real: con la fecha y el saldo precargados, a
    "cuanto debo en total?" el modelo contestaba "el total lo podemos ver en
    un momento". Tener el dato y aplazarlo fue 14 de 21 respuestas
    problematicas, y la frase estaba en el prompt para que la copiara.
    """
    # La frase solo puede existir como contraejemplo. Como instruccion es la
    # causa 1; en una linea "Malo:" es lo que le ensena a no hacerlo.
    for linea in ESTATICO.splitlines():
        if "en un momento" in normalizar(linea):
            assert linea.strip().startswith("Malo:"), linea
    assert "aplazarlo es la falla mas comun" in plano(ESTATICO)
    assert "lo tenias y lo aplazaste" in plano(ESTATICO)


def test_e0_no_afirma_que_no_tiene_datos():
    """En E0 viajan los 9 campos de la Escalera por la precarga del #12.

    El movimiento decia "no tienes ningun dato", que es falso, y empujaba al
    modelo a la frase generica incluso cuando ella pedia un dato concreto.
    """
    movimientos = " ".join(MOVIMIENTOS["E0"])
    assert "no tienes ningun dato" not in movimientos
    assert "DATOS_DISPONIBLES" in movimientos


def test_el_ejemplo_de_crisis_no_es_cansancio():
    """El ejemplo de angustia_fuerte era "ya no puedo mas".

    Contradice la regla que C validó en C10: "no puedo mas" sin señales de
    riesgo es cuidadora_agotada. El ejemplo empujaba a mandar el texto de
    crisis por cansancio.
    """
    assert 'angustia_fuerte          "no quiero seguir viviendo"' in ESTATICO
    assert "no puedo mas" not in plano(ESTATICO).split("etiquetas")[0]
    assert "es cuidadora_agotada, no angustia_fuerte" in ESTATICO


def test_el_prompt_dice_para_que_existe():
    """Faltaba el objetivo. Sin el, el modelo no tiene con que resolver lo
    que la tabla no cubre, y los dos fallos P0 fueron eso."""
    assert "No eres terapeuta" in ESTATICO
    assert "no es que pague mas rapido" in plano(ESTATICO)
    assert "LO QUE SABES DE ALGUIEN COMO PATRICIA" in ESTATICO


def test_un_saludo_no_es_ambiguo():
    """"Hola" salia como `ambiguo`, que incrementa recognition_failures: dos
    saludos y se disparan las opciones de rescate para alguien que solo
    saludo. Hasta que exista la etiqueta `saludo` (contrato), va en tangente.
    """
    assert "Un saludo suelto NO es ambiguo" in ESTATICO
    assert 'tangente                 un saludo suelto' in ESTATICO


def test_la_salida_de_las_opciones_usa_las_palabras_que_el_codigo_reconoce():
    """RE_OFERTA reconoce "lo dejamos aqui"; "dejarlo hasta aqui" se le
    escapa, y entonces la oferta sale sin consumir el contador de AB2."""
    assert '"o lo dejamos aquí"' in ESTATICO
    assert "dejarlo hasta aqui" not in plano(ESTATICO)
