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


class BollingerPullbackCoreStrategy(BaseStrategy):
    """
    原版基準核心策略 (Pure Baseline Bollinger Pullback Core Strategy — Template Method Base Class)。

    封裝最原始、未加任何跨週期追價濾網之純粹三根 K 棒狀態機：
    - Bar 1：突破 5m 布林上下軌，記錄 signal_bar_close、signal_bar_q1、signal_bar_extreme。
    - Bar 2：僅允許情境一（標準淺幅拉回 q1 <= close <= signal_bar_close）。
    - Bar 3：開盤進場訊號 (BUY_OPEN / SELL_OPEN)。
    - 提供 _check_bar1_breakout、_check_bar2_confirm 與 _pass_entry_filters 鉤子方法供子類別繼承擴充。
    """

    def __init__(self, config: Optional[StrategyConfig] = None) -> None:
        """
        初始化原版基準核心策略實例。

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

    def _check_bar1_breakout(self, bar: BarData, upper: float, lower: float) -> None:
        """
        檢核當前收盤棒是否構成「Bar 1 突破訊號棒」。

        參數:
            bar (BarData): 剛收盤之 5 分 K 棒。
            upper (float): 布林通道上軌。
            lower (float): 布林通道下軌。
        """
        if upper <= 0 or lower <= 0:
            return

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

    def _check_bar2_confirm(
        self,
        bar: BarData,
        history_bars: List[BarData],
        target_side: PositionSide,
    ) -> tuple[bool, str, Dict[str, Any]]:
        """
        原版基準核心之「Bar 2 確認棒」判定邏輯（僅允許情境一：嚴格淺幅拉回／反彈）。

        參數:
            bar (BarData): 剛收盤之 Bar 2 K 棒。
            history_bars (List[BarData]): 歷史 K 棒序列。
            target_side (PositionSide): 候選進場方向 (LONG / SHORT)。

        返回:
            tuple[bool, str, Dict[str, Any]]: (是否確認通過, 進場模式標籤, 附加資訊字典)。
        """
        if target_side == PositionSide.LONG:
            is_pullback = (bar.close <= self.signal_bar_close) and (bar.close >= self.signal_bar_q1)
            if is_pullback:
                return True, "拉回確認", {}
        elif target_side == PositionSide.SHORT:
            is_pullback = (bar.close >= self.signal_bar_close) and (bar.close <= self.signal_bar_q1)
            if is_pullback:
                return True, "反彈確認", {}
        return False, "", {}

    def _pass_entry_filters(
        self,
        bar: BarData,
        history_bars: List[BarData],
        target_side: PositionSide,
    ) -> bool:
        """
        進場額外過濾通道鉤子方法 (Filter Channel Hook)。
        原版基準核心預設回傳 True；後續子類別可覆寫此方法疊加新過濾通道。

        參數:
            bar (BarData): 剛收盤之 Bar 2 K 棒。
            history_bars (List[BarData]): 歷史 K 棒序列。
            target_side (PositionSide): 候選進場方向 (LONG / SHORT)。

        返回:
            bool: 若通過所有過濾通道則回傳 True，否則回傳 False。
        """
        return True

    def on_bar_close(
        self,
        bar: BarData,
        history_bars: List[BarData],
        current_side: PositionSide,
    ) -> Optional[SignalEvent]:
        """
        K 棒收盤事件處理常式（Template Method 骨架）。

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
            # 狀態 A: 檢核當前收盤棒是否為「Bar 2 確認棒」
            if self.pending_signal_side != PositionSide.NONE:
                target_side = self.pending_signal_side
                sl_price = self.signal_bar_extreme
                self.pending_signal_side = PositionSide.NONE  # 評估完畢清除待確認標記

                confirmed, mode_tag, extra_info = self._check_bar2_confirm(bar, history_bars, target_side)
                if confirmed and self._pass_entry_filters(bar, history_bars, target_side):
                    self.highest_since_entry = bar.close
                    self.lowest_since_entry = bar.close
                    merged_extra = {"initial_sl": sl_price, "atr": atr}
                    merged_extra.update(extra_info)

                    if target_side == PositionSide.LONG:
                        return SignalEvent(
                            timestamp=bar.timestamp,
                            time_str=dt_str,
                            signal_type=SignalType.BUY_OPEN,
                            side=PositionSide.LONG,
                            price=bar.close,
                            stop_loss_price=sl_price,
                            reason=f"{mode_tag}成立立即開盤進場 (做多, 停損 {sl_price:.1f})",
                            extra=merged_extra,
                        )
                    elif target_side == PositionSide.SHORT:
                        return SignalEvent(
                            timestamp=bar.timestamp,
                            time_str=dt_str,
                            signal_type=SignalType.SELL_OPEN,
                            side=PositionSide.SHORT,
                            price=bar.close,
                            stop_loss_price=sl_price,
                            reason=f"{mode_tag}成立立即開盤進場 (放空, 停損 {sl_price:.1f})",
                            extra=merged_extra,
                        )

            # 狀態 B: 檢核當前收盤棒是否構成「Bar 1 突破訊號棒」
            self._check_bar1_breakout(bar, upper, lower)

        return None


