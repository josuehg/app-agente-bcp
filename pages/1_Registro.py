"""
pages/1_Registro.py
--------------------
El formulario que usa el personal de tienda para registrar la APERTURA
o el CIERRE de caja del Agente BCP. Reemplaza al Google Form.

POR QUE NO USAMOS st.form AQUI: st.form agrupa varios campos y solo
recarga la pagina cuando se aprieta "Guardar" -- eso significaba que ni
el Total en vivo ni el cambio a "Cierre" se reflejaban mientras
escribias, porque Streamlit no se enteraba hasta el final. Al sacar
todo del form, CADA cambio recarga la pagina al instante y todo se ve
actualizado en tiempo real (el precio es que perdemos el "todo o nada"
de un formulario, pero para este caso no hace falta).

CONCEPTO NUEVO: como ya no hay clear_on_submit para vaciar los campos
solos, lo hacemos a mano con un truco comun en Streamlit -- un numero de
"version" en session_state que se le pega al nombre (key) de cada campo.
Cuando guardamos, subimos la version en 1: Streamlit ve que esos campos
ahora tienen un key distinto y los dibuja de cero (vacios), como si
fueran campos nuevos.

EL LOCAL YA NO SE ELIGE DE UNA LISTA: se identifica con el PIN del local
(ver sh.pedir_pin_de_local en sheets_utils.py). Asi un registro siempre
queda ligado a un local verificado por PIN, y no a lo que alguien haya
elegido (por error o a proposito) en un dropdown.
"""

import streamlit as st

import sheets_utils as sh

st.set_page_config(page_title="Registro - Agente BCP", page_icon="📝")
sh.aplicar_estilo()
st.title("📝 Registro de Apertura / Cierre")

if "mensaje_guardado" in st.session_state:
    st.toast(st.session_state.pop("mensaje_guardado"), icon="✅")

if "form_version" not in st.session_state:
    st.session_state["form_version"] = 0
v = st.session_state["form_version"]  # sufijo de key para los campos que se limpian

local = sh.pedir_pin_de_local(
    "Ingresa el PIN de tu local para registrar la Apertura o el Cierre."
)

col_local, col_salir = st.columns([4, 1])
col_local.success(f"Local: **{local}**")
if col_salir.button("Cambiar de PIN"):
    st.session_state["local_autenticado"] = None
    st.rerun()

col2, col3 = st.columns(2)
with col2:
    # Cada local trabaja 2 turnos al dia, y cada turno tiene su propia
    # Apertura y Cierre (o sea, hasta 4 registros por local por dia).
    turno = st.radio("Turno", ["Mañana", "Tarde"], horizontal=True)
with col3:
    tipo = st.radio("Tipo de registro", ["Apertura", "Cierre"], horizontal=True)

nombre = st.text_input("Nombre de quien registra", key=f"nombre_{v}")

tarjeta = st.number_input("Monto en Tarjeta (S/)", min_value=0.0, step=10.0, key=f"tarjeta_{v}")

# Desglose de efectivo: en vez de subir una foto del "sello" con el
# conteo de billetes y monedas, se cuenta aqui mismo. En cada casilla se
# escribe el MONTO en soles que hay de esa denominacion (no la cantidad
# de billetes/monedas) -- por ejemplo, si hay 10 billetes de S/100, se
# escribe 1000. El total en efectivo es solo la suma de las casillas.
st.subheader("💵 Desglose de efectivo (billetes y monedas)")
st.caption(
    "Escribe el **monto en soles** que hay de cada denominacion (no la "
    "cantidad de billetes/monedas). Ejemplo: si tienes 10 billetes de "
    "S/ 100, escribe **1000** -- no 10."
)
cantidades = {}
columnas_denom = st.columns(3)

