"""
趋势突破引擎：唐奇安通道 + ADX + MA 过滤
"""

from typing import Any, Dict, Optional, Tuple

from strategy.engines.base import BaseEngine


class TrendEngine(BaseEngine):
    """趋势突破引擎，适用于 60m/1d 周期"""

    def signal(
        self,
        sym: str,
        sig: Dict[str, Any],
        cur_pos: int,
        pos_dict: Dict[str, Any],
        cfg: Dict[str, Any],
        is_safe_time: bool,
        equity: float,
        mult: float,
        min_vol: int,
        risk_ratio: float = 0.01,
        in_session_open_protection: bool = False,
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        趋势突破信号：
        - 突破上轨 + ADX 过滤 + MA 多头排列 -> 开多
        - 跌破下轨 + ADX 过滤 + MA 空头排列 -> 开空
        - 持多时跌破平多价 -> 平多
        - 持空时突破平空价 -> 平空
        """
        trend_ok = sig["adx_approx"] > cfg.get("adx_threshold", 20)
        h20, l20, h10, l10 = sig["high_20"], sig["low_20"], sig["high_10"], sig["low_10"]
        ma_lo, ma_sh = sig["ma_long_ok"], sig["ma_short_ok"]
        cp = sig["close_price"]
        atr_val = sig["atr_val"]

        lots = self.compute_lots(equity, risk_ratio, mult, min_vol, atr_val)

        # 开多：突破上轨、无多仓、趋势有效、安全时段、MA 多头
        if cp > h20 and cur_pos <= 0 and trend_ok and is_safe_time and ma_lo:
            return (lots, "open_long")

        # 开空：跌破下轨、无空仓、趋势有效、安全时段、MA 空头
        if cp < l20 and cur_pos >= 0 and trend_ok and is_safe_time and ma_sh:
            return (-lots, "open_short")

        # 平多：持多时跌破平多价
        if cp < l10 and cur_pos > 0:
            return (0, "flat_long")

        # 平空：持空时突破平空价
        if cp > h10 and cur_pos < 0:
            return (0, "flat_short")

        return (None, None)
