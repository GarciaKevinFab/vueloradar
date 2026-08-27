"""Settings de Django para VUELORADAR PERÚ.

Toda la configuración sensible se lee de `.env` (ver `.env.example`).
Nunca hardcodear credenciales aquí.
"""

import os
from decimal import Decimal
from pathlib import Path

import dj_database_url
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    """Entero desde el entorno. Un valor no numerico cae al default en vez de
    tumbar el arranque por una variable mal escrita."""
    crudo = os.getenv(name, "").strip()
    try:
        return int(crudo) if crudo else default
    except ValueError:
        return default


def env_decimal(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


# ---------------------------------------------------------------- Django core
DEBUG = env_bool("DJANGO_DEBUG", True)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "Falta DJANGO_SECRET_KEY en el .env (obligatorio con DJANGO_DEBUG=False)."
        )
    SECRET_KEY = "dev-insecure-key-solo-para-desarrollo"

ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "django.contrib.humanize",
    # apps del proyecto
    "apps.flights",
    "apps.scraping",
    "apps.users",
    "apps.alerts",
    "apps.ai_analyst",
    "apps.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Justo despues de SecurityMiddleware, como pide la doc de WhiteNoise.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # 404.html y 500.html viven fuera de las apps: son del proyecto.
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.web.context_processors.site",
            ],
        },
    },
]

# ------------------------------------------------------------------ Base de datos
# Supabase vía Session Pooler (puerto 5432). Si no hay DATABASE_URL,
# se cae a SQLite local para poder trabajar/testear sin conexión.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_CONN_MAX_AGE = int(os.getenv("DB_CONN_MAX_AGE", "60"))

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=DB_CONN_MAX_AGE,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ------------------------------------------------------------ i18n / zona horaria
# Marca del sitio publico. Se cambia por entorno sin tocar plantillas.
SITE_NAME = os.getenv("SITE_NAME", "VueloRadar")
# Usuario del bot, para los enlaces profundos desde la web (web -> Telegram).
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "Vuelosradar_bot")

# URL publica, para armar los enlaces de confirmacion y baja en los correos.
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "https://vueloradar.com")

# Correo. Sin EMAIL_HOST configurado se imprime en consola: en desarrollo se
# ve el mensaje completo sin mandar nada a nadie.
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = env_int("EMAIL_PORT", 587)
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "VueloRadar <avisos@vueloradar.com>")

# Un formulario publico que manda correo es un vector de spam: se limita por IP.
EMAIL_ALERTS_PER_IP_PER_HOUR = env_int("EMAIL_ALERTS_PER_IP_PER_HOUR", 5)

# Cloudflare: token con permiso "Zone > Cache Purge" sobre la zona del sitio.
# Vacío en desarrollo: sin esto la purga simplemente no ocurre.
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ZONE_ID = os.getenv("CLOUDFLARE_ZONE_ID", "")

# Credito del estudio que construyo el sitio. Va por entorno para que el pie no
# haya que tocarlo si cambia el nombre o el dominio.
BUILDER_NAME = os.getenv("BUILDER_NAME", "Star Insights IT by SISAC")
BUILDER_URL = os.getenv("BUILDER_URL", "https://sisac.pe/")
# Ruta del logotipo DENTRO de los estaticos. El archivo esta versionado en el
# repo, asi que viene activado. Si se apunta a otro, tiene que existir en disco:
# `{% static %}` con manifiesto revienta en produccion si falta.
BUILDER_LOGO = os.getenv("BUILDER_LOGO", "web/sisac-logo.png")

# --------------------------------------------------------------------- ads
# Los huecos publicitarios estan maquetados siempre, pero el script de Google
# solo se carga si hay ID de editor. Sin ADSENSE_CLIENT el sitio no hace ni una
# peticion a terceros y sigue en cero JS: AdSense exige aprobacion previa y un
# script que carga sin cuenta aprobada paga la latencia sin mostrar nada.
ADSENSE_CLIENT = os.getenv("ADSENSE_CLIENT", "")
# Un slot por ubicacion: Google reporta por slot, y un solo ID para todo el
# sitio hace imposible saber que espacio rinde.
ADSENSE_SLOT_HOME = os.getenv("ADSENSE_SLOT_HOME", "")
ADSENSE_SLOT_ROUTE = os.getenv("ADSENSE_SLOT_ROUTE", "")

