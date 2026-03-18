"""
策略引擎包：趋势突破 / 均值回归
"""

from typing import Union

from strategy.engines.base import BaseEngine
from strategy.engines.trend_engine import TrendEngine
from strategy.engines.mr_engine import MeanReversionEngine

# 5m/10m/30m -> 均值回归；60m/1d -> 趋势突破
MR_TIMEFRAMES = ("5m", "10m", "30m")


def get_engine(tf_str: str) -> Union[TrendEngine, MeanReversionEngine]:
    """
    根据周期字符串返回对应引擎实例。

    Args:
        tf_str: 周期字符串，如 "5m", "10m", "30m", "60m", "1d"

    Returns:
        TrendEngine 或 MeanReversionEngine 实例
    """
    if tf_str in MR_TIMEFRAMES:
        return MeanReversionEngine()
    return TrendEngine()


__all__ = [
    "BaseEngine",
    "TrendEngine",
    "MeanReversionEngine",
    "get_engine",
]
