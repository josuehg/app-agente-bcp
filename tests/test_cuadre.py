"""
Pruebas de cuadre.py (modelo de cortes Apertura -> Cierre).

Como cuadre.py no importa streamlit ni gspread, esto corre sin
credenciales ni conexion:

    pip install -r requirements.txt -r requirements-dev.txt
    pytest
"""

import pandas as pd
import pytest

from cuadre import (
    politica_sucursal,
    estado_visible,
    estado_parcial,
    nota_vigente,
    con_estado_nota,
    estado_nota,
    a_descontar,
    margen_error,
    mapa_alias,
    resolver_nombre,
    creditos_repuso,
    items_ticket,
    totales_ticket,
    ESTADO_ABIERTO,
    ESTADO_CIERRE_SUELTO,
    ESTADO_CUADRADO,
    ESTADO_GRANDE,
    ESTADO_NOMBRE_DISTINTO,
    ESTADO_SALTO_COINCIDE,
    ESTADO_SALTO_DIFERENCIA,
    ESTADO_SECUENCIA,
    acumulado_por_persona,
    acumulado_saltos_por_persona,
    calcular_cortes,
    calcular_saltos,
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


def _cortes(registros):
    return calcular_cortes(pd.DataFrame(registros), INDICE)


# --- Un solo corte -------------------------------------------------------

def test_corte_cuadrado(r):
    t = _cortes([r("Apertura", 5000.00, "Ana"), r("Cierre", 5000.50, "Ana")])
    assert len(t) == 1
    assert t.iloc[0]["estado"] == ESTADO_CUADRADO
    assert t.iloc[0]["diferencia"] == pytest.approx(0.50)
    assert t.iloc[0]["diferencia_fmt"] == "+0.50"
    assert t.iloc[0]["nombre"] == "Ana"


def test_corte_diferencia_grande(r):
    t = _cortes([r("Apertura", 5000.00, "Ana"), r("Cierre", 4000.00, "Ana")])
    assert t.iloc[0]["estado"] == ESTADO_GRANDE


def test_corte_umbral_binario_sin_estado_intermedio(r):
    # Semaforo binario: hasta S/1 de diferencia cuadra (redondeos), mas de
    # S/1 ya es "Diferencia" -- no hay un tercer estado intermedio.
    t_borde = _cortes([r("Apertura", 5000.00, "Ana"), r("Cierre", 5001.00, "Ana")])
    assert t_borde.iloc[0]["estado"] == ESTADO_CUADRADO

    t_pasado = _cortes([r("Apertura", 5000.00, "Ana"), r("Cierre", 4970.00, "Ana")])
    assert t_pasado.iloc[0]["estado"] == ESTADO_GRANDE


# --- Cierres parciales (varios cortes) --------------------------------

def test_dos_cortes_retiro_entre_medio_no_es_descuadre(r):
    # Corte 1: 5000 -> 5000 (cuadra). Entre medio se retiran S/2000 a
    # proposito (la 2da Apertura arranca en 3000). Corte 2: 3000 -> 3000.
    t = _cortes([
        r("Apertura", 5000.00, "Ana"),
        r("Cierre", 5000.00, "Ana"),
        r("Apertura", 3000.00, "Ana"),
        r("Cierre", 3000.00, "Ana"),
    ])
    assert list(t["corte"]) == [1, 2]
    assert all(t["estado"] == ESTADO_CUADRADO)
    resumen = resumen_turnos(t, INDICE)
    assert len(resumen) == 1
    assert resumen.iloc[0]["n_cortes"] == 2
    assert resumen.iloc[0]["nombres"] == "Ana"  # misma persona los 2 cortes, sin repetir
    assert resumen.iloc[0]["diferencia"] == pytest.approx(0.0)  # el retiro no cuenta
    assert resumen.iloc[0]["estado"] == ESTADO_CUADRADO


def test_suma_de_cortes_en_resumen(r):
    t = _cortes([
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
    t = _cortes([
        r("Apertura", 5000.00, "Ana"),
        r("Apertura", 5100.00, "Beto"),
        r("Cierre", 5100.00, "Beto"),
    ])
    # La 1ra Apertura queda como corte abierto; la 2da si cierra.
    assert list(t["estado"]) == [ESTADO_ABIERTO, ESTADO_CUADRADO]
    assert resumen_turnos(t, INDICE).iloc[0]["estado"] == ESTADO_SECUENCIA


def test_cierre_sin_apertura(r):
    t = _cortes([r("Cierre", 4000.00, "Ana")])
    assert t.iloc[0]["estado"] == ESTADO_CIERRE_SUELTO
    assert resumen_turnos(t, INDICE).iloc[0]["estado"] == ESTADO_SECUENCIA


def test_turno_sin_cerrar(r):
    t = _cortes([r("Apertura", 5000.00, "Ana")])
    assert t.iloc[0]["estado"] == ESTADO_ABIERTO


# --- Nombre distinto en el Cierre -----------------------------------

def test_cierre_con_otro_nombre_se_marca_y_se_atribuye_a_apertura(r):
    t = _cortes([
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
    acum = acumulado_por_persona(calcular_cortes(df, INDICE)).set_index("nombre")
    assert acum.loc["Ana", "n_cortes"] == 2
    assert acum.loc["Ana", "diferencia"] == pytest.approx(-7.0)
    assert acum.loc["Beto", "diferencia"] == pytest.approx(1.0)
    # Ana tiene mas descuadre absoluto -> va primero
    assert list(acumulado_por_persona(calcular_cortes(df, INDICE))["nombre"]) == ["Ana", "Beto"]


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
    resumen = resumen_turnos(calcular_cortes(df, INDICE), INDICE).set_index("local")
    assert resumen.loc["Fau", "estado"] == ESTADO_CUADRADO
    assert resumen.loc["Zol", "estado"] == ESTADO_GRANDE


# --- Vacio ---------------------------------------------------------

def test_df_vacio():
    t = calcular_cortes(pd.DataFrame(), INDICE)
    assert t.empty
    assert "estado" in t.columns
    assert resumen_turnos(t, INDICE).empty
    assert acumulado_por_persona(t).empty


# --- calcular_saltos: continuidad Cierre -> siguiente Apertura --------
# (a diferencia de calcular_cortes, que mide cada corte contra su propia
# Apertura y a proposito ignora lo que pasa ENTRE cortes, calcular_saltos
# mide justo ese "entre medio": cualquier salto Cierre -> Apertura, sea
# dentro del mismo turno, entre turnos del mismo dia, o entre dias.)

def _reg(local, fecha, turno, tipo, total, nombre, ts):
    return {
        "local": local, "fecha": fecha, "turno": turno, "tipo": tipo,
        "total": total, "nombre": nombre, "timestamp": ts,
    }


def test_salto_mismo_turno_coincide():
    df = pd.DataFrame([
        _reg("Fau", "2026-09-06", "Mañana", "Apertura", 5000, "Ana", "2026-09-06T08:00:00"),
        _reg("Fau", "2026-09-06", "Mañana", "Cierre", 5000, "Ana", "2026-09-06T12:00:00"),
        _reg("Fau", "2026-09-06", "Mañana", "Apertura", 5000, "Ana", "2026-09-06T12:05:00"),
        _reg("Fau", "2026-09-06", "Mañana", "Cierre", 5000, "Ana", "2026-09-06T15:00:00"),
    ])
    saltos = calcular_saltos(df)
    assert len(saltos) == 1
    assert saltos.iloc[0]["tipo_salto"] == "Mismo turno"
    assert saltos.iloc[0]["estado"] == ESTADO_SALTO_COINCIDE
    assert saltos.iloc[0]["diferencia"] == pytest.approx(0.0)


def test_salto_entre_turnos_mismo_dia_con_diferencia():
    df = pd.DataFrame([
        _reg("Fau", "2026-09-06", "Mañana", "Apertura", 5000, "Ana", "2026-09-06T08:00:00"),
        _reg("Fau", "2026-09-06", "Mañana", "Cierre", 5000, "Ana", "2026-09-06T13:00:00"),
        _reg("Fau", "2026-09-06", "Tarde", "Apertura", 4700, "Beto", "2026-09-06T14:00:00"),
        _reg("Fau", "2026-09-06", "Tarde", "Cierre", 4700, "Beto", "2026-09-06T20:00:00"),
    ])
    saltos = calcular_saltos(df)
    assert len(saltos) == 1
    fila = saltos.iloc[0]
    assert fila["tipo_salto"] == "Entre turnos"
    assert fila["diferencia"] == pytest.approx(-300.0)
    assert fila["estado"] == ESTADO_SALTO_DIFERENCIA
    # Sin columna "id" en los registros de prueba -> queda vacio, no falla.
    assert fila["id_cierre"] == ""


def test_salto_entre_dias():
    df = pd.DataFrame([
        _reg("Fau", "2026-09-06", "Tarde", "Apertura", 5000, "Ana", "2026-09-06T14:00:00"),
        _reg("Fau", "2026-09-06", "Tarde", "Cierre", 5000, "Ana", "2026-09-06T20:00:00"),
        _reg("Fau", "2026-09-07", "Mañana", "Apertura", 5000, "Beto", "2026-09-07T08:00:00"),
        _reg("Fau", "2026-09-07", "Mañana", "Cierre", 5000, "Beto", "2026-09-07T13:00:00"),
    ])
    saltos = calcular_saltos(df)
    assert len(saltos) == 1
    assert saltos.iloc[0]["tipo_salto"] == "Entre días"
    assert saltos.iloc[0]["estado"] == ESTADO_SALTO_COINCIDE


def test_saltos_no_se_mezclan_entre_locales():
    df = pd.DataFrame([
        _reg("Fau", "2026-09-06", "Mañana", "Cierre", 5000, "Ana", "2026-09-06T13:00:00"),
        _reg("Zol", "2026-09-06", "Mañana", "Apertura", 3000, "Beto", "2026-09-06T08:05:00"),
    ])
    # Un Cierre de "Fau" no debe emparejarse con una Apertura de "Zol".
    assert calcular_saltos(df).empty


def test_saltos_df_vacio():
    saltos = calcular_saltos(pd.DataFrame())
    assert saltos.empty
    assert "estado" in saltos.columns


# --- acumulado_saltos_por_persona: se atribuye a quien CIERRA ---------

def test_acumulado_saltos_se_atribuye_a_quien_cierra():
    df = pd.DataFrame([
        _reg("Fau", "2026-09-06", "Mañana", "Apertura", 5000, "Ana", "2026-09-06T08:00:00"),
        _reg("Fau", "2026-09-06", "Mañana", "Cierre", 5000, "Ana", "2026-09-06T13:00:00"),
        _reg("Fau", "2026-09-06", "Tarde", "Apertura", 4970, "Beto", "2026-09-06T14:00:00"),
        _reg("Fau", "2026-09-06", "Tarde", "Cierre", 4970, "Beto", "2026-09-06T20:00:00"),
        _reg("Fau", "2026-09-07", "Mañana", "Apertura", 4960, "Ana", "2026-09-07T08:00:00"),
        _reg("Fau", "2026-09-07", "Mañana", "Cierre", 4960, "Ana", "2026-09-07T13:00:00"),
    ])
    saltos = calcular_saltos(df)
    acum = acumulado_saltos_por_persona(saltos).set_index("nombre")
    # Salto 1 (Ana cierra Mañana -> Beto abre Tarde): -30, se le atribuye a Ana.
    # Salto 2 (Beto cierra Tarde -> Ana abre Mañana siguiente): -10, se le atribuye a Beto.
    assert acum.loc["Ana", "diferencia_saltos"] == pytest.approx(-30.0)
    assert acum.loc["Beto", "diferencia_saltos"] == pytest.approx(-10.0)
    assert acum.loc["Ana", "n_saltos"] == 1
    assert acum.loc["Beto", "n_saltos"] == 1


def test_acumulado_saltos_vacio():
    assert acumulado_saltos_por_persona(pd.DataFrame()).empty


def test_ticket_ignora_redondeo_y_separa_cortes_de_entregas():
    cortes = pd.DataFrame(
        [
            {"nombre": "Ana", "fecha": "2026-09-10", "local": "L1", "turno": "Mañana", "corte": 1,
             "hora_apertura": "08:00", "hora_cierre": "14:00", "diferencia": -20.0, "observaciones": "x"},
            {"nombre": "Ana", "fecha": "2026-09-11", "local": "L1", "turno": "Mañana", "corte": 1,
             "hora_apertura": "08:00", "hora_cierre": "14:00", "diferencia": -0.5, "observaciones": ""},
        ]
    )
    saltos = pd.DataFrame(
        [
            {"nombre_cierre": "Ana", "fecha_cierre": "2026-09-10", "local": "L1", "tipo_salto": "Entre días",
             "turno_cierre": "Tarde", "hora_cierre": "20:00", "fecha_apertura": "2026-09-11",
             "hora_apertura": "07:50", "nombre_apertura": "Beto", "diferencia": 5.0},
        ]
    )
    items = items_ticket(cortes, saltos)
    assert list(items["tipo"]) == ["Corte", "Entrega de caja"]
    assert totales_ticket(items) == {"faltantes": 20.0, "sobrantes": 5.0, "neto": -15.0, "a_revisar": 20.0}
    assert totales_ticket(items, compensar=True)["a_revisar"] == 15.0
    assert len(items_ticket(cortes, saltos, incluir_entregas=False)) == 1


def test_ticket_vacio():
    items = items_ticket(None, None)
    assert items.empty
    assert totales_ticket(items)["a_revisar"] == 0.0


def test_creditos_repuso_solo_ingresos_con_repuso_en_rango():
    from datetime import date

    aj = pd.DataFrame(
        [
            {"local": "L1", "fecha": date(2026, 9, 30), "monto": 10.0, "motivo": "repuso", "repuso": "María"},
            {"local": "L1", "fecha": date(2026, 9, 30), "monto": -10.0, "motivo": "retiro", "repuso": "María"},
            {"local": "L1", "fecha": date(2026, 9, 30), "monto": 5.0, "motivo": "otro", "repuso": ""},
            {"local": "L2", "fecha": date(2026, 9, 30), "monto": 7.0, "motivo": "x", "repuso": "Ana"},
            {"local": "L1", "fecha": date(2026, 8, 1), "monto": 9.0, "motivo": "viejo", "repuso": "Ana"},
        ]
    )
    c = creditos_repuso(aj, date(2026, 9, 1), date(2026, 10, 2), ["L1"])
    assert list(c["persona"]) == ["María"] and list(c["monto"]) == [10.0]
    assert creditos_repuso(None, date(2026, 9, 1), date(2026, 10, 2)).empty


def test_alias_une_variantes_y_desambigua_por_local():
    personal = pd.DataFrame(
        [
            {"nombre": "Ana León", "local": "Colon", "alias": "Ana"},
            {"nombre": "Ana Machaca", "local": "Fer213", "alias": "Ana|Ana Maritza Machaca Vilca"},
        ]
    )
    m = mapa_alias(personal)
    assert resolver_nombre("Ana Maritza Machaca Vilca", "Colon", m) == "Ana Machaca"
    assert resolver_nombre("ana machaca", "Colon", m) == "Ana Machaca"
    assert resolver_nombre("Ana", "Colon", m) == "Ana León"
    assert resolver_nombre("Ana", "Fer213", m) == "Ana Machaca"
    assert resolver_nombre("Ana", "OtroLocal", m) == "Ana"
    assert resolver_nombre("Pedro", "Colon", m) == "Pedro"
    assert resolver_nombre("Ana", "Colon", mapa_alias(None)) == "Ana"


def test_margen_y_franquicia():
    assert margen_error(0, 5, 0.01, 30) == 5
    assert margen_error(700, 5, 0.01, 30) == 12
    assert margen_error(5000, 5, 0.01, 30) == 30
    assert a_descontar(21.5, 30) == 0
    assert a_descontar(48.01, 9.32) == pytest.approx(38.69)
    assert a_descontar(-5, 10) == 0


def test_incentivo_condicionado_a_nota_del_local():
    from datetime import date

    assert estado_nota(61) == "Aprobado"
    assert estado_nota(55) == "Aprobado"
    assert estado_nota(54.9) == "No aprobado"
    assert estado_nota(45.5) == "No aprobado"
    assert estado_nota(None) == "Condicionado"
    assert estado_nota(float("nan")) == "Condicionado"

    enc = pd.DataFrame(
        [
            {"fecha": date(2026, 9, 5), "local": "L1", "nombre": "Ana", "incentivo": 10.0},
            {"fecha": date(2026, 9, 6), "local": "L2", "nombre": "Ana", "incentivo": 10.0},
            {"fecha": date(2026, 10, 1), "local": "L1", "nombre": "Ana", "incentivo": 10.0},
        ]
    )
    notas = pd.DataFrame([{"mes": "2026-09", "local": "L1", "nota": 75.0}, {"mes": "2026-09", "local": "L2", "nota": 54.0}])
    r = con_estado_nota(enc, notas)
    assert list(r["estado_nota"]) == ["Aprobado", "No aprobado", "Condicionado"]
    assert con_estado_nota(None, notas) is None


def test_nota_parcial_informa_pero_no_habilita_pagos():
    from datetime import date

    enc = pd.DataFrame([{"fecha": date(2026, 10, 5), "local": "L1", "nombre": "Ana", "incentivo": 10.0}])
    notas = pd.DataFrame(
        [
            {"mes": "2026-09", "local": "L1", "nota": 72.0, "tipo": "Final", "fecha_corte": ""},
            {"mes": "2026-10", "local": "L1", "nota": 80.0, "tipo": "Parcial", "fecha_corte": "15/10"},
        ]
    )
    # la parcial de octubre NO aprueba el pago de octubre
    assert list(con_estado_nota(enc, notas)["estado_nota"]) == ["Condicionado"]

    vigente, ultimo_final = nota_vigente(notas, "L1")
    assert vigente["tipo"] == "Parcial" and vigente["mes"] == "2026-10"
    assert ultimo_final["mes"] == "2026-09"

    con_final = pd.concat(
        [notas, pd.DataFrame([{"mes": "2026-10", "local": "L1", "nota": 50.0, "tipo": "Final", "fecha_corte": ""}])]
    )
    assert nota_vigente(con_final, "L1")[0]["tipo"] == "Final"
    assert list(con_estado_nota(enc, con_final)["estado_nota"]) == ["No aprobado"]
    assert nota_vigente(notas, "otro") == (None, None)
    assert estado_parcial(55) == "En camino" and estado_parcial(54) == "En riesgo"


def test_estado_visible_de_la_encuesta():
    assert estado_visible("Pendiente", "Aprobado") == "Pendiente de pago"
    assert estado_visible("Pendiente", "Condicionado") == "En espera de la nota del banco"
    assert estado_visible("Pendiente", "No aprobado") == "No se abona (calificación baja)"
    assert estado_visible("Pagada", "No aprobado") == "Pagada"
    assert estado_visible("No válido", "Aprobado") == "No válido"


def test_politica_sucursal_pool_franquicia_tope_y_reparto():
    items = pd.DataFrame(
        [
            {"persona": "A", "tipo": "Corte", "fecha": "d", "local": "L1", "detalle": "", "diferencia": -30.0, "observaciones": ""},
            {"persona": "B", "tipo": "Corte", "fecha": "d", "local": "L1", "detalle": "", "diferencia": -20.0, "observaciones": ""},
            {"persona": "C", "tipo": "Corte", "fecha": "d", "local": "L1", "detalle": "", "diferencia": 10.0, "observaciones": ""},
            {"persona": "A", "tipo": "Corte", "fecha": "d", "local": "L1", "detalle": "", "diferencia": -100.0, "observaciones": ""},
        ]
    )
    suc, rep, grandes = politica_sucursal(items, {"L1": 2000, "L2": 500}, tasa=0.01, piso=0, tope=30, grande=40)
    l1 = suc[suc["local"] == "L1"].iloc[0]
    assert (l1["faltantes"], l1["sobrantes"], l1["perdida_neta"]) == (50.0, 10.0, 40.0)
    assert l1["franquicia"] == 20.0 and l1["descuento"] == 20.0
    assert l1["items_grandes"] == 1 and l1["monto_grandes"] == -100.0
    a = rep[(rep["local"] == "L1") & (rep["persona"] == "A")].iloc[0]
    b = rep[(rep["local"] == "L1") & (rep["persona"] == "B")].iloc[0]
    assert round(a["asignado"], 2) == 12.0 and round(b["asignado"], 2) == 8.0
    assert "C" not in set(rep["persona"])  # el sobrante no paga
    assert suc[suc["local"] == "L2"].iloc[0]["descuento"] == 0.0
    # el tope manda
    suc2, _, _ = politica_sucursal(items, {"L1": 0}, tasa=0, piso=0, tope=15, grande=40)
    assert suc2.iloc[0]["descuento"] == 15.0
    vacio = politica_sucursal(None, {}, 0.01, 0, 30, 40)
    assert vacio[0].empty and vacio[1].empty
