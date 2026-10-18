"""
reportes.py
------------
Los reportes que el bot de Telegram manda cuando le escriben un comando (/fondo, /corte,
/encuestas, /operaciones, /solicitudes, /diferencias).

Igual que cuadre.py, NO importa streamlit ni gspread: recibe DataFrames ya leidos (los mismos
que usa el Dashboard) y devuelve texto en HTML de Telegram (solo <b>, <i> y <code>). Asi se
puede probar con pytest sin credenciales. El texto escrito por personas (nombres,
observaciones) siempre se escapa.
"""

from __future__ import annotations

import html
import unicodedata
from datetime import date, timedelta

import pandas as pd

from cuadre import (
    ESTADO_NO_SE_ABONA,
    ESTADO_PENDIENTE_PAGO,
    UMBRAL_ALERTA_DIFERENCIA,
    UMBRAL_VERDE,
    calcular_cortes,
    calcular_saltos,
    con_estado_nota,
    estado_nota,
    estado_parcial,
    marcar_autorizados,
    minutos_entre,
    nota_vigente,
    operaciones_por_turno,
    solicitudes_activas,
    texto_espera,
)

_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

AYUDA = (
    "🤖 <b>Bot del Agente BCP</b>\n"
    "Escríbeme un comando y te respondo al instante.\n"
    "👉 Toca cualquier comando azul para usarlo.\n"
    "\n"
    "📊 <b>¿Cómo van los locales?</b>\n"
    "/fondo\n"
    "   💵 Cuánto hay en efectivo, tarjeta y total en cada local\n"
    "/corte\n"
    "   ✂️ El último corte cerrado: Apertura, Cierre, diferencia y operaciones\n"
    "/operaciones\n"
    "   📈 Operaciones de hoy y del mes, y si se cumplió la meta\n"
    "/resumen\n"
    "   🌙 El resumen del día completo (el mismo que te mando solo cada noche)\n"
    "\n"
    "🏆 <b>Equipo</b>\n"
    "/ranking\n"
    "   🏅 Ranking de trabajadores por operaciones, con turnos, meta, encuestas y diferencias\n"
    "\n"
    "⚠️ <b>¿Hay algo que revisar?</b>\n"
    "/diferencias\n"
    "   🔎 Diferencias de más de S/ {umbral:g} que todavía NO están autorizadas (últimos 7 días)\n"
    "/solicitudes\n"
    "   📨 Pedidos de efectivo o saldo que están esperando\n"
    "\n"
    "⭐ <b>Encuestas</b>\n"
    "/encuestas\n"
    "   📋 Encuestas del mes, cuántas se pagaron y la nota NPS de cada local\n"
    "\n"
    "💡 <b>Trucos</b>\n"
    "• Para ver un solo local, agrega su nombre (toca el ejemplo para copiarlo):\n"
    "   <code>/corte colon</code>\n"
    "   <code>/fondo zol</code>\n"
    "• Para ver más días:\n"
    "   <code>/diferencias 30</code>\n"
    "• Para incluir también las ya autorizadas:\n"
    "   <code>/diferencias todas</code>\n"
    "• Ranking de otro período (hoy, semana, mes o anterior):\n"
    "   <code>/ranking semana</code>\n"
    "   <code>/ranking anterior colon</code>\n"
    "\n"
    "🔔 Además te aviso solo: diferencias grandes, pedidos nuevos, turnos sin cerrar y un resumen cada noche.\n"
    "🆔 /id — tu número de chat"
)


# ---------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------
def esc(texto) -> str:
    return html.escape(str(texto if texto is not None else ""), quote=False)


def soles(x) -> str:
    return f"S/ {float(x):,.2f}"


def _num(x, defecto: float = 0.0) -> float:
    v = pd.to_numeric(x, errors="coerce")
    return defecto if pd.isna(v) else float(v)


def _quitar_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t)) if unicodedata.category(c) != "Mn")


def _clave(t: str) -> str:
    return "".join(c for c in _quitar_acentos(t).lower() if c.isalnum())


def buscar_locales(texto: str, locales) -> list[str]:
    """Locales cuyo nombre contiene el texto (sin importar mayusculas ni acentos). Texto vacio
    = todos."""
    locales = sorted({str(x) for x in locales})
    clave = _clave(texto)
    if not clave:
        return locales
    return [x for x in locales if clave in _clave(x)]


