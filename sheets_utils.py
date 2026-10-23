"""
sheets_utils.py
----------------
Aqui vive TODA la logica para hablar con Google (Sheets + Drive).
La idea es que el resto de la app (los formularios, el dashboard) nunca
tengan que saber COMO se guarda un dato, solo llaman a estas funciones.

Esto se llama "separar responsabilidades": un archivo se encarga de una
sola cosa (hablar con Google) y los demas archivos solo lo usan.

CONCEPTOS NUEVOS QUE VAS A VER AQUI:
- Autenticacion con una "cuenta de servicio" (un usuario robot de Google,
  no tu cuenta personal).
- gspread: libreria para leer/escribir Google Sheets como si fueran tablas.
- Google Drive API: para subir fotos y obtener un link para verlas.
- st.cache_data / st.cache_resource: le dicen a Streamlit "no repitas este
  trabajo pesado cada vez que alguien hace clic, guardalo un rato".
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# Logica pura del cuadre por turno (no depende de streamlit ni Google).
# Se re-exporta para que el resto de la app la use como sh.calcular_cortes, etc.
from cuadre import (  # noqa: F401  (re-export para el resto de la app)
    a_descontar,
    con_estado_nota,
    ESTADO_ESPERA_NOTA,
    ESTADO_NO_SE_ABONA,
    ESTADO_PENDIENTE_PAGO,
    estado_nota,
    estado_parcial,
    nota_vigente,
    UMBRAL_NOTA_LOCAL,
    ESTADO_SALTO_COINCIDE,
    ESTADO_SALTO_DIFERENCIA,
    UMBRAL_VERDE,
    acumulado_por_persona,
    acumulado_saltos_por_persona,
    calcular_cortes,
    calcular_saltos,
    creditos_repuso,
    items_ticket,
    linea_de_tiempo,
    clave_alias,
    conflictos_alias,
    mapa_alias,
    operaciones_por_turno,
    sugerir_oficial,
    politica_sucursal,
    margen_error,
    resolver_nombre,
    resumen_turnos,
    totales_ticket,
    ESTADOS_SOLICITUD_ACTIVOS,
    solicitud_repetida,
    ESTADOS_CONFORMIDAD,
    ESTADO_SIN_CONFORMIDAD,
    COLUMNAS_CONFORMIDAD,
    con_conformidad,
    UMBRAL_ALERTA_DIFERENCIA,
    alertas_de_registro,
    texto_alerta_diferencia,
    minutos_entre,
    solicitudes_activas,
    texto_espera,
)

# Los "scopes" son los permisos que le pedimos a Google. Sin el permiso
# exacto, la llamada falla aunque las credenciales sean correctas.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

NOMBRE_HOJA_REGISTROS = "Registros"
NOMBRE_HOJA_CONFIG = "Config"

# ---------------------------------------------------------------------
# Fecha y hora LOCAL (Peru)
# ---------------------------------------------------------------------
# El servidor de Streamlit Community Cloud corre en horario UTC, no en
# hora de Peru. Si usamos datetime.now() / date.today() "pelados", un
# Cierre del turno Tarde hecho a las 8 pm (hora Peru) se guardaria con
# la fecha del dia SIGUIENTE (a esa hora en UTC ya son pasadas las
# 00:00). Resultado: el cuadre Apertura vs Cierre del Dashboard no
# emparejaria ese turno (veria "Falta Cierre" un dia y "Falta Apertura"
# al siguiente).
#
# Por eso TODA la app pide la fecha/hora a estas dos funciones, nunca a
# datetime.now() / date.today() directamente.
ZONA_LOCAL = ZoneInfo("America/Lima")


def ahora_local() -> datetime:
    """Fecha y hora actual en Peru (con tzinfo)."""
    return datetime.now(ZONA_LOCAL)


def hoy_local() -> date:
    """Fecha de hoy en Peru."""
    return datetime.now(ZONA_LOCAL).date()


def _texto_seguro(df: pd.DataFrame, columnas_no_texto: set[str]) -> pd.DataFrame:
    """Deja TODAS las columnas que no sean numero/fecha como texto puro.

    POR QUE: al leer la hoja, una columna como 'observaciones' puede
    terminar con tipos mezclados -- texto en unas filas y NaN (float) o un
    numero suelto en otras (p.ej. si alguien escribio solo "123", o si la
    columna es nueva y esta vacia para las filas antiguas). Cuando
    st.dataframe intenta convertir eso a una tabla Arrow revienta con
    "Expected bytes, got a 'float' object"; y en algunas versiones de
    pyarrow ese error llega a tumbar TODO el proceso ("malloc(): invalid
    size"), que es lo que tiraba la app abajo. Forzando str + "" evitamos
    por completo ese camino.
    """
    for col in df.columns:
        if col not in columnas_no_texto:
            df[col] = (
                df[col]
                .fillna("")
                .astype(str)
                .replace({"nan": "", "NaT": "", "None": ""})
            )
    return df


def _normalizar_nombre(serie: pd.Series) -> pd.Series:
    """Nombres consistentes: sin espacios de sobra y con la misma
    capitalizacion, para que 'MAFER', 'Mafer' y ' mafer ' cuenten como
    una sola persona en los rankings."""
    return (
        serie.fillna("")
        .astype(str)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.title()
    )


def arrow_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve una copia del DataFrame lista para st.dataframe / st.table.

    Toda columna que NO sea numero ni fecha/hora nativa se pasa a texto
    puro (None/NaT/NaN -> ""). Es la red de seguridad definitiva contra el
    "Segmentation fault" de pyarrow al convertir a Arrow una columna
    'object' con tipos mezclados (texto + fecha + NaT, floats + pd.NA,
    etc.). Llamar SIEMPRE justo antes de mostrar una tabla.
    """
    salida = df.copy()
    for col in salida.columns:
        serie = salida[col]
        if pd.api.types.is_numeric_dtype(serie) or pd.api.types.is_datetime64_any_dtype(serie):
            continue
        salida[col] = serie.map(
            lambda x: "" if x is None or (not isinstance(x, str) and pd.isna(x)) else str(x)
        )
    return salida


COLUMNAS_REGISTROS = [
    "id",
    "timestamp",
    "fecha",
    "local",
    "turno",  # "Mañana" o "Tarde"
    "tipo",  # "Apertura" o "Cierre"
    "nombre",
    # Desglose de efectivo contado (reemplaza la foto del "sello"): la
    # cantidad de cada billete/moneda. El campo "efectivo" de mas abajo
    # se calcula automaticamente sumando esto, no se escribe a mano.
    "billetes_200",
    "billetes_100",
    "billetes_50",
    "billetes_20",
    "billetes_10",
    "monedas_5",
    "monedas_2",
    "monedas_1",
    "monedas_050",
    "monedas_020",
    "monedas_010",
    "efectivo",
    "tarjeta",
    "total",
    "num_operaciones",
    "observaciones",
    "foto_voucher_saldo_inicial_tarjeta",
    "foto_voucher_saldo_final_tarjeta",
    "foto_voucher_nro_movimientos",
    # Solo se llena en un Cierre cuando lo registra una persona distinta a
    # la que abrio ese corte: el motivo que escribio para justificarlo.
    "motivo_cierre_otro_nombre",
]

# (etiqueta a mostrar, nombre de columna, valor en soles de esa denominacion)
DENOMINACIONES = [
    ("Monto en billetes de S/ 200", "billetes_200", 200),
    ("Monto en billetes de S/ 100", "billetes_100", 100),
    ("Monto en billetes de S/ 50", "billetes_50", 50),
    ("Monto en billetes de S/ 20", "billetes_20", 20),
    ("Monto en billetes de S/ 10", "billetes_10", 10),
    ("Monto en monedas de S/ 5", "monedas_5", 5),
    ("Monto en monedas de S/ 2", "monedas_2", 2),
    ("Monto en monedas de S/ 1", "monedas_1", 1),
    ("Monto en monedas de S/ 0.50", "monedas_050", 0.5),
    ("Monto en monedas de S/ 0.20", "monedas_020", 0.2),
    ("Monto en monedas de S/ 0.10", "monedas_010", 0.1),
]
# NOTA: cada casilla pide el MONTO (en soles) que hay de esa denominacion,
# no la cantidad de billetes/monedas -- p.ej. si hay 10 billetes de S/100,
# se escribe 1000. Antes se pedia la cantidad y la app multiplicaba por el
# valor; se cambio porque en la practica el personal ya cuenta y suma el
# monto por denominacion de cabeza, y les resultaba mas natural escribir
# eso directo. El "valor" de cada tupla ahora se usa solo para validar que
# el monto ingresado sea un multiplo exacto de esa denominacion (ver
# pages/1_Registro.py).


# ---------------------------------------------------------------------
# Estilo visual (tipografia de marca)
# ---------------------------------------------------------------------

def aplicar_estilo() -> None:
    """Carga la tipografia 'Comfortaa' desde Google Fonts y la aplica a
    toda la pagina (titulos, botones, menu lateral, texto, etc.) via CSS.

    CADA pagina de Streamlit se dibuja de cero al navegar (no es como una
    pagina web normal donde el <head> se comparte) -- por eso esta
    funcion se llama al principio de TODAS las paginas (Bienvenida.py y
    cada archivo en pages/), justo despues de st.set_page_config(). Si
    se llama en una sola pagina, esa se ve con la fuente nueva pero las
    demas se quedan con la fuente por defecto.

    ".stApp" es la clase del contenedor principal de Streamlit: aplicar
    la fuente ahi y a todos sus descendientes (`.stApp *`) es mas
    confiable que apuntar a clases internas de Streamlit, que cambian de
    nombre entre versiones.

    OJO CON LOS ICONOS: la flecha de colapsar el menu, el "ojito" de
    mostrar/ocultar el PIN, las flechitas de los desplegables, etc. NO
    son dibujos -- son texto (literalmente dice "visibility",
    "keyboard_double_arrow_left", etc.) que una fuente especial de
    simbolos (Material Symbols) convierte en un icono. Si a esos
    tambien les forzamos Comfortaa, se ve el nombre en texto plano en
    vez del dibujo. Por eso hay una segunda regla, mas especifica, que
    les devuelve su fuente de iconos original.
    """
    st.markdown(
        """
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Comfortaa:wght@300..700&display=swap" rel="stylesheet">
        <style>
        html, body, .stApp, .stApp * {
            font-family: 'Comfortaa', sans-serif !important;
        }
        .stApp [data-testid="stIconMaterial"],
        .stApp .material-symbols-outlined,
        .stApp .material-symbols-rounded {
            font-family: 'Material Symbols Outlined', 'Material Symbols Rounded' !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------
# Conexion / autenticacion
# ---------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _get_credentials() -> Credentials:
    """Arma las credenciales de la cuenta de servicio a partir de los
    "secrets" de Streamlit (ver .streamlit/secrets.toml.example).

    @st.cache_resource hace que esto se calcule UNA sola vez por sesion
    del servidor, no en cada clic del usuario.
    """
    info = dict(st.secrets["gcp_service_account"])
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def _con_reintento_429(request):
    """Google Sheets deja hacer ~60 lecturas por minuto. Si se pasa (429), en
    vez de romper la pagina esperamos unos segundos y reintentamos (3, 6 y 12 s)."""

    def _envuelto(*args, **kwargs):
        for intento in range(4):
            try:
                return request(*args, **kwargs)
            except gspread.exceptions.APIError as error:
                codigo = getattr(error, "code", None) or getattr(
                    getattr(error, "response", None), "status_code", None
                )
                if codigo != 429 or intento == 3:
                    raise
                time.sleep(3 * 2**intento)

    return _envuelto


@st.cache_resource(show_spinner=False)
def _get_gspread_client() -> gspread.Client:
    cliente = gspread.authorize(_get_credentials())
    cliente.http_client.request = _con_reintento_429(cliente.http_client.request)
    return cliente


@st.cache_resource(show_spinner=False)
def _get_drive_service():
    return build("drive", "v3", credentials=_get_credentials())


@st.cache_resource(show_spinner=False)
def _get_spreadsheet():
    # Cacheado como recurso: abrir el spreadsheet por su ID es una llamada
    # a la API de Google. Sin cache, se repetia cada vez que se refrescaba
    # un dato (cada cache-miss de las funciones de lectura de abajo), lo
    # que acerca a la app al limite de 60 llamadas/minuto de Google cuando
    # varias tiendas la usan a la vez.
    client = _get_gspread_client()
    return client.open_by_key(st.secrets["spreadsheet_id"])


# Hojas ya verificadas en este proceso. Abrir una hoja cuesta 2 lecturas a la
# API (sus datos de la pestaña y su encabezado); como se llamaba en CADA
# lectura de datos, la app triplicaba su consumo y chocaba con el limite de
# lecturas por minuto de Google. Con esto se verifica una sola vez.
_HOJAS_VERIFICADAS: dict = {}


def _get_or_create_worksheet(nombre: str, columnas: list[str]):
    """Devuelve la hoja (tab) con ese nombre dentro del spreadsheet.

    Si no existe todavia, la crea con el encabezado correcto. Si ya
    existe pero su fila 1 (encabezado) no coincide con las columnas que
    la app usa ahora mismo -- por ejemplo, porque se agrego un campo
    nuevo como "turno" -- la corrige automaticamente. Asi no hace falta
    borrar la pestana a mano cada vez que el codigo cambia; los datos
    ya guardados en las filas de abajo no se tocan.
    """
    clave_hoja = (nombre, tuple(columnas))
    if clave_hoja in _HOJAS_VERIFICADAS:
        return _HOJAS_VERIFICADAS[clave_hoja]
    sh = _get_spreadsheet()
    try:
        ws = sh.worksheet(nombre)
        encabezado_actual = ws.row_values(1)
        if encabezado_actual != columnas:
            columnas_faltantes = len(columnas) - ws.col_count
            if columnas_faltantes > 0:
                ws.add_cols(columnas_faltantes)
            ws.update(values=[columnas], range_name="A1")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=nombre, rows=1000, cols=len(columnas))
        ws.append_row(columnas)
    _HOJAS_VERIFICADAS[clave_hoja] = ws
    return ws


# ---------------------------------------------------------------------
# Config (por local: fondo minimo, PIN, tipo de agente y tarifa de comision)
# ---------------------------------------------------------------------

# meta_ops_manana / meta_ops_tarde: minimo de operaciones que se exige por turno
# en ese local (0 o vacio = sin meta). Se editan desde el Dashboard.
COLUMNAS_CONFIG = [
    "local", "fondo_minimo", "pin", "tipo_agente", "soles_por_operacion",
    "meta_ops_manana", "meta_ops_tarde",
]

# Tarifa promedio de comision por operacion, por tipo de agente. Es solo
# el valor por defecto: si en la hoja 'Config' se llena
# 'soles_por_operacion' para un local, manda ese.
TARIFA_DEFAULT = {"superagente": 0.30, "normal": 0.225}

# Palabras en el nombre del local que indican "superagente" (para cuando
# la columna 'tipo_agente' de la hoja esta vacia). El usuario puede
# sobreescribir esto llenando la columna a mano.
_PISTAS_SUPERAGENTE = ("zola", "zolb", "fau", "fer213")


def _tipo_agente_por_nombre(nombre_local: str) -> str:
    n = str(nombre_local).lower()
    return "superagente" if any(p in n for p in _PISTAS_SUPERAGENTE) else "normal"


@st.cache_data(ttl=60, show_spinner=False)
def get_config_df() -> pd.DataFrame:
    """Lee la hoja 'Config'. Columnas:
    - local, fondo_minimo, pin (como siempre).
    - tipo_agente: "superagente" o "normal". Si esta vacio, se adivina por
      el nombre del local (ver _PISTAS_SUPERAGENTE).
    - soles_por_operacion: tarifa promedio de comision por operacion de
      ese local. Si esta vacio, se usa TARIFA_DEFAULT segun el tipo.

    Se cachea 60 s: los cambios en la hoja tardan como maximo un minuto.
    """
    ws = _get_or_create_worksheet(NOMBRE_HOJA_CONFIG, COLUMNAS_CONFIG)
    registros = ws.get_all_records()
    if not registros:
        locales_default = [f"Local {i+1}" for i in range(6)]
        for i, local in enumerate(locales_default):
            ws.append_row([local, 5000, f"{1001 + i}", "normal", 0.225])
        registros = ws.get_all_records()
    df = pd.DataFrame(registros)
    for col in COLUMNAS_CONFIG:
        if col not in df.columns:
            df[col] = ""

    df["fondo_minimo"] = pd.to_numeric(df["fondo_minimo"], errors="coerce").fillna(0)
    for col in ("meta_ops_manana", "meta_ops_tarde"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df = _texto_seguro(df, {"fondo_minimo", "soles_por_operacion", "meta_ops_manana", "meta_ops_tarde"})
    df["pin"] = df["pin"].str.strip()
    df["local"] = df["local"].str.strip()

    # tipo_agente: usar lo de la hoja si es valido; si no, adivinar.
    df["tipo_agente"] = df["tipo_agente"].str.strip().str.lower()
    df["tipo_agente"] = df["tipo_agente"].where(
        df["tipo_agente"].isin(["superagente", "normal"]),
        df["local"].map(_tipo_agente_por_nombre),
    )

    # soles_por_operacion: usar lo de la hoja si es > 0; si no, el default.
    tarifa_default = df["tipo_agente"].map(TARIFA_DEFAULT).fillna(TARIFA_DEFAULT["normal"])
    tarifa_hoja = pd.to_numeric(df["soles_por_operacion"], errors="coerce")
    df["soles_por_operacion"] = tarifa_hoja.where(tarifa_hoja > 0, tarifa_default)
    return df


def get_locales() -> list[str]:
    return get_config_df()["local"].tolist()


def get_meta_operaciones(local: str, turno: str) -> int:
    """Minimo de operaciones exigido para ese local y turno (0 = sin meta)."""
    df = get_config_df()
    fila = df[df["local"] == local]
    columna = "meta_ops_manana" if turno == "Mañana" else "meta_ops_tarde"
    if fila.empty:
        return 0
    return int(fila.iloc[0][columna])


def resumen_meta_turno(local: str, turno: str, df: pd.DataFrame | None = None) -> dict:
    """Para mostrar la meta en Registro: la meta del turno, las operaciones que
    ya se registraron HOY en ese turno (Cierres parciales anteriores) y el
    promedio de los ultimos 7 turnos cerrados de ese local y turno."""
    meta = get_meta_operaciones(local, turno)
    if df is None:
        df = get_registros_df()
    if df.empty:
        return {"meta": meta, "previas": 0, "promedio": None, "n": 0}
    hoy = hoy_local()
    propio = df[(df["local"] == local) & (df["turno"] == turno)]
    cierres_hoy = propio[(propio["fecha"] == hoy) & (propio["tipo"] == "Cierre")]
    previas = int(pd.to_numeric(cierres_hoy["num_operaciones"], errors="coerce").fillna(0).sum())
    ops = operaciones_por_turno(propio)
    ops = ops[ops["fecha"] < hoy].sort_values("fecha").tail(7)
    return {
        "meta": meta,
        "previas": previas,
        "promedio": float(ops["operaciones"].mean()) if not ops.empty else None,
        "n": len(ops),
    }


def guardar_metas(metas: dict) -> None:
    """Guarda las metas {(local, turno): minimo} en la hoja Config (una escritura
    confirmada). Las filas se buscan por nombre de local."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_CONFIG, COLUMNAS_CONFIG)
    locales = [str(v).strip() for v in ws.col_values(COLUMNAS_CONFIG.index("local") + 1)]
    cambios = []
    for (local, turno), minimo in metas.items():
        if local not in locales:
            raise ValueError(f"No se encontró el local {local} en la hoja Config.")
        fila = locales.index(local) + 1
        columna = "meta_ops_manana" if turno == "Mañana" else "meta_ops_tarde"
        a1 = gspread.utils.rowcol_to_a1(fila, COLUMNAS_CONFIG.index(columna) + 1)
        cambios.append({"range": a1, "values": [[int(minimo)]]})
    if cambios:
        _escribir_y_confirmar(ws, cambios)
        get_config_df.clear()


def guardar_tarifas(tarifas: dict) -> None:
    """Guarda {local: tarifa por operacion} en la columna soles_por_operacion de
    Config (una escritura confirmada)."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_CONFIG, COLUMNAS_CONFIG)
    locales = [str(v).strip() for v in ws.col_values(COLUMNAS_CONFIG.index("local") + 1)]
    cambios = []
    for local, tarifa in tarifas.items():
        if local not in locales:
            raise ValueError(f"No se encontró el local {local} en la hoja Config.")
        a1 = gspread.utils.rowcol_to_a1(
            locales.index(local) + 1, COLUMNAS_CONFIG.index("soles_por_operacion") + 1
        )
        cambios.append({"range": a1, "values": [[round(float(tarifa), 4)]]})
    if cambios:
        _escribir_y_confirmar(ws, cambios)
        get_config_df.clear()


def get_fondo_minimo(local: str) -> float:
    df = get_config_df()
    fila = df[df["local"] == local]
    if fila.empty:
        return 0.0
    return float(fila.iloc[0]["fondo_minimo"])


def get_local_por_pin(pin: str) -> str | None:
    """Busca en 'Config' que local tiene este PIN.

    Devuelve el nombre del local si el PIN coincide con alguno, o None
    si no coincide con ninguno (PIN incorrecto).
    """
    pin = (pin or "").strip()
    if not pin:
        return None
    df = get_config_df()
    encontrados = df[(df["pin"] != "") & (df["pin"] == pin)]
    if encontrados.empty:
        return None
    return str(encontrados.iloc[0]["local"])


def pedir_pin_de_local(texto_ayuda: str = "") -> str:
    """Pide el PIN del local y devuelve el nombre de ese local ya
    autenticado. La usan TANTO pages/1_Registro.py COMO
    pages/3_Historial.py, para que el PIN se pida una sola vez por
    sesion y no dos.

    CONCEPTO CLAVE: st.session_state se comparte entre TODAS las
    paginas de una misma sesion de navegador (no es exclusivo de una
    pagina). Por eso, si el cajero ya puso su PIN en Registro, al entrar
    a Historial esta funcion ve que "local_autenticado" ya esta guardado
    y no le vuelve a preguntar nada.

    Si el PIN todavia no se puso (o esta mal), esta funcion dibuja el
    campo de texto y CORTA la ejecucion del resto de la pagina con
    st.stop() -- por eso la pagina que llama a esto puede simplemente
    hacer `local = sh.pedir_pin_de_local(...)` y seguir de largo como si
    el local ya estuviera garantizado.
    """
    if "local_autenticado" not in st.session_state:
        st.session_state["local_autenticado"] = None

    if not st.session_state["local_autenticado"]:
        if texto_ayuda:
            st.caption(texto_ayuda)
        pin_ingresado = st.text_input("PIN de tu local", type="password")
        if pin_ingresado:
            local_encontrado = get_local_por_pin(pin_ingresado)
            if local_encontrado:
                st.session_state["local_autenticado"] = local_encontrado
                st.rerun()
            else:
                st.error("PIN incorrecto.")
        st.caption(
            "El PIN de cada local se configura en la hoja 'Config' del "
            "Google Sheet (columna 'pin'), sin tocar codigo."
        )
        st.stop()

    return st.session_state["local_autenticado"]


# ---------------------------------------------------------------------
# Fotos (Google Drive)
# ---------------------------------------------------------------------

def subir_foto(archivo, nombre_archivo: str) -> str:
    """Sube un archivo (lo que devuelve st.file_uploader) a la carpeta de
    Drive configurada y devuelve un link para verlo.

    `archivo` es un UploadedFile de Streamlit: se comporta como un
    archivo normal (tiene .getvalue() y .type).

    IMPORTANTE: las cuentas de servicio no tienen almacenamiento propio
    en "Mi unidad" (Google lo quito hace tiempo). Por eso drive_folder_id
    DEBE apuntar a una "Unidad compartida" (Shared Drive) de tu
    organizacion, no a una carpeta normal -- ahi el espacio pertenece a
    la organizacion, no a una persona. `supportsAllDrives=True` es el
    parametro que le dice a la API "esto puede ser una Unidad compartida".

    SOBRE `num_retries=3`: cuando el wifi de una tienda tiene un corte
    breve justo mientras se sube la foto, googleapiclient puede terminar
    lanzando errores de conexion (por ejemplo
    "ConnectionAbortedError: [Errno 10053]"). Ese parametro le dice a la
    libreria "si falla por un problema de red, reintenta un par de veces
    solo antes de rendirte" -- resuelve solo, sin que el usuario tenga
    que hacer nada, la mayoria de esos cortes momentaneos.
    """
    contenido = archivo.getvalue()
    if not _imagen_completa(contenido):
        # La foto llego cortada (se corto la conexion mientras se subia).
        # No la mandamos a Drive: un archivo truncado despues no se ve.
        raise ValueError(
            "La foto se subió incompleta (se cortó la conexión). Vuelve a "
            "adjuntarla y guarda de nuevo."
        )
    service = _get_drive_service()
    media = MediaIoBaseUpload(
        io.BytesIO(contenido), mimetype=archivo.type, resumable=False
    )
    carpeta = st.secrets.get("drive_folder_id")
    metadata = {"name": nombre_archivo}
    if carpeta:
        metadata["parents"] = [carpeta]

    file = (
        service.files()
        .create(body=metadata, media_body=media, fields="id", supportsAllDrives=True)
        .execute(num_retries=3)
    )
    file_id = file["id"]

    # Intentamos hacer el archivo visible para "cualquiera con el link".
    # Si el Workspace de la organizacion tiene bloqueado ese tipo de
    # compartido, esta llamada falla -- y NO pasa nada: la app igual
    # muestra las fotos porque las descarga con la cuenta de servicio
    # (ver descargar_imagen_drive). Por eso lo envolvemos en try/except:
    # que no se caiga el guardado del registro solo por esto.
    try:
        service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
            supportsAllDrives=True,
        ).execute(num_retries=3)
    except Exception:
        pass

    return f"https://drive.google.com/file/d/{file_id}/view"


def _tipo_imagen(data: bytes | None) -> str | None:
    """Devuelve 'jpeg' / 'png' segun la firma del archivo, o None si no
    parece una imagen completa. NO usa Pillow."""
    if not data or len(data) < 100:
        return None
    if data[:3] == b"\xff\xd8\xff" and b"\xff\xd9" in data[-16:]:
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n" and b"IEND" in data[-16:]:
        return "png"
    return None


def _imagen_completa(data: bytes | None) -> bool:
    """True si `data` parece un JPEG o PNG completo (firma + marcador de
    fin), sin decodificar. Rechaza archivos truncados (subida cortada)."""
    return _tipo_imagen(data) is not None


def mostrar_imagen(data, ancho_px: int | None = None, caption: str | None = None) -> bool:
    """Muestra una foto SIN pasar por Pillow.

    POR QUE: `st.image()` siempre decodifica con Pillow, y en este stack
    (Streamlit Cloud) una foto de voucher corrupta hace que Pillow tumbe
    el proceso entero (Segmentation fault) -- ha pasado varias veces.
    Aca mandamos la imagen como data-URI y la decodifica EL NAVEGADOR: si
    esta dañada se ve rota en pantalla, pero el servidor ni se entera.

    `data` puede ser bytes o un UploadedFile de st.file_uploader.
    """
    if data is None:
        return False
    if hasattr(data, "getvalue"):  # UploadedFile
        data = data.getvalue()

    tipo = _tipo_imagen(data)
    if tipo is None:
        st.caption("⚠️ La foto no se pudo cargar (archivo incompleto o dañado).")
        return False

    import base64

    b64 = base64.b64encode(data).decode("ascii")
    estilo = "max-width:100%;height:auto;border-radius:6px"
    if ancho_px:
        estilo = f"width:{int(ancho_px)}px;max-width:100%;height:auto;border-radius:6px"
    html = f'<img src="data:image/{tipo};base64,{b64}" style="{estilo}">'
    if caption:
        html += f'<div style="font-size:0.8em;color:#667;margin-top:2px">{caption}</div>'
    st.markdown(html, unsafe_allow_html=True)
    return True


# ttl + max_entries acotan cuanta RAM se acumula con las fotos (cada una
# son varios MB). Streamlit Cloud tiene ~1 GB; sin tope, una sesion larga
# revisando muchos vouchers podia acercarse al limite.
@st.cache_data(ttl=600, max_entries=40, show_spinner=False)
def descargar_imagen_drive(link_ver: str) -> bytes | None:
    """Descarga los BYTES de una foto guardada en Drive, usando la misma
    cuenta de servicio que la subio, para poder mostrarla con st.image().

    ANTES intentabamos armar un link "para incrustar" tipo
    drive.google.com/uc?export=view&id=... y pasarselo directo a
    st.image(). Eso dejo de funcionar de forma confiable: Google viene
    restringiendo cada vez mas ese tipo de link "hotlink" por temas de
    abuso, y el resultado es el icono de imagen rota. Descargando el
    archivo nosotros mismos con la API (con permiso de sobra, porque la
    cuenta de servicio es quien lo subio) evitamos depender de como
    Google decida tratar ese link publico en cada momento.

    Se cachea 1 hora por link para no volver a descargar la misma foto
    en cada clic dentro de la misma sesion.
    """
    if not link_ver or "/d/" not in link_ver:
        return None
    file_id = link_ver.split("/d/", 1)[1].split("/", 1)[0]
    try:
        service = _get_drive_service()
        data = (
            service.files()
            .get_media(fileId=file_id, supportsAllDrives=True)
            .execute(num_retries=2)
        )
    except Exception:
        return None
    # Rechazamos archivos truncados/corruptos (firma o marcador de fin
    # ausente). El decode real lo hace el navegador (ver mostrar_imagen).
    return data if _imagen_completa(data) else None


# ---------------------------------------------------------------------
# Registros (aperturas y cierres)
# ---------------------------------------------------------------------

def nuevo_id() -> str:
    return uuid.uuid4().hex[:10]


class SecuenciaInvalida(Exception):
    """El registro rompe la secuencia Apertura -> Cierre del turno.

    Se lanza al guardar cuando:
    - se intenta una Apertura y el turno ya tiene una Apertura sin su
      Cierre (habria dos Aperturas seguidas), o
    - se intenta un Cierre y el turno no tiene ninguna Apertura abierta
      (habria un Cierre sin Apertura o dos Cierres seguidos).

    La pagina de Registro la atrapa y muestra el mensaje tal cual.
    """


def _leer_registros_crudos() -> list[dict]:
    """Lee la hoja 'Registros' SIN cache (todo como texto). Una sola lectura."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_REGISTROS, COLUMNAS_REGISTROS)
    return ws.get_all_records(numericise_ignore=["all"])


def _del_turno(filas: list[dict], local: str, fecha: str, turno: str) -> list[dict]:
    """Filas de ese turno (mismo local + misma fecha + mismo Mañana/Tarde), por hora."""
    del_turno = [
        f
        for f in filas
        if str(f.get("local", "")) == str(local)
        and str(f.get("fecha", "")) == str(fecha)
        and str(f.get("turno", "")) == str(turno)
    ]
    del_turno.sort(key=lambda f: str(f.get("timestamp", "")))
    return del_turno


def _registros_crudos_del_turno(local: str, fecha: str, turno: str) -> list[dict]:
    """Lee la hoja 'Registros' SIN cache y devuelve las filas de ese turno."""
    return _del_turno(_leer_registros_crudos(), local, fecha, turno)


def estado_turno(local: str, fecha: str, turno: str, filas_turno: list[dict] | None = None) -> dict:
    """Como esta el turno AHORA MISMO (lectura fresca, sin cache):

    - abierto: True si el ultimo registro del turno es una Apertura que
      todavia no tiene su Cierre.
    - apertura_abierta: {"nombre", "hora"} de esa Apertura, o None.
    - ultimo_tipo: "Apertura" | "Cierre" | None (si el turno no tiene nada).
    """
    filas = _registros_crudos_del_turno(local, fecha, turno) if filas_turno is None else filas_turno
    if not filas:
        return {"abierto": False, "apertura_abierta": None, "ultimo_tipo": None}
    ultimo = filas[-1]
    if str(ultimo.get("tipo", "")).strip() == "Apertura":
        ts = str(ultimo.get("timestamp", ""))
        hora = ts[11:16] if len(ts) >= 16 else ""
        return {
            "abierto": True,
            "apertura_abierta": {
                "nombre": nombre_oficial(str(ultimo.get("nombre", "")).strip(), local),
                "hora": hora,
            },
            "ultimo_tipo": "Apertura",
        }
    return {"abierto": False, "apertura_abierta": None, "ultimo_tipo": "Cierre"}


def estado_turno_df(df: pd.DataFrame, local: str, fecha: str, turno: str) -> dict:
    """Igual que estado_turno, pero calculado sobre un DataFrame que YA se leyo
    (get_registros_df): no gasta lecturas de Google Sheets. Sirve para las
    pistas del formulario; el guardado igual revalida con lectura fresca."""
    vacio = {"abierto": False, "apertura_abierta": None, "ultimo_tipo": None}
    if df is None or df.empty:
        return vacio
    sub = df[
        (df["local"] == local) & (df["fecha"] == date.fromisoformat(str(fecha))) & (df["turno"] == turno)
    ].sort_values("timestamp")
    if sub.empty:
        return vacio
    ultimo = sub.iloc[-1]
    if str(ultimo["tipo"]).strip() == "Apertura":
        ts = pd.to_datetime(ultimo["timestamp"], errors="coerce")
        return {
            "abierto": True,
            "apertura_abierta": {
                "nombre": str(ultimo["nombre"]).strip(),
                "hora": "" if pd.isna(ts) else ts.strftime("%H:%M"),
            },
            "ultimo_tipo": "Apertura",
        }
    return {"abierto": False, "apertura_abierta": None, "ultimo_tipo": "Cierre"}


@st.cache_data(ttl=15, show_spinner=False)
def estado_turno_cache(local: str, fecha: str, turno: str) -> dict:
    """Igual que estado_turno pero cacheado 15 s. Se usa solo para las
    PISTAS que ve la persona mientras llena el formulario (para no llamar
    a la API en cada tecla). La validacion de verdad, al guardar, usa
    estado_turno (fresco)."""
    return estado_turno(local, fecha, turno)


def guardar_registro(datos: dict) -> None:
    """Agrega una fila nueva a la hoja 'Registros'.

    `datos` debe traer las llaves de COLUMNAS_REGISTROS que apliquen;
    las que falten se guardan vacias, para no romper si un campo no
    aplica (ej: num_operaciones no existe en una Apertura).

    Antes de escribir valida la secuencia del turno (ver SecuenciaInvalida):
    no se permite una Apertura sobre un turno ya abierto, ni un Cierre sin
    Apertura. Esta comprobacion se hace con lectura FRESCA para que dos
    personas registrando casi a la vez no puedan crear un duplicado.
    """
    tipo = str(datos.get("tipo", "")).strip()
    todas = _leer_registros_crudos()  # UNA lectura fresca: sirve para validar y para la alerta
    estado = estado_turno(
        datos["local"], datos["fecha"], datos["turno"],
        _del_turno(todas, datos["local"], datos["fecha"], datos["turno"]),
    )

    if tipo == "Apertura" and estado["abierto"]:
        ab = estado["apertura_abierta"]
        raise SecuenciaInvalida(
            f"Este turno ya tiene una Apertura sin cerrar (la hizo "
            f"{ab['nombre'] or 'alguien'} a las {ab['hora'] or '--:--'}). "
            f"Primero hay que registrar el Cierre de ese corte."
        )
    if tipo == "Cierre" and not estado["abierto"]:
        raise SecuenciaInvalida(
            "Este turno no tiene una Apertura abierta para cerrar. "
            "Primero hay que registrar la Apertura."
        )

    ws = _get_or_create_worksheet(NOMBRE_HOJA_REGISTROS, COLUMNAS_REGISTROS)
    fila = [datos.get(col, "") for col in COLUMNAS_REGISTROS]
    ws.append_row(fila)
    # Como acabamos de escribir, invalidamos los caches de lectura para que
    # el dashboard y las pistas del formulario reflejen esto sin esperar el
    # TTL completo.
    get_registros_df.clear()
    estado_turno_cache.clear()
    try:
        _avisar_diferencia(todas, dict(zip(COLUMNAS_REGISTROS, fila)))
    except Exception:
        pass  # el aviso es un extra: nunca debe afectar al guardado


# ---------------------------------------------------------------------
# Personal: una fila por trabajador (nombre oficial, local habitual, alias
# con los que firmo antes separados por "|", y si sigue activo). Sirve para
# unir variantes del mismo nombre y desambiguar "Ana" segun el local.
# ---------------------------------------------------------------------
NOMBRE_HOJA_PERSONAL = "Personal"
COLUMNAS_PERSONAL = ["nombre", "local", "alias", "activo"]


def guardar_persona(datos: dict) -> None:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_PERSONAL, COLUMNAS_PERSONAL)
    ws.append_row([datos.get(col, "") for col in COLUMNAS_PERSONAL])
    get_personal_df.clear()
    get_registros_df.clear()
    get_ajustes_df.clear()
    get_encuestas_df.clear()


def eliminar_persona(nombre_orig: str, local_orig: str) -> None:
    """Quita de la hoja Personal la fila que coincide con (nombre, local). No
    toca los registros de caja: solo deja de existir esa persona como tal."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_PERSONAL, COLUMNAS_PERSONAL)
    for numero, fila in enumerate(ws.get_all_values()[1:], start=2):
        fila = fila + ["", ""]
        if fila[0].strip() == nombre_orig and fila[1].strip() == local_orig:
            ws.delete_rows(numero)
            get_personal_df.clear()
            get_registros_df.clear()
            get_ajustes_df.clear()
            get_encuestas_df.clear()
            return
    raise ValueError("No se encontró a esa persona en la hoja Personal.")


def actualizar_persona(nombre_orig: str, local_orig: str, datos: dict) -> None:
    """Cambia la fila de Personal que coincide con (nombre, local) originales."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_PERSONAL, COLUMNAS_PERSONAL)
    filas = ws.get_all_values()
    for numero, fila in enumerate(filas[1:], start=2):
        if (fila + ["", ""])[0].strip() == nombre_orig and (fila + ["", ""])[1].strip() == local_orig:
            ws.update(
                values=[[datos.get(col, "") for col in COLUMNAS_PERSONAL]],
                range_name=f"A{numero}:D{numero}",
            )
            get_personal_df.clear()
            get_registros_df.clear()
            get_ajustes_df.clear()
            get_encuestas_df.clear()
            return
    raise ValueError("No se encontró a esa persona en la hoja Personal.")


@st.cache_data(ttl=30, show_spinner=False)
def get_personal_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_PERSONAL, COLUMNAS_PERSONAL)
    df = pd.DataFrame(ws.get_all_records(), columns=COLUMNAS_PERSONAL)
    for col in COLUMNAS_PERSONAL:
        df[col] = df[col].fillna("").astype(str).str.strip()
    return df


