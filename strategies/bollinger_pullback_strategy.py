"""
布林動能突破策略 - 嚴格拉回確認版 (strategies/bollinger_pullback_strategy.py)

由決策者構思之核心量化交易策略實體：
1. 三根 K 棒狀態機 (Three-Bar State Machine)：
   - Bar 1 (突破棒)：收盤突破布林通道上軌且為紅 K (多頭) 或跌破下軌且為黑 K (空頭)。
     記錄突破收盤價 signal_bar_close，實體 1/4 強度防守點 signal_bar_q1，以及訊號棒極值 signal_bar_extreme。
   - Bar 2 (拉回棒)：嚴格拉回確認 (既不追價且守住 1/4 強勢支撐/壓力)。
     確認成立當下，立即發出 Bar 3 開盤進場訊號 (SignalType.BUY_OPEN / SELL_OPEN)。
   - Bar 3 (進場棒)：以開盤市價即刻掛單成交。
2. 雙軌風控出場機制：
   - 盤中 Tick 觸價即時停損 (Intra-bar Stop Loss)。
   - 收盤動態 ATR 追蹤停利 (Bar-Close Dynamic ATR Trailing Take Profit)。
   - 結算日 13:00 強制平倉 (Monthly Settlement)。

遵循 PEP 8 與 Python Type Hints 規範。
"""

from typing import Optional, List, Dict, Any
from datetime import datetime

from services.live.bar_aggregator import BarData
from strategies.common.base import BaseStrategy
from strategies.common.models import StrategyConfig, SignalType, SignalEvent, PositionSide
from strategies.common.indicators import calculate_bollinger_bands, calculate_atr
from strategies.common.risk import (
    check_intra_bar_stop_loss,
    check_trailing_take_profit,
    is_third_wednesday_settlement,
)