def _hhmm(ts) -> str:
    t = pd.to_datetime(ts, errors="coerce")
    return "" if pd.isna(t) else t.strftime("%H:%M")


def _dm(f) -> str:
    try:
        return f"{f:%d/%m}"
    except (TypeError, ValueError):
        return str(f)


def partir_mensaje(texto: str, limite: int = 3800) -> list[str]:
    """Telegram acepta ~4096 caracteres por mensaje: se parte en los saltos de linea."""
    if len(texto) <= limite:
        return [texto]
    partes, actual = [], ""
    for linea in texto.split("\n"):
        while len(linea) > limite:  # una sola linea gigante: se corta a la fuerza
            if actual:
                partes.append(actual)
                actual = ""
            partes.append(linea[:limite])
            linea = linea[limite:]
        if len(actual) + len(linea) + 1 > limite:
            partes.append(actual)
            actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        partes.append(actual)
    return partes


def _con_signo(x) -> str:
    """+12.50 / -20.00; un cero (o casi) se muestra como 0.00."""
    x = float(x)
    return "0.00" if abs(x) < 0.005 else f"{x:+,.2f}"


def _montos(ef, ta, total) -> list[str]:
    """Cada monto en su propia linea (se lee mejor en el celular)."""
    return [f"💵 Efectivo: {soles(ef)}", f"💳 Tarjeta: {soles(ta)}", f"💰 Total: {soles(total)}"]


# ---------------------------------------------------------------------
# /fondo: lo mismo que "Alertas de fondo" del Dashboard
# ---------------------------------------------------------------------
def reporte_fondo(registros: pd.DataFrame, config: pd.DataFrame, hoy: date, local_filtro: str = "") -> str:
    if registros is None or registros.empty:
        return "Todavía no hay registros."
    minimos = {}
    if config is not None and not config.empty:
        minimos = {str(r["local"]): _num(r["fondo_minimo"]) for _, r in config.iterrows()}
    ultimos = registros.sort_values("timestamp").groupby("local").tail(1).set_index("local")
    elegidos = buscar_locales(local_filtro, ultimos.index)
    if not elegidos:
        return f"No encontré ningún local que se parezca a «{esc(local_filtro)}»."

    filas = []
    for local in elegidos:
        r = ultimos.loc[local]
        total = _num(r["total"])
        minimo = minimos.get(local, 0.0)
        bajo = minimo > 0 and total < minimo
        cuando = f"{r['tipo']} {_hhmm(r['timestamp'])}" + ("" if r["fecha"] == hoy else f" del {_dm(r['fecha'])}")
        lineas = [
            f"{'🔴' if bajo else '✅'} <b>{esc(local)}</b>",
            f"🕒 {esc(cuando)} · {esc(r['nombre'] or '—')}",
            *_montos(_num(r["efectivo"]), _num(r["tarjeta"]), total),
        ]
        if minimo > 0:
            lineas.append(f"⚠️ Mínimo {soles(minimo)} — BAJO EL MÍNIMO" if bajo else f"✔️ Mínimo {soles(minimo)}")
        filas.append((not bajo, local, "\n".join(lineas), total))
    filas.sort(key=lambda x: (x[0], x[1]))  # los que estan bajo el minimo, primero
    cuerpo = "\n\n".join(f[2] for f in filas)
    pie = f"\n\n💰 <b>Total de {len(filas)} locales:</b> {soles(sum(f[3] for f in filas))}" if len(filas) > 1 else ""
    return f"🏦 <b>Fondo actual</b>\n(último registro de cada local)\n\n{cuerpo}{pie}"


