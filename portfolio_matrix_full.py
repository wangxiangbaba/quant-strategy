"""
===============================================================
  10品种跨板块量化矩阵系统 (回测 + 实盘 统一架构版)
  
  运行方式:
  - 回测模式: python portfolio_matrix_full.py backtest
  - 实盘模式: python portfolio_matrix_full.py live
  
  新增功能:
  1. 接入 TqBacktest，支持多品种并发回测
  2. 自动记录资金曲线，回测结束后生成包含最大回撤、夏普比率的绩效报告
  3. 自动绘制多品种组合资金曲线图
===============================================================
"""

import os

# ==========================================
# 🚀 霸道拦截：强制抹除当前 Python 进程的所有系统代理
# 必须放在所有第三方库（特别是 tqsdk 和 requests）导入之前！
# ==========================================
proxy_keys = [
    'http_proxy', 'https_proxy', 'all_proxy',
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'
]
for key in proxy_keys:
    if key in os.environ:
        del os.environ[key]


import logging
import sys
import time as time_module
import warnings
from datetime import datetime, time, date
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from tqsdk import TqApi, TqAuth, TargetPosTask, TqAccount
from tqsdk import TqBacktest, TqSim, BacktestFinished
from tqsdk.ta import ATR

warnings.filterwarnings("ignore")
plt.rcParams["font.sans-serif"] = ["SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ╔══════════════════════════════════════════════╗
# ║  1. 全局配置与参数                             ║
# ╚══════════════════════════════════════════════╝

PORTFOLIO_CONFIG = {
    # 最小开仓手数>1的合约(如SA/UR)会在运行时自动排除，因 TargetPosTask 暂不支持
    "symbols": [
        "KQ.m@CZCE.FG", "KQ.m@CZCE.SA", "KQ.m@CZCE.CF", "KQ.m@CZCE.UR",
        "KQ.m@DCE.y",   "KQ.m@DCE.p",   "KQ.m@DCE.m", 
        "KQ.m@SHFE.hc", "KQ.m@SHFE.ao", 
        "KQ.m@GFEX.lc"
    ],
    "kline_freq": 24 * 60 * 60,  # 日线
    "donchian_entry": 20,
    "donchian_exit": 10,
    "adx_threshold": 20,
    
    # ── 回测专属配置 ──
    "init_capital": 500_000.0,            # 回测初始资金
    "bt_start": date(2024, 1, 1),         # 回测开始日期
    "bt_end": date(2026, 3, 1),           # 回测结束日期
    
    # ── 实盘账号配置 ──
    "phone":      "15528503735",
    "password":   "QQ1392070089",
    "broker_id":  "快期模拟",
    "account_id": "15528503735",
    "account_pwd":"QQ1392070089",
}

try:
    from push_notify import push
    from push_notify import matrix_start, matrix_close, matrix_status, matrix_long, matrix_short
    from push_notify import matrix_flat_long, matrix_flat_short, matrix_trade, matrix_fuse, matrix_error
except Exception:
    push = lambda t: None
    matrix_start = lambda b: ""
    matrix_close = lambda s: ""
    matrix_status = lambda *a, **k: ""
    matrix_long = matrix_short = matrix_flat_long = matrix_flat_short = matrix_trade = lambda *a, **k: ""
    matrix_fuse = lambda l: ""
    matrix_error = lambda e: ""

WATERMARK = {
    "high_watermark": 0.0,
    "drawdown_limit": 0.05,
    "base_risk": 0.01,
    "safe_risk": 0.003
}

DAILY_RISK = {
    "date": datetime.now().date(),
    "start_equity": 0.0,
    "max_daily_loss": 5000.0,
    "is_locked": False
}


# ╔══════════════════════════════════════════════╗
# ║  2. 组件模块 (保持不变)                        ║
# ╚══════════════════════════════════════════════╝

TRADING_WINDOWS = [
    (time(9, 1), time(10, 14)), (time(10, 31), time(11, 29)),
    (time(13, 31), time(14, 55)), (time(21, 1), time(22, 55)),
]

def is_trading_time() -> bool:
    now = datetime.now().time()
    return any(s <= now <= e for s, e in TRADING_WINDOWS)

def check_daily_circuit_breaker(current_equity: float) -> bool:
    today = datetime.now().date()
    if today != DAILY_RISK["date"]:
        DAILY_RISK["date"] = today
        DAILY_RISK["start_equity"] = current_equity
        DAILY_RISK["is_locked"] = False
        return False
    if DAILY_RISK["start_equity"] == 0:
        DAILY_RISK["start_equity"] = current_equity
    daily_pnl = current_equity - DAILY_RISK["start_equity"]
    if daily_pnl < -DAILY_RISK["max_daily_loss"] and not DAILY_RISK["is_locked"]:
        DAILY_RISK["is_locked"] = True
        _msg = matrix_fuse(-daily_pnl)
        push(_msg)
        log.warning(_msg.replace("\n", " | "))
        return True
    return DAILY_RISK["is_locked"]

def get_dynamic_risk(current_equity: float) -> float:
    if current_equity > WATERMARK["high_watermark"]:
        WATERMARK["high_watermark"] = current_equity
    if WATERMARK["high_watermark"] <= 0:
        return WATERMARK["base_risk"]
    drawdown = (WATERMARK["high_watermark"] - current_equity) / WATERMARK["high_watermark"]
    return WATERMARK["safe_risk"] if drawdown > WATERMARK["drawdown_limit"] else WATERMARK["base_risk"]

def build_positions_detail(pos_dict: dict, quote_dict: dict) -> list:
    """构建持仓明细列表，用于推送"""
    out = []
    for sym, pos in pos_dict.items():
        pl = pos.pos_long - 0
        ps = pos.pos_short - 0
        last_p = float(getattr(quote_dict.get(sym), "last_price", 0) or 0)
        disp_sym = getattr(pos, "instrument_id", sym)
        if pl > 0:
            open_p = float(getattr(pos, "open_price_long", 0) or 0)
            fp = float(getattr(pos, "float_profit_long", 0) or 0)
            out.append({"sym": disp_sym, "direction": "多", "lots": pl, "open_price": open_p, "float_profit": fp, "last_price": last_p})
        if ps > 0:
            open_p = float(getattr(pos, "open_price_short", 0) or 0)
            fp = float(getattr(pos, "float_profit_short", 0) or 0)
            out.append({"sym": disp_sym, "direction": "空", "lots": ps, "open_price": open_p, "float_profit": fp, "last_price": last_p})
    return out


def is_safe_entry_time(mode: str) -> bool:
    """回测模式下关闭时间过滤，因为使用的是日线走势，实盘则开启"""
    if mode == "backtest":
        return True
    now = datetime.now().time()
    safe_windows = [
        (time(9, 5), time(10, 0)), (time(10, 35), time(11, 15)),
        (time(13, 35), time(14, 45)), (time(21, 5), time(22, 45))
    ]
    return any(start <= now <= end for start, end in safe_windows)


# ╔══════════════════════════════════════════════╗
# ║  3. 绩效分析模块 (回测专用)                    ║
# ╚══════════════════════════════════════════════╝

def print_and_plot_report(equity_list: list, cfg: dict):
    if not equity_list:
        log.warning("没有权益数据，无法生成报告。")
        return
        
    eq_series = pd.Series(equity_list)
    total_ret = (eq_series.iloc[-1] - eq_series.iloc[0]) / eq_series.iloc[0]
    
    # 近似计算年化（假设每天记录一次，一年约252个交易日）
    trading_days = len(eq_series)
    ann_ret = (1 + total_ret) ** (252 / trading_days) - 1 if trading_days > 0 else 0
    
    roll_max = eq_series.cummax()
    drawdown = (eq_series - roll_max) / roll_max
    max_dd = drawdown.min()
    
    daily_ret = eq_series.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else 0

    print("\n" + "═" * 50)
    print(" 📊 10品种矩阵 — 回测绩效报告")
    print("═" * 50)
    print(f" 初始资金:   ¥{cfg['init_capital']:,.2f}")
    print(f" 最终权益:   ¥{eq_series.iloc[-1]:,.2f}")
    print(f" 总收益率:   {total_ret:.2%}")
    print(f" 年化收益:   {ann_ret:.2%}")
    print(f" 最大回撤:   {max_dd:.2%}")
    print(f" 夏普比率:   {sharpe:.2f}")
    print("═" * 50 + "\n")

    # 绘制组合权益曲线
    plt.figure(figsize=(12, 6))
    plt.plot(eq_series.index, eq_series.values, label="Portfolio Equity", color="#2ecc71", lw=2)
    plt.fill_between(eq_series.index, eq_series.iloc[0], eq_series.values, 
                     where=eq_series.values >= eq_series.iloc[0], alpha=0.15, color="green")
    plt.fill_between(eq_series.index, eq_series.iloc[0], eq_series.values, 
                     where=eq_series.values < eq_series.iloc[0], alpha=0.15, color="red")
    plt.axhline(eq_series.iloc[0], color="gray", lw=1, linestyle="--")
    plt.title("10-Asset Portfolio Equity Curve (Donchian + Risk Parity)", fontsize=14, fontweight="bold")
    plt.xlabel("Trading Days")
    plt.ylabel("Equity (CNY)")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.savefig("portfolio_backtest_result.png", dpi=150, bbox_inches="tight")
    log.info("权益曲线图已保存至: portfolio_backtest_result.png")
    plt.show()


# ╔══════════════════════════════════════════════╗
# ║  4. 核心矩阵引擎                               ║
# ╚══════════════════════════════════════════════╝

def run_portfolio(mode="live"):
    cfg = PORTFOLIO_CONFIG
    
    # ── 引擎初始化 (区分实盘与回测) ──
    if mode == "backtest":
        log.info(f"⚙️ 初始化回测引擎 | 时间: {cfg['bt_start']} ~ {cfg['bt_end']} | 初始资金: {cfg['init_capital']}")
        sim = TqSim(init_balance=cfg["init_capital"])
        # 天勤回测需要传入 TqBacktest 对象
        api = TqApi(account=sim, backtest=TqBacktest(start_dt=cfg["bt_start"], end_dt=cfg["bt_end"]), 
                    auth=TqAuth(cfg["phone"], cfg["password"]))
    else:
        log.info("⚙️ 初始化实盘引擎...")
        sim_account = TqAccount(cfg["broker_id"], cfg["account_id"], cfg["account_pwd"])
        api = TqApi(account=sim_account, auth=TqAuth(cfg["phone"], cfg["password"]))
    
    klines_dict, quote_dict, pos_task_dict, pos_dict = {}, {}, {}, {}
    for sym in cfg["symbols"]:
        klines_dict[sym] = api.get_kline_serial(sym, cfg["kline_freq"], data_length=100)
        quote_dict[sym] = api.get_quote(sym)
    for _ in range(3):
        api.wait_update()
    symbols_to_trade = []
    for sym in cfg["symbols"]:
        q = quote_dict[sym]
        trade_sym = (getattr(q, "underlying_symbol", None) or "").strip()
        if not trade_sym or trade_sym.startswith("KQ."):
            trade_sym = sym
        q_check = api.get_quote(trade_sym) if trade_sym != sym else q
        min_vol = max(
            int(getattr(q_check, "open_min_market_order_volume", 1) or 1),
            int(getattr(q_check, "open_min_limit_order_volume", 1) or 1),
        )
        if min_vol > 1:
            log.warning(f"品种 {sym} 最小开仓手数={min_vol}，TargetPosTask 不支持，已排除")
            continue
        if sym != trade_sym:
            log.info(f"主连映射: {sym} -> {trade_sym}")
        symbols_to_trade.append(sym)
        pos_dict[sym] = api.get_position(trade_sym)
        pos_task_dict[sym] = TargetPosTask(api, trade_sym)
    if not symbols_to_trade:
        raise RuntimeError("无可用交易品种，请检查配置或排除最小开仓手数>1的合约")
        
    account = api.get_account()
    equity_curve = []  # 用于收集回测权益
    last_open_state = None
    last_status_push = datetime.now()
    init_equity = 0.0  # 程序启动时权益，用于计算总盈亏

    log.info(f"🚀 系统启动成功 | 模式: {mode.upper()} | 当前权益: ¥{account.balance:,.0f}")
    if mode == "live":
        init_equity = float(account.balance)
        DAILY_RISK["start_equity"] = init_equity
        DAILY_RISK["date"] = datetime.now().date()
        _msg = matrix_start(init_equity, init_equity)
        push(_msg)
        log.info(_msg.replace("\n", " | "))
        # 启动时推送一次账户状态
        api.wait_update()
        equity = account.balance + account.float_profit
        bal = float(account.balance)
        avail = float(getattr(account, "available", bal))
        margin = float(getattr(account, "margin", 0.0))
        fp = float(account.float_profit)
        close_profit = float(getattr(account, "close_profit", 0) or 0)
        daily_pnl = equity - DAILY_RISK["start_equity"] if DAILY_RISK["start_equity"] > 0 else 0
        positions = build_positions_detail(pos_dict, quote_dict)
        total_lots = sum(p.pos_long + p.pos_short for p in pos_dict.values())
        symbols_str = "  ".join(s.split("@")[-1] if "@" in s else s for s in symbols_to_trade)
        close = ma_s = ma_l = rsi = adx = 0.0
        first = klines_dict[symbols_to_trade[0]] if symbols_to_trade else klines_dict[cfg["symbols"][0]]
        if len(first) > cfg["donchian_entry"] + 5:
            close = float(first["close"].iloc[-1])
            ma_s = float(first["close"].rolling(cfg["donchian_entry"]).mean().iloc[-1])
            ma_l = float(first["close"].rolling(cfg["donchian_entry"] * 2).mean().iloc[-1])
            try:
                d = first["close"].diff()
                g = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
                l = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
                rsi = 100 - 100 / (1 + g / (l + 1e-9))
            except Exception:
                pass
            p_dm = max(float(first["high"].diff().iloc[-1]), 0)
            m_dm = max(float(-first["low"].diff().iloc[-1]), 0)
            adx = abs(p_dm - m_dm) / (p_dm + m_dm + 1e-9) * 100
        _msg = matrix_status(bal, avail, margin, fp, equity, close_profit, daily_pnl, init_equity,
                             positions, symbols_str, total_lots, close, ma_s, ma_l, rsi, adx, label="启动")
        push(_msg)
        log.info(_msg.replace("\n", " | "))

    try:
        while True:
            api.wait_update()
            now = datetime.now()
            equity = account.balance + account.float_profit

            # 休市推送
            if mode == "live":
                is_open = is_trading_time()
                if is_open != last_open_state and not is_open:
                    _msg = matrix_close(now.strftime("%Y-%m-%d %H:%M:%S"))
                    push(_msg)
                    log.info(_msg.replace("\n", " | "))
                last_open_state = is_open

                # 5 分钟账户状态推送
                if is_open and (now - last_status_push).total_seconds() >= 300:
                    bal = float(account.balance)
                    avail = float(getattr(account, "available", bal))
                    margin = float(getattr(account, "margin", 0.0))
                    fp = float(account.float_profit)
                    close_profit = float(getattr(account, "close_profit", 0) or 0)
                    daily_pnl = equity - DAILY_RISK["start_equity"] if DAILY_RISK["start_equity"] > 0 else 0
                    positions = build_positions_detail(pos_dict, quote_dict)
                    total_lots = sum(p.pos_long + p.pos_short for p in pos_dict.values())
                    symbols_str = "  ".join(s.split("@")[-1] if "@" in s else s for s in symbols_to_trade)
                    close = ma_s = ma_l = rsi = adx = 0.0
                    first_sym = symbols_to_trade[0] if symbols_to_trade else cfg["symbols"][0]
                    kl = klines_dict[first_sym]
                    if len(kl) > cfg["donchian_entry"] + 5:
                        close = float(kl["close"].iloc[-1])
                        ma_s = float(kl["close"].rolling(cfg["donchian_entry"]).mean().iloc[-1])
                        ma_l = float(kl["close"].rolling(cfg["donchian_entry"] * 2).mean().iloc[-1])
                        try:
                            delta = kl["close"].diff()
                            gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
                            loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
                            rs = gain.iloc[-1] / (loss.iloc[-1] + 1e-9)
                            rsi = 100 - 100 / (1 + rs)
                        except Exception:
                            pass
                        p_dm = max(float(kl["high"].diff().iloc[-1]), 0)
                        m_dm = max(float(-kl["low"].diff().iloc[-1]), 0)
                        adx = abs(p_dm - m_dm) / (p_dm + m_dm + 1e-9) * 100
                    _msg = matrix_status(bal, avail, margin, fp, equity, close_profit, daily_pnl, init_equity,
                                         positions, symbols_str, total_lots, close, ma_s, ma_l, rsi, adx)
                    push(_msg)
                    log.info(_msg.replace("\n", " | "))
                    last_status_push = now

            # 当任意一根K线收盘时触发逻辑
            if any(api.is_changing(klines_dict[sym].iloc[-1], "datetime") for sym in cfg["symbols"]):
                
                equity = account.balance + account.float_profit
                
                # 收集日度权益（回测用）
                if mode == "backtest":
                    equity_curve.append(equity)
                
                # 风控状态检查 (回测模式不锁日内时间)
                is_melted = False if mode == "backtest" else check_daily_circuit_breaker(equity)
                current_risk_ratio = get_dynamic_risk(equity)
                is_safe_time = is_safe_entry_time(mode)
                
                for sym in symbols_to_trade:
                    kl = klines_dict[sym]
                    if len(kl) < cfg["donchian_entry"] + 5:
                        continue
                        
                    # 1. 唐奇安通道
                    high_20 = kl["high"].rolling(cfg["donchian_entry"]).max().shift(1).iloc[-1]
                    low_20  = kl["low"].rolling(cfg["donchian_entry"]).min().shift(1).iloc[-1]
                    high_10 = kl["high"].rolling(cfg["donchian_exit"]).max().shift(1).iloc[-1]
                    low_10  = kl["low"].rolling(cfg["donchian_exit"]).min().shift(1).iloc[-1]
                    
                    # 2. ATR、ADX、RSI
                    try:
                        atr_val = ATR(kl, 14)["atr"].iloc[-1]
                        p_dm = max(float(kl["high"].diff().iloc[-1]), 0)
                        m_dm = max(float(-kl["low"].diff().iloc[-1]), 0)
                        adx_approx = abs(p_dm - m_dm) / (p_dm + m_dm + 1e-9) * 100
                        d = kl["close"].diff()
                        g = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
                        l = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
                        rsi_val = 100 - 100 / (1 + g / (l + 1e-9))
                    except Exception:
                        continue
                        
                    close_price = float(kl["close"].iloc[-1])
                    cur_pos = pos_dict[sym].pos_long - pos_dict[sym].pos_short
                    
                    # 3. 仓位计算 (Risk Parity)
                    multiplier = quote_dict[sym].volume_multiple
                    if atr_val > 0 and multiplier > 0:
                        loss_per_lot = atr_val * 2 * multiplier 
                        calc_lots = (equity * current_risk_ratio) / loss_per_lot
                        lots = max(1, int(np.floor(calc_lots))) if loss_per_lot > 0 else 1
                    else:
                        lots = 1
                    
                    trend_ok = adx_approx > cfg["adx_threshold"]
                    
                    # 4. 执行开平仓
                    if close_price > high_20 and cur_pos <= 0 and trend_ok and not is_melted and is_safe_time:
                        if mode == "live":
                            ma_s = float(kl["close"].rolling(cfg["donchian_entry"]).mean().iloc[-1])
                            ma_l = float(kl["close"].rolling(cfg["donchian_entry"]*2).mean().iloc[-1])
                            fp = float(account.float_profit)
                            daily_pnl = equity - DAILY_RISK["start_equity"] if DAILY_RISK["start_equity"] > 0 else 0
                            positions = build_positions_detail(pos_dict, quote_dict)
                            _msg = matrix_long(sym, lots, close_price, ma_s, ma_l, rsi_val, adx_approx,
                                               equity, fp, daily_pnl, positions)
                            push(_msg)
                            log.info(_msg.replace("\n", " | "))
                            _trade = matrix_trade(pos_dict[sym].instrument_id, "BUY", "OPEN", lots, close_price)
                            push(_trade)
                            log.info(_trade.replace("\n", " | "))
                        pos_task_dict[sym].set_target_volume(lots)
                        
                    elif close_price < low_20 and cur_pos >= 0 and trend_ok and not is_melted and is_safe_time:
                        if mode == "live":
                            ma_s = float(kl["close"].rolling(cfg["donchian_entry"]).mean().iloc[-1])
                            ma_l = float(kl["close"].rolling(cfg["donchian_entry"]*2).mean().iloc[-1])
                            fp = float(account.float_profit)
                            daily_pnl = equity - DAILY_RISK["start_equity"] if DAILY_RISK["start_equity"] > 0 else 0
                            positions = build_positions_detail(pos_dict, quote_dict)
                            _msg = matrix_short(sym, lots, close_price, ma_s, ma_l, rsi_val, adx_approx,
                                                equity, fp, daily_pnl, positions)
                            push(_msg)
                            log.info(_msg.replace("\n", " | "))
                            _trade = matrix_trade(pos_dict[sym].instrument_id, "SELL", "OPEN", lots, close_price)
                            push(_trade)
                            log.info(_trade.replace("\n", " | "))
                        pos_task_dict[sym].set_target_volume(-lots)
                        
                    elif close_price < low_10 and cur_pos > 0:
                        if mode == "live":
                            open_p = float(getattr(pos_dict[sym], "open_price_long", 0) or 0)
                            mult = float(getattr(quote_dict[sym], "volume_multiple", 1) or 1)
                            realized = (close_price - open_p) * cur_pos * mult
                            daily_pnl = equity - DAILY_RISK["start_equity"] if DAILY_RISK["start_equity"] > 0 else 0
                            _msg = matrix_flat_long(sym, close_price, open_p, cur_pos, realized, equity, daily_pnl)
                            push(_msg)
                            log.info(_msg.replace("\n", " | "))
                            _trade = matrix_trade(pos_dict[sym].instrument_id, "SELL", "CLOSE", cur_pos, close_price)
                            push(_trade)
                            log.info(_trade.replace("\n", " | "))
                        pos_task_dict[sym].set_target_volume(0)
                        
                    elif close_price > high_10 and cur_pos < 0:
                        if mode == "live":
                            open_p = float(getattr(pos_dict[sym], "open_price_short", 0) or 0)
                            mult = float(getattr(quote_dict[sym], "volume_multiple", 1) or 1)
                            realized = (open_p - close_price) * abs(cur_pos) * mult
                            daily_pnl = equity - DAILY_RISK["start_equity"] if DAILY_RISK["start_equity"] > 0 else 0
                            _msg = matrix_flat_short(sym, close_price, open_p, abs(cur_pos), realized, equity, daily_pnl)
                            push(_msg)
                            log.info(_msg.replace("\n", " | "))
                            _trade = matrix_trade(pos_dict[sym].instrument_id, "BUY", "CLOSE", abs(cur_pos), close_price)
                            push(_trade)
                            log.info(_trade.replace("\n", " | "))
                        pos_task_dict[sym].set_target_volume(0)

    except BacktestFinished:
        # TqBacktest 回测跑完历史数据后，会抛出这个异常，属于正常流程
        log.info("✅ 历史数据回测完成。")
        print_and_plot_report(equity_curve, PORTFOLIO_CONFIG)
        
    except KeyboardInterrupt:
        log.info("接收到手动退出信号，正在关闭系统。")
    except Exception as e:
        log.error(f"系统异常: {e}", exc_info=True)
        if mode == "live":
            _msg = matrix_error(str(e))
            push(_msg)
            log.error(_msg.replace("\n", " | "))
    finally:
        api.close()
        log.info("API 连接已断开。")

if __name__ == "__main__":
    # 解析命令行参数
    run_mode = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    
    if run_mode not in ["live", "backtest"]:
        print("参数错误。请使用: python script.py [live|backtest]")
        sys.exit(1)
        
    if run_mode == "backtest":
        run_portfolio(mode="backtest")
    else:
        # 实盘死循环守护
        while True:
            try:
                run_portfolio(mode="live")
            except Exception as e:
                log.error(f"实盘进程崩溃，10 秒后重启: {e}")
                _msg = matrix_error(str(e))
                push(_msg)
                log.error(_msg.replace("\n", " | "))
                time_module.sleep(10)