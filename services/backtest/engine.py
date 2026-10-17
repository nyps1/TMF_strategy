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
from .indicators import (
    calculate_bollinger_bands,
    calculate_atr,
    calculate_donchian,
    calculate_supertrend_series,
    calculate_htf_ma_slopes,
)
from .data_loader import get_third_wednesday

logger = logging.getLogger(__name__)


class BaselinePullbackEngine:
    """
    原版基準核心回測引擎 (Pure Baseline Pullback Core Engine — Template Method Base Class)。

    封裝最原始、未加任何跨週期追價濾網之純粹核心規則：
    1. 動態口數與槓桿風控（基準 A 整戶維持率 130% safe_qty 嚴格計算）。
    2. 三根 K 棒狀態機（Bar 1 突破布林上下軌 -> Bar 2 嚴格落於頂部/底部 1/4 拉回區間 -> Bar 3 開盤進場）。
    3. 盤中觸碰停損點觸價出場、收盤 ATR crossunder 動能停利、結算日 13:00 強制平倉。
    4. 提供受保護之鉤子方法 (_prepare_indicators, _check_bar1_breakout, _check_bar2_confirm, _pass_entry_filters)，
       供後續子類別透過繼承 (Inheritance) 疊加方案一或其他過濾通道，確保原版核心 100% 獨立零污染。
    """

    def __init__(self, config: Optional[BacktestConfig] = None) -> None:
        """
        初始化原版基準回測核心引擎。

        參數:
            config (Optional[BacktestConfig]): 回測參數設定。若未提供則使用預設值。
        """
        self.config: BacktestConfig = config or BacktestConfig()
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
        依據當前帳戶淨值、保證金維持率門檻 (基準 A: 130%) 與單口原始保證金，計算安全可下口數。

        參數:
            current_price (float): 當前市場價格。

        返回:
            int: 允許下單之安全整數口數 (若資金不足則回傳 0)。
        """
        if self.equity < self.config.margin_req or current_price <= 0:
            return 0

        if self.config.position_sizing_mode in ("margin_ratio_130", "margin"):
            ratio = max(1.0, float(self.config.min_margin_ratio))
            required_per_lot = self.config.margin_req * ratio
            if self.equity < required_per_lot:
                return 0
            safe_qty = int(math.floor(self.equity / required_per_lot))
            return max(0, safe_qty)

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
        apply_slippage: bool = True,
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

        # 記錄進場時點之帳戶權益以計算動態真實槓桿與整戶維持率
        equity_before = self.equity

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
        effective_leverage = (cost_basis / equity_before) if equity_before > 0 else 0.0
        margin_ratio_pct = (
            (equity_before / (pos.qty * self.config.margin_req)) * 100.0
            if (pos.qty > 0 and self.config.margin_req > 0)
            else 0.0
        )

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
            equity_after=round(self.equity, 2),
            contract_value=round(cost_basis, 2),
            effective_leverage=round(effective_leverage, 2),
            margin_ratio_pct=round(margin_ratio_pct, 2),
        )
        self.trades.append(trade)

        # 重置在手持倉
        self.position = ActivePosition()

    def _prepare_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        準備原版基準核心所需之 5 分 K 基礎指標（布林通道、ATR、唐奇安通道、SuperTrend）。
        子類別可透過 super()._prepare_indicators(df) 繼承並擴充額外的過濾通道指標。

        參數:
            df (pd.DataFrame): 原始 5 分 K 資料表。

        返回:
            pd.DataFrame: 附加基礎技術指標後之 DataFrame。
        """
        df = calculate_bollinger_bands(df, length=self.config.bb_length, mult=self.config.bb_mult)
        df = calculate_atr(df, length=self.config.atr_length, sig_length=self.config.atr_sig_length)
        df = calculate_donchian(df, length=self.config.donchian_length)
        df = calculate_supertrend_series(
            df,
            length=self.config.supertrend_length,
            multiplier=self.config.supertrend_multiplier,
        )
        return df

    def _check_bar1_breakout(
        self,
        i: int,
        cur_open: float,
        cur_high: float,
        cur_low: float,
        cur_close: float,
        bb_upper: float,
        bb_lower: float,
    ) -> None:
        """
        檢核當根 K 棒是否構成「第一根突破訊號棒 (Bar 1)」。
        若成立則寫入 pending_signal_side、signal_bar_close、signal_bar_q1 與 signal_bar_extreme。

        參數:
            i (int): 當前 K 棒索引。
            cur_open (float): 當前開盤價。
            cur_high (float): 當前最高價。
            cur_low (float): 當前最低價。
            cur_close (float): 當前收盤價。
            bb_upper (float): 當前布林通道上軌。
            bb_lower (float): 當前布林通道下軌。
        """
        if np.isnan(bb_upper) or np.isnan(bb_lower):
            return

        ratio = self.config.pullback_ratio
        # 多頭突破: 收盤大於布林上軌且為紅 K
        if cur_close > bb_upper and cur_close > cur_open:
            body = cur_close - cur_open
            self.pending_signal_side = PositionSide.LONG
            self.signal_bar_idx = i
            self.signal_bar_close = cur_close
            self.signal_bar_q1 = cur_close - (body * ratio)
            self.signal_bar_extreme = cur_low  # 記錄第一根最低價作為多單停損

        # 空頭突破: 收盤小於布林下軌且為黑 K
        elif cur_close < bb_lower and cur_close < cur_open:
            body = cur_open - cur_close
            self.pending_signal_side = PositionSide.SHORT
            self.signal_bar_idx = i
            self.signal_bar_close = cur_close
            self.signal_bar_q1 = cur_close + (body * ratio)
            self.signal_bar_extreme = cur_high  # 記錄第一根最高價作為空單停損

    def _check_bar2_confirm(
        self,
        i: int,
        target_side: PositionSide,
        cur_close: float,
    ) -> bool:
        """
        原版基準核心之「第二根確認棒 (Bar 2)」判定規則（僅允許情境一：嚴格淺幅拉回／反彈）。
        - 多頭：既不追高 (cur_close <= signal_bar_close) 且守住頂部 1/4 防守點 (cur_close >= signal_bar_q1)。
        - 空頭：既不追空 (cur_close >= signal_bar_close) 且守住底部 1/4 防守點 (cur_close <= signal_bar_q1)。

        參數:
            i (int): 當前 K 棒索引。
            target_side (PositionSide): 候選訊號方向 (LONG / SHORT)。
            cur_close (float): Bar 2 收盤價。

        返回:
            bool: 若符合原版基準拉回確認則回傳 True，否則回傳 False。
        """
        if target_side == PositionSide.LONG:
            return (cur_close <= self.signal_bar_close) and (cur_close >= self.signal_bar_q1)
        elif target_side == PositionSide.SHORT:
            return (cur_close >= self.signal_bar_close) and (cur_close <= self.signal_bar_q1)
        return False

    def _pass_entry_filters(
        self,
        i: int,
        target_side: PositionSide,
        cur_close: float,
    ) -> bool:
        """
        進場額外過濾通道鉤子方法 (Filter Channel Hook)。
        原版基準核心預設不加任何額外過濾（永遠回傳 True）。
        後續若新增其他過濾通道子類別，可覆寫此方法並呼叫 super()._pass_entry_filters(...)。

        參數:
            i (int): 當前 K 棒索引。
            target_side (PositionSide): 準備進場之方向 (LONG / SHORT)。
            cur_close (float): Bar 2 收盤價。

        返回:
            bool: 若通過所有過濾通道則回傳 True，否則回傳 False。
        """
        return True

    def run(self, df: pd.DataFrame) -> BacktestMetrics:
        """
        執行主回測運算迴圈（Template Method 骨架）。

        參數:
            df (pd.DataFrame): 具備 OHLCV 之 K 線時序 DataFrame。

        返回:
            BacktestMetrics: 完整回測績效指標數據。
        """
        self.reset()

        # 1. 透過多型鉤子方法準備技術指標
        df = self._prepare_indicators(df)

        n_bars = len(df)
        if n_bars < self.config.bb_length + 2:
            raise ValueError(
                f"Status: ERROR | Root Cause: K線長度 ({n_bars}) 小於指標最小計算週期 | Suggested Fix: 提供更長時序資料。"
            )

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
        donchian_highs = df["donchian_high"].to_numpy()
        donchian_lows = df["donchian_low"].to_numpy()
        st_vals = df["supertrend_val"].to_numpy()
        st_dirs = df["supertrend_dir"].to_numpy()

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
                        entry_bar_index=i,
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
                        reason=ExitReason.ROLLOVER_SETTLEMENT,
                    )

                # 檢核 2: 盤中觸碰停損點
                elif pos.side == PositionSide.LONG and cur_low <= pos.sl_price:
                    trigger_price = min(cur_open, pos.sl_price)
                    self._close_position(
                        exit_time=cur_time_str,
                        raw_exit_price=trigger_price,
                        reason=ExitReason.STOP_LOSS,
                    )

                elif pos.side == PositionSide.SHORT and cur_high >= pos.sl_price:
                    trigger_price = max(cur_open, pos.sl_price)
                    self._close_position(
                        exit_time=cur_time_str,
                        raw_exit_price=trigger_price,
                        reason=ExitReason.STOP_LOSS,
                    )

                # 檢核 3: 依設定之 exit_mode 執行停損停利出場
                elif self.config.exit_mode == "donchian":
                    if pos.side == PositionSide.LONG and not np.isnan(donchian_lows[i]) and cur_close < donchian_lows[i]:
                        self._close_position(
                            exit_time=cur_time_str,
                            raw_exit_price=cur_close,
                            reason=ExitReason.DONCHIAN_EXIT,
                        )
                    elif pos.side == PositionSide.SHORT and not np.isnan(donchian_highs[i]) and cur_close > donchian_highs[i]:
                        self._close_position(
                            exit_time=cur_time_str,
                            raw_exit_price=cur_close,
                            reason=ExitReason.DONCHIAN_EXIT,
                        )

                elif self.config.exit_mode == "supertrend":
                    if pos.side == PositionSide.LONG and (st_dirs[i] == -1 or cur_close < st_vals[i]):
                        self._close_position(
                            exit_time=cur_time_str,
                            raw_exit_price=cur_close,
                            reason=ExitReason.SUPERTREND_EXIT,
                        )
                    elif pos.side == PositionSide.SHORT and (st_dirs[i] == 1 or cur_close > st_vals[i]):
                        self._close_position(
                            exit_time=cur_time_str,
                            raw_exit_price=cur_close,
                            reason=ExitReason.SUPERTREND_EXIT,
                        )

                elif self.config.exit_mode == "atr":
                    if (
                        not np.isnan(atr_vals[i])
                        and not np.isnan(atr_sigs[i])
                        and not np.isnan(atr_vals[i - 1])
                        and not np.isnan(atr_sigs[i - 1])
                    ):
                        prev_above = atr_vals[i - 1] >= atr_sigs[i - 1]
                        cur_below = atr_vals[i] < atr_sigs[i]
                        if prev_above and cur_below:
                            self._close_position(
                                exit_time=cur_time_str,
                                raw_exit_price=cur_close,
                                reason=ExitReason.ATR_TAKE_PROFIT,
                            )

            # ----------------------------------------------------
            # 步驟 C: 第二根確認棒與第一根訊號棒判斷 (僅在空手時評估)
            # ----------------------------------------------------
            if self.position.side == PositionSide.NONE and self.entry_ready_side == PositionSide.NONE:
                # 1. 檢核是否有前一根剛成立的候選訊號棒需做「第二根確認」與「過濾通道檢核」
                if self.pending_signal_side != PositionSide.NONE and (i - 1 == self.signal_bar_idx):
                    target_side = self.pending_signal_side
                    if self._check_bar2_confirm(i, target_side, cur_close) and self._pass_entry_filters(
                        i, target_side, cur_close
                    ):
                        self.entry_ready_side = target_side
                        self.entry_sl_price = self.signal_bar_extreme

                    # 第二根評估結束，清除前期待確認狀態
                    self.pending_signal_side = PositionSide.NONE

                # 2. 檢核當根是否構成「第一根訊號棒」
                self._check_bar1_breakout(
                    i=i,
                    cur_open=cur_open,
                    cur_high=cur_high,
                    cur_low=cur_low,
                    cur_close=cur_close,
                    bb_upper=bb_uppers[i],
                    bb_lower=bb_lowers[i],
                )

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

            self.equity_curve.append(
                {
                    "ts": cur_time_str,
                    "equity": round(current_equity, 2),
                    "close": cur_close,
                    "in_position": self.position.side != PositionSide.NONE,
                }
            )

        # 回測結束時若仍有部位，強制市價平倉以利指標統計
        if self.position.side != PositionSide.NONE:
            last_ts = str(timestamps[-1])
            self._close_position(
                exit_time=last_ts,
                raw_exit_price=closes[-1],
                reason=ExitReason.ROLLOVER_SETTLEMENT,
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
            equity_curve=self.equity_curve,
        )


