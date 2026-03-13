from django.db import models


class StrategySnapshot(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    symbol = models.CharField(max_length=32, db_index=True)

    close = models.FloatField(default=0.0)
    ma_short = models.FloatField(default=0.0)
    ma_long = models.FloatField(default=0.0)
    rsi = models.FloatField(default=0.0)
    adx = models.FloatField(default=0.0)

    balance = models.FloatField(default=0.0)
    available = models.FloatField(default=0.0)
    margin = models.FloatField(default=0.0)
    float_profit = models.FloatField(default=0.0)
    equity = models.FloatField(default=0.0)

    cur_pos = models.IntegerField(default=0)
    held_symbols = models.TextField(default="无持仓", blank=True)

    class Meta:
        ordering = ["-created_at"]


class StrategyEvent(models.Model):
    LEVEL_INFO = "info"
    LEVEL_WARN = "warn"
    LEVEL_ERROR = "error"
    LEVEL_CHOICES = [
        (LEVEL_INFO, "info"),
        (LEVEL_WARN, "warn"),
        (LEVEL_ERROR, "error"),
    ]

    created_at = models.DateTimeField(auto_now_add=True)
    symbol = models.CharField(max_length=32, db_index=True)
    event_type = models.CharField(max_length=64, db_index=True)
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    message = models.TextField()
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class AlertEvent(models.Model):
    LEVEL_INFO = "info"
    LEVEL_WARN = "warn"
    LEVEL_ERROR = "error"
    LEVEL_CHOICES = [
        (LEVEL_INFO, "info"),
        (LEVEL_WARN, "warn"),
        (LEVEL_ERROR, "error"),
    ]

    created_at = models.DateTimeField(auto_now_add=True)
    symbol = models.CharField(max_length=32, db_index=True)
    alert_type = models.CharField(max_length=64, db_index=True)
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, default=LEVEL_WARN)
    message = models.TextField()
    snapshot = models.ForeignKey(
        StrategySnapshot, null=True, blank=True, on_delete=models.SET_NULL, related_name="alerts"
    )
    is_read = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
