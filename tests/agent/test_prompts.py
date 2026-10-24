"""Tests del armado de contexto.

Desde la precarga del #12 la propiedad cambio de naturaleza: el modelo SI ve
los datos de otros escalones, en un segundo bloque, y la garantia es que
solo los dice si ella los pide en este mensaje. Donde no cambio nada es en
las pausas, APOYO y CIERRE: ahi no hay ninguna cifra en el contexto, y eso
es lo que sostiene C01 y C10.
"""

import json
import pathlib

from app.modules.agent.interface import Classification, Intent
from app.modules.agent.prompts import (
    CLASIFICADOR,
    MODOS,
    MOVIMIENTOS,
    REDACTOR,
    campos_visibles,
    contexto_redaccion,
    movimientos_para,
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


def armar_contexto(estado, datos, pregunta_previa=None, pidio_calculo_cuota=False,
                   historial=None, calculos=None):
    """El contexto de redaccion, armado como lo hace el agente.

    Existe para que estos tests sigan hablando de "el contexto del turno" sin
    repetir en cada uno la resolucion de movimientos. El agente hace lo mismo
    en `service.redactar()`: resuelve los movimientos y se los pasa al
    contexto, que ya no evalua nada.
    """
    modo = estado.get("modo", "E0")
    etiqueta = pregunta_previa or estado.get("etiqueta")
    clasificacion = Classification(
        intent=etiqueta or Intent.OFF_TOPIC,
        essential=estado.get("esencial"),
        pace=estado.get("ritmo"),
    )
    visibles = campos_visibles(modo, etiqueta, pidio_calculo_cuota)
    datos_del_modo = {c: datos[c] for c in visibles if c in (datos or {})}
    movimientos = movimientos_para(
        modo,
        clasificacion=clasificacion,
        puede_ofrecer=estado.get("puedes_ofrecer_continuar", False),
        puede_aclarar=estado.get("puedes_aclarar_ruta", False),
        limites=estado.get("limites_ya_dichos") or (),
        escalon_maximo=estado.get("escalon_maximo", "E0"),
        pidio_calculo_cuota=pidio_calculo_cuota,
        datos=datos_del_modo,
        calculos=calculos,
    )
    return contexto_redaccion(
        datos_del_modo, calculos, movimientos, estado.get("mensaje", "x"), historial
    )


def ctx(modo, **kw):
    estado = {"modo": modo, "puedes_ofrecer_continuar": kw.pop("oferta", True)}
    estado.update({k: v for k, v in kw.items() if k in
                   ("puedes_aclarar_ruta", "limites_ya_dichos", "etiqueta",
                    "esencial", "ritmo", "escalon_maximo")})
    extra = {k: v for k, v in kw.items() if k in
             ("pregunta_previa", "pidio_calculo_cuota", "calculos")}
    return armar_contexto(estado, DATOS, **extra)


# El bloque "solo si ella lo pide" desaparecio con el turno en tres pasos.
# Existia porque el modo se decidia DESPUES de redactar: habia que mandar toda
# la Escalera y verificar la respuesta a posteriori. Era la causa 1 de las
# respuestas vagas, medida contra el modelo real: el modelo tenia la cifra y
# una instruccion ambigua sobre cuando decirla, y contestaba "lo podemos ver
# en un momento". Ahora el contexto trae exactamente los campos del modo
# final, asi que no hay nada que interpretar.


def test_el_contexto_trae_exactamente_los_campos_del_modo():
    for modo, esperados in (
        ("E1", {"vencimiento"}),
        ("E2", {"vencimiento", "minimo", "saldo"}),
        ("APOYO", set()),
        ("PAUSA_RECHAZO", set()),
        ("CIERRE", set()),
    ):
        salida = ctx(modo)
        bloque = salida.split("LO QUE TIENES QUE HACER")[0]
        presentes = {c for c in DATOS if c + ":" in bloque}
        assert presentes == esperados, (modo, presentes)


def test_no_queda_ningun_bloque_condicional_en_el_contexto():
    """Ni "solo si ella lo pide", ni flags de estado, ni ramas."""
    for modo in MODOS:
        salida = ctx(modo)
        plano_salida = plano(salida)
        assert "solo si ella lo pide" not in plano_salida, modo
        for flag in ("puedes_ofrecer_continuar", "puedes_aclarar_ruta",
                     "limites_ya_dichos", "escalon_maximo"):
            assert flag not in salida, (modo, flag)


def test_el_facturado_entra_en_el_turno_en_que_ella_pregunta():
    """Antes entraba un turno tarde, con `previous_intent`."""
    assert "868.000" in ctx("E2", pregunta_previa="pregunta_monto_facturado")
    assert "868.000" not in ctx("E2")


def test_la_tasa_entra_desde_e3_cuando_pregunta_por_intereses():
    assert "2,8" in ctx("E3", pregunta_previa="pregunta_intereses")
    assert "2,8" not in ctx("E2", pregunta_previa="pregunta_intereses")


def test_el_desglose_es_del_escalon_solo_en_e3():
    assert "23.100" in ctx("E3")
    assert "23.100" not in ctx("E2")


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


def test_los_bloques_siempre_estan():
    for modo in MODOS:
        salida = ctx(modo)
        for bloque in ("DATOS QUE PUEDES DECIR", "LO QUE TIENES QUE HACER",
                       "LOS DOS ULTIMOS TURNOS"):
            assert bloque in salida, (modo, bloque)
        # Un bloque vacio se escribe igual: su ausencia haria que improvise.
        assert "ninguno" in salida or "DATOS QUE PUEDES DECIR\n" in salida


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


def test_la_pausa_acoge_en_vez_de_repetir_su_texto():
    """`rechazo_pausa` ya no llega al redactor: sale como texto fijo sin
    llamar al modelo. Lo que llega a PAUSA_RECHAZO es ella pidiendo algo
    estando en pausa, y ahi lo correcto es acoger, no repetir la pausa.
    """
    salida = ctx("PAUSA_RECHAZO")
    assert "Acoger lo que dijo" in salida
    assert "texto fijo de rechazo_pausa" not in salida


def _viejo_test_pausa_tiene_el_texto_literal_de_un_solo_movimiento():
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
    """AB1/AB8: o el movimiento esta, o la regla no existe en este turno.

    Y solo con la etiqueta `ambiguo`: aclarar la ruta es la respuesta a no
    haberla entendido, no algo que se ofrece en cualquier turno.
    """
    estado = {"modo": "E2", "etiqueta": Intent.AMBIGUOUS}
    con = armar_contexto({**estado, "puedes_aclarar_ruta": True}, DATOS)
    sin = armar_contexto({**estado, "puedes_aclarar_ruta": False}, DATOS)
    assert "que lo complica" in con
    assert "o lo dejamos aqu" in con
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
    assert "Acusar recibo" in salida
    assert "la vez pasada" not in salida


def test_retoma_con_avance_previo_reconoce_sin_repetir_cifras():
    salida = armar_contexto({"modo": "E0", "escalon_maximo": "E2"}, DATOS)
    assert "tu pago minimo y tu saldo" in salida
    assert "logro" in salida  # la instruccion de no usar la palabra "logro"
    # Con la precarga las cifras existen en el contexto, pero en el bloque
    # reservado: el modelo no puede ofrecerlas por iniciativa propia, que es
    # lo que esta regla protege (AB6, no repetir lo ya visto).
    assert "74.350" not in salida
    assert "1.064.000" not in salida


# --- movimientos medidos contra el modelo real (23 oct) -----------------


def test_pausa_compra_con_el_calculo_listo_manda_contarlo():  # noqa: D103
    """C05 turno 3: con `minimo_con_cuota` en el contexto, el modelo volvia a
    ofrecer las dos opciones en vez de dar la cifra. Medido contra el modelo
    real: "$60.000 en 6 cuotas" recibio "cual de las opciones prefieres?"."""
    datos = dict(DATOS, minimo_con_cuota={"valor": 84350, "estado": "confirmado"})
    salida = armar_contexto({"modo": "PAUSA_COMPRA"}, datos, pidio_calculo_cuota=True)

    assert "cuanto le sumaria esa cuota" in salida
    assert "NO volver a ofrecer las dos opciones" in salida
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


def test_la_retoma_desde_cierre_ya_no_necesita_una_excepcion():
    """C01 turno 4: con modo=CIERRE el modelo se despedia de alguien que
    volvia. Medido: "hola, ya estoy mejor" recibio "que estes bien".

    Hacia falta un movimiento que dijera "NO te despidas si ella retoma",
    porque el modo se actualizaba despues de redactar. Con el turno en tres
    pasos la transicion corre antes: si ella retoma, el redactor ya recibe E0
    y nunca ve CIERRE. La excepcion se borro en vez de mantenerse.
    """
    from app.modules.agent.interface import ConversationMode as M
    from app.modules.conversation.transitions import siguiente_modo

    assert siguiente_modo(M.CLOSING, M.BALANCE_OVERVIEW,
                          Intent.VOLUNTARY_RESUME, None) is M.GREETING

    salida = armar_contexto({"modo": "CIERRE"}, {})
    assert "Despedirte" in salida
    assert "despidas" not in salida

    # Y en E0, con el escalon maximo en E2, el movimiento es reconocer lo visto.
    retoma = armar_contexto({"modo": "E0", "escalon_maximo": "E2"}, {})
    assert "la vez pasada vieron" in retoma


# --- lo que la parte estatica tiene que decir ---------------------------


def test_el_prompt_prohibe_pedir_credenciales():
    """Nada en el prompt lo prohibia. Patricia esta asustada y el canal es
    WhatsApp: es el escenario de phishing mas obvio del proyecto."""
    for frase in ("claves", "RUT", "códigos de verificación"):
        assert frase in REDACTOR, frase
    assert "no los repitas" in REDACTOR
    assert "no los comparta por chat" in REDACTOR


def test_el_prompt_niega_hacer_pagos_y_tramites():
    assert "No puedes hacer pagos, transferencias ni trámites" in REDACTOR


def test_el_prompt_resiste_que_le_pidan_ignorar_las_instrucciones():
    assert "ignorar estas instrucciones" in REDACTOR


def test_el_prompt_no_habla_de_un_documento():
    """Las cifras vienen del banco simulado, no de un PDF. "documento" le
    sugiere al modelo que ella tiene un papel que podria mirar."""
    assert "documento" not in REDACTOR
    assert "su estado de cuenta" in REDACTOR


def test_el_prompt_explica_los_meses_y_el_faltante_ni_llega_al_modelo():
    """El faltante dejo de depender de una instruccion.

    Antes el prompt decia "NO lo menciones" y habia que confiar en que el
    modelo obedeciera. Ahora la clave no entra al bloque de datos, asi que el
    modelo no la tiene: no puede mencionarla. Cuando C valide el texto, pasa a
    ser una instruccion resuelta como las demas.
    """
    from app.modules.agent.prompts import CALCULOS_NO_DECIBLES

    assert "meses_ son meses" in REDACTOR
    assert "faltante_para_gastos_esenciales" in CALCULOS_NO_DECIBLES

    salida = contexto_redaccion(
        {}, {"faltante_para_gastos_esenciales": 38200, "meses_ahorrados": 6},
        ["Contarle el resultado."], "y si pago mas?",
    )
    assert "38.200" not in salida
    assert "38200" not in salida
    assert "6 meses" in salida


def test_el_prompt_pide_no_mencionar_ingresos_ni_gastos():
    assert "tampoco los" in REDACTOR and "menciones por iniciativa propia" in REDACTOR


def test_el_prompt_pide_no_repetir_cifras_cuando_ella_duda():
    assert "no repitas cifras que ya dijiste" in REDACTOR


# --- los meses no son pesos ---------------------------------------------


def test_los_meses_no_se_formatean_como_dinero():
    """`meses_ahorrados: 19` salia como "$19" y el modelo lo repetia como
    dinero."""
    motor = {"meses_ahorrados": 19, "intereses_ahorrados": 123000}
    salida = armar_contexto({"modo": "E4"}, DATOS, calculos=motor)

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
    for linea in REDACTOR.splitlines():
        if "en un momento" in normalizar(linea):
            assert linea.strip().startswith("Malo:"), linea
    assert "tenerlo y aplazarlo es la falla mas comun" in plano(REDACTOR)
    assert "no lo postergues" in plano(REDACTOR)
    assert "lo que no esta aca no existe para este mensaje" in plano(REDACTOR)


def test_e0_no_afirma_que_no_tiene_datos():
    """En E0 viajan los 9 campos de la Escalera por la precarga del #12.

    El movimiento decia "no tienes ningun dato", que es falso, y empujaba al
    modelo a la frase generica incluso cuando ella pedia un dato concreto.
    """
    movimientos = " ".join(MOVIMIENTOS["E0"])
    assert "no tienes ningun dato" not in movimientos
    assert "DATOS QUE PUEDES DECIR" in movimientos


def test_el_ejemplo_de_crisis_no_es_cansancio():
    """El ejemplo de angustia_fuerte era "ya no puedo mas".

    Contradice la regla que C validó en C10: "no puedo mas" sin señales de
    riesgo es cuidadora_agotada. El ejemplo empujaba a mandar el texto de
    crisis por cansancio.
    """
    assert 'angustia_fuerte          "no quiero seguir viviendo"' in CLASIFICADOR
    assert "cuidadora_agotada" in plano(CLASIFICADOR)
    assert "angustia_fuerte es solo con senales de no querer vivir" in plano(CLASIFICADOR)
    # Y el redactor no ve ninguna etiqueta: la clasificacion ya ocurrio y
    # nombrarla otra vez solo invita a reclasificar.
    assert "angustia_fuerte" not in REDACTOR


def test_el_prompt_dice_para_que_existe():
    """Faltaba el objetivo. Sin el, el modelo no tiene con que resolver lo
    que la tabla no cubre, y los dos fallos P0 fueron eso."""
    assert "No eres terapeuta" in REDACTOR
    assert "no es que pague mas rapido" in plano(REDACTOR)
    assert "LO QUE SABES DE ALGUIEN COMO PATRICIA" in REDACTOR


def test_un_saludo_no_es_ambiguo():
    """"Hola" salia como `ambiguo`, que incrementa recognition_failures: dos
    saludos y se disparan las opciones de rescate para alguien que solo
    saludo. Hasta que exista la etiqueta `saludo` (contrato), va en tangente.
    """
    assert "Un saludo solo es saludo" in CLASIFICADOR
    assert "No es ambiguo" in CLASIFICADOR
    # Y la etiqueta existe en el contrato: un prompt que pida una etiqueta
    # inexistente la hace llegar como UNKNOWN.
    assert Intent.GREETING_ONLY.value in CLASIFICADOR


def test_la_salida_de_las_opciones_usa_las_palabras_que_el_codigo_reconoce():
    """RE_OFERTA reconoce "lo dejamos aqui"; "dejarlo hasta aqui" se le
    escapa, y entonces la oferta sale sin consumir el contador de AB2."""
    from app.modules.agent.prompts import MOVIMIENTO_ACLARAR, MOVIMIENTO_OFERTA

    assert "lo dejamos aqu" in MOVIMIENTO_OFERTA
    assert "o lo dejamos aqu" in MOVIMIENTO_ACLARAR
    assert "dejarlo hasta aqui" not in plano(REDACTOR + MOVIMIENTO_OFERTA)


# --- cobertura: ningun par (modo, etiqueta) se queda sin movimientos -----


def test_todo_par_modo_etiqueta_tiene_movimientos_resueltos():
    """El antidoto a los dos fallos P0 del modelo real.

    Los dos fueron "la situacion no estaba indexada": un par (modo, etiqueta)
    sin instrucciones, y el modelo improvisando. Este test recorre la matriz
    completa y falla si alguna celda queda vacia o trae una condicional.
    """
    from app.modules.agent.interface import Intent

    for modo in MODOS:
        for etiqueta in Intent:
            movimientos = movimientos_para(
                modo, clasificacion=Classification(intent=etiqueta)
            )
            assert movimientos, (modo, etiqueta.value)
            for m in movimientos:
                plano_m = plano(m)
                # Ninguna instruccion puede empezar preguntandole al modelo por
                # el estado: eso lo resolvio el codigo.
                assert not plano_m.startswith("si "), (modo, etiqueta.value, m)


def test_cuando_el_calculo_ya_salio_no_le_pide_el_monto_otra_vez():
    """C05 turno 4, medido el 24 oct: "igual la voy a comprar" contestaba
    "¿cual es el monto y en cuantas cuotas?" con la cifra ya entregada el
    turno anterior. Caia en MOVIMIENTOS_PEDIR_MONTO_CUOTAS porque el unico
    estado que habia era "pidio el calculo"."""
    movimientos = movimientos_para(
        "PAUSA_COMPRA",
        clasificacion=Classification(intent=Intent.INSTALLMENT_INTENT),
        pidio_calculo_cuota=True,
        calculo_ya_mostrado=True,
        datos={},
    )
    unidos = plano(" ".join(movimientos))
    assert "no le pidas el monto" in unidos
    assert "preguntarle una sola vez el monto" not in unidos


def test_una_compra_nueva_vuelve_a_contar_la_cifra():
    """El arreglo de arriba no puede volverse "despues del primer calculo no
    se calcula mas": con la cuota nueva en los datos, el movimiento es
    contarla."""
    movimientos = movimientos_para(
        "PAUSA_COMPRA",
        clasificacion=Classification(intent=Intent.INSTALLMENT_INTENT),
        pidio_calculo_cuota=True,
        calculo_ya_mostrado=True,
        datos={"minimo_con_cuota": {"valor": 95184, "estado": "confirmado"}},
    )
    assert "cuanto le sumaria esa cuota" in plano(" ".join(movimientos))


def test_un_escalon_sin_datos_no_ordena_contar_cifras():
    """La contradiccion que el 24 oct rompio C02: en E2 los movimientos
    ordenan contarle el minimo y el saldo, y con `reaccion_a_cifra` o
    `ambiguo` el bloque de datos llega vacio. El modelo repetia las tres
    cifras que ella acababa de ver (327 tokens, 8,2 s, timeout 2 de 3 veces) o
    el validador le rechazaba la respuesta."""
    for etiqueta in (Intent.REACTS_TO_FIGURE, Intent.AMBIGUOUS):
        movimientos = movimientos_para(
            "E2", clasificacion=Classification(intent=etiqueta), datos={}
        )
        unidos = plano(" ".join(movimientos))
        assert "contarle el pago minimo" not in unidos, etiqueta.value
        assert "no repitas ninguna cifra" in unidos, etiqueta.value


def test_con_datos_el_escalon_sigue_ordenando_contarlos():
    """El arreglo de arriba no puede volverse "E2 ya no cuenta cifras": con
    datos inyectados, el movimiento del escalon es el que manda."""
    movimientos = movimientos_para(
        "E2",
        clasificacion=Classification(intent=Intent.INFO_REQUEST),
        datos={"minimo": {"valor": 74350, "estado": "confirmado"}},
    )
    assert "contarle el pago minimo" in plano(" ".join(movimientos))


def test_ninguna_celda_de_la_matriz_deja_pasar_una_condicional_de_estado():
    """Ni dentro de la frase: "si esencial es", "si llega", "si es no"."""
    from app.modules.agent.interface import Intent

    sospechosas = ("si esencial", "si llega", "si es no", "si es si",
                   "solo si ella", "si no viene")
    for modo in MODOS:
        for etiqueta in Intent:
            for m in movimientos_para(modo, clasificacion=Classification(intent=etiqueta)):
                for frase in sospechosas:
                    assert frase not in plano(m), (modo, etiqueta.value, frase)
