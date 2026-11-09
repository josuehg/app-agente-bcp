"""
pages/9_Soporte.py
-------------------
El local reporta una falla del agente: internet, cargador o energia, equipo, accesos u otro problema que
requiera soporte de BCP. Usa el MISMO PIN de local que Registro, Solicitudes e Historial. Administracion
recibe el aviso por Telegram, toma el caso (lo gestiona con BCP o el proveedor) desde el Dashboard
(pestaña "🛠️ Soporte") y aca el local ve en que va, agrega novedades y confirma cuando ya funciona.

    Reportada -> En gestión -> Resuelta -> Confirmada      (o Cancelada antes de resolverse)
"""

import pandas as pd
import streamlit as st

import sheets_utils as sh

st.set_page_config(page_title="Soporte - Agente BCP", page_icon="🛠️")
sh.aplicar_estilo()
st.title("🛠️ Reportar una falla")

if "sop_msg" in st.session_state:
    _texto, _alerta_ok = st.session_state.pop("sop_msg")
    st.toast("Reporte enviado", icon="✅")
    if _alerta_ok:
        st.success(f"✅ {_texto} Administración ya fue avisada.")
    else:
        st.warning(
            f"✅ {_texto} Pero la alerta automática no se pudo enviar: **llama a administración** para avisarle."
        )
if "sop_aviso" in st.session_state:
    st.success(st.session_state.pop("sop_aviso"))

if "sop_version" not in st.session_state:
    st.session_state["sop_version"] = 0
v = st.session_state["sop_version"]  # sufijo de key para vaciar el formulario

local = sh.pedir_pin_de_local("Ingresa el PIN de tu local para reportar una falla.")

col_local, col_salir = st.columns([4, 1])
col_local.success(f"Local: **{local}**")
sh.mostrar_nota_nps(local)
if col_salir.button("Cambiar de PIN"):
    st.session_state["local_autenticado"] = None
    st.rerun()

try:
    contacto = sh.get_soporte_contacto()
except Exception:
    contacto = ""
if contacto.strip():
    with st.expander("📞 Teléfonos y datos de soporte"):
        st.text(contacto)

try:
    incidencias_df = sh.get_incidencias_df()
    _lectura_ok = True
except Exception:
    incidencias_df = pd.DataFrame(columns=sh.COLUMNAS_INCIDENCIAS)
    _lectura_ok = False
    st.warning(
        "Google está ocupado y no se pudieron leer tus reportes anteriores. Puedes enviar uno nuevo igual "
        "(se vuelve a validar al guardar)."
    )

MENSAJE_ESTADO = {
    "Reportada": "⏳ **Reportada** — administración fue avisada y la va a tomar.",
    "En gestión": "🔧 **En gestión** — administración ya la está gestionando.",
    "Resuelta": "✅ **Resuelta** — ¿ya funciona todo? Confírmalo aquí abajo.",
}


def _guardar_cambio(accion, *args, mensaje: str, **kwargs) -> None:
    """Ejecuta un cambio de estado; si Google esta ocupado, avisa y NO sigue (nada se pierde: se reintenta)."""
    try:
        accion(*args, **kwargs)
    except Exception:
        st.error("No se pudo guardar (Google está ocupado). Intenta de nuevo en un momento.")
        st.stop()
    st.session_state["sop_aviso"] = mensaje
    st.rerun()


