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
# Se re-exporta para que el resto de la app la use como sh.calcular_cuadre_turnos.
from cuadre import (  # noqa: F401  (re-export para el resto de la app)
    UMBRAL_AMARILLO,
    UMBRAL_VERDE,
    calcular_cuadre_turnos,
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


@st.cache_resource(show_spinner=False)
def _get_gspread_client() -> gspread.Client:
    return gspread.authorize(_get_credentials())


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


def _get_or_create_worksheet(nombre: str, columnas: list[str]):
    """Devuelve la hoja (tab) con ese nombre dentro del spreadsheet.

    Si no existe todavia, la crea con el encabezado correcto. Si ya
    existe pero su fila 1 (encabezado) no coincide con las columnas que
    la app usa ahora mismo -- por ejemplo, porque se agrego un campo
    nuevo como "turno" -- la corrige automaticamente. Asi no hace falta
    borrar la pestana a mano cada vez que el codigo cambia; los datos
    ya guardados en las filas de abajo no se tocan.
    """
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
    return ws


# ---------------------------------------------------------------------
# Config (locales + fondo minimo por local)
# ---------------------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def get_config_df() -> pd.DataFrame:
    """Lee la hoja 'Config' (columnas: local, fondo_minimo, pin).

    La columna "pin" es el PIN de acceso propio de cada local para la
    pagina de Historial (pages/3_Historial.py): con ese PIN, un cajero
    entra directo a ver SOLO los registros de su tienda, sin tener que
    elegir el local de una lista (y sin poder ver los de las demas).

    Se cachea 60 segundos: si cambias el fondo minimo o el PIN en la
    hoja, tarda como maximo un minuto en reflejarse en la app (para no
    golpear la API de Google en cada segundo).
    """
    ws = _get_or_create_worksheet(NOMBRE_HOJA_CONFIG, ["local", "fondo_minimo", "pin"])
    registros = ws.get_all_records()
    if not registros:
        # Primera vez que se usa la app: sembramos 6 locales de ejemplo
        # con un fondo minimo de referencia de S/ 5000 y un PIN de
        # prueba (1001, 1002, ...). El usuario los cambia directamente
        # en la hoja de Google, sin tocar codigo.
        locales_default = [f"Local {i+1}" for i in range(6)]
        for i, local in enumerate(locales_default):
            ws.append_row([local, 5000, f"{1001 + i}"])
        registros = ws.get_all_records()
    df = pd.DataFrame(registros)
    df["fondo_minimo"] = pd.to_numeric(df["fondo_minimo"], errors="coerce").fillna(0)
    if "pin" not in df.columns:
        # Sheet de una version anterior a que existiera esta columna.
        df["pin"] = ""
    # Los PIN se guardan como texto (aunque parezcan numeros) para poder
    # comparar tal cual lo que el cajero escribe, sin lios de "0100" vs 100.
    df["pin"] = df["pin"].astype(str).str.strip()
    return df


def get_locales() -> list[str]:
    return get_config_df()["local"].tolist()


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
    service = _get_drive_service()
    media = MediaIoBaseUpload(
        io.BytesIO(archivo.getvalue()), mimetype=archivo.type, resumable=False
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

    # Hacemos el archivo visible para "cualquiera que tenga el link".
    # No queda publico en buscadores, pero cualquiera con la URL exacta
    # puede verlo. Es el trade-off mas simple para que tu y el personal
    # puedan abrir la foto sin tener que dar acceso persona por persona.
    service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
        supportsAllDrives=True,
    ).execute(num_retries=3)

    return f"https://drive.google.com/file/d/{file_id}/view"


@st.cache_data(ttl=3600, show_spinner=False)
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
        return service.files().get_media(fileId=file_id, supportsAllDrives=True).execute(num_retries=2)
    except Exception:
        return None


# ---------------------------------------------------------------------
# Registros (aperturas y cierres)
# ---------------------------------------------------------------------

def nuevo_id() -> str:
    return uuid.uuid4().hex[:10]


def guardar_registro(datos: dict) -> None:
    """Agrega una fila nueva a la hoja 'Registros'.

    `datos` debe traer las llaves de COLUMNAS_REGISTROS que apliquen;
    las que falten se guardan vacias, para no romper si un campo no
    aplica (ej: num_operaciones no existe en una Apertura).
    """
    ws = _get_or_create_worksheet(NOMBRE_HOJA_REGISTROS, COLUMNAS_REGISTROS)
    fila = [datos.get(col, "") for col in COLUMNAS_REGISTROS]
    ws.append_row(fila)
    # Como acabamos de escribir, invalidamos el cache de lectura para que
    # el dashboard muestre este registro sin esperar el TTL completo.
    get_registros_df.clear()


@st.cache_data(ttl=30, show_spinner=False)
def get_registros_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_REGISTROS, COLUMNAS_REGISTROS)
    registros = ws.get_all_records()
    df = pd.DataFrame(registros, columns=COLUMNAS_REGISTROS)
    if df.empty:
        return df
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for col in ["efectivo", "tarjeta", "total", "num_operaciones"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ---------------------------------------------------------------------
# Cuadre por turno (Apertura vs Cierre): la logica vive en cuadre.py
# (sin depender de streamlit ni de Google, para poder probarla con
# pytest). Se importa arriba y el resto de la app la sigue llamando como
# sh.calcular_cuadre_turnos(...) / sh.UMBRAL_VERDE.
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
]


def guardar_encuesta(datos: dict) -> None:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_ENCUESTAS, COLUMNAS_ENCUESTAS)
    fila = [datos.get(col, "") for col in COLUMNAS_ENCUESTAS]
    ws.append_row(fila)
    get_encuestas_df.clear()


@st.cache_data(ttl=30, show_spinner=False)
def get_encuestas_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_ENCUESTAS, COLUMNAS_ENCUESTAS)
    registros = ws.get_all_records()
    df = pd.DataFrame(registros, columns=COLUMNAS_ENCUESTAS)
    if df.empty:
        return df
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["nota"] = pd.to_numeric(df["nota"], errors="coerce")
    df["incentivo"] = pd.to_numeric(df["incentivo"], errors="coerce").fillna(0)
    return df


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
    return pd.DataFrame(registros, columns=COLUMNAS_AYUDA)


def actualizar_estado_pago(id_encuesta: str, nuevo_estado: str) -> None:
    """Cambia el estado de pago ('Pendiente' / 'Pagada') de UNA encuesta,
    buscandola por su id. La usa el Dashboard del dueno cuando marca que
    ya le pago el incentivo a alguien (o si se equivoco, para revertirlo).
    """
    ws = _get_or_create_worksheet(NOMBRE_HOJA_ENCUESTAS, COLUMNAS_ENCUESTAS)
    columna_id = COLUMNAS_ENCUESTAS.index("id") + 1
    # En gspread 6.x, ws.find() devuelve None si no encuentra la celda (ya
    # no lanza la excepcion CellNotFound, que fue eliminada). Si el id no
    # existe -- por ejemplo, la fila se borro a mano del Sheet -- no hay
    # nada que actualizar y salimos sin error.
    celda = ws.find(id_encuesta, in_column=columna_id)
    if celda is None:
        return
    columna_estado = COLUMNAS_ENCUESTAS.index("estado_pago") + 1
    ws.update_cell(celda.row, columna_estado, nuevo_estado)
    get_encuestas_df.clear()
