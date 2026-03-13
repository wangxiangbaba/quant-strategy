"""
企业微信机器人消息推送模块
参考: https://developer.work.weixin.qq.com/document/path/91770
"""

import json
import logging
import requests

log = logging.getLogger(__name__)

WECHAT_CONFIG = {
    "enabled": True,
    "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fb2670a6-bde4-4824-bc60-e7a0ab9b776b",
}


def init(webhook: str = None, enabled: bool = None) -> None:
    if webhook is not None:
        WECHAT_CONFIG["webhook"] = webhook
    if enabled is not None:
        WECHAT_CONFIG["enabled"] = enabled


def wechat_notify(text: str) -> bool:
    cfg = WECHAT_CONFIG
    if not cfg.get("enabled") or not cfg.get("webhook"):
        return False
    try:
        payload = {"msgtype": "text", "text": {"content": text}}
        body = json.dumps(payload, ensure_ascii=False)
        r = requests.post(cfg["webhook"], data=body.encode("utf-8"),
                         headers={"Content-Type": "application/json; charset=utf-8"}, timeout=5)
        if not r.ok:
            log.warning(f"企业微信推送失败: {r.status_code}")
            return False
        return True
    except Exception as e:
        log.warning(f"企业微信推送异常: {e}")
        return False
