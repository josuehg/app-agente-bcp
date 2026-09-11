# Imagen para desplegar la app fuera de Streamlit Community Cloud
# (Railway, Google Cloud Run, o cualquier host que corra un Dockerfile).
#
# POR QUE ESTE ARCHIVO: en Streamlit Cloud no elegimos el sistema
# operativo ni la version exacta de Python del servidor -- solo un
# numero de version, y aun asi tuvimos varios crashes nativos
# (pyarrow, Pillow) que no se pudieron resolver desde el codigo de la
# app. Con Docker fijamos TODO el entorno nosotros: Python 3.12 sobre
# Debian "slim", nada mas.
FROM python:3.12-slim

# Pillow y otras librerias nativas necesitan estas librerias del sistema
# para leer JPEG/PNG. Sin esto, pip instala pillow pero falla en runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias primero (capa separada): si solo cambia el
# codigo de la app, Docker no vuelve a instalar todo, solo copia los
# archivos nuevos -- deploys mas rapidos.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# El "secrets.toml" real NUNCA va en la imagen (ver .dockerignore). Este
# script lo arma en el arranque a partir de una variable de entorno
# (ver docker-entrypoint.sh).
RUN chmod +x docker-entrypoint.sh

# Railway y Cloud Run avisan en que puerto escuchar con la variable de
# entorno PORT; 8080 es el valor por defecto si no la definen.
ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["./docker-entrypoint.sh"]
