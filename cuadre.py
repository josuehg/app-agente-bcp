"""
cuadre.py
----------
Logica del "cuadre por turno": emparejar la Apertura y el Cierre de un
mismo turno y calcular cuanto vario el fondo entre una y otra.

Vive en su propio archivo (y NO importa streamlit ni gspread) por dos
razones:

1. Es la unica logica de calculo "de negocio" de la app -- todo lo demas
   es leer/escribir en Google o dibujar pantallas. Tenerla aislada la
   hace facil de entender y de probar.
2. Al no depender de streamlit ni de Google, se puede probar con pytest
   sin credenciales ni conexion (ver tests/test_cuadre.py).

sheets_utils.py la re-exporta, asi que el resto de la app la sigue usando
como antes: sh.calcular_cuadre_turnos(...).
"""

from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------
# UMBRALES DEL SEMAFORO: son un punto de partida razonable, no una regla
# fija -- si en la practica resultan muy estrictos o muy sueltos, se
# ajustan aca no mas (los dos numeros de abajo).
# ---------------------------------------------------------------------
UMBRAL_VERDE = 1.0  # diferencia hasta este monto (0 a S/1): se considera cuadrado (redondeos normales)
UMBRAL_AMARILLO = 50.0  # entre el umbral verde y este (S/1 a S/50): revisar; mas de S/50: diferencia grande

# Columnas que siempre devuelve calcular_cuadre_turnos (ademas de las de
# `columnas_indice`), en este orden.
COLUMNAS_RESULTADO = ["Apertura", "Cierre", "diferencia", "diferencia_fmt", "estado"]


def calcular_cuadre_turnos(df: pd.DataFrame, columnas_indice: list[str]) -> pd.DataFrame:
    """
    Empareja la Apertura y el Cierre de un mismo turno (agrupando por
    `columnas_indice`, por ejemplo ["fecha","turno"] o
    ["local","fecha","turno"]) y calcula:

    - diferencia: Cierre - Apertura, CON signo. Positivo = sobro dinero
      (el Cierre quedo por encima de la Apertura); negativo = falto
      dinero (el Cierre quedo por debajo).
    - diferencia_fmt: lo mismo pero como texto con signo explicito
      ("+0.80" / "-0.80"), para que se lea de un vistazo sin tener que
      fijarse si hay un "-" chiquito antes del numero.
    - estado: semaforo segun que tan grande es la diferencia (en valor
      absoluto); o un aviso si falta la Apertura o el Cierre del turno;
      o "⚠️ Registros duplicados" si ese turno tiene mas de una Apertura
      o mas de un Cierre (alguien registro dos veces) -- en ese caso los
      montos de arriba toman uno cualquiera de los duplicados, asi que
      hay que ir a la hoja y borrar el registro repetido.
    """
    columnas_salida = list(columnas_indice) + COLUMNAS_RESULTADO
    if df.empty:
        return pd.DataFrame(columns=columnas_salida)

    # Monto de cada tipo (si hay duplicados, "first" toma uno cualquiera).
    montos = df.pivot_table(
        index=columnas_indice, columns="tipo", values="total", aggfunc="first"
    )
    # Cuantos registros hay de cada tipo, para detectar duplicados.
    conteos = df.pivot_table(
        index=columnas_indice, columns="tipo", values="total", aggfunc="count"
    )

    for columna_tipo in ["Apertura", "Cierre"]:
        if columna_tipo not in montos.columns:
            montos[columna_tipo] = pd.NA
        if columna_tipo not in conteos.columns:
            conteos[columna_tipo] = 0

    conteos = conteos.rename(
        columns={"Apertura": "_n_apertura", "Cierre": "_n_cierre"}
    )
    pivot = montos.join(conteos[["_n_apertura", "_n_cierre"]]).reset_index()
    pivot["_n_apertura"] = pivot["_n_apertura"].fillna(0).astype(int)
    pivot["_n_cierre"] = pivot["_n_cierre"].fillna(0).astype(int)

    pivot["diferencia"] = pivot["Cierre"] - pivot["Apertura"]
    pivot["diferencia_fmt"] = pivot["diferencia"].apply(
        lambda x: f"{x:+,.2f}" if pd.notna(x) else ""
    )

    def _estado(fila):
        if fila["_n_apertura"] > 1 or fila["_n_cierre"] > 1:
            return "⚠️ Registros duplicados"
        if pd.isna(fila["Apertura"]):
            return "⏳ Falta Apertura"
        if pd.isna(fila["Cierre"]):
            return "⏳ Falta Cierre"
        dif_abs = abs(fila["diferencia"])
        if dif_abs <= UMBRAL_VERDE:
            return "✅ Cuadrado"
        if dif_abs <= UMBRAL_AMARILLO:
            return "🟡 Revisar"
        return "🔴 Diferencia grande"

    pivot["estado"] = pivot.apply(_estado, axis=1)
    return pivot[columnas_salida]
