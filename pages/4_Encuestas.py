"""
pages/4_Encuestas.py
---------------------
Registro de encuestas NPS del Agente BCP calificadas con 9 o 10, que dan
derecho al incentivo interno de S/ 10. Reemplaza el Google Form
independiente que se usaba para esto.

Usa el MISMO PIN de local que Registro e Historial
(sh.pedir_pin_de_local): si el trabajador ya lo puso en otra pagina, no
se lo vuelve a pedir aca.

IMPORTANTE: marcar una encuesta como "Pagada" es una decision del dueno,
no de quien la registra -- por eso esa accion vive solo en el Dashboard
(pages/7_Dashboard.py), protegido con el PIN del dueno. Aca cualquier
trabajador puede registrar una encuesta nueva y ver el estado (Pendiente
/ Pagada) de las de su local, pero no puede cambiarlo.
"""

import streamlit as st

import sheets_utils as sh

st.set_page_config(page_title="Encuestas NPS - Agente BCP", page_icon="⭐")
sh.aplicar_estilo()
st.title("⭐ Encuestas NPS (incentivo S/ 10)")
st.caption(
    "Registra aqui cada encuesta de satisfaccion del Agente BCP que un "
    "cliente haya calificado con 9 o 10, para que se te reconozca el "
    "incentivo de S/ 10."
)

if "mensaje_guardado_encuesta" in st.session_state:
    st.toast(st.session_state.pop("mensaje_guardado_encuesta"), icon="✅")

if "encuesta_version" not in st.session_state:
    st.session_state["encuesta_version"] = 0
v = st.session_state["encuesta_version"]

local = sh.pedir_pin_de_local("Ingresa el PIN de tu local para registrar una encuesta.")

col_local, col_salir = st.columns([4, 1])
col_local.success(f"Local: **{local}**")
if col_salir.button("Cambiar de PIN"):
    st.session_state["local_autenticado"] = None
    st.rerun()

nombre = st.text_input("Ingrese su nombre", key=f"encuesta_nombre_{v}")
nota = st.radio("Nota que puso el cliente", ["9", "10"], horizontal=True, key=f"encuesta_nota_{v}")

captura_correo = st.file_uploader(
    "Captura del correo con la calificacion",
    type=["jpg", "jpeg", "png"],
    key=f"encuesta_correo_{v}",
)
captura_mensaje_exito = st.file_uploader(
    "Captura del mensaje de 'se completo con exito'",
    type=["jpg", "jpeg", "png"],
    key=f"encuesta_exito_{v}",
)

if "confirmar_encuesta" not in st.session_state:
    st.session_state["confirmar_encuesta"] = False

enviado = st.button(
    "Guardar encuesta",
    use_container_width=True,
    type="primary",
    disabled=st.session_state["confirmar_encuesta"],
)

if enviado:
    errores = []
    if not nombre.strip():
        errores.append("Falta ingresar su nombre.")
    if captura_correo is None:
        errores.append("Falta la captura del correo con la calificacion.")
    if captura_mensaje_exito is None:
        errores.append("Falta la captura del mensaje de 'se completo con exito'.")

    if errores:
        for error_texto in errores:
            st.error(error_texto)
        st.stop()

    # Todo llenado correctamente: mostramos un resumen en una ventana de
    # confirmacion antes de grabar de verdad en la hoja.
    st.session_state["confirmar_encuesta"] = True
    st.rerun()


@st.dialog("¿Confirmar encuesta?")
def _dialogo_confirmar_encuesta():
    st.write(f"Vas a registrar esta encuesta para **{local}**:")
    st.markdown(
        f"""
- **Nombre:** {nombre.strip()}
- **Nota:** {nota}
- **Incentivo:** S/ 10
"""
    )
    col1, col2 = st.columns(2)
    col1.image(captura_correo, caption="Captura del correo", width=140)
    col2.image(captura_mensaje_exito, caption="Captura del mensaje de éxito", width=140)

    st.divider()
    col_confirmar, col_cancelar = st.columns(2)
    confirmar = col_confirmar.button("✅ Sí, guardar", type="primary", use_container_width=True)
    cancelar = col_cancelar.button("✏️ Volver a editar", use_container_width=True)

    if cancelar:
        st.session_state["confirmar_encuesta"] = False
        st.rerun()

    if confirmar:
        try:
            with st.spinner("Guardando encuesta..."):
                ahora = sh.ahora_local()
                prefijo = f"{local}_Encuesta_{nombre.strip()}_{ahora:%Y%m%d_%H%M%S}"

                def _extension(archivo):
                    nombre_original = archivo.name
                    return nombre_original[nombre_original.rfind(".") :] if "." in nombre_original else ""

                datos = {
                    "id": sh.nuevo_id(),
                    "timestamp": ahora.replace(tzinfo=None).isoformat(timespec="seconds"),
                    "fecha": ahora.date().isoformat(),
                    "local": local,
                    "nombre": nombre.strip(),
                    "nota": nota,
                    "incentivo": 10,
                    "estado_pago": "Pendiente",
                    "captura_correo": sh.subir_foto(
                        captura_correo, f"{prefijo}_correo{_extension(captura_correo)}"
                    ),
                    "captura_mensaje_exito": sh.subir_foto(
                        captura_mensaje_exito, f"{prefijo}_exito{_extension(captura_mensaje_exito)}"
                    ),
                }
                sh.guardar_encuesta(datos)
        except Exception as error:
            st.error(
                "No se pudo guardar: parece que se corto la conexion a "
                "internet mientras se subian las capturas. Nada se perdio, "
                "solo vuelve a apretar 'Sí, guardar'."
            )
            st.caption(f"Detalle tecnico: {error}")
            st.stop()

        st.session_state["confirmar_encuesta"] = False
        st.session_state["encuesta_version"] += 1
        st.session_state["mensaje_guardado_encuesta"] = (
            f"Encuesta de {nombre.strip()} (nota {nota}) guardada correctamente."
        )
        st.rerun()


if st.session_state["confirmar_encuesta"]:
    _dialogo_confirmar_encuesta()

# ---------------------------------------------------------------------
# Resumen de este local: cuanto esta pendiente de pago, y el detalle.
# Estado de pago es de SOLO LECTURA aca -- se marca desde el Dashboard.
# ---------------------------------------------------------------------
st.divider()
st.subheader(f"Encuestas registradas — {local}")

df = sh.get_encuestas_df()
df_local = df[df["local"] == local] if not df.empty else df

if df_local.empty:
    st.info("Todavia no hay encuestas registradas para este local.")
else:
    df_local = df_local.sort_values("timestamp", ascending=False)
    pendientes = df_local[df_local["estado_pago"] == "Pendiente"]
    pagadas = df_local[df_local["estado_pago"] == "Pagada"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Encuestas registradas", len(df_local))
    col2.metric(
        "Pendientes de pago",
        f"{len(pendientes)} (S/ {pendientes['incentivo'].sum():,.2f})",
    )
    col3.metric(
        "Ya pagadas",
        f"{len(pagadas)} (S/ {pagadas['incentivo'].sum():,.2f})",
    )

    st.dataframe(
        df_local[["fecha", "nombre", "nota", "incentivo", "estado_pago"]].rename(
            columns={
                "fecha": "Fecha",
                "nombre": "Nombre",
                "nota": "Nota",
                "incentivo": "Incentivo (S/)",
                "estado_pago": "Estado",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption("El pago se marca desde el Dashboard del dueño; aqui solo puedes ver el estado.")
