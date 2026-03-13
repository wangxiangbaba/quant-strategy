"""
统一消息推送模块：同时推送到飞书 + Telegram
供 portfolio_matrix_full、m_quant_system_v3 等策略调用，保持主策略文件简洁。
"""

import logging
from datetime import datetime

log = logging.getLogger(__name__)

try:
    from feishu_notify import feishu_notify, init as feishu_init
except Exception:
    feishu_notify = lambda t: False
    feishu_init = lambda **kw: None

try:
    from telegram_notify import telegram_notify
except Exception:
    telegram_notify = lambda t: False


def init(feishu_webhook: str = None, feishu_enabled: bool = None) -> None:
    """初始化推送配置，供策略在启动时调用"""
    feishu_init(webhook=feishu_webhook, enabled=feishu_enabled)


def push(text: str) -> None:
    """同时推送到飞书和 Telegram"""
    if not text:
        return
    feishu_notify(text)
    telegram_notify(text)


# ═══════════════════════════════════════════════════════════════
#  矩阵策略 (portfolio_matrix_full) 消息格式化
# ═══════════════════════════════════════════════════════════════

def matrix_start(balance: float, init_capital: float = None) -> str:
    s = f"【矩阵策略】🚀 系统启动\n模式: 实盘\n权益: ¥{balance:,.0f}\n"
    if init_capital is not None:
        s += f"初始资金: ¥{init_capital:,.0f}\n"
    s += f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    return s


def matrix_close(now_str: str) -> str:
    return f"【矩阵策略】休市开始\n时间: {now_str}"


def _fmt_positions_detail(positions: list) -> str:
    """格式化持仓明细列表，每项: {sym, direction, lots, open_price, float_profit, last_price}"""
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
        lines.append(f"  {sym} {direction}{lots}手  均价{open_p:.1f}  现价{last_p:.1f}  浮盈{fp_str}")
    return "\n".join(lines)


def matrix_status(balance: float, available: float, margin: float, float_profit: float,
                  equity: float, close_profit: float, daily_pnl: float, init_equity: float,
                  positions: list, symbols_str: str, total_lots: int,
                  close: float, ma_s: float, ma_l: float, rsi: float, adx: float,
                  label: str = "5分钟") -> str:
    """positions: [{sym, direction, lots, open_price, float_profit, last_price}, ...]"""
    pos_detail = _fmt_positions_detail(positions)
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    close_str = f"+{close_profit:,.0f}" if close_profit >= 0 else f"{close_profit:,.0f}"
    total_pnl = equity - init_equity if init_equity > 0 else 0
    total_str = f"+{total_pnl:,.0f}" if total_pnl >= 0 else f"{total_pnl:,.0f}"
    return (
        f"【矩阵策略】账户状态更新（{label}）\n"
        f"品种: {symbols_str}\n"
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
        f"合约: {sym} | {lots} 手 | 成交价: {price:.1f}\n"
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
        f"合约: {sym} | {lots} 手 | 成交价: {price:.1f}\n"
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
        f"合约: {sym} | {lots} 手\n"
        f"开仓均价: {open_price:.1f} → 平仓价: {price:.1f}\n"
        f"本次盈亏: ¥{pnl_str} | 当前权益: ¥{equity:,.0f} | 本日: ¥{daily_str}"
    )


def matrix_flat_short(sym: str, price: float, open_price: float, lots: int,
                     realized_pnl: float, equity: float, daily_pnl: float) -> str:
    pnl_str = f"+{realized_pnl:,.0f}" if realized_pnl >= 0 else f"{realized_pnl:,.0f}"
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    return (
        f"【矩阵策略】🛑 平空\n"
        f"合约: {sym} | {lots} 手\n"
        f"开仓均价: {open_price:.1f} → 平仓价: {price:.1f}\n"
        f"本次盈亏: ¥{pnl_str} | 当前权益: ¥{equity:,.0f} | 本日: ¥{daily_str}"
    )


def matrix_trade(contract: str, direction: str, offset: str, lots: int, price: float) -> str:
    """成交通知，格式类似终端输出"""
    dir_cn = "买" if direction.upper() == "BUY" else "卖"
    offset_cn = "开仓" if offset.upper() == "OPEN" else "平仓"
    return (
        f"【矩阵策略】成交通知\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"合约: {contract}\n"
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
