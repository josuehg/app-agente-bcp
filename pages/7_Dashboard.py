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
tab_resumen, tab_cuadre, tab_operaciones, tab_comisiones, tab_incentivos, tab_registros = st.tabs(
    ["🏠 Resumen", "🔍 Cuadre", "📈 Operaciones", "💰 Comisiones", "⭐ Incentivos", "🗂️ Registros"]
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
                            st.caption(
                                f"S/ {aj['monto']:+,.2f} — {aj['motivo']} "
                                f"(autorizó: {aj['autorizado_por'] or '—'})"
                            )

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
                        col_monto, col_quien = st.columns(2)
                        monto_ajuste = col_monto.number_input(
                            "Monto (negativo = retiro, positivo = ingreso)",
                            value=round(fila["_restante"], 2),
                            step=10.0,
                            key=f"ajuste_monto_{clave_ajuste}",
                        )
                        autorizo = col_quien.text_input(
                            "Quién autoriza", key=f"ajuste_quien_{clave_ajuste}"
                        )
                        motivo_ajuste = st.text_area(
                            "Motivo", key=f"ajuste_motivo_{clave_ajuste}",
                            placeholder="Ej: retiro de efectivo para depósito en banco",
                        )
                        if st.button("✅ Registrar ajuste", key=f"ajuste_btn_{clave_ajuste}"):
                            if not motivo_ajuste.strip() or not autorizo.strip():
                                st.error("Completa quién autoriza y el motivo antes de guardar.")
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
        if ajuste_total == 0 or pd.isna(diferencia) or str(fila["estado"]).startswith("⚠️"):
            return pd.Series({"estado": fila["estado"], "_ajuste_total": ajuste_total, "_restante": diferencia})
        restante = diferencia - ajuste_total
        if abs(restante) <= sh.UMBRAL_VERDE:
            nuevo_estado = "🔷 Autorizado"
        else:
            nuevo_estado = "🔴 Diferencia"
        return pd.Series({"estado": nuevo_estado, "_ajuste_total": ajuste_total, "_restante": restante})


    _ajustado_turno = resumen.apply(_con_ajuste_turno, axis=1)
    resumen["estado"] = _ajustado_turno["estado"]
    resumen["_ajuste_total"] = _ajustado_turno["_ajuste_total"]
    resumen["_restante"] = _ajustado_turno["_restante"]

    # Turnos ya "🔷 Autorizado": se pisa el Estado de los cortes de ese
    # turno que YA estaban en "🔴 Diferencia" (en "cortes" mismo, asi que
    # se ve igual en "Ver corte por corte" y en el detalle por persona,
    # mas abajo). Un corte que ya estaba "✅ Cuadrado" por si solo NO se
    # toca -- el ajuste se calcula sobre la SUMA del turno, y si el turno
    # tiene varios cortes, marcar el que ya estaba bien como "Autorizado"
    # daria a entender que tenia algo que explicar cuando no era asi.
    _turnos_autorizados = set(
        resumen.loc[resumen["estado"] == "🔷 Autorizado", ["local", "fecha", "turno"]]
        .itertuples(index=False, name=None)
    )
    if _turnos_autorizados:
        cortes.loc[
            [
                (l, f, t) in _turnos_autorizados and est == "🔴 Diferencia"
                for l, f, t, est in zip(
                    cortes["local"], cortes["fecha"], cortes["turno"], cortes["estado"]
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
            ).drop(columns=["diferencia", "_ajuste_total", "_restante"])
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
    # aun no cuadran ni estan ya autorizados, y que no son un problema de secuencia (esos se
    # arreglan registrando bien, no con un ajuste de monto).
    _sospechosos_turno = resumen[
        ~resumen["estado"].isin(["✅ Cuadrado", "🔷 Autorizado"])
        & ~resumen["estado"].astype(str).str.startswith("⚠️")
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

    ESTADOS_PAGO_ENCUESTA = ["Pendiente", "Pagada", "No válido"]

    encuestas_df = sh.get_encuestas_df()

    if encuestas_df.empty:
        st.caption("Todavia no se ha registrado ninguna encuesta NPS.")
    else:
        pendientes_df = encuestas_df[encuestas_df["estado_pago"] == "Pendiente"]
        pagadas_df = encuestas_df[encuestas_df["estado_pago"] == "Pagada"]
        no_validas_df = encuestas_df[encuestas_df["estado_pago"] == "No válido"]

        colA, colB, colC, colD = st.columns(4)
        colA.metric("Encuestas totales", len(encuestas_df))
        colB.metric(
            "Pendientes de pago",
            f"{len(pendientes_df)} (S/ {pendientes_df['incentivo'].sum():,.2f})",
        )
        colC.metric(
            "Ya pagadas",
            f"{len(pagadas_df)} (S/ {pagadas_df['incentivo'].sum():,.2f})",
        )
        colD.metric("No validas", len(no_validas_df))

        st.caption("Cuanto se le debe a cada trabajador (solo lo pendiente):")
        if pendientes_df.empty:
            st.success("No hay incentivos pendientes de pago. 👍")
        else:
            resumen_por_nombre = (
                pendientes_df.groupby(["local", "nombre"])["incentivo"]
                .agg(["count", "sum"])
                .reset_index()
                .rename(
                    columns={
                        "local": "Local",
                        "nombre": "Nombre",
                        "count": "Encuestas pendientes",
                        "sum": "Monto pendiente (S/)",
                    }
                )
                .sort_values("Monto pendiente (S/)", ascending=False)
            )
            st.dataframe(sh.arrow_safe(resumen_por_nombre), width="stretch", hide_index=True)

        st.markdown("**Detalle de encuestas (ver capturas y cambiar estado):**")
        for _, fila_encuesta in encuestas_df.sort_values("timestamp", ascending=False).iterrows():
            titulo_encuesta = (
                f"{fila_encuesta['fecha']} · {fila_encuesta['local']} · {fila_encuesta['nombre']} "
                f"· Nota {fila_encuesta['nota']} · {fila_encuesta['estado_pago']}"
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

                estado_actual = fila_encuesta["estado_pago"]
                if estado_actual not in ESTADOS_PAGO_ENCUESTA:
                    estado_actual = "Pendiente"
                nuevo_estado = st.selectbox(
                    "Estado",
                    ESTADOS_PAGO_ENCUESTA,
                    index=ESTADOS_PAGO_ENCUESTA.index(estado_actual),
                    key=f"estado_{fila_encuesta['id']}",
                )
                if nuevo_estado != estado_actual:
                    sh.actualizar_estado_pago(fila_encuesta["id"], nuevo_estado)
                    st.rerun()
