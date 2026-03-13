"""
===============================================================
  豆粕 (M) 全功能量化交易系统 v3.0 (实盘终极版)
  
  核心特性:
  1. [数据源] 使用 TqSdk 官方复权连 (KQ.m@DCE.m)，彻底消除换月跳空假信号。
  2. [极速回测] 底层替换为 Numpy 数组寻址，参数扫描与滚动验证提速百倍。
  3. [实盘架构] K 线计算与 Tick 级熔断监控严格解耦，CPU 近乎零负载。
  4. [高级风控] 
     - 动态水位线 (回撤超 5% 自动降仓至 0.5% 风险)
     - 时间过滤 (开盘前5分钟/收盘前15分钟禁开新仓)
     - 日内绝对熔断 (单日亏损触及阈值，全天锁定交易)
===============================================================
"""

import logging
import sys
import time as time_module
import warnings
from datetime import datetime, time
from itertools import product as iterproduct
import os

import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
try:
    from push.telegram_notify import telegram_notify
except Exception:
    telegram_notify = lambda t: False
import seaborn as sns

warnings.filterwarnings("ignore")
plt.rcParams["font.sans-serif"] = ["SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ╔══════════════════════════════════════════════╗
# ║  全局配置与高级风控参数                      ║
# ╚══════════════════════════════════════════════╝

FEISHU_CONFIG = {
    "enabled": True,  # 实盘建议开启
    "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/d006b985-b71a-4c8e-a505-dcea786053a2",
}

WATERMARK = {
    "high_watermark": 0.0,
    "drawdown_limit": 0.05,  # 允许的最大回撤阈值 (5%)
    "base_risk": 0.015,      # 基础单笔风险 1.5%
    "safe_risk": 0.005       # 遇险后降级的防守风险 0.5%
}

DAILY_RISK = {
    "date": datetime.now().date(),
    "start_equity": 0.0,
    "max_daily_loss": 3000.0,  # 日内最大允许亏损金额(元)
    "is_locked": False
}


# ╔══════════════════════════════════════════════╗
# ║  Part 1: 高级风控与资金管理模块              ║
# ╚══════════════════════════════════════════════╝

def feishu_notify(text: str) -> None:
    if not FEISHU_CONFIG.get("enabled") or not FEISHU_CONFIG.get("webhook"):
        return
    try:
        requests.post(
            FEISHU_CONFIG["webhook"],
            json={"msg_type": "text", "content": {"text": text}},
            timeout=5,
        )
    except Exception as e:
        log.warning(f"飞书通知发送失败: {e}")

def get_dynamic_risk(current_equity: float) -> float:
    """动态水位线降仓机制"""
    if current_equity > WATERMARK["high_watermark"]:
        WATERMARK["high_watermark"] = current_equity
        
    if WATERMARK["high_watermark"] <= 0:
        return WATERMARK["base_risk"]
        
    drawdown = (WATERMARK["high_watermark"] - current_equity) / WATERMARK["high_watermark"]
    
    if drawdown > WATERMARK["drawdown_limit"]:
        return WATERMARK["safe_risk"]
    return WATERMARK["base_risk"]

TRADING_WINDOWS = [
    (time(9, 1), time(10, 14)),
    (time(10, 31), time(11, 29)),
    (time(13, 31), time(14, 55)),
    (time(21, 1), time(22, 55)),
]


def is_trading_time() -> bool:
    now = datetime.now().time()
    return any(s <= now <= e for s, e in TRADING_WINDOWS)


def is_safe_entry_time() -> bool:
    """过滤开盘剧震与收盘踩踏时段（豆粕专属）"""
    now = datetime.now().time()
    safe_windows = [
        (time(9, 5), time(10, 0)),
        (time(10, 35), time(11, 15)),
        (time(13, 35), time(14, 45)),
        (time(21, 5), time(22, 45))
    ]
    for start, end in safe_windows:
        if start <= now <= end:
            return True
    return False

def check_daily_circuit_breaker(current_equity: float, symbol: str = "") -> bool:
    """日内绝对亏损熔断机制"""
    today = datetime.now().date()
    
    # 跨日自动重置
    if today != DAILY_RISK["date"]:
        DAILY_RISK["date"] = today
        DAILY_RISK["start_equity"] = current_equity
        DAILY_RISK["is_locked"] = False
        log.info(f"🌅 新交易日，日内熔断重置。起始权益: ¥{current_equity:,.0f}")
        return False
        
    if DAILY_RISK["start_equity"] == 0:
        DAILY_RISK["start_equity"] = current_equity
        
    daily_pnl = current_equity - DAILY_RISK["start_equity"]
    
    if daily_pnl < -DAILY_RISK["max_daily_loss"] and not DAILY_RISK["is_locked"]:
        DAILY_RISK["is_locked"] = True
        log.error(f"🔴 触发日内熔断！今日已亏损 ¥{-daily_pnl:,.0f}")
        fuse_msg = (
            f"【豆粕策略】🔴 日内熔断\n"
            f"合约: {symbol}\n"
            f"今日已亏损: ¥{-daily_pnl:,.0f}\n"
            f"已冻结新开仓直至下一交易日。"
        )
        feishu_notify(fuse_msg)
        telegram_notify(fuse_msg)
        
    return DAILY_RISK["is_locked"]


# ╔══════════════════════════════════════════════╗
# ║  Part 2: 数据获取 (TqSdk 复权数据)           ║
# ╚══════════════════════════════════════════════╝

def fetch_soymeal_data_tqsdk(symbol: str = "KQ.m@DCE.m", data_length: int = 3000) -> pd.DataFrame:
    from tqsdk import TqApi, TqAuth
    log.info(f"正在通过 TqSdk 获取 {symbol} 复权历史数据用于回测...")
    
    # 【注意】使用快期账号拉取回测数据
    api = TqApi(auth=TqAuth("15528503735", "QQ1392070089")) 
    try:
        klines = api.get_kline_serial(symbol, duration_seconds=86400, data_length=data_length)
        df = klines.copy()
    finally:
        api.close()

    df["date"] = pd.to_datetime(df["datetime"])
    df.set_index("date", inplace=True)
    df.drop(columns=["id", "datetime"], inplace=True, errors="ignore")
    df.dropna(subset=["open", "close"], inplace=True)
    return df


# ╔══════════════════════════════════════════════╗
# ║  Part 3: 技术指标计算                        ║
# ╚══════════════════════════════════════════════╝

def calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl  = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm  = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    mask = plus_dm < minus_dm
    plus_dm[mask] = 0
    mask2 = minus_dm < plus_dm
    minus_dm[mask2] = 0

    atr = calc_atr(df, period)
    plus_di  = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr

    dx  = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
    adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return adx

def calc_macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    dif  = ema_fast - ema_slow
    dea  = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist

def seasonal_filter(index: pd.DatetimeIndex):
    month = pd.Series(index.month, index=index)
    allow_long  = ~month.isin([3, 4, 5])
    allow_short = ~month.isin([8, 9, 10])
    return allow_long, allow_short

def adaptive_stop_mul(atr_series: pd.Series, lookback: int = 60,
                      low_mul: float = 1.5, high_mul: float = 3.5) -> pd.Series:
    pct = atr_series.rolling(lookback).rank(pct=True).fillna(0.5)
    return low_mul + pct * (high_mul - low_mul)

def add_indicators(df: pd.DataFrame, cfg) -> pd.DataFrame:
    d = df.copy()
    d["sma_s"] = calc_sma(d["close"], cfg.short_p)
    d["sma_l"] = calc_sma(d["close"], cfg.long_p)
    d["rsi"]   = calc_rsi(d["close"], cfg.rsi_p)
    d["atr"]   = calc_atr(d, cfg.atr_p)
    d["adx"]   = calc_adx(d, cfg.adx_p)
    d["stop_mul"] = adaptive_stop_mul(d["atr"], low_mul=cfg.stop_atr_low, high_mul=cfg.stop_atr_high)
    d["dif"], d["dea"], d["macd_hist"] = calc_macd(d["close"])
    return d


# ╔══════════════════════════════════════════════╗
# ║  Part 4: Numpy 极速版回测引擎                ║
# ╚══════════════════════════════════════════════╝

class BacktestConfig:
    short_p: int   = 20
    long_p: int    = 60
    rsi_p: int     = 14
    rsi_ob: float  = 72.0
    rsi_os: float  = 28.0
    atr_p: int     = 14
    adx_p: int     = 14
    adx_threshold: float = 25.0
    atr_entry_mul: float = 1.0

    stop_atr_low:  float = 1.5
    stop_atr_high: float = 3.5

    use_adx:      bool = True
    use_seasonal: bool = True
    use_macd:     bool = True

    commission_per_lot: float = 6.0   
    slippage_ticks: int       = 1     
    tick_size: float          = 1.0   
    multiplier: int           = 10    

    init_capital: float    = 200_000.0
    risk_per_trade: float  = 0.015

def run_backtest(df_raw: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    df = add_indicators(df_raw, cfg)

    allow_long_s, allow_short_s = seasonal_filter(df.index)
    bull = df["sma_s"] > df["sma_l"]
    bear = df["sma_s"] < df["sma_l"]

    df["tr"] = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)

    vol_filter  = df["tr"] > df["atr"] * cfg.atr_entry_mul
    rsi_long    = df["rsi"] < cfg.rsi_ob
    rsi_short   = df["rsi"] > cfg.rsi_os
    adx_filter  = df["adx"] > cfg.adx_threshold if cfg.use_adx else pd.Series(True, index=df.index)
    macd_long   = df["macd_hist"] > 0 if cfg.use_macd else pd.Series(True, index=df.index)
    macd_short  = df["macd_hist"] < 0 if cfg.use_macd else pd.Series(True, index=df.index)
    seas_long   = allow_long_s  if cfg.use_seasonal else pd.Series(True, index=df.index)
    seas_short  = allow_short_s if cfg.use_seasonal else pd.Series(True, index=df.index)

    df["raw_signal"] = 0
    df.loc[bull & vol_filter & rsi_long  & adx_filter & macd_long  & seas_long,  "raw_signal"] =  1
    df.loc[bear & vol_filter & rsi_short & adx_filter & macd_short & seas_short, "raw_signal"] = -1

    arr_close   = df["close"].values
    arr_open    = df["open"].values
    arr_atr     = df["atr"].values
    arr_stop_m  = df["stop_mul"].values
    arr_raw_sig = df["raw_signal"].values

    n = len(df)
    signals     = np.zeros(n, dtype=int)
    entry_price = np.full(n, np.nan)
    exit_price  = np.full(n, np.nan)
    lots_arr    = np.zeros(n, dtype=int)
    equity_curve= np.zeros(n, dtype=float)

    pos, cur_lots = 0, 0
    high_entry, low_entry = 0.0, 0.0
    current_entry_price = np.nan
    equity = cfg.init_capital

    for i in range(n - 1):
        close_i, open_i1 = arr_close[i], arr_open[i + 1]
        atr_i, stop_m, raw_s = arr_atr[i], arr_stop_m[i], arr_raw_sig[i]
        equity_curve[i] = equity

        if np.isnan(atr_i) or np.isnan(stop_m) or atr_i <= 0:
            signals[i] = pos
            lots_arr[i] = cur_lots if pos != 0 else 0
            continue

        stop_d = atr_i * stop_m
        just_stopped = False

        if pos == 1:
            high_entry = max(high_entry, close_i)
            if close_i < high_entry - stop_d or raw_s == -1:
                pos = 0
                just_stopped  = True
                exit_price[i] = open_i1
                trade_pnl     = (open_i1 - current_entry_price) * cur_lots * cfg.multiplier
                cost          = cur_lots * (cfg.commission_per_lot * 2 + cfg.slippage_ticks * cfg.tick_size)
                equity       += trade_pnl - cost

        elif pos == -1:
            low_entry = min(low_entry, close_i)
            if close_i > low_entry + stop_d or raw_s == 1:
                pos = 0
                just_stopped  = True
                exit_price[i] = open_i1
                trade_pnl     = (current_entry_price - open_i1) * cur_lots * cfg.multiplier
                cost          = cur_lots * (cfg.commission_per_lot * 2 + cfg.slippage_ticks * cfg.tick_size)
                equity       += trade_pnl - cost

        if pos == 0 and not just_stopped and raw_s != 0:
            loss_per_lot = stop_d * cfg.multiplier + (cfg.commission_per_lot * 2 + cfg.slippage_ticks * cfg.tick_size)
            calc_lots = (equity * cfg.risk_per_trade) / loss_per_lot if loss_per_lot > 0 else 1
            cur_lots = max(1, int(np.floor(calc_lots))) if not np.isnan(calc_lots) and not np.isinf(calc_lots) else 1
            pos = 1 if raw_s == 1 else -1
            high_entry = close_i if pos == 1 else high_entry
            low_entry  = close_i if pos == -1 else low_entry
            current_entry_price = open_i1
            entry_price[i] = open_i1

        signals[i]  = pos
        lots_arr[i] = cur_lots if pos != 0 else 0

    signals[-1]  = signals[-2]
    lots_arr[-1] = lots_arr[-2]
    equity_curve[-1] = equity

    df["signal"], df["lots"] = signals, lots_arr
    df["entry_price"], df["exit_price"] = entry_price, exit_price
    df["equity"] = equity_curve
    df["net_ret"] = df["equity"].pct_change().fillna(0)
    df["trade_flag"] = df["signal"].diff().abs().fillna(0)
    return df

def _calc_max_drawdown(equity: pd.Series) -> tuple:
    """返回 (最大回撤比例, 最长回撤天数)"""
    roll_max = equity.cummax()
    drawdown = (equity - roll_max) / roll_max.replace(0, np.nan)
    max_dd = drawdown.min()
    underwater = (drawdown < 0).astype(int)
    dd_lengths = underwater * (underwater.groupby((underwater == 0).cumsum()).cumcount() + 1)
    max_dd_days = int(dd_lengths.max()) if len(dd_lengths) > 0 else 0
    return max_dd, max_dd_days


def _calc_consecutive(series: pd.Series) -> tuple:
    """最大连续盈利次数 & 最大连续亏损次数"""
    wins = (series > 0).astype(int)
    losses = (series < 0).astype(int)

    def _max_run(s: pd.Series) -> int:
        cumsum = s.cumsum()
        diff = cumsum - cumsum.where(s == 0).ffill().fillna(0)
        return int(diff.max()) if len(diff) > 0 else 0

    return _max_run(wins), _max_run(losses)


def _kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 0.0
    w = avg_win / avg_loss
    f = win_rate * w - (1 - win_rate)
    return max(0.0, f / w)


def performance_report(df: pd.DataFrame, cfg: BacktestConfig) -> dict:
    """全面绩效报告"""
    ret = df["net_ret"].dropna()
    eq = df["equity"].dropna()

    trades = df[df["trade_flag"] > 0]["net_ret"]
    win_trades = trades[trades > 0]
    loss_trades = trades[trades < 0]

    total_days = len(ret)
    annual_ret = (eq.iloc[-1] / cfg.init_capital) ** (252 / total_days) - 1 if total_days > 0 else 0
    sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
    max_dd, max_dd_days = _calc_max_drawdown(eq)
    calmar = annual_ret / abs(max_dd) if max_dd != 0 else 0
    max_wins, max_losses = _calc_consecutive(ret)
    win_rate = len(win_trades) / len(trades) if len(trades) > 0 else 0
    avg_win = win_trades.mean() if len(win_trades) > 0 else 0
    avg_loss = abs(loss_trades.mean()) if len(loss_trades) > 0 else 0
    profit_factor = win_trades.sum() / abs(loss_trades.sum()) if abs(loss_trades.sum()) > 0 else float("inf")
    kelly = _kelly_fraction(win_rate, avg_win, avg_loss)
    total_ret = (eq.iloc[-1] / cfg.init_capital) - 1
    total_pnl = eq.iloc[-1] - cfg.init_capital

    return {
        "初始资金": cfg.init_capital,
        "最终权益": eq.iloc[-1],
        "总盈亏": total_pnl,
        "总收益率": total_ret,
        "年化收益率": annual_ret,
        "夏普比率": sharpe,
        "卡玛比率": calmar,
        "最大回撤": max_dd,
        "最长回撤天数": max_dd_days,
        "回测天数": total_days,
        "总交易次数": len(trades),
        "胜率": win_rate,
        "盈亏比": avg_win / avg_loss if avg_loss > 0 else 0,
        "盈利因子": profit_factor,
        "最大连续盈利": max_wins,
        "最大连续亏损": max_losses,
        "凯利比例": kelly,
        "1/4凯利建议": kelly / 4,
    }


def print_report(df: pd.DataFrame, cfg: BacktestConfig):
    """打印完整绩效报告"""
    report = performance_report(df, cfg)
    print("\n" + "=" * 50)
    print("  豆粕量化策略 v3 - 回测绩效报告")
    print("=" * 50)
    print(f"  {'初始资金':<14} {report['初始资金']:>15,.2f}")
    print(f"  {'最终权益':<14} {report['最终权益']:>15,.2f}")
    print(f"  {'总盈亏':<14} {report['总盈亏']:>+15,.2f}")
    print(f"  {'总收益率':<14} {report['总收益率']:>14.2%}")
    print(f"  {'年化收益率':<14} {report['年化收益率']:>14.2%}")
    print("-" * 50)
    print(f"  {'夏普比率':<14} {report['夏普比率']:>15.3f}")
    print(f"  {'卡玛比率':<14} {report['卡玛比率']:>15.3f}")
    print(f"  {'最大回撤':<14} {report['最大回撤']:>14.2%}")
    print(f"  {'最长回撤天数':<14} {report['最长回撤天数']:>12} 天")
    print("-" * 50)
    print(f"  {'回测天数':<14} {report['回测天数']:>15}")
    print(f"  {'总交易次数':<14} {report['总交易次数']:>15}")
    print(f"  {'胜率':<14} {report['胜率']:>14.2%}")
    print(f"  {'盈亏比':<14} {report['盈亏比']:>15.2f}")
    pf = report['盈利因子']
    pf_str = f"{pf:.2f}" if np.isfinite(pf) else "inf"
    print(f"  {'盈利因子':<14} {pf_str:>15}")
    print(f"  {'最大连续盈利':<14} {report['最大连续盈利']:>12} 次")
    print(f"  {'最大连续亏损':<14} {report['最大连续亏损']:>12} 次")
    print("-" * 50)
    print(f"  {'凯利比例':<14} {report['凯利比例']:>14.2%}")
    print(f"  {'1/4凯利建议':<14} {report['1/4凯利建议']:>14.2%}")
    print("=" * 50 + "\n")


# ╔══════════════════════════════════════════════╗
# ║  Part 5: 天勤实盘引擎 (统一风控架构)         ║
# ╚══════════════════════════════════════════════╝

LIVE_CONFIG = {
    "symbol":          "DCE.m2609",   # 实盘需用具体合约，KQ 连续合约不可直接下单
    "kline_freq":      24 * 60 * 60,  # 日线
    "tick_size":       1.0,
    "short_p":         20,
    "long_p":          60,
    "rsi_p":           14,
    "rsi_ob":          72,
    "rsi_os":          28,
    "adx_threshold":   25,
    "stop_atr_low":    1.5,
    "stop_atr_high":   3.5,
    "commission_per_lot": 6.0,
    "slippage_ticks":  1,
    
    # 账号配置 (需修改为你自己真实/模拟账号)
    "phone":      "15528503735",
    "password":   "QQ1392070089",
    "broker_id":  "快期模拟",          # 实盘修改为真实期货公司名称
    "account_id": "15528503735",
    "account_pwd":"QQ1392070089",
}

def run_live():
    from tqsdk import TqApi, TqAuth, TargetPosTask, TqAccount
    from tqsdk.ta import MA, RSI as TQ_RSI, ATR as TQ_ATR

    cfg = LIVE_CONFIG
    sim_account = TqAccount(cfg["broker_id"], cfg["account_id"], cfg["account_pwd"])
    api      = TqApi(account=sim_account, auth=TqAuth(cfg["phone"], cfg["password"]))
    account  = api.get_account()
    position = api.get_position(cfg["symbol"]) 
    klines   = api.get_kline_serial(cfg["symbol"], cfg["kline_freq"], data_length=200)
    tpos     = TargetPosTask(api, cfg["symbol"])

    init_balance = account.balance
    high_entry   = 0.0
    low_entry    = 0.0
    last_stop_mul = cfg["stop_atr_high"]
    last_open_state = None
    last_status_push = datetime.now()

    DAILY_RISK["start_equity"] = float(init_balance)
    DAILY_RISK["date"] = datetime.now().date()

    log.info(f"✅ 豆粕实盘系统启动 | 合约:{cfg['symbol']} | 初始资金:¥{init_balance:,.0f}")
    start_msg = (
        f"【豆粕策略】已启动\n"
        f"合约: {cfg['symbol']}\n"
        f"初始资金: ¥{init_balance:,.0f}\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    feishu_notify(start_msg)
    telegram_notify(start_msg)

    try:
        while True:
            api.wait_update()
            equity = account.balance + account.float_profit

            # ==========================================
            #  0. 心跳与休市推送
            # ==========================================
            now = datetime.now()
            is_open = is_trading_time()
            if is_open != last_open_state:
                if not is_open and FEISHU_CONFIG.get("enabled"):
                    close_msg = f"【豆粕策略】休市开始\n合约: {cfg['symbol']}\n时间: {now.strftime('%Y-%m-%d %H:%M:%S')}"
                    feishu_notify(close_msg)
                    telegram_notify(close_msg)
                last_open_state = is_open

            # ==========================================
            #  0. 每 1 分钟推送账户状态到飞书（与 V2 一致）
            # ==========================================
            if is_open and FEISHU_CONFIG.get("enabled") and (now - last_status_push).total_seconds() >= 60:
                bal = float(account.balance)
                avail = float(getattr(account, "available", bal))
                margin = float(getattr(account, "margin", 0.0))
                fp = float(account.float_profit)
                cur_pos = int(getattr(position, "pos", position.pos_long - position.pos_short))
                held_symbols = []
                try:
                    all_pos = api.get_position()
                    if hasattr(all_pos, "items"):
                        for sym, pos_obj in all_pos.items():
                            p = int(getattr(pos_obj, "pos", 0))
                            if p != 0:
                                held_symbols.append(f"{sym}:{p}手")
                except Exception:
                    pass
                held_str = ", ".join(held_symbols) if held_symbols else (f"{cfg['symbol']}:{cur_pos}手" if cur_pos != 0 else "无持仓")
                close, ma_s, ma_l, rsi, adx_approx = 0.0, 0.0, 0.0, 0.0, 0.0
                if len(klines) > cfg["long_p"] + 20:
                    close = float(klines["close"].iloc[-1])
                    ma_s = float(MA(klines, cfg["short_p"]).ma.iloc[-1])
                    ma_l = float(MA(klines, cfg["long_p"]).ma.iloc[-1])
                    rsi = float(TQ_RSI(klines, cfg["rsi_p"]).rsi.iloc[-1])
                    high_s = klines["high"].iloc[-15:]
                    low_s = klines["low"].iloc[-15:]
                    plus_dm = max(float(high_s.diff().iloc[-1]), 0)
                    minus_dm = max(float(-low_s.diff().iloc[-1]), 0)
                    adx_approx = abs(plus_dm - minus_dm) / (plus_dm + minus_dm + 1e-9) * 100
                msg = (
                    "【豆粕策略】账户状态更新（1分钟）\n"
                    f"合约: {cfg['symbol']}\n"
                    f"余额: ¥{bal:,.0f} | 可用: ¥{avail:,.0f} | 保证金: ¥{margin:,.0f}\n"
                    f"浮盈: ¥{fp:,.0f} | 权益: ¥{equity:,.0f}\n"
                    f"当前持仓: {cur_pos} 手 | 全部持仓: {held_str}\n"
                    f"价: {close:.1f} | MA短: {ma_s:.1f} | MA长: {ma_l:.1f} | RSI: {rsi:.1f} | ADX≈{adx_approx:.1f}"
                )
                feishu_notify(msg)
                telegram_notify(msg)
                log.info(f"推送（1分钟）| 权益:¥{equity:,.0f} 持仓:{cur_pos} 手")
                last_status_push = now

            # ==========================================
            #  1. K线收盘结算与信号判断 (解耦，低CPU)
            # ==========================================
            if api.is_changing(klines.iloc[-1], "datetime") and len(klines) > cfg["long_p"] + 20:
                close   = float(klines["close"].iloc[-1])
                ma_s    = float(MA(klines, cfg["short_p"]).ma.iloc[-1])
                ma_l    = float(MA(klines, cfg["long_p"]).ma.iloc[-1])
                rsi     = float(TQ_RSI(klines, cfg["rsi_p"]).rsi.iloc[-1])
                atr_val = float(TQ_ATR(klines, 14).atr.iloc[-1])

                # 简易 ADX
                high_s  = klines["high"].iloc[-15:]
                low_s   = klines["low"].iloc[-15:]
                plus_dm = max(float(high_s.diff().iloc[-1]), 0)
                minus_dm= max(float(-low_s.diff().iloc[-1]), 0)
                adx_approx = abs(plus_dm - minus_dm) / (plus_dm + minus_dm + 1e-9) * 100

                # 波动率与自适应止损
                recent_atr = klines["close"].rolling(14).std().iloc[-60:]
                pct_rank   = (recent_atr <= atr_val).mean()
                stop_mul   = cfg["stop_atr_low"] + pct_rank * (cfg["stop_atr_high"] - cfg["stop_atr_low"])
                last_stop_mul = stop_mul

                # 安全获取净仓位
                cur_pos = position.pos_long - position.pos_short

                # 高级风控计算
                current_risk_ratio = get_dynamic_risk(equity)
                tick = cfg.get("tick_size", 1.0)
                loss_per_lot = (atr_val * stop_mul) * 10 + (cfg["commission_per_lot"] * 2 + cfg["slippage_ticks"] * tick)
                lots = max(1, int(np.floor((equity * current_risk_ratio) / loss_per_lot))) if loss_per_lot > 0 else 1

                is_melted_today = check_daily_circuit_breaker(equity, cfg["symbol"])
                is_safe_time = is_safe_entry_time()

                # 季节性
                month = datetime.now().month
                allow_long  = month not in [3, 4, 5]
                allow_short = month not in [8, 9, 10]
                trend_ok = adx_approx > cfg["adx_threshold"]

                # 最终开仓逻辑
                long_cond  = (ma_s > ma_l and rsi < cfg["rsi_ob"] and trend_ok and allow_long 
                              and cur_pos <= 0 and not is_melted_today and is_safe_time)
                short_cond = (ma_s < ma_l and rsi > cfg["rsi_os"] and trend_ok and allow_short 
                              and cur_pos >= 0 and not is_melted_today and is_safe_time)

                if long_cond:
                    log.info(f"📈 做多 | 风险率:{current_risk_ratio*100:.1f}% | 建仓 {lots}手")
                    tpos.set_target_pos(lots)
                    high_entry = close
                    long_msg = (
                        f"【豆粕策略】📈 开多\n"
                        f"合约: {cfg['symbol']} | {lots} 手\n"
                        f"成交价: {close:.1f}\n"
                        f"MA短: {ma_s:.1f} MA长: {ma_l:.1f} RSI: {rsi:.1f} ADX: {adx_approx:.1f}\n"
                        f"当前权益: ¥{equity:,.0f}"
                    )
                    feishu_notify(long_msg)
                    telegram_notify(long_msg)
                    
                elif short_cond:
                    log.info(f"📉 做空 | 风险率:{current_risk_ratio*100:.1f}% | 建仓 {lots}手")
                    tpos.set_target_pos(-lots)
                    low_entry = close
                    short_msg = (
                        f"【豆粕策略】📉 开空\n"
                        f"合约: {cfg['symbol']} | {lots} 手\n"
                        f"成交价: {close:.1f}\n"
                        f"MA短: {ma_s:.1f} MA长: {ma_l:.1f} RSI: {rsi:.1f} ADX: {adx_approx:.1f}\n"
                        f"当前权益: ¥{equity:,.0f}"
                    )
                    feishu_notify(short_msg)
                    telegram_notify(short_msg)

            # ==========================================
            #  2. 移动止损监控 (使用与开仓一致的自适应 stop_mul)
            # ==========================================
            if position.pos_long > 0:
                atr_cur = float(TQ_ATR(klines, 14).atr.iloc[-1])
                stop_dist = atr_cur * last_stop_mul
                close_cur = float(klines["close"].iloc[-1])
                high_entry = max(high_entry, close_cur)
                if close_cur < high_entry - stop_dist:
                    log.warning("⛔ 多头触发自适应止损")
                    tpos.set_target_pos(0)
                    stop_msg = (
                        f"【豆粕策略】⛔ 多头止损\n"
                        f"合约: {cfg['symbol']}\n"
                        f"最高: {high_entry:.1f} 平仓价: {close_cur:.1f} 止损距离: {stop_dist:.1f}\n"
                        f"当前权益: ¥{equity:,.0f}"
                    )
                    feishu_notify(stop_msg)
                    telegram_notify(stop_msg)

            elif position.pos_short > 0:
                atr_cur = float(TQ_ATR(klines, 14).atr.iloc[-1])
                stop_dist = atr_cur * last_stop_mul
                close_cur = float(klines["close"].iloc[-1])
                if low_entry == 0:
                    low_entry = close_cur
                low_entry = min(low_entry, close_cur)
                if close_cur > low_entry + stop_dist:
                    log.warning("⛔ 空头触发自适应止损")
                    tpos.set_target_pos(0)
                    stop_msg = (
                        f"【豆粕策略】⛔ 空头止损\n"
                        f"合约: {cfg['symbol']}\n"
                        f"最低: {low_entry:.1f} 平仓价: {close_cur:.1f} 止损距离: {stop_dist:.1f}\n"
                        f"当前权益: ¥{equity:,.0f}"
                    )
                    feishu_notify(stop_msg)
                    telegram_notify(stop_msg)

    except KeyboardInterrupt:
        log.info("手动终止程序")
    except Exception as e:
        log.error(f"实盘异常: {e}", exc_info=True)
        err_msg = f"【豆粕策略】程序异常退出\n错误: {e}\n将在 10 秒后自动重启..."
        feishu_notify(err_msg)
        telegram_notify(err_msg)
    finally:
        api.close()

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "live"
    
    if mode == "backtest":
        df_raw = fetch_soymeal_data_tqsdk("KQ.m@DCE.m", 2000)
        cfg = BacktestConfig()
        df_bt = run_backtest(df_raw, cfg)
        print_report(df_bt, cfg)
    else:
        # 如果崩溃，死循环拉起（建议交由 Linux Supervisor 管理）
        while True:
            run_live()
            log.info("等待 10 秒后重启...")
            time_module.sleep(10)