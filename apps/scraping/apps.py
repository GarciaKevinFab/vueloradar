from django.apps import AppConfig


class ScrapingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.scraping"
    label = "scraping"
    verbose_name = "Scraping"
