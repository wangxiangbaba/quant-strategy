"""
===============================================================
  10品种跨板块量化矩阵系统 (双核引擎: 趋势 + 均值回归)

  运行方式示例（在 program 目录下）:
  - 5分钟线(均值回归):  python -m strategy.portfolio_intraday_matrix backtest 5m
  - 60分钟线(趋势突破): python -m strategy.portfolio_intraday_matrix live 60m

  核心升级:
  1. 【双核引擎】5m/10m/30m 跑布林带均值回归；60m/1d 跑唐奇安趋势突破
  2. 【防接飞刀】均值回归中 ADX 过滤，避免单边暴涨/暴跌时逆势开仓
  3. 【单品种独立熔断】Tick 级盯市，亏损达标即强平并关小黑屋
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

from strategy.risk_manager import RiskManager
from strategy.order_executor import OrderExecutor
from strategy.engines import get_engine
from strategy.indicators import compute_indicators, compute_indicators_batch
from strategy.config import PortfolioConfig, TIMEFRAME_MAP
from strategy.utils import (
    is_trading_time,
    is_safe_entry_time,
    is_in_session_open_protection,
    get_dynamic_risk,
    build_positions_detail,
    print_and_plot_report,
)

warnings.filterwarnings("ignore")
plt.rcParams["font.sans-serif"] = ["SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

try:
    from push import push, matrix_launched, matrix_start, matrix_open, matrix_close, matrix_status
    from push import matrix_long, matrix_short, matrix_flat_long, matrix_flat_short, matrix_trade, matrix_fuse, matrix_error, matrix_symbol_fuse, matrix_backtest_report
    import push.trade_log as trade_log
except Exception:
    push = lambda t: None
    matrix_launched = matrix_start = matrix_open = matrix_close = matrix_status = lambda *a, **k: ""
    matrix_long = matrix_short = matrix_flat_long = matrix_flat_short = matrix_trade = lambda *a, **k: ""
    matrix_fuse = matrix_error = matrix_symbol_fuse = matrix_backtest_report = lambda *a, **k: ""
    trade_log = None


def run_portfolio(mode="live", tf_str="60m"):
    cfg = PortfolioConfig.from_tf(tf_str)
    cfg.adjust_data_length_for_live(mode)
    kline_freq = cfg["_kline_freq"]
    data_len = cfg["data_length"]
    strategy_type = cfg.strategy_type

    if mode == "live":
        engine_name = "均值回归(MR)" if strategy_type == "mr" else "趋势突破(Trend)"
        push(matrix_launched(tf_str, strategy_type))

    if mode == "backtest":
        log.info(f"初始化回测 | 引擎:{strategy_type.upper()} | {cfg['bt_start']}~{cfg['bt_end']} | 周期:{tf_str}")
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

    order_exec = OrderExecutor(
        pos_task_dict, pos_dict, trade_sym_dict, use_insert_order, symbols_to_trade
    )
    risk_mgr = RiskManager(symbols_to_trade, cfg["max_daily_loss_per_symbol"])
    engine = get_engine(tf_str)

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
        _msg = matrix_start(init_equity, init_equity, tf_str=tf_str, strategy_type=strategy_type)
        push(_msg)
        log.info(f"已推送启动通知: {_msg[:80]}...")
        if trade_log:
            trade_log.init_log_dir()
            _td = (datetime.now() - timedelta(hours=17)).date()
            trade_log.log_start(_td, init_equity, tf_str=tf_str, strategy_type=strategy_type)

    try:
        while True:
            if mode == "live":
                api.wait_update(deadline=time_module.time() + 30)
            else:
                api.wait_update()
            now = datetime.now()
            equity = account.balance + account.float_profit

            # 单品种熔断：RiskManager 更新（交易日历、Tick 盈亏、熔断）
            fuse_events, date_changed = risk_mgr.update(now, equity, pos_dict, quote_dict, target_pos_dict, mode)
            if date_changed and mode == "live":
                log.info(f"🌅 跨越结算线，各品种日内熔断额度重置 (归属日: {risk_mgr.trade_date})")
            for sym, loss_val in fuse_events:
                push(matrix_symbol_fuse(sym, loss_val, tf_str=tf_str, strategy_type=strategy_type))
                log.warning(f"🔴 单品种跳闸: {sym} 今日亏损 ¥{loss_val:,.0f}，已强平并关小黑屋")
                if trade_log:
                    trade_log.log_fuse(risk_mgr.trade_date, sym, loss_val, tf_str=tf_str)

            if mode == "live":
                is_open = is_trading_time()
                now_str = now.strftime("%Y-%m-%d %H:%M:%S")
                if is_open != last_open_state:
                    if is_open:
                        push(matrix_open(now_str, tf_str=tf_str, strategy_type=strategy_type))
                        if trade_log:
                            trade_log.log_open((now - timedelta(hours=17)).date(), now_str, tf_str=tf_str)
                    else:
                        push(matrix_close(now_str, tf_str=tf_str, strategy_type=strategy_type))
                        if trade_log:
                            trade_log.log_close((now - timedelta(hours=17)).date(), now_str, tf_str=tf_str)
                last_open_state = is_open
                if (now - last_status_push).total_seconds() >= 120:
                    status_sig_dict = {}
                    total_lots = sum(p.pos_long + p.pos_short for p in pos_dict.values())
                    symbols_str = " ".join(s.split("@")[-1] if "@" in s else s for s in symbols_to_trade)
                    close_v, ma_s_v, ma_l_v, rsi_v, adx_v = 0.0, 0.0, 0.0, 0.0, 0.0
                    approach_alerts = []
                    for sym in symbols_to_trade:
                        sig = compute_indicators(sym, klines_dict[sym], cfg)
                        if sig:
                            status_sig_dict[sym] = sig
                        if sig:
                            close_v = sig["close_price"] if close_v == 0 else close_v
                            ma_s_v = sig["ma_short"] if ma_s_v == 0 else ma_s_v
                            ma_l_v = sig["ma_long"] if ma_l_v == 0 else ma_l_v
                            rsi_v = sig["rsi_val"] if rsi_v == 0 else rsi_v
                            adx_v = sig["adx_approx"] if adx_v == 0 else adx_v
                            cur_pos = pos_dict[sym].pos_long - pos_dict[sym].pos_short
                            if strategy_type == "mr":
                                approach_alerts.append({
                                    "sym": sym, "close": sig["close_price"], "h20": sig["bb_dn"], "l20": sig["bb_up"],
                                    "h10": sig["bb_mid"], "l10": sig["bb_mid"], "cur_pos": cur_pos,
                                })
                            else:
                                approach_alerts.append({
                                    "sym": sym, "close": sig["close_price"], "h20": sig["high_20"], "l20": sig["low_20"],
                                    "h10": sig["high_10"], "l10": sig["low_10"], "cur_pos": cur_pos,
                                })
                    # 按品种构建指标字典 {sym: {close_price, ma_short, ...}}；live 模式下「价」用实时行情
                    symbol_indicators = {}
                    for sym in symbols_to_trade:
                        sig = status_sig_dict.get(sym)
                        if not sig:
                            continue
                        close_p = sig["close_price"]
                        if quote_dict:
                            q = quote_dict.get(sym)
                            if q:
                                live_p = float(getattr(q, "last_price", 0) or 0)
                                if live_p > 0:
                                    close_p = live_p
                        symbol_indicators[sym] = {
                            "close_price": close_p,
                            "ma_short": sig["ma_short"],
                            "ma_long": sig["ma_long"],
                            "rsi_val": sig["rsi_val"],
                            "adx_approx": sig["adx_approx"],
                        }
                    if not symbol_indicators:
                        close_v = 0.0
                        if symbols_to_trade and quote_dict:
                            q = quote_dict.get(symbols_to_trade[0])
                            if q:
                                live_p = float(getattr(q, "last_price", 0) or 0)
                                if live_p > 0:
                                    close_v = live_p
                    positions = build_positions_detail(pos_dict, quote_dict, status_sig_dict, cfg, strategy_type)
                    push(matrix_status(float(account.balance), float(getattr(account, "available", account.balance)),
                         float(getattr(account, "margin", 0)), float(account.float_profit), equity,
                         float(getattr(account, "close_profit", 0) or 0), equity - risk_mgr.daily_start_equity,
                         init_equity, positions, symbols_str, total_lots, close_v, ma_s_v, ma_l_v, rsi_v, adx_v,
                         label="2分钟", approach_alerts=approach_alerts, tf_str=tf_str, strategy_type=strategy_type,
                         symbol_indicators=symbol_indicators or None))
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
                            push(matrix_trade(inst, direction, offset, vol, price, tf_str=tf_str, strategy_type=strategy_type))
                            log.info(f"成交推送: {inst} {direction} {offset} {vol}手 @{price}")
                            if trade_log:
                                trade_log.log_trade_fill(
                                    (now - timedelta(hours=17)).date(), inst, direction, offset, vol, price,
                                    tf_str=tf_str, strategy_type=strategy_type)
            except Exception as e:
                log.debug(f"成交回报检查: {e}")

            current_risk_ratio = get_dynamic_risk(equity)

            if any(api.is_changing(klines_dict[sym].iloc[-1], "datetime") for sym in cfg["symbols"] if len(klines_dict[sym]) >= 2):
                if mode == "backtest":
                    equity_curve.append(equity)
                is_safe_time = is_safe_entry_time(mode)

                sig_list = compute_indicators_batch(
                    klines_dict, symbols_to_trade, cfg, executor
                )
                sig_dict = dict(sig_list)

                for sym, sig in sig_list:
                    if mode == "live" and risk_mgr.is_locked(sym):
                        continue
                    cur_pos = pos_dict[sym].pos_long - pos_dict[sym].pos_short
                    mult = float(getattr(quote_dict[sym], "volume_multiple", 1) or 1)
                    min_vol = min_vol_dict.get(sym, 1)
                    daily_pnl = equity - risk_mgr.daily_start_equity

                    target, signal_type = engine.signal(
                        sym, sig, cur_pos, pos_dict, cfg, is_safe_time, equity, mult, min_vol, current_risk_ratio,
                        in_session_open_protection=is_in_session_open_protection(now) if strategy_type == "mr" else False,
                    )
                    if target is None:
                        continue
                    target_pos_dict[sym] = target

                    if mode == "live" and signal_type:
                        cp = sig["close_price"]
                        inst = trade_sym_dict.get(sym, sym)
                        td = (now - timedelta(hours=17)).date()
                        if signal_type == "open_long":
                            msg = matrix_long(sym, target, cp, sig["ma_short"], sig["ma_long"], sig["rsi_val"],
                                              sig["adx_approx"], equity, float(account.float_profit), daily_pnl,
                                              build_positions_detail(pos_dict, quote_dict, sig_dict, cfg, strategy_type),
                                              h20=sig.get("high_20"), l10=sig.get("high_10", sig.get("bb_mid")),
                                              tf_str=tf_str, strategy_type=strategy_type)
                            if strategy_type == "mr":
                                msg = msg.replace("📈 开多", "📈 开多[均值回归(超卖)]")
                            push(msg)
                            trade_log and trade_log.log_signal(td, "开多", inst, target, cp,
                                "趋势突破" if strategy_type == "trend" else "均值回归(超卖)", equity, daily_pnl, tf_str=tf_str)
                        elif signal_type == "open_short":
                            msg = matrix_short(sym, abs(target), cp, sig["ma_short"], sig["ma_long"], sig["rsi_val"],
                                               sig["adx_approx"], equity, float(account.float_profit), daily_pnl,
                                               build_positions_detail(pos_dict, quote_dict, sig_dict, cfg, strategy_type),
                                               l20=sig.get("low_20"), h10=sig.get("low_10", sig.get("bb_mid")),
                                               tf_str=tf_str, strategy_type=strategy_type)
                            if strategy_type == "mr":
                                msg = msg.replace("📉 开空", "📉 开空[均值回归(超买)]")
                            push(msg)
                            trade_log and trade_log.log_signal(td, "开空", inst, abs(target), cp,
                                "趋势突破" if strategy_type == "trend" else "均值回归(超买)", equity, daily_pnl, tf_str=tf_str)
                        elif signal_type == "flat_long":
                            op = float(getattr(pos_dict[sym], "open_price_long", 0) or 0)
                            rl = (cp - op) * cur_pos * mult
                            msg = matrix_flat_long(sym, cp, op, cur_pos, rl, equity, daily_pnl,
                                                   l10=sig.get("low_10", sig.get("bb_mid")),
                                                   tf_str=tf_str, strategy_type=strategy_type)
                            if strategy_type == "mr":
                                msg = msg.replace("🛑 平多", "🛑 平多[回归中轨/止损]")
                            push(msg)
                            trade_log and trade_log.log_signal(td, "平多", inst, cur_pos, cp,
                                "跌破平多价" if strategy_type == "trend" else "回归中轨/止损", equity, daily_pnl,
                                realized_pnl=rl, tf_str=tf_str)
                        elif signal_type == "flat_short":
                            op = float(getattr(pos_dict[sym], "open_price_short", 0) or 0)
                            rl = (op - cp) * abs(cur_pos) * mult
                            msg = matrix_flat_short(sym, cp, op, abs(cur_pos), rl, equity, daily_pnl,
                                                    h10=sig.get("high_10", sig.get("bb_mid")),
                                                    tf_str=tf_str, strategy_type=strategy_type)
                            if strategy_type == "mr":
                                msg = msg.replace("🛑 平空", "🛑 平空[回归中轨/止损]")
                            push(msg)
                            trade_log and trade_log.log_signal(td, "平空", inst, abs(cur_pos), cp,
                                "突破平空价" if strategy_type == "trend" else "回归中轨/止损", equity, daily_pnl,
                                realized_pnl=rl, tf_str=tf_str)

                if mode == "backtest":
                    order_exec.sync_target_backtest(api, target_pos_dict)

            if mode == "live":
                order_exec.sync_target_live(
                    api, target_pos_dict, quote_tick_dict, is_safe_entry_time(mode)
                )

    except BacktestFinished:
        log.info("回测完成")
        report = print_and_plot_report(equity_curve, cfg, tf_str, strategy_type, log=log)
        if report:
            push(matrix_backtest_report(**report))
    except KeyboardInterrupt:
        log.info("手动退出")
    except Exception as e:
        log.error(f"异常: {e}", exc_info=True)
        if mode == "live":
            push(matrix_error(str(e), tf_str=tf_str, strategy_type=strategy_type))
            if trade_log:
                trade_log.log_error((datetime.now() - timedelta(hours=17)).date(), str(e), tf_str=tf_str)
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
                st = "mr" if tf_str in ["5m", "10m", "30m"] else "trend"
                push(matrix_error(str(e), tf_str=tf_str, strategy_type=st))
                time_module.sleep(10)
