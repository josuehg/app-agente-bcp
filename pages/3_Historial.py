"""
pages/3_Historial.py
---------------------
Vista para el PERSONAL DE TIENDA (cajeros), no para el dueno. La idea es
que puedan revisar los registros -- y las fotos de los vouchers -- de SU
propio local, para controlarse entre ellos (por ejemplo: el que entra en
el turno Tarde revisa que el Cierre de la Mañana quedo bien registrado).

DIFERENCIAS con el Dashboard (pages/2_Dashboard.py):
- El acceso es con el PIN PROPIO de cada local (columna "pin" en la hoja
  Config), no con el PIN unico del dueno. Cada local tiene el suyo.
- Ese PIN identifica automaticamente el local: el cajero NO elige de una
  lista, y no puede ver los registros de las otras 5 tiendas.
- Las fotos se muestran directamente en la pantalla (no como un link para
  hacer clic), para que sea mas rapido de revisar desde el celular.

EL PIN SE COMPARTE CON pages/1_Registro.py: si el cajero ya lo puso ahi,
aca no se lo vuelve a pedir (misma sesion de navegador, ver
sh.pedir_pin_de_local en sheets_utils.py).
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import sheets_utils as sh

st.set_page_config(page_title="Historial - Agente BCP", page_icon="🗂️")
sh.aplicar_estilo()
st.title("🗂️ Historial de turnos")
st.caption(
    "Revisa los registros y fotos de tu local para controlar entre el "
    "equipo que las Aperturas y Cierres quedaron bien anotados."
)

# ---------------------------------------------------------------------
# PIN por local: cada tienda tiene su propio PIN (columna "pin" de la
# hoja "Config"). Con ese PIN se identifica el local automaticamente y
# se bloquea la vista a SOLO esa tienda -- asi un cajero no puede ver
# (ni por error, ni a propósito) los montos de las otras 5.
# ---------------------------------------------------------------------
local = sh.pedir_pin_de_local(
    "Ingresa el PIN de tu local para ver su historial de registros."
)

col_local, col_salir = st.columns([4, 1])
col_local.success(f"Local: **{local}**")
if col_salir.button("Cambiar de PIN"):
    st.session_state["local_autenticado"] = None
    st.rerun()

df = sh.get_registros_df()

if df.empty:
    st.info("Todavia no hay registros guardados.")
    st.stop()

# --- Filtros (simples, pensados para celular) ---
col1, col2 = st.columns(2)
with col1:
    turnos_sel = st.multiselect(
        "Turno", ["Mañana", "Tarde"], default=["Mañana", "Tarde"]
    )
with col2:
    dias_atras = st.selectbox(
        "Ver desde", [7, 14, 30, 90], index=1, format_func=lambda d: f"Ultimos {d} dias"
    )

desde = date.today() - timedelta(days=dias_atras)

df_local = df[
    (df["local"] == local)
    & (df["turno"].isin(turnos_sel))
    & (df["fecha"] >= desde)
].sort_values("timestamp", ascending=False)

# ---------------------------------------------------------------------
# Cuadre rapido: Apertura vs Cierre del mismo turno, solo de este local.
# Le sirve al equipo para ver de un vistazo si algun turno quedo con un
# solo registro (falta el otro) o si el fondo salto de forma rara.
# ---------------------------------------------------------------------
st.subheader(f"Cuadre por turno — {local}")

pivot_turnos = sh.calcular_cuadre_turnos(df_local, ["fecha", "turno"])
pivot_turnos = pivot_turnos.sort_values(["fecha", "turno"], ascending=[False, True])

st.dataframe(
    pivot_turnos.drop(columns=["diferencia"]).rename(
        columns={
            "fecha": "Fecha",
            "turno": "Turno",
            "Apertura": "Apertura (S/)",
            "Cierre": "Cierre (S/)",
            "diferencia_fmt": "Diferencia (S/)",
            "estado": "Estado",
        }
    ),
    use_container_width=True,
    hide_index=True,
)
st.caption(
    "Diferencia con signo: **+** significa que sobró dinero (el Cierre quedó "
    "por encima de la Apertura), **-** que faltó. 🟡 Revisar y 🔴 Diferencia "
    "grande son solo una guía según el monto — no significa necesariamente "
    "un error."
)

# ---------------------------------------------------------------------
# Detalle registro por registro, con fotos, mas facil de revisar en el
# celular que una tabla gigante.
# ---------------------------------------------------------------------
st.subheader(f"Detalle de registros — {local}")

columnas_fotos = [c for c in df_local.columns if c.startswith("foto_")]

if df_local.empty:
    st.info("No hay registros de este local en el rango elegido.")

for _, fila in df_local.iterrows():
    titulo = f"{fila['fecha']} · {fila['turno']} · {fila['tipo']} · {fila['nombre']}"
    with st.expander(titulo):
        colA, colB, colC = st.columns(3)
        colA.metric("Efectivo", f"S/ {fila['efectivo']:,.2f}")
        colB.metric("Tarjeta", f"S/ {fila['tarjeta']:,.2f}")
        colC.metric("Total", f"S/ {fila['total']:,.2f}")

        if pd.notna(fila.get("num_operaciones")) and str(fila.get("num_operaciones")) != "":
            st.caption(f"N° de operaciones: {int(fila['num_operaciones'])}")
        if fila.get("observaciones"):
            st.caption(f"Observaciones: {fila['observaciones']}")

        fotos_presentes = [c for c in columnas_fotos if fila.get(c)]
        if fotos_presentes:
            st.write("**Fotos:**")
            cols_fotos = st.columns(len(fotos_presentes))
            for i, col in enumerate(fotos_presentes):
                with cols_fotos[i]:
                    etiqueta = col.replace("foto_voucher_", "").replace("_", " ").capitalize()
                    imagen_bytes = sh.descargar_imagen_drive(fila[col])
                    if imagen_bytes:
                        st.image(imagen_bytes, use_container_width=True)
                        st.caption(etiqueta)
                    else:
                        st.caption(f"{etiqueta}: no se pudo cargar.")
                        st.markdown(f"[Ver en Drive]({fila[col]})")
        else:
            st.caption("Este registro no tiene fotos.")
