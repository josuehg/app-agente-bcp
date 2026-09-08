"""
cuadre.py
----------
Logica del "cuadre por turno" del Agente BCP.

MODELO DE TURNO (con cierres parciales)
--------------------------------------
Un turno = local + fecha + Mañana/Tarde. Durante el turno puede haber
VARIOS cortes: la persona cierra la caja (cuenta todo), a veces se retira
o se ingresa efectivo a proposito, y se vuelve a abrir. Cada par
Apertura -> Cierre es un CORTE.

- La secuencia normal alterna Apertura, Cierre, Apertura, Cierre... y
  SIEMPRE termina en Cierre.
- La MISMA persona abre y cierra su corte. Si el Cierre quedo a nombre de
  otra persona, se registra igual (con un motivo), pero la diferencia del
  corte se le atribuye SIEMPRE a quien ABRIO.
- Diferencia de un corte = total del Cierre - total de la Apertura.
- Lo que cambie ENTRE el Cierre de un corte y la Apertura del siguiente
  (un retiro o un ingreso hechos a proposito) NO es descuadre: cada corte
  se mide solo contra su propia Apertura, asi que ese movimiento no
  ensucia el calculo.

Este archivo NO importa streamlit ni gspread (para poder probarlo con
pytest sin credenciales). sheets_utils.py lo re-exporta, asi que el resto
de la app lo usa como sh.calcular_cortes(...), sh.resumen_turnos(...), etc.
"""

from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------
# UMBRALES DEL SEMAFORO (por corte): punto de partida razonable, no una
# regla fija. Si en la practica quedan muy estrictos o muy sueltos, se
# cambian estos dos numeros.
# ---------------------------------------------------------------------
UMBRAL_VERDE = 1.0  # 0 a S/1: cuadrado (redondeos normales)
UMBRAL_AMARILLO = 50.0  # S/1 a S/50: revisar; mas de S/50: diferencia grande

ESTADO_CUADRADO = "✅ Cuadrado"
ESTADO_REVISAR = "🟡 Revisar"
ESTADO_GRANDE = "🔴 Diferencia grande"
ESTADO_ABIERTO = "⏳ Apertura sin Cierre"
ESTADO_CIERRE_SUELTO = "⚠️ Cierre sin Apertura"
ESTADO_NOMBRE_DISTINTO = "⚠️ Cerró otro nombre"
ESTADO_SECUENCIA = "⚠️ Revisar secuencia"

COLUMNAS_CORTE = [
    "corte",
    "nombre",  # a quien se le atribuye: SIEMPRE quien abrio
    "nombre_cierre",  # quien registro el Cierre (puede ser otro)
    "hora_apertura",
    "hora_cierre",
    "apertura",
    "cierre",
    "diferencia",
    "diferencia_fmt",
    "estado",
    "motivo",  # motivo por el que cerro otra persona, si aplica
]

COLUMNAS_RESUMEN = ["n_cortes", "nombres", "diferencia", "diferencia_fmt", "estado"]

COLUMNAS_PERSONA = ["nombre", "n_cortes", "diferencia", "diferencia_fmt", "descuadre_abs"]


def _semaforo(diferencia: float) -> str:
    d = abs(diferencia)
    if d <= UMBRAL_VERDE:
        return ESTADO_CUADRADO
    if d <= UMBRAL_AMARILLO:
        return ESTADO_REVISAR
    return ESTADO_GRANDE


def _fmt(x) -> str:
    return f"{x:+,.2f}" if pd.notna(x) else ""


def _hora(reg) -> str:
    if reg is None:
        return ""
    ts = pd.to_datetime(reg.get("timestamp"), errors="coerce")
    return "" if pd.isna(ts) else ts.strftime("%H:%M")


def _total(reg):
    if reg is None:
        return pd.NA
    valor = pd.to_numeric(reg.get("total"), errors="coerce")
    return pd.NA if pd.isna(valor) else float(valor)


def _nombre(reg) -> str:
    return "" if reg is None else str(reg.get("nombre", "")).strip()


