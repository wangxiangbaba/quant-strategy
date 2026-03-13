"""
统一消息推送模块：同时推送到飞书 + Telegram + 企业微信 + 个人微信(WeChatFerry)
"""

import re
import logging
from datetime import datetime

log = logging.getLogger(__name__)

SYMBOL_CN_NAMES = {
    "FG": "玻璃", "SA": "纯碱", "CF": "棉花", "UR": "尿素",
    "y": "豆油", "p": "棕榈油", "m": "豆粕",
    "hc": "热卷", "ao": "氧化铝", "lc": "碳酸锂",
}


def _sym_to_cn(sym: str) -> str:
    s = str(sym).strip()
    product = ""
    m = re.search(r"\.([a-zA-Z]+)\d", s)
    if m:
        product = m.group(1)
    else:
        m = re.search(r"(?:DCE|CZCE|SHFE|GFEX)\.([a-zA-Z]+)", s)
        if m:
            product = m.group(1)
        else:
            m = re.search(r"^([a-zA-Z]{1,3})\d", s)
            if m:
                product = m.group(1)
    product = (product or "").lower()
    for k, v in SYMBOL_CN_NAMES.items():
        if product == k.lower() or product == k:
            return v
    return ""


def _sym_display(sym: str) -> str:
    cn = _sym_to_cn(sym)
    return f"{sym}({cn})" if cn else sym

try:
    from .feishu_notify import feishu_notify, init as feishu_init
except Exception:
    feishu_notify = lambda t: False
    feishu_init = lambda **kw: None

try:
    from .telegram_notify import telegram_notify
except Exception:
    telegram_notify = lambda t: False

try:
    from .wechat_notify import wechat_notify, init as wechat_init
except Exception:
    wechat_notify = lambda t: False
    wechat_init = lambda **kw: None

try:
    from .wechat_ferry_notify import wechat_ferry_notify, init as wechat_ferry_init
except Exception:
    wechat_ferry_notify = lambda t: False
    wechat_ferry_init = lambda **kw: None


def init(feishu_webhook: str = None, feishu_enabled: bool = None) -> None:
    feishu_init(webhook=feishu_webhook, enabled=feishu_enabled)


def push(text: str) -> None:
    if not text:
        return
    feishu_notify(text)
    telegram_notify(text)
    wechat_notify(text)
    wechat_ferry_notify(text)


def matrix_launched(tf_str: str = "") -> str:
    s = f"【矩阵策略】程序已启动\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    if tf_str:
        s += f"\n周期: {tf_str}\n正在连接交易账户..."
    return s


def matrix_start(balance: float, init_capital: float = None) -> str:
    s = f"【矩阵策略】🚀 系统启动\n模式: 实盘\n权益: ¥{balance:,.0f}\n"
    if init_capital is not None:
        s += f"初始资金: ¥{init_capital:,.0f}\n"
    s += f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    return s


def matrix_open(now_str: str) -> str:
    return f"【矩阵策略】开盘\n时间: {now_str}"


def matrix_close(now_str: str) -> str:
    return f"【矩阵策略】休市开始\n时间: {now_str}"


def _fmt_positions_detail(positions: list) -> str:
    if not positions:
        return "无持仓"
    lines = []
    for p in positions:
        sym = p.get("sym", "")
        direction = p.get("direction", "")
        lots = p.get("lots", 0)
        open_p = p.get("open_price", 0)
        fp = p.get("float_profit", 0)
        last_p = p.get("last_price", 0)
        fp_str = f"+{fp:,.0f}" if fp >= 0 else f"{fp:,.0f}"
        sym_disp = _sym_display(sym)
        lines.append(f"  {sym_disp} {direction}{lots}手  均价{open_p:.1f}  现价{last_p:.1f}  浮盈{fp_str}")
    return "\n".join(lines)


def matrix_status(balance: float, available: float, margin: float, float_profit: float,
                  equity: float, close_profit: float, daily_pnl: float, init_equity: float,
                  positions: list, symbols_str: str, total_lots: int,
                  close: float, ma_s: float, ma_l: float, rsi: float, adx: float,
                  label: str = "5分钟") -> str:
    pos_detail = _fmt_positions_detail(positions)
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    close_str = f"+{close_profit:,.0f}" if close_profit >= 0 else f"{close_profit:,.0f}"
    total_pnl = equity - init_equity if init_equity > 0 else 0
    total_str = f"+{total_pnl:,.0f}" if total_pnl >= 0 else f"{total_pnl:,.0f}"
    symbols_disp = " ".join(_sym_display(s.strip()) for s in symbols_str.split())
    return (
        f"【矩阵策略】账户状态更新（{label}）\n"
        f"品种: {symbols_disp}\n"
        f"─────────────────────\n"
        f"余额: ¥{balance:,.0f}  |  可用: ¥{available:,.0f}  |  保证金: ¥{margin:,.0f}\n"
        f"浮盈: ¥{float_profit:,.0f}  |  权益: ¥{equity:,.0f}\n"
        f"本日: ¥{daily_str}  |  平仓: ¥{close_str}  |  总盈亏: ¥{total_str}\n"
        f"─────────────────────\n"
        f"当前持仓: {total_lots} 手  |  全部持仓: {pos_detail}\n"
        f"─────────────────────\n"
        f"价: {close:.1f}  |  MA短: {ma_s:.1f}  |  MA长: {ma_l:.1f}  |  RSI: {rsi:.1f}  |  ADX≈{adx:.1f}"
    )


