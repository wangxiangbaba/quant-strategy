"""
Telegram Bot 消息推送模块
参考: https://core.telegram.org/bots/api#sendmessage

配置步骤:
1. 在 Telegram 搜索 @BotFather，发送 /newbot 创建机器人，获取 token
2. 给机器人发一条消息（或把机器人拉进群后发消息）
3. 访问 https://api.telegram.org/bot{token}/getUpdates 查看 chat_id
4. 填写下方配置
"""

import logging

import requests

log = logging.getLogger(__name__)

TELEGRAM_CONFIG = {
    "enabled": True,   # 设为 True 启用
    "bot_token": "8630814901:AAFq-05vRiFZsmT0GPZxFZ851-QcQqGMxCA",   # 从 @BotFather 获取
    "chat_id": "-1003825270411",     # 私聊或群组的 chat_id，从 getUpdates 获取
    "proxy": "http://192.168.1.6:8890",   # 代理，访问 Telegram API 用。不用代理可设为 None
}

# 私聊: chat_id 为数字，如 -1001234567890
# 群组: chat_id 通常为负数，如 -1001234567890


def telegram_notify(text: str) -> bool:
    """
    向 Telegram 发送文本消息。
    仅当 TELEGRAM_CONFIG.enabled 且配置完整时发送。
    """
    cfg = TELEGRAM_CONFIG
    if not cfg.get("enabled") or not cfg.get("bot_token") or not cfg.get("chat_id"):
        return False
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    payload = {
        "chat_id": cfg["chat_id"],
        "text": text,
        "disable_web_page_preview": True,
    }
    try:
        proxies = None
        if cfg.get("proxy"):
            proxies = {"http": cfg["proxy"], "https": cfg["proxy"]}
        r = requests.post(url, json=payload, timeout=10, proxies=proxies)
        if not r.ok:
            log.warning(f"Telegram 推送失败: {r.status_code} {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.warning(f"Telegram 推送异常: {e}")
        return False
