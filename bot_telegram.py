"""
bot_telegram.py
----------------
Bot de Telegram con COMANDOS (/fondo, /corte, /encuestas, /operaciones, /solicitudes,
/diferencias, /ayuda, /id). Corre como un proceso aparte, al lado de la app de Streamlit
(lo arranca docker-entrypoint.sh) y escucha a Telegram con "long polling": no necesita una
direccion publica ni configurar nada mas que el token.

SEGURIDAD: solo responde con datos a los chats que estan en TELEGRAM_CHAT_ID (los mismos que
reciben las alertas). A cualquier otro solo le dice cual es su chat id, para que te lo pase y
lo agregues. Los reportes salen de los mismos datos y calculos del Dashboard (reportes.py).

Para probarlo en tu computadora:  python bot_telegram.py   (con TELEGRAM_BOT_TOKEN y
TELEGRAM_CHAT_ID definidos y el secrets.toml de siempre).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Este proceso no corre dentro de Streamlit: se silencian sus avisos de "no hay runtime".
for _nombre in ("streamlit", "streamlit.runtime.caching.cache_data_api", "streamlit.runtime.scriptrunner_utils.script_run_context"):
    logging.getLogger(_nombre).setLevel(logging.ERROR)

import reportes as rp  # noqa: E402
import sheets_utils as sh  # noqa: E402

COMANDOS_MENU = [
    ("fondo", "Efectivo, tarjeta y total actual de cada local"),
    ("corte", "Último corte cerrado de cada local"),
    ("encuestas", "Encuestas del mes, pagos y nota NPS"),
    ("operaciones", "Operaciones de hoy y del mes vs meta"),
    ("solicitudes", "Pedidos de efectivo o saldo pendientes"),
    ("diferencias", "Diferencias grandes sin autorizar"),
    ("ayuda", "Ver todos los comandos"),
    ("id", "Ver tu chat id"),
]
MENSAJES_MAS_VIEJOS_QUE = 300  # segundos: al reiniciar no se contestan mensajes antiguos


def log(texto: str) -> None:
    print(f"[bot] {texto}", flush=True)


# ---------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------
def llamar(token: str, metodo: str, datos: dict | None = None, timeout: int = 15):
    """Llama a la API de Telegram. Devuelve (resultado, error). El texto del error NUNCA lleva
    el token (la URL si lo lleva, por eso no se imprime la excepcion tal cual)."""
    try:
        cuerpo = urllib.parse.urlencode(datos or {}).encode()
        pedido = urllib.request.Request(f"https://api.telegram.org/bot{token}/{metodo}", data=cuerpo)
        with urllib.request.urlopen(pedido, timeout=timeout) as respuesta:
            return json.loads(respuesta.read().decode("utf-8")).get("result"), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, f"{type(e).__name__}"


def enviar(token: str, chat_id, texto: str) -> None:
    """Manda el texto (partido si es largo). Si Telegram rechaza el HTML, reintenta sin formato."""
    for parte in rp.partir_mensaje(texto):
        _, error = llamar(
            token, "sendMessage",
            {"chat_id": chat_id, "text": parte, "parse_mode": "HTML", "disable_web_page_preview": "true"},
        )
        if error == "HTTP 400":  # etiquetas mal formadas: se manda en texto plano
            llamar(token, "sendMessage", {"chat_id": chat_id, "text": re.sub(r"<[^>]+>", "", parte)})
        elif error:
            log(f"no se pudo enviar a {chat_id}: {error}")


# ---------------------------------------------------------------------
# Datos y respuestas
# ---------------------------------------------------------------------
def _fresco(*lectores):
    """Lee las hojas SIN el cache (un reporte tiene que reflejar lo ultimo guardado)."""
    salida = []
    for lector in lectores:
        lector.clear()
        salida.append(lector())
    return salida if len(salida) > 1 else salida[0]


def generar_respuesta(comando: str, argumento: str) -> str:
    hoy = sh.hoy_local()
    if comando in ("start", "ayuda", "help"):
        return rp.AYUDA.format(umbral=rp.UMBRAL_ALERTA_DIFERENCIA)
    if comando == "fondo":
        registros, config = _fresco(sh.get_registros_df, sh.get_config_df)
        return rp.reporte_fondo(registros, config, hoy, argumento)
    if comando == "corte":
        registros, ajustes = _fresco(sh.get_registros_df, sh.get_ajustes_df)
        return rp.reporte_corte(registros, argumento, ajustes)
    if comando == "encuestas":
        encuestas, notas = _fresco(sh.get_encuestas_df, sh.get_notas_df)
        return rp.reporte_encuestas(encuestas, notas, hoy, argumento)
    if comando == "operaciones":
        registros, config = _fresco(sh.get_registros_df, sh.get_config_df)
        return rp.reporte_operaciones(registros, config, hoy, argumento)
    if comando == "solicitudes":
        return rp.reporte_solicitudes(_fresco(sh.get_solicitudes_df), sh.ahora_local().replace(tzinfo=None))
    if comando == "diferencias":
        dias, filtro, todas = 7, [], False
        for p in argumento.split():
            if p.isdigit():
                dias = int(p)
            elif p.lower() in ("todas", "todo", "todos"):
                todas = True  # incluye tambien las que ya estan autorizadas
            else:
                filtro.append(p)
        registros, ajustes = _fresco(sh.get_registros_df, sh.get_ajustes_df)
        return rp.reporte_diferencias(
            registros, hoy, dias, sh.umbral_alerta_diferencia(), " ".join(filtro), ajustes, todas
        )
    return "No conozco ese comando. Escribe /ayuda para ver los que hay."


def procesar_mensaje(chat_id, texto: str, autorizados: list[str]) -> str | None:
    """Que responder a un mensaje (None = no responder). Funcion pura respecto de Telegram:
    la usan el bot y las pruebas."""
    texto = (texto or "").strip()
    es_comando = texto.startswith("/")
    if not es_comando:
        return "Escribe /ayuda para ver los comandos." if str(chat_id) in autorizados else None
    cabeza, _, argumento = texto.partition(" ")
    comando = cabeza[1:].split("@")[0].lower()
    if comando == "id" or str(chat_id) not in autorizados:
        base = f"🆔 Tu chat id es <code>{chat_id}</code>."
        if str(chat_id) in autorizados:
            return base + "\nEste chat sí está autorizado para recibir alertas y usar los comandos."
        return base + "\nPásaselo al administrador para que lo agregue y puedas usar el bot."
    try:
        return generar_respuesta(comando, argumento.strip())
    except Exception as e:
        log(f"error en /{comando}: {type(e).__name__}: {e}")
        return "⚠️ No pude leer los datos ahora (Google puede estar ocupado). Intenta de nuevo en un minuto."


def atender(token: str, update: dict) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg or "chat" not in msg:
        return
    if time.time() - float(msg.get("date", 0)) > MENSAJES_MAS_VIEJOS_QUE:
        return
    chat_id = msg["chat"]["id"]
    respuesta = procesar_mensaje(chat_id, msg.get("text", ""), sh.telegram_chat_ids())
    if respuesta:
        enviar(token, chat_id, respuesta)


# ---------------------------------------------------------------------
# Bucle principal
# ---------------------------------------------------------------------
def main() -> None:
    token = ""
    while not token:
        token = sh._secreto("telegram_token", "TELEGRAM_BOT_TOKEN")
        if not token:
            time.sleep(60)  # sin token no hay bot: se queda esperando a que lo configures
    llamar(token, "deleteWebhook")  # por si algun dia hubo uno: con webhook no se puede usar getUpdates
    llamar(token, "setMyCommands", {"commands": json.dumps([{"command": c, "description": d} for c, d in COMANDOS_MENU])})
    log("listo, escuchando comandos")
    offset = None
    while True:
        datos = {"timeout": 30, "allowed_updates": json.dumps(["message", "edited_message"])}
        if offset:
            datos["offset"] = offset
        actualizaciones, error = llamar(token, "getUpdates", datos, timeout=45)
        if error:
            log(f"getUpdates: {error}")
            time.sleep(15 if error == "HTTP 409" else 5)
            continue
        for upd in actualizaciones or []:
            offset = upd["update_id"] + 1
            try:
                atender(token, upd)
            except Exception as e:
                log(f"error atendiendo un mensaje: {type(e).__name__}: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
