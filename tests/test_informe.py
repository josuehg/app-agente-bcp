"""
Pruebas de informe_diferencias.py (reporte en PDF de las diferencias rojas de todas las sucursales).
No necesitan credenciales ni conexion.
"""

import datetime as dt

import pandas as pd
import pytest

import informe_diferencias as inf

HOY = dt.date(2026, 10, 18)


def _r(id_, local, fecha, turno, tipo, nombre, hora, efectivo, tarjeta, ops=None, obs=""):
    return {
        "id": id_, "local": local, "fecha": dt.date.fromisoformat(fecha), "turno": turno, "tipo": tipo,
        "nombre": nombre, "timestamp": pd.Timestamp(f"{fecha} {hora}"), "efectivo": float(efectivo),
        "tarjeta": float(tarjeta), "total": float(efectivo + tarjeta),
        "num_operaciones": float(ops) if ops is not None else float("nan"),
        "observaciones": obs, "motivo_cierre_otro_nombre": "",
    }


def _registros():
    # Colon: 17/10 Tarde corte -20 (Pedro) · entre el Cierre del 17 y la Apertura del 18: -80 · 18/10 Mañana corte +3 (Ana)
    # Zol A: todo cuadra
    return pd.DataFrame([
        _r("a1", "Colon", "2026-10-17", "Tarde", "Apertura", "Pedro", "14:30", 3000, 500),
        _r("c1", "Colon", "2026-10-17", "Tarde", "Cierre", "Pedro", "21:00", 2980, 500, ops=30, obs="faltaron 20"),
        _r("a2", "Colon", "2026-10-18", "Mañana", "Apertura", "Ana", "08:00", 2900, 500),
        _r("c2", "Colon", "2026-10-18", "Mañana", "Cierre", "Ana", "14:00", 2903, 500, ops=40),
        _r("a3", "Zol A", "2026-10-18", "Mañana", "Apertura", "Luis", "08:00", 5000, 1000),
        _r("c3", "Zol A", "2026-10-18", "Mañana", "Cierre", "Luis", "14:00", 5000, 1000, ops=40),
    ])


def _ajustes(*filas):
    cols = ["id", "local", "fecha", "monto", "motivo", "autorizado_por", "turno", "salto_id_cierre", "repuso"]
    return pd.DataFrame([dict(zip(cols, f)) for f in filas], columns=cols)


AJ_CORTE = ("x1", "Colon", dt.date(2026, 10, 17), -20.0, "retiro para depósito", "Josué", "Tarde", "", "")
AJ_ENTREGA = ("x2", "Colon", dt.date(2026, 10, 17), -80.0, "entregó a caja chica", "Josué", "", "c1", "")


def test_periodos_mes_actual_anterior_y_rango():
    assert inf.periodo_informe("mes", dt.date(2026, 11, 23)) == (dt.date(2026, 11, 1), dt.date(2026, 11, 23), "Mes actual · noviembre 2026")
    assert inf.periodo_informe("anterior", dt.date(2026, 11, 23)) == (dt.date(2026, 10, 1), dt.date(2026, 10, 31), "Mes anterior · octubre 2026")
    # enero mira a diciembre del anio anterior
    d, h, t = inf.periodo_informe("anterior", dt.date(2027, 1, 5))
    assert (d, h) == (dt.date(2026, 12, 1), dt.date(2026, 12, 31)) and "diciembre 2026" in t
    d, h, t = inf.periodo_informe("rango", dt.date(2026, 11, 23), (dt.date(2026, 11, 3), dt.date(2026, 11, 9)))
    assert (d, h) == (dt.date(2026, 11, 3), dt.date(2026, 11, 9)) and "03/11/2026 al 09/11/2026" in t
    assert inf.periodo_informe("rango", dt.date(2026, 11, 23), None)[0] == dt.date(2026, 11, 1)  # sin rango: el mes actual


