"""
===============================================================
  10品种跨板块量化矩阵系统 (全周期兼容版: 5m/10m/30m/60m/1d)

  运行方式示例（在 program 目录下）:
  - 5分钟线回测:  python -m strategy.portfolio_intraday_matrix backtest 5m
  - 60分钟线实盘: python -m strategy.portfolio_intraday_matrix live 60m
  - 日线回测:     python -m strategy.portfolio_intraday_matrix backtest 1d

  核心升级:
  1. 动态时间周期映射，参数随周期放大 (防分钟线假突破)
  2. 信号与执行分离: K线收盘只缓存目标，行情Tick才报单 (消灭休盘报错)
  3. 拉长K线数据量，保证 MA60/ATR 预热充分
===============================================================
"""

import os
proxy_keys = ['http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY']
for key in proxy_keys:
    if key in os.environ:
        del os.environ[key]

import logging
import sys
import time as time_module
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, date, timedelta
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from tqsdk import TqApi, TqAuth, TargetPosTask, TqKq
from tqsdk import TqBacktest, TqSim, BacktestFinished
from tqsdk.ta import ATR

warnings.filterwarnings("ignore")
plt.rcParams["font.sans-serif"] = ["SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "5m": 5 * 60,
    "10m": 10 * 60,
    "30m": 30 * 60,
    "60m": 60 * 60,
    "1d": 24 * 60 * 60,
}

TF_PARAMS = {
    "5m": {"donchian_entry": 120, "donchian_exit": 60, "ma_short_period": 120, "ma_long_period": 240, "data_length": 500},
    "10m": {"donchian_entry": 72, "donchian_exit": 36, "ma_short_period": 72, "ma_long_period": 144, "data_length": 400},
    "30m": {"donchian_entry": 40, "donchian_exit": 20, "ma_short_period": 40, "ma_long_period": 120, "data_length": 300},
    "60m": {"donchian_entry": 20, "donchian_exit": 10, "ma_short_period": 20, "ma_long_period": 60, "data_length": 250},
    "1d": {"donchian_entry": 20, "donchian_exit": 10, "ma_short_period": 20, "ma_long_period": 60, "data_length": 200},
}

try:
    from push import push, matrix_launched, matrix_start, matrix_open, matrix_close, matrix_status
    from push import matrix_long, matrix_short, matrix_flat_long, matrix_flat_short, matrix_trade, matrix_fuse, matrix_error
except Exception:
    push = lambda t: None
    matrix_launched = matrix_start = matrix_open = matrix_close = matrix_status = lambda *a, **k: ""
    matrix_long = matrix_short = matrix_flat_long = matrix_flat_short = matrix_trade = lambda *a, **k: ""
    matrix_fuse = matrix_error = lambda *a: ""

PORTFOLIO_CONFIG = {
    "symbols": [
        "KQ.m@CZCE.FG", "KQ.m@CZCE.SA", "KQ.m@CZCE.CF", "KQ.m@CZCE.UR",
        "KQ.m@DCE.y", "KQ.m@DCE.p", "KQ.m@DCE.m",
        "KQ.m@SHFE.hc", "KQ.m@SHFE.ao",
        "KQ.m@GFEX.lc"
    ],
    "adx_threshold": 20,
    "use_ma_filter": True,
    "use_parallel": True,
    "max_workers": 8,
    "init_capital": 500_000.0,
    "bt_start": date(2025, 2, 1),
    "bt_end": date(2025, 3, 1),
    **__import__("conf.config_account", fromlist=["TQ_ACCOUNT"]).TQ_ACCOUNT,
}

WATERMARK = {"high_watermark": 0.0, "drawdown_limit": 0.05, "base_risk": 0.01, "safe_risk": 0.003}
DAILY_RISK = {"date": datetime.now().date(), "start_equity": 0.0, "max_daily_loss": 5000.0, "is_locked": False}

TRADING_WINDOWS = [
    (time(9, 1), time(10, 14)), (time(10, 31), time(11, 29)),
    (time(13, 31), time(14, 55)), (time(21, 1), time(22, 55)),
]


