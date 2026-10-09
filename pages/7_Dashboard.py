"""
pages/7_Dashboard.py
---------------------
Vista consolidada de TODOS los locales, solo para uso interno (dueno /
administracion). Aqui se responde: "como van mis 6 agentes", "quien tiene
el fondo bajo ahora mismo", "que dias se mueve mas".

NOTA DE SEGURIDAD: esta app no tiene un sistema de usuarios real (eso es
mucho mas trabajo). Para que el personal de tienda no vea esta pagina,
le ponemos un PIN simple guardado en secrets.toml. No es seguridad de
nivel bancario, pero alcanza para "que no cualquiera con el link mire el
consolidado".
"""

from datetime import timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

import sheets_utils as sh

st.set_page_config(page_title="Dashboard - Agente BCP", page_icon="📊", layout="wide")
sh.aplicar_estilo()
st.title("📊 Dashboard consolidado")

# ---------------------------------------------------------------------
# PIN de acceso (opcional: si no configuras dashboard_pin en secrets,
# la pagina queda abierta para cualquiera con el link)
# ---------------------------------------------------------------------
pin_configurado = st.secrets.get("dashboard_pin")
if pin_configurado:
    if "dashboard_autenticado" not in st.session_state:
        st.session_state["dashboard_autenticado"] = False

    if not st.session_state["dashboard_autenticado"]:
        pin_ingresado = st.text_input("PIN de acceso", type="password")
        if pin_ingresado == pin_configurado:
            st.session_state["dashboard_autenticado"] = True
            st.rerun()
        elif pin_ingresado:
            st.error("PIN incorrecto.")
        st.stop()

# ---------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------
df = sh.get_registros_df()
config_df = sh.get_config_df()

if df.empty:
    st.info("Todavia no hay registros. Apenas se guarde el primero, aparecera aqui.")
    st.stop()

# --- Filtros ---
with st.sidebar:
    st.header("Filtros")
    locales_sel = st.multiselect(
        "Locales", options=sorted(df["local"].unique()), default=list(df["local"].unique())
    )
    turnos_disponibles = sorted(df["turno"].dropna().unique()) if "turno" in df.columns else []
    turnos_sel = st.multiselect(
        "Turno", options=turnos_disponibles, default=turnos_disponibles
    )
    fecha_min, fecha_max = df["fecha"].min(), df["fecha"].max()
    rango = st.date_input(
        "Rango de fechas",
        value=(max(fecha_min, sh.hoy_local() - timedelta(days=30)), fecha_max),
        min_value=fecha_min,
        max_value=fecha_max,
    )

if isinstance(rango, tuple) and len(rango) == 2:
    desde, hasta = rango
else:
    desde = hasta = rango

df_filtrado = df[
    df["local"].isin(locales_sel)
    & df["turno"].isin(turnos_sel)
    & df["fecha"].between(desde, hasta)
].sort_values("timestamp", ascending=False)

# Personal que registro algo en cada local en los ultimos 60 dias (para
# elegir quien "repuso" en un ajuste): solo gente de ESE local y reciente,
# no todo el historial de todos los locales.
_recientes = df[df["fecha"] >= sh.hoy_local() - timedelta(days=60)]
nombres_por_local = {
    loc: sorted(n for n in g["nombre"].unique() if str(n).strip())
    for loc, g in _recientes.groupby("local")
}

# Registro por id (para el "campo por campo" de abajo).
_registros_por_id = df.drop_duplicates("id").set_index("id")
_COLS_COMPARA = [col for _, col, _ in sh.DENOMINACIONES]


def _num(valor):
    x = pd.to_numeric(valor, errors="coerce")
    return 0.0 if pd.isna(x) else float(x)


def _comparar_par(row_izq, row_der, label_izq="Apertura", label_der="Cierre"):
    """Tabla comparando dos registros campo por campo (cada denominación +
    efectivo + tarjeta + total) + pistas de 'esto huele a error al
    registrar'. La usan la Continuidad entre días y el detalle por persona.
    La 'Diferencia' es (der - izq)."""
    filas = []
    campos = [(f"S/ {val:g}", col) for _e, col, val in sh.DENOMINACIONES]
    campos += [("Efectivo", "efectivo"), ("Tarjeta", "tarjeta"), ("Total", "total")]
    for etiqueta, col in campos:
        vi, vd = _num(row_izq.get(col)), _num(row_der.get(col))
        filas.append({"Campo": etiqueta, label_izq: vi, label_der: vd, "Diferencia": vd - vi})
    st.dataframe(sh.arrow_safe(pd.DataFrame(filas)), width="stretch", hide_index=True)

    obs_i = str(row_izq.get("observaciones", "") or "").strip()
    obs_d = str(row_der.get("observaciones", "") or "").strip()
    if obs_i:
        st.caption(f"📝 Observación en «{label_izq}»: {obs_i}")
    if obs_d:
        st.caption(f"📝 Observación en «{label_der}»: {obs_d}")

    n_i = str(row_izq.get("nombre", "")).strip() or "—"
    n_d = str(row_der.get("nombre", "")).strip() or "—"
    st.caption(f"Registró el «{label_izq}»: **{n_i}**  ·  el «{label_der}»: **{n_d}**")

    pistas = []
    ti, td = _num(row_izq.get("tarjeta")), _num(row_der.get("tarjeta"))
    if ti == 0 and td > 0:
        pistas.append(f"El **«{label_izq}» no tiene monto en Tarjeta** (quedó en 0).")
    if td == 0 and ti > 0:
        pistas.append(f"El **«{label_der}» no tiene monto en Tarjeta** (quedó en 0).")
    if _num(row_izq.get("total")) == 0:
        pistas.append(f"El **«{label_izq}» quedó en 0** — ¿no se registró el conteo?")
    if _num(row_der.get("total")) == 0:
        pistas.append(f"El **«{label_der}» quedó en 0** — ¿no se registró el conteo?")
    diferencia_total = _num(row_der.get("total")) - _num(row_izq.get("total"))
    for _e, col, val in sh.DENOMINACIONES:
        salto = _num(row_der.get(col)) - _num(row_izq.get(col))
        if abs(salto) >= 100 and abs(salto - diferencia_total) < 1:
            pistas.append(
                f"La denominación **S/ {val:g}** pasó de {_num(row_izq.get(col)):,.0f} a "
                f"{_num(row_der.get(col)):,.0f} y eso explica casi toda la diferencia → "
                f"probable error al escribir el monto."
            )
    if pistas:
        for p in pistas:
            st.warning(p)
    else:
        st.caption(
            "Sin señales obvias de error al registrar. Si aun así no cuadra: "
            "conteo físico mal hecho, o un retiro/ingreso de efectivo no anotado."
        )

# ---------------------------------------------------------------------
# Pestañas: el dashboard tenia todo en una sola pagina larga (12
# secciones seguidas) y se volvio dificil de navegar. Se agrupa en
# pestañas por tipo de pregunta que responde, sin tocar la logica de
# cada seccion -- el orden interno del codigo se mantiene igual (varias
# secciones reusan variables calculadas por la de arriba, por ejemplo
# "Cuadre por turno" calcula `cortes` y "Acumulado por persona" lo usa).
# ---------------------------------------------------------------------
(
    tab_resumen, tab_cuadre, tab_liquidacion, tab_sucursales, tab_operaciones,
    tab_comisiones, tab_incentivos, tab_registros, tab_personal,
) = st.tabs(
    [
        "🏠 Resumen", "🔍 Cuadre", "🧾 Liquidación", "🏪 Sucursales", "📈 Operaciones",
        "💰 Comisiones", "⭐ Incentivos", "🗂️ Registros", "👥 Personal",
    ]
)

with tab_resumen:
    # -------------------------------------------------------------
    # Alertas de fondo bajo (usa el ULTIMO cierre de cada local, sin
    # importar el filtro de fecha, porque queremos saber la situacion HOY)
    #
    # IMPORTANTE: el "fondo" del agente rota entre efectivo y tarjeta segun
    # las operaciones del dia (si entra mucho efectivo, la tarjeta baja, y
    # viceversa). Por eso la alerta compara el TOTAL (efectivo + tarjeta),
    # no solo el efectivo -- comparar solo efectivo dispararia alertas
    # falsas cuando el fondo simplemente se movio hacia el lado tarjeta.
    # -------------------------------------------------------------
    st.subheader("🚨 Alertas de fondo")

    # ultimo_registro_por_local: el registro mas reciente de cada local,
    # sea Apertura o Cierre -- lo usan tanto la alerta como el KPI de
    # "Fondo total actual" mas abajo. Si alguien acaba de abrir con plata
    # nueva agregada, eso ya es real y debe contar, sin esperar a que
    # cierren.
    ultimo_registro_por_local = df.sort_values("timestamp").groupby("local").tail(1).set_index("local")

    # Se muestran TODOS los locales (no solo los que estan bajos), asi de
    # un vistazo se ve el fondo de cada uno -- en rojo los que estan bajo
    # el minimo, en verde el resto. Antes un local por encima del minimo
    # simplemente no aparecia por ningun lado en esta lista.
    estado_locales = []
    for _, fila in config_df.iterrows():
        local = fila["local"]
        fondo_minimo = fila["fondo_minimo"]
        if local not in ultimo_registro_por_local.index:
            continue
        fondo_actual = ultimo_registro_por_local.loc[local, "total"]
        if pd.isna(fondo_actual):
            continue
        bajo_minimo = fondo_actual < fondo_minimo
        estado_locales.append((local, fondo_actual, fondo_minimo, bajo_minimo))

    # Los que estan bajos primero (lo mas urgente arriba).
    estado_locales.sort(key=lambda x: x[3], reverse=True)

    for local, fondo_actual, fondo_minimo, bajo_minimo in estado_locales:
        detalle_ultimo = ""
        ur = ultimo_registro_por_local.loc[local]
        hora_ur = ""
        if pd.notna(ur.get("timestamp")):
            hora_ur = pd.to_datetime(ur["timestamp"]).strftime("%H:%M")
        nombre_ur = str(ur.get("nombre", "")).strip() or "—"
        detalle_ultimo = (
            f"  \nÚltimo corte: **{ur.get('tipo', '')}** por **{nombre_ur}** "
            f"— {ur.get('fecha', '')} {hora_ur}"
        )
        mensaje = (
            f"**{local}**: fondo total en S/ {fondo_actual:,.2f} "
            f"(minimo configurado: S/ {fondo_minimo:,.2f}){detalle_ultimo}"
        )
        if bajo_minimo:
            st.error(mensaje)
        else:
            st.success(mensaje)

    if not estado_locales:
        st.caption("Todavía no hay registros para calcular el fondo de ningún local.")

    st.caption(
        "El fondo minimo de cada local se edita directamente en la hoja "
        "'Config' del Google Sheet, sin tocar codigo."
    )

    # -------------------------------------------------------------
    # Turnos que debieron cerrar y no cerraron (posible olvido)
    # -------------------------------------------------------------
    # La app no tiene un horario de turno configurado (Config solo trae
    # fondo_minimo/pin/etc.), asi que se usa un umbral generico: un turno
    # cuya Apertura lleva mas de UMBRAL_HORAS_TURNO_ABIERTO horas sin su
    # Cierre probablemente ya no sigue en curso -- se quedo sin cerrar por
    # olvido. Esto tambien atrapa turnos abiertos de un dia anterior (las
    # horas transcurridas ya son varias decenas).
    UMBRAL_HORAS_TURNO_ABIERTO = 9

    ultimo_por_turno = (
        df[df["local"].isin(locales_sel) & (df["fecha"] >= sh.hoy_local() - timedelta(days=1))]
        .sort_values("timestamp")
        .groupby(["local", "fecha", "turno"])
        .tail(1)
    )
    turnos_abiertos = ultimo_por_turno[ultimo_por_turno["tipo"] == "Apertura"].copy()
    if not turnos_abiertos.empty:
        _ahora_naive = sh.ahora_local().replace(tzinfo=None)
        turnos_abiertos["horas_abierto"] = (
            _ahora_naive - turnos_abiertos["timestamp"]
        ).dt.total_seconds() / 3600
        turnos_abiertos = turnos_abiertos[
            turnos_abiertos["horas_abierto"] >= UMBRAL_HORAS_TURNO_ABIERTO
        ].sort_values("horas_abierto", ascending=False)

    if not turnos_abiertos.empty:
        st.subheader("⏰ Turnos sin cerrar")
        for _, t in turnos_abiertos.iterrows():
            hora_ap = t["timestamp"].strftime("%H:%M") if pd.notna(t["timestamp"]) else ""
            st.warning(
                f"**{t['local']}** · turno **{t['turno']}** del {t['fecha']}: "
                f"Apertura de **{t['nombre']}** a las {hora_ap} "
                f"(hace {t['horas_abierto']:.0f} horas) sin Cierre registrado."
            )
        st.caption(
            "Puede ser que se olvidaron de registrar el Cierre, o que el "
            "Cierre se hizo pero no se guardo bien -- conviene revisar con "
            "el local."
        )

    # -------------------------------------------------------------
    # KPIs rapidos
    # -------------------------------------------------------------
    col1, col2, col3 = st.columns(3)
    col1.metric("Registros en el rango", len(df_filtrado))

    # Fondo total AHORA = suma del ULTIMO REGISTRO (Apertura o Cierre, el
    # que sea mas reciente) de cada local -- misma logica que Alertas de
    # fondo, mas arriba: si alguien acaba de abrir con plata agregada,
    # eso ya es real y debe contar, sin esperar a que cierren. Usa
    # ultimo_registro_por_local, sin el filtro de fechas, pero
    # respetando el filtro de locales.
    fondo_actual_total = ultimo_registro_por_local.loc[
        ultimo_registro_por_local.index.isin(locales_sel), "total"
    ].sum()
    col2.metric(
        "Fondo total actual (último registro de cada local)",
        f"S/ {fondo_actual_total:,.2f}",
        help="Suma del total (efectivo + tarjeta) del último registro (Apertura o Cierre) de cada local seleccionado.",
    )
    col3.metric(
        "Operaciones totales",
        int(df_filtrado["num_operaciones"].fillna(0).sum()),
    )

