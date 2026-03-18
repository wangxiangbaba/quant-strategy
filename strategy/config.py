"""
策略配置：品种、周期参数、风控参数、交易时段
"""

from datetime import date, time
from typing import Any, Dict, List, Tuple


TIMEFRAME_MAP = {
    "5m": 5 * 60,
    "10m": 10 * 60,
    "30m": 30 * 60,
    "60m": 60 * 60,
    "1d": 24 * 60 * 60,
}

# MR: 均值回归参数 | Trend: 趋势突破参数
TF_PARAMS = {
    "5m": {"bb_period": 60, "bb_std": 2.5, "mr_rsi_long": 30, "mr_rsi_short": 70, "mr_adx_max": 25,
           "donchian_entry": 120, "donchian_exit": 60, "ma_short_period": 120, "ma_long_period": 240, "data_length": 500},
    "10m": {"bb_period": 40, "bb_std": 2.2, "mr_rsi_long": 30, "mr_rsi_short": 70, "mr_adx_max": 25,
            "donchian_entry": 72, "donchian_exit": 36, "ma_short_period": 72, "ma_long_period": 144, "data_length": 400},
    "30m": {"bb_period": 20, "bb_std": 2.0, "mr_rsi_long": 35, "mr_rsi_short": 65, "mr_adx_max": 30,
            "donchian_entry": 40, "donchian_exit": 20, "ma_short_period": 40, "ma_long_period": 120, "data_length": 300},
    "60m": {"bb_period": 20, "bb_std": 2.0, "mr_rsi_long": 30, "mr_rsi_short": 70, "mr_adx_max": 25,
            "donchian_entry": 20, "donchian_exit": 10, "ma_short_period": 20, "ma_long_period": 60, "data_length": 250},
    "1d": {"bb_period": 20, "bb_std": 2.0, "mr_rsi_long": 30, "mr_rsi_short": 70, "mr_adx_max": 25,
           "donchian_entry": 20, "donchian_exit": 10, "ma_short_period": 20, "ma_long_period": 60, "data_length": 200},
}

TRADING_WINDOWS = [
    (time(9, 1), time(10, 14)), (time(10, 31), time(11, 29)),
    (time(13, 31), time(14, 55)), (time(21, 1), time(22, 55)),
]

# 安全开仓时段（避开开盘/收盘剧烈波动）
SAFE_ENTRY_WINDOWS = [
    (time(9, 5), time(10, 0)), (time(10, 35), time(11, 15)),
    (time(13, 35), time(14, 45)), (time(21, 5), time(22, 45)),
]

# 开盘保护：各时段开盘后 N 分钟内不执行「回归中轨」平仓，避免跳空/数据不稳误平
SESSION_OPEN_TIMES = [time(9, 1), time(10, 31), time(13, 31), time(21, 1)]
SESSION_OPEN_PROTECTION_MINUTES = 5

# 手动发单超时撤单与重发
ORDER_TIMEOUT_SEC = 30
CANCEL_COOLDOWN_SEC = 2


def _load_tq_account() -> Dict[str, Any]:
    try:
        mod = __import__("conf.config_account", fromlist=["TQ_ACCOUNT"])
        return getattr(mod, "TQ_ACCOUNT", {})
    except Exception:
        return {}


class PortfolioConfig:
    """合并后的策略配置，支持按周期获取"""

    def __init__(self, raw: Dict[str, Any]):
        self._cfg = raw

    def __getitem__(self, key: str) -> Any:
        return self._cfg[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._cfg.get(key, default)

    @property
    def symbols(self) -> List[str]:
        return self._cfg["symbols"]

    @property
    def strategy_type(self) -> str:
        return "mr" if self._cfg.get("_tf_str", "60m") in ["5m", "10m", "30m"] else "trend"

    @classmethod
    def from_tf(cls, tf_str: str, base: Dict[str, Any] = None) -> "PortfolioConfig":
        """按周期合并配置，base 为空时使用默认 PORTFOLIO_BASE"""
        if base is None:
            base = {
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
                "max_daily_loss_per_symbol": 18000.0,  # 需大于 3×ATR 止损额(~1.5万)，避免熔断抢在策略止损前触发
                "init_capital": 500_000.0,
                "bt_start": date(2025, 2, 1),
                "bt_end": date(2025, 3, 1),
                **_load_tq_account(),
            }
        merged = {**base, **TF_PARAMS.get(tf_str, TF_PARAMS["60m"])}
        merged["_tf_str"] = tf_str
        merged["_kline_freq"] = TIMEFRAME_MAP.get(tf_str, 3600)
        return cls(merged)

    def adjust_data_length_for_live(self, mode: str) -> None:
        """live 模式下调整 data_length"""
        if mode != "live":
            return
        min_required = max(
            self.get("donchian_entry", 20),
            self.get("ma_long_period", 60),
            self.get("bb_period", 20),
        ) + 10
        data_len = self._cfg["data_length"]
        self._cfg["data_length"] = max(min(data_len, 350), min_required)
