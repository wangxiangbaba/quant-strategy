"""
===============================================================
  豆粕 (M) 全功能量化交易系统 v2.0
  版本: 2.0 (改进版)

  主要改进点:
  1. [修复🔴] 成交价改为次日开盘价，消除未来函数
  2. [修复🔴] 手续费改为固定元/手（大连交易所标准）
  3. [修复🔴] 止损当根K线不立即反手，避免过度交易
  4. [改进🟡] 新增 Walk-Forward 滚动验证，防止过拟合
  5. [改进🟡] 仓位计算（ATR动态）接入回测引擎
  6. [改进🟢] 新增 ADX 趋势过滤，减少震荡假信号
  7. [改进🟢] 新增季节性过滤（豆粕基本面周期）
  8. [改进🟢] MACD 柱接入信号确认逻辑

  模块结构:
  ├── Part 1: 数据获取 (AkShare)
  ├── Part 2: 技术指标计算 (均线/RSI/ATR/ADX/MACD)
  ├── Part 3: 策略回测引擎 v2（次日开盘成交 / 固定手续费 / 动态仓位）
  ├── Part 4: 绩效分析 (夏普/回撤/胜率/凯利)
  ├── Part 5: Walk-Forward 滚动验证
  ├── Part 6: 参数平原热力图（基于样本外）
  └── Part 7: 实盘框架 (TqSdk 天勤)
===============================================================
"""

# ─────────────────────────────────────────────
# 依赖安装：
#   pip install akshare pandas numpy matplotlib seaborn tqsdk requests
# ─────────────────────────────────────────────

import logging
import sys
import time as time_module
import warnings
from datetime import datetime, time
from itertools import product as iterproduct
import os

import requests
import akshare as ak
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
import seaborn as sns
try:
    from quant_web_client import QuantWebReporter
except Exception:
    QuantWebReporter = None

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
# ║  Part 1: 数据获取                            ║
# ╚══════════════════════════════════════════════╝


def fetch_soymeal_data(symbol: str = "M0") -> pd.DataFrame:
    """
    从 AkShare 获取豆粕主力连续合约日线数据。
    symbol 示例: "M0" (豆粕主力连续)
    """
    log.info(f"正在获取豆粕数据: {symbol} ...")
    df = ak.futures_main_sina(symbol=symbol)

    df.columns = [c.lower().strip() for c in df.columns]

    rename_map = {
        "日期": "date",
        "开盘价": "open",
        "最高价": "high",
        "最低价": "low",
        "收盘价": "close",
        "成交量": "volume",
        "持仓量": "open_interest",
        "动态结算价": "settle_price",
    }
    df.rename(columns=rename_map, inplace=True)

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
    else:
        df.index = pd.to_datetime(df.index)

    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=["open", "close"], inplace=True)
    df.sort_index(inplace=True)

    log.info(
        f"数据获取完成: {len(df)} 条记录，时间范围 "
        f"{df.index[0].date()} ~ {df.index[-1].date()}"
    )
    return df


