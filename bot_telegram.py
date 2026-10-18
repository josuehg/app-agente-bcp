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

import calendar
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
    ("resumen", "El resumen del día completo"),
    ("ranking", "Ranking de trabajadores por operaciones"),
    ("sucursales", "Balance de diferencias por sucursal"),
    ("liquidacion", "Liquidación de los trabajadores"),
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
    if comando == "ranking":
        periodo, filtro = "mes", []
        for p in argumento.split():
            if p.lower() in rp.PERIODOS_RANKING:
                periodo = p.lower()
            else:
                filtro.append(p)
        registros, config, encuestas, ajustes = _fresco(
            sh.get_registros_df, sh.get_config_df, sh.get_encuestas_df, sh.get_ajustes_df
        )
        return rp.reporte_ranking(registros, config, encuestas, ajustes, hoy, periodo, " ".join(filtro))
    if comando == "liquidacion":
        periodo, filtro = "mes", []
        for p in argumento.split():
            if p.lower() in rp.PERIODOS_RANKING:
                periodo = p.lower()
            else:
                filtro.append(p)
        registros, config, encuestas, notas, ajustes, personal = _fresco(
            sh.get_registros_df, sh.get_config_df, sh.get_encuestas_df, sh.get_notas_df, sh.get_ajustes_df,
            sh.get_personal_df,
        )
        return rp.reporte_liquidacion(
            registros, config, encuestas, notas, ajustes, personal, hoy, periodo, " ".join(filtro)
        )
    if comando == "sucursales":
        periodo, filtro = "mes", []
        for p in argumento.split():
            if p.lower() in rp.PERIODOS_RANKING:
                periodo = p.lower()
            else:
                filtro.append(p)
        registros, config, ajustes = _fresco(sh.get_registros_df, sh.get_config_df, sh.get_ajustes_df)
        return rp.reporte_sucursales(registros, config, ajustes, hoy, periodo, " ".join(filtro))
    if comando == "resumen":
        registros, config, encuestas, ajustes, solicitudes = _fresco(
            sh.get_registros_df, sh.get_config_df, sh.get_encuestas_df, sh.get_ajustes_df, sh.get_solicitudes_df
        )
        return rp.resumen_diario(
            registros, config, encuestas, ajustes, solicitudes, hoy, sh.ahora_local().replace(tzinfo=None),
            sh.umbral_alerta_diferencia(),
        )
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
# Avisos programados: resumen de cada noche y turnos sin cerrar
# ---------------------------------------------------------------------
REVISION_TURNOS_CADA = 900  # segundos (15 min)


def enviar_a_todos(token: str, texto: str) -> None:
    for chat in sh.telegram_chat_ids():
        enviar(token, chat, texto)


def hora_resumen() -> int | None:
    """Minutos desde medianoche a los que se manda el resumen (RESUMEN_DIARIO_HORA, 'HH:MM';
    'off' lo apaga). Por defecto 22:00."""
    valor = (sh._secreto("resumen_diario_hora", "RESUMEN_DIARIO_HORA") or "22:00").strip().lower()
    if valor in ("off", "no", "0", "apagado"):
        return None
    try:
        h, m = valor.split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return 22 * 60


def dia_liquidacion() -> int | None:
    """Dia del mes en que se manda la liquidacion de todos (LIQUIDACION_DIA; 30 por defecto; 'off' la
    apaga). En un mes mas corto (febrero) sale el ultimo dia."""
    valor = (sh._secreto("liquidacion_dia", "LIQUIDACION_DIA") or "30").strip().lower()
    if valor in ("off", "no", "0", "apagado"):
        return None
    try:
        return min(max(int(valor), 1), 31)
    except ValueError:
        return 30


def hora_liquidacion() -> int:
    """Minutos desde medianoche (LIQUIDACION_HORA, 'HH:MM'; 21:30 por defecto)."""
    valor = (sh._secreto("liquidacion_hora", "LIQUIDACION_HORA") or "21:30").strip()
    try:
        h, m = valor.split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return 21 * 60 + 30