# ---------------------------------------------------------------------
# /corte: ultimo corte cerrado de cada local
# ---------------------------------------------------------------------
def reporte_corte(registros: pd.DataFrame, local_filtro: str = "", ajustes: pd.DataFrame | None = None) -> str:
    if registros is None or registros.empty:
        return "Todavía no hay registros."
    elegidos = buscar_locales(local_filtro, registros["local"].unique())
    if not elegidos:
        return f"No encontré ningún local que se parezca a «{esc(local_filtro)}»."

    por_id = registros.assign(_id=registros["id"].astype(str)).drop_duplicates("_id", keep="last").set_index("_id")
    bloques = []
    for local in elegidos:
        sub = registros[registros["local"] == local]
        cortes, _ = marcar_autorizados(calcular_cortes(sub, ["local", "fecha", "turno"]), None, ajustes)
        cerrados = cortes[cortes["id_cierre"].astype(str).isin(por_id.index) & cortes["cierre"].notna()].copy() if not cortes.empty else cortes
        if cerrados is None or cerrados.empty:
            bloques.append(f"✂️ <b>{esc(local)}</b>\nTodavía no tiene cortes cerrados.")
            continue
        cerrados["_ts"] = cerrados["id_cierre"].astype(str).map(por_id["timestamp"])
        c = cerrados.sort_values("_ts").iloc[-1]
        ci = por_id.loc[str(c["id_cierre"])]
        ap = por_id.loc[str(c["id_apertura"])] if str(c["id_apertura"]) in por_id.index else None
        dif = c["diferencia"]
        lineas = [
            f"✂️ <b>{esc(local)}</b> — {esc(c['turno'])}, {_dm(c['fecha'])}",
            f"👤 Abrió: {esc(c['nombre'] or '—')}",
            f"👤 Cerró: {esc(c['nombre_cierre'] or '—')}",
        ]
        if ap is not None:
            lineas += ["", f"🔓 <b>Apertura</b> {esc(c['hora_apertura'])}",
                       *_montos(_num(ap["efectivo"]), _num(ap["tarjeta"]), _num(ap["total"]))]
        lineas += ["", f"🔒 <b>Cierre</b> {esc(c['hora_cierre'])}",
                   *_montos(_num(ci["efectivo"]), _num(ci["tarjeta"]), _num(ci["total"])), ""]
        if pd.notna(dif):
            if bool(c["_autorizado"]):
                lineas.append(f"📊 Diferencia: {_con_signo(dif)} 🔷 Autorizada (ajuste {_con_signo(c['_ajuste'])})")
            else:
                icono = "✅" if abs(float(dif)) <= UMBRAL_VERDE else "🔴"
                lineas.append(f"📊 Diferencia: {_con_signo(dif)} {icono}")
                if c["_ajuste"] != 0:
                    lineas.append(
                        f"ℹ️ Ajuste parcial del turno {_con_signo(c['_ajuste'])} · sin explicar {_con_signo(c['_restante'])}"
                    )
        lineas.append(f"🔢 Operaciones: {int(_num(ci['num_operaciones']))}")
        obs = str(c.get("observaciones", "") or "").strip()
        for parte in (obs.split(" | ") if obs else []):  # Apertura y Cierre, cada una en su linea
            lineas.append(f"📝 {esc(parte)}")
        ultimo = sub.sort_values("timestamp").iloc[-1]
        if str(ultimo["tipo"]).strip() == "Apertura":
            lineas += ["", f"⏳ Ahora: turno {esc(ultimo['turno'])} abierto por {esc(ultimo['nombre'] or '—')} desde "
                           f"{_hhmm(ultimo['timestamp'])} ({soles(_num(ultimo['total']))})"]
        bloques.append("\n".join(lineas))
    return "\n\n———————\n\n".join(bloques)


