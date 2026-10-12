"""
秒 K 線匯出資料模型與組態模組 (services/tick_export/models.py)

定義高頻秒 K 線匯出任務之參數設定、單秒資料結構與統計摘要模型。
遵循 PEP 8 與 Python Type Hints 規範。
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class SecondBarRecord(BaseModel):
    """
    單一秒 K 線資料模型。
    包含使用者指定的六大欄位：時間、開盤價、最高價、最低價、收盤價、成交量。
    """
    time: str = Field(..., description="交易時間 (格式: YYYY-MM-DD HH:MM:SS)")
    open: float = Field(..., description="該秒第一筆成交開盤價")
    high: float = Field(..., description="該秒最高成交價")
    low: float = Field(..., description="該秒最低成交價")
    close: float = Field(..., description="該秒最後一筆成交收盤價")
    volume: int = Field(..., description="該秒累積成交口數")


class ExportConfig(BaseModel):
    """
    秒 K 線匯出組態設定模型。
    """
    symbol: str = Field(default="TMFJ6", description="合約代碼 (TMFJ6 對應微台10 / TMF202610)")
    start_date: str = Field(default="2026-09-16", description="上架起始日期 (YYYY-MM-DD)")
    end_date: str = Field(default="2026-10-12", description="匯出截止日期 (YYYY-MM-DD)")
    output_excel: str = Field(default="微台10_每秒逐筆歷史紀錄.xlsx", description="目標 Excel 輸出檔名")
    cache_dir: str = Field(default="data/ticks_cache", description="Tick 本機快取目錄")
    only_active_seconds: bool = Field(default=True, description="是否僅匯出有撮合成交之秒數 (避免空秒灌爆 Excel 行數上限)")


class ExportSummary(BaseModel):
    """
    匯出任務成果摘要統計模型。
    """
    symbol: str = Field(..., description="合約代碼")
    contract_name: str = Field(..., description="合約中文名稱")
    start_date: str = Field(..., description="資料起始日期")
    end_date: str = Field(..., description="資料截止日期")
    total_trading_days: int = Field(..., description="總處理交易日數")
    total_ticks_processed: int = Field(..., description="總處理逐筆成交 Tick 筆數")
    total_second_bars: int = Field(..., description="總產出秒 K 筆數")
    total_volume: int = Field(..., description="總累積成交口數")
    output_file_path: str = Field(..., description="產出 Excel 檔案絕對路徑")
    file_size_mb: float = Field(..., description="產出檔案大小 (MB)")
