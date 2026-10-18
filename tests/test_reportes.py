"""\nPruebas de reportes.py (lo que responde el bot de Telegram a cada comando) y de la parte del\nbot que decide a quien le responde. No necesitan credenciales ni conexion.\n"""

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
    assert "💵 Efectivo: S/ 2,900.00\n💳 Tarjeta: S/ 580.00\n💰 Total: S/ 3,480.00" in txt  # un monto por linea
    assert "BAJO EL MÍNIMO" in txt
    # el bajo va primero y el total junta los dos locales
    assert txt.index("Colon-H0715") < txt.index("Zol A-H3582")
    assert "S/ 9,480.00" in txt  # 3,480 + 6,000 (Zol A: ultimo registro del 18)
    assert "Zol A-H3582" not in rp.reporte_fondo(_registros(), _config(), HOY, "colon")
    assert "No encontré" in rp.reporte_fondo(_registros(), _config(), HOY, "inexistente")


def test_corte_ultimo_cerrado_con_operaciones_y_diferencia():
    txt = rp.reporte_corte(_registros(), "colon")
    assert "Mañana, 18/10" in txt
    assert "🔓 <b>Apertura</b> 08:00\n💵 Efectivo: S/ 3,000.00\n💳 Tarjeta: S/ 500.00\n💰 Total: S/ 3,500.00" in txt
    assert "🔒 <b>Cierre</b> 14:00\n💵 Efectivo: S/ 2,900.00\n💳 Tarjeta: S/ 580.00\n💰 Total: S/ 3,480.00" in txt
    assert "Diferencia: -20.00 🔴" in txt
    assert "Operaciones: 62" in txt
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
    assert "Colon-H0715</b>\n📋 Encuestas: 3 (S/ 30.00)\n✅ Pagadas: 1 (S/ 10.00)\n💸 Por pagar: 2 (S/ 20.00)" in txt
    assert "NPS final 60 ✅ aprobada" in txt
    assert "NPS: avance 40" in txt and "por debajo" in txt
    assert "📋 Encuestas: 4 (S/ 40.00)" in txt and "💸 Por pagar: S/ 20.00" in txt


def test_encuestas_con_local_lista_las_ultimas_y_sin_datos():
    txt = rp.reporte_encuestas(_encuestas(), _notas(), HOY, "colon")
    assert "Últimas encuestas:" in txt and "Ana" in txt and "Zol A" not in txt
    assert "Todavía no hay encuestas" in rp.reporte_encuestas(pd.DataFrame(), _notas(), HOY)


def test_operaciones_hoy_mes_y_meta():
    txt = rp.reporte_operaciones(_registros(), _config(), HOY)
    assert "🌅 Mañana: 62/60 ✅" in txt          # Colon cumplio la meta de la mañana
    assert "🌇 Tarde: en curso (meta 40)" in txt  # y la tarde sigue abierta
    assert "📅 Hoy: 62" in txt and "📅 Hoy: 40" in txt
    assert "Mes: 62 en 1 turno(s)" in txt and "Mes: 70 en 2 turno(s)" in txt  # Colon; Zol A (hoy 40 + ayer 30)
    assert "📅 Hoy: 102\n🗓 Mes: 132" in txt


def test_solicitudes_pendientes_y_vacio():
    s = pd.DataFrame([
        {"id": "1", "timestamp": pd.Timestamp("2026-10-18 09:00"), "local": "Colon", "tipo": "Falta saldo",
         "monto": 500.0, "urgente": "Sí", "solicitado_por": "Ana", "nota": "cola", "estado": "Solicitada"},
        {"id": "2", "timestamp": pd.Timestamp("2026-10-18 08:00"), "local": "Zol A", "tipo": "Falta efectivo",
         "monto": 100.0, "urgente": "", "solicitado_por": "Luis", "nota": "", "estado": "Completada"},
    ])
    txt = rp.reporte_solicitudes(s, pd.Timestamp("2026-10-18 09:12"))
    assert "1 solicitud(es) pendiente(s)" in txt and "URGENTE" in txt and "Hace 12 min" in txt and "Zol A" not in txt
    assert "💰 Monto: S/ 500.00" in txt and "👤 Pidió: Ana" in txt
    assert "No hay solicitudes pendientes" in rp.reporte_solicitudes(s.iloc[1:], pd.Timestamp("2026-10-18 09:12"))