# ---------------------------------------------------------------------
# /encuestas: tabla del mes por local
# ---------------------------------------------------------------------
def reporte_encuestas(encuestas: pd.DataFrame, notas: pd.DataFrame, hoy: date, local_filtro: str = "") -> str:
    mes = hoy.strftime("%Y-%m")
    titulo = f"⭐ <b>Encuestas de {_MESES[hoy.month - 1]} {hoy.year}</b>"
    if encuestas is None or encuestas.empty:
        return f"{titulo}\n\nTodavía no hay encuestas registradas."
    enc = con_estado_nota(encuestas, notas)
    enc = enc[enc["mes"] == mes]
    locales = set(enc["local"]) | (set(notas["local"]) if notas is not None and not notas.empty else set())
    elegidos = buscar_locales(local_filtro, locales)
    if not elegidos:
        return f"No encontré ningún local que se parezca a «{esc(local_filtro)}»."

    bloques, tot_n, tot_monto, tot_por_pagar = [], 0, 0.0, 0.0
    for local in elegidos:
        e = enc[enc["local"] == local]
        pagadas = e[e["estado_visible"] == "Pagada"]
        por_pagar = e[e["estado_visible"] == ESTADO_PENDIENTE_PAGO]
        espera = e[~e["estado_visible"].isin(["Pagada", ESTADO_PENDIENTE_PAGO, ESTADO_NO_SE_ABONA])]
        no_abona = e[e["estado_visible"] == ESTADO_NO_SE_ABONA]
        vigente, _ultimo_final = nota_vigente(notas, local)
        if vigente is None:
            nps = "⭐ NPS: sin nota cargada"
        else:
            nota = float(vigente["nota"])
            if str(vigente.get("tipo", "")).strip().lower() == "parcial":
                nps = f"⭐ NPS: avance {nota:g}" + (f" al {esc(vigente['fecha_corte'])}" if str(vigente.get("fecha_corte", "")).strip() else "")
                nps += " ✅ en camino" if estado_parcial(nota) == "En camino" else " ⚠️ por debajo"
            else:
                nps = f"⭐ NPS final {nota:g} " + ("✅ aprobada" if estado_nota(nota) == "Aprobado" else "🔴 no aprobada")
        lineas = [
            f"🏪 <b>{esc(local)}</b>",
            f"📋 Encuestas: {len(e)} ({soles(e['incentivo'].sum())})",
            f"✅ Pagadas: {len(pagadas)} ({soles(pagadas['incentivo'].sum())})",
            f"💸 Por pagar: {len(por_pagar)} ({soles(por_pagar['incentivo'].sum())})",
        ]
        if len(espera):
            lineas.append(f"⏳ En espera de la nota: {len(espera)} ({soles(espera['incentivo'].sum())})")
        if len(no_abona):
            lineas.append(f"🚫 No se abona: {len(no_abona)}")
        lineas.append(nps)
        if local_filtro.strip() and not e.empty:  # con un local: las ultimas encuestas
            lineas += ["", "<i>Últimas encuestas:</i>"]
            for _, r in e.sort_values("timestamp", ascending=False).head(10).iterrows():
                lineas.append(f"• {_dm(r['fecha'])} · {esc(r['nombre'])} · nota {int(_num(r['nota']))}")
                lineas.append(
                    f"   {esc(r['estado_visible'])}" + (f" · {esc(r['medio_pago'])}" if str(r["medio_pago"]).strip() else "")
                )
        bloques.append("\n".join(lineas))
        tot_n += len(e)
        tot_monto += float(e["incentivo"].sum())
        tot_por_pagar += float(por_pagar["incentivo"].sum())
    pie = ""
    if len(elegidos) > 1:
        pie = (
            f"\n\n📊 <b>Total</b>\n📋 Encuestas: {tot_n} ({soles(tot_monto)})\n💸 Por pagar: {soles(tot_por_pagar)}"
        )
    return f"{titulo}\n\n" + "\n\n".join(bloques) + pie


# ---------------------------------------------------------------------
# /operaciones: hoy y el mes, frente a la meta
# ---------------------------------------------------------------------
def reporte_operaciones(registros: pd.DataFrame, config: pd.DataFrame, hoy: date, local_filtro: str = "") -> str:
    titulo = f"📈 <b>Operaciones</b> — hoy {_dm(hoy)} y {_MESES[hoy.month - 1]}"
    if registros is None or registros.empty:
        return f"{titulo}\n\nTodavía no hay registros."
    elegidos = buscar_locales(local_filtro, registros["local"].unique())
    if not elegidos:
        return f"No encontré ningún local que se parezca a «{esc(local_filtro)}»."
    metas = {}
    if config is not None and not config.empty:
        for _, r in config.iterrows():
            metas[str(r["local"])] = {"Mañana": int(_num(r.get("meta_ops_manana"))), "Tarde": int(_num(r.get("meta_ops_tarde")))}
    ops = operaciones_por_turno(registros)
    inicio_mes = hoy.replace(day=1)
    bloques, tot_hoy, tot_mes = [], 0, 0
    for local in elegidos:
        o = ops[ops["local"] == local]
        hoy_l = o[o["fecha"] == hoy]
        mes_l = o[(o["fecha"] >= inicio_mes) & (o["fecha"] <= hoy)]
        turnos = []
        for turno, icono in (("Mañana", "🌅"), ("Tarde", "🌇")):
            meta = metas.get(local, {}).get(turno, 0)
            fila = hoy_l[hoy_l["turno"] == turno]
            if not fila.empty:
                n = int(fila["operaciones"].iloc[0])
                if meta:
                    estado = f"{n}/{meta} " + ("✅" if n >= meta else f"(faltaron {meta - n})")
                else:
                    estado = f"{n}"
            else:
                abierto = registros[(registros["local"] == local) & (registros["fecha"] == hoy) & (registros["turno"] == turno)]
                estado = ("en curso" if not abierto.empty else "—") + (f" (meta {meta})" if meta else "")
            turnos.append(f"   {icono} {turno}: {estado}")
        n_hoy = int(hoy_l["operaciones"].sum())
        n_mes = int(mes_l["operaciones"].sum())
        prom = f" · promedio {n_mes / len(mes_l):.0f} por turno" if len(mes_l) else ""
        bloques.append(
            "\n".join([f"🏪 <b>{esc(local)}</b>", f"📅 Hoy: {n_hoy}", *turnos, f"🗓 Mes: {n_mes:,} en {len(mes_l)} turno(s){prom}"])
        )
        tot_hoy += n_hoy
        tot_mes += n_mes
    pie = f"\n\n📊 <b>Total</b>\n📅 Hoy: {tot_hoy:,}\n🗓 Mes: {tot_mes:,}" if len(elegidos) > 1 else ""
    return f"{titulo}\n(solo turnos ya cerrados)\n\n" + "\n\n".join(bloques) + pie