def matrix_long(sym: str, lots: int, price: float, ma_s: float, ma_l: float,
                rsi: float, adx: float, equity: float, float_profit: float,
                daily_pnl: float, positions: list) -> str:
    pos_detail = _fmt_positions_detail(positions)
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    return (
        f"【矩阵策略】📈 开多\n"
        f"合约: {_sym_display(sym)} | {lots} 手 | 成交价: {price:.1f}\n"
        f"MA短: {ma_s:.1f} MA长: {ma_l:.1f} RSI: {rsi:.1f} ADX: {adx:.1f}\n"
        f"当前权益: ¥{equity:,.0f} | 持仓浮盈: ¥{float_profit:,.0f} | 本日: ¥{daily_str}\n"
        f"── 持仓明细 ──\n{pos_detail}"
    )


def matrix_short(sym: str, lots: int, price: float, ma_s: float, ma_l: float,
                 rsi: float, adx: float, equity: float, float_profit: float,
                 daily_pnl: float, positions: list) -> str:
    pos_detail = _fmt_positions_detail(positions)
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    return (
        f"【矩阵策略】📉 开空\n"
        f"合约: {_sym_display(sym)} | {lots} 手 | 成交价: {price:.1f}\n"
        f"MA短: {ma_s:.1f} MA长: {ma_l:.1f} RSI: {rsi:.1f} ADX: {adx:.1f}\n"
        f"当前权益: ¥{equity:,.0f} | 持仓浮盈: ¥{float_profit:,.0f} | 本日: ¥{daily_str}\n"
        f"── 持仓明细 ──\n{pos_detail}"
    )


def matrix_flat_long(sym: str, price: float, open_price: float, lots: int,
                    realized_pnl: float, equity: float, daily_pnl: float) -> str:
    pnl_str = f"+{realized_pnl:,.0f}" if realized_pnl >= 0 else f"{realized_pnl:,.0f}"
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    return (
        f"【矩阵策略】🛑 平多\n"
        f"合约: {_sym_display(sym)} | {lots} 手\n"
        f"开仓均价: {open_price:.1f} → 平仓价: {price:.1f}\n"
        f"本次盈亏: ¥{pnl_str} | 当前权益: ¥{equity:,.0f} | 本日: ¥{daily_str}"
    )


def matrix_flat_short(sym: str, price: float, open_price: float, lots: int,
                     realized_pnl: float, equity: float, daily_pnl: float) -> str:
    pnl_str = f"+{realized_pnl:,.0f}" if realized_pnl >= 0 else f"{realized_pnl:,.0f}"
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    return (
        f"【矩阵策略】🛑 平空\n"
        f"合约: {_sym_display(sym)} | {lots} 手\n"
        f"开仓均价: {open_price:.1f} → 平仓价: {price:.1f}\n"
        f"本次盈亏: ¥{pnl_str} | 当前权益: ¥{equity:,.0f} | 本日: ¥{daily_str}"
    )


def matrix_trade(contract: str, direction: str, offset: str, lots: int, price: float) -> str:
    dir_cn = "买" if direction.upper() == "BUY" else "卖"
    offset_cn = "开仓" if offset.upper() == "OPEN" else "平仓"
    return (
        f"【矩阵策略】成交通知\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"合约: {_sym_display(contract)}\n"
        f"开平: {offset_cn} | 方向: {dir_cn}\n"
        f"手数: {lots} | 价格: {price:.1f}"
    )


def matrix_fuse(daily_loss: float) -> str:
    return (
        f"【矩阵策略】🔴 日内熔断\n"
        f"今日已亏损: ¥{daily_loss:,.0f}\n"
        f"已冻结新开仓直至下一交易日。"
    )


def matrix_error(err: str) -> str:
    return f"【矩阵策略】程序异常退出\n错误: {err}\n将在 10 秒后自动重启..."
