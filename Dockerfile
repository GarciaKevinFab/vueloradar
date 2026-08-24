# ============================================================ Etapa de build
# Las dependencias se compilan acá y solo viajan los wheels a la imagen final.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir=/wheels -r requirements.txt


# ========================================================== Imagen definitiva
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# libpq5 para psycopg2, postgresql-client para el pg_dump del backup diario,
# curl para los healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels requirements.txt

# Chromium para los scrapers directos. Es lo que más pesa de la imagen; si no
# vas a usar Sky/JetSmart, comentá esta línea y ahorrás ~400 MB.
RUN playwright install --with-deps chromium

# Usuario sin privilegios: si alguien escapa del proceso, no es root.
RUN groupadd --gid 1000 vueloradar \
    && useradd --uid 1000 --gid vueloradar --create-home vueloradar \
    && mkdir -p /app /backups /tmp/scraper_fails \
    && chown -R vueloradar:vueloradar /app /backups /tmp/scraper_fails /opt/playwright

WORKDIR /app
COPY --chown=vueloradar:vueloradar . .

USER vueloradar

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