# ---------------------------------------------------------------------
# /solicitudes: pedidos de efectivo o saldo abiertos
# ---------------------------------------------------------------------
def reporte_solicitudes(solicitudes: pd.DataFrame, ahora) -> str:
    activas = solicitudes_activas(solicitudes)
    if activas is None or activas.empty:
        return "📨 No hay solicitudes pendientes. 🎉"
    bloques = []
    for _, s in activas.iterrows():
        urg = " ⚡ URGENTE" if str(s["urgente"]).strip() == "Sí" else ""
        lineas = [
            f"🔔 <b>{esc(s['local'])}</b>{urg}",
            f"📌 {esc(s['tipo'])}",
            f"💰 Monto: {soles(s['monto'])}",
            f"👤 Pidió: {esc(s['solicitado_por'])}",
            f"🕒 Hace {texto_espera(minutos_entre(s['timestamp'], ahora))}",
            f"🔄 Estado: {esc(s['estado'])}",
        ]
        if str(s["nota"]).strip():
            lineas.append(f"📝 {esc(s['nota'])}")
        bloques.append("\n".join(lineas))
    return f"📨 <b>{len(activas)} solicitud(es) pendiente(s)</b>\n\n" + "\n\n".join(bloques)


# ---------------------------------------------------------------------
# /diferencias: cortes y saltos (entre cierres) con diferencia grande
# ---------------------------------------------------------------------
def reporte_diferencias(registros: pd.DataFrame, hoy: date, dias: int = 7, umbral: float = UMBRAL_ALERTA_DIFERENCIA,
                        local_filtro: str = "", ajustes: pd.DataFrame | None = None,
                        incluir_autorizadas: bool = False) -> str:
    """Diferencias grandes de los ultimos dias. Las que administracion YA autorizo en el
    Dashboard no se muestran (salvo incluir_autorizadas): se descuentan igual que alla."""
    dias = max(1, min(int(dias), 90))
    titulo = f"🔎 <b>Diferencias mayores a S/ {umbral:g}</b> — últimos {dias} día(s)"
    if registros is None or registros.empty:
        return f"{titulo}\n\nTodavía no hay registros."
    elegidos = buscar_locales(local_filtro, registros["local"].unique())
    if not elegidos:
        return f"No encontré ningún local que se parezca a «{esc(local_filtro)}»."
    desde = hoy - timedelta(days=dias - 1)
    base = registros[registros["local"].isin(elegidos)]
    cortes, saltos = marcar_autorizados(calcular_cortes(base, ["local", "fecha", "turno"]), calcular_saltos(base), ajustes)
    items, n_aut = [], 0
    for _, c in cortes[cortes["diferencia"].notna()].iterrows():
        if c["fecha"] < desde or abs(float(c["diferencia"])) <= umbral:
            continue
        aut = bool(c["_autorizado"])
        if aut and not incluir_autorizadas:
            n_aut += 1
            continue
        lineas = [
            f"{'🔷' if aut else '🔴'} <b>{esc(c['local'])}</b> · {_dm(c['fecha'])} · {esc(c['turno'])}",
            "   Corte (Apertura → Cierre)",
            f"   👤 {esc(c['nombre'] or '—')}",
            f"   📊 Diferencia: {_con_signo(c['diferencia'])}",
        ]
        if aut:
            lineas.append(f"   🔷 Autorizada (ajuste {_con_signo(c['_ajuste'])})")
        elif c["_ajuste"] != 0:
            lineas.append(f"   ℹ️ Ajuste parcial del turno {_con_signo(c['_ajuste'])} · sin explicar {_con_signo(c['_restante'])}")
        items.append((c["fecha"], str(c["hora_cierre"] or ""), "\n".join(lineas)))
    for _, s in saltos[saltos["diferencia"].notna()].iterrows():
        if s["fecha_apertura"] < desde or abs(float(s["diferencia"])) <= umbral:
            continue
        aut = bool(s["_autorizado"])
        if aut and not incluir_autorizadas:
            n_aut += 1
            continue
        lineas = [
            f"{'🔷' if aut else '🟠'} <b>{esc(s['local'])}</b> · {_dm(s['fecha_apertura'])}",
            "   Entre cierre y apertura",
            f"   🔒 Cerró: {esc(s['nombre_cierre'] or '—')}",
            f"   🔓 Abrió: {esc(s['nombre_apertura'] or '—')}",
            f"   📊 Diferencia: {_con_signo(s['diferencia'])}",
        ]
        if aut:
            lineas.append(f"   🔷 Autorizada (ajuste {_con_signo(s['_ajuste'])})")
        elif s["_ajuste"] != 0:
            lineas.append(f"   ℹ️ Ajuste parcial {_con_signo(s['_ajuste'])} · sin explicar {_con_signo(s['_restante'])}")
        items.append((s["fecha_apertura"], str(s["hora_apertura"] or ""), "\n".join(lineas)))

    nota = "(incluye las autorizadas 🔷)" if incluir_autorizadas else "(las ya autorizadas en el Dashboard no se muestran)"
    pie_aut = f"\n\n🔷 {n_aut} ya autorizada(s) no se muestran. Escribe /diferencias todas para verlas." if n_aut else ""
    if not items:
        return f"{titulo}\n{nota}\n\n✅ No hay diferencias pendientes de ese tamaño.{pie_aut}"
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    cuerpo = "\n\n".join(texto for _, _, texto in items[:20])
    mas = f"\n\n… y {len(items) - 20} más (míralas en el Dashboard)." if len(items) > 20 else ""
    return (
        f"{titulo}\n{nota}\n\n{cuerpo}{mas}"
        f"\n\n🔴 corte · 🟠 entre cierre y apertura\n📊 Total: {len(items)}{pie_aut}"
    )