def test_diferencias_corte_y_entre_cierres():
    reg = pd.DataFrame([
        _r("a1", "Colon", "2026-10-17", "Tarde", "Apertura", "Pedro", "14:30", 3000, 500),
        _r("c1", "Colon", "2026-10-17", "Tarde", "Cierre", "Pedro", "21:00", 2980, 500, ops=30),   # -20
        _r("a2", "Colon", "2026-10-18", "Mañana", "Apertura", "Ana", "08:00", 2900, 500),            # -80 entre cierres
        _r("c2", "Colon", "2026-10-18", "Mañana", "Cierre", "Ana", "14:00", 2903, 500, ops=40),     # +3 (no avisa)
    ])
    txt = rp.reporte_diferencias(reg, HOY, 7, 6)
    assert "🔴 <b>Colon</b> · 17/10" in txt and "Diferencia: -20.00" in txt
    assert "🟠 <b>Colon</b> · 18/10" in txt and "Diferencia: -80.00" in txt
    assert "Cerró: Pedro" in txt and "Abrió: Ana" in txt
    assert "+3.00" not in txt and "Total: 2" in txt
    assert "No hay diferencias" in rp.reporte_diferencias(reg, HOY, 7, 100)
    assert "-20.00" not in rp.reporte_diferencias(reg, HOY, 1, 6)  # solo hoy


def test_ayuda_menciona_todos_los_comandos():
    for c in ("/fondo", "/corte", "/encuestas", "/operaciones", "/solicitudes", "/diferencias", "/sucursales", "/ranking", "/resumen", "/id"):
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


# ---------------------------------------------------------------------
# Lo ya AUTORIZADO en el Dashboard no se vuelve a mostrar como diferencia pendiente
# ---------------------------------------------------------------------
from cuadre import calcular_cortes, calcular_saltos, marcar_autorizados  # noqa: E402


def _ajustes(*filas):
    cols = ["id", "local", "fecha", "monto", "turno", "salto_id_cierre"]
    return pd.DataFrame([dict(zip(cols, f)) for f in filas], columns=cols)


def _reg_dif():
    # 17/10 Tarde: corte -20 ; entre cierre (17/10) y apertura (18/10): -80 ; 18/10 Mañana: corte +3
    return pd.DataFrame([
        _r("a1", "Colon", "2026-10-17", "Tarde", "Apertura", "Pedro", "14:30", 3000, 500),
        _r("c1", "Colon", "2026-10-17", "Tarde", "Cierre", "Pedro", "21:00", 2980, 500, ops=30),
        _r("a2", "Colon", "2026-10-18", "Mañana", "Apertura", "Ana", "08:00", 2900, 500),
        _r("c2", "Colon", "2026-10-18", "Mañana", "Cierre", "Ana", "14:00", 2903, 500, ops=40),
    ])


def test_diferencias_sin_ajustes_muestra_todo():
    txt = rp.reporte_diferencias(_reg_dif(), HOY, 7, 6, ajustes=_ajustes())
    assert "Diferencia: -20.00" in txt and "Diferencia: -80.00" in txt and "Total: 2" in txt
    assert "autorizada" not in txt.lower().replace("(las ya autorizadas en el dashboard no se muestran)", "")


def test_corte_autorizado_por_turno_no_se_muestra_pero_con_todas_si():
    aj = _ajustes(("x1", "Colon", dt.date(2026, 10, 17), -20.0, "Tarde", ""))
    txt = rp.reporte_diferencias(_reg_dif(), HOY, 7, 6, ajustes=aj)
    assert "Diferencia: -20.00" not in txt and "Diferencia: -80.00" in txt
    assert "1 ya autorizada(s) no se muestran" in txt and "/diferencias todas" in txt
    todas = rp.reporte_diferencias(_reg_dif(), HOY, 7, 6, ajustes=aj, incluir_autorizadas=True)
    assert "🔷 <b>Colon</b> · 17/10 · Tarde" in todas and "Autorizada (ajuste -20.00)" in todas