# sh.DENOMINACIONES viene ordenado de mayor a menor (200 -> 0.10). Para el
# CELULAR, donde Streamlit apila cada columna completa una tras otra (no
# fila por fila como en la web), hay que repartir los campos en 3 GRUPOS
# de tamaño fijo (no alternados de 1 en 1) para que, de moneda mas chica a
# billete mas grande, salgan en orden al leerlos de arriba a abajo en el
# celular: 0.10, 0.20, 0.50, 1, 2, 5, 10, 20, 50, 100, 200.
orden_ascendente = list(reversed(sh.DENOMINACIONES))
cantidad_total = len(orden_ascendente)
por_columna = -(-cantidad_total // 3)  # redondeo hacia arriba sin usar math.ceil

for indice, (etiqueta, columna, valor) in enumerate(orden_ascendente):
    columna_destino = columnas_denom[indice // por_columna]
    with columna_destino:
        cantidades[columna] = st.number_input(
            f"{etiqueta} (S/)", min_value=0.0, step=float(valor), key=f"denom_{columna}_{v}"
        )

efectivo = sum(cantidades.values())
total = efectivo + tarjeta

# Ahora si se puede mostrar en vivo: como ya no estamos dentro de un
# st.form, cada numero que escribes recarga la pagina y este metric se
# recalcula al instante.
st.metric("Total (efectivo + tarjeta)", f"S/ {total:,.2f}")

num_operaciones = None
foto_voucher_saldo_inicial_tarjeta = None
foto_voucher_saldo_final_tarjeta = None
foto_voucher_nro_movimientos = None

if tipo == "Apertura":
    st.subheader("Fotos de Apertura (obligatorio)")
    foto_voucher_saldo_inicial_tarjeta = st.file_uploader(
        "Voucher de saldo INICIAL en Tarjeta", type=["jpg", "jpeg", "png"], key=f"foto_vi_{v}"
    )
else:
    num_operaciones = st.number_input(
        "Numero de Operaciones", min_value=0, step=1, key=f"num_op_{v}"
    )
    st.subheader("Fotos de Cierre (obligatorio)")
    foto_voucher_saldo_final_tarjeta = st.file_uploader(
        "Voucher de saldo FINAL en Tarjeta", type=["jpg", "jpeg", "png"], key=f"foto_vf_{v}"
    )
    foto_voucher_nro_movimientos = st.file_uploader(
        "Voucher donde se vea el N° de movimientos", type=["jpg", "jpeg", "png"], key=f"foto_vm_{v}"
    )

observaciones = st.text_area(
    "Observaciones", placeholder="Obligatorio: escribe algo, aunque sea 'Sin novedad'", key=f"obs_{v}"
)

if "confirmar_registro" not in st.session_state:
    st.session_state["confirmar_registro"] = False

enviado = st.button(
    "Guardar registro",
    use_container_width=True,
    type="primary",
    disabled=st.session_state["confirmar_registro"],
)

if enviado:
    # AHORA TODOS LOS CAMPOS SON OBLIGATORIOS: juntamos todos los que
    # falten en una sola lista de errores, para que la persona vea de
    # una sola vez todo lo que le falta llenar (en vez de corregir uno,
    # volver a apretar Guardar, y recien enterarse del siguiente).
    errores = []
    if not nombre.strip():
        errores.append("Falta el nombre de quien registra.")
    if not observaciones.strip():
        errores.append("Las observaciones son obligatorias (escribe algo, aunque sea 'Sin novedad').")
    for etiqueta, columna, valor in sh.DENOMINACIONES:
        monto = cantidades[columna]
        if monto > 0:
            cociente = monto / valor
            if abs(cociente - round(cociente)) > 1e-6:
                errores.append(
                    f"'{etiqueta}': S/ {monto:,.2f} no es un múltiplo exacto de "
                    f"S/ {valor:g} -- revisa si escribiste bien el monto."
                )
    if tipo == "Apertura":
        if foto_voucher_saldo_inicial_tarjeta is None:
            errores.append("Falta la foto del voucher de saldo INICIAL en Tarjeta.")
    else:
        if foto_voucher_saldo_final_tarjeta is None:
            errores.append("Falta la foto del voucher de saldo FINAL en Tarjeta.")
        if foto_voucher_nro_movimientos is None:
            errores.append("Falta la foto del voucher con el N° de movimientos.")

    if errores:
        for error_texto in errores:
            st.error(error_texto)
        st.stop()

    # Todo esta llenado correctamente: en vez de guardar de una, abrimos
    # una ventana de confirmacion con un resumen -- asi la persona revisa
    # que no se le paso ningun numero o foto antes de que quede grabado
    # de verdad en la hoja.
    st.session_state["confirmar_registro"] = True
    st.rerun()


@st.dialog("¿Confirmar registro?")
def _dialogo_confirmar_registro():
    st.write(f"Vas a guardar un registro de **{tipo}** del turno **{turno}** en **{local}**:")
    st.markdown(
        f"""
- **Nombre:** {nombre.strip()}
- **Efectivo contado:** S/ {efectivo:,.2f}
- **Tarjeta:** S/ {tarjeta:,.2f}
- **Total:** S/ {total:,.2f}
{f"- **N° de operaciones:** {num_operaciones}" if tipo == "Cierre" else ""}
- **Observaciones:** {observaciones.strip()}
"""
    )

    fotos = [
        ("Voucher saldo inicial", foto_voucher_saldo_inicial_tarjeta),
        ("Voucher saldo final", foto_voucher_saldo_final_tarjeta),
        ("Voucher N° movimientos", foto_voucher_nro_movimientos),
    ]
    fotos_adjuntas = [(etiqueta, archivo) for etiqueta, archivo in fotos if archivo is not None]
    if fotos_adjuntas:
        st.write("**Fotos adjuntas:**")
        columnas_fotos = st.columns(len(fotos_adjuntas))
        for columna, (etiqueta, archivo) in zip(columnas_fotos, fotos_adjuntas):
            columna.image(archivo, caption=etiqueta, width=120)

    st.divider()
    col_confirmar, col_cancelar = st.columns(2)
    confirmar = col_confirmar.button("✅ Sí, guardar", type="primary", use_container_width=True)
    cancelar = col_cancelar.button("✏️ Volver a editar", use_container_width=True)

    if cancelar:
        st.session_state["confirmar_registro"] = False
        st.rerun()

    if confirmar:
        # Si el internet/wifi de la tienda se corta justo mientras se sube
        # una foto, sheets_utils.py ya reintenta un par de veces solo. Pero
        # si aun asi falla (por ejemplo, se cayo del todo la wifi), en vez
        # de mostrar la pantalla roja de error de Streamlit (que asusta y
        # hace pensar que se perdio todo lo escrito), atrapamos el error
        # aqui: mostramos un aviso claro y NO tocamos los campos, para que
        # la persona solo tenga que volver a apretar "Sí, guardar" cuando
        # la conexion vuelva -- sin volver a contar el efectivo.
        try:
            with st.spinner("Guardando registro y subiendo fotos..."):
                ahora = sh.ahora_local()
                prefijo = f"{local}_{turno}_{tipo}_{ahora:%Y%m%d_%H%M%S}"

                def _extension(archivo):
                    nombre_original = archivo.name
                    return nombre_original[nombre_original.rfind(".") :] if "." in nombre_original else ""

                def _subir(archivo, sufijo):
                    if archivo is None:
                        return ""
                    return sh.subir_foto(archivo, f"{prefijo}_{sufijo}{_extension(archivo)}")

                datos = {
                    "id": sh.nuevo_id(),
                    # timestamp sin el sufijo de zona (-05:00), para que
                    # tenga el mismo formato que las filas ya guardadas.
                    "timestamp": ahora.replace(tzinfo=None).isoformat(timespec="seconds"),
                    "fecha": ahora.date().isoformat(),
                    "local": local,
                    "turno": turno,
                    "tipo": tipo,
                    "nombre": nombre.strip(),
                    **cantidades,
                    "efectivo": efectivo,
                    "tarjeta": tarjeta,
                    "total": total,
                    "num_operaciones": num_operaciones if num_operaciones is not None else "",
                    "observaciones": observaciones.strip(),
                    "foto_voucher_saldo_inicial_tarjeta": _subir(
                        foto_voucher_saldo_inicial_tarjeta, "voucher_inicial"
                    ),
                    "foto_voucher_saldo_final_tarjeta": _subir(
                        foto_voucher_saldo_final_tarjeta, "voucher_final"
                    ),
                    "foto_voucher_nro_movimientos": _subir(
                        foto_voucher_nro_movimientos, "voucher_movimientos"
                    ),
                }
                sh.guardar_registro(datos)
        except Exception as error:
            st.error(
                "No se pudo guardar: parece que se corto la conexion a "
                "internet mientras se subia una foto. Nada de lo que "
                "escribiste se perdio -- revisa tu wifi/datos y vuelve a "
                "apretar 'Sí, guardar'."
            )
            st.caption(f"Detalle tecnico: {error}")
            st.stop()

        # Subimos la "version" para que nombre, tarjeta, denominaciones,
        # num_operaciones, fotos y observaciones se dibujen de cero (vacios)
        # en la proxima recarga. Local/Turno/Tipo NO se limpian a proposito:
        # es comun registrar varios movimientos seguidos del mismo local.
        st.session_state["confirmar_registro"] = False
        st.session_state["form_version"] += 1
        st.session_state["mensaje_guardado"] = (
            f"{tipo} de turno {turno} de {local} guardada correctamente."
        )
        st.rerun()


if st.session_state["confirmar_registro"]:
    _dialogo_confirmar_registro()