# ---------------------------------------------------------------------
# Turnos que se quedaron sin cerrar (alerta automatica y parte del resumen)
# ---------------------------------------------------------------------
def turnos_sin_cerrar(registros: pd.DataFrame, ahora, horas: float = 8.0) -> list[dict]:
    """Turnos de ayer u hoy cuyo ULTIMO registro es una Apertura de hace `horas` o mas. Es la
    misma idea (y el mismo umbral por defecto) que "⏰ Turnos sin cerrar" del Dashboard."""
    if registros is None or registros.empty:
        return []
    desde = ahora.date() - timedelta(days=1)
    r = registros[registros["fecha"] >= desde]
    if r.empty:
        return []
    ultimo = r.sort_values("timestamp").groupby(["local", "fecha", "turno"]).tail(1)
    salida = []
    for _, t in ultimo[ultimo["tipo"] == "Apertura"].iterrows():
        ts = pd.to_datetime(t["timestamp"], errors="coerce")
        if pd.isna(ts):
            continue
        abierto = (ahora - ts).total_seconds() / 3600
        if abierto >= horas:
            salida.append({
                "id": str(t["id"]), "local": t["local"], "turno": t["turno"], "fecha": t["fecha"],
                "nombre": t["nombre"], "hora": ts.strftime("%H:%M"), "horas": abierto, "total": _num(t["total"]),
            })
    salida.sort(key=lambda x: -x["horas"])
    return salida