def _fila_corte(contexto: dict, numero: int, apertura, cierre, estado_forzado) -> dict:
    ap_total = _total(apertura)
    ci_total = _total(cierre)
    nombre_ap = _nombre(apertura)
    nombre_ci = _nombre(cierre)
    motivo = ""
    if cierre is not None:
        motivo = str(cierre.get("motivo_cierre_otro_nombre", "") or "").strip()

    if pd.notna(ap_total) and pd.notna(ci_total):
        diferencia = ci_total - ap_total
        if nombre_ap and nombre_ci and nombre_ap.lower() != nombre_ci.lower():
            estado = ESTADO_NOMBRE_DISTINTO
        else:
            estado = _semaforo(diferencia)
    else:
        diferencia = pd.NA
        estado = estado_forzado or ESTADO_ABIERTO

    fila = dict(contexto)
    fila.update(
        {
            "corte": numero,
            # Atribucion: SIEMPRE quien abrio. Si no hay Apertura (Cierre
            # suelto), cae al nombre del Cierre para no perderlo.
            "nombre": nombre_ap or nombre_ci,
            "nombre_cierre": nombre_ci,
            "hora_apertura": _hora(apertura),
            "hora_cierre": _hora(cierre),
            "apertura": ap_total,
            "cierre": ci_total,
            "diferencia": diferencia,
            "diferencia_fmt": _fmt(diferencia),
            "estado": estado,
            "motivo": motivo,
        }
    )
    return fila


def _cortes_de_un_turno(grupo: pd.DataFrame, contexto: dict) -> list[dict]:
    """Recorre los registros de UN turno (ya ordenados por hora) y arma la
    lista de cortes, emparejando cada Apertura con su Cierre."""
    cortes: list[dict] = []
    numero = 0
    apertura_abierta = None

    for _, reg in grupo.iterrows():
        tipo = str(reg.get("tipo", "")).strip()
        if tipo == "Apertura":
            if apertura_abierta is not None:
                # Dos Aperturas seguidas sin Cierre en medio: la anterior
                # queda como corte abierto (anomalia).
                numero += 1
                cortes.append(
                    _fila_corte(contexto, numero, apertura_abierta, None, ESTADO_ABIERTO)
                )
            apertura_abierta = reg
        elif tipo == "Cierre":
            numero += 1
            if apertura_abierta is None:
                cortes.append(
                    _fila_corte(contexto, numero, None, reg, ESTADO_CIERRE_SUELTO)
                )
            else:
                cortes.append(_fila_corte(contexto, numero, apertura_abierta, reg, None))
                apertura_abierta = None

    if apertura_abierta is not None:
        numero += 1
        cortes.append(
            _fila_corte(contexto, numero, apertura_abierta, None, ESTADO_ABIERTO)
        )

    return cortes


def calcular_cortes(df: pd.DataFrame, columnas_indice: list[str]) -> pd.DataFrame:
    """
    Recibe registros (una fila por Apertura o Cierre) y devuelve UNA FILA
    POR CORTE, con la diferencia y el semaforo de cada corte.

    `columnas_indice` define como se agrupan los turnos:
    ["local","fecha","turno"] para el Dashboard (todos los locales) o
    ["fecha","turno"] para el Historial de un local.

    Columnas del resultado: columnas_indice + COLUMNAS_CORTE.
    """
    columnas_salida = list(columnas_indice) + COLUMNAS_CORTE
    if df is None or df.empty:
        return pd.DataFrame(columns=columnas_salida)

    faltan = (set(columnas_indice) | {"tipo", "total", "nombre"}) - set(df.columns)
    if faltan:
        raise KeyError(f"Faltan columnas para el cuadre: {sorted(faltan)}")

    trabajo = df.copy().reset_index(drop=True)
    # Orden cronologico dentro de cada turno. Si falta timestamp (datos
    # viejos), se conserva el orden en que venian las filas.
    if "timestamp" in trabajo.columns:
        trabajo["_ts"] = pd.to_datetime(trabajo["timestamp"], errors="coerce")
    else:
        trabajo["_ts"] = pd.NaT
    trabajo["_orden"] = range(len(trabajo))
    trabajo = trabajo.sort_values(["_ts", "_orden"], kind="stable")

    filas: list[dict] = []
    for claves, grupo in trabajo.groupby(columnas_indice, dropna=False, sort=False):
        if not isinstance(claves, tuple):
            claves = (claves,)
        contexto = dict(zip(columnas_indice, claves))
        filas.extend(_cortes_de_un_turno(grupo, contexto))

    salida = pd.DataFrame(filas, columns=columnas_salida)
    return _tipos_seguros(salida, columnas_indice)