class BollingerPullbackStrategy(BaseStrategy):
    """
    布林動能突破策略 (嚴格拉回確認版) 決策實例。
    """

    def __init__(self, config: Optional[StrategyConfig] = None) -> None:
        """
        初始化策略實例。

        參數:
            config (Optional[StrategyConfig]): 策略配置實例。
        """
        super().__init__(config)
        self.reset()

    def reset(self) -> None:
        """重置狀態機暫存資料。"""
        # 三根 K 棒訊號推進狀態
        self.pending_signal_side: PositionSide = PositionSide.NONE
        self.signal_bar_timestamp: int = 0
        self.signal_bar_close: float = 0.0
        self.signal_bar_q1: float = 0.0
        self.signal_bar_extreme: float = 0.0

        # 持倉追蹤指標
        self.highest_since_entry: float = 0.0
        self.lowest_since_entry: float = 999999.0

    def on_tick(
        self,
        timestamp: int,
        price: float,
        current_side: PositionSide,
        entry_price: float,
        stop_loss_price: float,
    ) -> Optional[SignalEvent]:
        """
        盤中 Tick 觸價即時停損檢核 (Intra-bar Stop Loss)。

        參數:
            timestamp (int): 成交時間戳 (秒)。
            price (float): 當前成交價。
            current_side (PositionSide): 當前持倉方向。
            entry_price (float): 進場成本價。
            stop_loss_price (float): 當前停損價。

        返回:
            Optional[SignalEvent]: 若擊穿停損價則發出 STOP_LOSS 訊號。
        """
        if current_side == PositionSide.NONE or stop_loss_price <= 0:
            return None

        # 更新持倉期間極值
        if price > self.highest_since_entry:
            self.highest_since_entry = price
        if price < self.lowest_since_entry:
            self.lowest_since_entry = price

        return check_intra_bar_stop_loss(
            timestamp=timestamp,
            price=price,
            current_side=current_side,
            stop_loss_price=stop_loss_price,
        )

    def on_bar_close(
        self,
        bar: BarData,
        history_bars: List[BarData],
        current_side: PositionSide,
    ) -> Optional[SignalEvent]:
        """
        K 棒收盤事件處理常式 (Bar-Close Event Handler)。

        參數:
            bar (BarData): 剛收盤之 K 棒數據。
            history_bars (List[BarData]): 歷史 K 棒序列。
            current_side (PositionSide): 當前持倉方向。

        返回:
            Optional[SignalEvent]: 產生的訊號事件。
        """
        dt_str = datetime.fromtimestamp(bar.timestamp).strftime("%Y-%m-%d %H:%M:%S")

        # 1. 優先處理結算日強制平倉
        if current_side != PositionSide.NONE and is_third_wednesday_settlement(bar.timestamp):
            self.reset()
            return SignalEvent(
                timestamp=bar.timestamp,
                time_str=dt_str,
                signal_type=SignalType.SETTLEMENT,
                side=current_side,
                price=bar.close,
                reason="結算日 13:00 強制平倉 (Monthly Settlement)",
            )

        # 2. 調用通用指標計算庫
        bb_ind = calculate_bollinger_bands(
            history_bars,
            length=self.config.bb_length,
            num_std=self.config.bb_std,
        )
        atr = calculate_atr(history_bars, length=self.config.atr_length)
        upper = bb_ind["upper_band"]
        lower = bb_ind["lower_band"]

        # 3. 持倉中：檢核動態 ATR 追蹤停利
        if current_side != PositionSide.NONE and atr > 0:
            tp_signal = check_trailing_take_profit(
                timestamp=bar.timestamp,
                close_price=bar.close,
                current_side=current_side,
                highest_since_entry=self.highest_since_entry,
                lowest_since_entry=self.lowest_since_entry,
                atr=atr,
                atr_multiplier=self.config.atr_tp_multiplier,
            )
            if tp_signal:
                self.reset()
                return tp_signal

            return None

        # 4. 空倉中：執行三根 K 棒狀態機進場決策
        if current_side == PositionSide.NONE:
            # 狀態 A: 檢核當前收盤棒是否為「Bar 2 拉回確認棒」
            if self.pending_signal_side != PositionSide.NONE:
                target_side = self.pending_signal_side
                sl_price = self.signal_bar_extreme
                self.pending_signal_side = PositionSide.NONE  # 評估完畢清除待確認標記

                if target_side == PositionSide.LONG:
                    # 多頭確認條件: 既不追高 (close <= close[1]) 且防守於頂部 1/4 強勢支撐 (close >= q1)
                    is_confirmed = (bar.close <= self.signal_bar_close) and (bar.close >= self.signal_bar_q1)
                    if is_confirmed:
                        self.highest_since_entry = bar.close
                        self.lowest_since_entry = bar.close
                        return SignalEvent(
                            timestamp=bar.timestamp,
                            time_str=dt_str,
                            signal_type=SignalType.BUY_OPEN,
                            side=PositionSide.LONG,
                            price=bar.close,
                            stop_loss_price=sl_price,
                            reason=f"拉回確認成立立即開盤進場 (做多, 停損 {sl_price:.1f})",
                            extra={"initial_sl": sl_price, "atr": atr},
                        )

                elif target_side == PositionSide.SHORT:
                    # 空頭確認條件: 既不追空 (close >= close[1]) 且防守於底部 1/4 強勢壓力 (close <= q1)
                    is_confirmed = (bar.close >= self.signal_bar_close) and (bar.close <= self.signal_bar_q1)
                    if is_confirmed:
                        self.highest_since_entry = bar.close
                        self.lowest_since_entry = bar.close
                        return SignalEvent(
                            timestamp=bar.timestamp,
                            time_str=dt_str,
                            signal_type=SignalType.SELL_OPEN,
                            side=PositionSide.SHORT,
                            price=bar.close,
                            stop_loss_price=sl_price,
                            reason=f"反彈確認成立立即開盤進場 (放空, 停損 {sl_price:.1f})",
                            extra={"initial_sl": sl_price, "atr": atr},
                        )

            # 狀態 B: 檢核當前收盤棒是否構成「Bar 1 突破訊號棒」
            if upper > 0 and lower > 0:
                is_bullish = bar.close > bar.open
                is_bearish = bar.close < bar.open
                body = abs(bar.close - bar.open)

                if bar.close > upper and is_bullish and body > 0:
                    # 多頭突破成立，標記等待下一根拉回確認
                    self.pending_signal_side = PositionSide.LONG
                    self.signal_bar_timestamp = bar.timestamp
                    self.signal_bar_close = bar.close
                    self.signal_bar_extreme = bar.low
                    self.signal_bar_q1 = bar.close - (body * self.config.pullback_ratio)
                elif bar.close < lower and is_bearish and body > 0:
                    # 空頭突破成立，標記等待下一根拉回確認
                    self.pending_signal_side = PositionSide.SHORT
                    self.signal_bar_timestamp = bar.timestamp
                    self.signal_bar_close = bar.close
                    self.signal_bar_extreme = bar.high
                    self.signal_bar_q1 = bar.close + (body * self.config.pullback_ratio)

        return None
