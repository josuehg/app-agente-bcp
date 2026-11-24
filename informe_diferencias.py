"""
informe_diferencias.py
-----------------------
Reporte en PDF de las diferencias "rojas" (mas de S/ 1) de TODAS las sucursales en un periodo: las que siguen
sin autorizar y, si se pide, tambien las ya autorizadas. Usa los mismos calculos del Dashboard (cortes, entregas
de caja y autorizaciones), asi que lo que sale aqui es lo mismo que se ve en la Linea de tiempo.

    diferencias_rojas(...)  -> una fila por diferencia (DataFrame)
    resumen_por_sucursal / resumen_por_persona(...)
    periodo_informe(...)    -> (desde, hasta, titulo) de "mes actual", "mes anterior" o un rango propio
    generar_pdf(...)        -> bytes del PDF (necesita fpdf2; no necesita streamlit ni Google)

El corte se le carga a quien abrio el turno; la entrega de caja (entre un Cierre y la siguiente Apertura), a quien
cerro. Es un documento INTERNO de administracion: trae el motivo y quien autorizo cada ajuste.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from cuadre import (
    UMBRAL_VERDE,
    calcular_cortes,
    calcular_saltos,
    con_conformidad,
    marcar_autorizados,
)

_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

COLUMNAS_INFORME = [
    "local", "fecha", "hora", "tipo", "turno", "persona", "cargo_a", "detalle", "diferencia", "estado",
    "ajuste", "restante", "autorizo", "motivo", "conformidad", "observaciones",
]
ESTADO_PENDIENTE, ESTADO_PARCIAL, ESTADO_AUTORIZADA = "Pendiente", "Parcial", "Autorizada"


# ---------------------------------------------------------------------
# Periodos
# ---------------------------------------------------------------------
def periodo_informe(clave: str, hoy: dt.date, rango: tuple | None = None) -> tuple[dt.date, dt.date, str]:
    """(desde, hasta, titulo). clave: 'mes' (en curso), 'anterior' (mes pasado completo) o 'rango' (el que se pase)."""
    if clave == "anterior":
        fin = hoy.replace(day=1) - dt.timedelta(days=1)
        return fin.replace(day=1), fin, f"Mes anterior · {_MESES[fin.month - 1]} {fin.year}"
    if clave == "rango" and rango:
        a, b = rango
        return a, b, f"Rango elegido · {a:%d/%m/%Y} al {b:%d/%m/%Y}"
    return hoy.replace(day=1), hoy, f"Mes actual · {_MESES[hoy.month - 1]} {hoy.year}"


# ---------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------
def _autorizaciones(ajustes, filtro) -> tuple[str, str]:
    """Quien autorizo y por que (lo que haya registrado en la hoja Ajustes para esa diferencia)."""
    if ajustes is None or ajustes.empty:
        return "", ""
    sel = ajustes[filtro(ajustes)]
    if sel.empty:
        return "", ""
    quienes = sorted({str(x).strip() for x in sel["autorizado_por"] if str(x).strip()})
    motivos = [str(x).strip() for x in sel["motivo"] if str(x).strip()]
    return ", ".join(quienes), "; ".join(dict.fromkeys(motivos))


_OBS_VACIAS = {"", "ninguna", "ninguno", "nada", "no", "n/a", "na", "-", "sin novedad", "sin novedades", "ninguna novedad", "sin observaciones"}


def _limpiar_observaciones(texto) -> str:
    """Las observaciones llegan como 'Apertura: Ninguna | Cierre: faltaron 20': se quitan las partes de relleno."""
    partes = []
    for parte in str(texto or "").split(" | "):
        cuerpo = parte.split(":", 1)[-1].strip().rstrip(".").lower()
        if cuerpo not in _OBS_VACIAS:
            partes.append(parte.strip())
    return " | ".join(partes)


def diferencias_rojas(registros: pd.DataFrame, ajustes, conformidades, desde: dt.date, hasta: dt.date) -> pd.DataFrame:
    """Todas las diferencias de mas de S/ UMBRAL_VERDE entre `desde` y `hasta` (cortes por la fecha del turno; entregas por la
    fecha del Cierre), con su estado: Pendiente, Parcial (hay un ajuste pero falta explicar parte) o Autorizada."""
    if registros is None or registros.empty:
        return pd.DataFrame(columns=COLUMNAS_INFORME)
    reg = registros[(registros["fecha"] >= desde) & (registros["fecha"] <= hasta)]
    cortes, saltos = marcar_autorizados(
        calcular_cortes(reg, ["local", "fecha", "turno"]), calcular_saltos(registros), ajustes
    )
    filas: list[dict] = []

    def _estado(autorizado: bool, ajuste: float) -> str:
        return ESTADO_AUTORIZADA if autorizado else (ESTADO_PARCIAL if ajuste != 0 else ESTADO_PENDIENTE)

    for _, c in cortes.iterrows():
        d = c["diferencia"]
        if pd.isna(d) or abs(float(d)) <= UMBRAL_VERDE:
            continue
        quien, motivo = _autorizaciones(
            ajustes, lambda a, c=c: (a["local"] == c["local"]) & (a["fecha"] == c["fecha"]) & (a["turno"] == c["turno"])
        )
        nombre = str(c["nombre"] or "").strip()
        cerro = str(c["nombre_cierre"] or "").strip()
        filas.append({
            "local": c["local"], "fecha": c["fecha"], "hora": str(c["hora_apertura"] or ""), "tipo": "Corte",
            "turno": c["turno"],
            "persona": nombre + (f" (cerró {cerro})" if cerro and cerro.lower() != nombre.lower() else ""),
            "cargo_a": nombre or "(sin nombre)",
            "detalle": f"Corte {c['corte']} · {c['hora_apertura']} a {c['hora_cierre'] or 'sin cerrar'}",
            "diferencia": float(d), "estado": _estado(bool(c["_autorizado"]), float(c["_ajuste"])),
            "ajuste": float(c["_ajuste"]), "restante": float(c["_restante"]), "autorizo": quien, "motivo": motivo,
            "id_cierre": str(c.get("id_cierre", "") or ""), "observaciones": _limpiar_observaciones(c.get("observaciones", "")),
        })
    if not saltos.empty:
        for _, s in saltos.iterrows():
            d = s["diferencia"]
            if pd.isna(d) or abs(float(d)) <= UMBRAL_VERDE or not (desde <= s["fecha_cierre"] <= hasta):
                continue

            def _filtro(a, s=s):
                por_id = a["salto_id_cierre"] == s["id_cierre"]
                if s["tipo_salto"] == "Entre días":  # ajustes viejos: por local + fecha, sin turno ni id
                    por_id = por_id | ((a["turno"] == "") & (a["salto_id_cierre"] == "") & (a["local"] == s["local"]) & (a["fecha"] == s["fecha_cierre"]))
                return por_id

            quien, motivo = _autorizaciones(ajustes, _filtro)
            cierra, abre = str(s["nombre_cierre"] or "").strip(), str(s["nombre_apertura"] or "").strip()
            filas.append({
                "local": s["local"], "fecha": s["fecha_cierre"], "hora": str(s["hora_cierre"] or ""),
                "tipo": "Entrega de caja", "turno": s["turno_cierre"], "persona": f"{cierra or '—'} → {abre or '—'}",
                "cargo_a": cierra or "(sin nombre)",
                "detalle": f"{s['tipo_salto']}: cierre {s['turno_cierre']} {s['hora_cierre']} → apertura {s['fecha_apertura']:%d/%m} {s['hora_apertura']}",
                "diferencia": float(d), "estado": _estado(bool(s["_autorizado"]), float(s["_ajuste"])),
                "ajuste": float(s["_ajuste"]), "restante": float(s["_restante"]), "autorizo": quien, "motivo": motivo,
                "id_cierre": str(s["id_cierre"]), "observaciones": "",
            })
    salida = pd.DataFrame(filas, columns=COLUMNAS_INFORME + ["id_cierre"])
    if salida.empty:
        return salida.drop(columns="id_cierre")
    salida["conformidad"] = con_conformidad(salida[["tipo", "id_cierre"]], conformidades)["conformidad"].to_numpy()
    salida = salida.sort_values(["local", "fecha", "hora", "tipo"], kind="stable").reset_index(drop=True)
    return salida[COLUMNAS_INFORME]


def _totales(filas: pd.DataFrame) -> dict:
    d = filas["diferencia"] if not filas.empty else pd.Series(dtype=float)
    return {
        "n": int(len(filas)),
        "faltantes": float(-d[d < 0].sum()),
        "sobrantes": float(d[d > 0].sum()),
        "neto": float(d.sum()),
    }


def resumen_por_sucursal(rojas: pd.DataFrame, locales) -> pd.DataFrame:
    """Una fila por sucursal (tambien las que no tuvieron diferencias): lo pendiente y lo ya autorizado."""
    salida = []
    for local in sorted({str(x) for x in locales}):
        mias = rojas[rojas["local"] == local] if not rojas.empty else rojas
        pend = mias[mias["estado"] != ESTADO_AUTORIZADA] if not mias.empty else mias
        aut = mias[mias["estado"] == ESTADO_AUTORIZADA] if not mias.empty else mias
        t = _totales(pend)
        salida.append({
            "local": local, "pendientes": t["n"], "faltantes": t["faltantes"], "sobrantes": t["sobrantes"],
            "neto": t["neto"], "autorizadas": int(len(aut)), "monto_autorizado": float(aut["diferencia"].sum()) if len(aut) else 0.0,
        })
    return pd.DataFrame(salida, columns=["local", "pendientes", "faltantes", "sobrantes", "neto", "autorizadas", "monto_autorizado"])


def resumen_por_persona(rojas: pd.DataFrame) -> pd.DataFrame:
    """Lo PENDIENTE por trabajador (a quien se le carga: el corte a quien abrio, la entrega a quien cerro)."""
    columnas = ["persona", "cortes", "entregas", "faltantes", "sobrantes", "neto"]
    if rojas is None or rojas.empty:
        return pd.DataFrame(columns=columnas)
    pend = rojas[rojas["estado"] != ESTADO_AUTORIZADA]
    salida = []
    for persona, g in pend.groupby("cargo_a", sort=False):
        t = _totales(g)
        salida.append({
            "persona": persona, "cortes": int((g["tipo"] == "Corte").sum()), "entregas": int((g["tipo"] != "Corte").sum()),
            "faltantes": t["faltantes"], "sobrantes": t["sobrantes"], "neto": t["neto"],
        })
    if not salida:
        return pd.DataFrame(columns=columnas)
    return pd.DataFrame(salida, columns=columnas).sort_values("neto", kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------
def _t(texto) -> str:
    """Texto seguro para las fuentes basicas del PDF (solo Latin-1: tildes y ñ salen bien; lo demas se reemplaza)."""
    s = str(texto if texto is not None else "")
    for a, b in (("→", "->"), ("←", "<-"), ("–", "-"), ("—", "-"), ("’", "'"), ("“", '"'), ("”", '"'), ("…", "..."), ("✓", "OK"), ("\n", " ")):
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def _soles(x, signo: bool = False) -> str:
    return f"{float(x):+,.2f}" if signo else f"{float(x):,.2f}"


def generar_pdf(rojas: pd.DataFrame, locales, desde: dt.date, hasta: dt.date, titulo_periodo: str,
                generado: dt.datetime, incluir_autorizadas: bool = False) -> bytes:
    """El PDF (horizontal, A4). `rojas` viene de diferencias_rojas(); `locales` son TODAS las sucursales (las que no
    tuvieron diferencias tambien salen, en cero). Sin incluir_autorizadas, el detalle muestra solo lo pendiente."""
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    AZUL, ROJO, NARANJA, GRIS = (31, 56, 100), (190, 30, 45), (200, 110, 0), (110, 110, 110)
    ENCABEZADO = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=AZUL)

    class Informe(FPDF):
        def footer(self):
            self.set_y(-10)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(*GRIS)
            self.cell(0, 4, _t("Uso interno de administración - no compartir con los cajeros (incluye motivos y quién autorizó)."), align="L")
            self.set_x(-40)
            self.cell(30, 4, f"Página {self.page_no()}/{{nb}}", align="R")

    pdf = Informe(orientation="L", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(True, margin=14)
    pdf.add_page()

    resumen = resumen_por_sucursal(rojas, locales)
    pendientes = rojas[rojas["estado"] != ESTADO_AUTORIZADA] if not rojas.empty else rojas
    t = _totales(pendientes)
    n_aut = int((rojas["estado"] == ESTADO_AUTORIZADA).sum()) if not rojas.empty else 0

    # --- encabezado
    pdf.set_font("Helvetica", "B", 17)
    pdf.set_text_color(*AZUL)
    pdf.cell(0, 8, _t("Diferencias por sucursal"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 6, _t(f"{titulo_periodo}  ({desde:%d/%m/%Y} al {hasta:%d/%m/%Y})"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GRIS)
    pdf.cell(
        0, 5,
        _t(f"Generado el {generado:%d/%m/%Y %H:%M}  ·  Diferencias de más de S/ {UMBRAL_VERDE:g}  ·  "
           + ("Incluye las ya autorizadas" if incluir_autorizadas else "Solo las que siguen sin autorizar")),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(2)

    # --- cifras clave
    ancho = (297 - 20 - 3 * 4) / 4
    cifras = [
        ("Pendientes", f"{t['n']}", ROJO if t["n"] else (30, 130, 60)),
        ("Faltantes (S/)", _soles(t["faltantes"]), ROJO),
        ("Sobrantes (S/)", _soles(t["sobrantes"]), (30, 90, 160)),
        ("Neto pendiente (S/)", _soles(t["neto"], True), ROJO if t["neto"] < 0 else (30, 90, 160)),
    ]
    y0 = pdf.get_y()
    for i, (rotulo, valor, color) in enumerate(cifras):
        x = 10 + i * (ancho + 4)
        pdf.set_xy(x, y0)
        pdf.set_fill_color(242, 245, 250)
        pdf.rect(x, y0, ancho, 15, style="F")
        pdf.set_xy(x + 2, y0 + 1.5)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*GRIS)
        pdf.cell(ancho - 4, 4, _t(rotulo))
        pdf.set_xy(x + 2, y0 + 6.5)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*color)
        pdf.cell(ancho - 4, 7, _t(valor))
    pdf.set_xy(10, y0 + 18)
    pdf.set_text_color(0, 0, 0)
    if n_aut:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*GRIS)
        pdf.cell(0, 4, _t(f"Ya autorizadas en el periodo: {n_aut} (S/ {_soles(rojas[rojas['estado'] == ESTADO_AUTORIZADA]['diferencia'].sum(), True)}). "
                          + ("Van marcadas en el detalle." if incluir_autorizadas else "No se listan: actívalo al generar el reporte para verlas.")),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    def _titulo(texto: str) -> None:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*AZUL)
        pdf.cell(0, 6, _t(texto), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    # --- resumen por sucursal
    _titulo("Resumen por sucursal")
    pdf.set_font("Helvetica", "", 8.5)
    with pdf.table(
        col_widths=(30, 12, 16, 16, 18, 14, 18), text_align=("LEFT", "CENTER", "RIGHT", "RIGHT", "RIGHT", "CENTER", "RIGHT"),
        headings_style=ENCABEZADO, line_height=5.2, width=150, align="LEFT",
    ) as tabla:
        fila = tabla.row()
        for h in ("Sucursal", "Pend.", "Faltantes", "Sobrantes", "Neto (S/)", "Autor.", "Autorizado"):
            fila.cell(h)
        for _, r in resumen.iterrows():
            fila = tabla.row()
            fila.cell(_t(r["local"]))
            fila.cell(str(r["pendientes"]) if r["pendientes"] else "-", style=FontFace(color=ROJO if r["pendientes"] else (30, 130, 60), emphasis="BOLD"))
            fila.cell(_soles(r["faltantes"]) if r["faltantes"] else "-", style=FontFace(color=ROJO))
            fila.cell(_soles(r["sobrantes"]) if r["sobrantes"] else "-", style=FontFace(color=(30, 90, 160)))
            fila.cell(_soles(r["neto"], True) if r["pendientes"] else "-", style=FontFace(emphasis="BOLD"))
            fila.cell(str(r["autorizadas"]) if r["autorizadas"] else "-")
            fila.cell(_soles(r["monto_autorizado"], True) if r["autorizadas"] else "-")
        fila = tabla.row()
        fila.cell("TOTAL", style=FontFace(emphasis="BOLD"))
        fila.cell(str(t["n"]), style=FontFace(emphasis="BOLD"))
        fila.cell(_soles(t["faltantes"]), style=FontFace(emphasis="BOLD", color=ROJO))
        fila.cell(_soles(t["sobrantes"]), style=FontFace(emphasis="BOLD", color=(30, 90, 160)))
        fila.cell(_soles(t["neto"], True), style=FontFace(emphasis="BOLD"))
        fila.cell(str(n_aut), style=FontFace(emphasis="BOLD"))
        fila.cell(_soles(rojas[rojas["estado"] == ESTADO_AUTORIZADA]["diferencia"].sum() if n_aut else 0, True), style=FontFace(emphasis="BOLD"))

    # --- por trabajador (lo pendiente)
    por_persona = resumen_por_persona(rojas)
    if not por_persona.empty:
        _titulo("Por trabajador (solo lo pendiente)")
        pdf.set_font("Helvetica", "", 8.5)
        with pdf.table(
            col_widths=(40, 11, 13, 16, 16, 18), text_align=("LEFT", "CENTER", "CENTER", "RIGHT", "RIGHT", "RIGHT"),
            headings_style=ENCABEZADO, line_height=5.2, width=150, align="LEFT",
        ) as tabla:
            fila = tabla.row()
            for h in ("Trabajador", "Cortes", "Entregas", "Faltantes", "Sobrantes", "Neto (S/)"):
                fila.cell(h)
            for _, r in por_persona.iterrows():
                fila = tabla.row()
                fila.cell(_t(r["persona"]))
                fila.cell(str(r["cortes"]))
                fila.cell(str(r["entregas"]))
                fila.cell(_soles(r["faltantes"]) if r["faltantes"] else "-", style=FontFace(color=ROJO))
                fila.cell(_soles(r["sobrantes"]) if r["sobrantes"] else "-", style=FontFace(color=(30, 90, 160)))
                fila.cell(_soles(r["neto"], True), style=FontFace(emphasis="BOLD"))

    # --- detalle por sucursal
    detalle = rojas if incluir_autorizadas else pendientes
    if detalle is None or detalle.empty:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(30, 130, 60)
        pdf.cell(0, 8, _t("No hay diferencias pendientes en este periodo."), new_x="LMARGIN", new_y="NEXT")
    else:
        if pdf.will_page_break(45):
            pdf.add_page()
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*AZUL)
        pdf.cell(0, 8, _t("Detalle por sucursal"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*GRIS)
        pdf.multi_cell(0, 4, _t("El corte se le carga a quien abrió el turno; la entrega de caja (entre un Cierre y la siguiente Apertura), a quien cerró. "
                                "Conformidad: si el trabajador ya vio y aceptó la diferencia."), new_x="LMARGIN", new_y="NEXT")
        for local, g in detalle.groupby("local", sort=True):
            if pdf.will_page_break(30):  # el titulo de una sucursal no se queda solo al final de una hoja
                pdf.add_page()
            tg = _totales(g[g["estado"] != ESTADO_AUTORIZADA])
            _titulo(f"{local}  -  {len(g)} diferencia(s)  ·  pendiente: faltantes {_soles(tg['faltantes'])} / sobrantes {_soles(tg['sobrantes'])} / neto {_soles(tg['neto'], True)}")
            pdf.set_font("Helvetica", "", 7.5)
            with pdf.table(
                col_widths=(14, 9, 16, 12, 34, 44, 15, 16, 17, 52),
                text_align=("LEFT", "LEFT", "LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "CENTER", "CENTER", "LEFT"),
                headings_style=ENCABEZADO, line_height=4.4,
            ) as tabla:
                fila = tabla.row()
                for h in ("Fecha", "Hora", "Tipo", "Turno", "Persona", "Detalle", "Dif. (S/)", "Estado", "Conformidad", "Autorización / observaciones"):
                    fila.cell(h)
                for _, r in g.iterrows():
                    color_estado = {ESTADO_PENDIENTE: ROJO, ESTADO_PARCIAL: NARANJA}.get(r["estado"], (30, 90, 160))
                    if r["estado"] == ESTADO_AUTORIZADA:
                        extra = f"Autorizó: {r['autorizo'] or '-'}" + (f" - {r['motivo']}" if r["motivo"] else "")
                    elif r["estado"] == ESTADO_PARCIAL:
                        extra = f"Ajuste parcial {_soles(r['ajuste'], True)}; sin explicar {_soles(r['restante'], True)}"
                    else:
                        extra = r["observaciones"]
                    if r["estado"] != ESTADO_AUTORIZADA and r["observaciones"] and r["estado"] == ESTADO_PARCIAL:
                        extra += f" | {r['observaciones']}"
                    fila = tabla.row()
                    fila.cell(f"{r['fecha']:%d/%m/%y}")
                    fila.cell(_t(r["hora"]))
                    fila.cell("Corte" if r["tipo"] == "Corte" else "Entrega")
                    fila.cell(_t(r["turno"]))
                    fila.cell(_t(r["persona"]))
                    fila.cell(_t(r["detalle"]))
                    fila.cell(_soles(r["diferencia"], True), style=FontFace(emphasis="BOLD", color=ROJO if r["diferencia"] < 0 else (30, 90, 160)))
                    fila.cell(_t(r["estado"]), style=FontFace(emphasis="BOLD", color=color_estado))
                    fila.cell(_t(r["conformidad"]))
                    fila.cell(_t(extra))
    return bytes(pdf.output())