def texto_turno_sin_cerrar(t: dict) -> str:
    return "\n".join([
        "⏰ <b>Turno sin cerrar</b>",
        f"🏪 {esc(t['local'])} · {esc(t['turno'])} · {_dm(t['fecha'])}",
        f"👤 Abrió: {esc(t['nombre'] or '—')} a las {t['hora']}",
        f"⌛ Lleva {texto_espera(t['horas'] * 60)} abierto",
        f"💰 Total al abrir: {soles(t['total'])}",
        "👉 Que lo cierren en Registro, o revísalo en el Dashboard.",
    ])


# ---------------------------------------------------------------------
# /ranking: trabajadores por operaciones
# ---------------------------------------------------------------------
PERIODOS_RANKING = ("hoy", "semana", "mes", "anterior")


def rango_periodo(periodo: str, hoy: date) -> tuple[date, date, str]:
    """(desde, hasta, nombre) de 'hoy', 'semana' (7 dias), 'mes' (en curso) o 'anterior'."""
    if periodo == "hoy":
        return hoy, hoy, "hoy"
    if periodo == "semana":
        return hoy - timedelta(days=6), hoy, "últimos 7 días"
    if periodo == "anterior":
        fin = hoy.replace(day=1) - timedelta(days=1)
        return fin.replace(day=1), fin, f"{_MESES[fin.month - 1]} {fin.year}"
    return hoy.replace(day=1), hoy, f"{_MESES[hoy.month - 1]} {hoy.year}"


_MEDALLAS = {1: "🥇", 2: "🥈", 3: "🥉"}


def reporte_ranking(registros: pd.DataFrame, config: pd.DataFrame, encuestas: pd.DataFrame, ajustes, hoy: date,
                    periodo: str = "mes", local_filtro: str = "", max_filas: int = 15) -> str:
    desde, hasta, etiqueta = rango_periodo(periodo, hoy)
    titulo = f"🏆 <b>Ranking de trabajadores</b> — {etiqueta}\n({_dm(desde)} al {_dm(hasta)})"
    if registros is None or registros.empty:
        return f"{titulo}\n\nTodavía no hay registros."
    elegidos = buscar_locales(local_filtro, registros["local"].unique())
    if not elegidos:
        return f"No encontré ningún local que se parezca a «{esc(local_filtro)}»."
    reg = registros[(registros["fecha"] >= desde) & (registros["fecha"] <= hasta) & registros["local"].isin(elegidos)]
    if reg.empty:
        return f"{titulo}\n\nNo hay registros en ese período."

    # operaciones y diferencias: por quien ABRIO el corte (igual que la Liquidacion del Dashboard)
    por_id = reg.assign(_id=reg["id"].astype(str)).drop_duplicates("_id", keep="last").set_index("_id")
    cortes, _ = marcar_autorizados(calcular_cortes(reg, ["local", "fecha", "turno"]), None, ajustes)
    cerrados = cortes[cortes["cierre"].notna()].copy() if not cortes.empty else cortes
    ops_p, dif_p = pd.Series(dtype=float), pd.Series(dtype=float)
    if cerrados is not None and not cerrados.empty:
        cerrados["_ops"] = pd.to_numeric(cerrados["id_cierre"].astype(str).map(por_id["num_operaciones"]), errors="coerce").fillna(0)
        ops_p = cerrados.groupby("nombre")["_ops"].sum()
        sin_aut = cerrados[(~cerrados["_autorizado"]) & (cerrados["diferencia"].abs() > UMBRAL_VERDE)]
        dif_p = sin_aut.groupby("nombre")["diferencia"].sum()

    # turnos trabajados (quien registro algo en ese turno) y turnos con la meta cumplida
    metas = {}
    if config is not None and not config.empty:
        for _, r in config.iterrows():
            metas[(str(r["local"]), "Mañana")] = int(_num(r.get("meta_ops_manana")))
            metas[(str(r["local"]), "Tarde")] = int(_num(r.get("meta_ops_tarde")))
    tp = reg[["local", "fecha", "turno", "nombre"]].drop_duplicates().merge(
        operaciones_por_turno(reg)[["local", "fecha", "turno", "operaciones"]], on=["local", "fecha", "turno"], how="left"
    )
    tp["meta"] = [metas.get((str(l), t), 0) for l, t in zip(tp["local"], tp["turno"])]
    tp["con_meta"] = (tp["meta"] > 0) & tp["operaciones"].notna()
    tp["cumple"] = tp["con_meta"] & (tp["operaciones"] >= tp["meta"])
    stats = tp.groupby("nombre").agg(trabajados=("turno", "size"), con_meta=("con_meta", "sum"), cumplidos=("cumple", "sum"))
    locales_p = tp.groupby("nombre")["local"].agg(lambda s: ", ".join(sorted(set(s))))

    n_enc = pd.Series(dtype=int)
    if encuestas is not None and not encuestas.empty:
        e = encuestas[(encuestas["fecha"] >= desde) & (encuestas["fecha"] <= hasta) & encuestas["local"].isin(elegidos)]
        n_enc = e.groupby("nombre").size()

    nombres = [n for n in (set(stats.index) | set(ops_p.index)) if str(n).strip()]
    filas = sorted(
        nombres,
        key=lambda n: (-float(ops_p.get(n, 0)), -int(stats["trabajados"].get(n, 0)), str(n)),
    )
    if not filas:
        return f"{titulo}\n\nNo hay trabajadores con registros en ese período."

    bloques = []
    for pos, n in enumerate(filas[:max_filas], start=1):
        ops = int(ops_p.get(n, 0))
        turnos = int(stats["trabajados"].get(n, 0))
        lineas = [
            f"{_MEDALLAS.get(pos, f'{pos}.')} <b>{esc(n)}</b> · {esc(locales_p.get(n, ''))}",
            f"   🔢 {ops:,} operaciones · {turnos} turno(s)",
        ]
        if turnos and ops:
            lineas.append(f"   📈 {ops / turnos:.0f} por turno")
        con_meta = int(stats["con_meta"].get(n, 0))
        if con_meta:
            lineas.append(f"   🎯 Meta cumplida: {int(stats['cumplidos'].get(n, 0))} de {con_meta} turno(s)")
        if int(n_enc.get(n, 0)):
            lineas.append(f"   ⭐ Encuestas: {int(n_enc.get(n, 0))}")
        d = float(dif_p.get(n, 0.0))
        if abs(d) >= 0.005:
            lineas.append(f"   📊 Diferencias sin autorizar: {_con_signo(d)}")
        bloques.append("\n".join(lineas))
    mas = f"\n\n… y {len(filas) - max_filas} más." if len(filas) > max_filas else ""
    pie = "\n\nℹ️ Las operaciones se cuentan por quien abrió el corte (igual que en la Liquidación del Dashboard)."
    return f"{titulo}\n\n" + "\n\n".join(bloques) + mas + pie


