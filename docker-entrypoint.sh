#!/usr/bin/env bash
# Arranca la app dentro del contenedor.
#
# Los "secrets" (credenciales de Google, IDs de Sheet/Drive, PIN del
# dashboard) NUNCA van dentro de la imagen de Docker -- se pasan como
# UNA variable de entorno llamada STREAMLIT_SECRETS, con el mismo
# contenido que ya usas en la caja "Secrets" de Streamlit Cloud (formato
# TOML). Este script la escribe a .streamlit/secrets.toml justo antes de
# arrancar, para que st.secrets la lea igual que siempre.
set -e

if [ -n "$STREAMLIT_SECRETS" ]; then
    mkdir -p .streamlit
    printf '%s' "$STREAMLIT_SECRETS" > .streamlit/secrets.toml
fi

# Bot de Telegram con comandos (/fondo, /corte, ...): un proceso aparte que escucha a Telegram.
# Si se cae, se vuelve a levantar solo; si todavia no hay TELEGRAM_BOT_TOKEN, queda en espera.
( while true; do python bot_telegram.py; sleep 10; done ) &

exec streamlit run Bienvenida.py \
    --server.port="${PORT:-8080}" \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
