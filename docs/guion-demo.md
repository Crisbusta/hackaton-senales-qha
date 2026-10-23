# Guion de la demo · Contigo

Duración sugerida: 4–5 minutos de WhatsApp en vivo. Datos 100 % ficticios (Banco Austral,
ficticio). Producción: `https://api-production-0f5fb.up.railway.app`.

## Antes de presentar (10 minutos antes)

1. **No mergear nada a `main`** desde ahora hasta terminar: cada merge redespliega y borra las
   conversaciones en memoria. Si alguien necesita mergear, desactivar el auto-deploy en
   Railway (servicio `api` → Settings → *Source*).
2. **Salud del servicio:** `curl https://api-production-0f5fb.up.railway.app/health` →
   `{"status":"ok","service":"contigo"}`.
3. **Teléfono de la demo:** que su número esté en `DEMO_ADMIN_NUMBERS` (Railway → Variables) y
   escribir **`/reiniciar`** → "Listo, la conversación empieza de cero. (modo demo)".
4. **Sandbox de WhatsApp:** si pasaron 3 días desde el `join`, volver a enviarlo.
5. **Ensayo corto:** enviar "Hola", ver que responde y volver a escribir `/reiniciar`.
6. **Pantalla:** WhatsApp proyectado (WhatsApp Web o espejo del teléfono). En una segunda
   pestaña, los logs de Railway (`railway logs --service api`) por si hay que explicar algo.

## Secuencia

Cada paso muestra un principio del producto. Enviar los mensajes **tal cual**, esperando la
respuesta antes del siguiente (el modelo tarda ~4–5 s).

| # | Patricia escribe | Qué debería pasar | Qué contar al jurado |
|---|---|---|---|
| 1 | `Hola` | Saluda sin cifras y ofrece mirar el estado de cuenta por ella, de a un dato | **Exposición progresiva:** no abre con la deuda |
| 2 | `Quiero ver lo de la tarjeta, pero poquito` | Propone una sola cosa, sin presión | **Su ritmo** |
| 3 | `Veamos` | "Vence el 3 de noviembre", y ofrece seguir con salida | Escalón E1: un dato por vez |
| 4 | `Sigamos` | Pago mínimo $74.350 y saldo $1.064.000 | Cifras **reales del banco**, nunca inventadas por el LLM |
| 5 | `¿Y si pago 100 mil?` | 13 meses frente a 19 pagando solo el mínimo y $97.662 menos de intereses, aclarando que es ilustrativo | **Motor financiero determinista:** el LLM solo redacta |
| 6 | `Me dan ganas de comprar en cuotas` | Ofrece esperar 10 minutos o ver cuánto le sumaría al mínimo, sin sermón | **Pausa amable** antes de una compra por estrés |
| 7 | `La segunda` | Pregunta monto y cuotas *(⚠️ depende del #19)* | Ella elige |
| 8 | `$60.000 en 6 cuotas` | "$10.000 al mes; tu mínimo pasaría de $74.350 a $84.350", ilustrativo *(⚠️ depende del #19)* | Cálculo exacto (caso C05) |
| 9 | `Prefiero no verlo más por hoy` | Texto de pausa, sin cifras ni preguntas | **Respeta el "no"** |
| 10 | `Hola, ya estoy mejor` | Bienvenida sin mencionar el rechazo | Vuelve cuando quiere, sin culpa |

**Opcional, solo si el tiempo y el tono lo permiten:** contar (sin enviarlo en vivo) que ante
señales de crisis ("no quiero seguir viviendo") el sistema responde en ~1 ms, **sin llamar al
modelo**, con un texto validado que ofrece \*4141 y 131. Se puede mostrar el resultado del caso
C10 en el reporte P0 en vez de escribirlo en el teléfono.

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| No responde en ~15 s | Twilio corta a los 15 s. Reenviar el mensaje. Si se repite, revisar `railway logs`: si no aparece `POST /webhooks/whatsapp`, el problema es Twilio (Monitor → Logs → Errors) |
| Responde "Perdona, tuve un problema…" | El agente falló o se agotó el tiempo. Reenviar el mensaje. El sistema no se cae: siempre responde algo |
| La conversación "se olvidó" | Hubo un redeploy. Escribir `/reiniciar` y retomar desde el paso 1 |
| Twilio o WhatsApp caídos | **Plan B:** mostrar el reporte del runner P0 (`uv run python -m scripts.p0_runner --agent llm --report resultados.md`), que tiene las respuestas reales del modelo para cada caso. Generarlo **antes** de presentar y tenerlo abierto |
| Internet caído | Plan B local: `uv run python -m scripts.p0_runner --agent mock` funciona sin red (respuestas simples, pero muestra el flujo, las cifras del banco y las reglas) |

## Mensajes clave para el pitch

- **El LLM nunca calcula ni inventa cifras.** Las cifras salen del banco y del motor financiero;
  el backend verifica cada respuesta antes de enviarla.
- **Respeta el "no".** Después de un rechazo no viaja ninguna cifra al modelo.
- **Consentimiento fuera del chat**, como en Open Finance. El banco es simulado; la arquitectura
  está lista para un proveedor regulado.
- **Seguro por defecto:** firma de Twilio validada, teléfonos enmascarados en los logs, sin
  mensajes completos en los logs de producción.
