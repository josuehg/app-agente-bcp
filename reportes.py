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
    minutos_entre,
    nota_vigente,
    operaciones_por_turno,
    solicitudes_activas,
    texto_espera,
)

_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

AYUDA = (
    "🤖 <b>Comandos del bot</b>\n\n"
    "/fondo — efectivo, tarjeta y total actual de cada local\n"
    "/corte — último corte cerrado de cada local (efectivo, tarjeta, total, operaciones, diferencia)\n"
    "/encuestas — encuestas del mes, pagos y nota NPS\n"
    "/operaciones — operaciones de hoy y del mes frente a la meta\n"
    "/solicitudes — pedidos de efectivo o saldo pendientes\n"
    "/diferencias — diferencias mayores a S/ {umbral:g} de los últimos 7 días\n"
    "/id — tu chat id\n\n"
    "Puedes filtrar por local: <code>/corte colon</code>, <code>/encuestas zol</code>. "
    "En /diferencias puedes poner los días: <code>/diferencias 30</code>."
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


def _fondo(ef, ta, total) -> str:
    return f"Efectivo {soles(ef)} · Tarjeta {soles(ta)} · Total {soles(total)}"


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
        texto = (
            f"{'🔴' if bajo else '✅'} <b>{esc(local)}</b> — {esc(cuando)} ({esc(r['nombre'] or '—')})\n"
            f"   {_fondo(_num(r['efectivo']), _num(r['tarjeta']), total)}"
            + (f"\n   Mínimo {soles(minimo)}" + (" ⚠️ BAJO EL MÍNIMO" if bajo else "") if minimo > 0 else "")
        )
        filas.append((not bajo, local, texto, total))
    filas.sort(key=lambda x: (x[0], x[1]))
    total_todos = sum(f[3] for f in filas)
    cuerpo = "\n\n".join(f[2] for f in filas)
    pie = f"\n\n<b>Total de {len(filas)} local(es):</b> {soles(total_todos)}" if len(filas) > 1 else ""
    return f"🏦 <b>Fondo actual</b> (último registro de cada local)\n\n{cuerpo}{pie}"


# ---------------------------------------------------------------------
# /corte: ultimo corte cerrado de cada local
# ---------------------------------------------------------------------
def reporte_corte(registros: pd.DataFrame, local_filtro: str = "") -> str:
    if registros is None or registros.empty:
        return "Todavía no hay registros."
    elegidos = buscar_locales(local_filtro, registros["local"].unique())
    if not elegidos:
        return f"No encontré ningún local que se parezca a «{esc(local_filtro)}»."

    por_id = registros.assign(_id=registros["id"].astype(str)).drop_duplicates("_id", keep="last").set_index("_id")
    bloques = []
    for local in elegidos:
        sub = registros[registros["local"] == local]
        cortes = calcular_cortes(sub, ["local", "fecha", "turno"])
        cerrados = cortes[cortes["id_cierre"].astype(str).isin(por_id.index) & cortes["cierre"].notna()].copy() if not cortes.empty else cortes
        if cerrados is None or cerrados.empty:
            bloques.append(f"✂️ <b>{esc(local)}</b>\nTodavía no tiene cortes cerrados.")
            continue
        cerrados["_ts"] = cerrados["id_cierre"].astype(str).map(por_id["timestamp"])
        c = cerrados.sort_values("_ts").iloc[-1]
        ci = por_id.loc[str(c["id_cierre"])]
        ap = por_id.loc[str(c["id_apertura"])] if str(c["id_apertura"]) in por_id.index else None
        dif = c["diferencia"]
        lineas = [f"✂️ <b>{esc(local)}</b> — {esc(c['turno'])}, {_dm(c['fecha'])}"]
        lineas.append(
            f"👤 Abrió {esc(c['nombre'] or '—')} {esc(c['hora_apertura'])} · cerró {esc(c['nombre_cierre'] or '—')} {esc(c['hora_cierre'])}"
        )
        if ap is not None:
            lineas.append(f"Apertura: {_fondo(_num(ap['efectivo']), _num(ap['tarjeta']), _num(ap['total']))}")
        lineas.append(f"Cierre: {_fondo(_num(ci['efectivo']), _num(ci['tarjeta']), _num(ci['total']))}")
        if pd.notna(dif):
            icono = "✅" if abs(float(dif)) <= UMBRAL_VERDE else "🔴"
            lineas.append(f"Diferencia: {_con_signo(dif)} {icono}")
        lineas.append(f"Operaciones de este cierre: {int(_num(ci['num_operaciones']))}")
        obs = str(c.get("observaciones", "") or "").strip()
        if obs:
            lineas.append(f"📝 {esc(obs)}")
        ultimo = sub.sort_values("timestamp").iloc[-1]
        if str(ultimo["tipo"]).strip() == "Apertura":
            lineas.append(
                f"⏳ Ahora: turno {esc(ultimo['turno'])} abierto por {esc(ultimo['nombre'] or '—')} desde "
                f"{_hhmm(ultimo['timestamp'])} ({soles(_num(ultimo['total']))})"
            )
        bloques.append("\n".join(lineas))
    return "\n\n".join(bloques)


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
            nps = "NPS: sin nota cargada"
        else:
            nota = float(vigente["nota"])
            if str(vigente.get("tipo", "")).strip().lower() == "parcial":
                nps = f"NPS: avance {nota:g}" + (f" al {esc(vigente['fecha_corte'])}" if str(vigente.get("fecha_corte", "")).strip() else "")
                nps += " ✅ en camino" if estado_parcial(nota) == "En camino" else " ⚠️ por debajo"
            else:
                nps = f"NPS final {nota:g} " + ("✅ aprobada" if estado_nota(nota) == "Aprobado" else "🔴 no aprobada")
        lineas = [
            f"<b>{esc(local)}</b> — {len(e)} encuesta(s) · {soles(e['incentivo'].sum())}",
            f"   Pagadas {len(pagadas)} ({soles(pagadas['incentivo'].sum())}) · Por pagar {len(por_pagar)} "
            f"({soles(por_pagar['incentivo'].sum())})",
        ]
        if len(espera) or len(no_abona):
            lineas.append(f"   En espera de la nota {len(espera)} · No se abona {len(no_abona)}")
        lineas.append(f"   {nps}")
        if local_filtro.strip() and not e.empty:  # con un local: las ultimas encuestas
            lineas.append("   <i>Últimas:</i>")
            for _, r in e.sort_values("timestamp", ascending=False).head(10).iterrows():
                lineas.append(
                    f"   • {_dm(r['fecha'])} {esc(r['nombre'])} · nota {int(_num(r['nota']))} · "
                    f"{esc(r['estado_visible'])}" + (f" · {esc(r['medio_pago'])}" if str(r["medio_pago"]).strip() else "")
                )
        bloques.append("\n".join(lineas))
        tot_n += len(e)
        tot_monto += float(e["incentivo"].sum())
        tot_por_pagar += float(por_pagar["incentivo"].sum())
    pie = ""
    if len(elegidos) > 1:
        pie = f"\n\n<b>Total:</b> {tot_n} encuesta(s) · {soles(tot_monto)} · por pagar {soles(tot_por_pagar)}"
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
        partes = []
        for turno in ("Mañana", "Tarde"):
            meta = metas.get(local, {}).get(turno, 0)
            fila = hoy_l[hoy_l["turno"] == turno]
            if not fila.empty:
                n = int(fila["operaciones"].iloc[0])
                if meta:
                    partes.append(f"{turno} {n}/{meta} " + ("✅" if n >= meta else f"(faltaron {meta - n})"))
                else:
                    partes.append(f"{turno} {n}")
            else:
                abierto = registros[(registros["local"] == local) & (registros["fecha"] == hoy) & (registros["turno"] == turno)]
                partes.append(f"{turno} " + ("en curso" if not abierto.empty else "—") + (f" (meta {meta})" if meta else ""))
        n_hoy = int(hoy_l["operaciones"].sum())
        n_mes = int(mes_l["operaciones"].sum())
        prom = f" · promedio {n_mes / len(mes_l):.0f} por turno" if len(mes_l) else ""
        bloques.append(
            f"<b>{esc(local)}</b>\n   Hoy: {n_hoy} ({' · '.join(partes)})\n   Mes: {n_mes:,} en {len(mes_l)} turno(s){prom}"
        )
        tot_hoy += n_hoy
        tot_mes += n_mes
    pie = f"\n\n<b>Total:</b> hoy {tot_hoy:,} · mes {tot_mes:,}" if len(elegidos) > 1 else ""
    return f"{titulo}\n(solo turnos ya cerrados)\n\n" + "\n\n".join(bloques) + pie


# ---------------------------------------------------------------------
# /solicitudes: pedidos de efectivo o saldo abiertos
# ---------------------------------------------------------------------
def reporte_solicitudes(solicitudes: pd.DataFrame, ahora) -> str:
    activas = solicitudes_activas(solicitudes)
    if activas is None or activas.empty:
        return "📨 No hay solicitudes pendientes. 🎉"
    lineas = [f"📨 <b>{len(activas)} solicitud(es) pendiente(s)</b>\n"]
    for _, s in activas.iterrows():
        urg = " ⚡ URGENTE" if str(s["urgente"]).strip() == "Sí" else ""
        espera = texto_espera(minutos_entre(s["timestamp"], ahora))
        lineas.append(
            f"🔔 <b>{esc(s['local'])}</b> · {esc(s['tipo'])} · {soles(s['monto'])}{urg}\n"
            f"   Pidió {esc(s['solicitado_por'])} · hace {espera} · {esc(s['estado'])}"
            + (f"\n   📝 {esc(s['nota'])}" if str(s["nota"]).strip() else "")
        )
    return "\n".join(lineas)


# ---------------------------------------------------------------------
# /diferencias: cortes y saltos (entre cierres) con diferencia grande
# ---------------------------------------------------------------------
def reporte_diferencias(registros: pd.DataFrame, hoy: date, dias: int = 7, umbral: float = UMBRAL_ALERTA_DIFERENCIA,
                        local_filtro: str = "") -> str:
    dias = max(1, min(int(dias), 90))
    titulo = f"🔎 <b>Diferencias mayores a S/ {umbral:g}</b> — últimos {dias} día(s)"
    if registros is None or registros.empty:
        return f"{titulo}\n\nTodavía no hay registros."
    elegidos = buscar_locales(local_filtro, registros["local"].unique())
    if not elegidos:
        return f"No encontré ningún local que se parezca a «{esc(local_filtro)}»."
    desde = hoy - timedelta(days=dias - 1)
    base = registros[registros["local"].isin(elegidos)]
    items = []
    cortes = calcular_cortes(base, ["local", "fecha", "turno"])
    if not cortes.empty:
        for _, c in cortes[cortes["diferencia"].notna()].iterrows():
            if c["fecha"] >= desde and abs(float(c["diferencia"])) > umbral:
                items.append((c["fecha"], _hhmm_str(c["hora_cierre"]), "🔴",
                              f"{_dm(c['fecha'])} <b>{esc(c['local'])}</b> {esc(c['turno'])} · corte de {esc(c['nombre'] or '—')}: "
                              f"{_con_signo(c['diferencia'])}"))
    saltos = calcular_saltos(base)
    if not saltos.empty:
        for _, s in saltos[saltos["diferencia"].notna()].iterrows():
            if s["fecha_apertura"] >= desde and abs(float(s["diferencia"])) > umbral:
                items.append((s["fecha_apertura"], _hhmm_str(s["hora_apertura"]), "🟠",
                              f"{_dm(s['fecha_apertura'])} <b>{esc(s['local'])}</b> entre cierre y apertura "
                              f"({esc(s['nombre_cierre'] or '—')} → {esc(s['nombre_apertura'] or '—')}): "
                              f"{_con_signo(s['diferencia'])}"))
    if not items:
        return f"{titulo}\n\n✅ No hay diferencias de ese tamaño."
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    lineas = [f"{icono} {texto}" for _, _, icono, texto in items[:20]]
    mas = f"\n… y {len(items) - 20} más (míralas en el Dashboard)." if len(items) > 20 else ""
    return (
        f"{titulo}\n(sin descontar lo ya autorizado en el Dashboard)\n\n" + "\n".join(lineas) + mas
        + f"\n\n🔴 corte (Apertura→Cierre) · 🟠 entre cierre y apertura. Total: {len(items)}."
    )


def _hhmm_str(x) -> str:
    return str(x or "")
