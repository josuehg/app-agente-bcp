"""
Pruebas de cuadre.calcular_cuadre_turnos.

Como cuadre.py no importa streamlit ni gspread, esto corre sin
credenciales ni conexion:

    pip install -r requirements-dev.txt
    pytest
"""

import pandas as pd
import pytest

from cuadre import calcular_cuadre_turnos

INDICE = ["local", "fecha", "turno"]


def _registro(local, fecha, turno, tipo, total):
    return {"local": local, "fecha": fecha, "turno": turno, "tipo": tipo, "total": total}


def _cuadre(registros):
    """Corre el cuadre sobre una lista de dicts y devuelve la unica fila
    resultante como Series (los tests de abajo usan un solo turno)."""
    df = pd.DataFrame(registros)
    resultado = calcular_cuadre_turnos(df, INDICE)
    assert len(resultado) == 1
    return resultado.iloc[0]


def test_turno_cuadrado():
    fila = _cuadre([
        _registro("Local 1", "2026-09-09", "Mañana", "Apertura", 5000.00),
        _registro("Local 1", "2026-09-09", "Mañana", "Cierre", 5000.50),
    ])
    assert fila["estado"] == "✅ Cuadrado"
    assert fila["diferencia"] == pytest.approx(0.50)
    assert fila["diferencia_fmt"] == "+0.50"


def test_falta_cierre():
    fila = _cuadre([
        _registro("Local 1", "2026-09-09", "Tarde", "Apertura", 5000.00),
    ])
    assert fila["estado"] == "⏳ Falta Cierre"
    assert fila["diferencia_fmt"] == ""


def test_falta_apertura():
    fila = _cuadre([
        _registro("Local 1", "2026-09-09", "Tarde", "Cierre", 4800.00),
    ])
    assert fila["estado"] == "⏳ Falta Apertura"


def test_diferencia_media_revisar():
    fila = _cuadre([
        _registro("Local 2", "2026-09-09", "Mañana", "Apertura", 5000.00),
        _registro("Local 2", "2026-09-09", "Mañana", "Cierre", 4970.00),
    ])
    assert fila["estado"] == "🟡 Revisar"
    assert fila["diferencia"] == pytest.approx(-30.00)
    assert fila["diferencia_fmt"] == "-30.00"


def test_diferencia_grande():
    fila = _cuadre([
        _registro("Local 2", "2026-09-09", "Tarde", "Apertura", 5000.00),
        _registro("Local 2", "2026-09-09", "Tarde", "Cierre", 4000.00),
    ])
    assert fila["estado"] == "🔴 Diferencia grande"


def test_registros_duplicados():
    # Dos Aperturas del mismo turno: alguien registro dos veces.
    fila = _cuadre([
        _registro("Local 3", "2026-09-09", "Mañana", "Apertura", 5000.00),
        _registro("Local 3", "2026-09-09", "Mañana", "Apertura", 5200.00),
        _registro("Local 3", "2026-09-09", "Mañana", "Cierre", 5010.00),
    ])
    assert fila["estado"] == "⚠️ Registros duplicados"


def test_varios_turnos_y_locales_no_se_mezclan():
    df = pd.DataFrame([
        _registro("Local 1", "2026-09-09", "Mañana", "Apertura", 5000.00),
        _registro("Local 1", "2026-09-09", "Mañana", "Cierre", 5000.00),
        _registro("Local 2", "2026-09-09", "Mañana", "Apertura", 3000.00),
        _registro("Local 2", "2026-09-09", "Mañana", "Cierre", 2000.00),
    ])
    resultado = calcular_cuadre_turnos(df, INDICE).set_index("local")
    assert resultado.loc["Local 1", "estado"] == "✅ Cuadrado"
    assert resultado.loc["Local 2", "estado"] == "🔴 Diferencia grande"


def test_df_vacio_devuelve_columnas_esperadas():
    resultado = calcular_cuadre_turnos(pd.DataFrame(), INDICE)
    assert resultado.empty
    for col in INDICE + ["Apertura", "Cierre", "diferencia", "diferencia_fmt", "estado"]:
        assert col in resultado.c
