"""
均值回归引擎：布林带 + RSI + ADX 过滤
"""

from typing import Any, Dict, Optional, Tuple

from strategy.engines.base import BaseEngine


class MeanReversionEngine(BaseEngine):
    """均值回归引擎，适用于 5m/10m/30m 周期"""

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
        均值回归信号：
        - 价格跌破下轨 + RSI 超卖 + ADX 弱趋势 -> 开多
        - 价格突破上轨 + RSI 超买 + ADX 弱趋势 -> 开空
        - 持多时回归中轨或止损 -> 平多
        - 持空时回归中轨或止损 -> 平空
        - in_session_open_protection=True 时，开盘后 N 分钟内仅执行止损平仓，不执行回归中轨平仓
        """
        bb_up, bb_dn, bb_mid = sig["bb_up"], sig["bb_dn"], sig["bb_mid"]
        rsi = sig["rsi_val"]
        adx = sig["adx_approx"]
        atr_val = sig["atr_val"]
        cp = sig["close_price"]

        mr_ok = adx < cfg.get("mr_adx_max", 30)
        pos = pos_dict.get(sym)
        op_long = float(getattr(pos, "open_price_long", 0) or 0) if pos else 0
        op_short = float(getattr(pos, "open_price_short", 0) or 0) if pos else 0
        stop_long = cur_pos > 0 and op_long > 0 and cp < (op_long - 3 * atr_val)
        stop_short = cur_pos < 0 and op_short > 0 and cp > (op_short + 3 * atr_val)

        lots = self.compute_lots(equity, risk_ratio, mult, min_vol, atr_val)

        # 平多：回归中轨或止损（开盘保护期内屏蔽回归中轨和止损，避免跳空/数据不稳误平）
        if cur_pos > 0 and not in_session_open_protection:
            if cp >= bb_mid or stop_long:
                return (0, "flat_long")

        # 平空：回归中轨或止损（开盘保护期内屏蔽回归中轨和止损）
        if cur_pos < 0 and not in_session_open_protection:
            if cp <= bb_mid or stop_short:
                return (0, "flat_short")

        # 开多：跌破下轨 + RSI 超卖 + 无多仓 + ADX 弱趋势 + 安全时段
        if cp < bb_dn and rsi < cfg.get("mr_rsi_long", 30) and cur_pos <= 0 and mr_ok and is_safe_time:
            return (lots, "open_long")

        # 开空：突破上轨 + RSI 超买 + 无空仓 + ADX 弱趋势 + 安全时段
        if cp > bb_up and rsi > cfg.get("mr_rsi_short", 70) and cur_pos >= 0 and mr_ok and is_safe_time:
            return (-lots, "open_short")

        return (None, None)
