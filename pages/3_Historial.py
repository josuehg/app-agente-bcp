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
import plotly.express as px
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
    opcion_rango = st.selectbox(
        "Ver desde",
        [7, 14, 30, "mes", 90],
        index=3,
        format_func=lambda d: "Este mes" if d == "mes" else f"Ultimos {d} dias",
    )

desde = (
    sh.hoy_local().replace(day=1)
    if opcion_rango == "mes"
    else sh.hoy_local() - timedelta(days=opcion_rango)
)

df_local = df[
    (df["local"] == local)
    & (df["turno"].isin(turnos_sel))
    & (df["fecha"] >= desde)
].sort_values("timestamp", ascending=False)

if df_local.empty:
    st.info("No hay registros de este local en el rango elegido (turno / días).")
    st.stop()

tab_cuadre, tab_continuidad, tab_acumulado, tab_registros = st.tabs(
    ["🔍 Cuadre por turno", "🔗 Continuidad", "👤 Acumulado por persona", "🗂️ Detalle de registros"]
)

with tab_cuadre:
    # -------------------------------------------------------------
    # Cuadre por turno: cada turno puede tener varios CORTES (cierres
    # parciales). Mostramos cada corte con su diferencia y, arriba, el
    # subtotal del turno (la suma de sus cortes). Le sirve al equipo para
    # ver si un turno quedo sin cerrar o si algun corte salto de forma rara.
    # -------------------------------------------------------------
    st.subheader(f"Cuadre por turno — {local}")

    cortes = sh.calcular_cortes(df_local, ["fecha", "turno"])
    resumen = sh.resumen_turnos(cortes, ["fecha", "turno"]).sort_values(
        ["fecha", "turno"], ascending=[False, True]
    )

    # Si administración ya registró un ajuste (retiro/ingreso autorizado)
    # para un turno, el estado pasa a "🔷 Autorizado" -- igual que en el
    # Dashboard. Aquí SOLO se muestra el estado: el motivo y quién autorizó
    # son información de administración, no se muestran en Historial.
    _ajustes_turno_local = sh.get_ajustes_df()
    if not _ajustes_turno_local.empty:
        _ajustes_turno_local = _ajustes_turno_local[
            (_ajustes_turno_local["local"] == local) & (_ajustes_turno_local["turno"] != "")
        ]
    if resumen.empty or _ajustes_turno_local.empty:
        pass
    else:
        _ajuste_por_fecha_turno = (
            _ajustes_turno_local.groupby(["fecha", "turno"])["monto"].sum().to_dict()
        )

        def _con_ajuste_turno_local(fila):
            ajuste_total = float(_ajuste_por_fecha_turno.get((fila["fecha"], fila["turno"]), 0.0))
            diferencia = fila["diferencia"]
            if ajuste_total == 0 or pd.isna(diferencia) or str(fila["estado"]).startswith("⚠️"):
                return fila["estado"]
            restante = diferencia - ajuste_total
            if abs(restante) <= sh.UMBRAL_VERDE:
                return "🔷 Autorizado"
            return "🔴 Diferencia"

        resumen["estado"] = resumen.apply(_con_ajuste_turno_local, axis=1)

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
                "observaciones": "Observaciones",
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
        "**+** = sobró, **−** = faltó. 🔴 Diferencia = más de S/1 sin "
        "explicar. ⚠️ Revisar secuencia = falta un Cierre o hay un "
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
                    .drop(columns=["diferencia", "observaciones"])
                ),
                width="stretch",
                hide_index=True,
            )