def horas_turno_abierto() -> float:
    """Horas con la Apertura sin Cierre desde las que se avisa (ALERTA_TURNO_ABIERTO_HORAS; 8.5, igual
    que el Dashboard)."""
    try:
        return float(sh._secreto("alerta_turno_abierto_horas", "ALERTA_TURNO_ABIERTO_HORAS") or 8.5)
    except ValueError:
        return 8.5


class Programador:
    """Revisa cada vuelta del bot si toca mandar el resumen de la noche o avisar de un turno sin
    cerrar. Lo ya enviado se guarda en la hoja 'Bot' para que un reinicio no repita avisos."""

    def __init__(self, token: str):
        self.token = token
        self.estado: dict | None = None
        self.proxima_revision_turnos = 0.0
        self.proximo_intento_resumen = 0.0
        self.proximo_intento_liquidacion = 0.0

    def _cargar(self) -> None:
        try:
            self.estado = sh.leer_estado_bot()
        except Exception as e:
            log(f"no se pudo leer el estado del bot ({type(e).__name__}); se reintenta")
            self.estado = None

    def tick(self, ahora=None) -> None:
        if not sh.telegram_chat_ids():
            return
        if self.estado is None:
            self._cargar()
            if self.estado is None:
                return
        ahora = ahora or sh.ahora_local().replace(tzinfo=None)
        self._resumen(ahora)
        self._liquidacion(ahora)
        self._turnos(ahora)

    def _resumen(self, ahora) -> None:
        minutos = hora_resumen()
        hoy = ahora.date().isoformat()
        if minutos is None or ahora.hour * 60 + ahora.minute < minutos:
            return
        if hoy in self.estado.get("ultimo_resumen", []) or time.time() < self.proximo_intento_resumen:
            return
        try:
            enviar_a_todos(self.token, generar_respuesta("resumen", ""))
            sh.guardar_estado_bot("ultimo_resumen", hoy, unico=True)
            self.estado["ultimo_resumen"] = [hoy]
            log("resumen del día enviado")
        except Exception as e:
            log(f"resumen del día: {type(e).__name__}: {e}")
            self.proximo_intento_resumen = time.time() + 600

    def _liquidacion(self, ahora) -> None:
        """La liquidacion de todos los trabajadores, una vez al mes (el dia 30 a las 21:30). Si el bot
        estaba apagado ese dia, sale en cuanto vuelve, siempre dentro del mismo mes."""
        dia = dia_liquidacion()
        if dia is None:
            return
        objetivo = min(dia, calendar.monthrange(ahora.year, ahora.month)[1])
        if ahora.day < objetivo or (ahora.day == objetivo and ahora.hour * 60 + ahora.minute < hora_liquidacion()):
            return
        mes = ahora.strftime("%Y-%m")
        if mes in self.estado.get("ultima_liquidacion", []) or time.time() < self.proximo_intento_liquidacion:
            return
        try:
            enviar_a_todos(self.token, generar_respuesta("liquidacion", ""))
            sh.guardar_estado_bot("ultima_liquidacion", mes, unico=True)
            self.estado["ultima_liquidacion"] = [mes]
            log("liquidación del mes enviada")
        except Exception as e:
            log(f"liquidación del mes: {type(e).__name__}: {e}")
            self.proximo_intento_liquidacion = time.time() + 600

    def _turnos(self, ahora) -> None:
        if time.time() < self.proxima_revision_turnos:
            return
        self.proxima_revision_turnos = time.time() + REVISION_TURNOS_CADA
        try:
            avisados = set(self.estado.get("turno_avisado", []))
            registros = _fresco(sh.get_registros_df)
            for t in rp.turnos_sin_cerrar(registros, ahora, horas_turno_abierto()):
                if t["id"] in avisados:
                    continue
                enviar_a_todos(self.token, rp.texto_turno_sin_cerrar(t))
                sh.guardar_estado_bot("turno_avisado", t["id"], unico=False)
                self.estado.setdefault("turno_avisado", []).append(t["id"])
                log(f"aviso de turno sin cerrar: {t['local']} {t['turno']} {t['fecha']}")
        except Exception as e:
            log(f"revisión de turnos: {type(e).__name__}: {e}")


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
    programador = Programador(token)
    while True:
        try:
            programador.tick()
        except Exception as e:
            log(f"avisos programados: {type(e).__name__}: {e}")
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
