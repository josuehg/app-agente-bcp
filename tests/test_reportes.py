"""
Pruebas de reportes.py (lo que responde el bot de Telegram a cada comando) y de la parte del
bot que decide a quien le responde. No necesitan credenciales ni conexion.
"""

import datetime as dt

import pandas as pd

import reportes as rp

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
    return pd.DataFrame([
        _r("a1", "Colon-H0715", "2026-10-18", "Mañana", "Apertura", "Ana", "08:00", 3000, 500),
        _r("c1", "Colon-H0715", "2026-10-18", "Mañana", "Cierre", "Ana", "14:00", 2900, 580, ops=62, obs="faltaron 20"),
        _r("a2", "Colon-H0715", "2026-10-18", "Tarde", "Apertura", "Pedro", "14:30", 2900, 580),
        _r("a3", "Zol A-H3582", "2026-10-18", "Mañana", "Apertura", "Luis", "08:00", 5000, 1000),
        _r("c3", "Zol A-H3582", "2026-10-18", "Mañana", "Cierre", "Luis", "14:00", 5000, 1000, ops=40),
        _r("a4", "Zol A-H3582", "2026-10-17", "Tarde", "Apertura", "Luis", "14:30", 4000, 900),
        _r("c4", "Zol A-H3582", "2026-10-17", "Tarde", "Cierre", "Luis", "21:00", 4000, 900, ops=30),
    ])


def _config():
    return pd.DataFrame({
        "local": ["Colon-H0715", "Zol A-H3582"], "fondo_minimo": [5000.0, 5000.0],
        "meta_ops_manana": [60, 0], "meta_ops_tarde": [40, 0],
    })


def test_buscar_locales_sin_acentos_ni_mayusculas():
    locales = ["Colon-H0715", "Zol A-H3582", "ZolB-H4863"]
    assert rp.buscar_locales("colón", locales) == ["Colon-H0715"]
    assert rp.buscar_locales("ZOL", locales) == ["Zol A-H3582", "ZolB-H4863"]
    assert rp.buscar_locales("", locales) == sorted(locales)
    assert rp.buscar_locales("xyz", locales) == []


def test_partir_mensaje_respeta_el_limite():
    texto = "\n".join(f"linea {i}" for i in range(2000))
    partes = rp.partir_mensaje(texto, limite=500)
    assert all(len(p) <= 500 for p in partes) and "\n".join(partes) == texto
    assert rp.partir_mensaje("corto") == ["corto"]
    assert all(len(p) <= 100 for p in rp.partir_mensaje("x" * 450, limite=100))


def test_fondo_muestra_efectivo_tarjeta_total_y_marca_el_que_esta_bajo():
    txt = rp.reporte_fondo(_registros(), _config(), HOY)
    assert "Efectivo S/ 2,900.00 · Tarjeta S/ 580.00 · Total S/ 3,480.00" in txt
    assert "BAJO EL MÍNIMO" in txt
    # el bajo va primero y el total junta los dos locales
    assert txt.index("Colon-H0715") < txt.index("Zol A-H3582")
    assert "S/ 9,480.00" in txt  # 3,480 + 6,000 (Zol A: ultimo registro del 18)
    assert "Zol A-H3582" not in rp.reporte_fondo(_registros(), _config(), HOY, "colon")
    assert "No encontré" in rp.reporte_fondo(_registros(), _config(), HOY, "inexistente")


def test_corte_ultimo_cerrado_con_operaciones_y_diferencia():
    txt = rp.reporte_corte(_registros(), "colon")
    assert "Mañana, 18/10" in txt
    assert "Apertura: Efectivo S/ 3,000.00 · Tarjeta S/ 500.00 · Total S/ 3,500.00" in txt
    assert "Cierre: Efectivo S/ 2,900.00 · Tarjeta S/ 580.00 · Total S/ 3,480.00" in txt
    assert "Diferencia: -20.00 🔴" in txt
    assert "Operaciones de este cierre: 62" in txt
    assert "faltaron 20" in txt
    assert "turno Tarde abierto por Pedro desde 14:30" in txt  # lo que esta en curso ahora


def test_corte_sin_cierres_y_escapa_html():
    solo_apertura = pd.DataFrame([_r("a1", "Colon", "2026-10-18", "Mañana", "Apertura", "A<b>", "08:00", 100, 0)])
    assert "Todavía no tiene cortes cerrados" in rp.reporte_corte(solo_apertura)
    con_obs = pd.DataFrame([
        _r("a1", "Colon", "2026-10-18", "Mañana", "Apertura", "Ana", "08:00", 100, 0),
        _r("c1", "Colon", "2026-10-18", "Mañana", "Cierre", "Ana", "14:00", 100, 0, ops=5, obs="<script>x</script> & más"),
    ])
    txt = rp.reporte_corte(con_obs)
    assert "<script>" not in txt and "&lt;script&gt;" in txt and "&amp;" in txt


def _encuestas():
    filas = []
    for i, (loc, estado, nombre) in enumerate([
        ("Colon-H0715", "Pendiente", "Ana"), ("Colon-H0715", "Pendiente", "Ana"),
        ("Colon-H0715", "Pagada", "Pedro"), ("Zol A-H3582", "Pendiente", "Luis"),
    ]):
        filas.append({
            "id": f"e{i}", "timestamp": pd.Timestamp(f"2026-10-1{i + 1} 10:00"), "fecha": dt.date(2026, 10, i + 1),
            "local": loc, "nombre": nombre, "nota": 10, "incentivo": 10.0, "estado_pago": estado, "medio_pago": "Yape",
        })
    return pd.DataFrame(filas)