# ---------------------------------------------------------------------
# Reportes abiertos de este local
# ---------------------------------------------------------------------
propias = (
    sh.incidencias_abiertas(incidencias_df, local=local) if _lectura_ok and not incidencias_df.empty else pd.DataFrame()
)
if not propias.empty:
    st.subheader("📌 Tus reportes abiertos")
    ahora_ = sh.ahora_local().replace(tzinfo=None)
    for _, i in propias.iterrows():
        with st.container(border=True):
            st.markdown(
                f"**{sh.etiqueta_tipo_incidencia(i['tipo'])}** · {sh.etiqueta_gravedad(i['gravedad'])}  \n"
                f"Reportó {i['reportado_por']} · {i['timestamp']:%d/%m %H:%M} "
                f"(hace {sh.texto_espera(sh.minutos_entre(i['timestamp'], ahora_))})"
            )
            if str(i["descripcion"]).strip():
                st.caption(f"📝 {i['descripcion']}")
            st.markdown(MENSAJE_ESTADO.get(i["estado"], i["estado"]))
            if str(i["ticket_bcp"]).strip():
                st.caption(f"🎫 N° de ticket: {i['ticket_bcp']}")
            if str(i["estado"]) == "Resuelta" and str(i["solucion"]).strip():
                st.info(f"🛠 Lo que se hizo: {i['solucion']}")
            with st.expander("Ver historial del caso"):
                st.text("\n".join(sh.lineas_bitacora(i["bitacora"])) or "Sin movimientos todavía.")

            if i["estado"] == "Resuelta":
                with st.form(f"sop_resuelta_{i['id']}"):
                    detalle = st.text_input("Si sigue fallando, cuéntanos qué pasa (opcional)")
                    b1, b2 = st.columns(2)
                    funciona = b1.form_submit_button("✅ Ya funciona", type="primary", width="stretch")
                    sigue = b2.form_submit_button("↩️ Sigue fallando", width="stretch")
                if funciona:
                    _guardar_cambio(sh.incidencia_confirmar, i["id"], local, mensaje="¡Listo! Gracias por confirmar.")
                if sigue:
                    _guardar_cambio(
                        sh.incidencia_reabrir, i["id"], local, detalle,
                        mensaje="Avisamos a administración que sigue fallando.",
                    )
            else:
                with st.form(f"sop_novedad_{i['id']}", clear_on_submit=True):
                    novedad = st.text_input("Agregar una novedad", placeholder="Ej: sigue sin internet desde las 9")
                    enviar_nov = st.form_submit_button("Enviar novedad")
                if enviar_nov:
                    if not novedad.strip():
                        st.error("Escribe la novedad.")
                    else:
                        _guardar_cambio(
                            sh.incidencia_novedad, i["id"], local, novedad, por_local=True,
                            mensaje="Novedad enviada a administración.",
                        )
                if i["estado"] == "Reportada" and st.button("Cancelar este reporte", key=f"sop_cancel_{i['id']}"):
                    _guardar_cambio(
                        sh.incidencia_cancelar, i["id"], local, "Cancelada por el local",
                        mensaje="Reporte cancelado.",
                    )

# ---------------------------------------------------------------------
# Formulario
# ---------------------------------------------------------------------
st.subheader("➕ Nuevo reporte")

tipo = st.radio(
    "¿Qué falla?",
    sh.TIPOS_INCIDENCIA,
    format_func=sh.etiqueta_tipo_incidencia,
    key=f"sop_tipo_{v}",
)
with st.expander("💡 Antes de enviar, prueba esto", expanded=True):
    st.markdown("\n".join(f"- {paso}" for paso in sh.GUIA_INCIDENCIA.get(tipo, [])))

OPCIONES_GRAVEDAD = {
    "No puedo operar": "🔴 No puedo operar (el local está parado)",
    "Opero con limitaciones": "🟠 Opero con limitaciones (algo funciona y algo no)",
    "Preventivo": "🟢 Preventivo (hoy no me afecta, pero hay que arreglarlo)",
}
gravedad = st.radio(
    "¿Qué tan grave es?",
    list(OPCIONES_GRAVEDAD),
    format_func=OPCIONES_GRAVEDAD.get,
    index=None,
    key=f"sop_gravedad_{v}",
)
if gravedad == "No puedo operar":
    st.warning("Si no puedes operar, además de enviar el reporte avisa por teléfono a administración.")