def test_salto_autorizado_por_id_del_cierre():
    aj = _ajustes(("x1", "Colon", dt.date(2026, 10, 17), -80.0, "", "c1"))
    txt = rp.reporte_diferencias(_reg_dif(), HOY, 7, 6, ajustes=aj)
    assert "Diferencia: -80.00" not in txt and "Diferencia: -20.00" in txt


def test_ajuste_viejo_por_local_y_fecha_solo_explica_saltos_entre_dias():
    aj = _ajustes(("x1", "Colon", dt.date(2026, 10, 17), -80.0, "", ""))  # esquema viejo: local + fecha del cierre
    txt = rp.reporte_diferencias(_reg_dif(), HOY, 7, 6, ajustes=aj)
    assert "Diferencia: -80.00" not in txt  # el salto 17/10 -> 18/10 es "Entre días"


def test_ajuste_parcial_sigue_apareciendo_con_lo_que_falta_explicar():
    aj = _ajustes(("x1", "Colon", dt.date(2026, 10, 17), -5.0, "Tarde", ""))
    txt = rp.reporte_diferencias(_reg_dif(), HOY, 7, 6, ajustes=aj)
    assert "Diferencia: -20.00" in txt
    assert "Ajuste parcial del turno -5.00 · sin explicar -15.00" in txt


def test_ajuste_que_deja_hasta_un_sol_sin_explicar_cuenta_como_autorizado():
    aj = _ajustes(("x1", "Colon", dt.date(2026, 10, 17), -19.5, "Tarde", ""))  # queda -0.50 (<= S/ 1)
    cortes, _ = marcar_autorizados(calcular_cortes(_reg_dif(), ["local", "fecha", "turno"]), None, aj)
    assert bool(cortes.loc[cortes["id_cierre"] == "c1", "_autorizado"].iloc[0])


def test_marcar_autorizados_no_toca_el_estado_de_secuencia_ni_lo_cuadrado():
    reg = pd.DataFrame([
        _r("a1", "Colon", "2026-10-17", "Tarde", "Apertura", "Pedro", "14:30", 3000, 500),
        _r("c1", "Colon", "2026-10-17", "Tarde", "Cierre", "Pedro", "21:00", 3000, 500, ops=30),  # cuadrado
    ])
    aj = _ajustes(("x1", "Colon", dt.date(2026, 10, 17), 50.0, "Tarde", ""))
    cortes, saltos = marcar_autorizados(calcular_cortes(reg, ["local", "fecha", "turno"]), calcular_saltos(reg), aj)
    assert not cortes["_autorizado"].any()  # ya estaba cuadrado: no se marca como autorizado
    assert saltos.empty and "_autorizado" in saltos.columns


def test_corte_en_el_bot_muestra_si_esta_autorizado():
    aj = _ajustes(("x1", "Colon-H0715", dt.date(2026, 10, 18), -20.0, "Mañana", ""))
    txt = rp.reporte_corte(_registros(), "colon", aj)
    assert "Diferencia: -20.00 🔷 Autorizada (ajuste -20.00)" in txt
    assert "Diferencia: -20.00 🔴" in rp.reporte_corte(_registros(), "colon", _ajustes())


# ---------------------------------------------------------------------
# Turnos sin cerrar, ranking de trabajadores y resumen del dia
# ---------------------------------------------------------------------
def test_turnos_sin_cerrar_solo_los_que_pasan_el_umbral():
    reg = pd.DataFrame([
        _r("a1", "Colon", "2026-10-18", "Mañana", "Apertura", "Ana", "08:00", 3000, 500),       # abierto
        _r("a2", "Zol A", "2026-10-18", "Mañana", "Apertura", "Luis", "08:00", 3000, 500),
        _r("c2", "Zol A", "2026-10-18", "Mañana", "Cierre", "Luis", "14:00", 3000, 500, ops=10),  # cerrado
        _r("a3", "Fer", "2026-10-10", "Tarde", "Apertura", "Pedro", "14:00", 100, 0),             # muy viejo: no cuenta
    ])
    ahora = pd.Timestamp("2026-10-18 17:30")
    [t] = rp.turnos_sin_cerrar(reg, ahora, 9)
    assert t["id"] == "a1" and t["local"] == "Colon" and round(t["horas"], 1) == 9.5
    assert rp.turnos_sin_cerrar(reg, pd.Timestamp("2026-10-18 12:00"), 9) == []
    txt = rp.texto_turno_sin_cerrar(t)
    assert "Turno sin cerrar" in txt and "Abrió: Ana a las 08:00" in txt and "Lleva 9 h 30 min" in txt


