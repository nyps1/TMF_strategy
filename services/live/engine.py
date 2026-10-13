"""
即時交易與風控執行引擎 (services/live/engine.py)

串接即時行情聚合器 (RealTimeBarAggregator) 與決策者策略 (BaseStrategy)，
提供模擬實盤 (Paper Trading) 與風控撮合：
1. 盤中 Tick 級別高頻風控：毫秒級觸價停損 (Intra-bar Stop Loss)。
2. 收盤 Bar 級別策略決策：動態口數計算 (safe_qty)、進場撮合與 ATR 停利。
3. 扣除真實摩擦成本 (手續費、期交稅、滑價)。
4. 狀態變更事件派發至 WebSocket 廣播器。

遵循 PEP 8 與 Python Type Hints 規範。
"""

import math
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime

from services.live.bar_aggregator import RealTimeBarAggregator, BarData
from services.strategy.models import StrategyConfig, SignalType, SignalEvent, PositionSide
from services.strategy.base import BaseStrategy
from services.strategy.bollinger_pullback import BollingerPullbackStrategy


class LiveTradingEngine:
    """
    即時量化交易與風控撮合引擎 (支援 Paper Trading 模擬實盤與未來實盤下單接入)。
    """

    def __init__(
        self,
        strategy: Optional[BaseStrategy] = None,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        """
        初始化即時交易引擎。

        參數:
            strategy (Optional[BaseStrategy]): 決策者策略實例。若未提供則預設使用動態布林突破策略。
            on_event (Optional[Callable[[str, Dict[str, Any]], None]]): 全域事件廣播回呼 (用於 WebSocket 推播)。
        """
        self.strategy: BaseStrategy = strategy or BollingerPullbackStrategy()
        self.config: StrategyConfig = self.strategy.config
        self.on_event: Optional[Callable[[str, Dict[str, Any]], None]] = on_event

        # 帳戶資金與持倉狀態
        self.initial_capital: float = self.config.initial_capital
        self.equity: float = self.config.initial_capital
        self.realized_pnl: float = 0.0

        self.current_side: PositionSide = PositionSide.NONE
        self.entry_price: float = 0.0
        self.entry_time: str = ""
        self.qty: int = 0
        self.stop_loss_price: float = 0.0

        # 交易歷史與 K 棒歷史快取
        self.history_bars: List[BarData] = []
        self.completed_trades: List[Dict[str, Any]] = []
        self.trade_counter: int = 0

        # 初始化即時聚合器 (預設 300 秒 = 5 分鐘)
        interval_sec = 300
        if self.config.interval.endswith("m"):
            interval_sec = int(self.config.interval[:-1]) * 60
        elif self.config.interval.endswith("h"):
            interval_sec = int(self.config.interval[:-1]) * 3600

        self.aggregator: RealTimeBarAggregator = RealTimeBarAggregator(
            interval_seconds=interval_sec,
            on_bar_update=self._handle_bar_update,
            on_bar_close=self._handle_bar_close,
        )

    def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """派發內部事件至外部廣播系統。"""
        if self.on_event:
            try:
                self.on_event(event_type, data)
            except Exception:
                pass

    def _calculate_safe_qty(self, current_price: float) -> int:
        """
        計算安全動態口數 (風控核心)。

        公式:
            theoretical_qty = (equity * max_leverage) / (current_price * point_value)
            margin_qty = equity / margin_req
            safe_qty = floor(min(theoretical_qty, margin_qty))
        """
        if self.equity <= 0 or current_price <= 0:
            return 0
        theoretical_qty = (self.equity * self.config.max_leverage) / (current_price * self.config.point_value)
        margin_qty = self.equity / self.config.margin_req
        safe_qty = int(math.floor(min(theoretical_qty, margin_qty)))
        return max(0, safe_qty)

    def _calculate_friction_costs(self, entry_price: float, exit_price: float, qty: int) -> float:
        """
        計算單筆完整交易之摩擦成本 (手續費 + 期交稅)。
        """
        total_fee = self.config.fee_per_contract * 2 * qty
        entry_val = entry_price * self.config.point_value * qty
        exit_val = exit_price * self.config.point_value * qty
        total_tax = (entry_val + exit_val) * self.config.tax_rate
        return round(total_fee + total_tax, 2)

    def _handle_bar_update(self, bar: BarData) -> None:
        """處理當前未收盤浮動棒之增量更新。"""
        self._emit("bar_update", bar.to_dict())

    def _handle_bar_close(self, bar: BarData) -> None:
        """
        處理 K 棒收盤事件：
        1. 納入歷史 K 棒快取。
        2. 呼叫決策者策略 on_bar_close。
        3. 處理進場或收盤停利出場。
        """
        self.history_bars.append(bar)
        # 限制快取長度以維持 O(1) 記憶體
        if len(self.history_bars) > 500:
            self.history_bars.pop(0)

        self._emit("bar_close", bar.to_dict())

        # 呼叫策略狀態機
        signal = self.strategy.on_bar_close(bar, self.history_bars, self.current_side)
        if signal:
            self._process_signal(signal)

    def _process_signal(self, signal: SignalEvent) -> None:
        """處理策略所產出之交易訊號。"""
        self._emit("signal", signal.model_dump())

        # 1. 處理進場訊號
        if signal.signal_type in (SignalType.BUY_OPEN, SignalType.SELL_OPEN):
            if self.current_side == PositionSide.NONE:
                # 計算滑價進場價
                slippage = self.config.slippage_points
                actual_price = signal.price + (slippage if signal.side == PositionSide.LONG else -slippage)

                qty = self._calculate_safe_qty(actual_price)
                if qty <= 0:
                    self._emit("risk_reject", {
                        "reason": "資金不足或超過槓桿限制",
                        "equity": self.equity,
                        "required_margin": self.config.margin_req,
                    })
                    return

                self.current_side = signal.side
                self.entry_price = actual_price
                self.entry_time = signal.time_str or datetime.fromtimestamp(signal.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                self.qty = qty
                self.stop_loss_price = signal.stop_loss_price

                self._emit("position_open", {
                    "side": self.current_side.value,
                    "qty": self.qty,
                    "entry_price": self.entry_price,
                    "entry_time": self.entry_time,
                    "stop_loss_price": self.stop_loss_price,
                    "reason": signal.reason,
                })

        # 2. 處理平倉訊號 (停利 / 停損 / 結算)
        elif signal.signal_type in (SignalType.TAKE_PROFIT, SignalType.STOP_LOSS, SignalType.SETTLEMENT):
            if self.current_side != PositionSide.NONE:
                slippage = self.config.slippage_points
                # 出場滑價方向：多單平倉賣出價降低，空單平倉買進價增加
                actual_exit_price = signal.price + (-slippage if self.current_side == PositionSide.LONG else slippage)
                exit_time = signal.time_str or datetime.fromtimestamp(signal.timestamp).strftime("%Y-%m-%d %H:%M:%S")

                # 計算損益
                if self.current_side == PositionSide.LONG:
                    gross_pts = actual_exit_price - self.entry_price
                else:
                    gross_pts = self.entry_price - actual_exit_price

                gross_pnl = gross_pts * self.config.point_value * self.qty
                costs = self._calculate_friction_costs(self.entry_price, actual_exit_price, self.qty)
                net_pnl = round(gross_pnl - costs, 2)

                self.realized_pnl += net_pnl
                self.equity += net_pnl
                self.trade_counter += 1

                trade_record = {
                    "trade_id": self.trade_counter,
                    "side": self.current_side.value,
                    "entry_time": self.entry_time,
                    "entry_price": self.entry_price,
                    "exit_time": exit_time,
                    "exit_price": actual_exit_price,
                    "qty": self.qty,
                    "exit_reason": signal.reason,
                    "gross_pnl": gross_pnl,
                    "costs": costs,
                    "net_pnl": net_pnl,
                    "equity_after": round(self.equity, 2),
                }
                self.completed_trades.append(trade_record)

                # 重置倉位
                self.current_side = PositionSide.NONE
                self.entry_price = 0.0
                self.entry_time = ""
                self.qty = 0
                self.stop_loss_price = 0.0

                self._emit("trade_closed", trade_record)
                self._emit("equity_update", {
                    "equity": round(self.equity, 2),
                    "realized_pnl": round(self.realized_pnl, 2),
                })

    def on_tick(self, timestamp: int, price: float, volume: int) -> None:
        """
        即時 Tick 入口點 (高頻核心)。

        參數:
            timestamp (int): Unix Epoch 時間戳。
            price (float): 最新成交價。
            volume (int): 成交量。
        """
        # 1. 優先盤中觸價停損檢核 (Intra-bar Stop Loss)
        if self.current_side != PositionSide.NONE and self.stop_loss_price > 0:
            sl_signal = self.strategy.on_tick(
                timestamp=timestamp,
                price=price,
                current_side=self.current_side,
                entry_price=self.entry_price,
                stop_loss_price=self.stop_loss_price,
            )
            if sl_signal:
                self._process_signal(sl_signal)

        # 2. 派發至即時 K 棒聚合器
        self.aggregator.feed_tick(timestamp=timestamp, price=price, volume=volume)

        # 3. 廣播 Tick 事件
        self._emit("tick", {
            "timestamp": timestamp,
            "price": price,
            "volume": volume,
        })

    def get_status(self) -> Dict[str, Any]:
        """
        取得引擎目前整體運行狀態與持倉摘要。

        返回:
            Dict[str, Any]: 狀態摘要字典。
        """
        unrealized_pnl = 0.0
        latest_price = self.aggregator.current_bar.close if self.aggregator.current_bar else 0.0
        if self.current_side != PositionSide.NONE and latest_price > 0:
            if self.current_side == PositionSide.LONG:
                pts = latest_price - self.entry_price
            else:
                pts = self.entry_price - latest_price
            unrealized_pnl = round(pts * self.config.point_value * self.qty, 2)

        return {
            "strategy": self.strategy.get_strategy_info(),
            "account": {
                "initial_capital": self.initial_capital,
                "equity": round(self.equity, 2),
                "realized_pnl": round(self.realized_pnl, 2),
                "unrealized_pnl": unrealized_pnl,
                "total_trades": len(self.completed_trades),
            },
            "position": {
                "side": self.current_side.value,
                "qty": self.qty,
                "entry_price": self.entry_price,
                "entry_time": self.entry_time,
                "stop_loss_price": self.stop_loss_price,
            },
            "latest_bar": self.aggregator.current_bar.to_dict() if self.aggregator.current_bar else None,
        }
