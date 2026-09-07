# Control de Agentes BCP

App interna (Streamlit) para llevar el control diario del **Agente BCP** en
los locales de **BCD Farma** y **Carpe Diem Botica**: aperturas y cierres
de caja por turno, fotos de vouchers, cuadre Apertura vs Cierre, encuestas
NPS con incentivo, campañas del BCP y un centro de ayuda.

Los datos viven en una hoja de **Google Sheets** y las fotos en **Google
Drive** (no hay base de datos que administrar).

## Pantallas

| Pantalla | Para quién | Qué hace |
|---|---|---|
| **Bienvenida** | Todos | Portada y explicación del menú |
| **Registro** | Personal de tienda | Registra la Apertura o el Cierre de caja del turno (desglose de efectivo + tarjeta + fotos de vouchers) |
| **Historial** | Personal de tienda | Revisa los registros y fotos **de su propio local** y el cuadre por turno |
| **Encuestas** | Personal de tienda | Registra encuestas NPS calificadas 9 o 10 (incentivo S/ 10) |
| **Campañas** | Todos | Campañas vigentes del BCP (las carga el dueño en la hoja) |
| **Ayuda** | Todos | Videos y guías de uso (las carga el dueño en la hoja) |
| **Dashboard** | Administración (PIN) | Consolidado de los 6 locales: alertas de fondo, cuadre por turno, incentivos pendientes |

El acceso del personal es por **PIN de local** (columna `pin` de la hoja
`Config`); el Dashboard usa un PIN aparte (`dashboard_pin` en los secrets).

## Estructura

```
Bienvenida.py        # punto de entrada + mapa de navegacion (st.navigation)
pages/               # una pantalla por archivo
sheets_utils.py      # toda la conexion con Google Sheets + Drive
cuadre.py            # logica pura del cuadre por turno (sin Google, testeable)
tests/               # pruebas de cuadre.py
.streamlit/          # config.toml (tema) + secrets.toml.example (plantilla)
assets/              # logos
```

## Configuración

La guía completa —crear el proyecto de Google Cloud, la cuenta de
servicio, la hoja, la carpeta de Drive y el deploy en Streamlit Cloud—
está en **[SETUP.md](SETUP.md)**.

## Correr en local

```bash
pip install -r requirements.txt
# copia .streamlit/secrets.toml.example a .streamlit/secrets.toml y llena tus datos (ver SETUP.md)
streamlit run Bienvenida.py
```

## Pruebas

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

## Cuadre por turno (tramos)

Un turno puede tener **cierres parciales**: se cierra la caja, se retira o
ingresa efectivo a propósito, y se vuelve a abrir. Cada par
Apertura → Cierre es un **tramo** y se mide contra su propia Apertura, así
que los movimientos hechos a propósito entre tramos no cuentan como
descuadre. La misma persona abre y cierra su tramo; si el Cierre lo hace
otra persona se registra con un motivo y la diferencia se le atribuye a
quien abrió. No se permite guardar dos Aperturas seguidas ni un Cierre sin
Apertura. Toda esa lógica está en `cuadre.py` (con pruebas en `tests/`).

## Notas

- El servidor de Streamlit Cloud corre en UTC; la app guarda fecha y hora
  en horario de Perú (`America/Lima`) — ver `ahora_local()` / `hoy_local()`
  en `sheets_utils.py`.
- `secrets.toml` (el real) **nunca** se sube al repo: está en `.gitignore`
  y en Streamlit Cloud se pega en la sección *Secrets* del panel.