with tab_operaciones:
    # -------------------------------------------------------------
    # Graficos
    # -------------------------------------------------------------
    _cierres_ops = df_filtrado[df_filtrado["tipo"] == "Cierre"].copy()
    _cierres_ops["num_operaciones"] = pd.to_numeric(
        _cierres_ops["num_operaciones"], errors="coerce"
    ).fillna(0)

    st.subheader("🕐 Movimientos por turno")
    st.caption(
        "Operaciones registradas en los Cierres, agrupadas por turno "
        "(Mañana / Tarde), en el rango de fechas filtrado."
    )

    if _cierres_ops.empty or _cierres_ops["num_operaciones"].sum() == 0:
        st.caption("No hay operaciones registradas en el rango seleccionado.")
    else:
        orden_turnos = sorted(_cierres_ops["turno"].dropna().unique())
        ops_turno = (
            _cierres_ops.groupby("turno")["num_operaciones"]
            .sum()
            .reindex(orden_turnos)
            .fillna(0)
            .reset_index()
        )

        # KPIs grandes: promedio TOTAL DIARIO de operaciones por turno --
        # para cada dia se suma el total de operaciones de Mañana (todos
        # los locales seleccionados juntos) y el de Tarde, y despues se
        # promedia esa suma diaria a lo largo del rango. Asi "Promedio
        # diario" responde "en un dia cualquiera, cuanto se mueve en
        # Mañana vs en Tarde", no "cuanto mueve un local en un turno".
        ops_dia_turno = (
            _cierres_ops.groupby(["fecha", "turno"])["num_operaciones"].sum().reset_index()
        )
        prom_diario_manana = ops_dia_turno.loc[
            ops_dia_turno["turno"] == "Mañana", "num_operaciones"
        ].mean()
        prom_diario_tarde = ops_dia_turno.loc[
            ops_dia_turno["turno"] == "Tarde", "num_operaciones"
        ].mean()
        suma_promedios = (prom_diario_manana or 0) + (prom_diario_tarde or 0)
        pct_manana = prom_diario_manana / suma_promedios * 100 if suma_promedios else 0
        pct_tarde = prom_diario_tarde / suma_promedios * 100 if suma_promedios else 0

        col_km, col_kt = st.columns(2)
        col_km.metric(
            "Promedio diario — Mañana",
            f"{prom_diario_manana:,.0f}" if pd.notna(prom_diario_manana) else "—",
            f"{pct_manana:.0f}% del total diario" if suma_promedios else None,
        )
        col_kt.metric(
            "Promedio diario — Tarde",
            f"{prom_diario_tarde:,.0f}" if pd.notna(prom_diario_tarde) else "—",
            f"{pct_tarde:.0f}% del total diario" if suma_promedios else None,
        )

        fig_ops_turno = px.bar(
            ops_turno,
            x="turno",
            y="num_operaciones",
            labels={"turno": "Turno", "num_operaciones": "N° de operaciones"},
        )
        st.plotly_chart(fig_ops_turno, width="stretch")

        total_ops_turnos = ops_turno["num_operaciones"].sum()
        if total_ops_turnos:
            for _, fila_t in ops_turno.iterrows():
                pct = fila_t["num_operaciones"] / total_ops_turnos * 100
                st.caption(
                    f"**{fila_t['turno']}**: {int(fila_t['num_operaciones']):,} "
                    f"operaciones ({pct:.0f}% del total)."
                )

        with st.expander("Ver turno por local"):
            ops_turno_local = (
                _cierres_ops.groupby(["turno", "local"])["num_operaciones"].sum().reset_index()
            )
            fig_ops_turno_local = px.bar(
                ops_turno_local,
                x="turno",
                y="num_operaciones",
                color="local",
                barmode="group",
                labels={"turno": "Turno", "num_operaciones": "N° de operaciones", "local": "Local"},
            )
            st.plotly_chart(fig_ops_turno_local, width="stretch")

    st.subheader("📅 Operaciones por día")
    st.caption(
        "Número de operaciones registradas en los Cierres, día a día (suma de "
        "todos los locales seleccionados)."
    )

    if _cierres_ops.empty or _cierres_ops["num_operaciones"].sum() == 0:
        st.caption("No hay operaciones registradas en el rango seleccionado.")
    else:
        ops_dia = (
            _cierres_ops.groupby("fecha")["num_operaciones"].sum().rename_axis("fecha").reset_index()
        ).sort_values("fecha")
        fig_ops_dia = px.bar(
            ops_dia,
            x="fecha",
            y="num_operaciones",
            labels={"fecha": "Fecha", "num_operaciones": "N° de operaciones"},
        )
        fig_ops_dia.update_traces(hovertemplate="%{x}<br>%{y:,.0f} operaciones<extra></extra>")
        st.plotly_chart(fig_ops_dia, width="stretch")

        prom_dia = ops_dia["num_operaciones"].mean()
        mejor = ops_dia.loc[ops_dia["num_operaciones"].idxmax()]
        st.caption(
            f"Promedio: **{prom_dia:,.0f}** operaciones/día. "
            f"Día más alto: **{mejor['fecha']}** con **{int(mejor['num_operaciones']):,}**."
        )

        with st.expander("Ver por local"):
            ops_dia_local = (
                _cierres_ops.groupby(["fecha", "local"])["num_operaciones"].sum().reset_index()
            ).sort_values("fecha")
            fig_ops_local = px.bar(
                ops_dia_local,
                x="fecha",
                y="num_operaciones",
                color="local",
                barmode="group",
                labels={"fecha": "Fecha", "num_operaciones": "N° de operaciones", "local": "Local"},
            )
            st.plotly_chart(fig_ops_local, width="stretch")

    st.subheader("👥 Operaciones por persona")
    st.caption(
        "Operaciones atribuidas a quien registró el Cierre. En el rango de fechas "
        "filtrado."
    )

    if _cierres_ops.empty or _cierres_ops["num_operaciones"].sum() == 0:
        st.caption("No hay operaciones registradas en el rango seleccionado.")
    else:
        ops_persona = (
            _cierres_ops[_cierres_ops["nombre"].astype(str).str.strip() != ""]
            .groupby("nombre")["num_operaciones"]
            .agg(operaciones="sum", cierres="count")
            .reset_index()
            .sort_values("operaciones", ascending=False)
        )
        ops_persona["prom_por_cierre"] = (
            (ops_persona["operaciones"] / ops_persona["cierres"].replace(0, pd.NA))
            .fillna(0)
            .round()
            .astype(int)
        )
        st.dataframe(
            sh.arrow_safe(
                ops_persona.rename(
                    columns={
                        "nombre": "Persona",
                        "operaciones": "Operaciones",
                        "cierres": "Cierres",
                        "prom_por_cierre": "Prom. por cierre",
                    }
                )
            ),
            width="stretch",
            hide_index=True,
        )
        fig_ops_persona = px.bar(
            ops_persona,
            x="nombre",
            y="operaciones",
            labels={"nombre": "Persona", "operaciones": "N° de operaciones"},
        )
        st.plotly_chart(fig_ops_persona, width="stretch")

    st.subheader("📈 Movimientos por dia de la semana")

    dias_es = {
        "Monday": "Lunes",
        "Tuesday": "Martes",
        "Wednesday": "Miercoles",
        "Thursday": "Jueves",
        "Friday": "Viernes",
        "Saturday": "Sabado",
        "Sunday": "Domingo",
    }
    orden_dias = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]

    cierres_filtrados = df_filtrado[df_filtrado["tipo"] == "Cierre"].copy()
    if not cierres_filtrados.empty:
        cierres_filtrados["dia_semana"] = pd.to_datetime(cierres_filtrados["fecha"]).dt.day_name().map(dias_es)
        resumen_dias = (
            cierres_filtrados.groupby("dia_semana")["num_operaciones"]
            .sum()
            .reindex(orden_dias)
            .fillna(0)
            .reset_index()
        )
        fig_dias = px.bar(
            resumen_dias,
            x="dia_semana",
            y="num_operaciones",
            labels={"dia_semana": "Dia", "num_operaciones": "N° de operaciones"},
        )
        st.plotly_chart(fig_dias, width="stretch")
    else:
        st.caption("No hay cierres en el rango seleccionado para graficar.")

    st.subheader("📉 Evolucion del fondo total (efectivo + tarjeta) por local")
    st.caption(
        "Se grafica el TOTAL, no solo el efectivo, porque el fondo rota entre "
        "efectivo y tarjeta segun las operaciones del dia."
    )
    if not cierres_filtrados.empty:
        fig_evol = px.line(
            cierres_filtrados.sort_values("fecha"),
            x="fecha",
            y="total",
            color="local",
            markers=True,
            labels={"fecha": "Fecha", "total": "Fondo total al cierre (S/)"},
        )
        st.plotly_chart(fig_evol, width="stretch")

with tab_resumen:
    # -------------------------------------------------------------
    # Fondo CONSOLIDADO por dia: la sumatoria de todos los locales y como
    # va variando dia a dia. Para cada dia se toma el ULTIMO Cierre de cada
    # local ese dia; si un local no cerro ese dia, se arrastra su ultimo
    # cierre anterior (ffill). Asi la linea es el "cuanto dinero hay en
    # total" al cierre de cada dia, no un promedio ni una suma de flujos.
    # -------------------------------------------------------------
    st.subheader("📊 Fondo consolidado (todos los locales) por día")
    st.caption(
        "Suma del fondo total (efectivo + tarjeta) del último Cierre de cada "
        "local seleccionado, día a día. Si un local no cerró un día, se arrastra "
        "su cierre anterior."
    )

    cierres_sel = df[
        (df["tipo"] == "Cierre") & (df["local"].isin(locales_sel))
    ].sort_values("timestamp")

    if cierres_sel.empty:
        st.caption("Todavía no hay cierres para consolidar.")
    else:
        ultimo_del_dia = cierres_sel.groupby(["local", "fecha"], sort=False).tail(1)
        pivote = ultimo_del_dia.pivot(index="fecha", columns="local", values="total").sort_index()
        # Rango de días: del primer cierre hasta el final del filtro de fechas.
        dias = pd.date_range(pivote.index.min(), max(pivote.index.max(), hasta)).date
        pivote = pivote.reindex(dias).ffill()
        serie = pivote.sum(axis=1).rename_axis("fecha").reset_index(name="fondo_total")
        serie = serie[(serie["fecha"] >= desde) & (serie["fecha"] <= hasta)]
        if serie.empty:
            st.caption("No hay días con datos en el rango seleccionado.")
        else:
            fig_consol = px.area(
                serie,
                x="fecha",
                y="fondo_total",
                labels={"fecha": "Fecha", "fondo_total": "Fondo consolidado (S/)"},
            )
            fig_consol.update_traces(hovertemplate="%{x}<br>S/ %{y:,.2f}<extra></extra>")
            st.plotly_chart(fig_consol, width="stretch")
            ultimo_valor = serie.iloc[-1]["fondo_total"]
            primer_valor = serie.iloc[0]["fondo_total"]
            st.caption(
                f"En el rango: de S/ {primer_valor:,.2f} a S/ {ultimo_valor:,.2f} "
                f"(variación S/ {ultimo_valor - primer_valor:+,.2f})."
            )