class HtfMaContinuationStrategy(BollingerPullbackCoreStrategy):
    """
    方案一策略 (Plan 1 HTF MA Continuation Strategy — 繼承自原版基準核心 BollingerPullbackCoreStrategy)。

    透過繼承原版基準核心：
    1. 優先呼叫 super()._check_bar2_confirm(...) 保留原版情境一（淺幅拉回）確認邏輯。
    2. 當 Bar 2 超越 Bar 1 收盤價（情境三）時，檢核已完整收盤之 30m 與 1h 20MA 斜率是否雙重同向共振。
    """

    def _compute_htf_slopes(
        self,
        history_bars: List[BarData],
        current_ts: int,
    ) -> tuple[float, float]:
        """
        從歷史 5 分 K 序列聚合已完整收盤之 30 分 K 與 1 小時 K，計算大週期 SMA 斜率。
        - 30 分 K (標籤 b_ts) 於 b_ts + 1500 秒收盤。
        - 1 小時 K (標籤 b_ts) 於 b_ts + 3300 秒收盤。

        參數:
            history_bars (List[BarData]): 已收盤之 5 分 K 歷史序列。
            current_ts (int): 當前 Bar 2 收盤時間戳 (秒)。

        返回:
            tuple[float, float]: (slope_30m, slope_1h)。
        """
        ma_len = int(getattr(self.config, "htf_ma_length", 20) or 20)
        if not history_bars or ma_len <= 0:
            return 0.0, 0.0

        buckets_30m: Dict[int, float] = {}
        buckets_1h: Dict[int, float] = {}
        for b in history_bars:
            b30 = (int(b.timestamp) // 1800) * 1800
            if b30 + 1500 <= current_ts:
                buckets_30m[b30] = float(b.close)
            b1h = (int(b.timestamp) // 3600) * 3600
            if b1h + 3300 <= current_ts:
                buckets_1h[b1h] = float(b.close)

        c30 = [buckets_30m[k] for k in sorted(buckets_30m.keys())]
        c1h = [buckets_1h[k] for k in sorted(buckets_1h.keys())]

        slope_30m = 0.0
        if len(c30) >= ma_len + 1:
            ma_cur = sum(c30[-ma_len:]) / ma_len
            ma_prev = sum(c30[-ma_len - 1 : -1]) / ma_len
            slope_30m = ma_cur - ma_prev

        slope_1h = 0.0
        if len(c1h) >= ma_len + 1:
            ma_cur = sum(c1h[-ma_len:]) / ma_len
            ma_prev = sum(c1h[-ma_len - 1 : -1]) / ma_len
            slope_1h = ma_cur - ma_prev

        return slope_30m, slope_1h

    def _check_bar2_confirm(
        self,
        bar: BarData,
        history_bars: List[BarData],
        target_side: PositionSide,
    ) -> tuple[bool, str, Dict[str, Any]]:
        """
        方案一之 Bar 2 確認邏輯：
        1. 先委派父類別 (BollingerPullbackCoreStrategy) 檢核原版情境一（標準淺幅拉回）。
        2. 若不屬於情境一且啟用 enable_htf_continuation，則檢核情境三（Bar 2 動能延續 + 30m & 1h 均線雙重同向）。

        參數:
            bar (BarData): 剛收盤之 Bar 2 K 棒。
            history_bars (List[BarData]): 歷史 K 棒序列。
            target_side (PositionSide): 候選進場方向 (LONG / SHORT)。

        返回:
            tuple[bool, str, Dict[str, Any]]: (是否確認通過, 進場模式標籤, 附加資訊字典)。
        """
        s30, s1h = self._compute_htf_slopes(history_bars, bar.timestamp)
        slope_extra = {"s30": s30, "s1h": s1h}

        # 1. 優先檢核父類別（原版基準核心：情境一標準拉回）
        confirmed, mode_tag, base_extra = super()._check_bar2_confirm(bar, history_bars, target_side)
        if confirmed:
            base_extra.update(slope_extra)
            return True, mode_tag, base_extra

        # 2. 若未啟用方案一動能延續，直接回傳不通過
        if not getattr(self.config, "enable_htf_continuation", True):
            return False, "", {}

        # 3. 檢核方案一：情境三動能延續 + 30m & 1h 20MA 同向斜率過濾
        if target_side == PositionSide.LONG:
            if (bar.close > self.signal_bar_close) and (s30 > 0.0) and (s1h > 0.0):
                return True, "30m&1h均線共振延續", slope_extra
        elif target_side == PositionSide.SHORT:
            if (bar.close < self.signal_bar_close) and (s30 < 0.0) and (s1h < 0.0):
                return True, "30m&1h均線共振延續", slope_extra

        return False, "", {}


class BollingerPullbackStrategy(HtfMaContinuationStrategy):
    """
    系統預設布林動能突破策略實例（繼承自 HtfMaContinuationStrategy，向下相容所有既有呼叫端）。
    - 若需單獨使用或繼承最原始純拉回核心，請直接使用或繼承 BollingerPullbackCoreStrategy。
    - 若需疊加額外過濾通道，可繼承 BollingerPullbackCoreStrategy 或 HtfMaContinuationStrategy 並覆寫 _pass_entry_filters。
    """

    pass