descripcion = st.text_area(
    "¿Qué pasa? (desde qué hora, qué mensaje sale...)" + (" — obligatorio" if tipo == "Otro" else ""),
    placeholder="Ej: no hay internet desde las 9:15, la luz del router está en rojo",
    key=f"sop_desc_{v}",
)

OTRA_PERSONA = "Otra persona (escribir el nombre)"
try:
    _propios, _otros = sh.personal_activo(local)
except Exception:
    _propios, _otros = [], []
if _propios or _otros:
    _opciones = list(_propios) + [n for n, _ in _otros] + [OTRA_PERSONA]
    _etiquetas = {n: f"{n} ({loc})" for n, loc in _otros}
    _sel = st.selectbox(
        "¿Quién reporta?",
        _opciones,
        index=None,
        placeholder="Elige tu nombre",
        format_func=lambda x: _etiquetas.get(x, x),
        key=f"sop_nombre_sel_{v}",
    )
    if _sel == OTRA_PERSONA:
        nombre = st.text_input("Escribe tu nombre completo", key=f"sop_nombre_otro_{v}")
    else:
        nombre = _sel or ""
else:
    nombre = st.text_input("¿Quién reporta?", key=f"sop_nombre_{v}")

# Se pueden enviar varios reportes aunque haya otros abiertos; solo se avisa para que no se repitan por error.
ya_abierto = (
    propias[propias["tipo"] == tipo] if not propias.empty else pd.DataFrame()
)
if not ya_abierto.empty:
    st.info(
        f"Ya tienes {len(ya_abierto)} reporte(s) abierto(s) de «{tipo}» (arriba los ves). "
        "Si es el mismo problema, mejor agrega una novedad ahí; si es otro, envíalo."
    )

if st.button("Enviar reporte", type="primary", width="stretch"):
    errores = []
    if not gravedad:
        errores.append("Elige qué tan grave es.")
    if not nombre.strip():
        errores.append("Falta el nombre de quien reporta.")
    if tipo == "Otro" and not descripcion.strip():
        errores.append("Cuéntanos qué pasa (en «Otro» la descripción es obligatoria).")
    if errores:
        for e in errores:
            st.error(e)
        st.stop()
    try:
        with st.spinner("Enviando reporte..."):
            alerta_ok = sh.guardar_incidencia(
                {
                    "local": local,
                    "tipo": tipo,
                    "gravedad": gravedad,
                    "reportado_por": nombre.strip(),
                    "descripcion": " ".join(descripcion.split()),
                }
            )
    except sh.IncidenciaDuplicada as error:
        st.error(str(error))
        st.stop()
    except Exception:
        st.error(
            "No se pudo guardar el reporte (Google está ocupado o se cortó la conexión). "
            "Nada se perdió: vuelve a apretar «Enviar reporte» en un momento."
        )
        st.stop()
    st.session_state["sop_version"] += 1
    st.session_state["sop_msg"] = (f"Reporte de {tipo.lower()} enviado.", alerta_ok)
    st.rerun()

# ---------------------------------------------------------------------
# Ultimos reportes cerrados
# ---------------------------------------------------------------------
if _lectura_ok and not incidencias_df.empty:
    cerrados = incidencias_df[
        (incidencias_df["local"] == local) & (~incidencias_df["estado"].isin(sh.ESTADOS_INCIDENCIA_ABIERTOS))
    ].head(8)
    if not cerrados.empty:
        st.subheader("🕘 Últimos reportes")
        tabla = pd.DataFrame(
            {
                "Fecha": cerrados["timestamp"].dt.strftime("%d/%m %H:%M"),
                "Tipo": cerrados["tipo"],
                "Gravedad": cerrados["gravedad"],
                "Estado": cerrados["estado"],
                "Duración": [
                    sh.texto_espera(sh.minutos_entre(a, b)) if e == "Confirmada" else ""
                    for a, b, e in zip(cerrados["timestamp"], cerrados["ts_cierre"], cerrados["estado"])
                ],
            }
        )
        st.dataframe(sh.arrow_safe(tabla), width="stretch", hide_index=True)