with tab_cuadre:
    # -------------------------------------------------------------
    # Continuidad: CUALQUIER salto Cierre -> Apertura siguiente, por
    # local, en orden cronologico -- sea dentro del mismo turno (corte
    # parcial), entre Mañana y Tarde del mismo día, o entre un día y el
    # siguiente. El fondo debería quedar guardado de un salto a otro; si
    # no, alguien movió la caja entre medio (o hubo un error).
    #
    # Antes esto solo miraba el cruce entre días; "Cuadre por turno" (mas
    # abajo) ya revisa cada corte contra su propia Apertura, pero a
    # proposito deja pasar SIN avisar lo que cambia ENTRE cortes -- este
    # es justo el control que faltaba para ese hueco.
    # -------------------------------------------------------------
    st.subheader("🔗 Continuidad (Cierre → Apertura siguiente)")
    st.caption(
        "El fondo se queda guardado de un Cierre a la Apertura que le sigue, "
        "sea el mismo turno, el turno siguiente del mismo día, o el día "
        "siguiente. Si no coincide, alguien movió la caja entre medio (o "
        "hubo un error al registrar)."
    )


    def _semaforo_continuidad(diferencia: float) -> str:
        # Binario, sin estado intermedio: entre un salto y el siguiente no
        # hay operaciones que muevan el fondo por si solas, asi que
        # cualquier diferencia mayor a redondeos normales (S/1) ya se
        # marca como sospechosa.
        dif_abs = abs(diferencia)
        if dif_abs <= 1.0:
            return "✅ Coincide"
        return "🔴 Diferencia"


    # Retiros/ingresos que la administración ya autorizó (ver más abajo, se
    # registran desde esta misma sección). Se restan de la diferencia antes
    # de poner el semáforo -- así un retiro tuyo no sale como faltante.
    # Dos esquemas conviven (ver sheets_utils.COLUMNAS_AJUSTES):
    # - Nuevo (salto_id_cierre != ""): apunta a UN salto especifico.
    # - Viejo (turno=="" y salto_id_cierre==""): ajustes guardados antes
    #   de que existiera esta sección ampliada, por local+fecha -- solo
    #   pueden explicar saltos "Entre días" (lo unico que existia antes).
    ajustes_df = sh.get_ajustes_df()
    if ajustes_df.empty:
        ajustes_continuidad = ajustes_df
        ajuste_por_salto = {}
        ajuste_viejo_por_local_fecha = {}
    else:
        ajustes_continuidad = ajustes_df[ajustes_df["salto_id_cierre"] != ""]
        ajuste_por_salto = ajustes_continuidad.groupby("salto_id_cierre")["monto"].sum().to_dict()
        ajustes_viejo_estilo = ajustes_df[
            (ajustes_df["turno"] == "") & (ajustes_df["salto_id_cierre"] == "")
        ]
        ajuste_viejo_por_local_fecha = (
            ajustes_viejo_estilo.groupby(["local", "fecha"])["monto"].sum().to_dict()
        )

    registros_sel = df[df["local"].isin(locales_sel)]
    saltos = sh.calcular_saltos(registros_sel)

    filas_continuidad = []
    for _, s in saltos.iterrows():
        ajuste_nuevo = float(ajuste_por_salto.get(s["id_cierre"], 0.0))
        ajuste_viejo = 0.0
        if s["tipo_salto"] == "Entre días":
            ajuste_viejo = float(
                ajuste_viejo_por_local_fecha.get((s["local"], s["fecha_cierre"]), 0.0)
            )
        ajuste_total = ajuste_nuevo + ajuste_viejo
        diferencia = s["diferencia"]
        if pd.isna(diferencia):
            continue
        restante = diferencia - ajuste_total
        if ajuste_total != 0 and abs(restante) <= 1.0:
            estado = "🔷 Autorizado"
        else:
            estado = _semaforo_continuidad(restante)
        filas_continuidad.append(
            {
                "_fecha_cierre": s["fecha_cierre"],
                "_id_cierre": s["id_cierre"],
                "_id_apertura": s["id_apertura"],
                "_local": s["local"],
                "_ajuste_total": ajuste_total,
                "_restante": restante,
                "Local": s["local"],
                "Tipo": s["tipo_salto"],
                "Cierre": f"{s['fecha_cierre']} {s['turno_cierre']} {s['hora_cierre']} — {s['nombre_cierre']}",
                "Apertura": f"{s['fecha_apertura']} {s['turno_apertura']} {s['hora_apertura']} — {s['nombre_apertura']}",
                "Cierre (S/)": round(s["cierre"], 2) if pd.notna(s["cierre"]) else None,
                "Apertura (S/)": round(s["apertura"], 2) if pd.notna(s["apertura"]) else None,
                "Diferencia (S/)": f"{diferencia:+,.2f}",
                "Ajuste autorizado (S/)": f"{ajuste_total:+,.2f}" if ajuste_total else "",
                "Restante (S/)": f"{restante:+,.2f}",
                "Estado": estado,
            }
        )

    cont_df = pd.DataFrame(filas_continuidad)
    if cont_df.empty:
        st.caption("Todavía no hay saltos Cierre → Apertura para comparar en el rango seleccionado.")
    else:
        cont_df = cont_df[
            (cont_df["_fecha_cierre"] >= desde) & (cont_df["_fecha_cierre"] <= hasta)
        ].sort_values("_fecha_cierre", ascending=False)
        if cont_df.empty:
            st.caption("No hay comparaciones en el rango de fechas seleccionado.")
        else:
            con_diferencia = int((cont_df["Estado"] == "🔴 Diferencia").sum())
            if con_diferencia:
                st.error(
                    f"⚠️ {con_diferencia} caso(s) donde el fondo cambió entre un Cierre y "
                    f"la Apertura siguiente, sin autorización registrada."
                )
            else:
                st.success("Todos los cierres coinciden (o están autorizados) con la apertura siguiente. 👍")
            st.dataframe(
                sh.arrow_safe(
                    cont_df.drop(
                        columns=["_fecha_cierre", "_id_cierre", "_id_apertura", "_local", "_ajuste_total", "_restante"]
                    )
                ),
                width="stretch",
                hide_index=True,
            )
            st.caption(
                "🔷 Autorizado = tiene un retiro/ingreso registrado por administración que explica "
                "la diferencia. Ábrelo para ver el motivo o registrar uno nuevo. **Tipo** dice si el "
                "salto es dentro del mismo turno, entre Mañana y Tarde, o entre un día y el siguiente."
            )

            # Detalle campo por campo + registrar retiro/ingreso autorizado.
            sospechosos = cont_df[cont_df["Estado"] != "✅ Coincide"]
            for _, fila in sospechosos.iterrows():
                titulo = (
                    f"{fila['Local']} · {fila['Tipo']} · {fila['Cierre']} → {fila['Apertura']} · "
                    f"{fila['Diferencia (S/)']} · {fila['Estado']}"
                )
                with st.expander(titulo):
                    if (
                        fila["_id_cierre"] in _registros_por_id.index
                        and fila["_id_apertura"] in _registros_por_id.index
                    ):
                        st.caption(
                            "Compara el Cierre con la Apertura que le sigue. Deberían ser "
                            "idénticos; si un campo cambió, ahí está el problema (o fue un "
                            "retiro/ingreso de efectivo hecho a propósito entre medio)."
                        )
                        _comparar_par(
                            _registros_por_id.loc[fila["_id_cierre"]],
                            _registros_por_id.loc[fila["_id_apertura"]],
                            "Cierre",
                            "Apertura siguiente",
                        )
                    else:
                        st.caption("No se encontraron los dos registros para comparar.")

                    # Ajustes ya registrados para este salto especifico, si hay
                    # (nuevo esquema por id_cierre, mas el viejo por local+fecha
                    # si este salto es "Entre días").
                    clave_ajuste = f"salto|{fila['_id_cierre']}"
                    _candidatos = list(nombres_por_local.get(fila["_local"], []))
                    _quien_abrio = (
                        str(_registros_por_id.loc[fila["_id_apertura"], "nombre"])
                        if fila["_id_apertura"] in _registros_por_id.index
                        else ""
                    )
                    if _quien_abrio in _candidatos:
                        _candidatos.remove(_quien_abrio)
                        _candidatos.insert(0, _quien_abrio)
                    previos = pd.DataFrame()
                    if not ajustes_continuidad.empty:
                        previos = ajustes_continuidad[
                            ajustes_continuidad["salto_id_cierre"] == fila["_id_cierre"]
                        ]
                    if fila["Tipo"] == "Entre días" and not ajustes_df.empty:
                        previos_viejo = ajustes_df[
                            (ajustes_df["turno"] == "")
                            & (ajustes_df["salto_id_cierre"] == "")
                            & (ajustes_df["local"] == fila["_local"])
                            & (ajustes_df["fecha"] == fila["_fecha_cierre"])
                        ]
                        previos = pd.concat([previos, previos_viejo]) if not previos.empty else previos_viejo
                    if not previos.empty:
                        st.markdown("**Ajustes ya registrados para este salto:**")
                        for _, aj in previos.iterrows():
                            _repuso_txt = f" · repuso: {aj['repuso']}" if aj.get("repuso") else ""
                            st.caption(
                                f"S/ {aj['monto']:+,.2f} — {aj['motivo']} "
                                f"(autorizó: {aj['autorizado_por'] or '—'}){_repuso_txt}"
                            )
                            # Permite marcar/corregir quien repuso en un ajuste YA
                            # guardado (p. ej. uno hecho antes de que existiera el
                            # campo, o con la persona equivocada).
                            if aj["monto"] > 0 and aj.get("id"):
                                _opc = ["— Nadie —"] + _candidatos
                                if aj.get("repuso") and aj["repuso"] not in _opc:
                                    _opc.append(aj["repuso"])
                                _c1, _c2 = st.columns([3, 1])
                                _nuevo = _c1.selectbox(
                                    "Repuso (quién puso la plata)",
                                    _opc,
                                    index=_opc.index(aj["repuso"]) if aj.get("repuso") in _opc else 0,
                                    key=f"repuso_prev_{aj['id']}",
                                )
                                _c2.write("")
                                if _c2.button("Guardar repuso", key=f"repuso_prev_btn_{aj['id']}"):
                                    sh.actualizar_repuso_ajuste(
                                        aj["id"], "" if _nuevo == "— Nadie —" else _nuevo
                                    )
                                    st.success("Listo.")
                                    st.rerun()

                    # Si ya quedó "Autorizado" (el/los ajuste(s) ya registrados
                    # explican toda la diferencia), no tiene sentido seguir
                    # mostrando el formulario para registrar OTRO ajuste -- ya
                    # está resuelto. Solo se ofrece el formulario mientras
                    # falte explicar algo (🔴).
                    if fila["Estado"] != "🔷 Autorizado":
                        st.markdown("**Registrar retiro/ingreso autorizado por administración**")
                        st.caption(
                            "Esto queda guardado con motivo y quién lo autorizó, y se resta de la "
                            "diferencia de este salto específico en adelante."
                        )
                        col_monto, col_quien, col_repuso = st.columns(3)
                        monto_ajuste = col_monto.number_input(
                            "Monto (negativo = retiro, positivo = ingreso)",
                            value=round(fila["_restante"], 2),
                            step=10.0,
                            key=f"ajuste_monto_{clave_ajuste}",
                        )
                        autorizo = col_quien.text_input(
                            "Quién autoriza", key=f"ajuste_quien_{clave_ajuste}"
                        )
                        repuso_sel = col_repuso.selectbox(
                            "Repuso (quién puso la plata)",
                            ["— Nadie —"] + _candidatos,
                            key=f"ajuste_repuso_{clave_ajuste}",
                            help="Opcional. Si alguien puso plata de su bolsillo para cubrir la diferencia, "
                            "queda a su favor en la Liquidación.",
                        )
                        motivo_ajuste = st.text_area(
                            "Motivo", key=f"ajuste_motivo_{clave_ajuste}",
                            placeholder="Ej: retiro de efectivo para depósito en banco",
                        )
                        if st.button("✅ Registrar ajuste", key=f"ajuste_btn_{clave_ajuste}"):
                            if not motivo_ajuste.strip() or not autorizo.strip():
                                st.error("Completa quién autoriza y el motivo antes de guardar.")
                            elif repuso_sel != "— Nadie —" and monto_ajuste <= 0:
                                st.error("«Repuso» solo aplica a un ingreso: el monto debe ser positivo.")
                            else:
                                ahora_aj = sh.ahora_local()
                                sh.guardar_ajuste(
                                    {
                                        "id": sh.nuevo_id(),
                                        "timestamp": ahora_aj.replace(tzinfo=None).isoformat(timespec="seconds"),
                                        "local": fila["_local"],
                                        "fecha": fila["_fecha_cierre"].isoformat(),
                                        "monto": monto_ajuste,
                                        "motivo": motivo_ajuste.strip(),
                                        "autorizado_por": autorizo.strip(),
                                        "turno": "",
                                        "salto_id_cierre": fila["_id_cierre"],
                                        "repuso": "" if repuso_sel == "— Nadie —" else repuso_sel,
                                    }
                                )
                                st.success("Ajuste guardado.")
                                st.rerun()

    # -------------------------------------------------------------
    # Cuadre por turno (cortes Apertura -> Cierre)
    #
    # Un turno puede tener VARIOS cortes (cierres parciales: se cierra, se
    # retira/ingresa efectivo a proposito, se vuelve a abrir). Cada corte se
    # mide contra su propia Apertura, asi que lo que se mueve a proposito
    # entre cortes no ensucia el calculo. Ver cuadre.py.
    # -------------------------------------------------------------
    st.subheader("🔍 Cuadre por turno")
    st.caption(
        "Diferencia = Cierre − Apertura de cada corte (fondo total = efectivo + "
        "tarjeta). Un turno con cierres parciales tiene varios cortes; acá se "
        "muestra la **suma** de sus diferencias."
    )

    INDICE_TURNO = ["local", "fecha", "turno"]
    cortes = sh.calcular_cortes(df_filtrado, INDICE_TURNO)
    resumen = sh.resumen_turnos(cortes, INDICE_TURNO)
    resumen = resumen.sort_values(["fecha", "local", "turno"], ascending=[False, True, True])

    # Ajustes de UN turno especifico (turno != "", a diferencia de los que
    # usa Continuidad -- turno == "" con salto_id_cierre) ya autorizados
    # por administración: se restan de la diferencia de ESE turno antes
    # del semáforo. Si explican todo -> "🔷 Autorizado"; los estados de
    # secuencia (⚠️) no se tocan, esos son problemas estructurales, no de
    # monto.
    if ajustes_df.empty:
        ajustes_turno = ajustes_df
        ajuste_turno_por_clave = {}
    else:
        ajustes_turno = ajustes_df[ajustes_df["turno"] != ""]
        ajuste_turno_por_clave = (
            ajustes_turno.groupby(["local", "fecha", "turno"])["monto"].sum().to_dict()
        )


    def _con_ajuste_turno(fila):
        ajuste_total = float(
            ajuste_turno_por_clave.get((fila["local"], fila["fecha"], fila["turno"]), 0.0)
        )
        diferencia = fila["diferencia"]
        if ajuste_total == 0 or pd.isna(diferencia):
            return pd.Series(
                {"estado": fila["estado"], "_ajuste_total": ajuste_total,
                 "_restante": diferencia, "_explicado": False}
            )
        restante = diferencia - ajuste_total
        explicado = abs(restante) <= sh.UMBRAL_VERDE
        if str(fila["estado"]).startswith("⚠️ Revisar secuencia"):
            # El problema de secuencia (corte abierto / Cierre suelto) sigue
            # ahi y se sigue mostrando, pero el MONTO si puede quedar
            # autorizado (_explicado) para que no se le cargue a nadie.
            nuevo_estado = fila["estado"]
        elif explicado:
            nuevo_estado = "🔷 Autorizado"
        else:
            nuevo_estado = "🔴 Diferencia"
        return pd.Series(
            {"estado": nuevo_estado, "_ajuste_total": ajuste_total,
             "_restante": restante, "_explicado": explicado}
        )


    _ajustado_turno = resumen.apply(_con_ajuste_turno, axis=1)
    resumen["estado"] = _ajustado_turno["estado"]
    resumen["_ajuste_total"] = _ajustado_turno["_ajuste_total"]
    resumen["_restante"] = _ajustado_turno["_restante"]
    resumen["_explicado"] = _ajustado_turno["_explicado"].astype(bool)

    # Turnos ya "🔷 Autorizado": se pisa el Estado de los cortes de ese
    # turno que YA estaban en "🔴 Diferencia" (en "cortes" mismo, asi que
    # se ve igual en "Ver corte por corte" y en el detalle por persona,
    # mas abajo). Un corte que ya estaba "✅ Cuadrado" por si solo NO se
    # toca -- el ajuste se calcula sobre la SUMA del turno, y si el turno
    # tiene varios cortes, marcar el que ya estaba bien como "Autorizado"
    # daria a entender que tenia algo que explicar cuando no era asi.
    # Tambien los de "⚠️ Cerró otro nombre" que tengan una diferencia real
    # (> S/1): ese aviso solo dice que cerro otra persona, pero la plata
    # que falta/sobra igual necesita (y ahora puede tener) su ajuste.
    _turnos_autorizados = set(
        resumen.loc[resumen["_explicado"], ["local", "fecha", "turno"]]
        .itertuples(index=False, name=None)
    )
    if _turnos_autorizados:
        cortes.loc[
            [
                (l, f, t) in _turnos_autorizados
                and (
                    est == "🔴 Diferencia"
                    or (est.startswith("⚠️ Cerró otro nombre") and abs(dif) > sh.UMBRAL_VERDE)
                )
                for l, f, t, est, dif in zip(
                    cortes["local"], cortes["fecha"], cortes["turno"],
                    cortes["estado"], cortes["diferencia"].fillna(0),
                )
            ],
            "estado",
        ] = "🔷 Autorizado"

    st.dataframe(
        sh.arrow_safe(
            resumen.rename(
                columns={
                    "local": "Local",
                    "fecha": "Fecha",
                    "turno": "Turno",
                    "n_cortes": "Cortes",
                    "nombres": "Personas",
                    "diferencia_fmt": "Diferencia total (S/)",
                    "estado": "Estado",
                    "observaciones": "Observaciones",
                }
            ).drop(columns=["diferencia", "_ajuste_total", "_restante", "_explicado"])
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "**+** = sobró (el Cierre quedó por encima de la Apertura), **−** = faltó. "
        "🔴 Diferencia = más de S/1 sin explicar. "
        "⚠️ Revisar secuencia = al turno le falta un Cierre o hay un Cierre sin Apertura."
    )

    with st.expander("Ver corte por corte"):
        if cortes.empty:
            st.caption("No hay cortes en el rango seleccionado.")
        else:
            cortes_orden = cortes.sort_values(
                ["fecha", "local", "turno", "corte"], ascending=[False, True, True, True]
            )
            st.dataframe(
                sh.arrow_safe(
                    cortes_orden.rename(
                        columns={
                            "local": "Local",
                            "fecha": "Fecha",
                            "turno": "Turno",
                            "corte": "Corte",
                            "nombre": "Abrió",
                            "nombre_cierre": "Cerró",
                            "hora_apertura": "Hora ap.",
                            "hora_cierre": "Hora cie.",
                            "apertura": "Apertura (S/)",
                            "cierre": "Cierre (S/)",
                            "diferencia_fmt": "Diferencia (S/)",
                            "estado": "Estado",
                            "motivo": "Motivo (otro nombre)",
                        }
                    ).drop(columns=["diferencia", "observaciones"])
                ),
                width="stretch",
                hide_index=True,
            )

    # Registrar/ver ajustes de un turno especifico (distinto de los de
    # Continuidad, que apuntan a un salto por id_cierre). Solo para los que
    # aun no cuadran ni estan ya autorizados.
    # Entra TODO turno con una diferencia real sin explicar (> S/1), tenga el
    # estado que tenga (⚠️ Cerró otro nombre, ⚠️ Revisar secuencia, 🔴):
    # antes los ⚠️ quedaban fuera y esa plata no se podia autorizar.
    _sospechosos_turno = resumen[
        ~resumen["estado"].isin(["✅ Cuadrado", "🔷 Autorizado"])
        & (resumen["_restante"].fillna(0).abs() > sh.UMBRAL_VERDE)
    ]
    if not _sospechosos_turno.empty:
        st.markdown("**Registrar retiro/ingreso autorizado de un turno**")
        for _, fila in _sospechosos_turno.iterrows():
            titulo = (
                f"{fila['local']} · {fila['fecha']} · {fila['turno']} · {fila['nombres']} · "
                f"{fila['diferencia_fmt']} · {fila['estado']}"
            )
            with st.expander(titulo):
                clave_t = f"turno|{fila['local']}|{fila['fecha']}|{fila['turno']}"
                if not ajustes_turno.empty:
                    previos_t = ajustes_turno[
                        (ajustes_turno["local"] == fila["local"])
                        & (ajustes_turno["fecha"] == fila["fecha"])
                        & (ajustes_turno["turno"] == fila["turno"])
                    ]
                    if not previos_t.empty:
                        st.markdown("**Ajustes ya registrados para este turno:**")
                        for _, aj in previos_t.iterrows():
                            st.caption(
                                f"S/ {aj['monto']:+,.2f} — {aj['motivo']} "
                                f"(autorizó: {aj['autorizado_por'] or '—'})"
                            )
                col_m, col_q = st.columns(2)
                monto_t = col_m.number_input(
                    "Monto (negativo = retiro, positivo = ingreso)",
                    value=round(float(fila["_restante"]), 2),
                    step=10.0,
                    key=f"aj_t_monto_{clave_t}",
                )
                quien_t = col_q.text_input("Quién autoriza", key=f"aj_t_quien_{clave_t}")
                motivo_t = st.text_area(
                    "Motivo",
                    key=f"aj_t_motivo_{clave_t}",
                    placeholder="Ej: retiro de efectivo a media tarde",
                )
                if st.button("✅ Registrar ajuste", key=f"aj_t_btn_{clave_t}"):
                    if not motivo_t.strip() or not quien_t.strip():
                        st.error("Completa quién autoriza y el motivo antes de guardar.")
                    else:
                        ahora_t = sh.ahora_local()
                        sh.guardar_ajuste(
                            {
                                "id": sh.nuevo_id(),
                                "timestamp": ahora_t.replace(tzinfo=None).isoformat(timespec="seconds"),
                                "local": fila["local"],
                                "fecha": fila["fecha"].isoformat(),
                                "monto": monto_t,
                                "motivo": motivo_t.strip(),
                                "autorizado_por": quien_t.strip(),
                                "turno": fila["turno"],
                            }
                        )
                        st.success("Ajuste guardado.")
                        st.rerun()

    # Para el grafico dejamos fuera los turnos con la secuencia rota (su suma
    # de diferencias es parcial y engaña); los de "Cerró otro nombre" sí van.
    turnos_completos = resumen[~resumen["estado"].astype(str).str.contains("secuencia")]
    if not turnos_completos.empty:
        fig_dif = px.bar(
            turnos_completos.sort_values("fecha"),
            x="fecha",
            y="diferencia",
            color="local",
            barmode="group",
            labels={"fecha": "Fecha", "diferencia": "Diferencia total del turno (S/)"},
        )
        st.plotly_chart(fig_dif, width="stretch")

    # -------------------------------------------------------------
    # Acumulado de diferencias por persona
    #
    # Cada corte se le atribuye a quien lo ABRIO (si el Cierre quedo a otro
    # nombre, igual va a quien abrio). Aca se suma, en el rango de fechas
    # filtrado, cuanto descuadre acumula cada persona -- para ver de un
    # vistazo si alguien viene arrastrando diferencias.
    #
    # Los turnos que ya quedaron "🔷 Autorizado" (la diferencia tiene un
    # retiro/ingreso registrado por administración que la explica) NO
    # entran a esta suma -- ya no es un descuadre real, y sumarlo aca
    # haría ver a la persona como si arrastrara una diferencia que en
    # realidad ya está resuelta.
    # -------------------------------------------------------------
    st.subheader("👤 Acumulado de diferencias por persona")
    st.caption(
        "En el rango de fechas filtrado. La diferencia de cada corte se le "
        "atribuye a quien abrió; la diferencia en la entrega se le atribuye "
        "a quien cerró (ver Continuidad, más arriba). No incluye lo ya "
        "'🔷 Autorizado'. Ordenado por descuadre total (sin importar el signo)."
    )

    # _turnos_autorizados ya se calculó arriba, en Cuadre por turno (se
    # reusa aca para no recalcularlo).
    if _turnos_autorizados:
        _claves_cortes = list(zip(cortes["local"], cortes["fecha"], cortes["turno"]))
        cortes_acumulado = cortes[[c not in _turnos_autorizados for c in _claves_cortes]]
    else:
        cortes_acumulado = cortes

    # "Diferencia entre cortes": ademas de lo que pasa DENTRO de un corte
    # (arriba), tambien se suma lo que pasa en el HUECO entre un Cierre y
    # la Apertura siguiente (ver Continuidad, mas arriba en esta misma
    # pestaña) -- eso se le atribuye a quien CERRO (ver
    # acumulado_saltos_por_persona). Igual que arriba, lo ya "🔷
    # Autorizado" no cuenta.
    _ids_autorizados_salto = (
        set(cont_df.loc[cont_df["Estado"] == "🔷 Autorizado", "_id_cierre"])
        if not cont_df.empty
        else set()
    )
    saltos_para_acumulado = (
        saltos[
            (saltos["fecha_cierre"] >= desde)
            & (saltos["fecha_cierre"] <= hasta)
            & (~saltos["id_cierre"].isin(_ids_autorizados_salto))
        ]
        if not saltos.empty
        else saltos
    )
    acumulado_saltos = sh.acumulado_saltos_por_persona(saltos_para_acumulado)

    acumulado = pd.merge(
        sh.acumulado_por_persona(cortes_acumulado)[
            ["nombre", "n_cortes", "diferencia", "diferencia_fmt", "descuadre_abs"]
        ],
        acumulado_saltos[["nombre", "n_saltos", "diferencia_saltos", "diferencia_saltos_fmt"]],
        on="nombre",
        how="outer",
    )
    for _col, _default in [
        ("n_cortes", 0), ("diferencia", 0.0), ("descuadre_abs", 0.0),
        ("n_saltos", 0), ("diferencia_saltos", 0.0),
    ]:
        acumulado[_col] = acumulado[_col].fillna(_default)
    acumulado["n_cortes"] = acumulado["n_cortes"].astype(int)
    acumulado["n_saltos"] = acumulado["n_saltos"].astype(int)
    acumulado["diferencia_fmt"] = acumulado["diferencia"].apply(lambda x: f"{x:+,.2f}")
    acumulado["diferencia_saltos_fmt"] = acumulado["diferencia_saltos"].apply(lambda x: f"{x:+,.2f}")
    acumulado = acumulado.sort_values("descuadre_abs", ascending=False)

    if acumulado.empty:
        st.caption("Todavía no hay cortes ni saltos completos en el rango seleccionado.")
    else:
        st.dataframe(
            sh.arrow_safe(
                acumulado.rename(
                    columns={
                        "nombre": "Persona",
                        "n_cortes": "Cortes",
                        "diferencia_fmt": "Diferencia neta (S/, sobra − falta, se cancelan)",
                        "descuadre_abs": "Descuadre total (S/, sin importar el signo)",
                        "n_saltos": "Entregas de caja",
                        "diferencia_saltos_fmt": "Diferencia en la entrega (S/, atribuida a quien cerró)",
                    }
                ).drop(columns=["diferencia", "diferencia_saltos"])
            ),
            width="stretch",
            hide_index=True,
        )
        fig_pers = px.bar(
            acumulado.sort_values("diferencia"),
            x="nombre",
            y="diferencia",
            labels={"nombre": "Persona", "diferencia": "Diferencia neta acumulada (S/)"},
        )
        st.plotly_chart(fig_pers, width="stretch")

        # --- Detalle por persona: ver de dónde salió el descuadre ---------
        # Sirve para distinguir un descuadre real de un error al registrar
        # (p. ej. escribir 100 donde iba 1000 en una denominación).
        persona_sel = st.selectbox(
            "Ver el detalle de una persona (para revisar si fue error al registrar)",
            ["—"] + acumulado["nombre"].tolist(),
        )
        if persona_sel != "—":
            cortes_persona = cortes[cortes["nombre"] == persona_sel].copy().sort_values(
                ["fecha", "local", "turno", "corte"]
            )
            st.markdown(f"**Cortes de {persona_sel} en el rango:**")
            st.dataframe(
                sh.arrow_safe(
                    cortes_persona[
                        ["fecha", "local", "turno", "corte", "nombre_cierre",
                         "apertura", "cierre", "diferencia_fmt", "estado", "observaciones"]
                    ].rename(
                        columns={
                            "fecha": "Fecha", "local": "Local", "turno": "Turno",
                            "corte": "Corte", "nombre_cierre": "Cerró",
                            "apertura": "Apertura (S/)", "cierre": "Cierre (S/)",
                            "diferencia_fmt": "Diferencia (S/)", "estado": "Estado",
                            "observaciones": "Observaciones",
                        }
                    )
                ),
                width="stretch",
                hide_index=True,
            )

            st.markdown("**Revisión corte por corte** (Apertura vs Cierre, campo por campo):")
            for _, c in cortes_persona.iterrows():
                encabezado = (
                    f"{c['fecha']} · {c['local']} · {c['turno']} · corte {c['corte']} · "
                    f"{c['diferencia_fmt']} · {c['estado']}"
                )
                with st.expander(encabezado):
                    id_ap, id_ci = c["id_apertura"], c["id_cierre"]
                    if (
                        id_ap in _registros_por_id.index
                        and id_ci in _registros_por_id.index
                    ):
                        _comparar_par(
                            _registros_por_id.loc[id_ap], _registros_por_id.loc[id_ci]
                        )
                    else:
                        st.caption("Este corte no tiene Apertura y Cierre completos para comparar.")

            # --- Entregas de caja: los saltos que esta persona CERRÓ -----
            # Complementa lo de arriba (que mide DENTRO de un corte): esto
            # es lo que pasó en el hueco entre su Cierre y la Apertura
            # siguiente (ver Continuidad, más arriba en esta pestaña).
            saltos_persona = (
                saltos[
                    (saltos["nombre_cierre"] == persona_sel)
                    & (saltos["fecha_cierre"] >= desde)
                    & (saltos["fecha_cierre"] <= hasta)
                ].copy()
                if not saltos.empty
                else saltos
            )
            if not saltos_persona.empty:
                # El "estado" que trae calcular_saltos() es el semaforo CRUDO
                # (no sabe de ajustes). Lo pisamos con el Estado ya calculado
                # en cont_df (que sí considera ajustes -> "🔷 Autorizado"),
                # para que este detalle diga lo mismo que la tabla de
                # Continuidad de más arriba.
                _estado_ajustado_por_id_cierre = (
                    dict(zip(cont_df["_id_cierre"], cont_df["Estado"])) if not cont_df.empty else {}
                )
                saltos_persona["estado"] = (
                    saltos_persona["id_cierre"]
                    .map(_estado_ajustado_por_id_cierre)
                    .fillna(saltos_persona["estado"])
                )
                st.markdown(f"**Entregas de caja de {persona_sel} en el rango:**")
                st.dataframe(
                    sh.arrow_safe(
                        saltos_persona[
                            ["tipo_salto", "fecha_cierre", "turno_cierre", "fecha_apertura",
                             "turno_apertura", "nombre_apertura", "cierre", "apertura",
                             "diferencia_fmt", "estado"]
                        ].rename(
                            columns={
                                "tipo_salto": "Tipo", "fecha_cierre": "Fecha cierre",
                                "turno_cierre": "Turno cierre", "fecha_apertura": "Fecha apertura",
                                "turno_apertura": "Turno apertura", "nombre_apertura": "Abrió después",
                                "cierre": "Cierre (S/)", "apertura": "Apertura (S/)",
                                "diferencia_fmt": "Diferencia (S/)", "estado": "Estado",
                            }
                        )
                    ),
                    width="stretch",
                    hide_index=True,
                )

                st.markdown("**Revisión de cada entrega** (Cierre vs Apertura siguiente, campo por campo):")
                for _, sp in saltos_persona.iterrows():
                    encabezado_salto = (
                        f"{sp['tipo_salto']} · {sp['fecha_cierre']} {sp['turno_cierre']} → "
                        f"{sp['fecha_apertura']} {sp['turno_apertura']} · "
                        f"{sp['diferencia_fmt']} · {sp['estado']}"
                    )
                    with st.expander(encabezado_salto):
                        id_ci_s, id_ap_s = sp["id_cierre"], sp["id_apertura"]
                        if (
                            id_ci_s in _registros_por_id.index
                            and id_ap_s in _registros_por_id.index
                        ):
                            _comparar_par(
                                _registros_por_id.loc[id_ci_s],
                                _registros_por_id.loc[id_ap_s],
                                "Cierre",
                                "Apertura siguiente",
                            )
                        else:
                            st.caption("No se encontraron los dos registros para comparar.")

# -------------------------------------------------------------
# Politica de descuento por sucursal (ver cuadre.politica_sucursal): se
# calcula UNA vez aca para usarla en Sucursales y en Liquidacion. Los
# parametros se editan en la pestaña Sucursales (widgets con estas keys).
# -------------------------------------------------------------
POL_TASA_DEF, POL_PISO_DEF, POL_TOPE_DEF, POL_GRANDE_DEF = 0.01, 0.0, 30.0, 40.0
items_pol = sh.items_ticket(
    cortes_acumulado, saltos_para_acumulado, st.session_state.get("suc_incluir_entregas", True)
)
ops_por_local = (
    cortes.assign(_ops=cortes["id_cierre"].map(_registros_por_id["num_operaciones"]).fillna(0))
    .groupby("local")["_ops"]
    .sum()
    .to_dict()
    if not cortes.empty
    else {}
)
pol_suc, pol_rep, pol_grandes = sh.politica_sucursal(
    items_pol,
    ops_por_local,
    st.session_state.get("pol_tasa", POL_TASA_DEF),
    st.session_state.get("pol_piso", POL_PISO_DEF),
    st.session_state.get("pol_tope", POL_TOPE_DEF),
    st.session_state.get("pol_grande", POL_GRANDE_DEF),
)

with tab_liquidacion:
    # -------------------------------------------------------------
    # Liquidacion por trabajador ("ticket"). Solo lectura: junta las
    # diferencias reales (> S/1) de cada persona para REVISAR si
    # corresponde algun descuento. Reusa cortes_acumulado y
    # saltos_para_acumulado (calculados en la pestaña Cuadre), que ya
    # excluyen lo "🔷 Autorizado" y respetan los filtros del sidebar.
    # -------------------------------------------------------------
    st.subheader("🧾 Liquidación por trabajador")
    st.caption(
        "Usa los filtros de la izquierda (locales, turno y rango de fechas). "
        "Excluye lo '🔷 Autorizado' y las diferencias de hasta S/ 1. Es una "
        "ayuda para revisar, no un descuento automático: antes de descontar, "
        "revisa cada ítem (puede ser un error al registrar)."
    )
    col_liq1, col_liq2 = st.columns(2)
    compensar_liq = col_liq1.checkbox(
        "Compensar faltantes con sobrantes", value=True,
        help="Si está activo, el monto a revisar es solo lo que quede faltando después de restar los sobrantes.",
    )
    incluir_entregas_liq = col_liq2.checkbox(
        "Incluir diferencias en entregas de caja", value=True,
        help="Lo que no coincidió entre el Cierre de la persona y la Apertura siguiente.",
    )

    items_liq = sh.items_ticket(cortes_acumulado, saltos_para_acumulado, incluir_entregas_liq)
    creditos_liq = sh.creditos_repuso(ajustes_df, desde, hasta, locales_sel)

    # Incentivos por encuestas del periodo, segun la nota de su local en su mes.
    encuestas_liq = sh.get_encuestas_df()
    if not encuestas_liq.empty:
        encuestas_liq = sh.con_estado_nota(encuestas_liq, sh.get_notas_df())
        encuestas_liq = encuestas_liq[
            encuestas_liq["fecha"].between(desde, hasta)
            & encuestas_liq["local"].isin(locales_sel)
            & (encuestas_liq["estado_pago"] != "No válido")
        ]

    def _incentivos_de(persona):
        if encuestas_liq.empty:
            return {"pagar": 0.0, "condicionado": 0.0, "no_aprobado": 0.0}
        e = encuestas_liq[
            (encuestas_liq["nombre"] == persona) & (encuestas_liq["estado_pago"] == "Pendiente")
        ]
        return {
            "pagar": float(e.loc[e["estado_nota"] == "Aprobado", "incentivo"].sum()),
            "condicionado": float(e.loc[e["estado_nota"] == "Condicionado", "incentivo"].sum()),
            "no_aprobado": float(e.loc[e["estado_nota"] == "No aprobado", "incentivo"].sum()),
        }

    personas_liq = sorted(
        set(items_liq["persona"])
        | set(creditos_liq["persona"])
        | (set(encuestas_liq["nombre"]) if not encuestas_liq.empty else set())
    )

    # Operaciones de cada persona en el rango: las del Cierre de cada corte
    # que ABRIO (misma atribucion que la diferencia). Sirve para calibrar un
    # margen de error proporcional al trabajo.
    ops_por_persona = (
        cortes.assign(_ops=cortes["id_cierre"].map(_registros_por_id["num_operaciones"]).fillna(0))
        .groupby("nombre")["_ops"]
        .sum()
        if not cortes.empty
        else pd.Series(dtype=float)
    )

    if not personas_liq:
        st.success("Sin diferencias para revisar en el rango seleccionado.")
    else:
        filas_resumen_liq = []
        for _persona in personas_liq:
            _t = sh.totales_ticket(items_liq[items_liq["persona"] == _persona], compensar_liq)
            filas_resumen_liq.append(
                {
                    "Persona": _persona,
                    "Ítems": int((items_liq["persona"] == _persona).sum()),
                    "Faltantes (S/)": _t["faltantes"],
                    "Sobrantes (S/)": _t["sobrantes"],
                    "Neto (S/)": _t["neto"],
                    "A revisar (S/)": _t["a_revisar"],
                    "A favor (S/)": float(creditos_liq.loc[creditos_liq["persona"] == _persona, "monto"].sum()),
                }
            )
            _ops = int(ops_por_persona.get(_persona, 0))
            _asignado = float(pol_rep.loc[pol_rep["persona"] == _persona, "asignado"].sum())
            filas_resumen_liq[-1]["Descuento asignado (S/)"] = _asignado
            filas_resumen_liq[-1]["A descontar (S/)"] = max(
                0.0, _asignado - filas_resumen_liq[-1]["A favor (S/)"]
            )
            filas_resumen_liq[-1]["Incentivo a pagar (S/)"] = _incentivos_de(_persona)["pagar"]
            filas_resumen_liq[-1]["Operaciones"] = _ops
        resumen_liq = pd.DataFrame(filas_resumen_liq).sort_values(
            ["A descontar (S/)", "A revisar (S/)"], ascending=False
        )
        st.dataframe(
            sh.arrow_safe(resumen_liq),
            width="stretch",
            hide_index=True,
            column_config={
                c: st.column_config.NumberColumn(format="%.2f")
                for c in ["Faltantes (S/)", "Sobrantes (S/)", "Neto (S/)", "A revisar (S/)", "A favor (S/)",
                          "Descuento asignado (S/)", "A descontar (S/)", "Incentivo a pagar (S/)"]
            },
        )
        st.caption(
            "El descuento se define primero por SUCURSAL (pestaña Sucursales: pérdida neta, "
            "franquicia y techo) y luego se reparte entre los operadores en proporción a lo que "
            "le faltó a cada uno. Descuento asignado = su parte; A favor = plata que repuso de "
            "su bolsillo (campo «Repuso» del ajuste); A descontar = Descuento asignado − A favor. "
            "Faltantes, Sobrantes, Neto y A revisar son su vista individual, para seguimiento."
        )

        persona_liq = st.selectbox("Ticket de", resumen_liq["Persona"].tolist())
        items_persona = items_liq[items_liq["persona"] == persona_liq]
        creditos_persona = creditos_liq[creditos_liq["persona"] == persona_liq]
        tot = sh.totales_ticket(items_persona, compensar_liq)
        a_favor = float(creditos_persona["monto"].sum())

        with st.container(border=True):
            st.markdown(f"### 🧾 {persona_liq}")
            st.caption(
                f"Periodo: {desde} al {hasta} · Locales: {', '.join(locales_sel)}"
            )
            m1, m2, m3 = st.columns(3)
            m1.metric("Faltantes", f"S/ {tot['faltantes']:,.2f}")
            m2.metric("Sobrantes", f"S/ {tot['sobrantes']:,.2f}")
            m3.metric("Neto", f"S/ {tot['neto']:+,.2f}")
            m4, m5, m6 = st.columns(3)
            m4.metric("A revisar", f"S/ {tot['a_revisar']:,.2f}")
            m5.metric("A favor (repuso)", f"S/ {a_favor:,.2f}")
            asignado_persona = float(pol_rep.loc[pol_rep["persona"] == persona_liq, "asignado"].sum())
            m6.metric(
                "Descuento asignado",
                f"S/ {asignado_persona:,.2f}",
                help="Su parte del descuento de la sucursal (pestaña Sucursales): proporcional a lo "
                "que le faltó, después de la franquicia y con el techo de la sucursal.",
            )
            ops_persona = int(ops_por_persona.get(persona_liq, 0))
            descuento_persona = max(0.0, asignado_persona - a_favor)
            grandes_persona = pol_grandes[pol_grandes["persona"] == persona_liq]
            m7, m8, m9 = st.columns(3)
            m7.metric("Operaciones", f"{ops_persona:,}")
            m8.metric(
                "A descontar",
                f"S/ {descuento_persona:,.2f}",
                help="Descuento asignado − lo que repuso de su bolsillo.",
            )
            m9.metric(
                "A investigar aparte",
                f"{len(grandes_persona)} (S/ {grandes_persona['diferencia'].sum():+,.2f})",
                help="Ítems grandes: no entran al reparto, se revisan uno por uno (pueden ser un error de registro).",
            )

            inc = _incentivos_de(persona_liq)
            if any(inc.values()):
                st.markdown(
                    "**Incentivos por encuestas** (se pagan solo si la nota del local en el mes es de "
                    f"{sh.UMBRAL_NOTA_LOCAL} o más; los ya pagados no aparecen aquí)"
                )
                _pend_p = encuestas_liq[
                    (encuestas_liq["nombre"] == persona_liq)
                    & (encuestas_liq["estado_pago"] == "Pendiente")
                    & (encuestas_liq["estado_nota"] == "Aprobado")
                ]
                _por_medio = _pend_p.assign(
                    medio_pago=_pend_p["medio_pago"].replace("", "Sin definir")
                ).groupby("medio_pago")["incentivo"].sum()
                if not _por_medio.empty:
                    st.caption(
                        "A pagar por forma de pago: "
                        + " · ".join(f"{m}: S/ {monto:,.2f}" for m, monto in _por_medio.items())
                    )
                i1, i2, i3 = st.columns(3)
                i1.metric("A pagar (local aprobado)", f"S/ {inc['pagar']:,.2f}")
                i2.metric("Condicionado (falta la nota)", f"S/ {inc['condicionado']:,.2f}")
                i3.metric("No se paga (nota no aprobada)", f"S/ {inc['no_aprobado']:,.2f}")
                st.caption(
                    f"Referencia (no es un movimiento): incentivos a pagar S/ {inc['pagar']:,.2f} − "
                    f"a descontar S/ {descuento_persona:,.2f} = S/ {inc['pagar'] - descuento_persona:,.2f}. "
                    "Cada concepto se paga o se descuenta por separado."
                )

            tabla_ticket = items_persona.drop(columns=["persona"]).rename(
                columns={
                    "tipo": "Tipo", "fecha": "Fecha", "local": "Local",
                    "detalle": "Detalle", "diferencia": "Diferencia (S/)",
                    "observaciones": "Observaciones",
                }
            )
            if not tabla_ticket.empty:
                st.dataframe(
                    sh.arrow_safe(tabla_ticket),
                    width="stretch",
                    hide_index=True,
                    column_config={"Diferencia (S/)": st.column_config.NumberColumn(format="%+.2f")},
                )
            else:
                st.caption("Sin diferencias propias por revisar.")

            tabla_favor = creditos_persona.drop(columns=["persona"]).rename(
                columns={"fecha": "Fecha", "local": "Local", "monto": "Repuso (S/)", "motivo": "Motivo"}
            )
            if not tabla_favor.empty:
                st.markdown("**A favor (repuso de su bolsillo):**")
                st.dataframe(
                    sh.arrow_safe(tabla_favor),
                    width="stretch",
                    hide_index=True,
                    column_config={"Repuso (S/)": st.column_config.NumberColumn(format="%.2f")},
                )

            _csv = sh.arrow_safe(tabla_ticket)
            if not tabla_favor.empty:
                _csv = pd.concat(
                    [
                        _csv,
                        sh.arrow_safe(
                            tabla_favor.rename(columns={"Repuso (S/)": "Diferencia (S/)", "Motivo": "Detalle"})
                            .assign(Tipo="A favor (repuso)")
                        ),
                    ],
                    ignore_index=True,
                )
            st.download_button(
                "Descargar ticket (CSV)",
                data=_csv.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"ticket_{persona_liq}_{desde}_{hasta}.csv".replace(" ", "_"),
                mime="text/csv",
            )

with tab_sucursales:
    # -------------------------------------------------------------
    # Balance de diferencias por sucursal: lo que falto y lo que sobro en
    # cada local (cortes y entregas de caja), sin lo ya autorizado, mas lo
    # autorizado por ajustes y lo que alguien repuso. Reusa cortes_acumulado
    # y saltos_para_acumulado (pestaña Cuadre): mismos filtros del sidebar.
    # -------------------------------------------------------------
    st.subheader("🏪 Balance de diferencias por sucursal")
    st.caption(
        "En el rango de fechas y locales filtrados. Faltantes y sobrantes son las diferencias "
        "de más de S/ 1 que siguen sin explicar (no incluyen lo '🔷 Autorizado'). "
        "Autorizado = retiros/ingresos registrados por administración en el periodo (con signo)."
    )
    incluir_entregas_suc = st.checkbox(
        "Incluir diferencias en entregas de caja", value=True, key="suc_incluir_entregas"
    )
    items_suc = sh.items_ticket(cortes_acumulado, saltos_para_acumulado, incluir_entregas_suc)
    if ajustes_df.empty:
        ajustes_suc = ajustes_df
    else:
        ajustes_suc = ajustes_df[
            (ajustes_df["fecha"] >= desde)
            & (ajustes_df["fecha"] <= hasta)
            & ajustes_df["local"].isin(locales_sel)
        ]

    filas_suc = []
    for _loc in locales_sel:
        _it = items_suc[items_suc["local"] == _loc] if not items_suc.empty else items_suc
        _dif = _it["diferencia"] if not _it.empty else pd.Series(dtype=float)
        _aj = ajustes_suc[ajustes_suc["local"] == _loc] if not ajustes_suc.empty else ajustes_suc
        _faltantes = float(abs(_dif[_dif < 0].sum()))
        _sobrantes = float(_dif[_dif > 0].sum())
        filas_suc.append(
            {
                "Local": _loc,
                "Cortes": int((cortes["local"] == _loc).sum()) if not cortes.empty else 0,
                "Ítems por revisar": int(len(_it)),
                "Faltantes (S/)": _faltantes,
                "Sobrantes (S/)": _sobrantes,
                "Neto sin explicar (S/)": _sobrantes - _faltantes,
                "Autorizado (S/)": float(_aj["monto"].sum()) if not _aj.empty else 0.0,
                "Repuesto (S/)": float(
                    _aj.loc[(_aj["monto"] > 0) & (_aj["repuso"] != ""), "monto"].sum()
                )
                if not _aj.empty
                else 0.0,
            }
        )

    if not filas_suc:
        st.caption("Selecciona al menos un local.")
    else:
        balance_suc = pd.DataFrame(filas_suc).sort_values("Neto sin explicar (S/)")
        total_suc = {
            "Local": "TOTAL",
            **{c: balance_suc[c].sum() for c in balance_suc.columns if c != "Local"},
        }
        st.dataframe(
            sh.arrow_safe(pd.concat([balance_suc, pd.DataFrame([total_suc])], ignore_index=True)),
            width="stretch",
            hide_index=True,
            column_config={
                c: st.column_config.NumberColumn(format="%.2f")
                for c in ["Faltantes (S/)", "Sobrantes (S/)", "Neto sin explicar (S/)",
                          "Autorizado (S/)", "Repuesto (S/)"]
            },
        )
        fig_suc = px.bar(
            balance_suc,
            x="Local",
            y="Neto sin explicar (S/)",
            color=balance_suc["Neto sin explicar (S/)"] < 0,
            color_discrete_map={True: "#E24B4A", False: "#639922"},
            labels={"color": "Faltó"},
        )
        fig_suc.update_layout(showlegend=False)
        st.plotly_chart(fig_suc, width="stretch")

        local_det = st.selectbox("Ver el detalle de una sucursal", ["—"] + balance_suc["Local"].tolist())
        if local_det != "—":
            det = items_suc[items_suc["local"] == local_det] if not items_suc.empty else items_suc
            if det.empty:
                st.success("Sin diferencias por revisar en esta sucursal.")
            else:
                st.dataframe(
                    sh.arrow_safe(
                        det.drop(columns=["local"]).rename(
                            columns={"persona": "Persona", "tipo": "Tipo", "fecha": "Fecha",
                                     "detalle": "Detalle", "diferencia": "Diferencia (S/)",
                                     "observaciones": "Observaciones"}
                        ).sort_values("Fecha", ascending=False)
                    ),
                    width="stretch",
                    hide_index=True,
                    column_config={"Diferencia (S/)": st.column_config.NumberColumn(format="%+.2f")},
                )

    st.divider()
    st.subheader("📐 Política de descuento por sucursal")
    st.caption(
        "Primero la sucursal, después el reparto entre operadores: (1) pérdida neta del mes = "
        "faltantes − sobrantes, sin los ítems grandes; (2) se resta la franquicia (piso + tasa × "
        "operaciones de la sucursal); (3) lo que sobra se descuenta con un techo por sucursal; "
        "(4) el descuento se reparte entre los operadores según lo que le faltó a cada uno, y quien "
        "no tuvo faltantes no paga. Los ítems grandes se investigan aparte."
    )
    pp1, pp2, pp3, pp4 = st.columns(4)
    pp1.number_input(
        "Franquicia: S/ por operación", min_value=0.0, value=POL_TASA_DEF, step=0.005,
        format="%.3f", key="pol_tasa",
    )
    pp2.number_input("Franquicia: piso (S/)", min_value=0.0, value=POL_PISO_DEF, step=1.0, key="pol_piso")
    pp3.number_input(
        "Techo de descuento por sucursal (S/)", min_value=0.0, value=POL_TOPE_DEF, step=5.0, key="pol_tope"
    )
    pp4.number_input(
        "Ítem grande (S/): se investiga aparte", min_value=1.0, value=POL_GRANDE_DEF, step=5.0, key="pol_grande"
    )
    st.caption("Los valores vuelven a los acordados (S/ 0.01 por operación, techo S/ 30, ítem grande S/ 40) al recargar la página.")

    if pol_suc.empty:
        st.caption("Sin datos para calcular la política en el rango seleccionado.")
    else:
        vista_pol = pol_suc.rename(
            columns={
                "local": "Local", "operaciones": "Operaciones", "faltantes": "Faltantes (S/)",
                "sobrantes": "Sobrantes (S/)", "perdida_neta": "Pérdida neta (S/)",
                "franquicia": "Franquicia (S/)", "exceso": "Pasa la franquicia (S/)",
                "descuento": "Descuento (S/)", "items_grandes": "Ítems grandes",
                "monto_grandes": "Monto ítems grandes (S/)",
            }
        )
        total_pol = {"Local": "TOTAL", **{c: vista_pol[c].sum() for c in vista_pol.columns if c != "Local"}}
        st.dataframe(
            sh.arrow_safe(pd.concat([vista_pol, pd.DataFrame([total_pol])], ignore_index=True)),
            width="stretch",
            hide_index=True,
            column_config={
                c: st.column_config.NumberColumn(format="%.2f")
                for c in ["Faltantes (S/)", "Sobrantes (S/)", "Pérdida neta (S/)", "Franquicia (S/)",
                          "Pasa la franquicia (S/)", "Descuento (S/)", "Monto ítems grandes (S/)"]
            },
        )
        if pol_rep.empty:
            st.success("Ninguna sucursal supera su franquicia: no hay descuento por repartir.")
        else:
            st.markdown("**Reparto entre operadores**")
            st.dataframe(
                sh.arrow_safe(
                    pol_rep.assign(participacion=pol_rep["participacion"] * 100)
                    .sort_values(["local", "asignado"], ascending=[True, False])
                    .rename(
                        columns={"local": "Local", "persona": "Persona", "faltante": "Faltante (S/)",
                                 "participacion": "Participación (%)", "asignado": "Descuento asignado (S/)"}
                    )
                ),
                width="stretch",
                hide_index=True,
                column_config={
                    "Faltante (S/)": st.column_config.NumberColumn(format="%.2f"),
                    "Participación (%)": st.column_config.NumberColumn(format="%.1f"),
                    "Descuento asignado (S/)": st.column_config.NumberColumn(format="%.2f"),
                },
            )
        if not pol_grandes.empty:
            st.markdown("**Ítems grandes: investigar aparte (no entran al reparto)**")
            st.dataframe(
                sh.arrow_safe(
                    pol_grandes.rename(
                        columns={"persona": "Persona", "tipo": "Tipo", "fecha": "Fecha", "local": "Local",
                                 "detalle": "Detalle", "diferencia": "Diferencia (S/)",
                                 "observaciones": "Observaciones"}
                    ).sort_values("Fecha", ascending=False)
                ),
                width="stretch",
                hide_index=True,
                column_config={"Diferencia (S/)": st.column_config.NumberColumn(format="%+.2f")},
            )

with tab_comisiones:
    # -------------------------------------------------------------
    # Comisiones estimadas (antes pages/8_Comisiones.py, movido acá
    # como pestaña para no tener una pagina de administracion aparte).
    #
    # Estima cuanto genera cada local en comision, a partir del numero
    # de operaciones registradas en los Cierres, multiplicado por una
    # tarifa PROMEDIO por operacion (configurable por local en la hoja
    # 'Config', columna 'soles_por_operacion'). La comision real del
    # BCP varia por tipo de operacion y por contrato, asi que esto es
    # un ESTIMADO, para tener una idea del mes, no el numero exacto.
    #
    # Usa los mismos filtros de Locales / Rango de fechas del sidebar
    # que el resto del Dashboard (no un sidebar aparte, para no
    # duplicar los mismos widgets dos veces en la misma pagina).
    # -------------------------------------------------------------
    st.subheader("💰 Comisiones estimadas")

    cierres_comision = df[
        (df["tipo"] == "Cierre")
        & (df["local"].isin(locales_sel))
        & (df["fecha"].between(desde, hasta))
    ].copy()
    cierres_comision["num_operaciones"] = pd.to_numeric(
        cierres_comision["num_operaciones"], errors="coerce"
    ).fillna(0)

    ops_por_local = (
        cierres_comision.groupby("local")["num_operaciones"].sum().rename("operaciones").reset_index()
    )

    cfg = config_df.drop_duplicates("local").set_index("local")
    # Tolerante a que Config todavia no tenga las columnas nuevas (caché vieja
    # tras un deploy): si faltan, se cae al tipo por nombre y la tarifa default.
    tipo_por_local = cfg["tipo_agente"].to_dict() if "tipo_agente" in cfg.columns else {}
    tarifa_por_local = (
        pd.to_numeric(cfg["soles_por_operacion"], errors="coerce").to_dict()
        if "soles_por_operacion" in cfg.columns
        else {}
    )


    def _tipo_comision(local):
        return tipo_por_local.get(local) or sh._tipo_agente_por_nombre(local)


    def _tarifa_comision(local):
        valor = tarifa_por_local.get(local)
        if valor is None or pd.isna(valor) or valor <= 0:
            return sh.TARIFA_DEFAULT.get(_tipo_comision(local), sh.TARIFA_DEFAULT["normal"])
        return float(valor)


    ops_por_local["tipo_agente"] = ops_por_local["local"].map(_tipo_comision)
    ops_por_local["soles_por_operacion"] = ops_por_local["local"].map(_tarifa_comision)
    ops_por_local["comision"] = (
        ops_por_local["operaciones"] * ops_por_local["soles_por_operacion"]
    )
    ops_por_local = ops_por_local.sort_values("comision", ascending=False)

    st.caption(
        f"Rango: **{desde}** a **{hasta}**. Comisión = operaciones × tarifa promedio por "
        "operación (configurable por local en la hoja `Config`, columna "
        "`soles_por_operacion`). Es un **estimado**."
    )

    # --- KPIs ---
    total_ops_comision = int(ops_por_local["operaciones"].sum())
    total_comision = float(ops_por_local["comision"].sum())
    col1, col2, col3 = st.columns(3)
    col1.metric("Operaciones totales", f"{total_ops_comision:,}")
    col2.metric("Comisión estimada total", f"S/ {total_comision:,.2f}")
    dias_rango = max((hasta - desde).days + 1, 1)
    col3.metric("Promedio por día", f"S/ {total_comision / dias_rango:,.2f}")

    # --- Por tipo de agente ---
    por_tipo = (
        ops_por_local.groupby("tipo_agente")
        .agg(operaciones=("operaciones", "sum"), comision=("comision", "sum"))
        .reset_index()
    )
    st.markdown("**Por tipo de agente**")
    st.dataframe(
        sh.arrow_safe(
            por_tipo.rename(
                columns={
                    "tipo_agente": "Tipo",
                    "operaciones": "Operaciones",
                    "comision": "Comisión estimada (S/)",
                }
            )
        ),
        width="stretch",
        hide_index=True,
    )

    # --- Por local ---
    st.markdown("**Por local**")
    st.dataframe(
        sh.arrow_safe(
            ops_por_local.rename(
                columns={
                    "local": "Local",
                    "tipo_agente": "Tipo",
                    "soles_por_operacion": "Tarifa (S/ x op)",
                    "operaciones": "Operaciones",
                    "comision": "Comisión estimada (S/)",
                }
            )
        ),
        width="stretch",
        hide_index=True,
    )

    if not ops_por_local.empty:
        fig_comision = px.bar(
            ops_por_local,
            x="local",
            y="comision",
            color="tipo_agente",
            labels={"local": "Local", "comision": "Comisión estimada (S/)", "tipo_agente": "Tipo"},
        )
        st.plotly_chart(fig_comision, width="stretch")

    st.caption(
        "Para ajustar una tarifa: en la hoja `Config` del Google Sheet, escribe el "
        "valor en la columna `soles_por_operacion` de ese local (y `tipo_agente` = "
        "superagente / normal). Si lo dejas vacío, se usa el promedio por defecto "
        f"(superagente S/ {sh.TARIFA_DEFAULT['superagente']:.3f}, normal "
        f"S/ {sh.TARIFA_DEFAULT['normal']:.3f})."
    )

with tab_registros:
    # -------------------------------------------------------------
    # Tabla consolidada
    # -------------------------------------------------------------
    st.subheader("🗂️ Registros")
    columnas_fotos = [c for c in df_filtrado.columns if c.startswith("foto_")]
    st.dataframe(
        sh.arrow_safe(df_filtrado.drop(columns=columnas_fotos)),
        width="stretch",
        hide_index=True,
    )

    with st.expander("Ver links de fotos de un registro"):
        if not df_filtrado.empty:
            id_elegido = st.selectbox("ID de registro", df_filtrado["id"])
            fila = df_filtrado[df_filtrado["id"] == id_elegido].iloc[0]
            for col in columnas_fotos:
                if fila[col]:
                    st.markdown(f"- [{col}]({fila[col]})")

with tab_incentivos:
    # -------------------------------------------------------------
    # Incentivos por encuestas NPS (S/ 10 por encuesta calificada 9 o 10)
    #
    # Viene del incentivo interno del negocio: reemplaza el Google Form
    # aparte que se usaba para esto. El personal registra cada encuesta
    # desde pages/4_Encuestas.py (con el PIN de su local); cambiar el
    # estado de pago SOLO se hace aca, protegido con tu PIN de dueno, para
    # que nadie pueda marcar su propio incentivo como pagado (o invalidarlo)
    # sin que tu lo hayas revisado.
    # -------------------------------------------------------------
    st.subheader("⭐ Incentivos por encuestas NPS (S/ 10 c/u)")
    if "msg_enc_guardada" in st.session_state:
        st.toast(st.session_state.pop("msg_enc_guardada"), icon="✅")

    ESTADOS_PAGO_ENCUESTA = ["Pendiente", "Pagada", "No válido"]

    encuestas_df = sh.get_encuestas_df()
    notas_df = sh.get_notas_df()
    encuestas_df = sh.con_estado_nota(encuestas_df, notas_df)

    # El incentivo solo se paga si la nota del LOCAL en ese mes (la manda el
    # ejecutivo de BCP al cerrar el mes) es de UMBRAL_NOTA_LOCAL o mas.
    st.markdown(
        f"**Nota mensual de cada local (BCP)** — el incentivo se paga solo si es de {sh.UMBRAL_NOTA_LOCAL} o más (nota mínima)"
    )
    _mes_actual = sh.hoy_local().strftime("%Y-%m")
    meses_nota = sorted(
        ({_mes_actual} | set(encuestas_df["mes"].dropna())) if not encuestas_df.empty else {_mes_actual},
        reverse=True,
    )
    st.caption(
        "Una nota **Parcial** (avance del mes que te da BCP) solo informa al personal: no habilita "
        "pagos. La **Final** (al cerrar el mes) es la que decide el incentivo. Cargar de nuevo el "
        "mismo mes y local reemplaza la anterior del mismo tipo."
    )
    with st.form("form_nota_local", clear_on_submit=True):
        n1, n2, n3 = st.columns(3)
        tipo_nota = n1.selectbox("Tipo", ["Final", "Parcial"])
        mes_nota = n2.selectbox("Mes", meses_nota)
        local_nota = n3.selectbox("Local", config_df["local"].tolist())
        n4, n5, n6 = st.columns(3)
        valor_nota = n4.number_input("Nota", min_value=0.0, max_value=100.0, value=0.0, step=1.0)
        fecha_corte_nota = n5.date_input("Al (solo para Parcial)", value=sh.hoy_local())
        quien_nota = n6.text_input("Cargado por")
        if st.form_submit_button("Guardar nota"):
            if not quien_nota.strip():
                st.error("Escribe quién carga la nota.")
            else:
                sh.guardar_nota(
                    {
                        "mes": mes_nota,
                        "local": local_nota,
                        "nota": valor_nota,
                        "cargado_por": quien_nota.strip(),
                        "timestamp": sh.ahora_local().replace(tzinfo=None).isoformat(timespec="seconds"),
                        "tipo": tipo_nota,
                        "fecha_corte": fecha_corte_nota.strftime("%d/%m") if tipo_nota == "Parcial" else "",
                    }
                )
                st.success("Nota guardada.")
                st.rerun()
    if notas_df.empty:
        st.caption("Todavía no hay notas cargadas: los incentivos quedan «Condicionados».")
    else:
        notas_vista = notas_df.sort_values(["mes", "local"], ascending=[False, True]).copy()
        notas_vista["Estado"] = [
            sh.estado_parcial(n) if t == "Parcial" else sh.estado_nota(n)
            for n, t in zip(notas_vista["nota"], notas_vista["tipo"])
        ]
        st.dataframe(
            sh.arrow_safe(
                notas_vista[["mes", "local", "tipo", "nota", "Estado", "fecha_corte", "cargado_por"]].rename(
                    columns={"mes": "Mes", "local": "Local", "tipo": "Tipo", "nota": "Nota",
                             "fecha_corte": "Al", "cargado_por": "Cargado por"}
                )
            ),
            width="stretch",
            hide_index=True,
        )

    if encuestas_df.empty:
        st.caption("Todavia no se ha registrado ninguna encuesta NPS.")
    else:
        pendientes_todas_df = encuestas_df[encuestas_df["estado_pago"] == "Pendiente"]
        pendientes_df = pendientes_todas_df[pendientes_todas_df["estado_nota"] == "Aprobado"]
        condicionadas_df = pendientes_todas_df[pendientes_todas_df["estado_nota"] == "Condicionado"]
        no_aprobadas_df = pendientes_todas_df[pendientes_todas_df["estado_nota"] == "No aprobado"]
        pagadas_df = encuestas_df[encuestas_df["estado_pago"] == "Pagada"]
        no_validas_df = encuestas_df[encuestas_df["estado_pago"] == "No válido"]

        colA, colB, colC, colD = st.columns(4)
        colA.metric("Encuestas totales", len(encuestas_df))
        colB.metric(
            "Por pagar (local aprobado)",
            f"{len(pendientes_df)} (S/ {pendientes_df['incentivo'].sum():,.2f})",
        )
        colC.metric(
            "Ya pagadas",
            f"{len(pagadas_df)} (S/ {pagadas_df['incentivo'].sum():,.2f})",
        )
        colD.metric("No validas", len(no_validas_df))
        colE, colF = st.columns(2)
        colE.metric(
            "Condicionadas (falta la nota del local)",
            f"{len(condicionadas_df)} (S/ {condicionadas_df['incentivo'].sum():,.2f})",
        )
        colF.metric(
            "No se pagan (nota del local no aprobada)",
            f"{len(no_aprobadas_df)} (S/ {no_aprobadas_df['incentivo'].sum():,.2f})",
        )

        st.caption("Cuanto se le debe a cada trabajador (solo lo pendiente y con local aprobado):")
        if pendientes_df.empty:
            st.success("No hay incentivos por pagar ahora. 👍")
        else:
            resumen_por_nombre = (
                pendientes_df.assign(medio_pago=pendientes_df["medio_pago"].replace("", "Sin definir"))
                .groupby(["local", "nombre", "medio_pago"])["incentivo"]
                .agg(["count", "sum"])
                .reset_index()
                .rename(
                    columns={
                        "local": "Local",
                        "nombre": "Nombre",
                        "medio_pago": "Forma de pago",
                        "count": "Encuestas pendientes",
                        "sum": "Monto pendiente (S/)",
                    }
                )
                .sort_values("Monto pendiente (S/)", ascending=False)
            )
            st.dataframe(sh.arrow_safe(resumen_por_nombre), width="stretch", hide_index=True)

        st.markdown("**Detalle de encuestas (ver capturas y cambiar estado):**")
        for _, fila_encuesta in encuestas_df.sort_values("timestamp", ascending=False).iterrows():
            _sube = fila_encuesta["timestamp"]
            _otro_mes = pd.notna(_sube) and (_sube.year, _sube.month) != (
                fila_encuesta["fecha"].year, fila_encuesta["fecha"].month
            )
            titulo_encuesta = (
                f"{'⚠️ ' if _otro_mes else ''}{fila_encuesta['fecha']} · {fila_encuesta['local']} · "
                f"{fila_encuesta['nombre']} "
                f"· Nota {fila_encuesta['nota']} · {fila_encuesta['estado_visible']} "
                f"· {fila_encuesta['medio_pago'] or 'Sin forma de pago'}"
            )
            with st.expander(titulo_encuesta):
                # Chicas por defecto; con el check se ven a ancho completo. Las
                # sirve la app (no un link de Drive), asi funciona aunque la
                # carpeta de Drive no este compartida.
                id_enc = fila_encuesta["id"]
                for campo, etiqueta in [
                    ("captura_correo", "Correo"),
                    ("captura_mensaje_exito", "Mensaje de éxito"),
                ]:
                    imagen = sh.descargar_imagen_drive(fila_encuesta[campo])
                    if imagen:
                        grande = st.checkbox(
                            f"🔍 Ver «{etiqueta}» más grande", key=f"zoom_{id_enc}_{campo}"
                        )
                        sh.mostrar_imagen(
                            imagen, ancho_px=None if grande else 260, caption=etiqueta
                        )
                    else:
                        st.caption(f"{etiqueta}: no se pudo cargar.")

                st.caption(
                    f"Fecha de la encuesta (la que declaró quien la subió): **{fila_encuesta['fecha']}** · "
                    f"subida el {_sube:%Y-%m-%d %H:%M}"
                    + (
                        " · ⚠️ **es de un mes distinto al de la subida: verifícala con la captura del correo**"
                        if _otro_mes
                        else ""
                    )
                )
                medios_opc = ["Sin definir"] + sh.MEDIOS_PAGO_ENCUESTA
                medio_actual = fila_encuesta["medio_pago"] or "Sin definir"
                estado_actual = fila_encuesta["estado_pago"]
                if estado_actual not in ESTADOS_PAGO_ENCUESTA:
                    estado_actual = "Pendiente"

                # Los dos campos se guardan JUNTOS con un solo boton: asi cambiar
                # uno no recarga toda la pagina (ni vuelve a leer Google Sheets)
                # antes de poder cambiar el otro.
                with st.form(f"form_enc_{fila_encuesta['id']}"):
                    nueva_fecha = st.date_input(
                        "Fecha de la encuesta",
                        value=fila_encuesta["fecha"],
                        max_value=sh.hoy_local(),
                        key=f"fecha_{fila_encuesta['id']}",
                        help="Corrígela si quien subió la encuesta puso la fecha de subida en vez de la fecha real "
                        "del correo: de ella depende a qué mes y a qué nota NPS pertenece.",
                    )
                    nuevo_medio = st.selectbox(
                        "Forma de pago",
                        medios_opc,
                        index=medios_opc.index(medio_actual) if medio_actual in medios_opc else 0,
                        key=f"medio_{fila_encuesta['id']}",
                    )
                    nuevo_estado = st.selectbox(
                        "Estado",
                        ESTADOS_PAGO_ENCUESTA,
                        index=ESTADOS_PAGO_ENCUESTA.index(estado_actual),
                        key=f"estado_{fila_encuesta['id']}",
                    )
                    guardar_enc = st.form_submit_button("Guardar cambios")
                if guardar_enc:
                    cambio_medio = nuevo_medio != medio_actual and nuevo_medio != "Sin definir"
                    cambio_estado = nuevo_estado != estado_actual
                    cambio_fecha = nueva_fecha != fila_encuesta["fecha"]
                    # Si se corrige la fecha, el pago se evalua con la nota del mes NUEVO.
                    _estado_nota_nuevo = (
                        sh.con_estado_nota(
                            pd.DataFrame([{"fecha": nueva_fecha, "local": fila_encuesta["local"]}]),
                            notas_df,
                        )["estado_nota"].iloc[0]
                        if cambio_fecha
                        else fila_encuesta["estado_nota"]
                    )
                    if nuevo_estado == "Pagada" and (cambio_estado or cambio_fecha) and _estado_nota_nuevo != "Aprobado":
                        st.error(
                            "No se puede marcar como pagada: la nota del local en "
                            f"{nueva_fecha:%Y-%m} está «{_estado_nota_nuevo}» "
                            f"(la nota mínima para pagar es {sh.UMBRAL_NOTA_LOCAL})."
                        )
                    elif cambio_medio or cambio_estado or cambio_fecha:
                        try:
                            sh.actualizar_encuesta(
                                fila_encuesta["id"],
                                estado_pago=nuevo_estado if cambio_estado else None,
                                medio_pago=nuevo_medio if cambio_medio else None,
                                fecha=nueva_fecha.isoformat() if cambio_fecha else None,
                                observaciones=(
                                    (str(fila_encuesta["observaciones"] or "").strip() + " | ").lstrip(" |")
                                    + f"Fecha corregida por administración de {fila_encuesta['fecha']} a {nueva_fecha}"
                                    if cambio_fecha
                                    else None
                                ),
                            )
                        except (ValueError, RuntimeError) as error:
                            st.error(f"No se guardó: {error}")
                        else:
                            _partes = []
                            if cambio_medio:
                                _partes.append(f"forma de pago: {nuevo_medio}")
                            if cambio_estado:
                                _partes.append(f"estado: {nuevo_estado}")
                            if cambio_fecha:
                                _partes.append(f"fecha: {nueva_fecha}")
                            st.session_state["msg_enc_guardada"] = (
                                f"Guardado y confirmado en la hoja ({', '.join(_partes)})."
                            )
                            st.rerun()
                    else:
                        st.info("No hay cambios para guardar.")


with tab_personal:
    # -------------------------------------------------------------
    # Personal: une las distintas formas en que alguien firmo su nombre y
    # desambigua nombres repetidos por local (p. ej. "Ana" en Colon es una
    # persona y en Fer213 otra). Lo que se cargue aca se aplica a TODOS los
    # reportes (Cuadre, Liquidacion, Historial...).
    # -------------------------------------------------------------
    st.subheader("👥 Personal")
    st.caption(
        "Cada persona tiene un nombre oficial, su local habitual y los nombres con los que "
        "firmó antes (alias). Los registros se unifican por alias; si un alias lo comparten "
        "dos personas (p. ej. «Ana»), se usa el local del registro para decidir cuál es."
    )

    personal_df = sh.get_personal_df()
    if personal_df.empty:
        st.info("Todavía no hay personal cargado. Agrega a la primera persona abajo.")
    else:
        st.dataframe(
            sh.arrow_safe(
                personal_df.rename(
                    columns={"nombre": "Nombre oficial", "local": "Local habitual",
                             "alias": "Alias (separados por |)", "activo": "Activo"}
                )
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption("Para editar o desactivar a alguien, cambia su fila directo en la hoja «Personal» del Google Sheet.")

    _conflictos = sh.conflictos_alias(personal_df)
    if _conflictos:
        st.error(
            "Hay nombres que apuntan a más de una persona y la app no puede decidir cuál es "
            "(por eso a veces salen separados en Liquidación). Usa «Fusionar personas duplicadas» "
            "o «Reasignar un nombre registrado» para dejar una sola:"
        )
        for c in _conflictos:
            st.markdown(f"- **{c['nombre']}** → " + " · ".join(c["personas"]))

    # Todos los nombres que aparecen en la app, de DONDE vengan: registros de
    # caja, encuestas y reposiciones (campo "Repuso" de los ajustes). Una
    # persona puede estar solo en las encuestas, y de ahi sale "otra persona"
    # en Liquidacion aunque no tenga registros de caja.
    _fuentes_nombres = [
        df[["nombre_original", "local", "nombre", "fecha"]].assign(Origen="Registros")
    ]
    _enc_nombres = sh.get_encuestas_df()
    if not _enc_nombres.empty:
        _fuentes_nombres.append(
            _enc_nombres[["nombre_original", "local", "nombre", "fecha"]].assign(Origen="Encuestas")
        )
    _aj_nombres = sh.get_ajustes_df()
    if not _aj_nombres.empty and (_aj_nombres["repuso_original"] != "").any():
        _aj_rep = _aj_nombres[_aj_nombres["repuso_original"] != ""]
        _fuentes_nombres.append(
            _aj_rep[["repuso_original", "local", "repuso", "fecha"]]
            .rename(columns={"repuso_original": "nombre_original", "repuso": "nombre"})
            .assign(Origen="Reposiciones")
        )
    todos_nombres = pd.concat(_fuentes_nombres, ignore_index=True)

    # Nombres que NO coinciden con ninguna persona/alias de la hoja Personal:
    # son los que hacen aparecer "mas nombres" en Liquidacion y los reportes.
    _mapa_pers = sh.mapa_alias(personal_df)
    sin_asignar = (
        todos_nombres.groupby(["nombre_original", "local"])
        .agg(Apariciones=("nombre", "size"), Origen=("Origen", lambda s: ", ".join(sorted(set(s)))))
        .reset_index()
    )
    sin_asignar = sin_asignar[
        [sh.clave_alias(n) not in _mapa_pers for n in sin_asignar["nombre_original"]]
    ]
    if sin_asignar.empty:
        st.success("Todos los nombres registrados están asignados a una persona del Personal.")
    else:
        oficiales_pers = sorted(set(personal_df["nombre"])) if not personal_df.empty else []
        sin_asignar = sin_asignar.assign(
            Sugerencia=[sh.sugerir_oficial(n, oficiales_pers) for n in sin_asignar["nombre_original"]]
        ).rename(columns={"nombre_original": "Nombre registrado", "local": "Local"})
        st.warning(
            f"{len(sin_asignar)} nombre(s) registrado(s) no están asignados a nadie del Personal, "
            "por eso salen como personas aparte en Liquidación y en los reportes."
        )
        st.dataframe(sh.arrow_safe(sin_asignar), width="stretch", hide_index=True)
        if oficiales_pers:
            u1, u2, u3 = st.columns([2, 2, 1])
            nombre_sin = u1.selectbox(
                "Nombre registrado", sin_asignar["Nombre registrado"].tolist(), key="alias_nombre_sin"
            )
            sugerido = sin_asignar.loc[sin_asignar["Nombre registrado"] == nombre_sin, "Sugerencia"].iloc[0]
            etiquetas_pers = [f"{r['nombre']} · {r['local']}" for _, r in personal_df.iterrows()]
            idx_sug = next(
                (i for i, r in enumerate(personal_df.itertuples()) if r.nombre == sugerido), 0
            )
            destino = u2.selectbox(
                "Es en realidad…", range(len(etiquetas_pers)),
                format_func=lambda i: etiquetas_pers[i], index=idx_sug,
                key=f"alias_destino_{nombre_sin}",
            )
            u3.write("")
            if u3.button("Unir", key="alias_unir"):
                fila_dest = personal_df.iloc[destino]
                alias_nuevos = [a for a in fila_dest["alias"].split("|") if a.strip()]
                if nombre_sin not in alias_nuevos:
                    alias_nuevos.append(nombre_sin)
                sh.actualizar_persona(
                    fila_dest["nombre"], fila_dest["local"],
                    {"nombre": fila_dest["nombre"], "local": fila_dest["local"],
                     "alias": "|".join(alias_nuevos), "activo": fila_dest["activo"] or "Sí"},
                )
                st.success(f"«{nombre_sin}» ahora se cuenta como {fila_dest['nombre']}.")
                st.rerun()
        st.caption("Si es una persona nueva, agrégala más abajo en «Agregar persona».")

    if not personal_df.empty:
        st.markdown("**Reasignar un nombre registrado**")
        st.caption(
            "Para cualquier nombre (de registros de caja, encuestas o reposiciones), esté ya asignado o no: se muestra a quién "
            "se cuenta hoy y puedes cambiarlo. Sirve cuando un nombre quedó unido a la persona "
            "equivocada."
        )
        cuenta_hoy = todos_nombres.drop_duplicates("nombre_original").set_index("nombre_original")["nombre"].to_dict()
        etiquetas_reas = [f"{r['nombre']} · {r['local']}" for _, r in personal_df.iterrows()]
        ra, rb, rc = st.columns([2, 2, 1])
        nombre_reas = ra.selectbox(
            "Nombre registrado",
            sorted(cuenta_hoy),
            format_func=lambda n: f"{n}  →  hoy cuenta como {cuenta_hoy.get(n, n)}",
            key="reasignar_nombre",
        )
        destino_reas = rb.selectbox(
            "Contarlo como…", range(len(etiquetas_reas)),
            format_func=lambda i: etiquetas_reas[i], key=f"reasignar_destino_{nombre_reas}",
        )
        rc.write("")
        if rc.button("Reasignar", key="reasignar_btn"):
            clave_reas = sh.clave_alias(nombre_reas)
            oficial_de = [p["nombre"] for _, p in personal_df.iterrows() if sh.clave_alias(p["nombre"]) == clave_reas]
            if oficial_de and sh.clave_alias(personal_df.iloc[destino_reas]["nombre"]) != clave_reas:
                st.error(
                    f"«{nombre_reas}» es el nombre oficial de una persona cargada: para unirla con "
                    "otra usa «Fusionar personas duplicadas»."
                )
            else:
                nuevos_alias = {}
                for pos, (_, p) in enumerate(personal_df.iterrows()):
                    lista = [a for a in p["alias"].split("|") if a.strip() and sh.clave_alias(a) != clave_reas]
                    nuevos_alias[pos] = lista
                if sh.clave_alias(personal_df.iloc[destino_reas]["nombre"]) != clave_reas:
                    nuevos_alias[destino_reas].append(nombre_reas)
                for pos, (_, p) in enumerate(personal_df.iterrows()):
                    if "|".join(nuevos_alias[pos]) != p["alias"]:
                        sh.actualizar_persona(
                            p["nombre"], p["local"],
                            {"nombre": p["nombre"], "local": p["local"],
                             "alias": "|".join(nuevos_alias[pos]), "activo": p["activo"] or "Sí"},
                        )
                st.success(f"«{nombre_reas}» ahora se cuenta como {personal_df.iloc[destino_reas]['nombre']}.")
                st.rerun()

    st.markdown("**Nombres tal como se registraron**")
    detectados = (
        todos_nombres.groupby(["nombre_original", "local"])
        .agg(Apariciones=("nombre", "size"), Ultimo=("fecha", "max"), Se_muestra_como=("nombre", "first"),
             Origen=("Origen", lambda s: ", ".join(sorted(set(s)))))
        .reset_index()
        .rename(columns={"nombre_original": "Nombre registrado", "local": "Local",
                         "Ultimo": "Última vez", "Se_muestra_como": "Se muestra como"})
        .sort_values(["Nombre registrado", "Local"])
    )
    st.dataframe(sh.arrow_safe(detectados), width="stretch", hide_index=True)

    st.markdown("**Días fuera de su local habitual (rotaciones)**")
    habituales = (
        personal_df[personal_df["nombre"] != ""]
        .drop_duplicates("nombre")[["nombre", "local"]]
        .rename(columns={"local": "Local habitual"})
    )
    rotaciones = df[df["fecha"].between(desde, hasta)].merge(habituales, on="nombre", how="inner")
    rotaciones = rotaciones[rotaciones["local"] != rotaciones["Local habitual"]]
    if rotaciones.empty:
        st.caption("Sin rotaciones en el rango de fechas seleccionado (o falta cargar el Personal).")
    else:
        st.dataframe(
            sh.arrow_safe(
                rotaciones.groupby(["fecha", "nombre", "local", "Local habitual", "turno"])
                .agg(Registros=("id", "count"))
                .reset_index()
                .sort_values(["fecha", "nombre"], ascending=[False, True])
                .rename(columns={"fecha": "Fecha", "nombre": "Persona",
                                 "local": "Registró en", "turno": "Turno"})
            ),
            width="stretch",
            hide_index=True,
        )

    if len(personal_df) >= 2:
        st.markdown("**Fusionar personas duplicadas**")
        st.caption(
            "Si una misma persona quedó cargada dos o más veces (por ejemplo «Yovana», «Yovana Sumir» "
            "y «Yovana Sumiri»), elige cuál se queda y cuáles se unen a ella. Los nombres de las "
            "unidas pasan a ser alias de la que se queda, así que sus registros se cuentan juntos."
        )
        etiquetas_fus = [f"{r['nombre']} · {r['local']}" for _, r in personal_df.iterrows()]
        quedan = st.selectbox(
            "Persona que se queda (nombre oficial correcto)", range(len(etiquetas_fus)),
            format_func=lambda i: etiquetas_fus[i], key="fusion_destino",
        )
        a_unir = st.multiselect(
            "Personas que se unen a ella (se quitan de la lista)",
            [i for i in range(len(etiquetas_fus)) if i != quedan],
            format_func=lambda i: etiquetas_fus[i], key=f"fusion_origen_{quedan}",
        )
        confirma_fusion = st.checkbox(
            "Confirmo que son la misma persona", key=f"fusion_confirma_{quedan}"
        )
        if st.button("Fusionar", key="fusion_btn"):
            if not a_unir:
                st.error("Elige al menos una persona para unir.")
            elif not confirma_fusion:
                st.error("Marca la casilla de confirmación.")
            else:
                destino_f = personal_df.iloc[quedan]
                alias_f = [a for a in destino_f["alias"].split("|") if a.strip()]
                for i in a_unir:
                    origen = personal_df.iloc[i]
                    for a in [origen["nombre"]] + [x for x in origen["alias"].split("|") if x.strip()]:
                        if a != destino_f["nombre"] and a not in alias_f:
                            alias_f.append(a)
                sh.actualizar_persona(
                    destino_f["nombre"], destino_f["local"],
                    {"nombre": destino_f["nombre"], "local": destino_f["local"],
                     "alias": "|".join(alias_f), "activo": destino_f["activo"] or "Sí"},
                )
                for i in a_unir:
                    origen = personal_df.iloc[i]
                    sh.eliminar_persona(origen["nombre"], origen["local"])
                st.success(f"Listo: ahora se cuentan juntos como {destino_f['nombre']}.")
                st.rerun()

    st.markdown("**Agregar persona**")
    with st.form("form_persona", clear_on_submit=True):
        c1, c2 = st.columns(2)
        oficiales_existentes = sorted(set(personal_df["nombre"])) if not personal_df.empty else []
        nombre_existente = c1.selectbox(
            "Nombre oficial (elige uno ya cargado)", ["— Nueva persona —"] + oficiales_existentes,
            help="Útil si la misma persona trabaja en otro local o quieres evitar errores al escribirlo.",
        )
        nombre_nuevo = c1.text_input(
            "…o escribe el nombre oficial de una persona nueva", placeholder="Ej: Ana León"
        )
        nombre_oficial = nombre_nuevo.strip() or (
            "" if nombre_existente == "— Nueva persona —" else nombre_existente
        )
        local_habitual = c2.selectbox("Local habitual", config_df["local"].tolist())
        alias_sel = st.multiselect(
            "Alias (nombres con los que ha firmado)",
            sorted(todos_nombres["nombre_original"].unique()),
            help="Elige todas las formas en que aparece su nombre en los registros.",
        )
        activa = st.checkbox("Sigue trabajando", value=True)
        if st.form_submit_button("Agregar persona"):
            if not nombre_oficial.strip():
                st.error("Elige un nombre oficial de la lista o escribe el de una persona nueva.")
            elif (
                not personal_df.empty
                and ((personal_df["nombre"].str.lower() == nombre_oficial.strip().lower())
                     & (personal_df["local"] == local_habitual)).any()
            ):
                st.error("Esa persona ya está cargada en ese local.")
            else:
                sh.guardar_persona(
                    {
                        "nombre": nombre_oficial.strip(),
                        "local": local_habitual,
                        "alias": "|".join(alias_sel),
                        "activo": "Sí" if activa else "No",
                    }
                )
                st.success("Persona agregada.")
                st.rerun()

    if not personal_df.empty:
        st.markdown("**Editar persona**")
        etiquetas_personal = [f"{r['nombre']} · {r['local']}" for _, r in personal_df.iterrows()]
        idx_edit = st.selectbox(
            "Persona a editar", range(len(etiquetas_personal)),
            format_func=lambda i: etiquetas_personal[i], key="persona_a_editar",
        )
        actual = personal_df.iloc[idx_edit]
        alias_actuales = [a for a in actual["alias"].split("|") if a.strip()]
        opciones_alias = sorted(set(todos_nombres["nombre_original"].unique()) | set(alias_actuales))
        locales_cfg = config_df["local"].tolist()
        with st.form(f"form_editar_persona_{idx_edit}"):
            e1, e2 = st.columns(2)
            nuevo_nombre = e1.text_input("Nombre oficial", value=actual["nombre"])
            nuevo_local = e2.selectbox(
                "Local habitual", locales_cfg,
                index=locales_cfg.index(actual["local"]) if actual["local"] in locales_cfg else 0,
            )
            nuevos_alias = st.multiselect("Alias", opciones_alias, default=alias_actuales)
            sigue = st.checkbox("Sigue trabajando", value=actual["activo"].lower() not in {"no", "false", "0"})
            if st.form_submit_button("Guardar cambios"):
                if not nuevo_nombre.strip():
                    st.error("Escribe el nombre oficial.")
                else:
                    sh.actualizar_persona(
                        actual["nombre"], actual["local"],
                        {
                            "nombre": nuevo_nombre.strip(),
                            "local": nuevo_local,
                            "alias": "|".join(nuevos_alias),
                            "activo": "Sí" if sigue else "No",
                        },
                    )
                    st.success("Cambios guardados.")
                    st.rerun()
