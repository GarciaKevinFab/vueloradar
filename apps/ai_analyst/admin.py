from django.contrib import admin

from .models import AIUsageLog


@admin.register(AIUsageLog)
class AIUsageLogAdmin(admin.ModelAdmin):
    list_display = ("date", "provider", "calls", "failures", "input_tokens", "output_tokens")
    list_filter = ("provider", "date")
    date_hierarchy = "date"
