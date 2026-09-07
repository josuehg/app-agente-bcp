"""
pages/2_Dashboard.py
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
col2.metric(
    "Fondo total en cierres (efectivo + tarjeta)",
    f"S/ {df_filtrado.loc[df_filtrado['tipo'] == 'Cierre', 'total'].sum():,.2f}",
)
col3.metric(
    "Operaciones totales",
    int(df_filtrado["num_operaciones"].fillna(0).sum()),
)

# ---------------------------------------------------------------------
# Graficos
# ---------------------------------------------------------------------
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
    st.plotly_chart(fig_dias, use_container_width=True)
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
    st.plotly_chart(fig_evol, use_container_width=True)

# ---------------------------------------------------------------------
# Cuadre por turno (tramos Apertura -> Cierre)
#
# Un turno puede tener VARIOS tramos (cierres parciales: se cierra, se
# retira/ingresa efectivo a proposito, se vuelve a abrir). Cada tramo se
# mide contra su propia Apertura, asi que lo que se mueve a proposito
# entre tramos no ensucia el calculo. Ver cuadre.py.
# ---------------------------------------------------------------------
st.subheader("🔍 Cuadre por turno")
st.caption(
    "Diferencia = Cierre − Apertura de cada tramo (fondo total = efectivo + "
    "tarjeta). Un turno con cierres parciales tiene varios tramos; acá se "
    "muestra la **suma** de sus diferencias."
)

INDICE_TURNO = ["local", "fecha", "turno"]
tramos = sh.calcular_tramos(df_filtrado, INDICE_TURNO)
resumen = sh.resumen_turnos(tramos, INDICE_TURNO)
resumen = resumen.sort_values(["fecha", "local", "turno"], ascending=[False, True, True])

st.dataframe(
    resumen.rename(
        columns={
            "local": "Local",
            "fecha": "Fecha",
            "turno": "Turno",
            "n_tramos": "Tramos",
            "diferencia_fmt": "Diferencia total (S/)",
            "estado": "Estado",
        }
    ).drop(columns=["diferencia"]),
    use_container_width=True,
    hide_index=True,
)
st.caption(
    "**+** = sobró (el Cierre quedó por encima de la Apertura), **−** = faltó. "
    "🟡 Revisar / 🔴 Diferencia grande son una guía según el monto. "
    "⚠️ Revisar secuencia = al turno le falta un Cierre o hay un Cierre sin Apertura."
)

with st.expander("Ver tramo por tramo"):
    if tramos.empty:
        st.caption("No hay tramos en el rango seleccionado.")
    else:
        tramos_orden = tramos.sort_values(
            ["fecha", "local", "turno", "tramo"], ascending=[False, True, True, True]
        )
        st.dataframe(
            tramos_orden.rename(
                columns={
                    "local": "Local",
                    "fecha": "Fecha",
                    "turno": "Turno",
                    "tramo": "Tramo",
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
            ).drop(columns=["diferencia"]),
            use_container_width=True,
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
    st.plotly_chart(fig_dif, use_container_width=True)

# ---------------------------------------------------------------------
# Acumulado de diferencias por persona
#
# Cada tramo se le atribuye a quien lo ABRIO (si el Cierre quedo a otro
# nombre, igual va a quien abrio). Aca se suma, en el rango de fechas
# filtrado, cuanto descuadre acumula cada persona -- para ver de un
# vistazo si alguien viene arrastrando diferencias.
# ---------------------------------------------------------------------
st.subheader("👤 Acumulado de diferencias por persona")
st.caption(
    "En el rango de fechas filtrado. La diferencia de cada tramo se le "
    "atribuye a quien abrió. Ordenado por descuadre total (sin importar el signo)."
)

acumulado = sh.acumulado_por_persona(tramos)
if acumulado.empty:
    st.caption("Todavía no hay tramos completos en el rango seleccionado.")
else:
    st.dataframe(
        acumulado.rename(
            columns={
                "nombre": "Persona",
                "n_tramos": "Tramos",
                "diferencia_fmt": "Diferencia neta (S/)",
                "descuadre_abs": "Descuadre total (S/)",
            }
        ).drop(columns=["diferencia"]),
        use_container_width=True,
        hide_index=True,
    )
    fig_pers = px.bar(
        acumulado.sort_values("diferencia"),
        x="nombre",
        y="diferencia",
        labels={"nombre": "Persona", "diferencia": "Diferencia neta acumulada (S/)"},
    )
    st.plotly_chart(fig_pers, use_container_width=True)

# ---------------------------------------------------------------------
# Tabla consolidada
# ---------------------------------------------------------------------
st.subheader("🗂️ Registros")
columnas_fotos = [c for c in df_filtrado.columns if c.startswith("foto_")]
st.dataframe(
    df_filtrado.drop(columns=columnas_fotos),
    use_container_width=True,
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
        st.dataframe(resumen_por_nombre, use_container_width=True, hide_index=True)

    st.markdown("**Detalle de encuestas (ver capturas y cambiar estado):**")
    for _, fila_encuesta in encuestas_df.sort_values("timestamp", ascending=False).iterrows():
        titulo_encuesta = (
            f"{fila_encuesta['fecha']} · {fila_encuesta['local']} · {fila_encuesta['nombre']} "
            f"· Nota {fila_encuesta['nota']} · {fila_encuesta['estado_pago']}"
        )
        with st.expander(titulo_encuesta):
            col_foto1, col_foto2 = st.columns(2)
            with col_foto1:
                imagen_correo = sh.descargar_imagen_drive(fila_encuesta["captura_correo"])
                if imagen_correo:
                    st.image(imagen_correo, caption="Correo", use_container_width=True)
            with col_foto2:
                imagen_exito = sh.descargar_imagen_drive(fila_encuesta["captura_mensaje_exito"])
                if imagen_exito:
                    st.image(imagen_exito, caption="Mensaje de exito", use_container_width=True)

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
