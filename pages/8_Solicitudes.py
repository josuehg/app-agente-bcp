"""
pages/8_Solicitudes.py
-----------------------
Un local pide efectivo o saldo a administracion cuando lo necesita
(sobre todo los locales lejanos). Usa el MISMO PIN de local que Registro e
Historial. La solicitud queda guardada y administracion recibe el aviso por
Telegram; la atiende desde el Dashboard (pestaña "📨 Solicitudes") y aca el
local ve en que estado va.

- "Falta efectivo": la caja se quedo corta de billetes/monedas.
- "Falta saldo": a la caja le sobra efectivo y necesita saldo en la tarjeta.

El total del local (efectivo + tarjeta) no cambia con estos movimientos, por
eso no afectan el cuadre de Apertura/Cierre.
"""

import pandas as pd
import streamlit as st

import sheets_utils as sh

st.set_page_config(page_title="Solicitudes - Agente BCP", page_icon="💵")
sh.aplicar_estilo()
st.title("💵 Solicitar efectivo o saldo")

if "sol_msg" in st.session_state:
    _texto, _alerta_ok = st.session_state.pop("sol_msg")
    st.toast("Solicitud enviada", icon="✅")
    if _alerta_ok:
        st.success(f"✅ {_texto} Administración ya fue avisada.")
    else:
        st.warning(
            f"✅ {_texto} Pero la alerta automática no se pudo enviar: "
            "**llama a administración** para avisarle."
        )

if "sol_version" not in st.session_state:
    st.session_state["sol_version"] = 0
v = st.session_state["sol_version"]  # sufijo de key para vaciar el formulario

local = sh.pedir_pin_de_local("Ingresa el PIN de tu local para solicitar efectivo o saldo.")

col_local, col_salir = st.columns([4, 1])
col_local.success(f"Local: **{local}**")
sh.mostrar_nota_nps(local)
if col_salir.button("Cambiar de PIN"):
    st.session_state["local_autenticado"] = None
    st.rerun()

try:
    solicitudes_df = sh.get_solicitudes_df()
    _lectura_ok = True
except Exception:
    solicitudes_df = pd.DataFrame(columns=sh.COLUMNAS_SOLICITUDES)
    _lectura_ok = False
    st.warning(
        "Google está ocupado y no se pudo leer tus solicitudes anteriores. Puedes enviar una nueva "
        "igual (se vuelve a validar al guardar)."
    )

MENSAJE_ESTADO = {
    "Solicitada": "⏳ **Solicitada** — administración fue avisada y la va a atender.",
    "En proceso": "🔄 **En proceso** — administración ya la verificó y la está completando.",
}

# ---------------------------------------------------------------------
# Solicitudes abiertas de este local
# ---------------------------------------------------------------------
propias = (
    sh.solicitudes_activas(solicitudes_df, local=local) if _lectura_ok and not solicitudes_df.empty else pd.DataFrame()
)
if not propias.empty:
    st.subheader("📌 Tus solicitudes abiertas")
    for _, s in propias.iterrows():
        with st.container(border=True):
            urgente = " · ⚡ **URGENTE**" if str(s["urgente"]).strip() == "Sí" else ""
            st.markdown(
                f"**{s['tipo']}** · S/ {float(s['monto']):,.2f}{urgente}  \n"
                f"Pedida por {s['solicitado_por']} · {s['timestamp']:%d/%m %H:%M} "
                f"(hace {sh.texto_espera(sh.minutos_entre(s['timestamp'], sh.ahora_local().replace(tzinfo=None)))})"
            )
            st.markdown(MENSAJE_ESTADO.get(s["estado"], s["estado"]))
            if s["estado"] == "Solicitada":
                if st.button("Cancelar esta solicitud", key=f"sol_cancel_{s['id']}"):
                    try:
                        sh.solicitud_cancelada(
                            s["id"], "Cancelada por el local", local=local, tipo=s["tipo"], por=local
                        )
                    except Exception:
                        st.error("No se pudo cancelar (Google está ocupado). Intenta de nuevo en un momento.")
                        st.stop()
                    st.rerun()

# ---------------------------------------------------------------------
# Formulario
# ---------------------------------------------------------------------
st.subheader("➕ Nueva solicitud")

OPCIONES_TIPO = {
    "Me falta efectivo (necesito billetes y monedas en la caja)": "Falta efectivo",
    "Me falta saldo (me sobra efectivo y necesito saldo en la tarjeta)": "Falta saldo",
}
etiqueta_tipo = st.radio("¿Qué necesitas?", list(OPCIONES_TIPO), key=f"sol_tipo_{v}")
tipo = OPCIONES_TIPO[etiqueta_tipo]