def nombre_oficial(nombre, local) -> str:
    """Un nombre suelto (como lo escribieron) -> el nombre oficial segun la
    hoja Personal (o solo normalizado si no esta cargado)."""
    limpio = " ".join(str(nombre).split()).title()
    if not limpio:
        return limpio
    return resolver_nombre(limpio, local, mapa_alias(get_personal_df()))


_INACTIVO = {"no", "false", "0"}


def personal_activo(local: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Personal en actividad: (los de ESTE local, [(nombre, local habitual)]
    de los demas locales, por si rotan)."""
    personal = get_personal_df()
    activos = personal[
        (personal["nombre"] != "") & ~personal["activo"].str.lower().isin(_INACTIVO)
    ]
    propios = sorted(activos.loc[activos["local"] == local, "nombre"].unique())
    otros = sorted(
        {(r["nombre"], r["local"]) for _, r in activos[activos["local"] != local].iterrows()
         if r["nombre"] not in propios}
    )
    return propios, otros


def unificar_nombres(nombres: pd.Series, locales: pd.Series) -> pd.Series:
    """Aplica los alias de la hoja Personal (si no hay, deja todo igual)."""
    mapa = mapa_alias(get_personal_df())
    if not mapa:
        return nombres
    unidos = [resolver_nombre(n, loc, mapa) for n, loc in zip(nombres, locales)]
    return _normalizar_nombre(pd.Series(unidos, index=nombres.index))


def _tipar_registros(df: pd.DataFrame) -> pd.DataFrame:
    """Fechas, numeros y totales recalculados: igual que se ven en el Dashboard."""
    columnas_denom = [col for _, col, _ in DENOMINACIONES]
    numericas = {"efectivo", "tarjeta", "total", "num_operaciones", *columnas_denom}
    df = _texto_seguro(df, numericas | {"fecha", "timestamp"})
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for col in numericas:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # RECALCULAR efectivo y total, no confiar en lo guardado. Asi, si en la
    # hoja alguien corrige una denominacion o el monto de tarjeta (p. ej.
    # una Apertura vieja donde no se puso la tarjeta), el cuadre se ajusta
    # solo. `efectivo` = suma de las denominaciones; si un registro viejo
    # no tiene desglose, se respeta el efectivo que ya tenia guardado.
    suma_denominaciones = df[columnas_denom].sum(axis=1, numeric_only=True)
    df["efectivo"] = suma_denominaciones.where(
        suma_denominaciones > 0, df["efectivo"]
    ).fillna(0)
    df["total"] = df["efectivo"] + df["tarjeta"].fillna(0)
    return df


@st.cache_data(ttl=30, show_spinner=False)
def get_registros_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_REGISTROS, COLUMNAS_REGISTROS)
    # numericise_ignore: todo llega como TEXTO. Si no, un id como "1234e56789" se
    # lee como numero (inf) y varios ids distintos terminan siendo "el mismo";
    # los montos se convierten a numero mas abajo con pd.to_numeric.
    registros = ws.get_all_records(numericise_ignore=["all"])
    df = pd.DataFrame(registros, columns=COLUMNAS_REGISTROS)
    if df.empty:
        return df
    df = _tipar_registros(df)

    df["nombre"] = _normalizar_nombre(df["nombre"])
    df["nombre_original"] = df["nombre"]
    df["nombre"] = unificar_nombres(df["nombre"], df["local"])
    return df


# ---------------------------------------------------------------------
# Cuadre por turno (cortes Apertura -> Cierre): la logica vive en
# cuadre.py (sin depender de streamlit ni de Google, para poder probarla
# con pytest). Se importa arriba y el resto de la app la usa como
# sh.calcular_cortes(...) / sh.resumen_turnos(...) / sh.acumulado_por_persona(...).
# ---------------------------------------------------------------------


# ---------------------------------------------------------------------
# Encuestas NPS (incentivo interno de S/10 por encuesta con nota 9 o 10)
#
# Reemplaza el Google Form independiente que se usaba para esto. Se
# registra ligado al PIN del local (igual que Registro/Historial), y el
# pago del incentivo se marca desde el Dashboard del dueno, no desde aca
# -- asi nadie puede marcar su propio incentivo como pagado sin que el
# dueno realmente lo haya hecho.
# ---------------------------------------------------------------------

NOMBRE_HOJA_ENCUESTAS = "Encuestas"

COLUMNAS_ENCUESTAS = [
    "id",
    "timestamp",
    "fecha",
    "local",
    "nombre",  # quien atendio la operacion (puede ser cualquier trabajador, no solo el cajero de turno)
    "nota",  # 9 o 10
    "incentivo",  # siempre 10 (solo se registran encuestas que ya calificaron con 9 o 10)
    "estado_pago",  # "Pendiente" o "Pagada"
    "captura_correo",
    "captura_mensaje_exito",
    "observaciones",
    # Agregada AL FINAL (ver _get_or_create_worksheet): como se paga el
    # incentivo, "Efectivo" o "Yape". La elige quien registra la encuesta y
    # el dueno puede cambiarla desde el Dashboard al momento de pagar.
    "medio_pago",
]
MEDIOS_PAGO_ENCUESTA = ["Efectivo", "Yape"]


def guardar_encuesta(datos: dict) -> None:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_ENCUESTAS, COLUMNAS_ENCUESTAS)
    fila = [datos.get(col, "") for col in COLUMNAS_ENCUESTAS]
    ws.append_row(fila)
    get_encuestas_df.clear()


@st.cache_data(ttl=30, show_spinner=False)
def get_encuestas_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_ENCUESTAS, COLUMNAS_ENCUESTAS)
    registros = ws.get_all_records(numericise_ignore=["all"])
    df = pd.DataFrame(registros, columns=COLUMNAS_ENCUESTAS)
    if df.empty:
        return df
    df = _texto_seguro(df, {"fecha", "timestamp", "nota", "incentivo"})
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["nota"] = pd.to_numeric(df["nota"], errors="coerce")
    df["incentivo"] = pd.to_numeric(df["incentivo"], errors="coerce").fillna(0)
    df["medio_pago"] = df["medio_pago"].fillna("").astype(str).str.strip().str.capitalize()
    df["nombre_original"] = _normalizar_nombre(df["nombre"])
    df["nombre"] = unificar_nombres(df["nombre_original"], df["local"])
    return df


# ---------------------------------------------------------------------
# Notas mensuales de cada local (las manda el ejecutivo de BCP al cerrar el
# mes). El incentivo por encuestas solo se paga si la nota del local en ese
# mes es mayor a UMBRAL_NOTA_LOCAL (ver cuadre.con_estado_nota).
# ---------------------------------------------------------------------
NOMBRE_HOJA_NOTAS = "Notas"
# "tipo" y "fecha_corte" se agregaron al final: una nota "Parcial" es el avance
# del mes (solo informa); la "Final" (la que manda BCP al cerrar) decide el pago.
COLUMNAS_NOTAS = ["mes", "local", "nota", "cargado_por", "timestamp", "tipo", "fecha_corte"]


_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


_TONOS_NPS = {
    "verde": ("#EAF3DE", "#27500A", "#97C459"),
    "rojo": ("#FCEBEB", "#791F1F", "#F09595"),
    "naranja": ("#FAEEDA", "#633806", "#EF9F27"),
}


def _caja_nps(html: str, tono: str) -> None:
    """Recuadro de color con letra GRANDE (el st.success/st.error de siempre
    usa letra chica y el personal no lo notaba)."""
    fondo, texto, borde = _TONOS_NPS[tono]
    st.markdown(
        f'<div style="background:{fondo};color:{texto};border:1.5px solid {borde};'
        f'border-radius:12px;padding:16px 20px;margin:10px 0;font-size:1.35rem;'
        f'line-height:1.5;">{html}</div>',
        unsafe_allow_html=True,
    )


def _nota_grande(nota: float) -> str:
    return f'<span style="font-size:2.1rem;font-weight:700;">{nota:g}</span>'


def mostrar_nota_nps(local: str | None = None) -> None:
    """Recuadros de color (letra grande) con la nota NPS del local: arriba el
    ULTIMO CIERRE (nota final: verde si aprobo y se pagan los incentivos de
    encuestas, rojo si no) y, debajo, el AVANCE del mes en curso si hay una
    nota parcial (verde "vas bien" / naranja "vas por debajo"). Sin ninguna
    nota: aviso naranja. Sin local (nadie puso su PIN todavia) no muestra
    nada. Llamarla justo despues del titulo de cada pagina."""
    local = local or st.session_state.get("local_autenticado")
    if not local:
        return
    try:
        vigente, ultimo_final = nota_vigente(get_notas_df(), local)
    except Exception:
        return  # es solo informativa: si Google esta ocupado, la pagina sigue sin ella
    if vigente is None:
        _caja_nps(
            "⭐ <b>Nota NPS:</b> todavía no hay nota cargada para tu local. Mientras tanto, "
            "los incentivos por encuestas quedan condicionados.",
            "naranja",
        )
        return

    def _mes_txt(fila) -> str:
        anio, mes = str(fila["mes"]).split("-")[:2]
        return f"{_MESES_ES[int(mes) - 1]} {anio}"

    if ultimo_final is not None:
        nota = float(ultimo_final["nota"])
        if estado_nota(nota) == "Aprobado":
            _caja_nps(
                f"⭐ <b>Nota NPS de {_mes_txt(ultimo_final)}:</b> {_nota_grande(nota)}<br>"
                f"¡Aprobada! Se pagan los incentivos por encuestas (la nota mínima es {UMBRAL_NOTA_LOCAL}).",
                "verde",
            )
        else:
            _caja_nps(
                f"⭐ <b>Nota NPS de {_mes_txt(ultimo_final)}:</b> {_nota_grande(nota)}<br>"
                f"No aprobada: la nota mínima es {UMBRAL_NOTA_LOCAL} y ese mes no se pagan los "
                "incentivos por encuestas.",
                "rojo",
            )

    if str(vigente.get("tipo", "")).strip().lower() == "parcial":
        nota = float(vigente["nota"])
        al = f" al {vigente['fecha_corte']}" if str(vigente.get("fecha_corte", "")).strip() else ""
        if estado_parcial(nota) == "En camino":
            _caja_nps(
                f"📈 <b>Avance de {_mes_txt(vigente)}{al}:</b> {_nota_grande(nota)}<br>"
                f"¡Vas bien! Para cobrar los incentivos de este mes hay que cerrar con "
                f"{UMBRAL_NOTA_LOCAL} o más (nota parcial).",
                "verde",
            )
        else:
            _caja_nps(
                f"📈 <b>Avance de {_mes_txt(vigente)}{al}:</b> {_nota_grande(nota)}<br>"
                f"¡Ojo, vas por debajo! Para cobrar los incentivos de este mes hay que cerrar con "
                f"{UMBRAL_NOTA_LOCAL} o más (nota parcial).",
                "naranja",
            )


def guardar_nota(datos: dict) -> None:
    """Guarda la nota de un local en un mes; si ya existia del mismo tipo
    (Final o Parcial), la reemplaza."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_NOTAS, COLUMNAS_NOTAS)
    fila = [datos.get(col, "") for col in COLUMNAS_NOTAS]
    tipo_nuevo = str(datos.get("tipo") or "Final").strip().lower()
    for numero, existente in enumerate(ws.get_all_values()[1:], start=2):
        existente = existente + [""] * len(COLUMNAS_NOTAS)
        tipo_existente = (existente[5].strip() or "Final").lower()
        if (
            existente[0].strip() == str(datos["mes"])
            and existente[1].strip() == str(datos["local"])
            and tipo_existente == tipo_nuevo
        ):
            ws.update(values=[fila], range_name=f"A{numero}:G{numero}")
            break
    else:
        ws.append_row(fila)
    get_notas_df.clear()


@st.cache_data(ttl=30, show_spinner=False)
def get_notas_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_NOTAS, COLUMNAS_NOTAS)
    df = pd.DataFrame(ws.get_all_records(), columns=COLUMNAS_NOTAS)
    if df.empty:
        return df
    df["mes"] = df["mes"].astype(str).str.strip()
    df["local"] = df["local"].astype(str).str.strip()
    df["nota"] = pd.to_numeric(df["nota"], errors="coerce")
    df["tipo"] = df["tipo"].fillna("").astype(str).str.strip().replace("", "Final").str.capitalize()
    df["fecha_corte"] = df["fecha_corte"].fillna("").astype(str).str.strip()
    return df