with tab_continuidad:
    # -------------------------------------------------------------
    # Continuidad: compara cada Cierre con la Apertura que le sigue
    # (mismo turno / entre turnos / entre días) -- igual que en el
    # Dashboard, pero SOLO LECTURA: se ve el Estado, no el motivo ni
    # quién autorizó un ajuste (eso es información de administración).
    #
    # Se calcula sobre TODO el historial del local (no solo lo filtrado
    # por fecha), para no perder el salto justo en el borde del rango
    # -- recien despues se filtra lo que se muestra.
    # -------------------------------------------------------------
    st.subheader(f"Continuidad — {local}")
    st.caption(
        "Compara cada Cierre con la Apertura que le sigue (mismo turno, "
        "entre turnos, o entre días): el fondo debería quedar guardado de "
        "un salto a otro. Aquí solo se ve el Estado."
    )

    saltos_local = sh.calcular_saltos(df[df["local"] == local])

    _ajustes_todas_local = sh.get_ajustes_df()
    if not _ajustes_todas_local.empty:
        _ajustes_todas_local = _ajustes_todas_local[_ajustes_todas_local["local"] == local]
    if _ajustes_todas_local.empty:
        _ajuste_por_salto_local = {}
        _ajuste_viejo_por_fecha_local = {}
    else:
        _ajustes_salto_local = _ajustes_todas_local[_ajustes_todas_local["salto_id_cierre"] != ""]
        _ajuste_por_salto_local = _ajustes_salto_local.groupby("salto_id_cierre")["monto"].sum().to_dict()
        _ajustes_viejo_local = _ajustes_todas_local[
            (_ajustes_todas_local["turno"] == "") & (_ajustes_todas_local["salto_id_cierre"] == "")
        ]
        _ajuste_viejo_por_fecha_local = _ajustes_viejo_local.groupby("fecha")["monto"].sum().to_dict()

    filas_cont_local = []
    for _, s in saltos_local.iterrows():
        if pd.isna(s["diferencia"]):
            continue
        ajuste_nuevo = float(_ajuste_por_salto_local.get(s["id_cierre"], 0.0))
        ajuste_viejo = 0.0
        if s["tipo_salto"] == "Entre días":
            ajuste_viejo = float(_ajuste_viejo_por_fecha_local.get(s["fecha_cierre"], 0.0))
        ajuste_total = ajuste_nuevo + ajuste_viejo
        diferencia = s["diferencia"]
        restante = diferencia - ajuste_total
        if ajuste_total != 0 and abs(restante) <= sh.UMBRAL_VERDE:
            estado = "🔷 Autorizado"
        elif abs(restante) <= sh.UMBRAL_VERDE:
            estado = "✅ Coincide"
        else:
            estado = "🔴 Diferencia"
        filas_cont_local.append(
            {
                "_fecha_cierre": s["fecha_cierre"],
                "_id_cierre": s["id_cierre"],
                "Tipo": s["tipo_salto"],
                "Cierre": f"{s['fecha_cierre']} {s['turno_cierre']} {s['hora_cierre']} — {s['nombre_cierre']}",
                "Apertura": f"{s['fecha_apertura']} {s['turno_apertura']} {s['hora_apertura']} — {s['nombre_apertura']}",
                "Diferencia (S/)": f"{diferencia:+,.2f}",
                "Estado": estado,
            }
        )

    cont_local_df = pd.DataFrame(filas_cont_local)
    if cont_local_df.empty:
        st.caption("Todavía no hay saltos Cierre → Apertura para comparar.")
    else:
        cont_local_df = cont_local_df[cont_local_df["_fecha_cierre"] >= desde].sort_values(
            "_fecha_cierre", ascending=False
        )
        if cont_local_df.empty:
            st.caption("No hay comparaciones en el rango de fechas seleccionado.")
        else:
            con_diferencia_local = int((cont_local_df["Estado"] == "🔴 Diferencia").sum())
            if con_diferencia_local:
                st.warning(f"⚠️ {con_diferencia_local} caso(s) sin explicar todavía.")
            else:
                st.success("Todo coincide (o está autorizado). 👍")
            st.dataframe(
                sh.arrow_safe(cont_local_df.drop(columns=["_fecha_cierre", "_id_cierre"])),
                width="stretch",
                hide_index=True,
            )
            st.caption(
                "🔷 Autorizado = administración ya registró un ajuste que explica la "
                "diferencia. **Tipo** dice si el salto es dentro del mismo turno, entre "
                "Mañana y Tarde, o entre un día y el siguiente."
            )

