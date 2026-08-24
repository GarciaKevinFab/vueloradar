from django.contrib import admin

from .models import Alert, AlertTrigger


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        "id", "user", "route", "alert_type", "target_price_pen",
        "flight_date", "is_active", "created_at",
    )
    list_filter = ("alert_type", "is_active", "route")
    search_fields = ("user__telegram_id", "user__username")
    list_select_related = ("user", "route", "route__origin", "route__destination")


@admin.register(AlertTrigger)
class AlertTriggerAdmin(admin.ModelAdmin):
    list_display = ("alert", "price_pen", "message_sent", "triggered_at")
    list_filter = ("message_sent", "triggered_at")
    date_hierarchy = "triggered_at"
    list_select_related = ("alert", "alert__route")