def is_trading_time() -> bool:
    return any(s <= datetime.now().time() <= e for s, e in TRADING_WINDOWS)


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
        push(matrix_fuse(-daily_pnl))
        log.warning(f"日内熔断: 今日亏损 {daily_pnl:,.0f}")
        return True
    return DAILY_RISK["is_locked"]


def get_dynamic_risk(current_equity: float) -> float:
    if current_equity > WATERMARK["high_watermark"]:
        WATERMARK["high_watermark"] = current_equity
    if WATERMARK["high_watermark"] <= 0:
        return WATERMARK["base_risk"]
    dd = (WATERMARK["high_watermark"] - current_equity) / WATERMARK["high_watermark"]
    return WATERMARK["safe_risk"] if dd > WATERMARK["drawdown_limit"] else WATERMARK["base_risk"]


def is_safe_entry_time(mode: str) -> bool:
    if mode == "backtest":
        return True
    now = datetime.now().time()
    safe = [(time(9, 5), time(10, 0)), (time(10, 35), time(11, 15)),
            (time(13, 35), time(14, 45)), (time(21, 5), time(22, 45))]
    return any(s <= now <= e for s, e in safe)


def build_positions_detail(pos_dict: dict, quote_dict: dict) -> list:
    out = []
    for sym, pos in pos_dict.items():
        pl, ps = pos.pos_long - 0, pos.pos_short - 0
        last_p = float(getattr(quote_dict.get(sym), "last_price", 0) or 0)
        disp = getattr(pos, "instrument_id", sym)
        if pl > 0:
            op = float(getattr(pos, "open_price_long", 0) or 0)
            fp = float(getattr(pos, "float_profit_long", 0) or 0)
            out.append({"sym": disp, "direction": "多", "lots": pl, "open_price": op, "float_profit": fp, "last_price": last_p})
        if ps > 0:
            op = float(getattr(pos, "open_price_short", 0) or 0)
            fp = float(getattr(pos, "float_profit_short", 0) or 0)
            out.append({"sym": disp, "direction": "空", "lots": ps, "open_price": op, "float_profit": fp, "last_price": last_p})
    return out


def print_and_plot_report(equity_list: list, cfg: dict, tf_str: str):
    if not equity_list:
        return
    eq = pd.Series(equity_list)
    total_ret = (eq.iloc[-1] - eq.iloc[0]) / eq.iloc[0]
    roll_max = eq.cummax()
    max_dd = ((eq - roll_max) / roll_max).min()
    daily_ret = eq.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if len(daily_ret) > 0 and daily_ret.std() > 0 else 0
    print("\n" + "=" * 55)
    print(f" 10品种矩阵 ({tf_str} 级别) - 回测报告")
    print("=" * 55)
    print(f" 初始资金:   {cfg['init_capital']:,.0f}")
    print(f" 最终权益:   {eq.iloc[-1]:,.2f}")
    print(f" 总收益率:   {total_ret:.2%}")
    print(f" 最大回撤:   {max_dd:.2%}")
    print(f" 夏普比率:   {sharpe:.2f}")
    print("=" * 55 + "\n")
    plt.figure(figsize=(12, 6))
    plt.plot(eq.index, eq.values, color="#2ecc71", lw=2)
    plt.axhline(eq.iloc[0], color="gray", ls="--")
    plt.title(f"Portfolio Equity (Timeframe: {tf_str})", fontsize=14)
    plt.grid(alpha=0.3)
    plt.savefig(f"portfolio_backtest_{tf_str.replace('/', '_')}.png", dpi=150)
    log.info(f"权益曲线已保存: portfolio_backtest_{tf_str.replace('/', '_')}.png")
    plt.show()


