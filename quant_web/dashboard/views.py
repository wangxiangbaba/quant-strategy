import json

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import AlertEvent, StrategyEvent, StrategySnapshot
from .services import create_alert_if_needed, ensure_heartbeat_alert, process_snapshot_rules


def _is_authorized(request) -> bool:
    expected = getattr(settings, "INGEST_API_TOKEN", "")
    if not expected:
        return True
    token = request.headers.get("X-API-Token", "")
    if token:
        return token == expected
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Token "):
        return auth.split(" ", 1)[1].strip() == expected
    return False


def _parse_json_body(request):
    try:
        raw = request.body.decode("utf-8") or "{}"
        return json.loads(raw)
    except Exception:
        return {}


def dashboard_page(request):
    return render(request, "dashboard/index.html")


@csrf_exempt
@require_POST
def ingest_snapshot(request):
    if not _is_authorized(request):
        return HttpResponseForbidden("invalid token")

    payload = _parse_json_body(request)
    symbol = payload.get("symbol", "")
    if not symbol:
        return JsonResponse({"ok": False, "error": "symbol required"}, status=400)

    snapshot = StrategySnapshot.objects.create(
        symbol=symbol,
        close=float(payload.get("close", 0.0)),
        ma_short=float(payload.get("ma_short", 0.0)),
        ma_long=float(payload.get("ma_long", 0.0)),
        rsi=float(payload.get("rsi", 0.0)),
        adx=float(payload.get("adx", 0.0)),
        balance=float(payload.get("balance", 0.0)),
        available=float(payload.get("available", 0.0)),
        margin=float(payload.get("margin", 0.0)),
        float_profit=float(payload.get("float_profit", 0.0)),
        equity=float(payload.get("equity", 0.0)),
        cur_pos=int(payload.get("cur_pos", 0)),
        held_symbols=payload.get("held_symbols", "无持仓"),
    )
    alerts = process_snapshot_rules(snapshot)
    return JsonResponse({"ok": True, "snapshot_id": snapshot.id, "alerts_created": len(alerts)})


@csrf_exempt
@require_POST
def ingest_event(request):
    if not _is_authorized(request):
        return HttpResponseForbidden("invalid token")

    payload = _parse_json_body(request)
    symbol = payload.get("symbol", "")
    event_type = payload.get("event_type", "")
    message = payload.get("message", "")
    level = payload.get("level", StrategyEvent.LEVEL_INFO)
    event_payload = payload.get("payload", {})

    if not symbol or not event_type or not message:
        return JsonResponse({"ok": False, "error": "symbol/event_type/message required"}, status=400)

    event = StrategyEvent.objects.create(
        symbol=symbol,
        event_type=event_type,
        level=level,
        message=message,
        payload=event_payload if isinstance(event_payload, dict) else {},
    )

    high_risk_event_types = {"melt_down", "system_exception", "restart"}
    if event_type in high_risk_event_types:
        create_alert_if_needed(
            symbol=symbol,
            alert_type=f"event_{event_type}",
            level=AlertEvent.LEVEL_ERROR if level == StrategyEvent.LEVEL_ERROR else AlertEvent.LEVEL_WARN,
            message=f"{symbol} 事件告警: {event_type} | {message}",
            snapshot=StrategySnapshot.objects.order_by("-created_at").first(),
            metadata={"event_id": event.id},
        )

    return JsonResponse({"ok": True, "event_id": event.id})


@require_GET
def dashboard_summary(request):
    ensure_heartbeat_alert()
    latest = StrategySnapshot.objects.order_by("-created_at").first()
    unread_count = AlertEvent.objects.filter(is_read=False).count()
    latest_events = list(
        StrategyEvent.objects.order_by("-created_at").values(
            "id", "created_at", "symbol", "event_type", "level", "message"
        )[:20]
    )
    latest_alerts = list(
        AlertEvent.objects.order_by("-created_at").values(
            "id", "created_at", "symbol", "alert_type", "level", "message", "is_read"
        )[:20]
    )

    latest_data = None
    if latest:
        latest_data = {
            "id": latest.id,
            "created_at": latest.created_at,
            "symbol": latest.symbol,
            "close": latest.close,
            "ma_short": latest.ma_short,
            "ma_long": latest.ma_long,
            "rsi": latest.rsi,
            "adx": latest.adx,
            "balance": latest.balance,
            "available": latest.available,
            "margin": latest.margin,
            "float_profit": latest.float_profit,
            "equity": latest.equity,
            "cur_pos": latest.cur_pos,
            "held_symbols": latest.held_symbols,
        }

    return JsonResponse(
        {
            "ok": True,
            "latest_snapshot": latest_data,
            "unread_alert_count": unread_count,
            "latest_events": latest_events,
            "latest_alerts": latest_alerts,
        }
    )


@require_GET
def dashboard_timeseries(request):
    limit = int(request.GET.get("limit", "200"))
    snapshots = list(
        StrategySnapshot.objects.order_by("-created_at").values(
            "created_at", "close", "ma_short", "ma_long", "rsi", "adx", "equity"
        )[:limit]
    )
    snapshots.reverse()
    return JsonResponse({"ok": True, "series": snapshots})


@require_GET
def dashboard_alerts(request):
    limit = int(request.GET.get("limit", "100"))
    rows = list(
        AlertEvent.objects.order_by("-created_at").values(
            "id", "created_at", "symbol", "alert_type", "level", "message", "is_read"
        )[:limit]
    )
    return JsonResponse({"ok": True, "alerts": rows})


@csrf_exempt
@require_POST
def mark_alert_read(request, alert_id: int):
    alert = get_object_or_404(AlertEvent, id=alert_id)
    alert.is_read = True
    alert.save(update_fields=["is_read"])
    return JsonResponse({"ok": True, "alert_id": alert.id})
