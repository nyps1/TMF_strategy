"""
績效報表與視覺化圖表生成模組 (services/backtest/reporter.py)

負責產出專業量化回測報表：
1. 終端機格式化 ASCII 績效指標摘要。
2. 匯出交易明細至 CSV 檔案。
3. 使用 Plotly 生成具備雙圖層的互動式 HTML 報表（權益曲線、水下回撤圖與進出場標記點位）。

遵循 PEP 8 與 Python Type Hints 規範。
"""

import os
import logging
from typing import Optional, Dict, Any
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .models import BacktestMetrics, TradeRecord

logger = logging.getLogger(__name__)


class BacktestReporter:
    """
    回測報告器，負責指標格式化印出、明細匯出與互動圖表生成。
    """
    def __init__(self, metrics: BacktestMetrics):
        """
        初始化報告器。

        參數:
            metrics (BacktestMetrics): 回測引擎結算產出之績效指標模型。
        """
        self.metrics = metrics

    def print_terminal_summary(self) -> None:
        """在終端機印出結構清楚、格式工整之回測績效摘要表格。"""
        m = self.metrics
        print("\n" + "=" * 55)
        print("  微台動能布林突破策略 (五分K) - 真實成本版成果摘要")
        print("=" * 55)
        print(f"  期初保證金資金 (NTD)   : {m.initial_capital:>15,}")
        print(f"  期末帳戶總權益 (NTD)   : {m.final_equity:>15,}")
        print(f"  策略毛利 (未扣成本 NTD): {m.total_gross_profit:>15,}")
        print(f"  總手續費與期交稅 (NTD) : {m.total_trading_costs:>15,}")
        print(f"  真實淨利 (Real Net NTD): {m.total_net_profit:>15,}")
        print(f"  總投資報酬率 (Total ROI): {m.total_return_pct:>14.2f}%")
        print(f"  年化複合報酬率 (CAGR)  : {m.annualized_return_pct:>14.2f}%")
        print("-" * 55)
        print(f"  總執行交易次數         : {m.total_trades:>15}")
        print(f"  獲利交易筆數 (勝筆)    : {m.winning_trades:>15}")
        print(f"  虧損交易筆數 (敗筆)    : {m.losing_trades:>15}")
        print(f"  策略勝率 (Win Rate)    : {m.win_rate_pct:>14.2f}%")
        print(f"  賺賠比 (Profit Factor) : {m.profit_factor:>15.2f}")
        print(f"  最大權益回撤 (MDD NTD) : {m.max_drawdown:>15,}")
        print(f"  最大權益回撤比率 (%)   : {m.max_drawdown_pct:>14.2f}%")
        print(f"  夏普比率 (Sharpe Ratio): {m.sharpe_ratio:>15.2f}")
        print("=" * 55 + "\n")

    def export_trades_csv(self, file_path: str = "trades.csv") -> None:
        """
        將每筆交易紀錄明細輸出至指定之 CSV 檔案。

        參數:
            file_path (str): 輸出目標 CSV 檔案路徑。
        """
        if not self.metrics.trades:
            logger.info("無任何交易紀錄可供匯出。")
            return

        trade_dicts = [t.model_dump() for t in self.metrics.trades]
        df = pd.DataFrame(trade_dicts)
        df.to_csv(file_path, index=False, encoding="utf-8-sig")
        logger.info(f"交易明細已成功匯出至: {file_path}")

    def generate_html_chart(self, output_path: str = "backtest_report.html") -> str:
        """
        使用 Plotly 生成互動式 HTML 圖表，包含：
        1. 累積權益曲線 (Equity Curve)。
        2. 水下權益回撤圖 (Underwater Drawdown %)。
        3. 買進與賣出標記點位。

        參數:
            output_path (str): HTML 報告檔案輸出路徑。

        返回:
            str: 產出之 HTML 檔案絕對路徑。
        """
        if not self.metrics.equity_curve:
            logger.warning("無權益時序數據，略過圖表生成。")
            return ""

        eq_df = pd.DataFrame(self.metrics.equity_curve)
        eq_df["ts"] = pd.to_datetime(eq_df["ts"])
        eq_df["cummax"] = eq_df["equity"].cummax()
        eq_df["drawdown_pct"] = ((eq_df["equity"] - eq_df["cummax"]) / eq_df["cummax"]) * 100.0

        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            subplot_titles=("帳戶權益走勢曲線 (Equity Curve)", "水下權益回撤百分比 (Drawdown %)"),
            row_heights=[0.7, 0.3]
        )

        # 1. 權益主圖
        fig.add_trace(
            go.Scatter(
                x=eq_df["ts"],
                y=eq_df["equity"],
                mode="lines",
                name="帳戶總淨值",
                line=dict(color="#1f77b4", width=2)
            ),
            row=1,
            col=1
        )

        # 標記進場點位 (多單 / 空單)
        if self.metrics.trades:
            long_entries = [t for t in self.metrics.trades if t.side.value == "LONG"]
            short_entries = [t for t in self.metrics.trades if t.side.value == "SHORT"]

            if long_entries:
                fig.add_trace(
                    go.Scatter(
                        x=[pd.to_datetime(t.entry_time) for t in long_entries],
                        y=[t.equity_after for t in long_entries],
                        mode="markers",
                        name="多單進場",
                        marker=dict(symbol="triangle-up", color="#2ca02c", size=9)
                    ),
                    row=1,
                    col=1
                )

            if short_entries:
                fig.add_trace(
                    go.Scatter(
                        x=[pd.to_datetime(t.entry_time) for t in short_entries],
                        y=[t.equity_after for t in short_entries],
                        mode="markers",
                        name="空單進場",
                        marker=dict(symbol="triangle-down", color="#d62728", size=9)
                    ),
                    row=1,
                    col=1
                )

        # 2. 水下回撤圖
        fig.add_trace(
            go.Scatter(
                x=eq_df["ts"],
                y=eq_df["drawdown_pct"],
                mode="lines",
                name="回撤幅度 (%)",
                line=dict(color="#d62728", width=1.5),
                fill="tozeroy",
                fillcolor="rgba(214, 39, 40, 0.2)"
            ),
            row=2,
            col=1
        )

        # 儀表板樣式最佳化 (真實成本版 Dashboard)
        pnl_color = "#2ca02c" if self.metrics.total_net_profit >= 0 else "#d62728"
        fig.update_layout(
            title=dict(
                text=(
                    "微台動能布林突破策略 (五分K) - 真實成本版回測報告<br>"
                    f"<span style='font-size:13px;color:#555;'>策略毛利 (未扣成本): <b>{self.metrics.total_gross_profit:,.0f} 元</b> | "
                    f"總手續費與稅金: <b>{self.metrics.total_trading_costs:,.0f} 元</b> | "
                    f"真實淨利 (台幣): <b style='color:{pnl_color}'>{self.metrics.total_net_profit:,.0f} 元</b> "
                    f"(報酬率: {self.metrics.total_return_pct:.2f}% | 勝率: {self.metrics.win_rate_pct:.1f}% | 賺賠比: {self.metrics.profit_factor:.2f})</span>"
                ),
                x=0.5,
                xanchor="center"
            ),
            template="plotly_white",
            hovermode="x unified",
            height=700,
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        fig.update_yaxes(title_text="淨值 (NTD)", row=1, col=1)
        fig.update_yaxes(title_text="回撤幅度 (%)", row=2, col=1)
        fig.update_xaxes(title_text="交易時間", row=2, col=1)

        fig.write_html(output_path)
        logger.info(f"互動式 HTML 圖表已儲存至: {output_path}")
        return os.path.abspath(output_path)