class HtfMaContinuationEngine(BaselinePullbackEngine):
    """
    方案一回測引擎 (Plan 1 HTF MA Continuation Engine — 繼承自原版基準核心 BaselinePullbackEngine)。

    透過繼承原版基準核心 (BaselinePullbackEngine)：
    1. 覆寫 _prepare_indicators：先呼叫 super()._prepare_indicators(df)，再掛載無偷看未來之 30m 與 1h 20MA 斜率序列。
    2. 覆寫 _check_bar2_confirm：
       - 第一步呼叫 super()._check_bar2_confirm(...)，完全保留原版情境一（淺幅拉回）確認邏輯。
       - 第二步若啟用 enable_htf_continuation，開放情境三（Bar 2 繼續創高/破底）並以 30m & 1h 均線雙重同向斜率過濾。
    """

    def __init__(self, config: Optional[BacktestConfig] = None) -> None:
        """
        初始化方案一跨週期均線動能延續回測引擎。

        參數:
            config (Optional[BacktestConfig]): 回測參數設定。
        """
        super().__init__(config)
        self._htf_30m_slopes: np.ndarray = np.array([], dtype=float)
        self._htf_1h_slopes: np.ndarray = np.array([], dtype=float)

    def _prepare_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        繼承原版基準核心指標，並新增 30m 與 1h 大週期均線斜率指標。

        參數:
            df (pd.DataFrame): 原始 5 分 K 資料表。

        返回:
            pd.DataFrame: 附加基礎指標與跨週期均線斜率之 DataFrame。
        """
        df = super()._prepare_indicators(df)
        df = calculate_htf_ma_slopes(df, ma_length=self.config.htf_ma_length)
        self._htf_30m_slopes = df["htf_30m_slope"].to_numpy()
        self._htf_1h_slopes = df["htf_1h_slope"].to_numpy()
        return df

    def _check_bar2_confirm(
        self,
        i: int,
        target_side: PositionSide,
        cur_close: float,
    ) -> bool:
        """
        方案一之 Bar 2 確認邏輯：
        1. 先委派父類別 (BaselinePullbackEngine) 檢核原版情境一（標準淺幅拉回）。
        2. 若不屬於情境一且啟用 enable_htf_continuation，則檢核情境三（Bar 2 動能延續 + 30m & 1h 均線雙重同向）。

        參數:
            i (int): 當前 K 棒索引。
            target_side (PositionSide): 候選訊號方向 (LONG / SHORT)。
            cur_close (float): Bar 2 收盤價。

        返回:
            bool: 若符合原版拉回或方案一跨週期均線延續則回傳 True，否則回傳 False。
        """
        # 1. 優先檢核父類別（原版基準核心：情境一標準拉回）
        if super()._check_bar2_confirm(i, target_side, cur_close):
            return True

        # 2. 若未啟用方案一動能延續，直接回傳 False
        if not self.config.enable_htf_continuation:
            return False

        # 3. 檢核方案一：情境三動能延續 + 30m & 1h 20MA 同向斜率過濾
        s30 = float(self._htf_30m_slopes[i]) if i < len(self._htf_30m_slopes) else 0.0
        s1h = float(self._htf_1h_slopes[i]) if i < len(self._htf_1h_slopes) else 0.0

        if target_side == PositionSide.LONG:
            return (cur_close > self.signal_bar_close) and (s30 > 0.0) and (s1h > 0.0)
        elif target_side == PositionSide.SHORT:
            return (cur_close < self.signal_bar_close) and (s30 < 0.0) and (s1h < 0.0)
        return False


class BacktestEngine(HtfMaContinuationEngine):
    """
    系統預設回測引擎入口類別 (繼承自 HtfMaContinuationEngine，向下相容所有既有呼叫端)。
    - 若需單獨執行或繼承最原始純拉回核心，請直接使用或繼承 BaselinePullbackEngine。
    - 若需在方案一基礎上疊加新過濾通道，可繼承 HtfMaContinuationEngine 或 BaselinePullbackEngine 並覆寫 _pass_entry_filters。
    """

    pass
