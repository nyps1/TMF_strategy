"""
微型台指期回測服務模組 (services/backtest)

提供回測設定、事件撮合引擎、資料載入器與報告生成器之統一匯出介面。
"""

from .models import (
    PositionSide,
    ExitReason,
    BacktestConfig,
    ActivePosition,
    TradeRecord,
    BacktestMetrics,
)
from .indicators import (
    calculate_bollinger_bands,
    calculate_atr,
    populate_all_indicators,
)
from .data_loader import DataLoader, get_third_wednesday
from .engine import BacktestEngine
from .reporter import BacktestReporter

__all__ = [
    "PositionSide",
    "ExitReason",
    "BacktestConfig",
    "ActivePosition",
    "TradeRecord",
    "BacktestMetrics",
    "calculate_bollinger_bands",
    "calculate_atr",
    "populate_all_indicators",
    "DataLoader",
    "get_third_wednesday",
    "BacktestEngine",
    "BacktestReporter",
]
