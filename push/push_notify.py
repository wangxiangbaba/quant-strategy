"""
统一消息推送模块：同时推送到飞书 + Telegram + 企业微信 + 个人微信(WeChatFerry)
"""

import re
import logging
from datetime import datetime

log = logging.getLogger(__name__)


def _beijing_now() -> str:
    """当前北京时间"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 矩阵策略交易时段（北京时间）
TRADING_HOURS_STR = "09:01-10:14  10:31-11:29  13:31-14:55  21:01-22:55"


def _fmt_strategy_mode(tf_str: str = "", strategy_type: str = "") -> str:
    """策略模式行：周期 + 引擎类型"""
    if not tf_str and not strategy_type:
        return ""
    engine = "均值回归" if strategy_type == "mr" else ("趋势突破" if strategy_type == "trend" else "")
    return f"策略模式: {tf_str} {engine}\n" if tf_str or engine else ""

SYMBOL_CN_NAMES = {
    "FG": "玻璃", "SA": "纯碱", "CF": "棉花", "UR": "尿素",
    "y": "豆油", "p": "棕榈油", "m": "豆粕",
    "hc": "热卷", "ao": "氧化铝", "lc": "碳酸锂",
    "SR": "白糖", "RM": "菜粕", "OI": "菜油", "TA": "PTA", "MA": "甲醇",
    "rb": "螺纹", "i": "铁矿石", "j": "焦炭", "jm": "焦煤", "ag": "白银", "au": "黄金",
    "cu": "沪铜", "al": "沪铝", "zn": "沪锌", "ni": "沪镍", "sn": "沪锡", "pb": "沪铅",
    "ru": "橡胶", "bu": "沥青", "sp": "纸浆", "ss": "不锈钢", "eb": "苯乙烯", "eg": "乙二醇",
    "pp": "聚丙烯", "l": "塑料", "v": "PVC", "pg": "液化气", "lh": "生猪", "pk": "花生",
}


def _sym_to_cn(sym: str) -> str:
    s = str(sym).strip()
    product = ""
    # CZCE.SR605 / DCE.m2505 等：交易所.品种+合约
    m = re.search(r"\.([a-zA-Z]+)\d", s)
    if m:
        product = m.group(1)
    else:
        m = re.search(r"(?:DCE|CZCE|SHFE|GFEX)\.([a-zA-Z]+)", s)
        if m:
            product = m.group(1)
        else:
            # SR605 / rb2505 等：纯品种+合约
            m = re.search(r"([a-zA-Z]{1,4})\d{2,4}", s)
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


def matrix_launched(tf_str: str = "", strategy_type: str = "") -> str:
    s = f"【矩阵策略】程序已启动\n{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}"
    if tf_str:
        s += f"\n周期: {tf_str}\n正在连接交易账户..."
    return s


def matrix_start(balance: float, init_capital: float = None, tf_str: str = "", strategy_type: str = "") -> str:
    s = f"【矩阵策略】🚀 系统启动\n{_fmt_strategy_mode(tf_str, strategy_type)}模式: 实盘\n权益: ¥{balance:,.0f}\n"
    if init_capital is not None:
        s += f"初始资金: ¥{init_capital:,.0f}\n"
    s += f"北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}"
    return s


def matrix_open(now_str: str, tf_str: str = "", strategy_type: str = "") -> str:
    return f"【矩阵策略】开盘\n{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {now_str}\n交易时段: {TRADING_HOURS_STR}"


def matrix_close(now_str: str, tf_str: str = "", strategy_type: str = "") -> str:
    return f"【矩阵策略】休市开始\n{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {now_str}\n交易时段: {TRADING_HOURS_STR}"


def _fmt_positions_detail(positions: list, with_plan: bool = False, strategy_type: str = "",
                          symbol_indicators: dict = None) -> str:
    """持仓明细。with_plan=True 时每笔持仓下展示止盈止损；symbol_indicators 为 {symbol_key: {close_price,ma_short,...}} 时在每笔持仓后展示该品种指标"""
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
        lines.append("--------------------------------------------------------------")
        lines.append(f"{sym_disp} {direction}{lots}手  均价{open_p:.1f}  现价{last_p:.1f}  浮盈{fp_str}")
        if with_plan and (p.get("tp_price") is not None or p.get("sl_price") is not None):
            tp, sl = p.get("tp_price"), p.get("sl_price")
            if direction == "多":
                if tp is not None:
                    lines.append(f"止盈: 价格 >= {tp:.2f} 时平多 (回归中轨)" if strategy_type == "mr" else f"止盈: 价格 <= {tp:.2f} 时平多 (跌破平多价)")
                if sl is not None:
                    lines.append(f"止损: 价格 <= {sl:.2f} 时平多")
            else:
                if tp is not None:
                    lines.append(f"止盈: 价格 <= {tp:.2f} 时平空 (回归中轨)" if strategy_type == "mr" else f"止盈: 价格 >= {tp:.2f} 时平空 (突破平空价)")
                if sl is not None:
                    lines.append(f"止损: 价格 >= {sl:.2f} 时平空")
        # 该品种指标嵌入持仓块内
        if symbol_indicators:
            ind = symbol_indicators.get(p.get("symbol_key"))
            if ind:
                close_p = ind.get("close_price", 0) or ind.get("close", 0)
                ma_s = ind.get("ma_short", 0)
                ma_l = ind.get("ma_long", 0)
                rsi = ind.get("rsi_val", 0)
                adx = ind.get("adx_approx", 0)
                ind_sym_disp = _sym_display(p.get("symbol_key", ""))
                lines.append(f"{ind_sym_disp}: 价{close_p:.1f}  |  MA短{ma_s:.1f}  |  MA长{ma_l:.1f}  |  RSI{rsi:.1f}  |  ADX≈{adx:.1f}")
    return "\n".join(lines)


def _fmt_symbol_indicators(symbol_indicators: list) -> str:
    """按品种分别展示指标：价、MA短、MA长、RSI、ADX"""
    if not symbol_indicators:
        return ""
    lines = []
    for item in symbol_indicators:
        sym = item.get("sym", "")
        close = item.get("close_price", 0) or item.get("close", 0)
        ma_s = item.get("ma_short", 0)
        ma_l = item.get("ma_long", 0)
        rsi = item.get("rsi_val", 0)
        adx = item.get("adx_approx", 0)
        sym_disp = _sym_display(sym)
        lines.append(f"{sym_disp}: 价{close:.1f}  |  MA短{ma_s:.1f}  |  MA长{ma_l:.1f}  |  RSI{rsi:.1f}  |  ADX≈{adx:.1f}")
    return "\n".join(lines)


def _fmt_approach_alerts(approach_list: list) -> str:
    """将临近阈值列表格式化为推送文本。每项: {sym, close, h20, l20, h10, l10, cur_pos}"""
    if not approach_list:
        return ""
    lines = []
    pct_near = 0.03  # 3% 内算临近
    for item in approach_list:
        sym = item["sym"]
        cp = item["close"]
        h20, l20, h10, l10 = item["h20"], item["l20"], item["h10"], item["l10"]
        cur_pos = item["cur_pos"]
        sym_disp = _sym_display(sym)
        if cur_pos > 0:
            # 持多: 现价接近平多价 l10（跌破即平多），还差 X 点
            if l10 > 0 and l10 < cp < l10 * (1 + pct_near):
                diff = cp - l10
                lines.append(f"  📉 {sym_disp}: 现价{cp:.1f} 距平多价{l10:.1f} 差{diff:.1f}点 → 准备平多")
        elif cur_pos < 0:
            # 持空: 现价接近平空价 h10（突破即平空），还差 X 点
            if h10 > 0 and h10 * (1 - pct_near) < cp < h10:
                diff = h10 - cp
                lines.append(f"  📈 {sym_disp}: 现价{cp:.1f} 距平空价{h10:.1f} 差{diff:.1f}点 → 准备平空")
        else:
            # 无仓: 距买入价 h20 / 卖出价 l20
            if h20 > 0 and h20 * (1 - pct_near) < cp < h20:
                diff = h20 - cp
                lines.append(f"  📈 {sym_disp}: 现价{cp:.1f} 距买入价{h20:.1f} 差{diff:.1f}点 → 准备开多")
            if l20 > 0 and l20 < cp < l20 * (1 + pct_near):
                diff = cp - l20
                lines.append(f"  📉 {sym_disp}: 现价{cp:.1f} 距卖出价{l20:.1f} 差{diff:.1f}点 → 准备开空")
    if not lines:
        return ""
    return "\n─────────────────────\n🔔 临近信号（距阈值3%内）\n" + "\n".join(lines)


def matrix_status(balance: float, available: float, margin: float, float_profit: float,
                  equity: float, close_profit: float, daily_pnl: float, init_equity: float,
                  positions: list, symbols_str: str, total_lots: int,
                  close: float, ma_s: float, ma_l: float, rsi: float, adx: float,
                  label: str = "5分钟", approach_alerts: list = None, tf_str: str = "", strategy_type: str = "",
                  symbol_indicators: dict = None) -> str:
    pos_detail = _fmt_positions_detail(positions, with_plan=True, strategy_type=strategy_type,
                                        symbol_indicators=symbol_indicators)
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    close_str = f"+{close_profit:,.0f}" if close_profit >= 0 else f"{close_profit:,.0f}"
    total_pnl = equity - init_equity if init_equity > 0 else 0
    total_str = f"+{total_pnl:,.0f}" if total_pnl >= 0 else f"{total_pnl:,.0f}"
    symbols_disp = " ".join(_sym_display(s.strip()) for s in symbols_str.split())
    approach_block = _fmt_approach_alerts(approach_alerts or [])
    # 无持仓或未传 symbol_indicators 时，底部展示单行指标；有 symbol_indicators 时指标已嵌入各持仓块
    if not symbol_indicators:
        indicators_block = f"价: {close:.1f}  |  MA短: {ma_s:.1f}  |  MA长: {ma_l:.1f}  |  RSI: {rsi:.1f}  |  ADX≈{adx:.1f}"
        return (
            f"【矩阵策略】账户状态更新（{label}）\n"
            f"{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}\n"
            f"品种: {symbols_disp}\n"
            f"─────────────────────\n"
            f"余额: ¥{balance:,.0f}  |  可用: ¥{available:,.0f}  |  保证金: ¥{margin:,.0f}\n"
            f"浮盈: ¥{float_profit:,.0f}  |  权益: ¥{equity:,.0f}\n"
            f"本日: ¥{daily_str}  |  平仓: ¥{close_str}  |  总盈亏: ¥{total_str}\n"
            f"─────────────────────\n"
            f"当前持仓: {total_lots} 手  \n"
            f"以下为全部持仓:\n"
            f"{pos_detail}\n"
            f"─────────────────────\n"
            f"{indicators_block}"
            f"{approach_block}"
        )
    return (
        f"【矩阵策略】账户状态更新（{label}）\n"
        f"{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}\n"
        f"品种: {symbols_disp}\n"
        f"─────────────────────\n"
        f"余额: ¥{balance:,.0f}  |  可用: ¥{available:,.0f}  |  保证金: ¥{margin:,.0f}\n"
        f"浮盈: ¥{float_profit:,.0f}  |  权益: ¥{equity:,.0f}\n"
        f"本日: ¥{daily_str}  |  平仓: ¥{close_str}  |  总盈亏: ¥{total_str}\n"
        f"─────────────────────\n"
        f"当前持仓: {total_lots} 手  \n"
        f"以下为全部持仓:\n"
        f"{pos_detail}\n"
        f"─────────────────────"
        f"{approach_block}"
    )


def matrix_long(sym: str, lots: int, price: float, ma_s: float, ma_l: float,
                rsi: float, adx: float, equity: float, float_profit: float,
                daily_pnl: float, positions: list,
                h20: float = None, l10: float = None,
                tf_str: str = "", strategy_type: str = "") -> str:
    pos_detail = _fmt_positions_detail(positions, with_plan=True, strategy_type=strategy_type)
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    s = (
        f"【矩阵策略】📈 开多\n"
        f"{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}\n"
        f"合约: {_sym_display(sym)} | {lots} 手 | 成交价: {price:.1f}\n"
        f"MA短: {ma_s:.1f} MA长: {ma_l:.1f} RSI: {rsi:.1f} ADX: {adx:.1f}\n"
    )
    if h20 is not None:
        s += f"触发: 突破买入价 {h20:.1f} ✓\n"
    if l10 is not None:
        s += f"平多阈值: 跌破 {l10:.1f} 时平仓\n"
    s += f"当前权益: ¥{equity:,.0f} | 持仓浮盈: ¥{float_profit:,.0f} | 本日: ¥{daily_str}\n"
    s += f"── 持仓明细 ──\n{pos_detail}"
    return s


def matrix_short(sym: str, lots: int, price: float, ma_s: float, ma_l: float,
                 rsi: float, adx: float, equity: float, float_profit: float,
                 daily_pnl: float, positions: list,
                 l20: float = None, h10: float = None,
                 tf_str: str = "", strategy_type: str = "") -> str:
    pos_detail = _fmt_positions_detail(positions, with_plan=True, strategy_type=strategy_type)
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    s = (
        f"【矩阵策略】📉 开空\n"
        f"{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}\n"
        f"合约: {_sym_display(sym)} | {lots} 手 | 成交价: {price:.1f}\n"
        f"MA短: {ma_s:.1f} MA长: {ma_l:.1f} RSI: {rsi:.1f} ADX: {adx:.1f}\n"
    )
    if l20 is not None:
        s += f"触发: 跌破卖出价 {l20:.1f} ✓\n"
    if h10 is not None:
        s += f"平空阈值: 突破 {h10:.1f} 时平仓\n"
    s += f"当前权益: ¥{equity:,.0f} | 持仓浮盈: ¥{float_profit:,.0f} | 本日: ¥{daily_str}\n"
    s += f"── 持仓明细 ──\n{pos_detail}"
    return s


def matrix_flat_long(sym: str, price: float, open_price: float, lots: int,
                    realized_pnl: float, equity: float, daily_pnl: float,
                    l10: float = None, tf_str: str = "", strategy_type: str = "") -> str:
    pnl_str = f"+{realized_pnl:,.0f}" if realized_pnl >= 0 else f"{realized_pnl:,.0f}"
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    s = (
        f"【矩阵策略】🛑 平多\n"
        f"{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}\n"
        f"合约: {_sym_display(sym)} | {lots} 手\n"
        f"开仓均价: {open_price:.1f} → 平仓价: {price:.1f}\n"
    )
    if l10 is not None:
        s += f"触发: 跌破平多价 {l10:.1f} ✓\n"
    s += f"本次盈亏: ¥{pnl_str} | 当前权益: ¥{equity:,.0f} | 本日: ¥{daily_str}"
    return s


def matrix_flat_short(sym: str, price: float, open_price: float, lots: int,
                     realized_pnl: float, equity: float, daily_pnl: float,
                     h10: float = None, tf_str: str = "", strategy_type: str = "") -> str:
    pnl_str = f"+{realized_pnl:,.0f}" if realized_pnl >= 0 else f"{realized_pnl:,.0f}"
    daily_str = f"+{daily_pnl:,.0f}" if daily_pnl >= 0 else f"{daily_pnl:,.0f}"
    s = (
        f"【矩阵策略】🛑 平空\n"
        f"{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}\n"
        f"合约: {_sym_display(sym)} | {lots} 手\n"
        f"开仓均价: {open_price:.1f} → 平仓价: {price:.1f}\n"
    )
    if h10 is not None:
        s += f"触发: 突破平空价 {h10:.1f} ✓\n"
    s += f"本次盈亏: ¥{pnl_str} | 当前权益: ¥{equity:,.0f} | 本日: ¥{daily_str}"
    return s


def matrix_trade(contract: str, direction: str, offset: str, lots: int, price: float,
                 tf_str: str = "", strategy_type: str = "") -> str:
    dir_cn = "买" if direction.upper() == "BUY" else "卖"
    offset_cn = "开仓" if offset.upper() == "OPEN" else "平仓"
    return (
        f"【矩阵策略】成交通知\n"
        f"{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}\n"
        f"合约: {_sym_display(contract)}\n"
        f"开平: {offset_cn} | 方向: {dir_cn}\n"
        f"手数: {lots} | 价格: {price:.1f}"
    )


def matrix_fuse(daily_loss: float) -> str:
    return (
        f"【矩阵策略】🔴 日内熔断\n"
        f"北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}\n"
        f"今日已亏损: ¥{daily_loss:,.0f}\n"
        f"已冻结新开仓直至下一交易日。"
    )


def matrix_symbol_fuse(sym: str, loss_val: float, tf_str: str = "", strategy_type: str = "") -> str:
    """单品种熔断专属推送"""
    return (
        f"【矩阵策略】🔴 单品种跳闸\n"
        f"{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}\n"
        f"合约: {_sym_display(sym)}\n"
        f"今日该品种已亏损: ¥{loss_val:,.0f}\n"
        f"已执行强平，并冻结该品种新开仓权限直至下一交易日。"
    )


def matrix_backtest_report(
    init_capital: float,
    final_equity: float,
    total_ret: float,
    max_dd: float,
    sharpe: float,
    tf_str: str = "",
    strategy_type: str = "trend",
    bt_start=None,
    bt_end=None,
) -> str:
    """回测报告推送文本"""
    engine_name = "均值回归(MR)" if strategy_type == "mr" else "趋势突破(Trend)"
    period_str = ""
    if bt_start is not None and bt_end is not None:
        period_str = f"回测区间:   {bt_start} ~ {bt_end}\n"
    return (
        f"【矩阵策略】📊 回测完成\n"
        f"{_fmt_strategy_mode(tf_str, strategy_type)}"
        f"10品种矩阵 ({engine_name} | {tf_str}级别)\n"
        f"─────────────────────\n"
        f"{period_str}"
        f"初始资金:   {init_capital:,.0f}\n"
        f"最终权益:   {final_equity:,.2f}\n"
        f"总收益率:   {total_ret:.2%}\n"
        f"最大回撤:   {max_dd:.2%}\n"
        f"夏普比率:   {sharpe:.2f}\n"
        f"─────────────────────\n"
        f"北京时间: {_beijing_now()}"
    )


def matrix_error(err: str, tf_str: str = "", strategy_type: str = "") -> str:
    return f"【矩阵策略】程序异常退出\n{_fmt_strategy_mode(tf_str, strategy_type)}北京时间: {_beijing_now()}\n交易时段: {TRADING_HOURS_STR}\n错误: {err}\n将在 10 秒后自动重启..."