def _compute_indicators(args):
    sym, kl, cfg = args
    de = cfg["donchian_entry"]
    dx = cfg["donchian_exit"]
    ms = cfg["ma_short_period"]
    ml = cfg["ma_long_period"]
    max_period = max(de, ml)
    if len(kl) < max_period + 6:
        return None
    try:
        high_20 = float(kl["high"].rolling(de).max().shift(1).iloc[-2])
        low_20 = float(kl["low"].rolling(de).min().shift(1).iloc[-2])
        high_10 = float(kl["high"].rolling(dx).max().shift(1).iloc[-2])
        low_10 = float(kl["low"].rolling(dx).min().shift(1).iloc[-2])
        ma_short = float(kl["close"].rolling(ms).mean().iloc[-2])
        ma_long = float(kl["close"].rolling(ml).mean().iloc[-2])
        ma_long_ok = (ma_short > ma_long) if cfg.get("use_ma_filter", True) else True
        ma_short_ok = (ma_short < ma_long) if cfg.get("use_ma_filter", True) else True
        atr_val = float(ATR(kl, 14)["atr"].iloc[-2])
        p_dm = max(float(kl["high"].diff().iloc[-2]), 0)
        m_dm = max(float(-kl["low"].diff().iloc[-2]), 0)
        adx_approx = abs(p_dm - m_dm) / (p_dm + m_dm + 1e-9) * 100
        d = kl["close"].diff()
        g = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean().iloc[-2]
        l = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean().iloc[-2]
        rsi_val = 100 - 100 / (1 + g / (l + 1e-9))
        close_price = float(kl["close"].iloc[-2])
        return {
            "sym": sym, "high_20": high_20, "low_20": low_20, "high_10": high_10, "low_10": low_10,
            "ma_short": ma_short, "ma_long": ma_long, "ma_long_ok": ma_long_ok, "ma_short_ok": ma_short_ok,
            "atr_val": atr_val, "adx_approx": adx_approx, "rsi_val": rsi_val, "close_price": close_price,
        }
    except Exception:
        return None