# ---------------------------------------------------------------------
# Respaldo de los datos
# ---------------------------------------------------------------------
def _hojas_respaldo() -> list:
    return [
        (NOMBRE_HOJA_REGISTROS, COLUMNAS_REGISTROS),
        (NOMBRE_HOJA_AJUSTES, COLUMNAS_AJUSTES),
        (NOMBRE_HOJA_ENCUESTAS, COLUMNAS_ENCUESTAS),
        (NOMBRE_HOJA_NOTAS, COLUMNAS_NOTAS),
        (NOMBRE_HOJA_PERSONAL, COLUMNAS_PERSONAL),
        (NOMBRE_HOJA_SOLICITUDES, COLUMNAS_SOLICITUDES),
        (NOMBRE_HOJA_CONFORMIDADES, COLUMNAS_CONFORMIDAD),
    ]


def crear_respaldo_drive() -> dict:
    """Copia el Google Sheet completo a la carpeta de Drive de la app, con fecha
    y hora en el nombre. Devuelve {'id', 'name', 'webViewLink'}."""
    service = _get_drive_service()
    cuerpo = {"name": f"Respaldo Agente BCP {ahora_local():%Y-%m-%d %H%M}"}
    carpeta = st.secrets.get("drive_folder_id")
    if carpeta:
        cuerpo["parents"] = [carpeta]
    return (
        service.files()
        .copy(
            fileId=st.secrets["spreadsheet_id"],
            body=cuerpo,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        .execute(num_retries=3)
    )


def respaldo_zip_csv() -> bytes:
    """ZIP con un CSV por hoja de datos (registros, ajustes, encuestas, notas y
    personal). NO incluye 'Config' (trae los PIN de los locales)."""
    import csv
    import zipfile

    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as zf:
        for nombre, columnas in _hojas_respaldo():
            filas = _get_or_create_worksheet(nombre, columnas).get_all_values()
            texto = io.StringIO()
            csv.writer(texto).writerows(filas)
            zf.writestr(f"{nombre}.csv", "\ufeff" + texto.getvalue())
    return memoria.getvalue()


# ---------------------------------------------------------------------
# Campañas del BCP (informativo, lo administra el dueno directo en la
# hoja "Campañas": titulo, descripcion, fecha_inicio, fecha_fin, link).
# No hay funcion para "guardar" desde la app a proposito -- es contenido
# que el dueno actualiza el mismo en el Google Sheet, sin tocar codigo,
# igual que ya hace con el fondo minimo en "Config".
# ---------------------------------------------------------------------

NOMBRE_HOJA_CAMPANAS = "Campañas"
COLUMNAS_CAMPANAS = ["titulo", "descripcion", "fecha_inicio", "fecha_fin", "link"]


@st.cache_data(ttl=60, show_spinner=False)
def get_campanas_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_CAMPANAS, COLUMNAS_CAMPANAS)
    registros = ws.get_all_records()
    df = pd.DataFrame(registros, columns=COLUMNAS_CAMPANAS)
    if df.empty:
        return df
    df = _texto_seguro(df, {"fecha_inicio", "fecha_fin"})
    df["fecha_inicio"] = pd.to_datetime(df["fecha_inicio"], errors="coerce").dt.date
    df["fecha_fin"] = pd.to_datetime(df["fecha_fin"], errors="coerce").dt.date
    return df


