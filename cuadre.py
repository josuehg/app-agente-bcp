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
# UMBRAL DEL SEMAFORO (por corte): punto de partida razonable, no una
# regla fija. Es binario a proposito (sin un estado intermedio "revisar")
# -- o cuadra dentro de redondeos normales, o hay que mirarlo.
# ---------------------------------------------------------------------
UMBRAL_VERDE = 1.0  # 0 a S/1: cuadrado (redondeos normales); mas de S/1: diferencia

ESTADO_CUADRADO = "✅ Cuadrado"
ESTADO_GRANDE = "🔴 Diferencia"
ESTADO_ABIERTO = "⏳ Apertura sin Cierre"
ESTADO_CIERRE_SUELTO = "⚠️ Cierre sin Apertura"
ESTADO_NOMBRE_DISTINTO = "⚠️ Cerró otro nombre"
ESTADO_SECUENCIA = "⚠️ Revisar secuencia"

# Estados para calcular_saltos() (ver mas abajo): mismo umbral binario,
# etiquetas propias porque ahi no se habla de "cortes" sino de la
# continuidad del fondo entre un Cierre y la Apertura que le sigue.
ESTADO_SALTO_COINCIDE = "✅ Coincide"
ESTADO_SALTO_DIFERENCIA = "🔴 Diferencia"

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
    "observaciones",  # lo que escribieron en Apertura y/o Cierre de este corte
    "id_apertura",  # id del registro de Apertura (para cruzar con la hoja)
    "id_cierre",  # id del registro de Cierre
]

COLUMNAS_RESUMEN = ["n_cortes", "nombres", "diferencia", "diferencia_fmt", "estado", "observaciones"]

COLUMNAS_PERSONA = ["nombre", "n_cortes", "diferencia", "diferencia_fmt", "descuadre_abs"]

COLUMNAS_SALTO_PERSONA = ["nombre", "n_saltos", "diferencia_saltos", "diferencia_saltos_fmt"]

COLUMNAS_SALTO = [
    "local",
    "tipo_salto",  # "Mismo turno" / "Entre turnos" / "Entre días"
    "fecha_cierre",
    "turno_cierre",
    "nombre_cierre",
    "hora_cierre",
    "fecha_apertura",
    "turno_apertura",
    "nombre_apertura",
    "hora_apertura",
    "cierre",
    "apertura",
    "diferencia",
    "diferencia_fmt",
    "estado",
    "id_cierre",  # clave unica del salto: un Cierre solo tiene UNA siguiente Apertura
    "id_apertura",
]


