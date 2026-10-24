# Medición del "después" (paso 7, parcial)

**Estado del paso 7: parcial.** El "antes" original (52 turnos clasificados a mano, 40 % problemáticos)
sigue sin estar en el repo: el PDF "Respuestas vagas de Contigo" no se encontró en la máquina donde
se retomó el trabajo. Lo que hay en `docs/diagnostico_contigo.html` es el diagnóstico del código (hallazgos 1 a 10),
no los 52 turnos. Por eso **este documento no se compara con el 40 %**. Mide otra cosa, con un método
que sí se puede repetir.

## Qué se midió

Los mismos casos P0 (`docs/casos_p0.md`, sin cambios entre los dos commits) corridos contra el modelo
real, con la traza encendida, en dos versiones del código:

| | Commit | Turno |
|---|---|---|
| Antes | `04c50a9` (#31) | una sola llamada al modelo |
| Después | `0d3e93f` (main) | clasificar → decidir → redactar |

```bash
AGENT_TRACE_ENABLED=true AGENT_TRACE_PATH=turnos.jsonl \
  uv run python -m scripts.p0_runner --agent llm --report resultados_<antes|despues>.md
```

Resultados completos: [`resultados_antes.md`](resultados_antes.md) y [`resultados_despues.md`](resultados_despues.md).
Una sola corrida por versión, 21 turnos de texto cada una (los dos pasos con PDF del runner se
omiten).

## Resultado automático (el del runner)

| | Casos sin fallas | Filas con falla | Turnos que salieron con texto de respaldo |
|---|---|---|---|
| Antes | 5 de 6 | 1 (C02 paso 3: faltó el mínimo de $74.350) | 1 (C05 paso 1, `respaldo_PAUSA_COMPRA_lista_blanca_numerica`) |
| Después | 6 de 6 | 0 | 0 |

## Clasificación manual, con el criterio original

Un turno cuenta como problemático si posterga un dato que sí estaba en el contexto, repite una cifra
que ella ya vio, ofrece lo mismo dos veces, o responde con una pregunta en vez del dato que pidió.
Un texto de respaldo cuenta como problemático.

| Turno | Antes | Después |
|---|---|---|
| C01 paso 4 ("Hola, ya estoy mejor") | repite la oferta de mirar el estado de cuenta | ✅ |
| C02 paso 1 ("Quiero ver lo de la tarjeta, pero poquito") | posterga: no da ni la fecha | ✅ da la fecha |
| C02 paso 3 ("Veamos") | posterga: da la fecha y no el mínimo | repite la fecha que ya dio (ver abajo) |
| C02 paso 4 ("Más alto de lo que pensaba") | repite la oferta "¿Seguimos o lo dejamos aquí?" | ✅ |
| C05 paso 1 ("Me dan ganas de comprar en cuotas") | texto de respaldo | ✅ ofrece las dos opciones |
| C05 paso 4 ("Igual lo voy a comprar") | vuelve a pedir el monto y las cuotas que ya dio | ✅ |
| C06 paso c ("Mejor no, dejémoslo") | repite el texto de pausa del paso b | repite el texto de pausa del paso b |
| **Total** | **7 de 21 (33 %)** | **2 de 21 (10 %)** |

## Cómo leer este número

- **No es comparable con el 40 %.** Son otros turnos, otro tamaño (21 contra 52) y los clasifiqué yo
  en una sola pasada, sin segunda persona. Que baje de 33 % a 10 % sobre los mismos 21 turnos es una
  señal, no una prueba.
- **Una corrida por versión.** El modelo no es determinista: con una segunda corrida los números
  pueden moverse unos turnos.
- **Los casos P0 no son una muestra de conversaciones reales.** Están escritos para pasar por los
  escalones y por las pausas, no para reproducir cómo escribe Patricia.
- Para cerrar el paso 7 de verdad faltan los 52 turnos originales y repetir la clasificación sobre
  ellos con la traza encendida.

## Defecto nuevo que salió de esta medición

**Al subir a E2, el redactor repite la fecha.** En C02 paso 3 ("Veamos") la traza muestra:

- `campos` inyectados: `["minimo","saldo","vencimiento"]`
- movimiento: "Contarle el pago minimo y el saldo adeudado, en 1 a 3 frases."
- salida: "Sí, veamos: el pago mínimo es $74.350 y el saldo adeudado es $1.064.000. **Tu tarjeta vence el
  3 de noviembre de 2026.** ¿Seguimos o lo dejamos aquí?"

La causa está en el código, no en el prompt: `FIELDS_BY_MODE` es acumulativo, así que E2 recibe también
`vencimiento`, que ella ya vio en E1, y el modelo lo cuenta aunque el movimiento no lo pida. Es la
misma raíz que la limitación "Al salir de una pausa, el turno llega con todos los campos del escalón
más alto" de `CLAUDE.md`: el contrato no distingue "entrar al escalón" de "volver a él".

**No se arregló.** `FIELDS_BY_MODE` vive en `agent/interface.py` (contrato compartido con B) y qué
debe decir ese turno es una decisión de C. Cuando se decida, el arreglo lleva un test con esta
medición en el docstring.