def test_diferencias_rojas_son_los_cortes_y_entregas_de_mas_de_un_sol():
    r = inf.diferencias_rojas(_registros(), None, None, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert len(r) == 3 and set(r["local"]) == {"Colon"}  # Zol A cuadra: no sale
    assert list(r["tipo"]) == ["Corte", "Entrega de caja", "Corte"]
    assert list(r["diferencia"]) == [-20.0, -80.0, 3.0]
    assert set(r["estado"]) == {"Pendiente"}
    # al corte lo carga quien abrio; a la entrega, quien cerro
    assert list(r["cargo_a"]) == ["Pedro", "Pedro", "Ana"]
    assert r.loc[1, "persona"] == "Pedro → Ana"
    assert list(r["conformidad"]) == ["Sin registrar"] * 3
    assert r.loc[0, "observaciones"] == "Cierre: faltaron 20"


def test_las_observaciones_de_relleno_se_quitan():
    assert inf._limpiar_observaciones("Apertura: Ninguna | Cierre: Ninguna") == ""
    assert inf._limpiar_observaciones("Apertura: Sin novedad. | Cierre: faltaron 20") == "Cierre: faltaron 20"
    assert inf._limpiar_observaciones("Cierre: retiro para pagar la letra") == "Cierre: retiro para pagar la letra"
    assert inf._limpiar_observaciones(None) == ""


def test_el_periodo_recorta_por_la_fecha_del_turno_y_del_cierre():
    r = inf.diferencias_rojas(_registros(), None, None, dt.date(2026, 10, 18), dt.date(2026, 10, 31))
    assert list(r["diferencia"]) == [3.0]  # la entrega se mide por la fecha del Cierre (17/10): queda fuera
    assert inf.diferencias_rojas(_registros(), None, None, dt.date(2026, 9, 1), dt.date(2026, 9, 30)).empty
    assert inf.diferencias_rojas(pd.DataFrame(), None, None, dt.date(2026, 9, 1), dt.date(2026, 9, 30)).empty
    assert list(inf.diferencias_rojas(pd.DataFrame(), None, None, HOY, HOY).columns) == inf.COLUMNAS_INFORME


def test_lo_autorizado_se_marca_con_quien_autorizo_y_el_motivo():
    r = inf.diferencias_rojas(_registros(), _ajustes(AJ_CORTE, AJ_ENTREGA), None, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert list(r["estado"]) == ["Autorizada", "Autorizada", "Pendiente"]
    assert r.loc[0, "autorizo"] == "Josué" and r.loc[0, "motivo"] == "retiro para depósito"
    assert r.loc[1, "autorizo"] == "Josué" and r.loc[1, "motivo"] == "entregó a caja chica"
    assert r.loc[2, "autorizo"] == "" and r.loc[2, "motivo"] == ""


def test_un_ajuste_que_no_alcanza_queda_parcial_con_lo_que_falta():
    parcial = ("x3", "Colon", dt.date(2026, 10, 17), -10.0, "retiro", "Josué", "Tarde", "", "")
    r = inf.diferencias_rojas(_registros(), _ajustes(parcial), None, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    corte = r[r["tipo"] == "Corte"].iloc[0]
    assert corte["estado"] == "Parcial" and corte["ajuste"] == -10.0 and corte["restante"] == -10.0


def test_la_conformidad_del_trabajador_sale_en_cada_diferencia():
    conf = pd.DataFrame([
        {"id": "k1", "timestamp": pd.Timestamp("2026-10-19 10:00"), "tipo": "Corte", "id_cierre": "c1", "estado": "Aceptó", "fecha_conformidad": "2026-10-19"},
        {"id": "k2", "timestamp": pd.Timestamp("2026-10-19 10:05"), "tipo": "Entrega de caja", "id_cierre": "c1", "estado": "No aceptó", "fecha_conformidad": "2026-10-19"},
    ])
    r = inf.diferencias_rojas(_registros(), None, conf, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert list(r["conformidad"]) == ["Aceptó", "No aceptó", "Sin registrar"]


def test_resumen_por_sucursal_incluye_las_que_no_tuvieron_diferencias_y_separa_lo_autorizado():
    r = inf.diferencias_rojas(_registros(), _ajustes(AJ_CORTE), None, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    s = inf.resumen_por_sucursal(r, ["Colon", "Zol A"]).set_index("local")
    assert s.loc["Colon", "pendientes"] == 2 and s.loc["Colon", "faltantes"] == 80.0 and s.loc["Colon", "sobrantes"] == 3.0
    assert s.loc["Colon", "neto"] == -77.0 and s.loc["Colon", "autorizadas"] == 1 and s.loc["Colon", "monto_autorizado"] == -20.0
    assert s.loc["Zol A", "pendientes"] == 0 and s.loc["Zol A", "neto"] == 0 and s.loc["Zol A", "autorizadas"] == 0
    assert inf.resumen_por_sucursal(pd.DataFrame(columns=inf.COLUMNAS_INFORME), ["Colon"]).loc[0, "pendientes"] == 0


def test_resumen_por_persona_solo_cuenta_lo_pendiente():
    r = inf.diferencias_rojas(_registros(), _ajustes(AJ_CORTE), None, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    p = inf.resumen_por_persona(r).set_index("persona")
    assert p.loc["Pedro", "entregas"] == 1 and p.loc["Pedro", "cortes"] == 0 and p.loc["Pedro", "neto"] == -80.0  # su corte ya esta autorizado
    assert p.loc["Ana", "cortes"] == 1 and p.loc["Ana", "sobrantes"] == 3.0
    assert list(p.index) == ["Pedro", "Ana"]  # el que mas debe, primero
    assert inf.resumen_por_persona(pd.DataFrame(columns=inf.COLUMNAS_INFORME)).empty


@pytest.mark.parametrize("incluir", [False, True])
def test_el_pdf_se_genera_con_y_sin_autorizadas(incluir):
    r = inf.diferencias_rojas(_registros(), _ajustes(AJ_CORTE), None, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    pdf = inf.generar_pdf(r, ["Colon", "Zol A"], dt.date(2026, 10, 1), dt.date(2026, 10, 31), "Mes actual · octubre 2026",
                          dt.datetime(2026, 10, 31, 22, 0), incluir_autorizadas=incluir)
    assert pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF") and len(pdf) > 2000


def test_el_pdf_sin_diferencias_y_con_caracteres_raros_no_se_cae():
    vacio = inf.generar_pdf(pd.DataFrame(columns=inf.COLUMNAS_INFORME), ["Colon"], HOY, HOY, "Mes actual", dt.datetime(2026, 10, 18, 9, 0))
    assert vacio.startswith(b"%PDF")
    raro = _registros()
    raro.loc[raro["nombre"] == "Pedro", "nombre"] = "Ñandú ✓ 日本 → \"x\""
    raro.loc[raro["id"] == "c1", "observaciones"] = "falta — “billete” … " + "muy largo " * 40
    r = inf.diferencias_rojas(raro, _ajustes(("x1", "Colon", dt.date(2026, 10, 17), -20.0, "ñ ✓ 日本", "Josué", "Tarde", "", "")), None,
                              dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    pdf = inf.generar_pdf(r, ["Colon"], dt.date(2026, 10, 1), dt.date(2026, 10, 31), "Rango", dt.datetime(2026, 10, 31, 22, 0), True)
    assert pdf.startswith(b"%PDF")


def test_el_pdf_con_muchas_filas_ocupa_varias_paginas():
    filas = []
    for i in range(1, 61):
        dia = f"2026-10-{(i % 28) + 1:02d}"
        filas += [
            _r(f"a{i}", "Colon", dia, "Mañana", "Apertura", "Ana", "08:00", 3000, 500),
            _r(f"c{i}", "Colon", dia, "Mañana", "Cierre", "Ana", "14:00", 2950, 500, ops=30),
        ]
    reg = pd.DataFrame(filas)
    r = inf.diferencias_rojas(reg, None, None, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    pdf = inf.generar_pdf(r, ["Colon"], dt.date(2026, 10, 1), dt.date(2026, 10, 31), "Mes", dt.datetime(2026, 10, 31, 22, 0))
    assert pdf.count(b"/Type /Page\n") >= 3 or pdf.count(b"/Type /Page") >= 4  # varias paginas, con el encabezado repetido
