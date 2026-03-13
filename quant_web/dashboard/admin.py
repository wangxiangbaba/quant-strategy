from django.contrib import admin
from .models import AlertEvent, StrategyEvent, StrategySnapshot


@admin.register(StrategySnapshot)
class StrategySnapshotAdmin(admin.ModelAdmin):
    list_display = ("created_at", "symbol", "equity", "cur_pos", "close", "rsi", "adx")
    list_filter = ("symbol",)
    search_fields = ("symbol", "held_symbols")


@admin.register(StrategyEvent)
class StrategyEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "symbol", "event_type", "level")
    list_filter = ("symbol", "event_type", "level")
    search_fields = ("symbol", "message")


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "symbol", "alert_type", "level", "is_read")
    list_filter = ("symbol", "alert_type", "level", "is_read")
    search_fields = ("symbol", "message")
