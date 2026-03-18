"""
策略工具函数：时间判断、动态风控、持仓详情、回测报告
"""

from datetime import datetime, time
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from strategy.config import TRADING_WINDOWS, SAFE_ENTRY_WINDOWS
from strategy.config import SESSION_OPEN_TIMES, SESSION_OPEN_PROTECTION_MINUTES

# 高水位与回撤风控（模块级状态，与 get_dynamic_risk 配合）
WATERMARK = {"high_watermark": 0.0, "drawdown_limit": 0.05, "base_risk": 0.01, "safe_risk": 0.003}


def is_trading_time() -> bool:
    """是否在交易时段内"""
    return any(s <= datetime.now().time() <= e for s, e in TRADING_WINDOWS)


def get_dynamic_risk(current_equity: float) -> float:
    """根据权益与高水位计算动态风险比例"""
    if current_equity > WATERMARK["high_watermark"]:
        WATERMARK["high_watermark"] = current_equity
    if WATERMARK["high_watermark"] <= 0:
        return WATERMARK["base_risk"]
    dd = (WATERMARK["high_watermark"] - current_equity) / WATERMARK["high_watermark"]
    return WATERMARK["safe_risk"] if dd > WATERMARK["drawdown_limit"] else WATERMARK["base_risk"]


def is_safe_entry_time(mode: str) -> bool:
    """是否在安全开仓时段（回测恒为 True）"""
    if mode == "backtest":
        return True
    now = datetime.now().time()
    return any(s <= now <= e for s, e in SAFE_ENTRY_WINDOWS)


def is_in_session_open_protection(now: datetime) -> bool:
    """是否处于开盘保护期内（开盘后 N 分钟内），用于 MR 引擎避免回归中轨误平"""
    t = now.time()
    for open_t in SESSION_OPEN_TIMES:
        # 开盘时刻到开盘+N分钟
        start_min = open_t.hour * 60 + open_t.minute
        end_min = start_min + SESSION_OPEN_PROTECTION_MINUTES
        now_min = t.hour * 60 + t.minute
        if start_min <= now_min < end_min:
            return True
    return False


def build_positions_detail(
    pos_dict: dict,
    quote_dict: dict,
    sig_dict: dict = None,
    cfg: dict = None,
    strategy_type: str = "trend",
) -> List[Dict[str, Any]]:
    """构建持仓详情列表，用于推送展示。可选传入 sig_dict/cfg/strategy_type 以计算每笔持仓的止盈止损"""
    out = []
    for sym, pos in pos_dict.items():
        pl, ps = pos.pos_long - 0, pos.pos_short - 0
        last_p = float(getattr(quote_dict.get(sym), "last_price", 0) or 0)
        disp = getattr(pos, "instrument_id", sym)
        sig = (sig_dict or {}).get(sym)
        if pl > 0:
            op = float(getattr(pos, "open_price_long", 0) or 0)
            fp = float(getattr(pos, "float_profit_long", 0) or 0)
            tp, sl = _calc_tp_sl(sig, cfg, strategy_type, "多", op) if sig else (None, None)
            out.append({"sym": disp, "symbol_key": sym, "direction": "多", "lots": pl, "open_price": op, "float_profit": fp, "last_price": last_p, "tp_price": tp, "sl_price": sl})
        if ps > 0:
            op = float(getattr(pos, "open_price_short", 0) or 0)
            fp = float(getattr(pos, "float_profit_short", 0) or 0)
            tp, sl = _calc_tp_sl(sig, cfg, strategy_type, "空", op) if sig else (None, None)
            out.append({"sym": disp, "symbol_key": sym, "direction": "空", "lots": ps, "open_price": op, "float_profit": fp, "last_price": last_p, "tp_price": tp, "sl_price": sl})
    return out


def _calc_tp_sl(sig: dict, cfg: dict, strategy_type: str, direction: str, open_price: float):
    """计算止盈止损价"""
    if strategy_type == "mr":
        tp = sig.get("bb_mid")
        atr = sig.get("atr_val") or 0
        sl = (open_price - 3 * atr) if direction == "多" and atr else ((open_price + 3 * atr) if direction == "空" and atr else None)
        return tp, sl
    tp = sig.get("low_10") if direction == "多" else sig.get("high_10")
    return tp, None


def print_and_plot_report(
    equity_list: List[float],
    cfg: dict,
    tf_str: str,
    strategy_type: str = "trend",
    log=None,
) -> Dict[str, Any]:
    """
    打印回测报告并保存权益曲线图。
    返回报告数据 dict，供推送使用；equity_list 为空时返回空 dict。
    """
    if not equity_list:
        return {}
    eq = pd.Series(equity_list)
    total_ret = (eq.iloc[-1] - eq.iloc[0]) / eq.iloc[0]
    roll_max = eq.cummax()
    max_dd = ((eq - roll_max) / roll_max).min()
    daily_ret = eq.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if len(daily_ret) > 0 and daily_ret.std() > 0 else 0
    engine_name = "均值回归(MR)" if strategy_type == "mr" else "趋势突破(Trend)"
    print("\n" + "=" * 55)
    print(f" 10品种矩阵 ({engine_name} | {tf_str}级别) - 回测报告")
    print("=" * 55)
    print(f" 初始资金:   {cfg['init_capital']:,.0f}")
    print(f" 最终权益:   {eq.iloc[-1]:,.2f}")
    print(f" 总收益率:   {total_ret:.2%}")
    print(f" 最大回撤:   {max_dd:.2%}")
    print(f" 夏普比率:   {sharpe:.2f}")
    print("=" * 55 + "\n")
    plt.figure(figsize=(12, 6))
    plt.plot(eq.index, eq.values, color="#9b59b6" if strategy_type == "mr" else "#2ecc71", lw=2)
    plt.axhline(eq.iloc[0], color="gray", ls="--")
    plt.title(f"Portfolio Equity ({engine_name} | {tf_str})", fontsize=14)
    plt.grid(alpha=0.3)
    plt.savefig(f"portfolio_backtest_{tf_str.replace('/', '_')}.png", dpi=150)
    if log:
        log.info(f"权益曲线已保存: portfolio_backtest_{tf_str.replace('/', '_')}.png")
    plt.show()
    return {
        "init_capital": cfg["init_capital"],
        "final_equity": float(eq.iloc[-1]),
        "total_ret": total_ret,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "tf_str": tf_str,
        "strategy_type": strategy_type,
        "bt_start": cfg.get("bt_start"),
        "bt_end": cfg.get("bt_end"),
    }