# Codigo de verificacion de Google Search Console (el `content` de la meta que
# da el panel). No es un secreto: identifica al dueno del sitio, no autoriza
# nada. Sin Search Console no hay forma de pedir indexacion ni de ver que
# consultas traen visitas.
GOOGLE_SITE_VERIFICATION = os.getenv("GOOGLE_SITE_VERIFICATION", "")

LANGUAGE_CODE = "es"
TIME_ZONE = "America/Lima"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# `collectstatic` deja aca los estaticos del admin; WhiteNoise los sirve
# comprimidos y con hash, y Cloudflare los cachea en el borde.
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ------------------------------------------------------------------ Redis / cache
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Nota: el backend Redis de Django no tiene "ignorar errores"; quien use el
# cache debe tolerar que Redis esté caído (ver apps/scraping/fx.py).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# ------------------------------------------------------------------- Scraping / FX
# Una hora, no un dia: para un producto que mide precios, una tasa de ayer es
# un dato inventado. No se pide por conversion porque un barrido son ~1.300
# consultas y eso agotaria la cuota de una API gratuita.
FX_CACHE_TTL_SECONDS = env_int("FX_CACHE_TTL_SECONDS", 60 * 60)
# Cuanto puede envejecer la ultima tasa buena antes de dejar de usarse. Pasado
# ese punto la conversion falla y la oferta se descarta: es preferible perder
# un precio a guardar uno calculado con una tasa vieja.
FX_LAST_GOOD_MAX_AGE_HOURS = env_int("FX_LAST_GOOD_MAX_AGE_HOURS", 24)

SCRAPE_DELAY_MIN = float(os.getenv("SCRAPE_DELAY_MIN", "3"))
SCRAPE_DELAY_MAX = float(os.getenv("SCRAPE_DELAY_MAX", "8"))

# Aeropuerto hub del mercado peruano: todas las conexiones pasan por aquí.
HUB_AIRPORT = "LIM"
# Tiempo mínimo de conexión en el hub para armar itinerarios sintéticos.
MIN_CONNECTION_MINUTES = 120

# ---------------------------------------------------------------- Integraciones
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ------------------------------------------------------------------------ Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)s %(name)s | %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "bot": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # aiogram loguea cada request a la API de Telegram en INFO: demasiado ruido.
        "aiogram": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}


# ============================================================== Celery (Fase 2)
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True

# Si Redis todavía no levantó cuando arranca el worker, reintenta en vez de morir.
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]

# Un worker no confirma la task hasta terminarla: si el proceso muere a mitad
# de un scraping, la task se re-encola en vez de perderse.
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_RESULT_EXPIRES = 60 * 60 * 24

CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "apps.scraping.tasks.scan_route_date": {"queue": "scraping"},
    "apps.scraping.tasks.scan_all_monitored": {"queue": "default"},
    "apps.scraping.tasks.compute_route_stats": {"queue": "default"},
    "apps.scraping.tasks.purge_old_offers": {"queue": "default"},
    "apps.scraping.tasks.pause_source": {"queue": "default"},
    "apps.alerts.tasks.send_alert_notification": {"queue": "default"},
    "apps.alerts.tasks.send_weekly_digest": {"queue": "default"},
    "apps.scraping.maintenance.backup_database": {"queue": "default"},
    "apps.scraping.maintenance.system_healthcheck": {"queue": "default"},
}

# --- Horizonte y granularidad del barrido (CLAUDE.md secc. 7) ---
SCAN_DAILY_HORIZON_DAYS = 14   # próximos 14 días: todos los días
SCAN_MAX_HORIZON_DAYS = 60     # hasta el día 60: cada N días
SCAN_SPARSE_STEP_DAYS = 3

# --- Resiliencia de fuentes ---
SOURCE_MAX_CONSECUTIVE_FAILURES = 3
SOURCE_PAUSE_SECONDS = 60 * 30      # 30 min de castigo tras 3 fallos seguidos
SOURCE_LOCK_TIMEOUT_SECONDS = 120   # techo del lock: nunca dejar la fuente trabada

# --- Retención ---
OFFER_RETENTION_DAYS = 90   # los snapshots no se purgan nunca: son el activo
ROUTE_STATS_WINDOW_DAYS = 30