def test_rango_periodo():
    assert rp.rango_periodo("hoy", HOY)[:2] == (HOY, HOY)
    assert rp.rango_periodo("semana", HOY)[:2] == (dt.date(2026, 10, 12), HOY)
    assert rp.rango_periodo("mes", HOY)[:2] == (dt.date(2026, 10, 1), HOY)
    assert rp.rango_periodo("anterior", HOY)[:3] == (dt.date(2026, 9, 1), dt.date(2026, 9, 30), "septiembre 2026")


def test_ranking_ordena_por_operaciones_y_atribuye_al_que_abrio():
    txt = rp.reporte_ranking(_registros(), _config(), _encuestas(), _ajustes(), HOY)
    assert "Ranking de trabajadores" in txt and "octubre 2026" in txt
    assert txt.index("🥇 <b>Luis</b>") < txt.index("🥈 <b>Ana</b>") < txt.index("🥉 <b>Pedro</b>")
    assert "🔢 70 operaciones · 2 turno(s)" in txt          # Luis: 40 (hoy) + 30 (ayer)
    assert "📈 35 por turno" in txt
    assert "🔢 62 operaciones · 1 turno(s)" in txt          # Ana: su corte de la mañana
    assert "🎯 Meta cumplida: 1 de 1 turno(s)" in txt       # Ana hizo 62 con meta de 60
    assert "⭐ Encuestas: 2" in txt                          # Ana
    assert "Diferencias sin autorizar: -20.00" in txt        # el corte de Ana (-20)


def test_ranking_no_cuenta_la_diferencia_ya_autorizada():
    aj = _ajustes(("x1", "Colon-H0715", dt.date(2026, 10, 18), -20.0, "Mañana", ""))
    assert "Diferencias sin autorizar" not in rp.reporte_ranking(_registros(), _config(), _encuestas(), aj, HOY, "mes", "colon")


def test_ranking_filtros_y_periodos_vacios():
    colon = rp.reporte_ranking(_registros(), _config(), _encuestas(), _ajustes(), HOY, "mes", "colon")
    assert "Luis" not in colon and "Ana" in colon
    assert "No hay registros en ese período" in rp.reporte_ranking(_registros(), _config(), _encuestas(), _ajustes(), HOY, "anterior")
    assert "No encontré" in rp.reporte_ranking(_registros(), _config(), _encuestas(), _ajustes(), HOY, "mes", "xyz")
    hoy_solo = rp.reporte_ranking(_registros(), _config(), _encuestas(), _ajustes(), HOY, "hoy")
    assert "🔢 40 operaciones" in hoy_solo and "🔢 70" not in hoy_solo   # Luis solo con lo de hoy


def test_resumen_diario_junta_todas_las_secciones():
    sol = pd.DataFrame([{"id": "1", "timestamp": pd.Timestamp("2026-10-18 09:00"), "local": "Colon", "tipo": "Falta saldo",
                         "monto": 500.0, "urgente": "", "solicitado_por": "Ana", "nota": "", "estado": "Solicitada"}])
    txt = rp.resumen_diario(_registros(), _config(), _encuestas(), _ajustes(), sol, HOY, pd.Timestamp("2026-10-18 22:00"))
    assert "Resumen del día</b> — domingo 18/10" in txt
    for seccion in ("Operaciones", "Fondo actual", "Diferencias", "Turnos todavía abiertos", "solicitud(es) pendiente(s)"):
        assert seccion in txt, seccion
    assert "Colon-H0715 · Tarde · 18/10" in txt   # el turno Tarde de Colon sigue abierto
    todo_cerrado = _registros()[lambda d: d["local"] == "Zol A-H3582"]
    cerrado = rp.resumen_diario(todo_cerrado, _config(), _encuestas().iloc[0:0], _ajustes(), sol.iloc[0:0], HOY, pd.Timestamp("2026-10-18 22:00"))
    assert "Todos los turnos están cerrados" in cerrado and "Hoy no se registraron encuestas" in cerrado


