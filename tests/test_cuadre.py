"""
Pruebas de cuadre.py (modelo de tramos Apertura -> Cierre).

Como cuadre.py no importa streamlit ni gspread, esto corre sin
credenciales ni conexion:

    pip install -r requirements.txt -r requirements-dev.txt
    pytest
"""

import pandas as pd
import pytest

from cuadre import (
    ESTADO_ABIERTO,
    ESTADO_CIERRE_SUELTO,
    ESTADO_CUADRADO,
    ESTADO_GRANDE,
    ESTADO_NOMBRE_DISTINTO,
    ESTADO_REVISAR,
    ESTADO_SECUENCIA,
    acumulado_por_persona,
    calcular_tramos,
    resumen_turnos,
)

INDICE = ["local", "fecha", "turno"]


class _Reloj:
    """Genera timestamps crecientes para que el orden de los registros
    sea el orden en que se llama a r()."""

    def __init__(self):
        self.minuto = 0

    def __call__(self, tipo, total, nombre, motivo=""):
        self.minuto += 7
        return {
            "local": "Fau",
            "fecha": "2026-09-06",
            "turno": "Mañana",
            "tipo": tipo,
            "total": total,
            "nombre": nombre,
            "timestamp": f"2026-09-06T08:{self.minuto:02d}:00",
            "motivo_cierre_otro_nombre": motivo,
        }


@pytest.fixture
def r():
    return _Reloj()


def _tramos(registros):
    return calcular_tramos(pd.DataFrame(registros), INDICE)


# --- Un solo tramo -------------------------------------------------------

def test_tramo_cuadrado(r):
    t = _tramos([r("Apertura", 5000.00, "Ana"), r("Cierre", 5000.50, "Ana")])
    assert len(t) == 1
    assert t.iloc[0]["estado"] == ESTADO_CUADRADO
    assert t.iloc[0]["diferencia"] == pytest.approx(0.50)
    assert t.iloc[0]["diferencia_fmt"] == "+0.50"
    assert t.iloc[0]["nombre"] == "Ana"


def test_tramo_revisar(r):
    t = _tramos([r("Apertura", 5000.00, "Ana"), r("Cierre", 4970.00, "Ana")])
    assert t.iloc[0]["estado"] == ESTADO_REVISAR


def test_tramo_diferencia_grande(r):
    t = _tramos([r("Apertura", 5000.00, "Ana"), r("Cierre", 4000.00, "Ana")])
    assert t.iloc[0]["estado"] == ESTADO_GRANDE


# --- Cierres parciales (varios tramos) --------------------------------

def test_dos_tramos_retiro_entre_medio_no_es_descuadre(r):
    # Tramo 1: 5000 -> 5000 (cuadra). Entre medio se retiran S/2000 a
    # proposito (la 2da Apertura arranca en 3000). Tramo 2: 3000 -> 3000.
    t = _tramos([
        r("Apertura", 5000.00, "Ana"),
        r("Cierre", 5000.00, "Ana"),
        r("Apertura", 3000.00, "Ana"),
        r("Cierre", 3000.00, "Ana"),
    ])
    assert list(t["tramo"]) == [1, 2]
    assert all(t["estado"] == ESTADO_CUADRADO)
    resumen = resumen_turnos(t, INDICE)
    assert len(resumen) == 1
    assert resumen.iloc[0]["n_tramos"] == 2
    assert resumen.iloc[0]["diferencia"] == pytest.approx(0.0)  # el retiro no cuenta
    assert resumen.iloc[0]["estado"] == ESTADO_CUADRADO


def test_suma_de_tramos_en_resumen(r):
    t = _tramos([
        r("Apertura", 5000.00, "Ana"),
        r("Cierre", 4997.00, "Ana"),   # -3
        r("Apertura", 4997.00, "Ana"),
        r("Cierre", 4990.00, "Ana"),   # -7
    ])
    resumen = resumen_turnos(t, INDICE)
    assert resumen.iloc[0]["diferencia"] == pytest.approx(-10.0)
    assert resumen.iloc[0]["diferencia_fmt"] == "-10.00"


# --- Secuencia rota ---------------------------------------------------