CELERY_BEAT_SCHEDULE = {
    "barrido-manana": {
        "task": "apps.scraping.tasks.scan_all_monitored",
        "schedule": crontab(hour=6, minute=0),
    },
    "barrido-tarde": {
        "task": "apps.scraping.tasks.scan_all_monitored",
        "schedule": crontab(hour=18, minute=0),
    },
    "purga-ofertas-viejas": {
        "task": "apps.scraping.tasks.purge_old_offers",
        "schedule": crontab(hour=3, minute=0, day_of_week=0),
    },
    "backup-diario": {
        "task": "apps.scraping.maintenance.backup_database",
        "schedule": crontab(hour=4, minute=0),
    },
    "chequeo-de-salud": {
        "task": "apps.scraping.maintenance.system_healthcheck",
        "schedule": crontab(minute="*/30"),
    },
    "resumen-semanal": {
        "task": "apps.alerts.tasks.send_weekly_digest",
        "schedule": crontab(hour=10, minute=0, day_of_week=0),
    },
}


# =============================================================== Bot (Fase 3)
# Límite del plan gratuito. Premium es ilimitado.
FREE_DAILY_SEARCHES = int(os.getenv("FREE_DAILY_SEARCHES", "10"))

# Cuántas ofertas se muestran en el mensaje de resultados.
BOT_RESULTS_LIMIT = int(os.getenv("BOT_RESULTS_LIMIT", "5"))

# Búsqueda flexible: tope de días alrededor de la fecha objetivo. Cada día
# extra son 2 consultas más al scraper, así que el techo es duro.
BOT_MAX_FLEXIBLE_DAYS = 3

# Cuántas búsquedas del bot pueden correr a la vez. El lock por fuente sigue
# serializando el scraping; esto solo evita quedarse sin hilos.
BOT_SEARCH_WORKERS = int(os.getenv("BOT_SEARCH_WORKERS", "4"))

# ------------------------------------------------------------- IA (nl_parser)
# CLAUDE.md fija sonnet-4-6 para la capa de IA. Sube a claude-opus-5 si querés
# más precisión en el parseo de lenguaje natural.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "300"))
NL_PARSER_CACHE_TTL = 60 * 60  # respuestas idénticas se cachean 1h


# ================================================== Router de IA (Fase 4)
# Cadena de respaldo: se intenta en orden hasta que uno responda. Si todos
# fallan el sistema sigue sin veredicto — jamás se bloquea una alerta por IA.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# llama-3.3-70b-versatile (el del plan original) fue retirado por Groq en 2026.
# Verificar con: client.models.list() si este también desaparece.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Solo para desarrollo local. Si está vacío, Ollama ni se intenta.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
# Circuit breaker: no quemar 20s de timeout por llamada si un proveedor se cayó.
LLM_CIRCUIT_FAILURES = 3
LLM_CIRCUIT_OPEN_SECONDS = 60 * 15

# ============================================================ Alertas (Fase 4)
FREE_MAX_ALERTS = int(os.getenv("FREE_MAX_ALERTS", "2"))
PREMIUM_MAX_ALERTS = int(os.getenv("PREMIUM_MAX_ALERTS", "20"))

# Anti-spam: ni más de una notificación cada 12h por alerta, ni re-disparo si
# el precio no bajó al menos un 5% respecto del último aviso.
ALERT_COOLDOWN_HOURS = 12
ALERT_MIN_DROP_PCT = Decimal("5")

# Backup offsite en R2 (S3-compatible). Sin esto el dump queda solo en disco,
# que en Railway es efímero. Ver apps/scraping/offsite.py.
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.getenv("R2_BUCKET", "")
R2_PREFIX = os.getenv("R2_PREFIX", "backups/")

# Impuestos de vuelos domésticos PE, para normalizar proveedores que publican
# tarifa base (ver apps/scraping/taxes.py). Verificados el 2026-08-23; la TUUA
# la fija el operador del aeropuerto y cambia, así que vive en settings.
IGV_RATE = Decimal("0.18")
TUUA_NACIONAL_PEN = Decimal("30.47")

# deal_detected: precio en el 10% más barato observado (p25 x 0.90), y solo
# con histórico suficiente para que el percentil signifique algo.
DEAL_P25_FACTOR = Decimal("0.90")
DEAL_MIN_SAMPLES = 20