# ---------------------------------------------------------------------
# Centro de Ayuda (videos y guias). Tambien lo administra el dueno
# directo en la hoja "Ayuda": titulo, descripcion, url_video (link de
# YouTube), url_documento (opcional, cualquier link -- PDF, Drive, etc.).
# ---------------------------------------------------------------------

NOMBRE_HOJA_AYUDA = "Ayuda"
COLUMNAS_AYUDA = ["titulo", "descripcion", "url_video", "url_documento"]


@st.cache_data(ttl=300, show_spinner=False)
def get_ayuda_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_AYUDA, COLUMNAS_AYUDA)
    registros = ws.get_all_records()
    df = pd.DataFrame(registros, columns=COLUMNAS_AYUDA)
    return df if df.empty else _texto_seguro(df, set())


def _fila_de_id(ws, id_valor, columna: int = 1) -> int:
    """Numero de fila (1-based) cuyo id coincide. Compara TEXTO contra TEXTO
    leyendo la columna completa: la busqueda con ws.find() fallaba en silencio
    con ids que parecen numeros ("1234567890", "1234e56789"), y entonces la app
    decia "Guardado" sin haber guardado nada."""
    objetivo = str(id_valor).strip()
    for numero, valor in enumerate(ws.col_values(columna), start=1):
        if str(valor).strip() == objetivo:
            return numero
    raise ValueError(f"No se encontró el registro {objetivo} en la hoja (¿se borró la fila?).")


