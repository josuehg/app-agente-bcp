# Guía de configuración — Control de Agentes BCP

Esta guía te lleva paso a paso desde cero hasta tener la app funcionando con tus datos reales y accesible desde un link para tus 6 locales.

## 1. Crear el proyecto en Google Cloud y habilitar las APIs

1. Entra a [console.cloud.google.com](https://console.cloud.google.com) con tu cuenta de Google.
2. Crea un proyecto nuevo (arriba a la izquierda, "Select a project" → "New Project"). Ponle un nombre como `agentes-bcp`.
3. Con el proyecto seleccionado, ve al buscador superior y activa estas dos APIs (una por una, dale "Enable" en cada una):
   - **Google Sheets API**
   - **Google Drive API**

## 2. Crear la cuenta de servicio (el "usuario robot")

1. En el menú, ve a **IAM & Admin → Service Accounts**.
2. Click en **Create Service Account**. Nombre: `agente-bcp-app`. Sigue los pasos con las opciones por defecto (no necesitas asignarle roles especiales a nivel de proyecto).
3. Ya creada, entra a la cuenta de servicio → pestaña **Keys** → **Add Key → Create new key → JSON**. Se descarga un archivo `.json` a tu computadora. **Guárdalo bien, no lo compartas ni lo subas a internet.**
4. Abre ese archivo JSON con un editor de texto. Copia el valor de `client_email` (algo como `agente-bcp-app@agentes-bcp-123456.iam.gserviceaccount.com`) — lo vas a necesitar en el siguiente paso.

## 3. Crear el Google Sheet y compartirlo con la cuenta de servicio

1. Crea una hoja de cálculo nueva en Google Sheets, llámala por ejemplo `Control Agentes BCP`. Puedes dejarla vacía — la app crea las pestañas `Registros` y `Config` automáticamente la primera vez que corre.
2. Dale clic en **Compartir** y agrega el `client_email` del paso anterior con permiso de **Editor**.
3. Copia el **ID de la hoja**: es la parte de la URL entre `/d/` y `/edit`.
   `https://docs.google.com/spreadsheets/d/ESTE-ES-EL-ID/edit` → guárdalo, es tu `spreadsheet_id`.

## 4. Crear la carpeta de Drive para las fotos

1. En Google Drive, crea una carpeta nueva, por ejemplo `Fotos Agentes BCP`.
2. Compártela también con el mismo `client_email`, permiso **Editor**.
3. Copia el **ID de la carpeta** de la URL: `https://drive.google.com/drive/folders/ESTE-ES-EL-ID` → es tu `drive_folder_id`.

## 5. Configurar los secrets de la app

1. Dentro de la carpeta del proyecto, copia `.streamlit/secrets.toml.example` a `.streamlit/secrets.toml`.
2. Rellena `spreadsheet_id` y `drive_folder_id` con lo que copiaste arriba.
3. Elige un `dashboard_pin` (un número simple que solo tú conozcas, para que el personal de tienda no entre al dashboard).
4. Abre el archivo `.json` que descargaste en el paso 2 y copia cada valor a la sección `[gcp_service_account]` de `secrets.toml` (los nombres de los campos son iguales en ambos archivos).

**Importante:** `secrets.toml` nunca se sube a GitHub (ya está en `.gitignore`). Solo vive en tu computadora y, más adelante, dentro del panel de Streamlit Cloud.

## 6. Probar la app en tu computadora

Necesitas tener Python instalado (3.9 o más nuevo). Luego, en una terminal, dentro de la carpeta del proyecto:

```bash
pip install -r requirements.txt
streamlit run Bienvenida.py
```

Se abre en tu navegador en `http://localhost:8501`. Prueba:
- Ir a **Registro**, elegir un local, llenar una Apertura de prueba, y guardarla.
- Revisar que apareció una fila nueva en la hoja `Registros` de tu Google Sheet.
- Ir a **Dashboard**, ingresar tu PIN, y confirmar que el registro aparece.

## 7. Ajustar los locales y el fondo mínimo

Abre la pestaña `Config` de tu Google Sheet. La app la crea con 6 filas `Local 1` a `Local 6` y fondo mínimo S/ 5000. Edita ahí mismo (sin tocar código):
- La columna `local` → pon los nombres reales de tus 6 tiendas.
- La columna `fondo_minimo` → el monto de referencia de cada una.

## 8. Publicar la app para que las 6 tiendas puedan entrar

1. Sube la carpeta del proyecto a un repositorio de GitHub (puede ser privado). Verifica que `secrets.toml` (el real, con tus datos) **no** esté incluido — solo debe subirse `secrets.toml.example`.
2. Entra a [share.streamlit.io](https://share.streamlit.io) con tu cuenta (puedes usar tu cuenta de Google), conecta tu repositorio de GitHub, y selecciona `Bienvenida.py` como archivo principal.
3. En la configuración de la app, sección **Secrets**, pega el contenido completo de tu `secrets.toml` real (con tus datos, no el ejemplo).
4. Al desplegar, Streamlit te da una URL pública fija (algo como `https://agentes-bcp.streamlit.app`). Ese es el link que compartes con tus 6 locales para el **Registro**, y que usas tú para el **Dashboard**.

## Notas finales

- Las fotos suben a Google Drive con permiso "cualquiera con el link puede ver" — no se indexan en buscadores, pero cualquiera con la URL exacta puede abrirlas. Es el balance más simple para que el personal pueda revisarlas sin darles acceso a toda la carpeta de Drive.
- El PIN del dashboard es una protección simple (evita que cualquiera con el link mire el consolidado), no un sistema de seguridad robusto. Si en el futuro necesitas login real por usuario, es el siguiente paso natural a construir.
- El plan gratuito de Google Sheets/Drive no tiene el límite de 1,000 filas que tiene Airtable, así que no deberías necesitar migrar de hoja en el futuro por volumen de datos.