# Veredicto del analista: cacheado por ruta+fecha+banda de precio.
VERDICT_CACHE_TTL = 60 * 60 * 6
VERDICT_PRICE_BAND = Decimal("10")
VERDICT_MIN_SAMPLES = 10


# ========================================================= Producción (Fase 5)
# --- Scrapers directos de aerolínea (Playwright) ---
# Pesados y frágiles: NO entran al barrido masivo. Se usan para verificar el
# precio antes de mandar una alerta y en rutas marcadas explícitamente.
ENABLE_SKY_SCRAPER = env_bool("ENABLE_SKY_SCRAPER", False)
ENABLE_JETSMART_SCRAPER = env_bool("ENABLE_JETSMART_SCRAPER", False)

DIRECT_SCRAPER_TIMEOUT_MS = int(os.getenv("DIRECT_SCRAPER_TIMEOUT_MS", "90000"))
DIRECT_SCRAPER_SCREENSHOT_DIR = os.getenv("DIRECT_SCRAPER_SCREENSHOT_DIR", "/tmp/scraper_fails")

# Si el precio directo difiere más que esto del de Google, gana el directo.
ALERT_PRICE_DISCREPANCY_PCT = Decimal("10")
# Verificar contra la aerolínea antes de mandar una alerta deal_detected.
VERIFY_DEALS_WITH_DIRECT_SCRAPER = env_bool("VERIFY_DEALS_WITH_DIRECT_SCRAPER", False)

# --- Bot ---
BOT_MODE = os.getenv("BOT_MODE", "polling")            # polling | webhook
BOT_WEBHOOK_URL = os.getenv("BOT_WEBHOOK_URL", "")     # https://dominio/telegram/<secreto>
BOT_WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", "/telegram/webhook")
BOT_WEBHOOK_HOST = os.getenv("BOT_WEBHOOK_HOST", "0.0.0.0")
BOT_WEBHOOK_PORT = int(os.getenv("BOT_WEBHOOK_PORT", "8080"))
BOT_WEBHOOK_SECRET = os.getenv("BOT_WEBHOOK_SECRET", "")

# Heartbeat: el polling lo toca cada 60s y el healthcheck de Docker mira su mtime.
BOT_HEARTBEAT_FILE = os.getenv("BOT_HEARTBEAT_FILE", "/tmp/vueloradar_bot_heartbeat")
BOT_HEARTBEAT_INTERVAL = 60

# Techo global de búsquedas on-demand simultáneas en todo el sistema.
BOT_GLOBAL_SEARCH_LIMIT = int(os.getenv("BOT_GLOBAL_SEARCH_LIMIT", "20"))
BOT_GLOBAL_SLOT_TTL = 300   # un slot huérfano se libera solo a los 5 min

# --- Backups ---
# El free tier de Supabase no da backups restaurables a demanda: el pg_dump
# diario es la única copia propia del dato.
BACKUP_DIR = os.getenv("BACKUP_DIR", "/backups")
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "14"))
PG_DUMP_PATH = os.getenv("PG_DUMP_PATH", "pg_dump")

# --- Salud del sistema ---
HEALTH_MAX_SNAPSHOT_AGE_HOURS = 8    # sin snapshots nuevos = algo se rompió
HEALTH_MAX_PAUSE_HOURS = 2           # fuente pausada demasiado tiempo

# --- Observabilidad ---
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
LOG_FORMAT = os.getenv("LOG_FORMAT", "console")   # console | json
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

if LOG_FORMAT == "json":
    LOGGING["formatters"]["json"] = {
        "()": "pythonjsonlogger.json.JsonFormatter",
        "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
    }
    LOGGING["handlers"]["console"]["formatter"] = "json"

for _logger in ("apps", "bot"):
    LOGGING["loggers"][_logger]["level"] = LOG_LEVEL

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
        send_default_pii=False,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
    )


# --------------------------------------------- Endurecimiento con DEBUG=False
# Solo aplica en producción: con DEBUG=True forzar HTTPS rompe el desarrollo
# local, donde no hay certificado.
if not DEBUG:
    # El admin va detrás de nginx con TLS (ver DEPLOY.md).
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Un año, incluyendo subdominios. Ojo: es difícil de revertir, los
    # navegadores lo cachean. Arrancá con 3600 y subilo cuando estés seguro.
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

    CSRF_TRUSTED_ORIGINS = [
        o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
    ]