def _escribir_y_confirmar(ws, cambios: list[dict]) -> None:
    """Escribe celdas como TEXTO (sin que Sheets reinterprete fechas ni
    numeros) y las vuelve a leer: si no coinciden, avisa en vez de dar por
    bueno un guardado que no ocurrio."""
    rangos = [c["range"] for c in cambios]
    # gspread MODIFICA los rangos que recibe (les antepone el nombre de la
    # hoja): se le pasa una copia para poder releer con los rangos originales.
    ws.batch_update([dict(c) for c in cambios], raw=True)
    leidos = ws.batch_get(rangos)
    for cambio, leido in zip(cambios, leidos):
        esperado = str(cambio["values"][0][0]).strip()
        actual = str(leido[0][0]).strip() if leido and leido[0] else ""
        if esperado != actual:
            raise RuntimeError(
                f"No se pudo confirmar el guardado en {cambio['range']}: se esperaba «{esperado}» "
                f"y la hoja tiene «{actual}»."
            )


def actualizar_encuesta(
    id_encuesta: str,
    estado_pago: str | None = None,
    medio_pago: str | None = None,
    fecha: str | None = None,
    observaciones: str | None = None,
) -> None:
    """Guarda en UNA sola operacion el estado de pago, la forma de pago, la
    fecha y/o las observaciones de una encuesta. Si no encuentra la fila o la
    hoja no refleja lo escrito, LANZA un error (nunca un 'guardado' falso)."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_ENCUESTAS, COLUMNAS_ENCUESTAS)
    fila = _fila_de_id(ws, id_encuesta, COLUMNAS_ENCUESTAS.index("id") + 1)
    cambios = []
    for columna, valor in (
        ("estado_pago", estado_pago),
        ("medio_pago", medio_pago),
        ("fecha", fecha),
        ("observaciones", observaciones),
    ):
        if valor is not None:
            a1 = gspread.utils.rowcol_to_a1(fila, COLUMNAS_ENCUESTAS.index(columna) + 1)
            cambios.append({"range": a1, "values": [[valor]]})
    if cambios:
        _escribir_y_confirmar(ws, cambios)
        get_encuestas_df.clear()


def actualizar_estado_pago(id_encuesta: str, nuevo_estado: str) -> None:
    """Cambia el estado de pago ('Pendiente' / 'Pagada') de UNA encuesta."""
    actualizar_encuesta(id_encuesta, estado_pago=nuevo_estado)


def actualizar_medio_pago(id_encuesta: str, medio: str) -> None:
    """Cambia como se paga el incentivo de UNA encuesta ('Efectivo' / 'Yape')."""
    actualizar_encuesta(id_encuesta, medio_pago=medio)


# ---------------------------------------------------------------------
# Ajustes de fondo (retiros/ingresos autorizados por administracion)
#
# Cuando el dueno retira o deposita efectivo/saldo de un local FUERA de
# un turno (p.ej. saca S/1000 de la tarjeta), el fondo cambia sin que haya
# un error de registro. Sin esto, tanto "Continuidad" como "Cuadre por
# turno" (pages/7_Dashboard.py) lo marcarian como una diferencia
# sospechosa. Cada ajuste se registra aca (con motivo) y se resta de la
# comparacion correspondiente.
#
# Un mismo ajuste aplica a UNA de estas tres cosas:
# - turno = "Mañana" / "Tarde": es un ajuste de UN turno especifico, se
#   resta en Cuadre por turno (compara local+fecha+turno).
# - salto_id_cierre = <id de un Cierre>: es un ajuste de UN salto
#   especifico (Cierre -> Apertura siguiente, sea mismo turno, entre
#   turnos o entre dias) en Continuidad -- el esquema NUEVO, preciso
#   porque un mismo dia puede tener varios saltos.
# - turno = "" Y salto_id_cierre = "": esquema VIEJO de Continuidad
#   (de antes de que existiera calcular_saltos), un ajuste "de la noche"
#   por local+fecha -- se sigue leyendo para no perder ajustes ya
#   guardados, pero los nuevos ya no se guardan asi.
# ---------------------------------------------------------------------

NOMBRE_HOJA_AJUSTES = "Ajustes"
COLUMNAS_AJUSTES = [
    "id",
    "timestamp",
    "local",
    # Continuidad (esquema viejo): fecha del CIERRE del salto que se
    # compara. Cuadre por turno: la fecha de ESE turno.
    "fecha",
    # Cuanto cambio el fondo a proposito: negativo = retiro, positivo =
    # ingreso. Mismo signo que "Diferencia (S/)".
    "monto",
    "motivo",
    "autorizado_por",
    # Agregada AL FINAL a proposito (ver _get_or_create_worksheet): asi
    # los ajustes guardados antes de que existiera esta columna no quedan
    # con los datos corridos -- simplemente quedan con turno = "".
    "turno",
    # Igual: agregada al final. Id del registro de Cierre de un salto
    # especifico (ver cuadre.calcular_saltos) -- identifica de forma
    # unica UN salto Cierre->Apertura, para que un ajuste no aplique "a
    # todo el dia" sino solo al hueco que realmente explica.
    "salto_id_cierre",
    # Igual: al final. Nombre de quien PUSO la plata de su bolsillo para
    # cubrir una diferencia (p. ej. la persona que abre y repone lo que
    # falto del dia anterior). Opcional: vacio = nadie repuso. Se usa en
    # la Liquidacion para ponerlo "a favor" de esa persona.
    "repuso",
]


def guardar_ajuste(datos: dict) -> None:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_AJUSTES, COLUMNAS_AJUSTES)
    fila = [datos.get(col, "") for col in COLUMNAS_AJUSTES]
    ws.append_row(fila)
    get_ajustes_df.clear()


def actualizar_repuso_ajuste(id_ajuste: str, repuso: str) -> None:
    """Pone (o cambia) quien repuso la plata en un ajuste ya guardado."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_AJUSTES, COLUMNAS_AJUSTES)
    celda = ws.find(str(id_ajuste), in_column=COLUMNAS_AJUSTES.index("id") + 1)
    if celda is None:
        raise ValueError("No se encontró ese ajuste en la hoja.")
    ws.update_cell(celda.row, COLUMNAS_AJUSTES.index("repuso") + 1, repuso)
    get_ajustes_df.clear()