def _semaforo(diferencia: float) -> str:
    d = abs(diferencia)
    if d <= UMBRAL_VERDE:
        return ESTADO_CUADRADO
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

    # Observaciones que se hayan escrito en la Apertura y/o el Cierre de
    # este corte -- util para ver, sin salir de esta tabla, si alguien ya
    # dejo anotado el motivo de una diferencia (p. ej. "retiro para pago
    # de letra").
    obs_apertura = str(apertura.get("observaciones", "") or "").strip() if apertura is not None else ""
    obs_cierre = str(cierre.get("observaciones", "") or "").strip() if cierre is not None else ""
    partes_obs = []
    if obs_apertura:
        partes_obs.append(f"Apertura: {obs_apertura}")
    if obs_cierre:
        partes_obs.append(f"Cierre: {obs_cierre}")
    observaciones = " | ".join(partes_obs)

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
            "observaciones": observaciones,
            "id_apertura": "" if apertura is None else str(apertura.get("id", "")),
            "id_cierre": "" if cierre is None else str(cierre.get("id", "")),
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
        # Observaciones de todos los cortes del turno, sin repetir (cada
        # una ya viene etiquetada "Apertura: ..." / "Cierre: ..." desde
        # _fila_corte).
        observaciones = " | ".join(dict.fromkeys(o for o in grupo["observaciones"].astype(str) if o))
        filas.append(
            {
                **contexto,
                "n_cortes": len(grupo),
                "nombres": nombres,
                "diferencia": diferencia_total,
                "diferencia_fmt": f"{diferencia_total:+,.2f}",
                "estado": _estado_turno(set(grupo["estado"])),
                "observaciones": observaciones,
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


def acumulado_saltos_por_persona(saltos: pd.DataFrame) -> pd.DataFrame:
    """Por cada persona que CERRO un salto (Cierre -> Apertura siguiente,
    ver calcular_saltos): cuantos saltos tuvo y la SUMA de sus
    diferencias, con signo.

    Se atribuye a quien CERRO (no a quien abrio despues) porque el que
    cierra es quien certifica el monto que deberia quedarse igual hasta
    el siguiente conteo -- si no coincide, lo mas comun es que el
    problema este en como se dejo la caja, no en quien la vuelve a
    abrir. Es un acumulado DISTINTO al de acumulado_por_persona() (que
    mide los cortes en si, atribuidos a quien abrio): uno mide lo que
    pasa DENTRO de un corte, este mide el hueco ENTRE cortes.
    """
    if saltos is None or saltos.empty:
        return pd.DataFrame(columns=COLUMNAS_SALTO_PERSONA)

    completos = saltos[saltos["diferencia"].notna() & (saltos["nombre_cierre"].astype(str) != "")]
    if completos.empty:
        return pd.DataFrame(columns=COLUMNAS_SALTO_PERSONA)

    agrupado = (
        completos.groupby("nombre_cierre")
        .agg(n_saltos=("diferencia", "count"), diferencia_saltos=("diferencia", "sum"))
        .reset_index()
        .rename(columns={"nombre_cierre": "nombre"})
    )
    agrupado["diferencia_saltos_fmt"] = agrupado["diferencia_saltos"].apply(lambda x: f"{x:+,.2f}")
    return agrupado[COLUMNAS_SALTO_PERSONA]


def _tipo_salto(fecha_cierre, turno_cierre, fecha_apertura, turno_apertura) -> str:
    if fecha_cierre != fecha_apertura:
        return "Entre días"
    if turno_cierre != turno_apertura:
        return "Entre turnos"
    return "Mismo turno"


def _fila_salto(local: str, cierre: dict, apertura: dict) -> dict:
    ci_total = _total(cierre)
    ap_total = _total(apertura)
    if pd.notna(ci_total) and pd.notna(ap_total):
        diferencia = ap_total - ci_total
        estado = ESTADO_SALTO_COINCIDE if abs(diferencia) <= UMBRAL_VERDE else ESTADO_SALTO_DIFERENCIA
    else:
        diferencia = pd.NA
        estado = ESTADO_SALTO_COINCIDE

    fecha_cierre = cierre.get("fecha")
    turno_cierre = cierre.get("turno")
    fecha_apertura = apertura.get("fecha")
    turno_apertura = apertura.get("turno")

    return {
        "local": local,
        "tipo_salto": _tipo_salto(fecha_cierre, turno_cierre, fecha_apertura, turno_apertura),
        "fecha_cierre": fecha_cierre,
        "turno_cierre": turno_cierre,
        "nombre_cierre": _nombre(cierre),
        "hora_cierre": _hora(cierre),
        "fecha_apertura": fecha_apertura,
        "turno_apertura": turno_apertura,
        "nombre_apertura": _nombre(apertura),
        "hora_apertura": _hora(apertura),
        "cierre": ci_total,
        "apertura": ap_total,
        "diferencia": diferencia,
        "diferencia_fmt": _fmt(diferencia),
        "estado": estado,
        "id_cierre": str(cierre.get("id", "")),
        "id_apertura": str(apertura.get("id", "")),
    }


def calcular_saltos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Recorre TODOS los registros de cada local en orden cronologico y arma
    un "salto" por cada Cierre seguido de una Apertura -- sea el mismo
    turno (corte parcial), el turno siguiente del mismo dia, o el dia
    siguiente. El fondo deberia quedar guardado de un salto a otro; si no
    coincide, alguien movio la caja entre medio (o hubo un error).

    A diferencia de calcular_cortes() (que mide cada corte contra su
    PROPIA Apertura, ignorando a proposito lo que pasa ENTRE cortes),
    esto mide exactamente ese "entre medio" que calcular_cortes() deja
    afuera -- son complementarios, no reemplazan el uno al otro.

    Cada salto se identifica de forma unica por "id_cierre" (un Cierre
    solo puede tener UNA Apertura siguiente), util para que un ajuste
    autorizado apunte a un salto especifico y no a "todo el dia".
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNAS_SALTO)

    faltan = {"local", "tipo", "fecha", "turno", "total", "nombre"} - set(df.columns)
    if faltan:
        raise KeyError(f"Faltan columnas para calcular_saltos: {sorted(faltan)}")

    trabajo = df.copy().reset_index(drop=True)
    if "timestamp" in trabajo.columns:
        trabajo["_ts"] = pd.to_datetime(trabajo["timestamp"], errors="coerce")
    else:
        trabajo["_ts"] = pd.NaT
    trabajo["_orden"] = range(len(trabajo))
    trabajo = trabajo.sort_values(["_ts", "_orden"], kind="stable")

    filas: list[dict] = []
    for local, grupo in trabajo.groupby("local", dropna=False, sort=False):
        registros = grupo.to_dict("records")
        for actual, siguiente in zip(registros, registros[1:]):
            if (
                str(actual.get("tipo", "")).strip() == "Cierre"
                and str(siguiente.get("tipo", "")).strip() == "Apertura"
            ):
                filas.append(_fila_salto(local, actual, siguiente))

    salida = pd.DataFrame(filas, columns=COLUMNAS_SALTO)
    if salida.empty:
        return salida
    for col in ["cierre", "apertura", "diferencia"]:
        salida[col] = pd.to_numeric(salida[col], errors="coerce")
    # fecha_cierre/fecha_apertura se dejan como vienen (objetos date), igual
    # que "fecha" en calcular_cortes(), para poder filtrar por rango de
    # fechas antes de mostrar (arrow_safe() ya las pasa a texto al pintar).
    for col in salida.columns:
        if col in {"cierre", "apertura", "diferencia", "fecha_cierre", "fecha_apertura"}:
            continue
        salida[col] = salida[col].fillna("").astype(str).replace({"nan": "", "None": ""})
    return salida


# ---------------------------------------------------------------------
# Liquidacion por trabajador ("ticket"): junta, por persona, las
# diferencias reales (mas de UMBRAL_VERDE) de sus cortes (atribuidas a
# quien abrio) y de sus entregas de caja (atribuidas a quien cerro).
# Quien llame debe pasar cortes/saltos YA sin lo "Autorizado".
# ---------------------------------------------------------------------
COLUMNAS_TICKET = ["persona", "tipo", "fecha", "local", "detalle", "diferencia", "observaciones"]


def items_ticket(cortes: pd.DataFrame, saltos: pd.DataFrame, incluir_entregas: bool = True) -> pd.DataFrame:
    filas: list[dict] = []

    if cortes is not None and not cortes.empty:
        reales = cortes[
            cortes["diferencia"].notna()
            & (cortes["diferencia"].abs() > UMBRAL_VERDE)
            & (cortes["nombre"].astype(str) != "")
        ]
        for _, r in reales.iterrows():
            filas.append(
                {
                    "persona": r["nombre"],
                    "tipo": "Corte",
                    "fecha": r["fecha"],
                    "local": r["local"],
                    "detalle": f"{r['turno']} · corte {r['corte']} ({r['hora_apertura']}–{r['hora_cierre']})",
                    "diferencia": float(r["diferencia"]),
                    "observaciones": r.get("observaciones", ""),
                }
            )

    if incluir_entregas and saltos is not None and not saltos.empty:
        reales = saltos[
            saltos["diferencia"].notna()
            & (saltos["diferencia"].abs() > UMBRAL_VERDE)
            & (saltos["nombre_cierre"].astype(str) != "")
        ]
        for _, r in reales.iterrows():
            filas.append(
                {
                    "persona": r["nombre_cierre"],
                    "tipo": "Entrega de caja",
                    "fecha": r["fecha_cierre"],
                    "local": r["local"],
                    "detalle": (
                        f"{r['tipo_salto']}: cierre {r['turno_cierre']} {r['hora_cierre']} → "
                        f"apertura {r['fecha_apertura']} {r['hora_apertura']} ({r['nombre_apertura']})"
                    ),
                    "diferencia": float(r["diferencia"]),
                    "observaciones": "",
                }
            )

    salida = pd.DataFrame(filas, columns=COLUMNAS_TICKET)
    if salida.empty:
        return salida
    return salida.sort_values(["persona", "fecha"], kind="stable").reset_index(drop=True)


def totales_ticket(items: pd.DataFrame, compensar: bool = False) -> dict:
    """Faltantes (suma de lo negativo, en positivo), sobrantes, neto y el
    monto 'a revisar': los faltantes tal cual, o -- si compensar -- solo
    lo que quede faltando despues de restar los sobrantes."""
    if items is None or items.empty:
        return {"faltantes": 0.0, "sobrantes": 0.0, "neto": 0.0, "a_revisar": 0.0}
    dif = items["diferencia"]
    faltantes = float(-dif[dif < 0].sum())
    sobrantes = float(dif[dif > 0].sum())
    neto = sobrantes - faltantes
    a_revisar = max(0.0, -neto) if compensar else faltantes
    return {"faltantes": faltantes, "sobrantes": sobrantes, "neto": neto, "a_revisar": a_revisar}


COLUMNAS_CREDITO = ["persona", "fecha", "local", "monto", "motivo"]


def creditos_repuso(ajustes: pd.DataFrame, desde, hasta, locales=None) -> pd.DataFrame:
    """Plata que una persona repuso de su bolsillo (columna 'repuso' del
    ajuste): queda 'a favor' de esa persona. Solo cuentan ajustes con
    monto positivo (un ingreso) dentro del rango y de los locales."""
    if ajustes is None or ajustes.empty or "repuso" not in ajustes.columns:
        return pd.DataFrame(columns=COLUMNAS_CREDITO)
    sel = ajustes[
        (ajustes["repuso"].astype(str) != "")
        & (ajustes["monto"] > 0)
        & (ajustes["fecha"] >= desde)
        & (ajustes["fecha"] <= hasta)
    ]
    if locales is not None:
        sel = sel[sel["local"].isin(locales)]
    if sel.empty:
        return pd.DataFrame(columns=COLUMNAS_CREDITO)
    salida = sel.rename(columns={"repuso": "persona"})[COLUMNAS_CREDITO]
    return salida.sort_values(["persona", "fecha"], kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------------
# Personal: unifica las distintas formas en que alguien firmo su nombre
# ("Ana Machaca", "Ana Maritza Machaca Vilca") y desambigua nombres
# repetidos ("Ana") usando el local donde se hizo el registro.
# ---------------------------------------------------------------------
def _clave_alias(texto) -> str:
    return " ".join(str(texto).split()).lower()


def mapa_alias(personal: pd.DataFrame) -> dict:
    """alias (en minusculas) -> lista de (nombre oficial, local). El alias
    de la hoja Personal va separado por '|'; el nombre oficial tambien
    cuenta como alias de si mismo."""
    mapa: dict = {}
    if personal is None or personal.empty:
        return mapa
    for _, r in personal.iterrows():
        oficial = str(r.get("nombre", "")).strip()
        if not oficial:
            continue
        local = str(r.get("local", "")).strip()
        alias = [a for a in str(r.get("alias", "")).split("|") if a.strip()]
        for a in alias + [oficial]:
            mapa.setdefault(_clave_alias(a), []).append((oficial, local))
    return mapa


def resolver_nombre(nombre, local, mapa: dict) -> str:
    candidatos = mapa.get(_clave_alias(nombre))
    if not candidatos:
        return nombre
    oficiales = list(dict.fromkeys(c[0] for c in candidatos))
    if len(oficiales) == 1:
        return oficiales[0]
    for oficial, loc in candidatos:
        if loc and loc == str(local).strip():
            return oficial
    return nombre


def margen_error(operaciones: float, piso: float, tasa: float, tope: float) -> float:
    """Margen de error tolerado: un piso fijo + una tasa por operacion,
    con un tope. Crece con el trabajo (mas operaciones, mas margen)."""
    return float(min(tope, piso + tasa * max(0.0, float(operaciones))))


def a_descontar(saldo: float, margen: float) -> float:
    """Franquicia: solo se descuenta lo que PASE del margen. Un saldo
    negativo (se le debe a la persona) no se descuenta."""
    return float(max(0.0, saldo - margen))


# ---------------------------------------------------------------------
# Incentivo por encuestas condicionado a la nota del LOCAL en el mes (la
# manda el ejecutivo de BCP al cerrar el mes): si la nota es de
# UMBRAL_NOTA_LOCAL o mas (es el minimo) se paga, si no, no. Mientras no haya nota, queda
# condicionado.
# ---------------------------------------------------------------------
UMBRAL_NOTA_LOCAL = 55


def estado_nota(nota) -> str:
    try:
        n = float(nota)
    except (TypeError, ValueError):
        return "Condicionado"
    if n != n:
        return "Condicionado"
    return "Aprobado" if n >= UMBRAL_NOTA_LOCAL else "No aprobado"


def con_estado_nota(encuestas: pd.DataFrame, notas: pd.DataFrame) -> pd.DataFrame:
    """Agrega 'mes' (YYYY-MM de la encuesta), 'nota_local' y 'estado_nota'
    (Aprobado / No aprobado / Condicionado) a cada encuesta, segun la nota
    cargada para SU local en SU mes."""
    if encuestas is None or encuestas.empty:
        return encuestas
    salida = encuestas.copy()
    salida["mes"] = pd.to_datetime(salida["fecha"], errors="coerce").dt.strftime("%Y-%m")
    mapa = {}
    if notas is not None and not notas.empty:
        mapa = {(r["mes"], r["local"]): r["nota"] for _, r in notas.iterrows()}
    salida["nota_local"] = [mapa.get((m, loc)) for m, loc in zip(salida["mes"], salida["local"])]
    salida["estado_nota"] = salida["nota_local"].map(estado_nota)
    return salida
