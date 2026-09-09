"""
pages/3_Historial.py
---------------------
Vista para el PERSONAL DE TIENDA (cajeros), no para el dueno. La idea es
que puedan revisar los registros -- y las fotos de los vouchers -- de SU
propio local, para controlarse entre ellos (por ejemplo: el que entra en
el turno Tarde revisa que el Cierre de la Mañana quedo bien registrado).

DIFERENCIAS con el Dashboard (pages/7_Dashboard.py):
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

from datetime import timedelta

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

desde = sh.hoy_local() - timedelta(days=dias_atras)

df_local = df[
    (df["local"] == local)
    & (df["turno"].isin(turnos_sel))
    & (df["fecha"] >= desde)
].sort_values("timestamp", ascending=False)

if df_local.empty:
    st.info("No hay registros de este local en el rango elegido (turno / días).")
    st.stop()

# ---------------------------------------------------------------------
# Cuadre por turno: cada turno puede tener varios CORTES (cierres
# parciales). Mostramos cada corte con su diferencia y, arriba, el
# subtotal del turno (la suma de sus cortes). Le sirve al equipo para
# ver si un turno quedo sin cerrar o si algun corte salto de forma rara.
# ---------------------------------------------------------------------
st.subheader(f"Cuadre por turno — {local}")

cortes = sh.calcular_cortes(df_local, ["fecha", "turno"])
resumen = sh.resumen_turnos(cortes, ["fecha", "turno"]).sort_values(
    ["fecha", "turno"], ascending=[False, True]
)

# df_local ya viene ordenado por timestamp descendente, asi que la
# primera fila es el registro MAS RECIENTE. Lo usamos para (1) un aviso
# claro arriba y (2) resaltar su turno en la tabla.
fila_reciente = df_local.iloc[0]
fecha_reciente = fila_reciente["fecha"]
turno_reciente = fila_reciente["turno"]
hora_reciente = ""
if pd.notna(fila_reciente["timestamp"]):
    hora_reciente = pd.to_datetime(fila_reciente["timestamp"]).strftime("%H:%M")

st.info(
    f"🆕 **Último registro:** {fila_reciente['tipo']} de {turno_reciente} "
    f"por **{fila_reciente['nombre']}** — {fecha_reciente} {hora_reciente}"
)

tabla_resumen = sh.arrow_safe(
    resumen.rename(
        columns={
            "fecha": "Fecha",
            "turno": "Turno",
            "n_cortes": "Cortes",
            "nombres": "Personas",
            "diferencia_fmt": "Diferencia total (S/)",
            "estado": "Estado",
        }
    ).drop(columns=["diferencia"])
)
# arrow_safe pasa la fecha a texto ("2026-09-08"), asi que comparamos como texto.
_fecha_reciente_txt = str(fecha_reciente)


def _resaltar_turno_reciente(fila):
    # Fila del turno del ultimo registro: fondo amarillo suave + negrita.
    es_reciente = fila["Fecha"] == _fecha_reciente_txt and fila["Turno"] == turno_reciente
    estilo = "background-color: #FFE9B0; font-weight: 700" if es_reciente else ""
    return [estilo] * len(fila)


st.dataframe(
    tabla_resumen.style.apply(_resaltar_turno_reciente, axis=1),
    width="stretch",
    hide_index=True,
)
st.caption(
    "La fila resaltada 🟡 es la del turno del último registro. "
    "**+** = sobró, **−** = faltó. 🟡 Revisar / 🔴 Diferencia grande son una "
    "guía según el monto. ⚠️ Revisar secuencia = falta un Cierre o hay un "
    "Cierre sin Apertura."
)

if not cortes.empty and (cortes["corte"].max() > 1 or "⚠️" in " ".join(resumen["estado"])):
    with st.expander("Ver corte por corte"):
        st.dataframe(
            sh.arrow_safe(
                cortes.sort_values(["fecha", "turno", "corte"], ascending=[False, True, True])
                .rename(
                    columns={
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
                )
                .drop(columns=["diferencia"])
            ),
            width="stretch",
            hide_index=True,
        )

# ---------------------------------------------------------------------
# Detalle registro por registro, con fotos, mas facil de revisar en el
# celular que una tabla gigante.
# ---------------------------------------------------------------------
st.subheader(f"Detalle de registros — {local}")

columnas_fotos = [c for c in df_local.columns if c.startswith("foto_")]

# Ancho fijo (en pixeles) para las fotos de los vouchers. Antes iban a
# ancho completo y se veian enormes, sobre todo la unica de la Apertura.
# A este tamaño se lee el voucher; el check de mas abajo lo agranda.
ANCHO_FOTO = 260

for posicion, (_, fila) in enumerate(df_local.iterrows()):
    titulo = f"{fila['fecha']} · {fila['turno']} · {fila['tipo']} · {fila['nombre']}"
    # El primero de la lista es el mas reciente (df_local va ordenado por
    # hora descendente): lo marcamos y lo dejamos abierto de una.
    es_mas_reciente = posicion == 0
    if es_mas_reciente:
        titulo = f"🆕 {titulo}  ·  (más reciente)"
    with st.expander(titulo, expanded=es_mas_reciente):
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
            for col in fotos_presentes:
                etiqueta = col.replace("foto_voucher_", "").replace("_", " ").capitalize()
                imagen_bytes = sh.descargar_imagen_drive(fila[col])
                if imagen_bytes:
                    # Chica por defecto; con el check se ve a ancho completo.
                    # La imagen la sirve la app (no un link de Drive), asi que
                    # funciona aunque la carpeta de Drive no este compartida
                    # con los correos de las sucursales.
                    ver_grande = st.checkbox(
                        f"🔍 Ver «{etiqueta}» más grande",
                        key=f"zoom_{fila['id']}_{col}",
                    )
                    if ver_grande:
                        st.image(imagen_bytes, width="stretch", caption=etiqueta)
                    else:
                        st.image(imagen_bytes, width=ANCHO_FOTO, caption=etiqueta)
                else:
                    st.caption(f"{etiqueta}: no se pudo cargar.")
        else:
            st.caption("Este registro no tiene fotos.")