def test_turnos_sin_cerrar_por_defecto_a_las_8_horas_y_media(monkeypatch):
    import bot_telegram as bot

    monkeypatch.delenv("ALERTA_TURNO_ABIERTO_HORAS", raising=False)
    assert bot.horas_turno_abierto() == 8.5
    monkeypatch.setenv("ALERTA_TURNO_ABIERTO_HORAS", "6.5")
    assert bot.horas_turno_abierto() == 6.5
    monkeypatch.setenv("ALERTA_TURNO_ABIERTO_HORAS", "xx")
    assert bot.horas_turno_abierto() == 8.5

    reg = pd.DataFrame([_r("a1", "Colon", "2026-10-18", "Tarde", "Apertura", "Ana", "13:00", 3000, 500)])
    assert rp.turnos_sin_cerrar(reg, pd.Timestamp("2026-10-18 21:29")) == []     # 8 h 29 min
    assert len(rp.turnos_sin_cerrar(reg, pd.Timestamp("2026-10-18 21:30"))) == 1  # 8 h 30 min


# ---------------------------------------------------------------------
# /sucursales: balance por sucursal y politica de descuento
# ---------------------------------------------------------------------
def test_sucursales_balance_y_politica_de_descuento():
    txt = rp.reporte_sucursales(_registros(), _config(), _ajustes(), HOY)
    assert "Balance por sucursal" in txt and "octubre 2026" in txt
    # Colon: faltaron 20 en un corte; con 62 operaciones la franquicia es 0.62 -> descuento 19.38
    assert "🔴 <b>Colon-H0715</b>" in txt
    assert "📉 Faltantes: S/ 20.00" in txt and "⚖️ Neto sin explicar: -20.00" in txt
    assert "descuento S/ 19.38" in txt and "👤 Ana: S/ 19.38 (100%)" in txt
    # Zol A: la entrega de 17/10 a 18/10 sube 1,100: es un item grande, se investiga aparte
    assert "🟢 <b>Zol A-H3582</b>" in txt
    assert "Ítems grandes (S/ 40 o más): 1 (+1,100.00) — investigar aparte" in txt
    assert txt.index("Colon-H0715") < txt.index("Zol A-H3582")  # el que mas falto, primero
    assert "📐 Descuento por política: S/ 19.38" in txt


def test_sucursales_lo_autorizado_no_cuenta_y_se_muestra():
    aj = _ajustes(("x1", "Colon-H0715", dt.date(2026, 10, 18), -20.0, "Mañana", ""))
    txt = rp.reporte_sucursales(_registros(), _config(), aj, HOY, "mes", "colon")
    assert "🟢 <b>Colon-H0715</b>" in txt and "⚖️ Neto sin explicar: 0.00" in txt
    assert "🔷 Autorizado: -20.00" in txt and "descuento S/ 0.00" in txt


def test_sucursales_con_un_local_lista_sus_diferencias_y_filtros():
    txt = rp.reporte_sucursales(_registros(), _config(), _ajustes(), HOY, "mes", "colon")
    assert "Zol A" not in txt and "Diferencias por revisar:" in txt and "• 18/10 · Corte · Ana: -20.00" in txt
    assert "No encontré" in rp.reporte_sucursales(_registros(), _config(), _ajustes(), HOY, "mes", "xyz")
    assert "No hay registros en ese período" in rp.reporte_sucursales(_registros(), _config(), _ajustes(), HOY, "anterior")
    assert "Todavía no hay registros" in rp.reporte_sucursales(pd.DataFrame(), _config(), _ajustes(), HOY)


