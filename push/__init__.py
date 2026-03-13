"""推送消息模块：飞书、Telegram、企业微信、个人微信(WeChatFerry)"""
from .push_notify import (
    push, init,
    matrix_launched, matrix_start, matrix_open, matrix_close,
    matrix_status, matrix_long, matrix_short,
    matrix_flat_long, matrix_flat_short, matrix_trade,
    matrix_fuse, matrix_symbol_fuse, matrix_error,
)

try:
    from .wechat_ferry_notify import wechat_ferry_notify, init as wechat_ferry_init
except Exception:
    wechat_ferry_notify = lambda t: False
    wechat_ferry_init = lambda **kw: None

__all__ = [
    "push", "init",
    "wechat_ferry_notify", "wechat_ferry_init",
    "matrix_launched", "matrix_start", "matrix_open", "matrix_close",
    "matrix_status", "matrix_long", "matrix_short",
    "matrix_flat_long", "matrix_flat_short", "matrix_trade",
    "matrix_fuse", "matrix_symbol_fuse", "matrix_error",
]
