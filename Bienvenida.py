"""
Bienvenida.py
--------------
Puerta de entrada de la app -- lo primero que ve cualquiera que abra el
link. El comando para correrla sigue siendo el mismo:

    streamlit run Bienvenida.py

AHORA ESTE ARCHIVO CUMPLE DOS ROLES:

1. Es el "mapa" de navegacion de TODA la app. Antes dejabamos que
   Streamlit arme el menu solo mirando la carpeta pages/ (y solo podia
   ordenarlo por el numero al inicio del nombre de archivo, tipo
   "1_Registro.py"). Eso hacia dificil separar visualmente "Dashboard"
   (que es para el dueño, con PIN) del resto de paginas de tienda. Con
   st.navigation() decidimos EXACTAMENTE que paginas van, en que orden,
   y las agrupamos en 2 secciones -- una general arriba, y
   "Administración" abajo, separada -- solo con Dashboard adentro.

   OJO: al usar st.navigation(), Streamlit deja de mirar la carpeta
   pages/ automaticamente -- por eso cada pagina se declara a mano aca
   abajo (con st.Page(...)). Si en el futuro agregas una pagina nueva,
   hay que sumarla tambien en esta lista, si no, no va a aparecer en el
   menu aunque el archivo exista.

2. Tambien contiene la pagina de bienvenida en si (la funcion
   mostrar_bienvenida de aca abajo), para no tener que crear un archivo
   aparte solo para eso.
"""

import os

import streamlit as st

import sheets_utils as sh


def mostrar_bienvenida():
    st.set_page_config(
        page_title="Agente BCP - BCD Farma / Carpe Diem Botica",
        page_icon="💳",
        layout="centered",
    )
    sh.aplicar_estilo()

    # Armamos la ruta a la carpeta "assets" a partir de DONDE ESTA ESTE
    # ARCHIVO (__file__), no del directorio desde el que se ejecuto el
    # comando "streamlit run" -- asi encuentra los logos sin importar
    # desde donde se inicie la app.
    carpeta_app = os.path.dirname(os.path.abspath(__file__))
    carpeta_assets = os.path.join(carpeta_app, "assets")

    def _logo(nombre_archivo):
        ruta = os.path.join(carpeta_assets, nombre_archivo)
        return ruta if os.path.exists(ruta) else None

    st.title("👋 Bienvenido")
    st.caption("Sistema interno de Agente BCP — BCD Farma y Carpe Diem Botica")

    logo_bcd = _logo("bcd_farma.png")
    logo_carpe_diem = _logo("carpe_diem_botica.png")
    logo_bcp = _logo("bcp_agente.png")

    if logo_bcd or logo_carpe_diem or logo_bcp:
        col1, col2, col3 = st.columns(3)
        if logo_bcd:
            col1.image(logo_bcd, width=200)
        if logo_carpe_diem:
            col2.image(logo_carpe_diem, width=130)
        if logo_bcp:
            col3.image(logo_bcp, width=140)
    else:
        st.caption(
            "(No se encontraron los logos: revisa que la carpeta 'assets' "
            "este al lado de Bienvenida.py)"
        )

    st.divider()

    st.write(
        """
        Usa el menu de la izquierda segun lo que necesites hacer:

        **Si trabajas en tienda:**
        - **Registro**: registra la Apertura o el Cierre de caja de tu turno.
        - **Historial**: revisa los registros y fotos de tu local, para controlarse entre el equipo.
        - **Encuestas**: registra las encuestas NPS calificadas con 9 o 10 (incentivo de S/ 10).
        - **Campañas**: revisa las campañas vigentes del BCP.
        - **Ayuda**: videos cortos y guias sobre como usar la app y el equipo.

        **Si eres administración:**
        - **Dashboard**: consolidado de todos los locales, alertas de fondo, cuadre por turno, comisiones estimadas e incentivos pendientes de pago (pide PIN, organizado en pestañas). Lo encuentras aparte, abajo del todo en el menu.
        """
    )

    st.info(
        "Si eres personal de tienda y es tu primera vez, entra a **Registro** "
        "o **Encuestas**: te va a pedir el PIN de tu local."
    )


# ---------------------------------------------------------------------
# Mapa de navegacion: 2 secciones. La primera (sin titulo) es de uso
# diario para el equipo de tienda; "Administración" queda separada
# abajo, con Dashboard como unica pagina ahi adentro.
# ---------------------------------------------------------------------
pagina_bienvenida = st.Page(mostrar_bienvenida, title="Bienvenida", icon="👋", default=True)
pagina_registro = st.Page("pages/1_Registro.py", title="Registro", icon="📝")
pagina_historial = st.Page("pages/3_Historial.py", title="Historial", icon="🗂️")
pagina_encuestas = st.Page("pages/4_Encuestas.py", title="Encuestas", icon="⭐")
pagina_campanas = st.Page("pages/5_Campanas.py", title="Campañas", icon="📢")
pagina_ayuda = st.Page("pages/6_Ayuda.py", title="Ayuda", icon="❓")
pagina_dashboard = st.Page("pages/7_Dashboard.py", title="Dashboard", icon="📊")

navegacion = st.navigation(
    {
        "": [
            pagina_bienvenida,
            pagina_registro,
            pagina_historial,
            pagina_encuestas,
            pagina_campanas,
            pagina_ayuda,
        ],
        # Comisiones vivia aca como pagina aparte; ahora es una pestaña
        # mas dentro del Dashboard (pestaña "💰 Comisiones"), asi que ya
        # no hace falta una pagina de navegacion separada para eso.
        "Administración": [pagina_dashboard],
    }
)
navegacion.run()
