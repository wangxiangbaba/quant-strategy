"""
飞书机器人消息推送模块
配置 webhook 后即可使用
"""

import json
import logging
import requests

log = logging.getLogger(__name__)

FEISHU_CONFIG = {
    "enabled": True,
    # 飞书群机器人 webhook，在群设置-群机器人-添加自定义机器人 中获取
    "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/d006b985-b71a-4c8e-a505-dcea786053a2",  # 例如: https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx
}


def init(webhook: str = None, enabled: bool = None) -> None:
    if webhook is not None:
        FEISHU_CONFIG["webhook"] = webhook
    if enabled is not None:
        FEISHU_CONFIG["enabled"] = enabled


def feishu_notify(text: str) -> bool:
    cfg = FEISHU_CONFIG
    if not cfg.get("enabled") or not cfg.get("webhook"):
        return False
    try:
        payload = {"msg_type": "text", "content": {"text": text}}
        r = requests.post(cfg["webhook"], json=payload, timeout=5)
        if not r.ok:
            log.warning(f"飞书推送失败: {r.status_code}")
            return False
        return True
    except Exception as e:
        log.warning(f"飞书推送异常: {e}")
        return False
