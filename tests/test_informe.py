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


# ---------------------------------------------------------------------
# /pdfdif: el mismo PDF, por Telegram
# ---------------------------------------------------------------------
def test_periodo_semana_son_los_ultimos_7_dias():
    assert inf.periodo_informe("semana", dt.date(2026, 11, 23)) == (dt.date(2026, 11, 17), dt.date(2026, 11, 23), "Últimos 7 días")


def test_texto_que_acompana_al_pdf_un_dato_por_linea():
    r = inf.diferencias_rojas(_registros(), _ajustes(AJ_CORTE), None, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert inf.texto_resumen(r, "Mes actual · octubre 2026").split("\n") == [
        "📄 <b>Diferencias por sucursal</b>", "📅 Mes actual · octubre 2026", "🔴 Pendientes: 2", "📉 Faltantes: S/ 80.00",
        "📈 Sobrantes: S/ 3.00", "📊 Neto: S/ -77.00", "🔷 Autorizadas: 1 (no se listan; usa /pdfdif todas)",
    ]
    assert inf.texto_resumen(r, "x", True).endswith("🔷 Autorizadas: 1 (van en el detalle)")
    vacio = inf.texto_resumen(pd.DataFrame(columns=inf.COLUMNAS_INFORME), "Mes anterior")
    assert vacio.split("\n") == ["📄 <b>Diferencias por sucursal</b>", "📅 Mes anterior", "✅ No hay diferencias pendientes"]


def _bot_con_datos(monkeypatch, registros=None, ajustes=None):
    import bot_telegram as bot

    reg = _registros() if registros is None else registros
    monkeypatch.setattr(bot, "_fresco", lambda *lectores: [reg, ajustes if ajustes is not None else _ajustes(), None])
    monkeypatch.setattr(bot.sh, "hoy_local", lambda: HOY)
    monkeypatch.setattr(bot.sh, "ahora_local", lambda: dt.datetime(2026, 10, 18, 22, 0))
    return bot


def test_pdfdif_manda_el_pdf_del_mes_con_su_resumen(monkeypatch):
    bot = _bot_con_datos(monkeypatch)
    doc = bot.procesar_mensaje(111, "/pdfdif", ["111"])
    assert isinstance(doc, bot.Documento)
    assert doc.contenido.startswith(b"%PDF") and doc.nombre == "diferencias_20261001_20261018.pdf"
    assert "Mes actual · octubre 2026" in doc.texto and "🔴 Pendientes: 3" in doc.texto


def test_pdfdif_acepta_periodo_todas_y_local_en_cualquier_orden(monkeypatch):
    bot = _bot_con_datos(monkeypatch, ajustes=_ajustes(AJ_CORTE))
    ant = bot.procesar_mensaje(111, "/pdfdif anterior", ["111"])
    assert ant.nombre == "diferencias_20250901_20250930.pdf".replace("2025", "2026") and "No hay diferencias pendientes" in ant.texto
    sem = bot.procesar_mensaje(111, "/pdfdif semana", ["111"])
    assert sem.nombre == "diferencias_20261012_20261018.pdf" and "Últimos 7 días" in sem.texto
    solo = bot.procesar_mensaje(111, "/pdfdif Colón todas", ["111"])  # sin acentos ni mayusculas da igual
    assert solo.nombre.endswith("_colon.pdf") and "Autorizadas: 1 (van en el detalle)" in solo.texto
    assert bot.procesar_mensaje(111, "/pdfdif todas colon", ["111"]).texto == solo.texto
    assert "No encontré ningún local" in bot.procesar_mensaje(111, "/pdfdif xyz", ["111"])
    # un local sin diferencias: el PDF sale igual, con el aviso de que no hay pendientes
    zol = bot.procesar_mensaje(111, "/pdfdif zol", ["111"])
    assert isinstance(zol, bot.Documento) and "No hay diferencias pendientes" in zol.texto


def test_pdfdif_sin_registros_sin_permiso_y_con_google_caido(monkeypatch):
    bot = _bot_con_datos(monkeypatch, registros=pd.DataFrame())
    assert "Todavía no hay registros" in bot.procesar_mensaje(111, "/pdfdif", ["111"])
    # quien no esta autorizado solo recibe su chat id, nunca el PDF
    resp = bot.procesar_mensaje(999, "/pdfdif", ["111"])
    assert isinstance(resp, str) and "999" in resp

    def caido(*lectores):
        raise RuntimeError("Google ocupado")

    monkeypatch.setattr(bot, "_fresco", caido)
    assert "No pude leer los datos" in bot.procesar_mensaje(111, "/pdfdif", ["111"])


def test_enviar_documento_arma_el_formulario_con_el_archivo(monkeypatch):
    import urllib.error
    import urllib.request

    import bot_telegram as bot

    visto = {}

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def falso(pedido, timeout=0):
        visto["url"], visto["cuerpo"], visto["tipo"] = pedido.full_url, pedido.data, pedido.get_header("Content-type")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", falso)
    doc = bot.Documento(b"%PDF-1.3 contenido", "diferencias_x.pdf", "📄 <b>Resumen</b>\nñandú")
    assert bot.enviar_documento("TOKEN", 111, doc) is True
    assert visto["url"].endswith("/botTOKEN/sendDocument")
    borde = visto["tipo"].split("boundary=")[1]
    cuerpo = visto["cuerpo"]
    assert cuerpo.count(f"--{borde}".encode()) == 5 and cuerpo.endswith(f"--{borde}--\r\n".encode())
    assert b'name="chat_id"\r\n\r\n111\r\n' in cuerpo and b'name="parse_mode"\r\n\r\nHTML' in cuerpo
    assert "ñandú".encode() in cuerpo and b'name="document"; filename="diferencias_x.pdf"' in cuerpo
    assert b"Content-Type: application/pdf\r\n\r\n%PDF-1.3 contenido\r\n" in cuerpo

    def roto(pedido, timeout=0):
        raise urllib.error.URLError("sin internet")

    monkeypatch.setattr(urllib.request, "urlopen", roto)
    assert bot.enviar_documento("TOKEN", 111, doc) is False  # no lanza error ni muestra el token


def test_atender_manda_el_archivo_y_avisa_si_telegram_lo_rechaza(monkeypatch):
    import time

    bot = _bot_con_datos(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    textos, archivos = [], []
    monkeypatch.setattr(bot, "enviar", lambda token, chat, texto: textos.append((chat, texto)))
    resultado = {"ok": True}
    monkeypatch.setattr(bot, "enviar_documento", lambda token, chat, doc: archivos.append((chat, doc.nombre)) or resultado["ok"])
    upd = {"update_id": 1, "message": {"date": time.time(), "chat": {"id": 111, "type": "private"}, "text": "/pdfdif"}}
    bot.atender("t", upd)
    assert archivos == [(111, "diferencias_20261001_20261018.pdf")] and textos == []
    resultado["ok"] = False
    bot.atender("t", upd)
    assert len(archivos) == 2 and len(textos) == 1 and "no me dejó enviarlo" in textos[0][1]


def test_la_ayuda_y_el_menu_incluyen_pdfdif():
    import bot_telegram as bot
    import reportes as rp

    assert "/pdfdif" in rp.AYUDA and "<code>/pdfdif anterior</code>" in rp.AYUDA
    assert "pdfdif" in [c for c, _ in bot.COMANDOS_MENU]
    assert all(len(c) <= 32 and c == c.lower() for c, _ in bot.COMANDOS_MENU)


# ---------------------------------------------------------------------
# El PDF de diferencias, solo, el dia 29 de cada mes
# ---------------------------------------------------------------------
def _programador(monkeypatch, enviar_ok=True):
    """Un Programador con Telegram y la hoja 'Bot' falsos. Devuelve (programador, archivos enviados, estado guardado)."""
    import bot_telegram as bot

    bot = _bot_con_datos(monkeypatch)
    monkeypatch.delenv("PDF_DIFERENCIAS_DIA", raising=False)
    monkeypatch.delenv("PDF_DIFERENCIAS_HORA", raising=False)
    monkeypatch.setattr(bot.sh, "telegram_chat_ids", lambda: ["111", "-100222"])
    enviados, guardado = [], []
    monkeypatch.setattr(bot, "enviar_documento", lambda token, chat, doc: enviados.append((chat, doc.nombre)) or enviar_ok)
    monkeypatch.setattr(bot, "enviar", lambda token, chat, texto: enviados.append((chat, texto)))
    monkeypatch.setattr(bot.sh, "guardar_estado_bot", lambda clave, valor, unico=True: guardado.append((clave, valor)))
    prog = bot.Programador("TOKEN")
    prog.estado = {}
    return prog, enviados, guardado


def test_el_pdf_sale_solo_el_29_a_las_10_y_una_vez_al_mes(monkeypatch):
    prog, enviados, guardado = _programador(monkeypatch)
    prog._pdf_diferencias(dt.datetime(2026, 10, 28, 12, 0))
    prog._pdf_diferencias(dt.datetime(2026, 10, 29, 9, 59))
    assert enviados == [] and guardado == []                      # antes del 29 a las 10:00, nada
    prog._pdf_diferencias(dt.datetime(2026, 10, 29, 10, 0))
    assert enviados == [("111", "diferencias_20261001_20261029.pdf"), ("-100222", "diferencias_20261001_20261029.pdf")]
    assert guardado == [("ultimo_pdf_dif", "2026-10")]            # es el PDF del mes en curso, hasta el dia 29
    prog._pdf_diferencias(dt.datetime(2026, 10, 29, 18, 0))
    prog._pdf_diferencias(dt.datetime(2026, 10, 30, 10, 0))
    assert len(enviados) == 2                                     # ya salio este mes: no se repite
    prog._pdf_diferencias(dt.datetime(2026, 11, 29, 10, 0))
    assert len(enviados) == 4 and guardado[-1] == ("ultimo_pdf_dif", "2026-11")  # y el mes siguiente, otra vez


def test_el_pdf_en_febrero_sale_el_ultimo_dia_y_si_el_bot_estaba_apagado_sale_al_volver(monkeypatch):
    prog, enviados, _ = _programador(monkeypatch)
    prog._pdf_diferencias(dt.datetime(2027, 2, 27, 12, 0))
    assert enviados == []
    prog._pdf_diferencias(dt.datetime(2027, 2, 28, 10, 0))        # febrero no tiene 29: sale el 28
    assert len(enviados) == 2
    prog, enviados, _ = _programador(monkeypatch)
    prog._pdf_diferencias(dt.datetime(2026, 10, 31, 15, 0))       # el bot estaba apagado el 29: sale cuando vuelve
    assert len(enviados) == 2


def test_el_dia_y_la_hora_del_pdf_se_pueden_cambiar_o_apagar(monkeypatch):
    prog, enviados, _ = _programador(monkeypatch)
    monkeypatch.setenv("PDF_DIFERENCIAS_DIA", "off")
    prog._pdf_diferencias(dt.datetime(2026, 10, 29, 12, 0))
    assert enviados == []
    monkeypatch.setenv("PDF_DIFERENCIAS_DIA", "25")
    monkeypatch.setenv("PDF_DIFERENCIAS_HORA", "08:30")
    prog._pdf_diferencias(dt.datetime(2026, 10, 25, 8, 29))
    assert enviados == []
    prog._pdf_diferencias(dt.datetime(2026, 10, 25, 8, 30))
    assert len(enviados) == 2
    monkeypatch.setenv("PDF_DIFERENCIAS_DIA", "abc")             # valor raro: vuelve al 29
    import bot_telegram as bot
    assert bot.dia_pdf_diferencias() == 29


def test_si_telegram_rechaza_el_pdf_no_se_da_por_enviado_y_se_reintenta(monkeypatch):
    prog, enviados, guardado = _programador(monkeypatch, enviar_ok=False)
    prog._pdf_diferencias(dt.datetime(2026, 10, 29, 10, 0))
    assert guardado == [] and "ultimo_pdf_dif" not in prog.estado  # no quedo marcado: se vuelve a intentar
    assert prog.proximo_intento_pdf > 0                           # pero no en cada vuelta: espera unos minutos
    antes = len(enviados)
    prog._pdf_diferencias(dt.datetime(2026, 10, 29, 10, 1))
    assert len(enviados) == antes


def test_el_pdf_automatico_forma_parte_de_las_tareas_del_bot_y_de_la_ayuda(monkeypatch):
    import reportes as rp

    prog, enviados, _ = _programador(monkeypatch)
    monkeypatch.setattr(prog, "_resumen", lambda ahora: None)
    monkeypatch.setattr(prog, "_liquidacion", lambda ahora: None)
    monkeypatch.setattr(prog, "_liquidacion_final", lambda ahora: None)
    monkeypatch.setattr(prog, "_turnos", lambda ahora: None)
    monkeypatch.setattr(prog, "_incidencias", lambda ahora: None)
    prog.tick(dt.datetime(2026, 10, 29, 10, 0))
    assert len(enviados) == 2
    assert "el PDF de diferencias el día 29 de cada mes" in rp.AYUDA
