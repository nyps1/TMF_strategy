"""
即時交易與串流聚合服務模組 (services/live)

提供即時行情聚合、動態 K 棒維護與即時狀態機調度。
"""

from .bar_aggregator import RealTimeBarAggregator, BarData

__all__ = ["RealTimeBarAggregator", "BarData"]