def _tipos_seguros(df: pd.DataFrame, columnas_indice: list[str]) -> pd.DataFrame:
    """Deja las columnas con tipos limpios: numeros como float/int (con
    NaN, nunca pd.NA en columnas 'object'), y todo lo demas como texto.

    POR QUE: si 'apertura'/'cierre'/'diferencia' quedan como columna
    'object' con floats y pd.NA mezclados (pasa cuando a un corte le
    falta la Apertura o el Cierre), st.dataframe al convertir a Arrow
    puede tirar el proceso entero (Segmentation fault) en pyarrow. Con
    dtypes limpios esa conversion es trivial y segura.
    """
    if df.empty:
        return df
    for col in ["apertura", "cierre", "diferencia"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["corte"] = pd.to_numeric(df["corte"], errors="coerce").fillna(0).astype(int)
    for col in df.columns:
        if col in columnas_indice or col in {"apertura", "cierre", "diferencia", "corte"}:
            continue
        df[col] = df[col].fillna("").astype(str).replace({"nan": "", "None": ""})
    return df


def _estado_turno(estados: set[str]) -> str:
    """El estado que se muestra para el turno completo, tomando lo mas
    urgente de sus cortes."""
    if {ESTADO_ABIERTO, ESTADO_CIERRE_SUELTO} & estados:
        return ESTADO_SECUENCIA
    if ESTADO_NOMBRE_DISTINTO in estados:
        return ESTADO_NOMBRE_DISTINTO
    if ESTADO_GRANDE in estados:
        return ESTADO_GRANDE
    if ESTADO_REVISAR in estados:
        return ESTADO_REVISAR
    return ESTADO_CUADRADO


def resumen_turnos(cortes: pd.DataFrame, columnas_indice: list[str]) -> pd.DataFrame:
    """Junta los cortes de cada turno en una sola fila: cuantos cortes
    tuvo, la SUMA de sus diferencias y el estado del turno."""
    columnas_salida = list(columnas_indice) + COLUMNAS_RESUMEN
    if cortes is None or cortes.empty:
        return pd.DataFrame(columns=columnas_salida)

    filas: list[dict] = []
    for claves, grupo in cortes.groupby(columnas_indice, dropna=False, sort=False):
        if not isinstance(claves, tuple):
            claves = (claves,)
        contexto = dict(zip(columnas_indice, claves))
        diferencia_total = grupo["diferencia"].dropna().sum()
        # Personas que trabajaron el turno (quien abrio cada corte), sin
        # repetir y en el orden en que aparecieron.
        nombres = ", ".join(dict.fromkeys(n for n in grupo["nombre"].astype(str) if n))
        filas.append(
            {
                **contexto,
                "n_cortes": len(grupo),
                "nombres": nombres,
                "diferencia": diferencia_total,
                "diferencia_fmt": f"{diferencia_total:+,.2f}",
                "estado": _estado_turno(set(grupo["estado"])),
            }
        )
    return pd.DataFrame(filas, columns=columnas_salida)


def acumulado_por_persona(cortes: pd.DataFrame) -> pd.DataFrame:
    """Por cada persona (la que ABRIO cada corte): cuantos cortes hizo, la
    SUMA de sus diferencias con signo, y el descuadre en valor absoluto
    (para ordenar de mayor a menor 'ruido')."""
    if cortes is None or cortes.empty:
        return pd.DataFrame(columns=COLUMNAS_PERSONA)

    completos = cortes[cortes["diferencia"].notna() & (cortes["nombre"].astype(str) != "")]
    if completos.empty:
        return pd.DataFrame(columns=COLUMNAS_PERSONA)

    agrupado = (
        completos.groupby("nombre")
        .agg(
            n_cortes=("diferencia", "count"),
            diferencia=("diferencia", "sum"),
            descuadre_abs=("diferencia", lambda s: s.abs().sum()),
        )
        .reset_index()
    )
    agrupado["diferencia_fmt"] = agrupado["diferencia"].apply(lambda x: f"{x:+,.2f}")
    return agrupado.sort_values("descuadre_abs", ascending=False)[COLUMNAS_PERSONA]
