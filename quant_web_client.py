import logging
from typing import Any

import requests

log = logging.getLogger(__name__)


class QuantWebReporter:
    def __init__(self, *, enabled: bool, base_url: str, token: str, timeout: float = 3.0):
        self.enabled = enabled
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _post(self, endpoint: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            requests.post(
                f"{self.base_url}{endpoint}",
                json=payload,
                headers={"X-API-Token": self.token},
                timeout=self.timeout,
            )
        except Exception as e:
            # 上报失败不影响策略主流程
            log.warning(f"量化看板上报失败: {e}")

    def post_snapshot(self, payload: dict[str, Any]) -> None:
        self._post("/api/ingest/snapshot", payload)

    def post_event(
        self, *, symbol: str, event_type: str, message: str, level: str = "info", payload: dict[str, Any] | None = None
    ) -> None:
        self._post(
            "/api/ingest/event",
            {
                "symbol": symbol,
                "event_type": event_type,
                "level": level,
                "message": message,
                "payload": payload or {},
            },
        )