# ---------------------------------------------------------------------
# /resumen: el resumen del dia (tambien se manda solo cada noche)
# ---------------------------------------------------------------------
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def resumen_diario(registros, config, encuestas, ajustes, solicitudes, hoy: date, ahora,
                   umbral: float = UMBRAL_ALERTA_DIFERENCIA) -> str:
    partes = [f"🌙 <b>Resumen del día</b> — {_DIAS[hoy.weekday()]} {_dm(hoy)}"]
    partes.append(reporte_operaciones(registros, config, hoy))
    partes.append(reporte_fondo(registros, config, hoy))
    partes.append(reporte_diferencias(registros, hoy, 1, umbral, "", ajustes))
    abiertos = turnos_sin_cerrar(registros, ahora, horas=0.0)
    abiertos = [t for t in abiertos if t["fecha"] >= hoy - timedelta(days=1)]
    if abiertos:
        partes.append("⏳ <b>Turnos todavía abiertos</b>\n\n" + "\n\n".join(
            f"🏪 {esc(t['local'])} · {esc(t['turno'])} · {_dm(t['fecha'])}\n👤 Abrió: {esc(t['nombre'] or '—')} a las {t['hora']}\n"
            f"⌛ Lleva {texto_espera(t['horas'] * 60)}" for t in abiertos
        ))
    else:
        partes.append("✅ Todos los turnos están cerrados.")
    partes.append(reporte_solicitudes(solicitudes, ahora))
    hoy_enc = encuestas[encuestas["fecha"] == hoy] if encuestas is not None and not encuestas.empty else None
    if hoy_enc is not None and len(hoy_enc):
        por_local = hoy_enc.groupby("local").size()
        partes.append("⭐ <b>Encuestas de hoy</b>\n" + "\n".join(f"🏪 {esc(l)}: {n}" for l, n in por_local.items()))
    else:
        partes.append("⭐ Hoy no se registraron encuestas.")
    return "\n\n———————\n\n".join(partes)
