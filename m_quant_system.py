"""
===============================================================
  豆粕 (M) 全功能量化交易系统
  作者: GPT-5.1 (Cursor)
  版本: 1.0

  模块结构:
  ├── Part 1: 数据获取 (AkShare)
  ├── Part 2: 技术指标计算 (均线/RSI/ATR/MACD)
  ├── Part 3: 策略回测引擎 (含手续费/滑点)
  ├── Part 4: 绩效分析 (夏普/回撤/胜率/凯利)
  ├── Part 5: 参数平原热力图
  └── Part 6: 实盘框架 (TqSdk 天勤)
===============================================================
"""

# ─────────────────────────────────────────────
# 依赖安装：
#   pip install akshare pandas numpy matplotlib seaborn tqsdk
# ─────────────────────────────────────────────

import logging
import warnings
from datetime import datetime, time
from itertools import product
import os

import akshare as ak
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
import seaborn as sns

warnings.filterwarnings("ignore")
plt.rcParams["font.sans-serif"] = ["SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ╔══════════════════════════════════════════════╗
# ║  Part 1: 数据获取                            ║
# ╚══════════════════════════════════════════════╝


def fetch_soymeal_data(symbol: str = "M0") -> pd.DataFrame:
    """
    从 AkShare 获取豆粕主力连续合约日线数据。

    说明：
    - 新版本 AkShare 已不再提供 futures_main_ctp，
      这里改为使用 `futures_main_sina` 获取主力连续数据。
    - symbol 示例：
      * 豆粕主力: "M0"
    """
    log.info(f"正在获取豆粕数据: {symbol} ...")
    # 使用 AkShare 当前推荐的主力连续接口
    df = ak.futures_main_sina(symbol=symbol)

    # 列名统一化
    df.columns = [c.lower().strip() for c in df.columns]

    # 若为中文列名（futures_main_sina 的典型返回），做一次映射
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

    # 兼容不同版本 AkShare：可能是列里有 date，也可能直接用索引表示日期
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
    else:
        df.index = pd.to_datetime(df.index)

    for col in ["open", "high", "low", "close", "volume", "hold"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=["close"], inplace=True)
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
    """Wilder's RSI（更标准的算法，用EWM而非简单滚动均值）"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """真实波幅 ATR，用于动态止损和仓位计算"""
    hl = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def calc_macd(series: pd.Series, fast=12, slow=26, signal=9):
    """MACD 三线：DIF / DEA / Histogram"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist


def add_indicators(
    df: pd.DataFrame,
    short_p: int,
    long_p: int,
    rsi_p: int = 14,
    atr_p: int = 14,
) -> pd.DataFrame:
    """向 DataFrame 添加全部技术指标"""
    d = df.copy()
    d["sma_s"] = calc_sma(d["close"], short_p)
    d["sma_l"] = calc_sma(d["close"], long_p)
    d["rsi"] = calc_rsi(d["close"], rsi_p)
    d["atr"] = calc_atr(d, atr_p)
    d["vol_ma"] = d["volume"].rolling(5).mean() if "volume" in d.columns else 1
    d["dif"], d["dea"], d["macd_hist"] = calc_macd(d["close"])
    return d


# ╔══════════════════════════════════════════════╗
# ║  Part 3: 策略回测引擎                        ║
# ╚══════════════════════════════════════════════╝


class BacktestConfig:
    """
    回测参数统一管理（针对豆粕稍微偏慢一点的趋势跟随）

    设计思路（稳健为主）：
      - 较慢双均线过滤掉短期噪声：20 / 60
      - ATR 波动率过滤：只有波动放大时才进场，避免极度震荡环境
      - RSI 作为情绪过滤：不过度追高杀跌
      - ATR 追踪止损 + 固定资金风险比例控制仓位
    """

    # 均线周期（略慢于螺纹钢）
    short_p: int = 20
    long_p: int = 60
    # RSI 过滤
    rsi_p: int = 14
    rsi_ob: float = 72.0  # 超买（多头不追）
    rsi_os: float = 28.0  # 超卖（空头不追）
    # ATR 波动率过滤
    atr_p: int = 14
    atr_mul: float = 1.0  # TR > ATR * atr_mul 才入场
    # 止损
    stop_atr_mul: float = 2.5  # 止损距离 = ATR * stop_atr_mul（豆粕走势较急，止损略放宽）
    # 成本参数（可按实际手续费调整）
    commission: float = 0.00015  # 万分之1.5 手续费
    slippage: int = 1  # 滑点 Tick 数
    tick_size: float = 1.0  # 豆粕最小变动 1元/吨
    multiplier: int = 10  # 合约乘数 10吨/手
    # 资金
    init_capital: float = 200_000.0
    risk_per_trade: float = 0.015  # 单笔风险 1.5%


def run_backtest(df_raw: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """
    双均线 + ATR 过滤 + RSI 过滤 + ATR 追踪止损 的趋势跟随策略。
    返回含 signal / net_ret / equity 等列的完整 DataFrame。
    """
    df = add_indicators(df_raw, cfg.short_p, cfg.long_p, cfg.rsi_p, cfg.atr_p)

    # ── 信号生成（向量化） ──────────────────────
    bull = df["sma_s"] > df["sma_l"]
    bear = df["sma_s"] < df["sma_l"]

    # ATR 波动率过滤（TR > ATR * mul）
    df["tr"] = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    vol_filter = df["tr"] > df["atr"] * cfg.atr_mul

    # RSI 过滤
    rsi_long = df["rsi"] < cfg.rsi_ob
    rsi_short = df["rsi"] > cfg.rsi_os

    df["raw_signal"] = 0
    df.loc[bull & vol_filter & rsi_long, "raw_signal"] = 1
    df.loc[bear & vol_filter & rsi_short, "raw_signal"] = -1

    # ── 逐行处理移动止损 ─────────────────────────
    signals, highs, lows = [], 0.0, 0.0
    pos = 0

    for i in range(len(df)):
        close = df["close"].iloc[i]
        atr_v = df["atr"].iloc[i]
        raw_s = df["raw_signal"].iloc[i]
        stop = atr_v * cfg.stop_atr_mul

        if pos == 1:
            highs = max(highs, close)
            if close < highs - stop:  # 多头追踪止损
                pos = 0
            elif raw_s == -1:  # 均线反向平多
                pos = 0
        elif pos == -1:
            lows = min(lows, close)
            if close > lows + stop:  # 空头追踪止损
                pos = 0
            elif raw_s == 1:  # 均线反向平空
                pos = 0

        # 入场
        if pos == 0:
            if raw_s == 1:
                pos, highs = 1, close
            elif raw_s == -1:
                pos, lows = -1, close

        signals.append(pos)

    df["signal"] = signals

    # ── 收益计算（含手续费与滑点） ────────────────
    cost_per_trade = (
        df["close"] * cfg.commission + cfg.slippage * cfg.tick_size
    ) / df["close"]  # 转化为百分比影响

    df["pct_chg"] = df["close"].pct_change()
    df["raw_ret"] = df["signal"].shift(1) * df["pct_chg"]
    df["trade_flag"] = df["signal"].diff().abs().fillna(0)
    df["net_ret"] = df["raw_ret"] - df["trade_flag"] * cost_per_trade
    df["net_ret"] = df["net_ret"].fillna(0)

    # 权益曲线
    df["equity"] = cfg.init_capital * (1 + df["net_ret"]).cumprod()
    df["equity_raw"] = cfg.init_capital * (1 + df["raw_ret"].fillna(0)).cumprod()

    return df


# ╔══════════════════════════════════════════════╗
# ║  Part 4: 绩效分析                            ║
# ╚══════════════════════════════════════════════╝


def calc_max_drawdown(equity: pd.Series) -> tuple[float, int]:
    """返回 (最大回撤比例, 最长回撤天数)"""
    roll_max = equity.cummax()
    drawdown = (equity - roll_max) / roll_max
    max_dd = drawdown.min()
    underwater = (drawdown < 0).astype(int)
    dd_lengths = underwater * (
        underwater.groupby((underwater == 0).cumsum()).cumcount() + 1
    )
    max_dd_days = int(dd_lengths.max())
    return max_dd, max_dd_days


def calc_consecutive(series: pd.Series) -> tuple[int, int]:
    """计算最大连续盈利次数和最大连续亏损次数"""
    wins = (series > 0).astype(int)
    losses = (series < 0).astype(int)

    def max_run(s: pd.Series) -> int:
        cumsum = s.cumsum()
        return int((cumsum - cumsum.where(s == 0).ffill().fillna(0)).max())

    return max_run(wins), max_run(losses)


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    凯利公式：f* = p/L - (1-p)/W
    建议实盘使用 1/4 凯利以控制风险
    """
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
    annual_ret = (eq.iloc[-1] / cfg.init_capital) ** (252 / total_days) - 1
    sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
    max_dd, max_dd_days = calc_max_drawdown(eq)
    max_wins, max_losses = calc_consecutive(ret)
    win_rate = len(win_trades) / len(trades) if len(trades) > 0 else 0
    avg_win = win_trades.mean() if len(win_trades) > 0 else 0
    avg_loss = abs(loss_trades.mean()) if len(loss_trades) > 0 else 0
    profit_factor = (
        win_trades.sum() / abs(loss_trades.sum())
        if abs(loss_trades.sum()) > 0
        else np.inf
    )
    kelly = kelly_fraction(win_rate, avg_win, avg_loss)

    report = {
        "初始资金": f"¥{cfg.init_capital:,.0f}",
        "最终权益": f"¥{eq.iloc[-1]:,.2f}",
        "年化收益率": f"{annual_ret:.2%}",
        "夏普比率": f"{sharpe:.3f}",
        "最大回撤": f"{max_dd:.2%}",
        "最长回撤天数": f"{max_dd_days} 天",
        "总交易次数": f"{len(trades)}",
        "胜率": f"{win_rate:.2%}",
        "盈亏比": f"{(avg_win/avg_loss if avg_loss>0 else 0):.2f}",
        "盈利因子": f"{profit_factor:.2f}",
        "最大连续盈利": f"{max_wins} 次",
        "最大连续亏损": f"{max_losses} 次",
        "全凯利仓位比例": f"{kelly:.2%}",
        "1/4凯利（建议）": f"{kelly/4:.2%}",
    }
    return report


def print_report(report: dict):
    print("\n" + "═" * 50)
    print("  📊 豆粕量化策略 — 绩效报告")
    print("═" * 50)
    for k, v in report.items():
        print(f"  {k:<18} {v}")
    print("═" * 50 + "\n")


def save_report_txt(report: dict, file_path: str) -> None:
    """将回测绩效结果保存到指定路径的 txt 文件中"""
    lines = ["豆粕量化策略 — 回测绩效报告", "=" * 30]
    for k, v in report.items():
        lines.append(f"{k}: {v}")
    content = "\n".join(lines) + "\n"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    log.info(f"回测绩效报告已保存到: {file_path}")


# ╔══════════════════════════════════════════════╗
# ║  Part 5: 参数平原热力图                      ║
# ╚══════════════════════════════════════════════╝


def param_plain_scan(
    df_raw: pd.DataFrame,
    short_range=(10, 40, 5),
    long_range=(40, 120, 10),
) -> pd.DataFrame:
    """
    扫描均线参数组合，输出夏普比率热力图。
    对豆粕采用更长周期的参数区间，寻找稳定的“参数高原”。
    """
    shorts = list(range(*short_range))
    longs = list(range(*long_range))
    results = pd.DataFrame(index=shorts, columns=longs, dtype=float)

    total = len(shorts) * len(longs)
    cnt = 0
    log.info(f"开始参数平原扫描，共 {total} 组组合...")

    for s, l in product(shorts, longs):
        if s >= l:
            results.loc[s, l] = np.nan
            continue
        cfg = BacktestConfig()
        cfg.short_p, cfg.long_p = s, l
        try:
            df_bt = run_backtest(df_raw, cfg)
            ret = df_bt["net_ret"].dropna()
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
        annot=True,
        fmt=".2f",
        cmap="RdYlGn",
        center=0,
        linewidths=0.5,
        linecolor="gray",
        cbar_kws={"label": "夏普比率"},
    )
    plt.title("豆粕 — 双均线参数平原（夏普比率）", fontsize=14, fontweight="bold")
    plt.xlabel("长周期均线", fontsize=12)
    plt.ylabel("短周期均线", fontsize=12)
    plt.tight_layout()
    if save_path is None:
        save_path = "m_param_plain.png"
    plt.savefig(save_path, dpi=150)
    plt.show()
    log.info(f"参数平原图已保存: {save_path}")


# ╔══════════════════════════════════════════════╗
# ║  Part 5b: 综合绩效可视化                     ║
# ╚══════════════════════════════════════════════╝


def plot_strategy(df: pd.DataFrame, symbol: str = "M", save_path: str | None = None):
    fig = plt.figure(figsize=(16, 14))
    gs = gridspec.GridSpec(4, 1, hspace=0.45, height_ratios=[3, 1, 1, 1])

    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    ax4 = fig.add_subplot(gs[3], sharex=ax1)

    # 子图1：价格 + 均线 + 信号
    ax1.plot(df.index, df["close"], color="#555", lw=1, alpha=0.7, label="收盘价")
    ax1.plot(df.index, df["sma_s"], color="#1e90ff", lw=1.5, label="短均线")
    ax1.plot(df.index, df["sma_l"], color="#ff4500", lw=1.5, label="长均线")

    buy_idx = df.index[df["signal"].diff() > 0]
    sell_idx = df.index[df["signal"].diff() < 0]
    ax1.scatter(
        buy_idx,
        df.loc[buy_idx, "close"],
        marker="^",
        color="lime",
        s=80,
        zorder=5,
        label="开多",
    )
    ax1.scatter(
        sell_idx,
        df.loc[sell_idx, "close"],
        marker="v",
        color="red",
        s=80,
        zorder=5,
        label="平/开空",
    )
    ax1.set_title(f"{symbol} 豆粕量化策略", fontsize=13, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(alpha=0.3)

    # 子图2：权益曲线
    ax2.plot(
        df.index,
        df["equity"],
        color="#2ecc71",
        lw=2,
        label="净收益（扣费后）",
    )
    ax2.plot(
        df.index,
        df["equity_raw"],
        color="#95a5a6",
        lw=1,
        linestyle="--",
        label="毛收益",
    )
    ax2.axhline(df["equity"].iloc[0], color="gray", lw=0.8, linestyle=":")
    ax2.fill_between(
        df.index,
        df["equity"].iloc[0],
        df["equity"],
        where=df["equity"] >= df["equity"].iloc[0],
        alpha=0.15,
        color="green",
    )
    ax2.fill_between(
        df.index,
        df["equity"].iloc[0],
        df["equity"],
        where=df["equity"] < df["equity"].iloc[0],
        alpha=0.15,
        color="red",
    )
    ax2.set_title("权益曲线", fontsize=11)
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(alpha=0.3)

    # 子图3：RSI
    ax3.plot(df.index, df["rsi"], color="#9b59b6", lw=1.2, label="RSI(14)")
    ax3.axhline(70, color="red", lw=0.8, linestyle="--", alpha=0.7)
    ax3.axhline(30, color="green", lw=0.8, linestyle="--", alpha=0.7)
    ax3.fill_between(df.index, 30, 70, alpha=0.05, color="gray")
    ax3.set_ylim(0, 100)
    ax3.set_title("RSI", fontsize=11)
    ax3.grid(alpha=0.3)

    # 子图4：MACD
    colors = ["#e74c3c" if v >= 0 else "#2ecc71" for v in df["macd_hist"]]
    ax4.bar(df.index, df["macd_hist"], color=colors, alpha=0.7, width=1, label="MACD柱")
    ax4.plot(df.index, df["dif"], color="#3498db", lw=1.2, label="DIF")
    ax4.plot(df.index, df["dea"], color="#e67e22", lw=1.2, label="DEA")
    ax4.axhline(0, color="gray", lw=0.8)
    ax4.set_title("MACD", fontsize=11)
    ax4.legend(loc="upper left", fontsize=9)
    ax4.grid(alpha=0.3)

    if save_path is None:
        save_path = "m_strategy_chart.png"
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()
    log.info(f"策略图表已保存: {save_path}")


# ╔══════════════════════════════════════════════╗
# ║  Part 6: ATR 动态仓位计算                    ║
# ╚══════════════════════════════════════════════╝


def calc_position_size(
    total_equity: float,
    risk_ratio: float,
    current_atr: float,
    multiplier: int = 10,
    stop_atr_mul: float = 2.5,
) -> int:
    """
    ATR动态仓位计算：
      手数 = (账户总权益 × 单笔风险比例) / (ATR × 止损倍数 × 合约乘数)
    """
    stop_distance = current_atr * stop_atr_mul
    loss_per_lot = stop_distance * multiplier
    if loss_per_lot <= 0:
        return 1
    lots = (total_equity * risk_ratio) / loss_per_lot
    return max(1, int(np.floor(lots)))


# ╔══════════════════════════════════════════════╗
# ║  Part 7: 实盘框架 (TqSdk 天勤)               ║
# ╚══════════════════════════════════════════════╝

LIVE_CONFIG = {
    "symbol": "DCE.m2609",  # ⚠ 请按当前主力合约调整
    "kline_freq": 15 * 60,  # 15分钟K线
    "short_p": 20,
    "long_p": 60,
    "rsi_p": 14,
    "rsi_ob": 72,
    "rsi_os": 28,
    "stop_atr_mul": 2.5,
    "risk_per_trade": 0.015,
    "max_loss_limit": 5000,  # 日内最大亏损（元）熔断
    "phone": "你的手机号",
    "password": "你的密码",
}

TRADING_WINDOWS = [
    (time(9, 1), time(10, 14)),
    (time(10, 31), time(11, 29)),
    (time(13, 31), time(14, 55)),
    (time(21, 1), time(22, 55)),  # 夜盘
]


def is_trading_time() -> bool:
    now = datetime.now().time()
    return any(s <= now <= e for s, e in TRADING_WINDOWS)


def check_contract_expiry(symbol: str) -> None:
    """简单提醒合约月份是否需要切换"""
    month = datetime.now().month
    if month in (1, 5, 9):
        log.warning(f"⚠ 当前月份 {month} 月，豆粕主力可能切换，请确认合约: {symbol}")


def run_live():
    """
    实盘/模拟盘入口（豆粕）
      - 双均线 + RSI 趋势方向过滤
      - ATR 动态仓位
      - ATR 追踪止损
      - 时间窗口过滤
      - 账户级熔断
    """
    try:
        from tqsdk import TqApi, TqAuth, TargetPosTask
        from tqsdk.ta import MA, RSI as TQ_RSI
    except ImportError:
        log.error("请先安装天勤SDK: pip install tqsdk")
        return

    cfg = LIVE_CONFIG
    check_contract_expiry(cfg["symbol"])

    api = None
    try:
        api = TqApi(auth=TqAuth(cfg["phone"], cfg["password"]))
        account = api.get_account()
        position = api.get_position(cfg["symbol"])
        klines = api.get_kline_serial(cfg["symbol"], cfg["kline_freq"], data_length=200)
        tpos = TargetPosTask(api, cfg["symbol"])

        init_balance = account.balance
        high_entry = 0.0
        low_entry = 0.0
        is_melted = False

        log.info(
            f"✅ 豆粕实盘系统启动 | 合约:{cfg['symbol']} | 初始资金:¥{init_balance:,.0f}"
        )
        log.info(
            f"   当前持仓: 多{position.pos_long}手 / 空{position.pos_short}手"
        )

        while True:
            api.wait_update()

            # A. 账户熔断
            equity = account.balance + account.float_profit
            if equity < (init_balance - cfg["max_loss_limit"]):
                if not is_melted:
                    log.error(
                        f"🔴 熔断触发！当前权益 ¥{equity:,.0f}，强制全平！"
                    )
                    tpos.set_target_pos(0)
                    is_melted = True
                continue

            # B. 时间过滤
            if not is_trading_time():
                continue

            # C. K线更新后计算指标
            if not (
                api.is_changing(klines.iloc[-1], "datetime")
                and len(klines) > cfg["long_p"] + 5
            ):
                continue

            close = float(klines["close"].iloc[-1])
            ma_s = float(MA(klines, cfg["short_p"]).iloc[-1])
            ma_l = float(MA(klines, cfg["long_p"]).iloc[-1])
            rsi = float(TQ_RSI(klines, cfg["rsi_p"]).iloc[-1])

            tr = max(
                klines["high"].iloc[-1] - klines["low"].iloc[-1],
                abs(klines["high"].iloc[-1] - klines["close"].iloc[-2]),
                abs(klines["low"].iloc[-1] - klines["close"].iloc[-2]),
            )
            atr = float(klines["close"].rolling(cfg["rsi_p"]).std().iloc[-1]) or tr

            cur_pos = tpos.get_target_pos()

            # D. 开仓逻辑
            lots = calc_position_size(
                equity,
                cfg["risk_per_trade"],
                atr,
                stop_atr_mul=cfg["stop_atr_mul"],
            )

            if ma_s > ma_l and rsi < cfg["rsi_ob"] and cur_pos <= 0:
                log.info(
                    f"📈 多头信号 | 价格:{close} MA短:{ma_s:.1f} "
                    f"MA长:{ma_l:.1f} RSI:{rsi:.1f} | 建议{lots}手"
                )
                tpos.set_target_pos(lots)
                high_entry = close

            elif ma_s < ma_l and rsi > cfg["rsi_os"] and cur_pos >= 0:
                log.info(
                    f"📉 空头信号 | 价格:{close} MA短:{ma_s:.1f} "
                    f"MA长:{ma_l:.1f} RSI:{rsi:.1f} | 建议{lots}手"
                )
                tpos.set_target_pos(-lots)
                low_entry = close

            # E. 移动止损
            stop_dist = atr * cfg["stop_atr_mul"]

            if cur_pos > 0:
                high_entry = max(high_entry, close)
                if close < high_entry - stop_dist:
                    log.warning(
                        f"⛔ 多头ATR止损 | 最高:{high_entry:.1f} "
                        f"当前:{close:.1f} 止损距:{stop_dist:.1f}"
                    )
                    tpos.set_target_pos(0)

            elif cur_pos < 0:
                if low_entry == 0:
                    low_entry = close
                low_entry = min(low_entry, close)
                if close > low_entry + stop_dist:
                    log.warning(
                        f"⛔ 空头ATR止损 | 最低:{low_entry:.1f} "
                        f"当前:{close:.1f} 止损距:{stop_dist:.1f}"
                    )
                    tpos.set_target_pos(0)

    except KeyboardInterrupt:
        log.info("用户手动停止豆粕策略。")
    except Exception as e:
        log.error(f"系统异常: {e}", exc_info=True)
    finally:
        if api is not None:
            api.close()
            log.info("API已关闭，豆粕策略停止。")


# ╔══════════════════════════════════════════════╗
# ║  Main: 一键运行回测 + 报告 + 图表            ║
# ╚══════════════════════════════════════════════╝


if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "backtest"

    # 为本次运行设置统一的时间戳和日志目录（品种: 豆粕）
    base_dir = os.path.dirname(__file__)
    product_code = "豆粕"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    logs_root = os.path.join(base_dir, "logs", product_code)
    os.makedirs(logs_root, exist_ok=True)
    log_run_dir = os.path.join(logs_root, f"{product_code}_{ts}")
    os.makedirs(log_run_dir, exist_ok=True)

    log_file_path = os.path.join(log_run_dir, "m_strategy.log")
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(file_handler)

    if mode == "live":
        # 实盘/模拟盘模式
        run_live()

    elif mode == "scan":
        # 参数平原扫描
        df_raw = fetch_soymeal_data("M0")
        results = param_plain_scan(df_raw)

        # 为本次扫描创建独立结果目录: results/M/M_时间戳
        base_results_dir = os.path.join(base_dir, "results", product_code)
        os.makedirs(base_results_dir, exist_ok=True)
        run_dir = os.path.join(base_results_dir, f"{product_code}_{ts}")
        os.makedirs(run_dir, exist_ok=True)

        heatmap_path = os.path.join(run_dir, "m_param_plain.png")
        plot_param_plain(results, save_path=heatmap_path)

    else:
        # 回测模式（默认）
        df_raw = fetch_soymeal_data("M0")
        cfg = BacktestConfig()
        df_bt = run_backtest(df_raw, cfg)
        report = performance_report(df_bt, cfg)

        # 为本次回测创建独立结果目录: results/M/M_时间戳
        base_results_dir = os.path.join(base_dir, "results", product_code)
        os.makedirs(base_results_dir, exist_ok=True)
        run_dir = os.path.join(base_results_dir, f"{product_code}_{ts}")
        os.makedirs(run_dir, exist_ok=True)

        # 保存报告和图表到该目录
        print_report(report)
        report_path = os.path.join(run_dir, "m_backtest_report.txt")
        chart_path = os.path.join(run_dir, "m_strategy_chart.png")
        save_report_txt(report, report_path)
        plot_strategy(df_bt, save_path=chart_path)

        log.info("回测完成。运行方式:")
        log.info("  python m_quant_system.py            # 回测")
        log.info("  python m_quant_system.py scan       # 参数平原扫描")
        log.info("  python m_quant_system.py live       # 实盘/模拟盘")

