from datetime import datetime, time

import requests
from django.conf import settings
from django.utils import timezone

from .models import AlertEvent, StrategySnapshot


TRADING_WINDOWS = [
    (time(9, 1), time(10, 14)),
    (time(10, 31), time(11, 29)),
    (time(13, 31), time(14, 55)),
    (time(21, 1), time(22, 55)),
]


def is_trading_time(now_dt: datetime | None = None) -> bool:
    now_dt = now_dt or timezone.localtime()
    now_t = now_dt.time()
    return any(start <= now_t <= end for start, end in TRADING_WINDOWS)


def send_feishu_alert(text: str) -> None:
    webhook = getattr(settings, "FEISHU_WEBHOOK", "")
    if not webhook:
        return
    try:
        requests.post(
            webhook,
            json={"msg_type": "text", "content": {"text": text}},
            timeout=5,
        )
    except Exception:
        # 告警推送失败不影响主流程
        return


def create_alert_if_needed(
    *,
    symbol: str,
    alert_type: str,
    level: str,
    message: str,
    snapshot: StrategySnapshot | None = None,
    metadata: dict | None = None,
) -> AlertEvent | None:
    metadata = metadata or {}
    dedup_seconds = int(getattr(settings, "ALERT_DEDUP_SECONDS", 300))
    threshold = timezone.now() - timezone.timedelta(seconds=dedup_seconds)
    exists_recent = AlertEvent.objects.filter(
        symbol=symbol, alert_type=alert_type, created_at__gte=threshold
    ).exists()
    if exists_recent:
        return None

    alert = AlertEvent.objects.create(
        symbol=symbol,
        alert_type=alert_type,
        level=level,
        message=message,
        snapshot=snapshot,
        metadata=metadata,
    )
    send_feishu_alert(f"【量化预警】{message}")
    return alert


def process_snapshot_rules(snapshot: StrategySnapshot) -> list[AlertEvent]:
    alerts = []
    symbol = snapshot.symbol
    equity = float(snapshot.equity or 0.0)
    available = float(snapshot.available or 0.0)
    margin = float(snapshot.margin or 0.0)
    cur_pos = int(snapshot.cur_pos or 0)

    if equity > 0:
        available_ratio = available / equity
        margin_ratio = margin / equity
    else:
        available_ratio = 0.0
        margin_ratio = 0.0

    baseline = StrategySnapshot.objects.order_by("created_at").first()
    baseline_equity = float(baseline.equity) if baseline and baseline.equity > 0 else equity
    drawdown_ratio = 0.0
    if baseline_equity > 0 and equity > 0:
        drawdown_ratio = max(0.0, (baseline_equity - equity) / baseline_equity)

    if available_ratio < float(getattr(settings, "ALERT_AVAILABLE_RATIO", 0.2)):
        alert = create_alert_if_needed(
            symbol=symbol,
            alert_type="available_low",
            level=AlertEvent.LEVEL_WARN,
            message=f"{symbol} 可用资金占比过低: {available_ratio:.1%}",
            snapshot=snapshot,
            metadata={"available_ratio": available_ratio},
        )
        if alert:
            alerts.append(alert)

    if margin_ratio > float(getattr(settings, "ALERT_MARGIN_RATIO", 0.6)):
        alert = create_alert_if_needed(
            symbol=symbol,
            alert_type="margin_high",
            level=AlertEvent.LEVEL_WARN,
            message=f"{symbol} 保证金占比过高: {margin_ratio:.1%}",
            snapshot=snapshot,
            metadata={"margin_ratio": margin_ratio},
        )
        if alert:
            alerts.append(alert)

    if drawdown_ratio > float(getattr(settings, "ALERT_DRAWDOWN_RATIO", 0.03)):
        alert = create_alert_if_needed(
            symbol=symbol,
            alert_type="drawdown_high",
            level=AlertEvent.LEVEL_ERROR,
            message=f"{symbol} 权益回撤过大: {drawdown_ratio:.1%}",
            snapshot=snapshot,
            metadata={"drawdown_ratio": drawdown_ratio, "baseline_equity": baseline_equity},
        )
        if alert:
            alerts.append(alert)

    if (not is_trading_time()) and cur_pos != 0:
        alert = create_alert_if_needed(
            symbol=symbol,
            alert_type="position_off_hours",
            level=AlertEvent.LEVEL_WARN,
            message=f"{symbol} 非交易时段仍有持仓: {cur_pos} 手",
            snapshot=snapshot,
            metadata={"cur_pos": cur_pos},
        )
        if alert:
            alerts.append(alert)

    return alerts


def ensure_heartbeat_alert() -> AlertEvent | None:
    latest = StrategySnapshot.objects.order_by("-created_at").first()
    if not latest:
        return None
    delta_seconds = (timezone.now() - latest.created_at).total_seconds()
    timeout = int(getattr(settings, "HEARTBEAT_TIMEOUT_SECONDS", 120))
    if delta_seconds <= timeout:
        return None
    return create_alert_if_needed(
        symbol=latest.symbol,
        alert_type="heartbeat_timeout",
        level=AlertEvent.LEVEL_ERROR,
        message=f"{latest.symbol} 心跳超时: 最近一次上报距今 {int(delta_seconds)} 秒",
        snapshot=latest,
        metadata={"delta_seconds": delta_seconds},
    )