@st.cache_data(ttl=30, show_spinner=False)
def get_ajustes_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_AJUSTES, COLUMNAS_AJUSTES)
    registros = ws.get_all_records(numericise_ignore=["all"])
    df = pd.DataFrame(registros, columns=COLUMNAS_AJUSTES)
    if df.empty:
        return df
    df = _texto_seguro(df, {"fecha", "timestamp", "monto"})
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["monto"] = pd.to_numeric(df["monto"], errors="coerce").fillna(0)
    df["autorizado_por"] = _normalizar_nombre(df["autorizado_por"])
    df["repuso_original"] = _normalizar_nombre(df["repuso"])
    df["repuso"] = unificar_nombres(df["repuso_original"], df["local"])
    return df


# ---------------------------------------------------------------------
# Solicitudes de efectivo / saldo
#
# Un local lejano pide plata a administracion: "Falta efectivo" (necesita
# billetes en la caja) o "Falta saldo" (le sobra efectivo y necesita saldo en
# la tarjeta). Administracion recibe el aviso por Telegram, verifica en camara
# el movimiento fisico y cierra la solicitud desde el Dashboard. El total del
# local (efectivo + tarjeta) no cambia, asi que el cuadre no se toca.
#
#   Solicitada -> En proceso (verificado en camara) -> Completada
#                 (o Cancelada en cualquier momento antes de completarse)
# ---------------------------------------------------------------------
NOMBRE_HOJA_SOLICITUDES = "Solicitudes"
TIPOS_SOLICITUD = ["Falta efectivo", "Falta saldo"]
CUENTAS_DESTINO = ["Empresa", "Personal"]
COLUMNAS_SOLICITUDES = [
    "id",
    "timestamp",
    "local",
    "tipo",  # "Falta efectivo" | "Falta saldo"
    "monto",
    "urgente",  # "Sí" o vacio
    "solicitado_por",
    "nota",
    "estado",  # Solicitada | En proceso | Completada | Cancelada
    "personal_confianza",  # quien intervino en el local (lo anota administracion)
    "ts_proceso",  # cuando administracion verifico en camara
    "referencia",  # N° de operacion de la transferencia o del deposito
    "cuenta_destino",  # solo "Falta efectivo": a que cuenta se deposito
    "ts_completado",  # cierre (completada o cancelada)
    "observaciones_admin",
]


