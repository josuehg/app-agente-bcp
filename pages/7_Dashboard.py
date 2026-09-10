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
# Alertas de fondo bajo (usa el ULTIMO cierre de cada local, sin importar
# el filtro de fecha, porque queremos saber la situacion HOY)
#
# IMPORTANTE: el "fondo" del agente rota entre efectivo y tarjeta segun
# las operaciones del dia (si entra mucho efectivo, la tarjeta baja, y
# viceversa). Por eso la alerta compara el TOTAL (efectivo + tarjeta),
# no solo el efectivo -- comparar solo efectivo dispararia alertas
# falsas cuando el fondo simplemente se movio hacia el lado tarjeta.
# ---------------------------------------------------------------------
st.subheader("🚨 Alertas de fondo")

cierres = df[df["tipo"] == "Cierre"].sort_values("timestamp")
ultimo_cierre_por_local = cierres.groupby("local").tail(1).set_index("local")

alertas = []
for _, fila in config_df.iterrows():
    local = fila["local"]
    fondo_minimo = fila["fondo_minimo"]
    if local in ultimo_cierre_por_local.index:
        fondo_actual = ultimo_cierre_por_local.loc[local, "total"]
        if pd.notna(fondo_actual) and fondo_actual < fondo_minimo:
            alertas.append((local, fondo_actual, fondo_minimo))

if alertas:
    for local, fondo_actual, fondo_minimo in alertas:
        st.error(
            f"**{local}**: fondo total en S/ {fondo_actual:,.2f} "
            f"(minimo configurado: S/ {fondo_minimo:,.2f})"
        )
else:
    st.success("Todos los locales estan por encima de su fondo minimo. 👍")

st.caption(
    "El fondo minimo de cada local se edita directamente en la hoja "
    "'Config' del Google Sheet, sin tocar codigo."
)

# ---------------------------------------------------------------------
# KPIs rapidos
# ---------------------------------------------------------------------
col1, col2, col3 = st.columns(3)
col1.metric("Registros en el rango", len(df_filtrado))

# Fondo total AHORA = suma del ULTIMO cierre de cada local (no la suma de
# todos los cierres del rango, que no significa nada). Usa el mismo
# ultimo_cierre_por_local de las alertas, sin el filtro de fechas, pero
# respetando el filtro de locales.
fondo_actual_total = ultimo_cierre_por_local.loc[
    ultimo_cierre_por_local.index.isin(locales_sel), "total"
].sum()
col2.metric(
    "Fondo total actual (último cierre de cada local)",
    f"S/ {fondo_actual_total:,.2f}",
    help="Suma del total (efectivo + tarjeta) del último Cierre registrado de cada local seleccionado.",
)
col3.metric(
    "Operaciones totales",
    int(df_filtrado["num_operaciones"].fillna(0).sum()),
)

# ---------------------------------------------------------------------
# Graficos
# ---------------------------------------------------------------------
st.subheader("📅 Operaciones por día")
st.caption(
    "Número de operaciones registradas en los Cierres, día a día (suma de "
    "todos los locales seleccionados)."
)

_cierres_ops = df_filtrado[df_filtrado["tipo"] == "Cierre"].copy()
_cierres_ops["num_operaciones"] = pd.to_numeric(
    _cierres_ops["num_operaciones"], errors="coerce"
).fillna(0)

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

# ---------------------------------------------------------------------
# Fondo CONSOLIDADO por dia: la sumatoria de todos los locales y como
# va variando dia a dia. Para cada dia se toma el ULTIMO Cierre de cada
# local ese dia; si un local no cerro ese dia, se arrastra su ultimo
# cierre anterior (ffill). Asi la linea es el "cuanto dinero hay en
# total" al cierre de cada dia, no un promedio ni una suma de flujos.
# ---------------------------------------------------------------------
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

