"""Settings para la suite de tests.

Fuerza SQLite en memoria y cache local: los tests nunca tocan Supabase,
Redis ni la red.
"""

from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "vueloradar-tests",
    }
}

# Sin esperas artificiales en tests.
SCRAPE_DELAY_MIN = 0
SCRAPE_DELAY_MAX = 0

# Celery corre inline: no hay broker ni workers en los tests.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False

# Sin credenciales de Telegram: send_admin_alert corta antes de tocar la red.
TELEGRAM_TOKEN = ""
TELEGRAM_ADMIN_CHAT_ID = ""