class SolicitudDuplicada(Exception):
    """La misma solicitud ya se envio hace un momento (doble clic). Se permiten varias abiertas a la vez."""


def _secreto(nombre_secret: str, nombre_env: str) -> str:
    """Un dato sensible desde st.secrets o, si no esta, desde una variable de
    entorno (en Railway es lo mas simple: una variable por dato)."""
    try:
        valor = st.secrets.get(nombre_secret)
    except Exception:
        valor = None
    return str(valor or os.environ.get(nombre_env, "")).strip()


def telegram_chat_ids() -> list[str]:
    """Chats que reciben las alertas: TELEGRAM_CHAT_ID admite uno o varios, separados por
    coma (o punto y coma / espacio). Sin repetidos y en el mismo orden."""
    crudo = _secreto("telegram_chat_id", "TELEGRAM_CHAT_ID")
    vistos: list[str] = []
    for parte in crudo.replace(";", ",").replace(" ", ",").split(","):
        parte = parte.strip()
        if parte and parte not in vistos:
            vistos.append(parte)
    return vistos


def telegram_configurado() -> bool:
    return bool(_secreto("telegram_token", "TELEGRAM_BOT_TOKEN") and telegram_chat_ids())


def _enviar_telegram(token: str, chat_id: str, texto: str) -> bool:
    try:
        cuerpo = urllib.parse.urlencode({"chat_id": chat_id, "text": texto}).encode()
        pedido = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=cuerpo)
        with urllib.request.urlopen(pedido, timeout=8) as respuesta:
            return 200 <= respuesta.status < 300
    except Exception:
        return False


def _enviar_a_todos(token: str, chats: list[str], texto: str) -> tuple[list[str], list[str]]:
    """Envia a cada chat por separado: si uno falla (bloqueo el bot, no toco Iniciar), los
    demas lo reciben igual. Devuelve (a quienes llego, a quienes no)."""
    llegaron, fallaron = [], []
    for chat in chats:
        (llegaron if _enviar_telegram(token, chat, texto) else fallaron).append(chat)
    return llegaron, fallaron


def notificar_telegram_detalle(texto: str) -> tuple[list[str], list[str]]:
    """Como notificar_telegram, pero dice a quienes llego y a quienes no. NUNCA lanza error
    ni muestra el token."""
    token = _secreto("telegram_token", "TELEGRAM_BOT_TOKEN")
    chats = telegram_chat_ids()
    if not token or not chats:
        return [], list(chats)
    return _enviar_a_todos(token, chats, texto)


def notificar_telegram(texto: str) -> bool:
    """Manda un mensaje a los chats de administracion. True si llego a por lo menos uno.
    NUNCA lanza error ni muestra el detalle (la URL lleva el token): si falla, el registro
    o la solicitud igual quedan guardados y solo se avisa que no hubo alerta."""
    return bool(notificar_telegram_detalle(texto)[0])


def notificar_telegram_en_segundo_plano(texto: str) -> None:
    """Igual que notificar_telegram pero sin hacer esperar a quien guarda: se envia desde
    otro hilo (las credenciales se leen aqui, antes de lanzarlo)."""
    token = _secreto("telegram_token", "TELEGRAM_BOT_TOKEN")
    chats = telegram_chat_ids()
    if token and chats:
        threading.Thread(target=_enviar_a_todos, args=(token, chats, texto), daemon=True).start()


def umbral_alerta_diferencia() -> float:
    """Desde cuantos soles de diferencia se avisa por Telegram (6 por defecto). Se puede
    cambiar con la variable ALERTA_DIFERENCIA_SOLES en Railway."""
    try:
        return float(_secreto("alerta_diferencia_soles", "ALERTA_DIFERENCIA_SOLES") or UMBRAL_ALERTA_DIFERENCIA)
    except ValueError:
        return UMBRAL_ALERTA_DIFERENCIA


def _enlace_dashboard() -> str:
    dominio = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    return f"https://{dominio}/Dashboard" if dominio else ""


def _avisar_diferencia(filas_previas: list[dict], nueva: dict) -> None:
    """Despues de guardar una Apertura o un Cierre: si el corte (Cierre) o el salto con el
    Cierre anterior (Apertura) supera el umbral, avisa por Telegram. Reusa las filas que
    guardar_registro ya leyo, asi que no gasta lecturas de Google."""
    if not telegram_configurado():
        return
    df = _tipar_registros(pd.DataFrame(list(filas_previas) + [nueva], columns=COLUMNAS_REGISTROS))
    df["nombre"] = _normalizar_nombre(df["nombre"])
    try:
        df["nombre"] = unificar_nombres(df["nombre"], df["local"])
    except Exception:
        pass  # sin la hoja Personal igual avisamos con el nombre tal cual
    for alerta in alertas_de_registro(df, str(nueva["id"]), umbral_alerta_diferencia()):
        notificar_telegram_en_segundo_plano(texto_alerta_diferencia(alerta, _enlace_dashboard()))


NOMBRE_HOJA_BOT = "Bot"
COLUMNAS_BOT = ["clave", "valor", "timestamp"]


def leer_estado_bot() -> dict:
    """Lo que el bot ya hizo (resumen enviado, turnos ya avisados), para no repetirlo si se
    reinicia. Devuelve {clave: [valores]}."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_BOT, COLUMNAS_BOT)
    estado: dict = {}
    for f in ws.get_all_records(numericise_ignore=["all"]):
        estado.setdefault(str(f.get("clave", "")).strip(), []).append(str(f.get("valor", "")).strip())
    return estado


def guardar_estado_bot(clave: str, valor, unico: bool = True) -> None:
    """unico=True: una sola fila por clave (se actualiza). unico=False: una fila nueva cada vez."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_BOT, COLUMNAS_BOT)
    ahora = ahora_local().replace(tzinfo=None).isoformat(timespec="seconds")
    if unico:
        try:
            fila = _fila_de_id(ws, clave, 1)
        except ValueError:
            fila = None
        if fila:
            _escribir_y_confirmar(ws, [
                {"range": gspread.utils.rowcol_to_a1(fila, 2), "values": [[str(valor)]]},
                {"range": gspread.utils.rowcol_to_a1(fila, 3), "values": [[ahora]]},
            ])
            return
    ws.append_row([clave, str(valor), ahora])