def _notas():
    return pd.DataFrame([
        {"mes": "2026-10", "local": "Colon-H0715", "nota": 60.0, "tipo": "Final", "fecha_corte": ""},
        {"mes": "2026-10", "local": "Zol A-H3582", "nota": 40.0, "tipo": "Parcial", "fecha_corte": "2026-10-15"},
    ])


def test_encuestas_del_mes_por_local_con_nota_nps():
    txt = rp.reporte_encuestas(_encuestas(), _notas(), HOY)
    assert "octubre 2026" in txt
    assert "Colon-H0715</b> — 3 encuesta(s) · S/ 30.00" in txt
    assert "Pagadas 1 (S/ 10.00) · Por pagar 2 (S/ 20.00)" in txt
    assert "NPS final 60 ✅ aprobada" in txt
    assert "NPS: avance 40" in txt and "por debajo" in txt
    assert "<b>Total:</b> 4 encuesta(s)" in txt


def test_encuestas_con_local_lista_las_ultimas_y_sin_datos():
    txt = rp.reporte_encuestas(_encuestas(), _notas(), HOY, "colon")
    assert "Últimas:" in txt and "Ana" in txt and "Zol A" not in txt
    assert "Todavía no hay encuestas" in rp.reporte_encuestas(pd.DataFrame(), _notas(), HOY)


def test_operaciones_hoy_mes_y_meta():
    txt = rp.reporte_operaciones(_registros(), _config(), HOY)
    assert "Mañana 62/60 ✅" in txt          # Colon cumplio la meta de la mañana
    assert "Tarde en curso (meta 40)" in txt  # y la tarde sigue abierta
    assert "Hoy: 62" in txt and "Hoy: 40" in txt
    assert "Mes: 62 en 1 turno(s)" in txt and "Mes: 70 en 2 turno(s)" in txt  # Colon; Zol A (hoy 40 + ayer 30)
    assert "<b>Total:</b> hoy 102 · mes 132" in txt


def test_solicitudes_pendientes_y_vacio():
    s = pd.DataFrame([
        {"id": "1", "timestamp": pd.Timestamp("2026-10-18 09:00"), "local": "Colon", "tipo": "Falta saldo",
         "monto": 500.0, "urgente": "Sí", "solicitado_por": "Ana", "nota": "cola", "estado": "Solicitada"},
        {"id": "2", "timestamp": pd.Timestamp("2026-10-18 08:00"), "local": "Zol A", "tipo": "Falta efectivo",
         "monto": 100.0, "urgente": "", "solicitado_por": "Luis", "nota": "", "estado": "Completada"},
    ])
    txt = rp.reporte_solicitudes(s, pd.Timestamp("2026-10-18 09:12"))
    assert "1 solicitud(es) pendiente(s)" in txt and "URGENTE" in txt and "hace 12 min" in txt and "Zol A" not in txt
    assert "No hay solicitudes pendientes" in rp.reporte_solicitudes(s.iloc[1:], pd.Timestamp("2026-10-18 09:12"))


def test_diferencias_corte_y_entre_cierres():
    reg = pd.DataFrame([
        _r("a1", "Colon", "2026-10-17", "Tarde", "Apertura", "Pedro", "14:30", 3000, 500),
        _r("c1", "Colon", "2026-10-17", "Tarde", "Cierre", "Pedro", "21:00", 2980, 500, ops=30),   # -20
        _r("a2", "Colon", "2026-10-18", "Mañana", "Apertura", "Ana", "08:00", 2900, 500),            # -80 entre cierres
        _r("c2", "Colon", "2026-10-18", "Mañana", "Cierre", "Ana", "14:00", 2903, 500, ops=40),     # +3 (no avisa)
    ])
    txt = rp.reporte_diferencias(reg, HOY, 7, 6)
    assert "🔴 17/10" in txt and "-20.00" in txt
    assert "🟠 18/10" in txt and "-80.00" in txt and "Pedro → Ana" in txt
    assert "+3.00" not in txt and "Total: 2" in txt
    assert "No hay diferencias" in rp.reporte_diferencias(reg, HOY, 7, 100)
    assert "-20.00" not in rp.reporte_diferencias(reg, HOY, 1, 6)  # solo hoy


def test_ayuda_menciona_todos_los_comandos():
    for c in ("/fondo", "/corte", "/encuestas", "/operaciones", "/solicitudes", "/diferencias", "/id"):
        assert c in rp.AYUDA


# ---------------------------------------------------------------------
# A quien le responde el bot
# ---------------------------------------------------------------------
def test_bot_solo_da_datos_a_chats_autorizados():
    import bot_telegram as bot

    permitidos = ["111", "-100222"]
    # un desconocido solo recibe su chat id, nunca datos
    resp = bot.procesar_mensaje(999, "/fondo", permitidos)
    assert "999" in resp and "Pásaselo al administrador" in resp and "Efectivo" not in resp
    assert "999" in bot.procesar_mensaje(999, "/start", permitidos)
    assert bot.procesar_mensaje(999, "hola", permitidos) is None  # y si solo escribe texto, silencio
    # un autorizado: /id, /ayuda, comando desconocido y texto suelto
    assert "autorizado" in bot.procesar_mensaje(111, "/id", permitidos)
    assert "/fondo" in bot.procesar_mensaje(111, "/ayuda", permitidos)
    assert "/fondo" in bot.procesar_mensaje(-100222, "/ayuda@mi_bot", permitidos)  # grupo, con @bot
    assert "No conozco ese comando" in bot.procesar_mensaje(111, "/xyz", permitidos)
    assert "/ayuda" in bot.procesar_mensaje(111, "hola", permitidos)