def test_dos_aperturas_seguidas(r):
    t = _tramos([
        r("Apertura", 5000.00, "Ana"),
        r("Apertura", 5100.00, "Beto"),
        r("Cierre", 5100.00, "Beto"),
    ])
    # La 1ra Apertura queda como tramo abierto; la 2da si cierra.
    assert list(t["estado"]) == [ESTADO_ABIERTO, ESTADO_CUADRADO]
    assert resumen_turnos(t, INDICE).iloc[0]["estado"] == ESTADO_SECUENCIA


def test_cierre_sin_apertura(r):
    t = _tramos([r("Cierre", 4000.00, "Ana")])
    assert t.iloc[0]["estado"] == ESTADO_CIERRE_SUELTO
    assert resumen_turnos(t, INDICE).iloc[0]["estado"] == ESTADO_SECUENCIA


def test_turno_sin_cerrar(r):
    t = _tramos([r("Apertura", 5000.00, "Ana")])
    assert t.iloc[0]["estado"] == ESTADO_ABIERTO


# --- Nombre distinto en el Cierre -----------------------------------

def test_cierre_con_otro_nombre_se_marca_y_se_atribuye_a_apertura(r):
    t = _tramos([
        r("Apertura", 5000.00, "Ana"),
        r("Cierre", 4995.00, "Beto", motivo="Ana se retiró"),
    ])
    fila = t.iloc[0]
    assert fila["estado"] == ESTADO_NOMBRE_DISTINTO
    assert fila["nombre"] == "Ana"          # atribucion a quien abrio
    assert fila["nombre_cierre"] == "Beto"
    assert fila["motivo"] == "Ana se retiró"
    assert fila["diferencia"] == pytest.approx(-5.0)  # la diferencia igual cuenta
    # y en el acumulado por persona, el descuadre es de Ana, no de Beto
    acum = acumulado_por_persona(t).set_index("nombre")
    assert "Ana" in acum.index
    assert "Beto" not in acum.index
    assert acum.loc["Ana", "diferencia"] == pytest.approx(-5.0)


# --- Acumulado por persona ------------------------------------------

def test_acumulado_por_persona_suma_y_ordena(r):
    df = pd.DataFrame([
        r("Apertura", 5000.00, "Ana"), r("Cierre", 4996.00, "Ana"),   # Ana -4
        r("Apertura", 4996.00, "Ana"), r("Cierre", 4993.00, "Ana"),   # Ana -3
        r("Apertura", 4993.00, "Beto"), r("Cierre", 4994.00, "Beto"), # Beto +1
    ])
    acum = acumulado_por_persona(calcular_tramos(df, INDICE)).set_index("nombre")
    assert acum.loc["Ana", "n_tramos"] == 2
    assert acum.loc["Ana", "diferencia"] == pytest.approx(-7.0)
    assert acum.loc["Beto", "diferencia"] == pytest.approx(1.0)
    # Ana tiene mas descuadre absoluto -> va primero
    assert list(acumulado_por_persona(calcular_tramos(df, INDICE))["nombre"]) == ["Ana", "Beto"]


# --- Varios locales no se mezclan ---------------------------------

def test_locales_distintos_no_se_mezclan():
    df = pd.DataFrame([
        {"local": "Fau", "fecha": "2026-09-06", "turno": "Mañana", "tipo": "Apertura",
         "total": 5000, "nombre": "Ana", "timestamp": "2026-09-06T08:00:00"},
        {"local": "Fau", "fecha": "2026-09-06", "turno": "Mañana", "tipo": "Cierre",
         "total": 5000, "nombre": "Ana", "timestamp": "2026-09-06T13:00:00"},
        {"local": "Zol", "fecha": "2026-09-06", "turno": "Mañana", "tipo": "Apertura",
         "total": 3000, "nombre": "Beto", "timestamp": "2026-09-06T08:00:00"},
        {"local": "Zol", "fecha": "2026-09-06", "turno": "Mañana", "tipo": "Cierre",
         "total": 2000, "nombre": "Beto", "timestamp": "2026-09-06T13:00:00"},
    ])
    resumen = resumen_turnos(calcular_tramos(df, INDICE), INDICE).set_index("local")
    assert resumen.loc["Fau", "estado"] == ESTADO_CUADRADO
    assert resumen.loc["Zol", "estado"] == ESTADO_GRANDE


# --- Vacio ---------------------------------------------------------

def test_df_vacio():
    t = calcular_tramos(pd.DataFrame(), INDICE)
    assert t.empty
    assert "estado" in t.columns
    assert resumen_turnos(t, INDICE).empty
    assert acumulado_por_persona(t).empty
