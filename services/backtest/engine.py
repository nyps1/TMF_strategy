"""
核心回測撮合引擎模組 (services/backtest/engine.py)

負責執行逐根 5 分 K 棒事件驅動撮合：
1. 動態口數與槓桿風控（safe_qty 嚴格計算）。
2. 三根 K 棒狀態機驗證（Bar 1 突破 -> Bar 2 頂部 1/4 拉回守住 -> Bar 3 開盤進場）。
3. 盤中觸碰停損點觸價出場、收盤 ATR crossunder 動能停利、結算日 13:00 強制平倉。
4. 手續費、期交稅與滑價真實摩擦損耗扣除。

遵循 PEP 8 與 Python Type Hints 規範。
"""

import math
import logging
from typing import Optional, List, Dict, Any, Tuple
import pandas as pd
import numpy as np

from .models import (
    PositionSide,
    ExitReason,
    BacktestConfig,
    ActivePosition,
    TradeRecord,
    BacktestMetrics,
)
from .indicators import populate_all_indicators
from .data_loader import get_third_wednesday

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    事件驅動微台指量化回測引擎。
    """
    def __init__(self, config: Optional[BacktestConfig] = None):
        """
        初始化回測引擎。

        參數:
            config (Optional[BacktestConfig]): 回測參數設定。若未提供則使用預設值。
        """
        self.config = config or BacktestConfig()
        self.reset()

    def reset(self) -> None:
        """重置引擎所有運算狀態與帳戶淨值。"""
        self.equity: float = self.config.initial_capital
        self.position: ActivePosition = ActivePosition()
        self.trades: List[TradeRecord] = []
        self.equity_curve: List[Dict[str, Any]] = []
        self.trade_counter: int = 0

        # 三根 K 棒訊號候選暫存狀態
        self.pending_signal_side: PositionSide = PositionSide.NONE
        self.signal_bar_idx: int = -1
        self.signal_bar_close: float = 0.0
        self.signal_bar_q1: float = 0.0
        self.signal_bar_extreme: float = 0.0  # 多頭記錄 low, 空頭記錄 high

        # 確認成立待進場標記 (通知下一根開盤成交)
        self.entry_ready_side: PositionSide = PositionSide.NONE
        self.entry_sl_price: float = 0.0

    def _calculate_safe_qty(self, current_price: float) -> int:
        """
        依據當前帳戶淨值、最大槓桿倍數與單口保證金，計算安全可下口數。

        公式:
            theoretical_max_qty = (equity * max_leverage) / (current_price * point_value)
            margin_allowed_qty = equity / margin_req
            safe_qty = floor(min(theoretical_max_qty, margin_allowed_qty))

        參數:
            current_price (float): 當前市場價格。

        返回:
            int: 允許下單之安全整數口數 (若資金不足則回傳 0)。
        """
        if self.equity <= 0 or current_price <= 0:
            return 0

        max_contract_val = self.equity * self.config.max_leverage
        single_contract_val = current_price * self.config.point_value
        theoretical_qty = max_contract_val / single_contract_val
        margin_qty = self.equity / self.config.margin_req

        safe_qty = int(math.floor(min(theoretical_qty, margin_qty)))
        return max(0, safe_qty)

    def _is_third_wednesday_settlement(self, ts: pd.Timestamp) -> bool:
        """
        檢核時間戳是否為台指期結算日（每月第三個星期三）13:00。

        參數:
            ts (pd.Timestamp): 當前 K 棒時間。

        返回:
            bool: 若為結算時點則回傳 True，否則回傳 False。
        """
        if ts.weekday() == 2 and ts.hour == 13 and ts.minute == 0:
            third_wed = get_third_wednesday(ts.year, ts.month)
            return ts.day == third_wed.day
        return False

    def _close_position(
        self,
        exit_time: str,
        raw_exit_price: float,
        reason: ExitReason,
        apply_slippage: bool = True
    ) -> None:
        """
        執行平倉撮合結算，計入手續費、期交稅與滑價，並更新帳戶淨值與交易紀錄。

        參數:
            exit_time (str): 出場時間字串。
            raw_exit_price (float): 未計入滑價之理論成交價。
            reason (ExitReason): 平倉原因 (停損、ATR停利或換月結算)。
            apply_slippage (bool): 是否計入滑價。
        """
        pos = self.position
        if pos.side == PositionSide.NONE or pos.qty <= 0:
            return

        # 考慮滑價後的真實出場價
        slip = self.config.slippage_points if apply_slippage else 0.0
        if pos.side == PositionSide.LONG:
            exit_price = raw_exit_price - slip
            gross_points = exit_price - pos.entry_price
        else:
            exit_price = raw_exit_price + slip
            gross_points = pos.entry_price - exit_price

        gross_pnl = gross_points * self.config.point_value * pos.qty
        
        # 依真實成本版規則精算手續費與期交稅 (math.ceil 取整數)
        entry_tax = math.ceil(pos.entry_price * self.config.point_value * pos.qty * self.config.tax_rate)
        exit_tax = math.ceil(exit_price * self.config.point_value * pos.qty * self.config.tax_rate)
        tax_total = float(entry_tax + exit_tax)

        entry_fee = math.ceil(self.config.commission_per_order * pos.qty)
        exit_fee = math.ceil(self.config.commission_per_order * pos.qty)
        fee_total = float(entry_fee + exit_fee)

        slippage_loss = (2.0 * self.config.slippage_points * self.config.point_value) * pos.qty

        net_pnl = gross_pnl - fee_total - tax_total - slippage_loss
        self.equity += net_pnl
        self.trade_counter += 1

        cost_basis = pos.entry_price * self.config.point_value * pos.qty
        return_pct = (net_pnl / cost_basis) * 100.0 if cost_basis > 0 else 0.0

        trade = TradeRecord(
            trade_id=self.trade_counter,
            side=pos.side,
            entry_time=pos.entry_time,
            entry_price=round(pos.entry_price, 2),
            exit_time=exit_time,
            exit_price=round(exit_price, 2),
            qty=pos.qty,
            exit_reason=reason,
            gross_pnl=round(gross_pnl, 2),
            fee_total=round(fee_total, 2),
            tax_total=round(tax_total, 2),
            slippage_loss=round(slippage_loss, 2),
            net_pnl=round(net_pnl, 2),
            return_pct=round(return_pct, 4),
            equity_after=round(self.equity, 2)
        )
        self.trades.append(trade)

        # 重置在手持倉
        self.position = ActivePosition()

    def run(self, df: pd.DataFrame) -> BacktestMetrics:
        """
        執行主回測運算迴圈。

        參數:
            df (pd.DataFrame): 具備 OHLCV 之 K 線時序 DataFrame。

        返回:
            BacktestMetrics: 完整回測績效指標數據。
        """
        self.reset()
        
        # 1. 補充計算技術指標
        df = populate_all_indicators(
            df,
            bb_length=self.config.bb_length,
            bb_mult=self.config.bb_mult,
            atr_length=self.config.atr_length,
            atr_sig_length=self.config.atr_sig_length
        )

        n_bars = len(df)
        if n_bars < self.config.bb_length + 2:
            raise ValueError(f"Status: ERROR | Root Cause: K線長度 ({n_bars}) 小於指標最小計算週期 | Suggested Fix: 提供更長時序資料。")

        # 預先擷取 NumPy 陣列以確保向量化取用效能
        opens = df["open"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        closes = df["close"].to_numpy()
        timestamps = df["ts"].tolist()
        bb_uppers = df["bb_upper"].to_numpy()
        bb_lowers = df["bb_lower"].to_numpy()
        atr_vals = df["atr_val"].to_numpy()
        atr_sigs = df["atr_sig"].to_numpy()

        for i in range(1, n_bars):
            ts = timestamps[i]
            cur_open = opens[i]
            cur_high = highs[i]
            cur_low = lows[i]
            cur_close = closes[i]
            cur_time_str = str(ts)

            # ----------------------------------------------------
            # 步驟 A: 第三根 K 棒開盤進場執行 (Bar 3 Open Execution)
            # ----------------------------------------------------
            if self.entry_ready_side != PositionSide.NONE and self.position.side == PositionSide.NONE:
                safe_qty = self._calculate_safe_qty(cur_open)
                if safe_qty >= 1:
                    slip = self.config.slippage_points
                    if self.entry_ready_side == PositionSide.LONG:
                        fill_price = cur_open + slip
                    else:
                        fill_price = cur_open - slip

                    self.position = ActivePosition(
                        side=self.entry_ready_side,
                        qty=safe_qty,
                        entry_price=fill_price,
                        entry_time=cur_time_str,
                        sl_price=self.entry_sl_price,
                        entry_bar_index=i
                    )
                # 清除進場準備標記
                self.entry_ready_side = PositionSide.NONE

            # ----------------------------------------------------
            # 步驟 B: 出場撮合檢核 (若當前持有部位)
            # ----------------------------------------------------
            if self.position.side != PositionSide.NONE:
                pos = self.position
                
                # 檢核 1: 換月結算日 13:00 強制平倉
                if self._is_third_wednesday_settlement(ts):
                    self._close_position(
                        exit_time=cur_time_str,
                        raw_exit_price=cur_close,
                        reason=ExitReason.ROLLOVER_SETTLEMENT
                    )

                # 檢核 2: 盤中觸碰停損點
                elif pos.side == PositionSide.LONG and cur_low <= pos.sl_price:
                    # 跌破停損價 (若開盤即跳空跌破，以開盤價成交)
                    trigger_price = min(cur_open, pos.sl_price)
                    self._close_position(
                        exit_time=cur_time_str,
                        raw_exit_price=trigger_price,
                        reason=ExitReason.STOP_LOSS
                    )

                elif pos.side == PositionSide.SHORT and cur_high >= pos.sl_price:
                    # 突破停損價 (若開盤即跳空突破，以開盤價成交)
                    trigger_price = max(cur_open, pos.sl_price)
                    self._close_position(
                        exit_time=cur_time_str,
                        raw_exit_price=trigger_price,
                        reason=ExitReason.STOP_LOSS
                    )

                # 檢核 3: ATR 動能停利 (ta.crossunder(atr_val, atr_sig))
                # 依訪談確認決策：於當根 5 分 K 收盤價平倉
                elif not np.isnan(atr_vals[i]) and not np.isnan(atr_sigs[i]) and not np.isnan(atr_vals[i-1]) and not np.isnan(atr_sigs[i-1]):
                    prev_above = atr_vals[i-1] >= atr_sigs[i-1]
                    cur_below = atr_vals[i] < atr_sigs[i]
                    if prev_above and cur_below:
                        self._close_position(
                            exit_time=cur_time_str,
                            raw_exit_price=cur_close,
                            reason=ExitReason.ATR_TAKE_PROFIT
                        )

            # ----------------------------------------------------
            # 步驟 C: 第二根確認棒與第一根訊號棒判斷 (僅在空手時評估)
            # ----------------------------------------------------
            if self.position.side == PositionSide.NONE and self.entry_ready_side == PositionSide.NONE:
                # 1. 檢核是否有前一根剛成立的候選訊號棒需做「第二根確認」
                if self.pending_signal_side != PositionSide.NONE and (i - 1 == self.signal_bar_idx):
                    if self.pending_signal_side == PositionSide.LONG:
                        # 多頭確認條件 (真實成本版): ((close <= close[1]) or (close >= q1_long))
                        is_confirmed = (cur_close <= self.signal_bar_close) or (cur_close >= self.signal_bar_q1)
                        if is_confirmed:
                            self.entry_ready_side = PositionSide.LONG
                            self.entry_sl_price = self.signal_bar_extreme
                    elif self.pending_signal_side == PositionSide.SHORT:
                        # 空頭確認條件 (真實成本版): ((close >= close[1]) or (close <= q1_short))
                        is_confirmed = (cur_close >= self.signal_bar_close) or (cur_close <= self.signal_bar_q1)
                        if is_confirmed:
                            self.entry_ready_side = PositionSide.SHORT
                            self.entry_sl_price = self.signal_bar_extreme

                    # 第二根評估結束，清除前期待確認狀態
                    self.pending_signal_side = PositionSide.NONE

                # 2. 檢核當根是否構成「第一根訊號棒」
                if not np.isnan(bb_uppers[i]) and not np.isnan(bb_lowers[i]):
                    # 多頭突破: 收盤大於布林上軌且為紅 K
                    if cur_close > bb_uppers[i] and cur_close > cur_open:
                        body = cur_close - cur_open
                        self.pending_signal_side = PositionSide.LONG
                        self.signal_bar_idx = i
                        self.signal_bar_close = cur_close
                        self.signal_bar_q1 = cur_close - (body * 0.25)
                        self.signal_bar_extreme = cur_low  # 記錄第一根最低價作為多單停損

                    # 空頭突破: 收盤小於布林下軌且為黑 K
                    elif cur_close < bb_lowers[i] and cur_close < cur_open:
                        body = cur_open - cur_close
                        self.pending_signal_side = PositionSide.SHORT
                        self.signal_bar_idx = i
                        self.signal_bar_close = cur_close
                        self.signal_bar_q1 = cur_close + (body * 0.25)
                        self.signal_bar_extreme = cur_high  # 記錄第一根最高價作為空單停損

            # ----------------------------------------------------
            # 步驟 D: 記錄當根權益曲線 (包含未實現損益)
            # ----------------------------------------------------
            current_equity = self.equity
            if self.position.side != PositionSide.NONE:
                pos = self.position
                if pos.side == PositionSide.LONG:
                    unrealized_pnl = (cur_close - pos.entry_price) * self.config.point_value * pos.qty
                else:
                    unrealized_pnl = (pos.entry_price - cur_close) * self.config.point_value * pos.qty
                current_equity += unrealized_pnl

            self.equity_curve.append({
                "ts": cur_time_str,
                "equity": round(current_equity, 2),
                "close": cur_close,
                "in_position": self.position.side != PositionSide.NONE
            })

        # 回測結束時若仍有部位，強制市價平倉以利指標統計
        if self.position.side != PositionSide.NONE:
            last_ts = str(timestamps[-1])
            self._close_position(
                exit_time=last_ts,
                raw_exit_price=closes[-1],
                reason=ExitReason.ROLLOVER_SETTLEMENT
            )

        return self._compute_summary_metrics(df)

    def _compute_summary_metrics(self, df: pd.DataFrame) -> BacktestMetrics:
        """
        計算回測綜合量化績效指標（報酬率、勝率、賺賠比、最大權益回落、夏普比率）。

        參數:
            df (pd.DataFrame): 原始 K 線 DataFrame。

        返回:
            BacktestMetrics: 績效評估指標模型。
        """
        initial_cap = self.config.initial_capital
        final_eq = self.equity
        total_net_profit = final_eq - initial_cap
        total_return_pct = (total_net_profit / initial_cap) * 100.0

        # 計算年化報酬率
        if not df.empty:
            start_dt = df["ts"].iloc[0]
            end_dt = df["ts"].iloc[-1]
            days = (end_dt - start_dt).days or 1
            years = max(days / 365.25, 0.01)
            annualized_return_pct = (((final_eq / initial_cap) ** (1.0 / years)) - 1.0) * 100.0
        else:
            annualized_return_pct = 0.0

        # 交易勝負統計
        total_trades = len(self.trades)
        winning_trades = sum(1 for t in self.trades if t.net_pnl > 0)
        losing_trades = sum(1 for t in self.trades if t.net_pnl < 0)
        win_rate_pct = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0

        # 計算策略毛利 (未扣成本) 與總摩擦成本 (手續費 + 期交稅 + 滑價)
        total_gross_profit = sum(t.gross_pnl for t in self.trades)
        total_trading_costs = sum((t.fee_total + t.tax_total + t.slippage_loss) for t in self.trades)

        gross_profit = sum(t.net_pnl for t in self.trades if t.net_pnl > 0)
        gross_loss = abs(sum(t.net_pnl for t in self.trades if t.net_pnl < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        # 計算最大回撤 (Max Drawdown)
        eq_series = pd.Series([item["equity"] for item in self.equity_curve])
        if not eq_series.empty:
            cummax = eq_series.cummax()
            drawdowns = cummax - eq_series
            drawdown_pcts = (drawdowns / cummax) * 100.0
            max_dd = float(drawdowns.max())
            max_dd_pct = float(drawdown_pcts.max())
        else:
            max_dd = 0.0
            max_dd_pct = 0.0

        # 計算夏普比率 (日頻率標準化，以無風險利率 1.5% 估算)
        if len(self.equity_curve) > 1:
            eq_df = pd.DataFrame(self.equity_curve)
            eq_df["ts"] = pd.to_datetime(eq_df["ts"])
            daily_eq = eq_df.set_index("ts")["equity"].resample("D").last().dropna()
            daily_returns = daily_eq.pct_change().dropna()
            if len(daily_returns) > 1 and daily_returns.std() > 0:
                # 252 個交易日年化
                rf_daily = 0.015 / 252.0
                excess_returns = daily_returns - rf_daily
                sharpe = float(np.sqrt(252.0) * excess_returns.mean() / daily_returns.std())
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        return BacktestMetrics(
            initial_capital=round(initial_cap, 2),
            final_equity=round(final_eq, 2),
            total_gross_profit=round(total_gross_profit, 2),
            total_trading_costs=round(total_trading_costs, 2),
            total_net_profit=round(total_net_profit, 2),
            total_return_pct=round(total_return_pct, 2),
            annualized_return_pct=round(annualized_return_pct, 2),
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate_pct=round(win_rate_pct, 2),
            profit_factor=round(profit_factor, 2),
            max_drawdown=round(max_dd, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            sharpe_ratio=round(sharpe, 2),
            trades=self.trades,
            equity_curve=self.equity_curve
        )
