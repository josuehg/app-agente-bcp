"""
pages/5_Campanas.py
--------------------
Vitrina de las campañas vigentes del BCP para que todo el personal las
vea en un solo lugar. El dueño las administra directamente en la hoja
"Campañas" del Google Sheet (columnas: titulo, descripcion,
fecha_inicio, fecha_fin, link) -- sin tocar codigo, igual que ya hace
con el fondo minimo en "Config".

Una campaña se considera VIGENTE si hoy cae entre fecha_inicio y
fecha_fin. Si se deja una de esas fechas vacia, no se aplica ese limite
(por ejemplo, sin fecha_fin = campaña indefinida hasta que le pongas
una fecha o borres la fila).

No pide PIN: es informacion general para todo el equipo, no datos
sensibles del negocio.
"""

from datetime import date

import pandas as pd
import streamlit as st

import sheets_utils as sh

st.set_page_config(page_title="Campañas - Agente BCP", page_icon="📢")
sh.aplicar_estilo()
st.title("📢 Campañas vigentes del BCP")

df = sh.get_campanas_df()

if df.empty:
    st.info(
        "Todavia no hay campañas cargadas. El dueño las agrega en la "
        "hoja 'Campañas' del Google Sheet (sin tocar codigo)."
    )
    st.stop()

hoy = date.today()

vigente_mask = df.apply(
    lambda fila: (pd.isna(fila["fecha_inicio"]) or fila["fecha_inicio"] <= hoy)
    and (pd.isna(fila["fecha_fin"]) or hoy <= fila["fecha_fin"]),
    axis=1,
)
vigentes = df[vigente_mask].sort_values("fecha_inicio")
resto = df[~vigente_mask].sort_values("fecha_inicio", ascending=False)

if vigentes.empty:
    st.info("No hay campañas vigentes en este momento.")
else:
    for _, fila in vigentes.iterrows():
        with st.container(border=True):
            st.subheader(fila["titulo"])
            if fila.get("descripcion"):
                st.write(fila["descripcion"])
            if pd.notna(fila["fecha_inicio"]) or pd.notna(fila["fecha_fin"]):
                inicio_texto = fila["fecha_inicio"].isoformat() if pd.notna(fila["fecha_inicio"]) else "sin fecha"
                fin_texto = fila["fecha_fin"].isoformat() if pd.notna(fila["fecha_fin"]) else "sin fecha de fin"
                st.caption(f"Vigente: {inicio_texto} — {fin_texto}")
            if fila.get("link"):
                st.markdown(f"[Ver más]({fila['link']})")

if not resto.empty:
    with st.expander("Ver campañas pasadas o futuras"):
        for _, fila in resto.iterrows():
            inicio_texto = fila["fecha_inicio"].isoformat() if pd.notna(fila["fecha_inicio"]) else "sin fecha"
            fin_texto = fila["fecha_fin"].isoformat() if pd.notna(fila["fecha_fin"]) else "sin fecha de fin"
            st.markdown(f"**{fila['titulo']}** ({inicio_texto} — {fin_texto})")
            if fila.get("descripcion"):
                st.caption(fila["descripcion"])
