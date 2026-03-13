"""
Telegram 消息推送模块
需配置 BOT_TOKEN 和 CHAT_ID
"""

import logging
import requests

log = logging.getLogger(__name__)

TELEGRAM_CONFIG = {
    "enabled": True,
    # 在 @BotFather 创建机器人后获取
    "bot_token": "",   # 需从 @BotFather 获取，机器人: @bear_longquan_bear_bot
    "chat_id": "-1003825270411",  # 熊熊量化基金 群组
}


def telegram_notify(text: str) -> bool:
    cfg = TELEGRAM_CONFIG
    if not cfg.get("enabled") or not cfg.get("bot_token") or not cfg.get("chat_id"):
        return False
    try:
        url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
        payload = {"chat_id": cfg["chat_id"], "text": text}
        r = requests.post(url, json=payload, timeout=5)
        if not r.ok:
            log.warning(f"Telegram 推送失败: {r.status_code}")
            return False
        return True
    except Exception as e:
        log.warning(f"Telegram 推送异常: {e}")
        return False
