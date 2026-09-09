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
# Se re-exporta para que el resto de la app la use como sh.calcular_cortes, etc.
from cuadre import (  # noqa: F401  (re-export para el resto de la app)
    UMBRAL_AMARILLO,
    UMBRAL_VERDE,
    acumulado_por_persona,
    calcular_cortes,
    resumen_turnos,
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
# Config (por local: fondo minimo, PIN, tipo de agente y tarifa de comision)
# ---------------------------------------------------------------------

COLUMNAS_CONFIG = ["local", "fondo_minimo", "pin", "tipo_agente", "soles_por_operacion"]

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
    df = _texto_seguro(df, {"fondo_minimo", "soles_por_operacion"})
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
    if not _imagen_completa(contenido) or not _imagen_decodificable(contenido):
        # La foto llego cortada o dañada (se corto la conexion, o el
        # archivo no es una imagen valida). No la mandamos a Drive: un
        # archivo asi despues rompe el Historial. Que la vuelvan a subir.
        raise ValueError(
            "La foto no se pudo leer (llegó incompleta o dañada). Vuelve a "
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


def mostrar_imagen(data, **kwargs) -> bool:
    """st.image() protegido. Si la imagen no se puede mostrar (archivo
    dañado, formato raro), pone un aviso y devuelve False en vez de dejar
    que el error tumbe la pagina. Un Segmentation fault de Pillow no se
    puede atrapar desde Python -- para eso esta _imagen_completa(), que
    filtra los archivos truncados antes de llegar aca."""
    if not data:
        return False
    try:
        st.image(data, **kwargs)
        return True
    except Exception:
        st.caption("⚠️ La foto no se pudo mostrar (archivo dañado o incompleto).")
        return False


def _imagen_completa(data: bytes | None) -> bool:
    """True solo si `data` parece un JPEG o PNG **completo** (sin decodificar).

    POR QUE: cuando a alguien se le corta la conexion subiendo un voucher,
    queda un archivo truncado en Drive. Si ese archivo llega a st.image(),
    Pillow a veces no lanza un error limpio sino que tumba el proceso
    entero (Segmentation fault). Chequeando la firma del inicio Y el
    marcador de fin (que un archivo cortado no tiene) descartamos lo mas
    obvio antes de que Pillow lo toque.
    """
    if not data or len(data) < 100:
        return False
    if data[:3] == b"\xff\xd8\xff":  # JPEG: empieza en SOI
        return b"\xff\xd9" in data[-16:]  # ...y termina en EOI
    if data[:8] == b"\x89PNG\r\n\x1a\n":  # PNG: firma
        return b"IEND" in data[-16:]  # ...y termina en el chunk IEND
    return False


def _imagen_decodificable(data: bytes) -> bool:
    """Segunda barrera: que Pillow pueda abrir y verificar la imagen.

    verify() revisa la integridad SIN hacer el decode completo, asi que
    atrapa muchos archivos corruptos (datos internos danados) con menos
    riesgo que un st.image() directo. No es 100% a prueba de un segfault
    de libjpeg, pero cierra bastante la ventana.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        return True
    except Exception:
        return False


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
    # Doble barrera antes de que la foto llegue a st.image():
    # 1) estructura completa (rechaza truncados sin tocar Pillow),
    # 2) Pillow puede abrirla y verificarla.
    # Si no pasa las dos, devolvemos None -> "no se pudo cargar".
    if not _imagen_completa(data) or not _imagen_decodificable(data):
        return None
    return data


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


def _registros_crudos_del_turno(local: str, fecha: str, turno: str) -> list[dict]:
    """Lee la hoja 'Registros' SIN cache y devuelve las filas de ese turno
    (mismo local + misma fecha + mismo Mañana/Tarde), ordenadas por hora."""
    ws = _get_or_create_worksheet(NOMBRE_HOJA_REGISTROS, COLUMNAS_REGISTROS)
    filas = ws.get_all_records()
    del_turno = [
        f
        for f in filas
        if str(f.get("local", "")) == str(local)
        and str(f.get("fecha", "")) == str(fecha)
        and str(f.get("turno", "")) == str(turno)
    ]
    del_turno.sort(key=lambda f: str(f.get("timestamp", "")))
    return del_turno


def estado_turno(local: str, fecha: str, turno: str) -> dict:
    """Como esta el turno AHORA MISMO (lectura fresca, sin cache):

    - abierto: True si el ultimo registro del turno es una Apertura que
      todavia no tiene su Cierre.
    - apertura_abierta: {"nombre", "hora"} de esa Apertura, o None.
    - ultimo_tipo: "Apertura" | "Cierre" | None (si el turno no tiene nada).
    """
    filas = _registros_crudos_del_turno(local, fecha, turno)
    if not filas:
        return {"abierto": False, "apertura_abierta": None, "ultimo_tipo": None}
    ultimo = filas[-1]
    if str(ultimo.get("tipo", "")).strip() == "Apertura":
        ts = str(ultimo.get("timestamp", ""))
        hora = ts[11:16] if len(ts) >= 16 else ""
        return {
            "abierto": True,
            "apertura_abierta": {
                "nombre": str(ultimo.get("nombre", "")).strip(),
                "hora": hora,
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
    estado = estado_turno(datos["local"], datos["fecha"], datos["turno"])

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


@st.cache_data(ttl=30, show_spinner=False)
def get_registros_df() -> pd.DataFrame:
    ws = _get_or_create_worksheet(NOMBRE_HOJA_REGISTROS, COLUMNAS_REGISTROS)
    registros = ws.get_all_records()
    df = pd.DataFrame(registros, columns=COLUMNAS_REGISTROS)
    if df.empty:
        return df
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

    df["nombre"] = _normalizar_nombre(df["nombre"])
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
    df = _texto_seguro(df, {"fecha", "timestamp", "nota", "incentivo"})
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["nota"] = pd.to_numeric(df["nota"], errors="coerce")
    df["incentivo"] = pd.to_numeric(df["incentivo"], errors="coerce").fillna(0)
    df["nombre"] = _normalizar_nombre(df["nombre"])
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