def test_sucursales_repuesto_suma_solo_ingresos_con_quien_repuso():
    aj = pd.DataFrame([
        {"id": "x1", "local": "Colon-H0715", "fecha": dt.date(2026, 10, 18), "monto": 30.0, "turno": "", "salto_id_cierre": "zz", "repuso": "Ana"},
        {"id": "x2", "local": "Colon-H0715", "fecha": dt.date(2026, 10, 18), "monto": 10.0, "turno": "", "salto_id_cierre": "yy", "repuso": ""},
    ])
    txt = rp.reporte_sucursales(_registros(), _config(), aj, HOY, "mes", "colon")
    assert "↩️ Repuesto: S/ 30.00" in txt and "🔷 Autorizado: +40.00" in txt


# ---------------------------------------------------------------------
# /liquidacion: la liquidacion de cada trabajador (misma tabla que el Dashboard)
# ---------------------------------------------------------------------
def _personal():
    return pd.DataFrame([
        {"nombre": "Maria", "local": "Colon-H0715", "alias": "", "activo": "si"},
        {"nombre": "Rosa", "local": "Colon-H0715", "alias": "", "activo": "no"},
    ])


def _tabla_liq(ajustes=None):
    return rp.tabla_liquidacion(
        _registros(), _config(), _encuestas(), _notas(), _ajustes() if ajustes is None else ajustes, _personal(),
        dt.date(2026, 10, 1), HOY, ["Colon-H0715", "Zol A-H3582"],
    ).set_index("Persona")


def test_tabla_liquidacion_incluye_a_todos_y_calcula_cada_columna():
    t = _tabla_liq()
    assert list(t.index) == ["Ana", "Luis", "Maria", "Pedro"]  # Rosa esta inactiva; Maria no tuvo registros
    ana = t.loc["Ana"]
    assert (ana["Faltantes (S/)"], ana["Neto (S/)"], ana["A revisar (S/)"]) == (20.0, -20.0, 20.0)
    assert round(ana["Descuento asignado (S/)"], 2) == 19.38 and round(ana["A descontar (S/)"], 2) == 19.38
    assert ana["Incentivo a pagar (S/)"] == 20.0 and ana["Encuestas llenadas"] == 2   # nota de Colon aprobada
    assert (ana["Operaciones"], ana["Turnos trabajados"], ana["Turnos con meta"], ana["Meta cumplida"]) == (62, 1, 1, 1)
    luis = t.loc["Luis"]   # la entrega de +1100 es un item grande: se ve en su neto, pero no entra al reparto
    assert luis["Sobrantes (S/)"] == 1100.0 and luis["A revisar (S/)"] == 0.0 and luis["A descontar (S/)"] == 0.0
    assert luis["Operaciones"] == 70 and luis["Turnos trabajados"] == 2 and luis["Incentivo a pagar (S/)"] == 0.0  # nota sin cierre
    maria = t.loc["Maria"]
    assert maria["Ítems"] == 0 and maria["Operaciones"] == 0 and maria["Turnos trabajados"] == 0
    assert t.loc["Pedro", "Incentivo a pagar (S/)"] == 0.0 and t.loc["Pedro", "Encuestas llenadas"] == 1  # ya pagada


def test_liquidacion_lo_autorizado_y_lo_repuesto_cambian_el_descuento():
    aut = _ajustes(("x1", "Colon-H0715", dt.date(2026, 10, 18), -20.0, "Mañana", ""))
    assert _tabla_liq(aut).loc["Ana", "A descontar (S/)"] == 0.0 and _tabla_liq(aut).loc["Ana", "Faltantes (S/)"] == 0.0
    repuso = pd.DataFrame([{"id": "x2", "local": "Colon-H0715", "fecha": dt.date(2026, 10, 18), "monto": 15.0, "turno": "",
                            "salto_id_cierre": "zz", "repuso": "Ana", "motivo": "repuso"}])
    ana = _tabla_liq(repuso).loc["Ana"]
    assert ana["A favor (S/)"] == 15.0 and round(ana["A descontar (S/)"], 2) == 4.38  # 19.38 - 15