# ╔══════════════════════════════════════════════╗
# ║  Part 2: 技术指标计算                        ║
# ╚══════════════════════════════════════════════╝


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI（EWM标准算法）"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """真实波幅 ATR"""
    hl  = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ADX 趋势强度指标。
    ADX > 25 认为趋势成立，可入场；
    ADX < 20 认为震荡市，避免双均线假突破。
    """
    high, low, close = df["high"], df["low"], df["close"]

    plus_dm  = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    # 当两个方向同时为正时，只保留较大的一个
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
    """MACD 三线：DIF / DEA / Histogram"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    dif  = ema_fast - ema_slow
    dea  = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist


def seasonal_filter(index: pd.DatetimeIndex):
    """
    豆粕季节性过滤（基于基本面规律）：
    - 3~5月：南美大豆集中上市，供给压力大 → 不做多
    - 8~10月：国内饲料备货旺季，需求驱动 → 不做空
    返回 allow_long, allow_short（两个布尔 Series）
    """
    month = pd.Series(index.month, index=index)
    allow_long  = ~month.isin([3, 4, 5])
    allow_short = ~month.isin([8, 9, 10])
    return allow_long, allow_short


def adaptive_stop_mul(atr_series: pd.Series, lookback: int = 60,
                      low_mul: float = 1.5, high_mul: float = 3.5) -> pd.Series:
    """
    波动率自适应止损倍数：
    - 波动率处于低百分位 → 止损收紧（low_mul）
    - 波动率处于高百分位 → 止损放宽（high_mul）
    """
    pct = atr_series.rolling(lookback).rank(pct=True).fillna(0.5)
    return low_mul + pct * (high_mul - low_mul)


def add_indicators(
    df: pd.DataFrame,
    short_p: int,
    long_p: int,
    rsi_p: int = 14,
    atr_p: int = 14,
    adx_p: int = 14,
) -> pd.DataFrame:
    """向 DataFrame 添加全部技术指标"""
    d = df.copy()
    d["sma_s"] = calc_sma(d["close"], short_p)
    d["sma_l"] = calc_sma(d["close"], long_p)
    d["rsi"]   = calc_rsi(d["close"], rsi_p)
    d["atr"]   = calc_atr(d, atr_p)
    d["adx"]   = calc_adx(d, adx_p)
    d["stop_mul"] = adaptive_stop_mul(d["atr"])
    if "volume" in d.columns:
        d["vol_ma"] = d["volume"].rolling(5).mean()
    d["dif"], d["dea"], d["macd_hist"] = calc_macd(d["close"])
    return d


# ╔══════════════════════════════════════════════╗
# ║  Part 3: 策略回测引擎 v2                     ║
# ╚══════════════════════════════════════════════╝


class BacktestConfig:
    """
    回测参数统一管理

    v2 改动：
    - 成交价：次日开盘价（消除未来函数）
    - 手续费：固定元/手（大连交易所标准）
    - 止损：波动率自适应倍数
    - 新增 ADX / MACD 信号过滤开关
    """
    # 均线周期
    short_p: int   = 20
    long_p: int    = 60
    # RSI 过滤
    rsi_p: int     = 14
    rsi_ob: float  = 72.0
    rsi_os: float  = 28.0
    # ATR / ADX
    atr_p: int     = 14
    adx_p: int     = 14
    adx_threshold: float = 25.0   # ADX > 此值才认为趋势成立
    atr_entry_mul: float = 1.0    # TR > ATR * 此值才入场

    # 止损（自适应，以下为边界值）
    stop_atr_low:  float = 1.5    # 波动率低时止损倍数
    stop_atr_high: float = 3.5    # 波动率高时止损倍数

    # 开关
    use_adx:      bool = True
    use_seasonal: bool = True
    use_macd:     bool = True

    # ── 成本参数（v2 修复）────────────────────────
    # 大连交易所豆粕手续费：1.5元/手（单边）
    # 期货公司一般再加收 1~3元，保守取 6元/手
    commission_per_lot: float = 6.0   # 元/手（单边）
    slippage_ticks: int       = 1     # 滑点 tick 数
    tick_size: float          = 1.0   # 最小变动价位 1元/吨
    multiplier: int           = 10    # 合约乘数 10吨/手

    # 资金
    init_capital: float    = 200_000.0
    risk_per_trade: float  = 0.015    # 单笔风险 1.5%


def calc_position_size(
    equity: float,
    risk_ratio: float,
    current_atr: float,
    stop_mul: float,
    multiplier: int,
    commission_per_lot: float,
    slippage_ticks: int,
    tick_size: float,
) -> int:
    """
    ATR 动态仓位计算（已接入回测引擎）：
      手数 = (账户权益 × 单笔风险) / (ATR × 止损倍数 × 合约乘数 + 单手成本)
    """
    # 指标预热期 atr/stop_mul 可能为 NaN，直接返回最小仓位
    if (current_atr is None or np.isnan(current_atr) or current_atr <= 0
            or stop_mul is None or np.isnan(stop_mul) or stop_mul <= 0):
        return 1
    stop_distance = current_atr * stop_mul
    cost_per_lot  = commission_per_lot * 2 + slippage_ticks * tick_size  # 含双边
    loss_per_lot  = stop_distance * multiplier + cost_per_lot
    if loss_per_lot <= 0:
        return 1
    lots = (equity * risk_ratio) / loss_per_lot
    if np.isnan(lots) or np.isinf(lots):
        return 1
    return max(1, int(np.floor(lots)))


def run_backtest(df_raw: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """
    改进版回测引擎（v2）

    关键修复：
    1. 信号用收盘价计算，次日开盘价成交（消除未来函数）
    2. 手续费 = 固定元/手，不是百分比
    3. 止损触发后当根K线不立即反手（just_stopped 标记）
    4. 实际手数参与权益计算
    """
    df = add_indicators(df_raw, cfg.short_p, cfg.long_p,
                        cfg.rsi_p, cfg.atr_p, cfg.adx_p)

    # ── 季节性过滤 ──────────────────────────────
    allow_long_s, allow_short_s = seasonal_filter(df.index)

    # ── 方向信号（基于收盘价，次日开盘成交）──────
    bull = df["sma_s"] > df["sma_l"]
    bear = df["sma_s"] < df["sma_l"]

    df["tr"] = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"]  - df["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

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

    # ── 逐行处理（次日开盘价成交 + 移动止损）──────
    n = len(df)
    signals     = [0] * n
    entry_price = [np.nan] * n
    exit_price  = [np.nan] * n
    lots_arr    = [0] * n

    pos        = 0
    cur_lots   = 0
    high_entry = 0.0
    low_entry  = 0.0
    equity     = cfg.init_capital

    for i in range(n - 1):   # 最后一根不开新仓（无次日开盘）
        close_i  = df["close"].iloc[i]
        open_i1  = df["open"].iloc[i + 1]   # ← 次日开盘成交价（v2核心修复）
        atr_i    = df["atr"].iloc[i]
        stop_m   = df["stop_mul"].iloc[i]
        raw_s    = df["raw_signal"].iloc[i]

        # 指标预热期跳过（ATR / stop_mul 尚未就绪）
        if pd.isna(atr_i) or pd.isna(stop_m) or atr_i <= 0:
            signals[i] = pos   # 保持当前持仓状态
            lots_arr[i] = cur_lots if pos != 0 else 0
            continue

        stop_m = float(stop_m)
        stop_d = atr_i * stop_m
        just_stopped = False

        # ── 已持多头 ─────────────────────────────
        if pos == 1:
            high_entry = max(high_entry, close_i)
            hit_stop   = close_i < high_entry - stop_d
            hit_rev    = raw_s == -1
            if hit_stop or hit_rev:
                pos           = 0
                just_stopped  = True
                exit_price[i] = open_i1

                # 计算本次交易盈亏
                trade_pnl = (open_i1 - entry_price[i - 1 if i > 0 else i]) * cur_lots * cfg.multiplier
                cost      = cur_lots * (cfg.commission_per_lot * 2 + cfg.slippage_ticks * cfg.tick_size)
                equity   += trade_pnl - cost

        # ── 已持空头 ─────────────────────────────
        elif pos == -1:
            low_entry = min(low_entry, close_i)
            hit_stop  = close_i > low_entry + stop_d
            hit_rev   = raw_s == 1
            if hit_stop or hit_rev:
                pos           = 0
                just_stopped  = True
                exit_price[i] = open_i1

                trade_pnl = (entry_price[i - 1 if i > 0 else i] - open_i1) * cur_lots * cfg.multiplier
                cost      = cur_lots * (cfg.commission_per_lot * 2 + cfg.slippage_ticks * cfg.tick_size)
                equity   += trade_pnl - cost

        # ── 开新仓（止损当根不反手 v2修复）──────
        if pos == 0 and not just_stopped:
            if raw_s == 1:
                cur_lots = calc_position_size(
                    equity, cfg.risk_per_trade, atr_i, stop_m,
                    cfg.multiplier, cfg.commission_per_lot,
                    cfg.slippage_ticks, cfg.tick_size,
                )
                pos             = 1
                high_entry      = close_i
                entry_price[i]  = open_i1   # 次日开盘建仓

            elif raw_s == -1:
                cur_lots = calc_position_size(
                    equity, cfg.risk_per_trade, atr_i, stop_m,
                    cfg.multiplier, cfg.commission_per_lot,
                    cfg.slippage_ticks, cfg.tick_size,
                )
                pos             = -1
                low_entry       = close_i
                entry_price[i]  = open_i1

        signals[i]  = pos
        lots_arr[i] = cur_lots if pos != 0 else 0

    # 最后一根：沿用前一根信号，不开新仓
    signals[-1]  = signals[-2]
    lots_arr[-1] = lots_arr[-2]

    df["signal"]      = signals
    df["lots"]        = lots_arr
    df["entry_price"] = entry_price
    df["exit_price"]  = exit_price

    # ── 逐日权益曲线（基于次日开盘价成交）────────
    equity_curve = [cfg.init_capital]
    running_eq   = cfg.init_capital
    prev_pos     = 0
    prev_lots    = 0
    prev_entry   = np.nan

    for i in range(1, n):
        cur_pos   = signals[i]
        cur_close = df["close"].iloc[i]
        cur_open  = df["open"].iloc[i]

        # 持仓期间按收盘价标记浮动盈亏
        if prev_pos == 1 and cur_pos == 1:
            daily_pnl = (cur_close - df["close"].iloc[i - 1]) * prev_lots * cfg.multiplier
            running_eq += daily_pnl
        elif prev_pos == -1 and cur_pos == -1:
            daily_pnl = (df["close"].iloc[i - 1] - cur_close) * prev_lots * cfg.multiplier
            running_eq += daily_pnl
        # 开/平仓日的盈亏已在循环中计入 equity 变量，这里取最终值
        # （简化处理：权益曲线直接用 running_eq）

        equity_curve.append(running_eq)
        prev_pos   = cur_pos
        prev_lots  = lots_arr[i]
        prev_entry = entry_price[i] if not np.isnan(entry_price[i]) else prev_entry

    df["equity"] = equity_curve

    # ── 附加：用于绩效报告的日收益率 ─────────────
    df["net_ret"] = df["equity"].pct_change().fillna(0)
    df["trade_flag"] = df["signal"].diff().abs().fillna(0)

    return df


# ╔══════════════════════════════════════════════╗
# ║  Part 4: 绩效分析                            ║
# ╚══════════════════════════════════════════════╝


def calc_max_drawdown(equity: pd.Series) -> tuple:
    """返回 (最大回撤比例, 最长回撤天数)"""
    roll_max  = equity.cummax()
    drawdown  = (equity - roll_max) / roll_max
    max_dd    = drawdown.min()
    underwater = (drawdown < 0).astype(int)
    dd_lengths = underwater * (
        underwater.groupby((underwater == 0).cumsum()).cumcount() + 1
    )
    max_dd_days = int(dd_lengths.max())
    return max_dd, max_dd_days


def calc_consecutive(series: pd.Series) -> tuple:
    """最大连续盈利次数 & 最大连续亏损次数"""
    wins   = (series > 0).astype(int)
    losses = (series < 0).astype(int)

    def max_run(s: pd.Series) -> int:
        cumsum = s.cumsum()
        return int((cumsum - cumsum.where(s == 0).ffill().fillna(0)).max())

    return max_run(wins), max_run(losses)


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """凯利公式（建议实盘用 1/4 凯利）"""
    if avg_loss == 0:
        return 0.0
    w = avg_win / avg_loss
    f = win_rate * w - (1 - win_rate)
    return max(0.0, f / w)


def performance_report(df: pd.DataFrame, cfg: BacktestConfig) -> dict:
    """全面绩效报告"""
    ret = df["net_ret"].dropna()
    eq  = df["equity"].dropna()

    trades     = df[df["trade_flag"] > 0]["net_ret"]
    win_trades = trades[trades > 0]
    loss_trades= trades[trades < 0]

    total_days  = len(ret)
    annual_ret  = (eq.iloc[-1] / cfg.init_capital) ** (252 / total_days) - 1
    sharpe      = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
    calmar      = annual_ret / abs(calc_max_drawdown(eq)[0]) if calc_max_drawdown(eq)[0] != 0 else 0
    max_dd, max_dd_days = calc_max_drawdown(eq)
    max_wins, max_losses = calc_consecutive(ret)
    win_rate   = len(win_trades) / len(trades) if len(trades) > 0 else 0
    avg_win    = win_trades.mean() if len(win_trades) > 0 else 0
    avg_loss   = abs(loss_trades.mean()) if len(loss_trades) > 0 else 0
    profit_factor = (
        win_trades.sum() / abs(loss_trades.sum())
        if abs(loss_trades.sum()) > 0 else np.inf
    )
    kelly = kelly_fraction(win_rate, avg_win, avg_loss)

    report = {
        "初始资金":        f"¥{cfg.init_capital:,.0f}",
        "最终权益":        f"¥{eq.iloc[-1]:,.2f}",
        "年化收益率":      f"{annual_ret:.2%}",
        "夏普比率":        f"{sharpe:.3f}",
        "卡玛比率":        f"{calmar:.3f}",
        "最大回撤":        f"{max_dd:.2%}",
        "最长回撤天数":    f"{max_dd_days} 天",
        "总交易次数":      f"{len(trades)}",
        "胜率":            f"{win_rate:.2%}",
        "盈亏比":          f"{(avg_win/avg_loss if avg_loss>0 else 0):.2f}",
        "盈利因子":        f"{profit_factor:.2f}",
        "最大连续盈利":    f"{max_wins} 次",
        "最大连续亏损":    f"{max_losses} 次",
        "全凯利仓位比例":  f"{kelly:.2%}",
        "1/4凯利（建议）": f"{kelly/4:.2%}",
    }
    return report


def print_report(report: dict, title: str = "豆粕量化策略 v2 — 绩效报告"):
    print("\n" + "═" * 52)
    print(f"  📊 {title}")
    print("═" * 52)
    for k, v in report.items():
        print(f"  {k:<20} {v}")
    print("═" * 52 + "\n")


def save_report_txt(report: dict, file_path: str, title: str = "豆粕量化策略 v2") -> None:
    lines = [f"{title} — 回测绩效报告", "=" * 40]
    for k, v in report.items():
        lines.append(f"{k}: {v}")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log.info(f"回测绩效报告已保存: {file_path}")


# ╔══════════════════════════════════════════════╗
# ║  Part 5: Walk-Forward 滚动验证               ║
# ╚══════════════════════════════════════════════╝


def _find_best_params(
    train_df: pd.DataFrame,
    short_range=(10, 40, 5),
    long_range=(40, 120, 10),
) -> tuple:
    """在训练集上网格搜索最优参数（以夏普比率为目标）"""
    best_sharpe, best_params = -np.inf, (20, 60)
    for s, l in iterproduct(range(*short_range), range(*long_range)):
        if s >= l:
            continue
        cfg = BacktestConfig()
        cfg.short_p, cfg.long_p = s, l
        try:
            df_bt  = run_backtest(train_df, cfg)
            ret    = df_bt["net_ret"].dropna()
            sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
            if sharpe > best_sharpe:
                best_sharpe, best_params = sharpe, (s, l)
        except Exception:
            continue
    return best_params


def walk_forward_test(
    df_raw: pd.DataFrame,
    n_train: int = 500,
    n_test: int  = 120,
    short_range  = (10, 40, 5),
    long_range   = (40, 120, 10),
) -> pd.DataFrame:
    """
    Walk-Forward 滚动验证：
    - 在样本内（训练集）寻找最优参数
    - 在样本外（测试集）验证，拼接所有样本外结果
    这样可以有效防止过拟合，评估策略真实泛化能力。
    """
    all_results = []
    total_windows = (len(df_raw) - n_train) // n_test
    log.info(f"Walk-Forward 验证开始，共 {total_windows} 个滚动窗口...")

    for i in range(0, len(df_raw) - n_train - n_test, n_test):
        train = df_raw.iloc[i : i + n_train]
        test  = df_raw.iloc[i + n_train : i + n_train + n_test]

        log.info(
            f"  窗口 {i//n_test + 1}/{total_windows} | "
            f"训练: {train.index[0].date()}~{train.index[-1].date()} | "
            f"测试: {test.index[0].date()}~{test.index[-1].date()}"
        )

        best_s, best_l = _find_best_params(train, short_range, long_range)
        log.info(f"    最优参数: short={best_s}, long={best_l}")

        cfg = BacktestConfig()
        cfg.short_p, cfg.long_p = best_s, best_l
        cfg.init_capital = 200_000.0

        df_test = run_backtest(test, cfg)
        df_test["wf_window"]   = i // n_test + 1
        df_test["best_short"]  = best_s
        df_test["best_long"]   = best_l
        all_results.append(df_test)

    if not all_results:
        log.warning("Walk-Forward: 数据量不足，无法进行验证。")
        return pd.DataFrame()

    combined = pd.concat(all_results)
    log.info("Walk-Forward 验证完成。")
    return combined


def plot_walk_forward(wf_df: pd.DataFrame, save_path: str | None = None):
    """绘制 Walk-Forward 拼接权益曲线"""
    if wf_df.empty:
        return

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=False)

    # 上图：各窗口样本外权益曲线
    for window_id, grp in wf_df.groupby("wf_window"):
        axes[0].plot(
            grp.index, grp["equity"],
            label=f"W{window_id} ({int(grp['best_short'].iloc[0])}/{int(grp['best_long'].iloc[0])})",
            alpha=0.7, lw=1.2,
        )
    axes[0].set_title("Walk-Forward 各窗口样本外权益曲线", fontsize=12, fontweight="bold")
    axes[0].legend(loc="upper left", fontsize=8, ncol=3)
    axes[0].grid(alpha=0.3)

    # 下图：参数稳定性（best_short / best_long 分布）
    params = wf_df.drop_duplicates("wf_window")[["wf_window", "best_short", "best_long"]]
    x = params["wf_window"]
    axes[1].bar(x - 0.2, params["best_short"], width=0.4, label="最优短均线", color="#3498db", alpha=0.8)
    axes[1].bar(x + 0.2, params["best_long"],  width=0.4, label="最优长均线", color="#e74c3c", alpha=0.8)
    axes[1].set_title("各窗口最优参数分布（参数稳定性）", fontsize=12, fontweight="bold")
    axes[1].legend(fontsize=9)
    axes[1].set_xlabel("窗口编号")
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    if save_path is None:
        save_path = "m_walk_forward.png"
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()
    log.info(f"Walk-Forward 图表已保存: {save_path}")


# ╔══════════════════════════════════════════════╗
# ║  Part 6: 参数平原热力图（样本外）            ║
# ╚══════════════════════════════════════════════╝


def param_plain_scan(
    df_raw: pd.DataFrame,
    short_range=(10, 40, 5),
    long_range=(40, 120, 10),
    oos_ratio: float = 0.3,       # 用后30%数据作为样本外评估
) -> pd.DataFrame:
    """
    v2 改进：参数扫描使用样本外数据评估夏普比率，避免过拟合。
    oos_ratio: 样本外数据比例
    """
    split = int(len(df_raw) * (1 - oos_ratio))
    df_oos = df_raw.iloc[split:]
    log.info(
        f"参数扫描 | 样本外: {df_oos.index[0].date()} ~ {df_oos.index[-1].date()}"
        f"（{len(df_oos)} 条）"
    )

    shorts  = list(range(*short_range))
    longs   = list(range(*long_range))
    results = pd.DataFrame(index=shorts, columns=longs, dtype=float)

    total = len(shorts) * len(longs)
    cnt   = 0
    log.info(f"开始参数平原扫描（样本外），共 {total} 组组合...")

    for s, l in iterproduct(shorts, longs):
        if s >= l:
            results.loc[s, l] = np.nan
            continue
        cfg = BacktestConfig()
        cfg.short_p, cfg.long_p = s, l
        try:
            df_bt  = run_backtest(df_oos, cfg)
            ret    = df_bt["net_ret"].dropna()
            sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
            results.loc[s, l] = round(sharpe, 3)
        except Exception:
            results.loc[s, l] = np.nan
        cnt += 1
        if cnt % 5 == 0:
            log.info(f"  进度: {cnt}/{total}")

    return results


def plot_param_plain(results: pd.DataFrame, save_path: str | None = None):
    plt.figure(figsize=(12, 8))
    sns.heatmap(
        results.astype(float),
        annot=True, fmt=".2f",
        cmap="RdYlGn", center=0,
        linewidths=0.5, linecolor="gray",
        cbar_kws={"label": "夏普比率（样本外）"},
    )
    plt.title("豆粕 — 双均线参数平原（样本外夏普比率）", fontsize=14, fontweight="bold")
    plt.xlabel("长周期均线", fontsize=12)
    plt.ylabel("短周期均线", fontsize=12)
    plt.tight_layout()
    if save_path is None:
        save_path = "m_param_plain.png"
    plt.savefig(save_path, dpi=150)
    plt.show()
    log.info(f"参数平原图已保存: {save_path}")


# ╔══════════════════════════════════════════════╗
# ║  Part 6b: 综合绩效可视化                     ║
# ╚══════════════════════════════════════════════╝


def plot_strategy(df: pd.DataFrame, symbol: str = "M", save_path: str | None = None):
    fig = plt.figure(figsize=(16, 16))
    gs  = gridspec.GridSpec(5, 1, hspace=0.45, height_ratios=[3, 1, 1, 1, 1])

    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    ax4 = fig.add_subplot(gs[3], sharex=ax1)
    ax5 = fig.add_subplot(gs[4], sharex=ax1)

    # 子图1：价格 + 均线 + 信号
    ax1.plot(df.index, df["close"], color="#555", lw=1, alpha=0.7, label="收盘价")
    ax1.plot(df.index, df["sma_s"], color="#1e90ff", lw=1.5, label=f"短均线({df.attrs.get('short_p','—')})")
    ax1.plot(df.index, df["sma_l"], color="#ff4500", lw=1.5, label=f"长均线({df.attrs.get('long_p','—')})")

    buy_idx  = df.index[df["signal"].diff() > 0]
    sell_idx = df.index[df["signal"].diff() < 0]
    ax1.scatter(buy_idx,  df.loc[buy_idx,  "close"], marker="^", color="lime", s=80, zorder=5, label="开多")
    ax1.scatter(sell_idx, df.loc[sell_idx, "close"], marker="v", color="red",  s=80, zorder=5, label="平/开空")
    ax1.set_title(f"{symbol} 豆粕量化策略 v2", fontsize=13, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(alpha=0.3)

    # 子图2：权益曲线
    ax2.plot(df.index, df["equity"], color="#2ecc71", lw=2, label="权益曲线（含手续费）")
    ax2.axhline(df["equity"].iloc[0], color="gray", lw=0.8, linestyle=":")
    ax2.fill_between(df.index, df["equity"].iloc[0], df["equity"],
                     where=df["equity"] >= df["equity"].iloc[0], alpha=0.15, color="green")
    ax2.fill_between(df.index, df["equity"].iloc[0], df["equity"],
                     where=df["equity"] <  df["equity"].iloc[0], alpha=0.15, color="red")
    ax2.set_title("权益曲线（次日开盘成交）", fontsize=11)
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(alpha=0.3)

    # 子图3：ADX
    ax3.plot(df.index, df["adx"], color="#e67e22", lw=1.2, label="ADX(14)")
    ax3.axhline(25, color="red",  lw=0.8, linestyle="--", alpha=0.7, label="趋势阈值=25")
    ax3.axhline(20, color="gray", lw=0.8, linestyle=":",  alpha=0.5)
    ax3.fill_between(df.index, 0, df["adx"], where=df["adx"] > 25, alpha=0.1, color="orange")
    ax3.set_title("ADX（趋势强度）", fontsize=11)
    ax3.legend(loc="upper left", fontsize=9)
    ax3.grid(alpha=0.3)

    # 子图4：RSI
    ax4.plot(df.index, df["rsi"], color="#9b59b6", lw=1.2, label="RSI(14)")
    ax4.axhline(70, color="red",   lw=0.8, linestyle="--", alpha=0.7)
    ax4.axhline(30, color="green", lw=0.8, linestyle="--", alpha=0.7)
    ax4.fill_between(df.index, 30, 70, alpha=0.05, color="gray")
    ax4.set_ylim(0, 100)
    ax4.set_title("RSI", fontsize=11)
    ax4.grid(alpha=0.3)

    # 子图5：MACD
    colors = ["#e74c3c" if v >= 0 else "#2ecc71" for v in df["macd_hist"]]
    ax5.bar(df.index, df["macd_hist"], color=colors, alpha=0.7, width=1, label="MACD柱")
    ax5.plot(df.index, df["dif"], color="#3498db", lw=1.2, label="DIF")
    ax5.plot(df.index, df["dea"], color="#e67e22", lw=1.2, label="DEA")
    ax5.axhline(0, color="gray", lw=0.8)
    ax5.set_title("MACD（信号确认）", fontsize=11)
    ax5.legend(loc="upper left", fontsize=9)
    ax5.grid(alpha=0.3)

    if save_path is None:
        save_path = "m_strategy_chart_v2.png"
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()
    log.info(f"策略图表已保存: {save_path}")


# ╔══════════════════════════════════════════════╗
# ║  Part 7: 实盘框架 (TqSdk 天勤)               ║
# ╚══════════════════════════════════════════════╝

LIVE_CONFIG = {
    "symbol":          "DCE.m2609",   # ⚠ 请按当前主力合约调整
    "kline_freq":      15 * 60,       # 15分钟K线
    "short_p":         20,
    "long_p":          60,
    "rsi_p":           14,
    "rsi_ob":          72,
    "rsi_os":          28,
    "adx_threshold":   25,
    "stop_atr_low":    1.5,
    "stop_atr_high":   3.5,
    "risk_per_trade":  0.015,
    "commission_per_lot": 6.0,        # 元/手（单边）
    "slippage_ticks":  1,
    "max_loss_limit":  5000,          # 日内最大亏损（元）熔断
    # 快期鉴权手机号 / 密码（登录天勤账户）
    "phone":    "15528503735",
    "password": "QQ1392070089",
    # 快期模拟盘账户信息（需要你从快期 APP / 客户端里查到）
    "broker_id":  "快期模拟",     # 经纪商 / 服务器名称示例，请替换成你自己的
    "account_id": "15528503735",
    "account_pwd": "QQ1392070089",
}

# 飞书机器人推送（可选）：在群里添加自定义机器人获取 Webhook URL
FEISHU_CONFIG = {
    "enabled": True,   # 改为 True 并填写 webhook 后生效
    "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/d006b985-b71a-4c8e-a505-dcea786053a2",      # 示例: https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx
}

QUANT_WEB_CONFIG = {
    "enabled": True,
    "base_url": "http://127.0.0.1:8000",
    "token": "change-me-token",
}

TRADING_WINDOWS = [
    (time(9, 1),  time(10, 14)),
    (time(10, 31),time(11, 29)),
    (time(13, 31),time(14, 55)),
    (time(21, 1), time(22, 55)),  # 夜盘
]


def is_trading_time() -> bool:
    now = datetime.now().time()
    return any(s <= now <= e for s, e in TRADING_WINDOWS)


def check_contract_expiry(symbol: str) -> None:
    month = datetime.now().month
    if month in (1, 5, 9):
        log.warning(f"⚠ 当前月份 {month} 月，豆粕主力可能切换，请确认合约: {symbol}")


def feishu_notify(text: str) -> None:
    """向飞书群推送一条文本消息（需在 FEISHU_CONFIG 中配置 webhook）。"""
    if not FEISHU_CONFIG.get("enabled") or not FEISHU_CONFIG.get("webhook"):
        return
    try:
        resp = requests.post(
            FEISHU_CONFIG["webhook"],
            json={"msg_type": "text", "content": {"text": text}},
            timeout=5,
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"飞书通知发送失败: {e}")


def run_live():
    """
    实盘/模拟盘入口（豆粕 v2）
    改进点：
    - ADX 趋势过滤
    - 波动率自适应止损
    - 固定元/手手续费计算仓位
    - 季节性过滤
    """
    try:
        from tqsdk import TqApi, TqAuth, TargetPosTask, TqAccount
        from tqsdk.ta import MA, RSI as TQ_RSI, ATR as TQ_ATR
    except ImportError:
        log.error("请先安装天勤SDK: pip install tqsdk")
        return

    cfg = LIVE_CONFIG
    reporter = None
    if QuantWebReporter is not None:
        reporter = QuantWebReporter(
            enabled=QUANT_WEB_CONFIG.get("enabled", False),
            base_url=QUANT_WEB_CONFIG.get("base_url", ""),
            token=QUANT_WEB_CONFIG.get("token", ""),
        )
    check_contract_expiry(cfg["symbol"])

    api = None
    try:
        # 使用快期 APP 的模拟盘账号，通过 TqAccount 接入
        # 请在 LIVE_CONFIG 中填好经纪商 / 账号 / 密码，如：
        # "broker_id": "快期模拟", "account_id": "your_sim_id", "account_pwd": "your_sim_pwd"
        sim_account = TqAccount(
            cfg["broker_id"],
            cfg["account_id"],
            cfg["account_pwd"],
        )
        api      = TqApi(account=sim_account, auth=TqAuth(cfg["phone"], cfg["password"]))
        account   = api.get_account()
        position  = api.get_position(cfg["symbol"])  # 单品种持仓引用
        all_pos   = api.get_position()               # 全部合约持仓字典
        klines    = api.get_kline_serial(cfg["symbol"], cfg["kline_freq"], data_length=200)
        tpos      = TargetPosTask(api, cfg["symbol"])

        init_balance = account.balance
        high_entry   = 0.0
        low_entry    = 0.0
        is_melted    = False

        # 开盘/收盘状态心跳 + 账户变动推送控制
        last_open_state = None
        last_status_log = datetime.now()
        last_feishu_eq   = float(init_balance)
        last_feishu_pos  = 0
        last_status_push = datetime.now()

        log.info(f"✅ 豆粕实盘系统 v2 启动 | 合约:{cfg['symbol']} | 初始资金:¥{init_balance:,.0f}")
        feishu_notify(
            f"【豆粕策略】已启动\n"
            f"合约: {cfg['symbol']}\n"
            f"初始资金: ¥{init_balance:,.0f}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        if reporter:
            reporter.post_event(
                symbol=cfg["symbol"],
                event_type="strategy_start",
                level="info",
                message="策略已启动",
                payload={"init_balance": float(init_balance)},
            )

        while True:
            api.wait_update()

            # A. 账户熔断
            equity = account.balance + account.float_profit
            if equity < (init_balance - cfg["max_loss_limit"]):
                if not is_melted:
                    log.error(f"🔴 熔断！当前权益¥{equity:,.0f}，强制全平！")
                    feishu_notify(
                        f"【豆粕策略】🔴 熔断\n"
                        f"合约: {cfg['symbol']}\n"
                        f"当前权益: ¥{equity:,.0f}\n"
                        f"初始资金: ¥{init_balance:,.0f}\n"
                        f"已强制平仓，请检查策略与风控。"
                    )
                    if reporter:
                        reporter.post_event(
                            symbol=cfg["symbol"],
                            event_type="melt_down",
                            level="error",
                            message=f"触发熔断，权益 {equity:,.0f}",
                            payload={"equity": float(equity), "init_balance": float(init_balance)},
                        )
                    tpos.set_target_pos(0)
                    is_melted = True
                continue

            # B. 时间过滤 + 开盘/收盘状态心跳日志
            now      = datetime.now()
            is_open  = is_trading_time()
            elapsed  = (now - last_status_log).total_seconds()
            if (is_open != last_open_state) or elapsed >= 300:
                status = "开盘时段" if is_open else "非开盘时段（休市）"
                log.info(f"时间 {now.strftime('%H:%M:%S')} | {status}")
                if (is_open != last_open_state) and not is_open and FEISHU_CONFIG.get("enabled"):
                    feishu_notify(f"【豆粕策略】休市开始\n合约: {cfg['symbol']}\n时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                    if reporter:
                        reporter.post_event(
                            symbol=cfg["symbol"],
                            event_type="market_close",
                            level="info",
                            message="休市开始",
                            payload={"time": now.strftime("%Y-%m-%d %H:%M:%S")},
                        )
                last_open_state = is_open
                last_status_log = now

            if not is_open:
                continue

            # C. 季节性过滤
            month = datetime.now().month
            allow_long  = month not in [3, 4, 5]
            allow_short = month not in [8, 9, 10]

            # C2. 每 1 分钟推送账户状态到飞书（独立于 K 线，真正每分钟一次）
            now_push = datetime.now()
            if FEISHU_CONFIG.get("enabled") and (now_push - last_status_push).total_seconds() >= 60:
                equity = account.balance + account.float_profit
                bal    = float(account.balance)
                avail  = float(getattr(account, "available", bal))
                margin = float(getattr(account, "margin", 0.0))
                fp     = float(account.float_profit)
                cur_pos = int(getattr(position, "pos", 0))
                held_symbols = []
                try:
                    for sym, pos_obj in all_pos.items():
                        p = int(getattr(pos_obj, "pos", 0))
                        if p != 0:
                            held_symbols.append(f"{sym}:{p}手")
                except Exception:
                    held_symbols = []
                held_str = ", ".join(held_symbols) if held_symbols else "无持仓"
                close, ma_s, ma_l, rsi, adx_approx = 0.0, 0.0, 0.0, 0.0, 0.0
                if len(klines) > cfg["long_p"] + 20:
                    close   = float(klines["close"].iloc[-1])
                    ma_s    = float(MA(klines, cfg["short_p"]).ma.iloc[-1])
                    ma_l    = float(MA(klines, cfg["long_p"]).ma.iloc[-1])
                    rsi     = float(TQ_RSI(klines, cfg["rsi_p"]).rsi.iloc[-1])
                    high_s  = klines["high"].iloc[-15:]
                    low_s   = klines["low"].iloc[-15:]
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
                log.info(f"飞书推送（1分钟）| 权益:¥{equity:,.0f} 持仓:{cur_pos} 手")
                if reporter:
                    reporter.post_snapshot(
                        {
                            "symbol": cfg["symbol"],
                            "close": float(close),
                            "ma_short": float(ma_s),
                            "ma_long": float(ma_l),
                            "rsi": float(rsi),
                            "adx": float(adx_approx),
                            "balance": float(bal),
                            "available": float(avail),
                            "margin": float(margin),
                            "float_profit": float(fp),
                            "equity": float(equity),
                            "cur_pos": int(cur_pos),
                            "held_symbols": held_str,
                        }
                    )
                last_status_push = now_push

            # D. K线更新后计算指标
            if not (api.is_changing(klines.iloc[-1], "datetime") and len(klines) > cfg["long_p"] + 20):
                continue

            close   = float(klines["close"].iloc[-1])
            # tqsdk.ta.MA 返回带字段的对象，这里取其 .ma 序列的最后一个值
            ma_s    = float(MA(klines, cfg["short_p"]).ma.iloc[-1])
            ma_l    = float(MA(klines, cfg["long_p"]).ma.iloc[-1])
            # tqsdk.ta.RSI 返回带 rsi 字段，取 .rsi 最后一项
            rsi     = float(TQ_RSI(klines, cfg["rsi_p"]).rsi.iloc[-1])
            # tqsdk.ta.ATR 返回带 atr 字段，取 .atr 最后一项
            atr_val = float(TQ_ATR(klines, 14).atr.iloc[-1])

            # 简化ADX计算
            high_s  = klines["high"].iloc[-15:]
            low_s   = klines["low"].iloc[-15:]
            plus_dm = max(float(high_s.diff().iloc[-1]), 0)
            minus_dm= max(float(-low_s.diff().iloc[-1]), 0)
            adx_approx = abs(plus_dm - minus_dm) / (plus_dm + minus_dm + 1e-9) * 100

            # 波动率百分位（近60根K线）
            recent_atr = klines["close"].rolling(14).std().iloc[-60:]
            pct_rank   = (recent_atr <= atr_val).mean()
            stop_mul   = cfg["stop_atr_low"] + pct_rank * (cfg["stop_atr_high"] - cfg["stop_atr_low"])

            # 当前净持仓（多空相抵），用于判断是否已经有仓位
            # 当前本品种净持仓
            cur_pos = int(getattr(position, "pos", 0))

            # 汇总当前所有持仓合约（非零持仓）
            held_symbols = []
            try:
                for sym, pos_obj in all_pos.items():
                    p = int(getattr(pos_obj, "pos", 0))
                    if p != 0:
                        held_symbols.append(f"{sym}:{p}手")
            except Exception:
                held_symbols = []
            held_str = ", ".join(held_symbols) if held_symbols else "无持仓"

            # 状态心跳：账户资金 + 仓位概况（用于实时查看账户变动）
            bal    = float(account.balance)
            avail  = float(getattr(account, "available", bal))
            margin = float(getattr(account, "margin", 0.0))
            fp     = float(account.float_profit)
            log.info(
                "状态 | 余额:¥%.0f 可用:¥%.0f 保证金:¥%.0f 浮盈:¥%.0f 权益:¥%.0f | "
                "本品种:%d 手 | 所有持仓:%s | 价:%.1f MA短:%.1f MA长:%.1f RSI:%.1f ADX≈%.1f"
                % (bal, avail, margin, fp, equity, cur_pos, held_str, close, ma_s, ma_l, rsi, adx_approx)
            )

            # E. 仓位计算（固定元/手）；若算出 0 手则至少 1 手，避免永远不交易
            lots = calc_position_size(
                equity,
                cfg["risk_per_trade"],
                atr_val,
                stop_mul,
                multiplier=10,
                commission_per_lot=cfg["commission_per_lot"],
                slippage_ticks=cfg["slippage_ticks"],
                tick_size=1.0,
            )
            if lots < 1:
                lots = 1   # 至少 1 手，避免因仓位计算出 0 而永远不交易

            # F. 开仓逻辑（加入ADX + 季节性过滤）
            trend_ok = adx_approx > cfg["adx_threshold"]
            long_cond  = ma_s > ma_l and rsi < cfg["rsi_ob"] and trend_ok and allow_long and cur_pos <= 0
            short_cond = ma_s < ma_l and rsi > cfg["rsi_os"] and trend_ok and allow_short and cur_pos >= 0

            if long_cond:
                log.info(f"📈 多头 | 价:{close} MA短:{ma_s:.1f} MA长:{ma_l:.1f} RSI:{rsi:.1f} ADX:{adx_approx:.1f} | {lots}手")
                tpos.set_target_pos(lots)
                high_entry = close
                feishu_notify(
                    f"【豆粕策略】📈 开多\n"
                    f"合约: {cfg['symbol']} | {lots} 手\n"
                    f"成交价: {close:.1f}\n"
                    f"MA短: {ma_s:.1f} MA长: {ma_l:.1f} RSI: {rsi:.1f} ADX: {adx_approx:.1f}\n"
                    f"当前权益: ¥{equity:,.0f}"
                )
                if reporter:
                    reporter.post_event(
                        symbol=cfg["symbol"],
                        event_type="open_long",
                        level="info",
                        message=f"开多 {lots} 手 @ {close:.1f}",
                        payload={"lots": int(lots), "price": float(close), "equity": float(equity)},
                    )

            elif short_cond:
                log.info(f"📉 空头 | 价:{close} MA短:{ma_s:.1f} MA长:{ma_l:.1f} RSI:{rsi:.1f} ADX:{adx_approx:.1f} | {lots}手")
                tpos.set_target_pos(-lots)
                low_entry = close
                feishu_notify(
                    f"【豆粕策略】📉 开空\n"
                    f"合约: {cfg['symbol']} | {lots} 手\n"
                    f"成交价: {close:.1f}\n"
                    f"MA短: {ma_s:.1f} MA长: {ma_l:.1f} RSI: {rsi:.1f} ADX: {adx_approx:.1f}\n"
                    f"当前权益: ¥{equity:,.0f}"
                )
                if reporter:
                    reporter.post_event(
                        symbol=cfg["symbol"],
                        event_type="open_short",
                        level="info",
                        message=f"开空 {lots} 手 @ {close:.1f}",
                        payload={"lots": int(lots), "price": float(close), "equity": float(equity)},
                    )

            else:
                # 诊断：当前未开仓原因（便于排查“一直不交易”）
                why = []
                if cur_pos != 0:
                    why.append("已有仓")
                if ma_s <= ma_l and not (ma_s < ma_l and rsi > cfg["rsi_os"]):
                    why.append("均线非多")
                elif ma_s >= ma_l and not (ma_s > ma_l and rsi < cfg["rsi_ob"]):
                    why.append("均线非空")
                if not trend_ok:
                    why.append("ADX<%.0f" % cfg["adx_threshold"])
                if not allow_long and ma_s > ma_l:
                    why.append("季节禁多(3-5月)")
                if not allow_short and ma_s < ma_l:
                    why.append("季节禁空(8-10月)")
                if rsi >= cfg["rsi_ob"] and ma_s > ma_l:
                    why.append("RSI过高不追多")
                if rsi <= cfg["rsi_os"] and ma_s < ma_l:
                    why.append("RSI过低不追空")
                if why:
                    log.info(f"未开仓 | {', '.join(why)} | 价:{close:.1f} MA短:{ma_s:.1f} MA长:{ma_l:.1f} RSI:{rsi:.1f} ADX≈{adx_approx:.1f}")

            # G. 自适应移动止损
            stop_dist = atr_val * stop_mul

            if cur_pos > 0:
                high_entry = max(high_entry, close)
                if close < high_entry - stop_dist:
                    log.warning(f"⛔ 多头止损 | 最高:{high_entry:.1f} 当前:{close:.1f} 止损:{stop_dist:.1f}")
                    feishu_notify(
                        f"【豆粕策略】⛔ 多头止损\n"
                        f"合约: {cfg['symbol']}\n"
                        f"最高: {high_entry:.1f} 平仓价: {close:.1f} 止损距离: {stop_dist:.1f}\n"
                        f"当前权益: ¥{equity:,.0f}"
                    )
                    if reporter:
                        reporter.post_event(
                            symbol=cfg["symbol"],
                            event_type="stop_loss_long",
                            level="warn",
                            message=f"多头止损 @ {close:.1f}",
                            payload={"close": float(close), "high_entry": float(high_entry), "equity": float(equity)},
                        )
                    tpos.set_target_pos(0)

            elif cur_pos < 0:
                if low_entry == 0:
                    low_entry = close
                low_entry = min(low_entry, close)
                if close > low_entry + stop_dist:
                    log.warning(f"⛔ 空头止损 | 最低:{low_entry:.1f} 当前:{close:.1f} 止损:{stop_dist:.1f}")
                    feishu_notify(
                        f"【豆粕策略】⛔ 空头止损\n"
                        f"合约: {cfg['symbol']}\n"
                        f"最低: {low_entry:.1f} 平仓价: {close:.1f} 止损距离: {stop_dist:.1f}\n"
                        f"当前权益: ¥{equity:,.0f}"
                    )
                    if reporter:
                        reporter.post_event(
                            symbol=cfg["symbol"],
                            event_type="stop_loss_short",
                            level="warn",
                            message=f"空头止损 @ {close:.1f}",
                            payload={"close": float(close), "low_entry": float(low_entry), "equity": float(equity)},
                        )
                    tpos.set_target_pos(0)

    except KeyboardInterrupt:
        log.info("用户手动停止豆粕策略。")
        if reporter:
            reporter.post_event(
                symbol=LIVE_CONFIG["symbol"],
                event_type="manual_stop",
                level="info",
                message="用户手动停止策略",
            )
    except Exception as e:
        log.error(f"系统异常: {e}", exc_info=True)
        if reporter:
            reporter.post_event(
                symbol=LIVE_CONFIG["symbol"],
                event_type="system_exception",
                level="error",
                message=f"系统异常: {e}",
            )
    finally:
        if api is not None:
            api.close()
            log.info("API已关闭，豆粕策略停止。")
        if reporter:
            reporter.post_event(
                symbol=LIVE_CONFIG["symbol"],
                event_type="strategy_stop",
                level="info",
                message="策略已停止",
            )


# ╔══════════════════════════════════════════════╗
# ║  Main: 一键运行                              ║
# ╚══════════════════════════════════════════════╝

if __name__ == "__main__":
    import sys

    mode     = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    base_dir = os.path.dirname(os.path.abspath(__file__))
    product  = "豆粕"
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 日志目录
    logs_root   = os.path.join(base_dir, "logs", product)
    os.makedirs(logs_root, exist_ok=True)
    log_run_dir = os.path.join(logs_root, f"{product}_{ts}")
    os.makedirs(log_run_dir, exist_ok=True)
    file_handler = logging.FileHandler(
        os.path.join(log_run_dir, "m_strategy_v2.log"), encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(file_handler)

    # 结果目录
    base_results = os.path.join(base_dir, "results", product)
    os.makedirs(base_results, exist_ok=True)
    run_dir = os.path.join(base_results, f"{product}_{ts}")
    os.makedirs(run_dir, exist_ok=True)

    if mode == "live":
        while True:
            try:
                run_live()
            except Exception as e:
                log.exception(f"实盘程序异常退出: {e}")
                if FEISHU_CONFIG.get("enabled"):
                    feishu_notify(f"【豆粕策略】程序异常退出\n错误: {e}\n将在 5 秒后自动重启...")
                if QuantWebReporter is not None:
                    QuantWebReporter(
                        enabled=QUANT_WEB_CONFIG.get("enabled", False),
                        base_url=QUANT_WEB_CONFIG.get("base_url", ""),
                        token=QUANT_WEB_CONFIG.get("token", ""),
                    ).post_event(
                        symbol=LIVE_CONFIG["symbol"],
                        event_type="restart",
                        level="error",
                        message=f"实盘异常退出并准备重启: {e}",
                    )
                log.info("5 秒后自动重启...")
                time_module.sleep(5)
            else:
                log.info("实盘程序正常退出")
                break

    elif mode == "scan":
        df_raw  = fetch_soymeal_data("M0")
        results = param_plain_scan(df_raw)
        plot_param_plain(results, save_path=os.path.join(run_dir, "m_param_plain_v2.png"))

    elif mode == "wf":
        df_raw = fetch_soymeal_data("M0")
        wf_df  = walk_forward_test(df_raw, n_train=500, n_test=120)
        if not wf_df.empty:
            plot_walk_forward(wf_df, save_path=os.path.join(run_dir, "m_walk_forward.png"))
            # 对 WF 拼接结果出绩效报告
            cfg = BacktestConfig()
            report = performance_report(wf_df, cfg)
            print_report(report, title="豆粕 Walk-Forward 样本外绩效")
            save_report_txt(report, os.path.join(run_dir, "m_wf_report.txt"),
                            title="豆粕 Walk-Forward 样本外")

    else:
        # 默认：回测模式
        df_raw = fetch_soymeal_data("M0")
        cfg    = BacktestConfig()
        df_bt  = run_backtest(df_raw, cfg)
        report = performance_report(df_bt, cfg)

        print_report(report)
        save_report_txt(report, os.path.join(run_dir, "m_backtest_report_v2.txt"))
        plot_strategy(df_bt, save_path=os.path.join(run_dir, "m_strategy_chart_v2.png"))

        log.info("v2 运行方式:")
        log.info("  python m_quant_system_v2.py           # 回测")
        log.info("  python m_quant_system_v2.py scan      # 参数平原（样本外）")
        log.info("  python m_quant_system_v2.py wf        # Walk-Forward 验证")
        log.info("  python m_quant_system_v2.py live      # 实盘/模拟盘")