if tipo == "Falta efectivo":
    st.info(
        "El personal de confianza de tu local te entregará efectivo de la reserva. Después, deposita ese "
        "monto en el mismo agente a la cuenta que te indique administración."
    )
else:
    st.info(
        "Entrega el efectivo que te sobra al personal de confianza para que lo guarde en la reserva. "
        "Administración lo verifica y te transfiere el monto a tu tarjeta."
    )

monto = st.number_input("Monto (S/)", min_value=0.0, step=100.0, format="%.2f", key=f"sol_monto_{v}")
urgente = st.checkbox("⚡ Es urgente (me estoy quedando sin)", key=f"sol_urgente_{v}")

OTRA_PERSONA = "Otra persona (escribir el nombre)"
try:
    _propios, _otros = sh.personal_activo(local)
except Exception:
    _propios, _otros = [], []
if _propios or _otros:
    _opciones = list(_propios) + [n for n, _ in _otros] + [OTRA_PERSONA]
    _etiquetas = {n: f"{n} ({loc})" for n, loc in _otros}
    _sel = st.selectbox(
        "¿Quién solicita?",
        _opciones,
        index=None,
        placeholder="Elige tu nombre",
        format_func=lambda x: _etiquetas.get(x, x),
        key=f"sol_nombre_sel_{v}",
    )
    if _sel == OTRA_PERSONA:
        nombre = st.text_input("Escribe tu nombre completo", key=f"sol_nombre_otro_{v}")
    else:
        nombre = _sel or ""
else:
    nombre = st.text_input("¿Quién solicita?", key=f"sol_nombre_{v}")

nota = st.text_input("Nota (opcional)", placeholder="Ej: hay cola de retiros", key=f"sol_nota_{v}")

# Aviso temprano si ya hay una abierta del mismo tipo (el guardado igual revalida con lectura fresca).
ya_abierta = (
    sh.solicitudes_activas(solicitudes_df, local=local, tipo=tipo)
    if _lectura_ok and not solicitudes_df.empty
    else pd.DataFrame()
)
if not ya_abierta.empty:
    st.warning(
        f"Ya tienes una solicitud abierta de «{tipo}» (S/ {float(ya_abierta.iloc[0]['monto']):,.2f}). "
        "Espera a que se atienda o cancélala arriba."
    )

if st.button("Enviar solicitud", type="primary", width="stretch", disabled=not ya_abierta.empty):
    errores = []
    if monto <= 0:
        errores.append("Escribe el monto que necesitas.")
    if not nombre.strip():
        errores.append("Falta el nombre de quien solicita.")
    if errores:
        for e in errores:
            st.error(e)
        st.stop()
    try:
        with st.spinner("Enviando solicitud..."):
            alerta_ok = sh.guardar_solicitud(
                {
                    "local": local,
                    "tipo": tipo,
                    "monto": round(float(monto), 2),
                    "urgente": "Sí" if urgente else "",
                    "solicitado_por": nombre.strip(),
                    "nota": nota.strip(),
                }
            )
    except sh.SolicitudDuplicada as error:
        st.error(str(error))
        st.stop()
    except Exception:
        st.error(
            "No se pudo guardar la solicitud (Google está ocupado o se cortó la conexión). "
            "Nada se perdió: vuelve a apretar «Enviar solicitud» en un momento."
        )
        st.stop()
    st.session_state["sol_version"] += 1
    st.session_state["sol_msg"] = (f"Solicitud de {tipo.lower()} por S/ {monto:,.2f} enviada.", alerta_ok)
    st.rerun()

# ---------------------------------------------------------------------
# Ultimas solicitudes atendidas o canceladas
# ---------------------------------------------------------------------
if _lectura_ok and not solicitudes_df.empty:
    cerradas = solicitudes_df[
        (solicitudes_df["local"] == local) & (~solicitudes_df["estado"].isin(sh.ESTADOS_SOLICITUD_ACTIVOS))
    ].head(8)
    if not cerradas.empty:
        st.subheader("🕘 Últimas solicitudes")
        tabla = pd.DataFrame(
            {
                "Fecha": cerradas["timestamp"].dt.strftime("%d/%m %H:%M"),
                "Tipo": cerradas["tipo"],
                "Monto (S/)": cerradas["monto"],
                "Estado": cerradas["estado"],
                "Tiempo": [
                    sh.texto_espera(sh.minutos_entre(a, b)) if e == "Completada" else ""
                    for a, b, e in zip(cerradas["timestamp"], cerradas["ts_completado"], cerradas["estado"])
                ],
            }
        )
        st.dataframe(sh.arrow_safe(tabla), width="stretch", hide_index=True)
