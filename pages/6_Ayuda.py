"""
pages/6_Ayuda.py
------------------
Centro de ayuda: videos cortos y guias sobre como usar la app y el
equipo del Agente BCP. El dueño administra el contenido directamente en
la hoja "Ayuda" del Google Sheet (columnas: titulo, descripcion,
url_video, url_documento) -- sin tocar codigo.

SOBRE LOS VIDEOS DE YOUTUBE: subelos como "No listado" (Unlisted), NO
como "Privado". Un video "No listado" no aparece en busquedas ni en tu
canal publico, pero cualquiera con el link lo puede ver SIN tener que
iniciar sesion en una cuenta de Google especifica -- clave aca, porque
los celulares/computadoras de tienda no necesariamente estan logueados
con una cuenta autorizada. Un video "Privado" exige agregar cuenta por
cuenta de Google con permiso, y ademas no se puede incrustar (embed)
para nadie fuera de esa lista -- no funcionaria en esta pagina.

st.video() de Streamlit acepta el link normal de YouTube
(https://www.youtube.com/watch?v=...) y lo muestra como un reproductor
incrustado, no hace falta nada especial.

No pide PIN: es contenido de ayuda para todo el equipo, no datos
sensibles del negocio.
"""

import streamlit as st

import sheets_utils as sh

st.set_page_config(page_title="Ayuda - Agente BCP", page_icon="❓")
sh.aplicar_estilo()
st.title("❓ Ayuda y videos de uso")
st.caption("Videos cortos y guias sobre como usar la app y el equipo del Agente BCP.")

df = sh.get_ayuda_df()

if df.empty or df["titulo"].astype(str).str.strip().eq("").all():
    st.info(
        "Todavia no hay contenido de ayuda cargado. El dueño lo agrega "
        "en la hoja 'Ayuda' del Google Sheet (sin tocar codigo)."
    )
    st.stop()

for _, fila in df.iterrows():
    if not str(fila.get("titulo", "")).strip():
        continue
    with st.container(border=True):
        st.subheader(fila["titulo"])
        if fila.get("descripcion"):
            st.write(fila["descripcion"])
        if fila.get("url_video"):
            st.video(fila["url_video"])
        if fila.get("url_documento"):
            st.markdown(f"[Ver documento]({fila['url_documento']})")