def test_reporte_liquidacion_un_dato_por_linea_y_filtros():
    txt = rp.reporte_liquidacion(_registros(), _config(), _encuestas(), _notas(), _ajustes(), _personal(), HOY)
    assert "Liquidación por trabajador</b> — octubre 2026" in txt and "no un descuento automático" in txt
    assert "💸 A descontar en total: S/ 19.38" in txt and "⭐ Incentivos a pagar: S/ 20.00" in txt and "👥 Trabajadores: 4" in txt
    assert "🧾 <b>Ana</b> · Colon-H0715\n📉 Faltantes: S/ 20.00" in txt
    assert "💸 <b>A descontar: S/ 19.38</b>" in txt and "🎯 Meta cumplida: 1 de 1 turnos" in txt
    assert "✅ <b>Maria</b> — sin diferencias" in txt           # los que no tuvieron nada tambien salen
    assert txt.index("<b>Ana</b>") < txt.index("<b>Luis</b>")    # primero a quien hay que descontar
    solo_ana = rp.reporte_liquidacion(_registros(), _config(), _encuestas(), _notas(), _ajustes(), _personal(), HOY, "mes", "ana")
    assert "<b>Ana</b>" in solo_ana and "<b>Luis</b>" not in solo_ana
    solo_zol = rp.reporte_liquidacion(_registros(), _config(), _encuestas(), _notas(), _ajustes(), _personal(), HOY, "mes", "zol")
    assert "<b>Luis</b>" in solo_zol and "<b>Ana</b>" not in solo_zol
    assert "No encontré a nadie" in rp.reporte_liquidacion(_registros(), _config(), _encuestas(), _notas(), _ajustes(), _personal(), HOY, "mes", "zzz")
    assert "Todavía no hay registros" in rp.reporte_liquidacion(pd.DataFrame(), _config(), _encuestas(), _notas(), _ajustes(), _personal(), HOY)


def test_liquidacion_final_del_mes_anterior_lista_lo_que_sigue_sin_autorizar():
    hoy = dt.date(2026, 11, 3)
    txt = rp.reporte_liquidacion(_registros(), _config(), _encuestas(), _notas(), _ajustes(), _personal(), hoy, "anterior")
    assert "Liquidación FINAL por trabajador</b> — octubre 2026" in txt and "(01/10 al 31/10)" in txt
    assert "Liquidación definitiva del mes" in txt
    assert "Diferencias que siguen sin autorizar (2)" in txt
    assert "• 17/10 · Zol A-H3582 · Entrega de caja · Luis: +1,100.00" in txt
    assert "• 18/10 · Colon-H0715 · Corte · Ana: -20.00" in txt
    assert txt.index("Diferencias que siguen sin autorizar") < txt.index("🧾 <b>Ana</b>")   # primero lo pendiente
    # el mes en curso no es "FINAL" ni lista pendientes
    mes = rp.reporte_liquidacion(_registros(), _config(), _encuestas(), _notas(), _ajustes(), _personal(), HOY)
    assert "FINAL" not in mes and "siguen sin autorizar" not in mes


def test_liquidacion_final_sin_pendientes_cuando_todo_esta_autorizado():
    aj = _ajustes(
        ("x1", "Colon-H0715", dt.date(2026, 10, 18), -20.0, "Mañana", ""),
        ("x2", "Zol A-H3582", dt.date(2026, 10, 17), 1100.0, "", "c4"),
    )
    txt = rp.reporte_liquidacion(_registros(), _config(), _encuestas(), _notas(), aj, _personal(), dt.date(2026, 11, 3), "anterior")
    assert "No quedan diferencias sin autorizar" in txt and "siguen sin autorizar" not in txt


def test_liquidacion_final_de_un_trabajador_o_local_filtra_los_pendientes():
    hoy = dt.date(2026, 11, 3)
    ana = rp.reporte_liquidacion(_registros(), _config(), _encuestas(), _notas(), _ajustes(), _personal(), hoy, "anterior", "ana")
    assert "sin autorizar (1)" in ana and "Luis" not in ana
    zol = rp.reporte_liquidacion(_registros(), _config(), _encuestas(), _notas(), _ajustes(), _personal(), hoy, "anterior", "zol")
    assert "sin autorizar (1)" in zol and "Colon" not in zol.split("🧾")[0]
