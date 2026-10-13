"""
布林_超級趨勢 交易策略 (strategies/bollinger_supertrend_strategy.py)

策略特色：
1. 進場端：採用「布林動能突破 (嚴格拉回確認版)」三根 K 棒狀態機。
2. 出場端：
   - 盤中初始防守：以突破訊號棒 (Bar 1) 之極值作為即時觸價停損。
   - 收盤移動防守：採用超級趨勢線 (SuperTrend，預設長度 10，乘數 3.0) 作為單向不可逆追蹤停損停利軌道。
     具備 Ratchet 機制，防守線永遠只會順勢推進，絕不隨市場大幅回抽而後退，精確鎖住暴衝波段利潤。
   - 結算機制：台指期結算日 13:00 強制平倉。

遵循 PEP 8 與 Python Type Hints 規範。
"""

from typing import Optional, List, Dict, Any
from datetime import datetime

from services.live.bar_aggregator import BarData
from strategies.common.base import BaseStrategy
from strategies.common.models import StrategyConfig, SignalType, SignalEvent, PositionSide
from strategies.common.indicators import calculate_bollinger_bands, calculate_supertrend
from strategies.common.risk import (
    check_intra_bar_stop_loss,
    check_supertrend_exit,
    is_third_wednesday_settlement,
)


class BollingerSuperTrendStrategy(BaseStrategy):
    """
    布林_超級趨勢 量化交易策略實體。
    """

    def __init__(
        self,
        config: Optional[StrategyConfig] = None,
        st_length: int = 10,
        st_multiplier: float = 3.0,
    ) -> None:
        """
        初始化布林_超級趨勢策略。

        參數:
            config (Optional[StrategyConfig]): 策略配置實例。
            st_length (int): SuperTrend ATR 週期長度，預設 10。
            st_multiplier (float): SuperTrend 軌道乘數，預設 3.0。
        """
        super().__init__(config)
        self.config.strategy_name = "布林_超級趨勢"
        self.st_length = st_length
        self.st_multiplier = st_multiplier
        self.reset()

    def reset(self) -> None:
        """重置狀態機暫存資料。"""
        self.pending_signal_side: PositionSide = PositionSide.NONE
        self.signal_bar_timestamp: int = 0
        self.signal_bar_close: float = 0.0
        self.signal_bar_q1: float = 0.0
        self.signal_bar_extreme: float = 0.0

        # 持倉追蹤指標
        self.entry_price: float = 0.0
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
        """
        if current_side == PositionSide.NONE or stop_loss_price <= 0:
            return None

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
        """
        dt_str = datetime.fromtimestamp(bar.timestamp).strftime("%Y-%m-%d %H:%M:%S")

        # 1. 結算日強制平倉
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

        # 2. 計算布林通道與超級趨勢線
        bb_ind = calculate_bollinger_bands(
            history_bars,
            length=self.config.bb_length,
            num_std=self.config.bb_std,
        )
        upper = bb_ind["upper_band"]
        lower = bb_ind["lower_band"]

        st_ind = calculate_supertrend(
            history_bars,
            length=self.st_length,
            multiplier=self.st_multiplier,
        )

        # 3. 持倉中：檢核超級趨勢線出場
        if current_side != PositionSide.NONE:
            exit_signal = check_supertrend_exit(
                timestamp=bar.timestamp,
                close_price=bar.close,
                current_side=current_side,
                entry_price=self.entry_price or bar.close,
                supertrend_val=st_ind["supertrend"],
                direction=st_ind["direction"],
            )
            if exit_signal:
                self.reset()
                return exit_signal

            return None

        # 4. 空倉中：執行三根 K 棒狀態機突破進場
        if current_side == PositionSide.NONE:
            # 狀態 A: 檢核當前收盤棒是否為「Bar 2 拉回確認棒」
            if self.pending_signal_side != PositionSide.NONE:
                target_side = self.pending_signal_side
                sl_price = self.signal_bar_extreme
                self.pending_signal_side = PositionSide.NONE

                if target_side == PositionSide.LONG:
                    is_confirmed = (bar.close <= self.signal_bar_close) and (bar.close >= self.signal_bar_q1)
                    if is_confirmed:
                        self.entry_price = bar.close
                        self.highest_since_entry = bar.close
                        self.lowest_since_entry = bar.close
                        return SignalEvent(
                            timestamp=bar.timestamp,
                            time_str=dt_str,
                            signal_type=SignalType.BUY_OPEN,
                            side=PositionSide.LONG,
                            price=bar.close,
                            stop_loss_price=sl_price,
                            reason=f"拉回確認做多 (超級趨勢出場, 停損 {sl_price:.1f})",
                            extra={"initial_sl": sl_price, "supertrend": st_ind["supertrend"]},
                        )

                elif target_side == PositionSide.SHORT:
                    is_confirmed = (bar.close >= self.signal_bar_close) and (bar.close <= self.signal_bar_q1)
                    if is_confirmed:
                        self.entry_price = bar.close
                        self.highest_since_entry = bar.close
                        self.lowest_since_entry = bar.close
                        return SignalEvent(
                            timestamp=bar.timestamp,
                            time_str=dt_str,
                            signal_type=SignalType.SELL_OPEN,
                            side=PositionSide.SHORT,
                            price=bar.close,
                            stop_loss_price=sl_price,
                            reason=f"反彈確認放空 (超級趨勢出場, 停損 {sl_price:.1f})",
                            extra={"initial_sl": sl_price, "supertrend": st_ind["supertrend"]},
                        )

            # 狀態 B: 檢核當前收盤棒是否構成「Bar 1 突破訊號棒」
            if upper > 0 and lower > 0:
                is_bullish = bar.close > bar.open
                is_bearish = bar.close < bar.open
                body = abs(bar.close - bar.open)

                if bar.close > upper and is_bullish and body > 0:
                    self.pending_signal_side = PositionSide.LONG
                    self.signal_bar_timestamp = bar.timestamp
                    self.signal_bar_close = bar.close
                    self.signal_bar_extreme = bar.low
                    self.signal_bar_q1 = bar.close - (body * self.config.pullback_ratio)
                elif bar.close < lower and is_bearish and body > 0:
                    self.pending_signal_side = PositionSide.SHORT
                    self.signal_bar_timestamp = bar.timestamp
                    self.signal_bar_close = bar.close
                    self.signal_bar_extreme = bar.high
                    self.signal_bar_q1 = bar.close + (body * self.config.pullback_ratio)

        return None
