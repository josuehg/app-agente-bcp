"""
pages/8_Comisiones.py
----------------------
Estima cuanto genera cada local en comision, a partir del numero de
operaciones registradas en los Cierres, multiplicado por una tarifa
PROMEDIO por operacion.

La comision real del BCP varia por tipo de operacion y por contrato; aca
se usa un solo promedio por operacion, configurable por local en la hoja
'Config' (columna 'soles_por_operacion'). Por eso el resultado es un
ESTIMADO, para tener una idea del mes, no el numero exacto del BCP.

Es una pagina de administracion: pide el mismo PIN que el Dashboard.
"""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

import sheets_utils as sh

st.set_page_config(page_title="Comisiones - Agente BCP", page_icon="💰", layout="wide")
sh.aplicar_estilo()
st.title("💰 Comisiones estimadas")

# ---------------------------------------------------------------------
# PIN de acceso (el mismo del Dashboard)
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

df = sh.get_registros_df()
config_df = sh.get_config_df()

if df.empty:
    st.info("Todavia no hay registros.")
    st.stop()

# ---------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------
hoy = sh.hoy_local()
inicio_mes = hoy.replace(day=1)
fecha_min, fecha_max = df["fecha"].min(), df["fecha"].max()

with st.sidebar:
    st.header("Filtros")
    locales_sel = st.multiselect(
        "Locales", options=sorted(df["local"].unique()), default=list(df["local"].unique())
    )
    rango = st.date_input(
        "Rango de fechas",
        value=(max(fecha_min, inicio_mes), fecha_max),
        min_value=fecha_min,
        max_value=fecha_max,
    )

if isinstance(rango, tuple) and len(rango) == 2:
    desde, hasta = rango
else:
    desde = hasta = rango

# ---------------------------------------------------------------------
# Operaciones por local (van en los Cierres) x tarifa
# ---------------------------------------------------------------------
cierres = df[
    (df["tipo"] == "Cierre")
    & (df["local"].isin(locales_sel))
    & (df["fecha"].between(desde, hasta))
].copy()
cierres["num_operaciones"] = pd.to_numeric(cierres["num_operaciones"], errors="coerce").fillna(0)

ops_por_local = (
    cierres.groupby("local")["num_operaciones"].sum().rename("operaciones").reset_index()
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


def _tipo(local):
    return tipo_por_local.get(local) or sh._tipo_agente_por_nombre(local)


def _tarifa(local):
    valor = tarifa_por_local.get(local)
    if valor is None or pd.isna(valor) or valor <= 0:
        return sh.TARIFA_DEFAULT.get(_tipo(local), sh.TARIFA_DEFAULT["normal"])
    return float(valor)


ops_por_local["tipo_agente"] = ops_por_local["local"].map(_tipo)
ops_por_local["soles_por_operacion"] = ops_por_local["local"].map(_tarifa)
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
total_ops = int(ops_por_local["operaciones"].sum())
total_comision = float(ops_por_local["comision"].sum())
col1, col2, col3 = st.columns(3)
col1.metric("Operaciones totales", f"{total_ops:,}")
col2.metric("Comisión estimada total", f"S/ {total_comision:,.2f}")
dias_rango = max((hasta - desde).days + 1, 1)
col3.metric("Promedio por día", f"S/ {total_comision / dias_rango:,.2f}")

# --- Por tipo de agente ---
por_tipo = (
    ops_por_local.groupby("tipo_agente")
    .agg(operaciones=("operaciones", "sum"), comision=("comision", "sum"))
    .reset_index()
)
st.subheader("Por tipo de agente")
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
st.subheader("Por local")
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
    fig = px.bar(
        ops_por_local,
        x="local",
        y="comision",
        color="tipo_agente",
        labels={"local": "Local", "comision": "Comisión estimada (S/)", "tipo_agente": "Tipo"},
    )
    st.plotly_chart(fig, width="stretch")

st.caption(
    "Para ajustar una tarifa: en la hoja `Config` del Google Sheet, escribe el "
    "valor en la columna `soles_por_operacion` de ese local (y `tipo_agente` = "
    "superagente / normal). Si lo dejas vacío, se usa el promedio por defecto "
    f"(superagente S/ {sh.TARIFA_DEFAULT['superagente']:.3f}, normal "
    f"S/ {sh.TARIFA_DEFAULT['normal']:.3f})."
)
