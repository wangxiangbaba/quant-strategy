"""
策略引擎抽象基类
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

import numpy as np


class BaseEngine(ABC):
    """策略引擎抽象基类"""

    @staticmethod
    def compute_lots(
        equity: float,
        risk_ratio: float,
        mult: float,
        min_vol: int,
        atr_val: float,
    ) -> int:
        """
        根据权益、风险比例、合约乘数、最小手数、ATR 计算开仓手数。

        Args:
            equity: 账户权益
            risk_ratio: 风险比例
            mult: 合约乘数
            min_vol: 最小交易手数
            atr_val: ATR 值

        Returns:
            计算后的手数（>= min_vol）
        """
        if atr_val <= 0 or mult <= 0:
            return min_vol
        loss_per = atr_val * 2 * mult
        raw = max(1, int(np.floor((equity * risk_ratio) / loss_per)))
        lots = max(min_vol, int(np.floor(raw / min_vol)) * min_vol)
        return lots

    @abstractmethod
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
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        计算交易信号。

        Args:
            sym: 品种代码
            sig: 指标字典（含 high_20, low_20, high_10, low_10, bb_up, bb_dn, bb_mid,
                 ma_long_ok, ma_short_ok, atr_val, adx_approx, rsi_val, close_price 等）
            cur_pos: 当前净持仓（多 - 空）
            pos_dict: 品种 -> 持仓对象
            cfg: 策略配置
            is_safe_time: 是否在安全开仓时段
            equity: 账户权益
            mult: 合约乘数
            min_vol: 最小交易手数
            risk_ratio: 风险比例（如 get_dynamic_risk(equity) 返回值）

        Returns:
            (target, signal_type)
            - target: 目标持仓（正=多，负=空，0=平仓），无信号时为 None
            - signal_type: "open_long"|"open_short"|"flat_long"|"flat_short"|None
        """
        pass