# ---------------------------------------------------------------------
# Continuidad entre días: el ULTIMO Cierre de un día vs la PRIMERA
# Apertura del siguiente día con actividad, por local. Deberían coincidir
# (el fondo se queda guardado). Si no, alguien movió la caja cuando el
# local estaba cerrado, o hubo un error al registrar -> posible faltante.
# Lo de adentro del día (cortes parciales) se ve en "Cuadre por turno".
# ---------------------------------------------------------------------
st.subheader("🔗 Continuidad entre días (cierre vs apertura siguiente)")
st.caption(
    "El fondo se queda guardado de un día para otro. Si el último Cierre de "
    "un día no coincide con la primera Apertura del día siguiente, alguien "
    "movió la caja cerrado el local (o hubo un error al registrar)."
)


def _semaforo_continuidad(diferencia: float) -> str:
    # Mas estricto que el cuadre por turno: de un dia al otro el local
    # esta cerrado, no hay operaciones que muevan el fondo -- cualquier
    # diferencia real es sospechosa.
    dif_abs = abs(diferencia)
    if dif_abs <= 1.0:
        return "✅ Coincide"
    if dif_abs <= 10.0:
        return "🟡 Revisar"
    return "🔴 Diferencia grande"


registros_sel = df[df["local"].isin(locales_sel)].sort_values("timestamp")
filas_continuidad = []
for nombre_local, grupo_local in registros_sel.groupby("local", sort=True):
    ult_cierre_dia = (
        grupo_local[grupo_local["tipo"] == "Cierre"].groupby("fecha").tail(1).set_index("fecha")
    )
    prim_apertura_dia = (
        grupo_local[grupo_local["tipo"] == "Apertura"].groupby("fecha").head(1).set_index("fecha")
    )
    dias_con_apertura = sorted(d for d in prim_apertura_dia.index if pd.notna(d))
    for dia_cierre in sorted(d for d in ult_cierre_dia.index if pd.notna(d)):
        posteriores = [d for d in dias_con_apertura if d > dia_cierre]
        if not posteriores:
            continue
        dia_apertura = posteriores[0]
        fila_cierre = ult_cierre_dia.loc[dia_cierre]
        fila_apertura = prim_apertura_dia.loc[dia_apertura]
        cierre = _num(fila_cierre.get("total"))
        apertura = _num(fila_apertura.get("total"))
        diferencia = apertura - cierre
        filas_continuidad.append(
            {
                "_fecha": dia_cierre,
                "_id_cierre": str(fila_cierre.get("id", "")),
                "_id_apertura": str(fila_apertura.get("id", "")),
                "Local": nombre_local,
                "Cierre del día": dia_cierre,
                "Cierre (S/)": round(cierre, 2),
                "Abre el día": dia_apertura,
                "Apertura (S/)": round(apertura, 2),
                "Diferencia (S/)": f"{diferencia:+,.2f}",
                "Estado": _semaforo_continuidad(diferencia),
            }
        )

cont_df = pd.DataFrame(filas_continuidad)
if cont_df.empty:
    st.caption("Todavía no hay días consecutivos con Cierre y Apertura para comparar.")
else:
    cont_df = cont_df[
        (cont_df["_fecha"] >= desde) & (cont_df["_fecha"] <= hasta)
    ].sort_values("_fecha", ascending=False)
    if cont_df.empty:
        st.caption("No hay comparaciones en el rango de fechas seleccionado.")
    else:
        con_diferencia = int((cont_df["Estado"] == "🔴 Diferencia grande").sum())
        if con_diferencia:
            st.error(
                f"⚠️ {con_diferencia} caso(s) donde el fondo cambió entre el "
                f"cierre de un día y la apertura del siguiente."
            )
        else:
            st.success("Todos los cierres coinciden con la apertura del día siguiente. 👍")
        st.dataframe(
            sh.arrow_safe(cont_df.drop(columns=["_fecha", "_id_cierre", "_id_apertura"])),
            width="stretch",
            hide_index=True,
        )

        # Detalle campo por campo de los casos 🟡 / 🔴.
        sospechosos = cont_df[cont_df["Estado"] != "✅ Coincide"]
        for _, fila in sospechosos.iterrows():
            titulo = (
                f"{fila['Local']} · cierre {fila['Cierre del día']} → apertura "
                f"{fila['Abre el día']} · {fila['Diferencia (S/)']} · {fila['Estado']}"
            )
            with st.expander(titulo):
                if (
                    fila["_id_cierre"] in _registros_por_id.index
                    and fila["_id_apertura"] in _registros_por_id.index
                ):
                    st.caption(
                        "Compara el Cierre de un día con la Apertura del siguiente. "
                        "Deberían ser idénticos; si un campo cambió, ahí está el problema "
                        "(o fue un retiro de efectivo hecho a propósito esa noche)."
                    )
                    _comparar_par(
                        _registros_por_id.loc[fila["_id_cierre"]],
                        _registros_por_id.loc[fila["_id_apertura"]],
                        "Cierre día anterior",
                        "Apertura día siguiente",
                    )
                else:
                    st.caption("No se encontraron los dos registros para comparar.")