def telegram_token_configurado() -> bool:
    return bool(_secreto("telegram_token", "TELEGRAM_BOT_TOKEN"))


def _telegram_api(metodo: str):
    """Llama a un metodo de la API de Telegram. Devuelve (resultado, error): uno de los
    dos es None. El texto del error NUNCA incluye el token."""
    token = _secreto("telegram_token", "TELEGRAM_BOT_TOKEN")
    if not token:
        return None, "Falta el token."
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{metodo}", timeout=8) as respuesta:
            datos = json.loads(respuesta.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detalle = json.loads(e.read().decode("utf-8")).get("description", "")
        except Exception:
            detalle = ""
        return None, f"Telegram respondió {e.code}" + (f": {detalle}" if detalle else "")
    except Exception:
        return None, "El servidor no pudo conectarse con Telegram."
    if not datos.get("ok", True):
        return None, f"Telegram respondió: {datos.get('description', 'error')}"
    return datos.get("result"), None


def telegram_diagnostico() -> dict:
    """Para el asistente del Dashboard: a que bot pertenece el token, que chats le
    escribieron (Telegram guarda ~24 h) y, si algo falla, por que."""
    info, error = _telegram_api("getMe")
    if error:
        return {"bot": None, "chats": [], "error": error}
    bot = {"usuario": (info or {}).get("username", ""), "nombre": (info or {}).get("first_name", "")}
    updates, error = _telegram_api("getUpdates")
    if error:
        return {"bot": bot, "chats": [], "error": error}
    vistos: dict = {}
    for upd in updates or []:
        for clave in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (upd.get(clave) or {}).get("chat")
            if chat and chat.get("id") is not None:
                nombre = (
                    chat.get("title")
                    or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                    or chat.get("username")
                    or ""
                )
                vistos[chat["id"]] = {"id": chat["id"], "nombre": nombre, "tipo": chat.get("type", "")}
    return {"bot": bot, "chats": list(vistos.values()), "error": ""}


def telegram_chats_recientes() -> list[dict]:
    """Solo la lista de chats (ver telegram_diagnostico)."""
    return telegram_diagnostico()["chats"]


def _texto_alerta_solicitud(datos: dict, previas: int = 0) -> str:
    pide_efectivo = datos["tipo"] == "Falta efectivo"
    accion = (
        "Acción: el personal de confianza entrega efectivo de la reserva; el cajero deposita ese "
        "monto en el mismo agente a la cuenta."
        if pide_efectivo
        else "Acción: el efectivo va a la reserva (verifica en cámara) y tú transfieres a su tarjeta."
    )
    lineas = [
        f"🔔 {datos['local']} pide {'EFECTIVO' if pide_efectivo else 'SALDO'}"
        + ("  ⚡ URGENTE" if str(datos.get("urgente", "")).strip() == "Sí" else ""),
        f"💰 S/ {float(datos['monto']):,.2f}",
        f"👤 {datos['solicitado_por']}",
    ]
    if str(datos.get("nota", "")).strip():
        lineas.append(f"📝 {str(datos['nota']).strip()}")
    if previas:
        lineas.append(f"📌 Este local ya tenía {previas} solicitud(es) de este tipo sin atender.")
    lineas.append(accion)
    lineas.append("Atiéndela en Dashboard → 📨 Solicitudes.")
    return "\n".join(lineas)


def guardar_solicitud(datos: dict) -> bool:
    """Guarda una solicitud nueva (estado Solicitada) y avisa por Telegram. Devuelve True si el aviso salio.
    Un local puede tener VARIAS solicitudes abiertas a la vez (aunque administracion no haya respondido las
    anteriores); lo unico que se rechaza es enviar exactamente la misma dos veces seguidas (doble clic)."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_SOLICITUDES, COLUMNAS_SOLICITUDES)
    filas = ws.get_all_records(numericise_ignore=["all"])
    ahora = ahora_local().replace(tzinfo=None)
    repetida = solicitud_repetida(filas, datos, ahora)
    if repetida:
        raise SolicitudDuplicada(
            f"Ya enviaste esa misma solicitud hace un momento (S/ {repetida.get('monto', '')}). "
            "Si es otra, cambia el monto o espera un par de minutos."
        )
    previas = sum(
        1
        for f in filas
        if str(f.get("local", "")).strip() == str(datos["local"]).strip()
        and str(f.get("tipo", "")).strip() == str(datos["tipo"]).strip()
        and str(f.get("estado", "")).strip() in ESTADOS_SOLICITUD_ACTIVOS
    )
    fila = {
        "id": nuevo_id(),
        "timestamp": ahora.isoformat(timespec="seconds"),
        "estado": "Solicitada",
        **datos,
    }
    ws.append_row([fila.get(col, "") for col in COLUMNAS_SOLICITUDES])
    get_solicitudes_df.clear()
    return notificar_telegram(_texto_alerta_solicitud(fila, previas))


@st.cache_data(ttl=30, show_spinner=False)
def get_solicitudes_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_SOLICITUDES, COLUMNAS_SOLICITUDES)
    registros = ws.get_all_records(numericise_ignore=["all"])
    df = pd.DataFrame(registros, columns=COLUMNAS_SOLICITUDES)
    if df.empty:
        return df
    df = _texto_seguro(df, {"timestamp", "ts_proceso", "ts_completado", "monto"})
    for col in ("timestamp", "ts_proceso", "ts_completado"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df["monto"] = pd.to_numeric(df["monto"], errors="coerce").fillna(0)
    for col in ("local", "estado", "tipo"):
        df[col] = df[col].str.strip()
    return df.sort_values("timestamp", ascending=False).reset_index(drop=True)


def actualizar_solicitud(id_solicitud: str, **campos) -> None:
    """Cambia campos de UNA solicitud en una sola escritura confirmada. Si no
    encuentra la fila o la hoja no refleja lo escrito, LANZA un error."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_SOLICITUDES, COLUMNAS_SOLICITUDES)
    fila = _fila_de_id(ws, id_solicitud, COLUMNAS_SOLICITUDES.index("id") + 1)
    cambios = []
    for columna, valor in campos.items():
        if columna not in COLUMNAS_SOLICITUDES or columna == "id":
            raise ValueError(f"Campo no permitido en una solicitud: {columna}")
        a1 = gspread.utils.rowcol_to_a1(fila, COLUMNAS_SOLICITUDES.index(columna) + 1)
        cambios.append({"range": a1, "values": [[valor]]})
    if cambios:
        _escribir_y_confirmar(ws, cambios)
        get_solicitudes_df.clear()


def _ahora_iso() -> str:
    return ahora_local().replace(tzinfo=None).isoformat(timespec="seconds")


def solicitud_en_proceso(id_solicitud: str, personal_confianza: str) -> None:
    """Administracion verifico en camara el movimiento fisico."""
    actualizar_solicitud(
        id_solicitud, estado="En proceso", personal_confianza=personal_confianza.strip(), ts_proceso=_ahora_iso()
    )


def solicitud_completada(id_solicitud: str, referencia: str, cuenta_destino: str = "") -> None:
    actualizar_solicitud(
        id_solicitud,
        estado="Completada",
        referencia=referencia.strip(),
        cuenta_destino=cuenta_destino,
        ts_completado=_ahora_iso(),
    )


def solicitud_cancelada(id_solicitud: str, motivo: str = "", local: str = "", tipo: str = "", por: str = "") -> bool:
    """Cancela una solicitud y avisa por Telegram. Devuelve si el aviso salio."""
    actualizar_solicitud(
        id_solicitud, estado="Cancelada", observaciones_admin=motivo.strip(), ts_completado=_ahora_iso()
    )
    return notificar_telegram(
        f"✖ Solicitud cancelada: {local} · {tipo}"
        + (f" (por {por})" if por else "")
        + (f"\nMotivo: {motivo.strip()}" if motivo.strip() else "")
    )


# ---------------------------------------------------------------------
# Conformidad del trabajador (ver cuadre.con_conformidad)
# ---------------------------------------------------------------------
NOMBRE_HOJA_CONFORMIDADES = "Conformidades"


def guardar_conformidad(datos: dict) -> None:
    """Agrega un registro de conformidad (nunca se pisa uno anterior: queda el historial y manda el
    ultimo). `datos`: tipo, id_cierre, local, fecha, persona, monto, estado, fecha_conformidad,
    registrado_por, nota."""
    if str(datos.get("estado", "")) not in ESTADOS_CONFORMIDAD:
        raise ValueError(f"Estado de conformidad no valido: {datos.get('estado')}")
    if not str(datos.get("id_cierre", "")).strip():
        raise ValueError("Esta diferencia no tiene un identificador de Cierre: no se puede guardar su conformidad.")
    ws = _get_or_create_worksheet(NOMBRE_HOJA_CONFORMIDADES, COLUMNAS_CONFORMIDAD)
    fila = {"id": nuevo_id(), "timestamp": ahora_local().replace(tzinfo=None).isoformat(timespec="seconds"), **datos}
    ws.append_row([str(fila.get(c, "")) for c in COLUMNAS_CONFORMIDAD])
    get_conformidades_df.clear()


@st.cache_data(ttl=30, show_spinner=False)
def get_conformidades_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_CONFORMIDADES, COLUMNAS_CONFORMIDAD)
    df = pd.DataFrame(ws.get_all_records(numericise_ignore=["all"]), columns=COLUMNAS_CONFORMIDAD)
    if df.empty:
        return df
    df = _texto_seguro(df, {"timestamp", "monto"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["monto"] = pd.to_numeric(df["monto"], errors="coerce").fillna(0)
    return df
