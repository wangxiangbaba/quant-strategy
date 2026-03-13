"""
飞书机器人消息推送模块
参考: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot

配置步骤:
1. 在飞书群聊中添加自定义机器人，获取 webhook 地址
2. 填写下方 FEISHU_CONFIG
"""

import logging
import requests

log = logging.getLogger(__name__)

FEISHU_CONFIG = {
    "enabled": True,   # 设为 True 启用
    "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/d006b985-b71a-4c8e-a505-dcea786053a2",
}


def init(webhook: str = None, enabled: bool = None) -> None:
    """运行时覆盖配置"""
    if webhook is not None:
        FEISHU_CONFIG["webhook"] = webhook
    if enabled is not None:
        FEISHU_CONFIG["enabled"] = enabled


def feishu_notify(text: str) -> bool:
    """
    向飞书群发送文本消息。
    仅当 FEISHU_CONFIG.enabled 且 webhook 配置完整时发送。
    """
    cfg = FEISHU_CONFIG
    if not cfg.get("enabled") or not cfg.get("webhook"):
        return False
    try:
        r = requests.post(
            cfg["webhook"],
            json={"msg_type": "text", "content": {"text": text}},
            timeout=5,
        )
        if not r.ok:
            log.warning(f"飞书推送失败: {r.status_code} {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.warning(f"飞书推送异常: {e}")
        return False