def run_portfolio(mode="live", tf_str="60m"):
    cfg = {**PORTFOLIO_CONFIG, **TF_PARAMS.get(tf_str, TF_PARAMS["60m"])}
    kline_freq = TIMEFRAME_MAP.get(tf_str, 3600)
    data_len = cfg["data_length"]
    if mode == "live":
        # 保证指标计算所需K线（5m需ma_long=240+6=246根），否则价/MA/RSI/ADX全为0
        min_required = max(cfg.get("donchian_entry", 20), cfg.get("ma_long_period", 60)) + 10
        data_len = max(min(data_len, 350), min_required)

    if mode == "live":
        push(matrix_launched(tf_str))

    if mode == "backtest":
        log.info(f"初始化回测 | {cfg['bt_start']}~{cfg['bt_end']} | 周期:{tf_str}")
        sim = TqSim(init_balance=cfg["init_capital"])
        api = TqApi(account=sim, backtest=TqBacktest(start_dt=cfg["bt_start"], end_dt=cfg["bt_end"]),
                    auth=TqAuth(cfg["phone"], cfg["password"]))
    else:
        log.info("初始化实盘...")
        acc = TqKq()
        api = TqApi(account=acc, auth=TqAuth(cfg["phone"], cfg["password"]))

    klines_dict, quote_dict, pos_task_dict, pos_dict = {}, {}, {}, {}
    quote_tick_dict = {}
    target_pos_dict = {}
    last_insert_target = {}
    min_vol_dict, trade_sym_dict, use_insert_order = {}, {}, {}

    for sym in cfg["symbols"]:
        klines_dict[sym] = api.get_kline_serial(sym, kline_freq, data_length=data_len)
        quote_dict[sym] = api.get_quote(sym)
    if mode == "live":
        print("正在等待初始K线数据（约10个品种），请稍候...", flush=True)
        for i in range(6):
            api.wait_update(deadline=time_module.time() + 15)
            min_bars = min(len(klines_dict[s]) for s in cfg["symbols"]) if klines_dict else 0
            if min_bars >= 2:
                log.info(f"K线数据就绪，最少 {min_bars} 根")
                break
        if min_bars < 2:
            log.warning("休市时段K线数据可能不足，建议在交易时段启动")
    else:
        for _ in range(3):
            api.wait_update()

    symbols_to_trade = []
    for sym in cfg["symbols"]:
        q = quote_dict[sym]
        trade_sym = (getattr(q, "underlying_symbol", None) or "").strip()
        if not trade_sym or trade_sym.startswith("KQ."):
            trade_sym = sym
        q_check = api.get_quote(trade_sym) if trade_sym != sym else q
        min_vol = max(int(getattr(q_check, "open_min_market_order_volume", 1) or 1),
                     int(getattr(q_check, "open_min_limit_order_volume", 1) or 1))
        min_vol_dict[sym] = min_vol
        trade_sym_dict[sym] = trade_sym
        use_insert_order[sym] = min_vol > 1
        if min_vol > 1:
            log.info(f"品种 {sym} 最小开仓={min_vol}，使用 insert_order")
        if sym != trade_sym:
            log.info(f"主连映射: {sym} -> {trade_sym}")
        symbols_to_trade.append(sym)
        pos_dict[sym] = api.get_position(trade_sym)
        pos_task_dict[sym] = None if use_insert_order[sym] else TargetPosTask(api, trade_sym)
        quote_tick_dict[sym] = api.get_quote(trade_sym)
        target_pos_dict[sym] = 0
        last_insert_target[sym] = None

    if not symbols_to_trade:
        raise RuntimeError("无可用交易品种")

    account = api.get_account()
    equity_curve = []
    last_open_state = None
    last_status_push = datetime.now()
    init_equity = 0.0
    pushed_trade_ids = set()
    try:
        _t0 = api.get_trade() if hasattr(api, "get_trade") else {}
        if hasattr(_t0, "keys"):
            pushed_trade_ids = set(_t0.keys())
    except Exception:
        pass

    executor = None
    if mode == "backtest" and cfg.get("use_parallel", True):
        nw = min(cfg.get("max_workers", 8), len(symbols_to_trade))
        executor = ThreadPoolExecutor(max_workers=nw)

    startup_msg = f"[OK] 系统启动 | 模式:{mode.upper()} | 周期:{tf_str} | 权益:¥{account.balance:,.0f}"
    print(startup_msg, flush=True)
    log.info(startup_msg)

    if mode == "live":
        init_equity = float(account.balance)
        DAILY_RISK["start_equity"] = init_equity
        DAILY_RISK["date"] = datetime.now().date()
        _msg = matrix_start(init_equity, init_equity)
        push(_msg)
        log.info(f"已推送启动通知: {_msg[:80]}...")

    try:
        while True:
            if mode == "live":
                api.wait_update(deadline=time_module.time() + 30)
            else:
                api.wait_update()
            now = datetime.now()
            equity = account.balance + account.float_profit

            if mode == "live":
                is_open = is_trading_time()
                now_str = now.strftime("%Y-%m-%d %H:%M:%S")
                if is_open != last_open_state:
                    if is_open:
                        push(matrix_open(now_str))
                    else:
                        push(matrix_close(now_str))
                last_open_state = is_open
                if (now - last_status_push).total_seconds() >= 120:
                    positions = build_positions_detail(pos_dict, quote_dict)
                    total_lots = sum(p.pos_long + p.pos_short for p in pos_dict.values())
                    symbols_str = " ".join(s.split("@")[-1] if "@" in s else s for s in symbols_to_trade)
                    close_v, ma_s_v, ma_l_v, rsi_v, adx_v = 0.0, 0.0, 0.0, 0.0, 0.0
                    approach_alerts = []
                    for sym in symbols_to_trade:
                        sig = _compute_indicators((sym, klines_dict[sym], cfg))
                        if sig:
                            close_v = sig["close_price"] if close_v == 0 else close_v
                            ma_s_v = sig["ma_short"] if ma_s_v == 0 else ma_s_v
                            ma_l_v = sig["ma_long"] if ma_l_v == 0 else ma_l_v
                            rsi_v = sig["rsi_val"] if rsi_v == 0 else rsi_v
                            adx_v = sig["adx_approx"] if adx_v == 0 else adx_v
                            cur_pos = pos_dict[sym].pos_long - pos_dict[sym].pos_short
                            approach_alerts.append({
                                "sym": sym, "close": sig["close_price"], "h20": sig["high_20"], "l20": sig["low_20"],
                                "h10": sig["high_10"], "l10": sig["low_10"], "cur_pos": cur_pos,
                            })
                    # K线不足时，至少用首个品种的现价
                    if close_v == 0 and symbols_to_trade:
                        q = quote_dict.get(symbols_to_trade[0])
                        if q:
                            close_v = float(getattr(q, "last_price", 0) or 0)
                    push(matrix_status(float(account.balance), float(getattr(account, "available", account.balance)),
                         float(getattr(account, "margin", 0)), float(account.float_profit), equity,
                         float(getattr(account, "close_profit", 0) or 0), equity - DAILY_RISK["start_equity"],
                         init_equity, positions, symbols_str, total_lots, close_v, ma_s_v, ma_l_v, rsi_v, adx_v,
                         label="2分钟", approach_alerts=approach_alerts))
                    last_status_push = now

            try:
                trades_obj = api.get_trade() if hasattr(api, "get_trade") else None
                if trades_obj is not None:
                    for tid, t in (trades_obj.items() if hasattr(trades_obj, "items") else []):
                        if tid in pushed_trade_ids:
                            continue
                        pushed_trade_ids.add(tid)
                        inst = getattr(t, "instrument_id", "") or ""
                        direction = getattr(t, "direction", "BUY") or "BUY"
                        offset = getattr(t, "offset", "OPEN") or "OPEN"
                        vol = int(getattr(t, "volume", 0) or 0)
                        price = float(getattr(t, "price", 0) or 0)
                        if vol > 0 and inst:
                            push(matrix_trade(inst, direction, offset, vol, price))
                            log.info(f"成交推送: {inst} {direction} {offset} {vol}手 @{price}")
            except Exception as e:
                log.debug(f"成交回报检查: {e}")

            if any(api.is_changing(klines_dict[sym].iloc[-1], "datetime") for sym in cfg["symbols"] if len(klines_dict[sym]) >= 2):
                equity = account.balance + account.float_profit
                if mode == "backtest":
                    equity_curve.append(equity)
                is_melted = False if mode == "backtest" else check_daily_circuit_breaker(equity)
                current_risk_ratio = get_dynamic_risk(equity)
                is_safe_time = is_safe_entry_time(mode)

                if executor:
                    tasks = [(s, klines_dict[s], cfg) for s in symbols_to_trade]
                    results = list(executor.map(_compute_indicators, tasks))
                    sig_list = [(symbols_to_trade[i], results[i]) for i in range(len(symbols_to_trade)) if results[i]]
                else:
                    sig_list = [(s, _compute_indicators((s, klines_dict[s], cfg))) for s in symbols_to_trade]
                    sig_list = [(s, r) for s, r in sig_list if r]

                for sym, sig in sig_list:
                    cur_pos = pos_dict[sym].pos_long - pos_dict[sym].pos_short
                    mult = quote_dict[sym].volume_multiple
                    min_vol = min_vol_dict.get(sym, 1)
                    atr_val = sig["atr_val"]
                    if atr_val > 0 and mult > 0:
                        loss_per = atr_val * 2 * mult
                        raw = max(1, int(np.floor((equity * current_risk_ratio) / loss_per)))
                        lots = max(min_vol, int(np.floor(raw / min_vol)) * min_vol)
                    else:
                        lots = min_vol
                    trend_ok = sig["adx_approx"] > cfg["adx_threshold"]
                    cp, h20, l20, h10, l10 = sig["close_price"], sig["high_20"], sig["low_20"], sig["high_10"], sig["low_10"]
                    ma_lo, ma_sh = sig["ma_long_ok"], sig["ma_short_ok"]

                    if cp > h20 and cur_pos <= 0 and trend_ok and not is_melted and is_safe_time and ma_lo:
                        target_pos_dict[sym] = lots
                        if mode == "live":
                            push(matrix_long(sym, lots, cp, sig["ma_short"], sig["ma_long"], sig["rsi_val"],
                                         sig["adx_approx"], equity, float(account.float_profit),
                                         equity - DAILY_RISK["start_equity"], build_positions_detail(pos_dict, quote_dict),
                                         h20=sig["high_20"], l10=sig["low_10"]))
                    elif cp < l20 and cur_pos >= 0 and trend_ok and not is_melted and is_safe_time and ma_sh:
                        target_pos_dict[sym] = -lots
                        if mode == "live":
                            push(matrix_short(sym, lots, cp, sig["ma_short"], sig["ma_long"], sig["rsi_val"],
                                          sig["adx_approx"], equity, float(account.float_profit),
                                          equity - DAILY_RISK["start_equity"], build_positions_detail(pos_dict, quote_dict),
                                          l20=sig["low_20"], h10=sig["high_10"]))
                    elif cp < l10 and cur_pos > 0:
                        target_pos_dict[sym] = 0
                        if mode == "live":
                            op = float(getattr(pos_dict[sym], "open_price_long", 0) or 0)
                            rl = (cp - op) * cur_pos * float(getattr(quote_dict[sym], "volume_multiple", 1) or 1)
                            push(matrix_flat_long(sym, cp, op, cur_pos, rl, equity, equity - DAILY_RISK["start_equity"], l10=sig["low_10"]))
                    elif cp > h10 and cur_pos < 0:
                        target_pos_dict[sym] = 0
                        if mode == "live":
                            op = float(getattr(pos_dict[sym], "open_price_short", 0) or 0)
                            rl = (op - cp) * abs(cur_pos) * float(getattr(quote_dict[sym], "volume_multiple", 1) or 1)
                            push(matrix_flat_short(sym, cp, op, abs(cur_pos), rl, equity, equity - DAILY_RISK["start_equity"], h10=sig["high_10"]))

                if mode == "backtest":
                    for sym in symbols_to_trade:
                        cur_real = pos_dict[sym].pos_long - pos_dict[sym].pos_short
                        target = target_pos_dict[sym]
                        if cur_real == target:
                            last_insert_target[sym] = None
                            continue
                        ts = trade_sym_dict[sym]
                        if pos_task_dict[sym]:
                            pos_task_dict[sym].set_target_volume(target)
                        else:
                            if last_insert_target.get(sym) == target:
                                continue
                            delta = target - cur_real
                            if delta > 0:
                                off = "CLOSE" if cur_real < 0 else "OPEN"
                                api.insert_order(symbol=ts, direction="BUY", offset=off, volume=delta)
                                last_insert_target[sym] = target
                            elif delta < 0:
                                off = "CLOSE" if cur_real > 0 else "OPEN"
                                api.insert_order(symbol=ts, direction="SELL", offset=off, volume=abs(delta))
                                last_insert_target[sym] = target

            if mode == "live":
                for sym in symbols_to_trade:
                    if not api.is_changing(quote_tick_dict[sym], "datetime"):
                        continue
                    if not is_safe_entry_time(mode):
                        continue
                    cur_real = pos_dict[sym].pos_long - pos_dict[sym].pos_short
                    target = target_pos_dict[sym]
                    if cur_real == target:
                        last_insert_target[sym] = None
                        continue
                    ts = trade_sym_dict[sym]
                    if pos_task_dict[sym]:
                        pos_task_dict[sym].set_target_volume(target)
                    else:
                        if last_insert_target.get(sym) == target:
                            continue
                        delta = target - cur_real
                        if delta > 0:
                            off = "CLOSE" if cur_real < 0 else "OPEN"
                            api.insert_order(symbol=ts, direction="BUY", offset=off, volume=delta)
                            last_insert_target[sym] = target
                        elif delta < 0:
                            off = "CLOSE" if cur_real > 0 else "OPEN"
                            api.insert_order(symbol=ts, direction="SELL", offset=off, volume=abs(delta))
                            last_insert_target[sym] = target

    except BacktestFinished:
        log.info("回测完成")
        print_and_plot_report(equity_curve, cfg, tf_str)
    except KeyboardInterrupt:
        log.info("手动退出")
    except Exception as e:
        log.error(f"异常: {e}", exc_info=True)
        if mode == "live":
            push(matrix_error(str(e)))
    finally:
        if executor:
            executor.shutdown(wait=False)
        api.close()


if __name__ == "__main__":
    run_mode = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    tf_str = sys.argv[2] if len(sys.argv) > 2 else "60m"
    if run_mode not in ["live", "backtest"]:
        print("模式: live | backtest")
        sys.exit(1)
    if tf_str not in TIMEFRAME_MAP:
        print(f"周期: {' | '.join(TIMEFRAME_MAP.keys())}")
        sys.exit(1)
    if run_mode == "backtest":
        run_portfolio(mode="backtest", tf_str=tf_str)
    else:
        while True:
            try:
                run_portfolio(mode="live", tf_str=tf_str)
            except Exception as e:
                log.error(f"崩溃，10秒后重启: {e}")
                push(matrix_error(str(e)))
                time_module.sleep(10)