# ---------------------------------------------------------------------
# Cuadre por turno (cortes Apertura -> Cierre)
#
# Un turno puede tener VARIOS cortes (cierres parciales: se cierra, se
# retira/ingresa efectivo a proposito, se vuelve a abrir). Cada corte se
# mide contra su propia Apertura, asi que lo que se mueve a proposito
# entre cortes no ensucia el calculo. Ver cuadre.py.
# ---------------------------------------------------------------------
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
            }
        ).drop(columns=["diferencia"])
    ),
    width="stretch",
    hide_index=True,
)
st.caption(
    "**+** = sobró (el Cierre quedó por encima de la Apertura), **−** = faltó. "
    "🟡 Revisar / 🔴 Diferencia grande son una guía según el monto. "
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
                ).drop(columns=["diferencia"])
            ),
            width="stretch",
            hide_index=True,
        )

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

# ---------------------------------------------------------------------
# Acumulado de diferencias por persona
#
# Cada corte se le atribuye a quien lo ABRIO (si el Cierre quedo a otro
# nombre, igual va a quien abrio). Aca se suma, en el rango de fechas
# filtrado, cuanto descuadre acumula cada persona -- para ver de un
# vistazo si alguien viene arrastrando diferencias.
# ---------------------------------------------------------------------
st.subheader("👤 Acumulado de diferencias por persona")
st.caption(
    "En el rango de fechas filtrado. La diferencia de cada corte se le "
    "atribuye a quien abrió. Ordenado por descuadre total (sin importar el signo)."
)

acumulado = sh.acumulado_por_persona(cortes)
if acumulado.empty:
    st.caption("Todavía no hay cortes completos en el rango seleccionado.")
else:
    st.dataframe(
        sh.arrow_safe(
            acumulado.rename(
                columns={
                    "nombre": "Persona",
                    "n_cortes": "Cortes",
                    "diferencia_fmt": "Diferencia neta (S/)",
                    "descuadre_abs": "Descuadre total (S/)",
                }
            ).drop(columns=["diferencia"])
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
                     "apertura", "cierre", "diferencia_fmt", "estado"]
                ].rename(
                    columns={
                        "fecha": "Fecha", "local": "Local", "turno": "Turno",
                        "corte": "Corte", "nombre_cierre": "Cerró",
                        "apertura": "Apertura (S/)", "cierre": "Cierre (S/)",
                        "diferencia_fmt": "Diferencia (S/)", "estado": "Estado",
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

# ---------------------------------------------------------------------
# Tabla consolidada
# ---------------------------------------------------------------------
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

# ---------------------------------------------------------------------
# Incentivos por encuestas NPS (S/ 10 por encuesta calificada 9 o 10)
#
# Viene del incentivo interno del negocio: reemplaza el Google Form
# aparte que se usaba para esto. El personal registra cada encuesta
# desde pages/4_Encuestas.py (con el PIN de su local); cambiar el
# estado de pago SOLO se hace aca, protegido con tu PIN de dueno, para
# que nadie pueda marcar su propio incentivo como pagado (o invalidarlo)
# sin que tu lo hayas revisado.
# ---------------------------------------------------------------------
st.divider()
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
