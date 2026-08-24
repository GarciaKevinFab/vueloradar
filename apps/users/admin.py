from django.contrib import admin

from .models import TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = (
        "telegram_id", "username", "first_name", "plan",
        "searches_today", "searches_reset_date", "last_active_at", "created_at",
    )
    list_filter = ("plan", "searches_reset_date")
    search_fields = ("telegram_id", "username", "first_name")
    readonly_fields = ("created_at",)
    actions = ("hacer_premium", "hacer_free")

    @admin.action(description="Pasar a plan Premium")
    def hacer_premium(self, request, queryset):
        n = queryset.update(plan=TelegramUser.PLAN_PREMIUM)
        self.message_user(request, f"{n} usuarios pasados a Premium.")

    @admin.action(description="Pasar a plan Gratis")
    def hacer_free(self, request, queryset):
        n = queryset.update(plan=TelegramUser.PLAN_FREE)
        self.message_user(request, f"{n} usuarios pasados a Gratis.")