with tab_acumulado:
    # -------------------------------------------------------------
    # Acumulado de diferencias por persona: quien abrio cada corte se
    # queda con su diferencia (igual que en el Dashboard). Solo del
    # local propio (df_local ya viene filtrado por el PIN), asi que un
    # cajero puede ver esto de todo su equipo, pero no de otras tiendas.
    #
    # Los turnos ya "🔷 Autorizado" NO se cuentan -- la diferencia ya
    # esta explicada por un ajuste de administracion, no es un
    # descuadre real que deba sumarse a nadie.
    # -------------------------------------------------------------
    st.subheader(f"Acumulado de diferencias por persona — {local}")
    st.caption(
        "En el rango de fechas filtrado. La diferencia de cada corte se le "
        "atribuye a quien abrió; la diferencia entre cortes se le atribuye "
        "a quien cerró (ver pestaña Continuidad). No incluye lo ya "
        "'🔷 Autorizado'. Ordenado por descuadre total (sin importar el signo)."
    )

    _turnos_autorizados_local = set(
        resumen.loc[resumen["estado"] == "🔷 Autorizado", ["fecha", "turno"]]
        .itertuples(index=False, name=None)
    )
    if _turnos_autorizados_local:
        _claves_cortes_local = list(zip(cortes["fecha"], cortes["turno"]))
        cortes_acumulado_local = cortes[
            [c not in _turnos_autorizados_local for c in _claves_cortes_local]
        ]
    else:
        cortes_acumulado_local = cortes

    # "Diferencia entre cortes": lo que pasa en el HUECO entre un Cierre y
    # la Apertura siguiente (pestaña Continuidad), atribuido a quien
    # cerró -- distinto de la diferencia DENTRO de un corte (arriba).
    _ids_autorizados_salto_local = (
        set(cont_local_df.loc[cont_local_df["Estado"] == "🔷 Autorizado", "_id_cierre"])
        if not cont_local_df.empty
        else set()
    )
    saltos_para_acumulado_local = (
        saltos_local[
            (saltos_local["fecha_cierre"] >= desde)
            & (~saltos_local["id_cierre"].isin(_ids_autorizados_salto_local))
        ]
        if not saltos_local.empty
        else saltos_local
    )
    acumulado_saltos_local = sh.acumulado_saltos_por_persona(saltos_para_acumulado_local)

    acumulado_local = pd.merge(
        sh.acumulado_por_persona(cortes_acumulado_local)[
            ["nombre", "n_cortes", "diferencia", "diferencia_fmt", "descuadre_abs"]
        ],
        acumulado_saltos_local[["nombre", "n_saltos", "diferencia_saltos", "diferencia_saltos_fmt"]],
        on="nombre",
        how="outer",
    )
    for _col, _default in [
        ("n_cortes", 0), ("diferencia", 0.0), ("descuadre_abs", 0.0),
        ("n_saltos", 0), ("diferencia_saltos", 0.0),
    ]:
        acumulado_local[_col] = acumulado_local[_col].fillna(_default)
    acumulado_local["n_cortes"] = acumulado_local["n_cortes"].astype(int)
    acumulado_local["n_saltos"] = acumulado_local["n_saltos"].astype(int)
    acumulado_local["diferencia_fmt"] = acumulado_local["diferencia"].apply(lambda x: f"{x:+,.2f}")
    acumulado_local["diferencia_saltos_fmt"] = acumulado_local["diferencia_saltos"].apply(
        lambda x: f"{x:+,.2f}"
    )
    acumulado_local = acumulado_local.sort_values("descuadre_abs", ascending=False)

    if acumulado_local.empty:
        st.caption("Todavía no hay cortes ni saltos completos en el rango seleccionado.")
    else:
        st.dataframe(
            sh.arrow_safe(
                acumulado_local.rename(
                    columns={
                        "nombre": "Persona",
                        "diferencia_fmt": "Diferencia neta (S/, sobra − falta, se cancelan)",
                        "descuadre_abs": "Descuadre total (S/, sin importar el signo)",
                        "diferencia_saltos_fmt": "Diferencia entre cortes (S/, atribuida a quien cerró)",
                    }
                ).drop(columns=["diferencia", "diferencia_saltos", "n_cortes", "n_saltos"])
            ),
            width="stretch",
            hide_index=True,
        )
        fig_acumulado_local = px.bar(
            acumulado_local.sort_values("diferencia"),
            x="nombre",
            y="diferencia",
            labels={"nombre": "Persona", "diferencia": "Diferencia neta acumulada (S/)"},
        )
        st.plotly_chart(fig_acumulado_local, width="stretch")

with tab_registros:
    # -------------------------------------------------------------
    # Detalle registro por registro, con fotos, mas facil de revisar en el
    # celular que una tabla gigante.
    # -------------------------------------------------------------
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

            filas_denom = []
            for etiqueta, col, valor_denom in sh.DENOMINACIONES:
                monto = pd.to_numeric(fila.get(col), errors="coerce")
                if pd.notna(monto) and monto != 0:
                    filas_denom.append({"Denominación": f"S/ {valor_denom:g}", "Monto (S/)": float(monto)})
            if filas_denom:
                st.caption("Detalle del efectivo por billetes/monedas:")
                st.dataframe(
                    sh.arrow_safe(pd.DataFrame(filas_denom)),
                    width="stretch",
                    hide_index=True,
                )

            if pd.notna(fila.get("num_operaciones")) and str(fila.get("num_operaciones")) != "":
                st.caption(f"N° de operaciones: {int(fila['num_operaciones'])}")
            if fila.get("observaciones"):
                st.caption(f"Observaciones: {fila['observaciones']}")

            fotos_presentes = [c for c in columnas_fotos if fila.get(c)]
            if fotos_presentes:
                st.write("**Fotos:**")
                for col in fotos_presentes:
                    etiqueta = col.replace("foto_voucher_", "").replace("_", " ").capitalize()
                    # Las fotos NO se muestran solas al abrir el registro -- cada
                    # una pesa varios MB en base64 y viaja entera por el
                    # websocket (no por un link aparte, ver mostrar_imagen). Con
                    # varias fotos mostrandose de una en el registro mas
                    # reciente (que se abre solo), en 4G/señal débil el celular
                    # puede cortar a medio mensaje y tirar un "Connection error".
                    # Pidiendo un click por foto, solo se manda lo que el
                    # cajero realmente quiere ver, de a una.
                    mostrar_key = f"mostrar_foto_{fila['id']}_{col}"
                    if not st.session_state.get(mostrar_key, False):
                        if st.button(f"📷 Ver «{etiqueta}»", key=f"btn_foto_{fila['id']}_{col}"):
                            st.session_state[mostrar_key] = True
                            st.rerun()
                        continue
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
                        sh.mostrar_imagen(
                            imagen_bytes,
                            ancho_px=None if ver_grande else ANCHO_FOTO,
                            caption=etiqueta,
                        )
                    else:
                        st.caption(f"{etiqueta}: no se pudo cargar (archivo incompleto o dañado).")
            else:
                st.caption("Este registro no tiene fotos.")
