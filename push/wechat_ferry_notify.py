"""
个人微信推送模块（基于 WeChatFerry / wcferry）

前置条件:
1. 安装指定版本的 Windows 微信（见 WeChatFerry Releases）
2. pip install wcferry
3. 使用微信小号（建议）
4. 启动策略前需已登录 PC 微信
"""

import logging
import threading

log = logging.getLogger(__name__)

WECHAT_FERRY_CONFIG = {
    "enabled": False,  # 已关闭 WeChatFerry 推送
    "receiver": "filehelper",
    "host": None,
    "port": 10086,
    "block": False,
}

_wcf = None
_wcf_lock = threading.Lock()


def init(enabled: bool = None, receiver: str = None, host: str = None, port: int = None, block: bool = None) -> None:
    cfg = WECHAT_FERRY_CONFIG
    if enabled is not None:
        cfg["enabled"] = enabled
    if receiver is not None:
        cfg["receiver"] = receiver
    if host is not None:
        cfg["host"] = host
    if port is not None:
        cfg["port"] = port
    if block is not None:
        cfg["block"] = block


def _get_wcf():
    global _wcf
    with _wcf_lock:
        if _wcf is not None:
            return _wcf
        try:
            from wcferry import Wcf
            cfg = WECHAT_FERRY_CONFIG
            inst = Wcf(host=cfg.get("host"), port=cfg.get("port", 10086),
                       debug=False, block=cfg.get("block", False))
            _wcf = inst
            return _wcf
        except ImportError as e:
            log.warning(f"wcferry 未安装，个人微信推送不可用: {e}")
            return None
        except Exception as e:
            log.warning(f"WeChatFerry 初始化失败: {e}")
            return None


def wechat_ferry_notify(text: str, receiver: str = None) -> bool:
    cfg = WECHAT_FERRY_CONFIG
    if not cfg.get("enabled") or not text:
        return False
    recv = receiver or cfg.get("receiver") or "filehelper"
    if not recv:
        return False
    wcf = _get_wcf()
    if wcf is None:
        return False
    try:
        if not wcf.is_login():
            log.warning("WeChatFerry: 微信未登录，跳过推送")
            return False
        ret = wcf.send_text(text.strip(), recv)
        if ret == 0:
            return True
        log.warning(f"WeChatFerry 发送失败: ret={ret}, receiver={recv}")
        return False
    except Exception as e:
        log.warning(f"WeChatFerry 推送异常: {e}")
        return False


def get_contacts() -> list:
    wcf = _get_wcf()
    if wcf is None or not wcf.is_login():
        return []
    try:
        return wcf.get_contacts()
    except Exception as e:
        log.warning(f"获取通讯录失败: {e}")
        return []
