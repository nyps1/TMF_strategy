"""
K 線與策略回測互動網頁儀表板生成器 (generate_kline_dashboard.py)

從 services/storage/kline_service.py 讀取微台10在 SQLite 中的高頻時序資料，
聚合 11 種時間週期，並調用 services/backtest/engine.py 執行真實成本版動態布林突破策略，
產出自給自足（Self-contained）、支援離線開啟與 TradingView 暗黑配色的互動式雙圖儀表板：
1. 上圖：高頻 K 線燭柱圖、成交量柱狀圖、以及精準標記之多空建倉、停損、停利交易信號 (Markers)。
2. 下圖：100% 擬真 TradingView 策略回測關鍵數據面板與 0 軸分界紅綠漸層累計損益走勢圖 (BaselineSeries)。
3. 雙圖時間軸與十字準星雙向即時同步連動。

遵循 PEP 8 與 Python Type Hints 規範。
"""

import os
import json
import logging
from typing import Dict, Any, List, Tuple
import pandas as pd

from services.storage import KlineService
from services.backtest import BacktestEngine, BacktestConfig, BacktestMetrics, PositionSide, ExitReason, BacktestReporter

logger = logging.getLogger("DashboardGenerator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def read_standalone_lightweight_charts() -> str:
    """
    讀取本機已安裝之 Lightweight Charts 獨立生產版本 JS 腳本。
    若檔案不存在則回傳空字串，前端將自動降級至官方 CDN。

    返回:
        str: Lightweight Charts JS 程式碼字串。
    """
    local_path = os.path.join(
        os.path.dirname(__file__),
        "frontend",
        "node_modules",
        "lightweight-charts",
        "dist",
        "lightweight-charts.standalone.production.js"
    )
    if os.path.exists(local_path):
        with open(local_path, "r", encoding="utf-8") as f:
            logger.info("已成功載入本機 Lightweight Charts 獨立函式庫。")
            return f.read()
    return ""


def run_strategy_backtest(bars_5m: List[Dict[str, Any]]) -> Tuple[BacktestMetrics, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    針對 5 分 K 線時序資料執行真實成本版回測運算，
    產出交易標記 (Markers) 與累計損益曲線 (Cumulative PnL Curve)。

    參數:
        bars_5m (List[Dict[str, Any]]): 5 分鐘 K 線資料列表。

    返回:
        Tuple[BacktestMetrics, List[Dict[str, Any]], List[Dict[str, Any]]]:
            - 回測評估指標物件。
            - 交易標記資料列表 (供主圖繪製建倉與平倉點位)。
            - 累計損益時序資料列表 (供副圖繪製紅綠 Baseline 曲線)。
    """
    logger.info("正在執行策略回測以萃取交易信號與權益時序...")
    df = pd.DataFrame(bars_5m)
    df["ts"] = pd.to_datetime(df["datetime"])

    config = BacktestConfig(
        initial_capital=105150.0,
        point_value=10.0,
        margin_req=35050.0,
        max_leverage=7.0,
        bb_length=30,
        bb_mult=1.75,
        atr_length=14,
        atr_sig_length=5,
        commission_per_order=18.0,
        tax_rate=0.00002,
        slippage_points=0.0
    )

    engine = BacktestEngine(config=config)
    metrics = engine.run(df)

    # 1. 建立建倉與平倉 Markers
    markers: List[Dict[str, Any]] = []
    for trade in metrics.trades:
        # 進場建倉標記
        entry_ts = int(pd.Timestamp(trade.entry_time).timestamp())
        is_long = trade.side == PositionSide.LONG
        markers.append({
            "rawTime": entry_ts,
            "time": entry_ts,
            "position": "belowBar" if is_long else "aboveBar",
            "color": "#089981" if is_long else "#f23645",
            "shape": "arrowUp" if is_long else "arrowDown",
            "text": f"買進 {trade.qty}口" if is_long else f"放空 {trade.qty}口",
            "tradeId": trade.trade_id,
            "type": "entry",
            "side": trade.side.value,
            "price": float(trade.entry_price),
            "qty": trade.qty,
            "timeStr": trade.entry_time
        })

        # 出場平倉標記
        exit_ts = int(pd.Timestamp(trade.exit_time).timestamp())
        is_profit = trade.net_pnl >= 0
        pnl_str = f"+{trade.net_pnl:,.0f}" if is_profit else f"{trade.net_pnl:,.0f}"

        if trade.exit_reason == ExitReason.STOP_LOSS:
            reason_label = "停損"
            marker_color = "#f23645"
        elif trade.exit_reason == ExitReason.ATR_TAKE_PROFIT:
            reason_label = "停利"
            marker_color = "#089981"
        else:
            reason_label = "結算"
            marker_color = "#f0b90b"

        markers.append({
            "rawTime": exit_ts,
            "time": exit_ts,
            "position": "aboveBar" if is_long else "belowBar",
            "color": marker_color,
            "shape": "circle",
            "text": f"{reason_label} ({pnl_str})",
            "tradeId": trade.trade_id,
            "type": "exit",
            "side": trade.side.value,
            "price": float(trade.exit_price),
            "qty": trade.qty,
            "pnl": float(trade.net_pnl),
            "reason": trade.exit_reason.value,
            "timeStr": trade.exit_time
        })

    # 嚴格按時間升序排序，同一時間進場優先於出場
    markers.sort(key=lambda m: (m["rawTime"], 0 if m["type"] == "entry" else 1))

    # 2. 建立累計損益曲線 (相對於期初資金之淨盈虧)
    cumulative_pnl_curve: List[Dict[str, Any]] = []
    initial_cap = config.initial_capital

    for pt in metrics.equity_curve:
        ts_sec = int(pd.Timestamp(pt["ts"]).timestamp())
        cum_pnl = round(pt["equity"] - initial_cap, 2)
        cumulative_pnl_curve.append({
            "time": ts_sec,
            "value": cum_pnl,
            "equity": round(pt["equity"], 2),
            "close": pt["close"],
            "in_position": pt["in_position"]
        })

    logger.info(
        f"回測完成: 總筆數 {metrics.total_trades}, 淨損益 {metrics.total_net_profit:,.0f} 元, "
        f"勝率 {metrics.win_rate_pct:.2f}%, 獲利因子 {metrics.profit_factor:.3f}, Markers 共 {len(markers)} 個。"
    )

    try:
        reporter = BacktestReporter(metrics)
        reporter.export_trades_csv("trades.csv")
    except Exception as e:
        logger.warning(f"匯出 trades.csv 警告 (檔案可能被外部開啟): {e}")

    return metrics, markers, cumulative_pnl_curve


def build_html_content(
    intervals_data: Dict[str, List[Dict[str, Any]]],
    markers: List[Dict[str, Any]],
    cumulative_pnl: List[Dict[str, Any]],
    metrics: BacktestMetrics,
    standalone_js: str
) -> str:
    """
    建構符合 TradingView 暗黑配色、具備 K 線主圖、交易 Markers、關鍵數據卡片與紅綠 Baseline 走勢圖的 HTML 網頁。

    參數:
        intervals_data (Dict[str, List[Dict[str, Any]]]): 11 種週期的 K 線資料字典。
        markers (List[Dict[str, Any]]): 交易建倉與平倉標記列表。
        cumulative_pnl (List[Dict[str, Any]]): 累計損益時序曲線列表。
        metrics (BacktestMetrics): 回測績效指標數據。
        standalone_js (str): 內嵌 Lightweight Charts 函式庫程式碼 (可為空)。

    返回:
        str: 完整 HTML 頁面內容。
    """
    data_json = json.dumps(intervals_data, ensure_ascii=False)
    markers_json = json.dumps(markers, ensure_ascii=False)
    pnl_json = json.dumps(cumulative_pnl, ensure_ascii=False)

    # 序列化 64 筆交易完整明細 (與 trades.csv 100% 欄位對齊，並注入累計損益 cum_pnl)
    trades_list = []
    running_cum_pnl = 0.0
    for t in metrics.trades:
        running_cum_pnl += t.net_pnl
        trades_list.append({
            "trade_id": t.trade_id,
            "side": t.side.value,
            "entry_time": t.entry_time,
            "entry_price": float(t.entry_price),
            "exit_time": t.exit_time,
            "exit_price": float(t.exit_price),
            "qty": t.qty,
            "exit_reason": "停利" if t.exit_reason.value == "ATR_TAKE_PROFIT" else ("停損" if t.exit_reason.value == "STOP_LOSS" else "結算"),
            "costs": float(t.fee_total + t.tax_total),
            "net_pnl": float(t.net_pnl),
            "cum_pnl": round(running_cum_pnl, 2),
            "return_pct": float(t.return_pct),
            "equity_after": float(t.equity_after),
            "entry_ts": int(pd.Timestamp(t.entry_time).timestamp()),
            "exit_ts": int(pd.Timestamp(t.exit_time).timestamp())
        })
    trades_json = json.dumps(trades_list, ensure_ascii=False)

    # 格式化統計指標供前端直接呈現
    net_pnl_sign = "+" if metrics.total_net_profit >= 0 else ""
    return_sign = "+" if metrics.total_return_pct >= 0 else ""
    metrics_dict = {
        "totalNetProfitStr": f"{net_pnl_sign}{metrics.total_net_profit:,.2f}",
        "totalReturnPctStr": f"{return_sign}{metrics.total_return_pct:.2f}%",
        "isProfit": metrics.total_net_profit >= 0,
        "maxDrawdownStr": f"{metrics.max_drawdown:,.2f}",
        "maxDrawdownPctStr": f"{metrics.max_drawdown_pct:.2f}%",
        "winRateStr": f"{metrics.win_rate_pct:.2f}%",
        "tradesRatioStr": f"{metrics.winning_trades}/{metrics.total_trades}",
        "profitFactorStr": f"{metrics.profit_factor:.3f}",
        "initialCapitalStr": f"{metrics.initial_capital:,.0f}",
        "finalEquityStr": f"{metrics.final_equity:,.2f}"
    }
    metrics_json = json.dumps(metrics_dict, ensure_ascii=False)

    script_tag = f"<script>{standalone_js}</script>" if standalone_js else (
        '<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>'
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>微台10 (TMFJ6) 策略回測與多週期 K 線儀表板 - 永豐 Shioaji</title>
    {script_tag}
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            background-color: #0f0f11;
            color: #d1d4dc;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            overflow: hidden;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }}

        /* 頂部控制列 */
        .top-toolbar {{
            height: 48px;
            background-color: #141417;
            border-bottom: 1px solid #232733;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 16px;
            user-select: none;
            z-index: 10;
            flex-shrink: 0;
        }}
        .symbol-badge {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .symbol-badge .symbol-name {{
            font-size: 15px;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: 0.5px;
        }}
        .symbol-badge .symbol-code {{
            background: #242735;
            color: #089981;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }}
        .symbol-badge .strategy-tag {{
            background: rgba(41, 98, 255, 0.15);
            color: #2962ff;
            border: 1px solid rgba(41, 98, 255, 0.3);
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }}
        .timeframe-group {{
            display: flex;
            align-items: center;
            gap: 3px;
            background: #191b24;
            padding: 3px;
            border-radius: 6px;
            border: 1px solid #262a38;
        }}
        .tf-divider {{
            width: 1px;
            height: 18px;
            background-color: #33384a;
            margin: 0 4px;
        }}
        .tf-btn {{
            background: transparent;
            border: none;
            color: #9299a8;
            font-size: 12px;
            font-weight: 600;
            padding: 4px 8px;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s ease-in-out;
        }}
        .tf-btn:hover {{
            color: #ffffff;
            background: #282c3c;
        }}
        .tf-btn.active {{
            background: #089981;
            color: #ffffff;
            box-shadow: 0 0 8px rgba(8, 153, 129, 0.4);
        }}
        .actions-group {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .toggle-btn {{
            background: #1e2230;
            border: 1px solid #2e3448;
            color: #d1d4dc;
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 600;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .toggle-btn:hover {{
            background: #2a3044;
            color: #ffffff;
        }}
        .toggle-btn.active {{
            border-color: #089981;
            color: #089981;
            background: rgba(8, 153, 129, 0.12);
        }}
        .reset-btn {{
            background: #1e2230;
            border: 1px solid #2e3448;
            color: #d1d4dc;
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 600;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .reset-btn:hover {{
            background: #2a3044;
            color: #ffffff;
        }}

        /* 雙主窗格切換分層 */
        .workspace-split {{
            flex: 1;
            display: flex;
            flex-direction: column;
            width: 100%;
            height: calc(100vh - 48px);
            overflow: hidden;
        }}

        /* 上半部：K 線圖容器 (佔約 58%) */
        .kline-section {{
            flex: 58;
            position: relative;
            width: 100%;
            background: #0f0f11;
            border-bottom: 1px solid #232733;
            min-height: 240px;
        }}
        #kline-container {{
            width: 100%;
            height: 100%;
        }}

        /* 左上角即時 OHLC 數據懸浮標籤 */
        .ohlc-legend {{
            position: absolute;
            top: 10px;
            left: 14px;
            z-index: 5;
            pointer-events: none;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 12px;
            font-size: 12px;
            font-weight: 500;
            background: rgba(15, 15, 17, 0.82);
            padding: 5px 12px;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            backdrop-filter: blur(4px);
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        .legend-label {{
            color: #787b86;
        }}
        .legend-value {{
            font-weight: 600;
            color: #ffffff;
        }}
        .legend-value.up {{
            color: #089981 !important;
        }}
        .legend-value.down {{
            color: #f23645 !important;
        }}
        .legend-time {{
            color: #9299a8;
        }}
        .trade-info-tip {{
            color: #f0b90b;
            font-weight: 600;
            display: none;
        }}

        /* 下半部：策略績效全覽面板 (完全擬真 TradingView 截圖樣式) */
        .performance-section {{
            flex: 42;
            display: flex;
            flex-direction: column;
            background: #0d0e12;
            width: 100%;
            min-height: 200px;
            position: relative;
        }}

        /* 1. 關鍵數據頂部標題與四欄指標 (Key Metrics Header) */
        .metrics-card {{
            padding: 10px 18px 8px 18px;
            background: #111318;
            border-bottom: 1px solid #1e222c;
            flex-shrink: 0;
        }}
        .metrics-title {{
            font-size: 14px;
            font-weight: 700;
            color: #ffffff;
            margin-bottom: 8px;
            letter-spacing: 0.3px;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
        }}
        .metric-col {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}
        .metric-label {{
            font-size: 11px;
            color: #787b86;
            font-weight: 500;
        }}
        .metric-main-value {{
            font-size: 16px;
            font-weight: 700;
            display: flex;
            align-items: baseline;
            gap: 6px;
        }}
        .metric-main-value .currency {{
            font-size: 11px;
            font-weight: 600;
            color: #787b86;
        }}
        .metric-main-value .pct {{
            font-size: 13px;
            font-weight: 600;
        }}
        .metric-main-value.positive {{
            color: #089981;
        }}
        .metric-main-value.positive .pct {{
            color: #089981;
        }}
        .metric-main-value.negative {{
            color: #f23645;
        }}
        .metric-main-value.negative .pct {{
            color: #f23645;
        }}
        .metric-main-value.neutral {{
            color: #ffffff;
        }}
        .metric-main-value .sub-ratio {{
            font-size: 13px;
            color: #d1d4dc;
            font-weight: 600;
        }}

        /* 2. 績效圖表頂部功能切換列 (Performance Sub-Toolbar) */
        .perf-header-bar {{
            height: 38px;
            background: #111318;
            border-bottom: 1px solid #1a1e28;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 18px;
            flex-shrink: 0;
            user-select: none;
        }}
        .perf-title-group {{
            display: flex;
            align-items: center;
            gap: 16px;
        }}
        .perf-title {{
            font-size: 13px;
            font-weight: 700;
            color: #ffffff;
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        .perf-title-info {{
            font-size: 12px;
            color: #787b86;
            cursor: pointer;
        }}
        .perf-tab-list {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .perf-tab {{
            font-size: 12px;
            font-weight: 500;
            color: #787b86;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 4px;
            transition: color 0.15s;
        }}
        .perf-tab:hover {{
            color: #d1d4dc;
        }}
        .perf-tab.active {{
            color: #ffffff;
            font-weight: 700;
        }}
        .perf-tab-icon {{
            font-size: 11px;
            color: #555a68;
        }}
        .perf-tools {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .perf-tool-btn {{
            background: transparent;
            border: none;
            color: #787b86;
            cursor: pointer;
            font-size: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 2px 4px;
            border-radius: 4px;
            transition: all 0.15s;
        }}
        .perf-tool-btn:hover {{
            color: #ffffff;
            background: #1f2330;
        }}

        /* 3. 新增：固定 Y 軸(交易次數) × X 軸(損益/淨值) 圖表容器樣式 */
        .trade-equity-container-wrapper {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            background: #0d0e12;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        .trade-chart-header-info {{
            height: 28px;
            background: #111318;
            border-bottom: 1px solid #1a1e28;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 16px;
            font-size: 11px;
            flex-shrink: 0;
            user-select: none;
        }}
        .trade-chart-header-info .info-tag {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .trade-chart-header-info .mode-tag {{
            color: #2962ff;
            font-weight: 600;
        }}
        .trade-chart-header-info .tip-tag {{
            color: #787b86;
        }}
        .canvas-scroll-viewport {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            overflow: auto;
            background: #0d0e12;
        }}
        #trade-equity-canvas {{
            display: block;
            cursor: crosshair;
        }}
        .trade-tooltip {{
            position: absolute;
            z-index: 50;
            pointer-events: none;
            background: rgba(17, 19, 24, 0.95);
            border: 1px solid #2e3448;
            border-radius: 6px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
            padding: 10px 14px;
            font-size: 11px;
            line-height: 1.5;
            color: #d1d4dc;
            display: none;
            white-space: nowrap;
            backdrop-filter: blur(4px);
        }}
        .trade-tooltip .tt-title {{
            font-weight: 700;
            font-size: 12px;
            color: #ffffff;
            margin-bottom: 4px;
            padding-bottom: 4px;
            border-bottom: 1px solid #242838;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }}
        .trade-tooltip .tt-row {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            margin: 2px 0;
        }}
        .trade-tooltip .tt-label {{
            color: #787b86;
        }}
        .trade-tooltip .tt-val {{
            font-weight: 600;
        }}
        .trade-tooltip .tt-val.win {{
            color: #089981;
        }}
        .trade-tooltip .tt-val.loss {{
            color: #f23645;
        }}
        .trade-tooltip .tt-footer {{
            margin-top: 6px;
            padding-top: 4px;
            border-top: 1px dashed #242838;
            color: #2962ff;
            font-size: 10px;
        }}

        /* 4. 原累計損益圖表容器 (時間軸版) */
        .perf-chart-container-wrapper {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            background: #0d0e12;
            display: none;
        }}
        #perf-chart-container {{
            width: 100%;
            height: 100%;
        }}

        /* 累計損益即時 Hover 懸浮數據 */
        .perf-legend {{
            position: absolute;
            top: 8px;
            left: 18px;
            z-index: 5;
            pointer-events: none;
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 12px;
            background: rgba(13, 14, 18, 0.85);
            padding: 4px 10px;
            border-radius: 4px;
            border: 1px solid rgba(255, 255, 255, 0.06);
        }}
        .perf-legend-val {{
            font-weight: 700;
        }}

        /* 4. 底部盈虧區段微型彩條 (Profit/Loss Ribbon) */
        .pnl-ribbon-bar {{
            height: 5px;
            width: 100%;
            display: flex;
            background: #111318;
            flex-shrink: 0;
            overflow: hidden;
        }}
        .ribbon-segment {{
            height: 100%;
        }}
        .ribbon-green {{
            background-color: #089981;
        }}
        .ribbon-red {{
            background-color: #f23645;
        }}

        /* 5. 交易明細清單表格樣式 (與 trades.csv 100% 逐行完全對齊) */
        .trades-table-container-wrapper {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            overflow-y: auto;
            background: #0d0e12;
            display: none;
        }}
        .trades-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: #d1d4dc;
        }}
        .trades-table th {{
            position: sticky;
            top: 0;
            background: #141720;
            color: #787b86;
            font-weight: 600;
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid #232733;
            z-index: 2;
            white-space: nowrap;
        }}
        .trades-table td {{
            padding: 7px 12px;
            border-bottom: 1px solid #161922;
            white-space: nowrap;
        }}
        .trades-table tr:hover {{
            background: #181c28;
            cursor: pointer;
        }}
        .badge-side-long {{
            background: rgba(8, 153, 129, 0.18);
            color: #089981;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: 700;
        }}
        .badge-side-short {{
            background: rgba(242, 54, 69, 0.18);
            color: #f23645;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: 700;
        }}
        .pnl-positive {{
            color: #089981;
            font-weight: 700;
        }}
        .pnl-negative {{
            color: #f23645;
            font-weight: 700;
        }}
        .btn-focus-trade {{
            background: #1e2230;
            color: #d1d4dc;
            border: 1px solid #2e3448;
            padding: 2px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 11px;
            transition: all 0.15s;
        }}
        .btn-focus-trade:hover {{
            background: #089981;
            color: #ffffff;
            border-color: #089981;
        }}

        /* 浮水印 */
        .watermark {{
            position: absolute;
            bottom: 24px;
            left: 20px;
            font-size: 24px;
            font-weight: 800;
            color: rgba(255, 255, 255, 0.025);
            user-select: none;
            pointer-events: none;
            letter-spacing: 2px;
        }}
    </style>
</head>
<body>

    <!-- 頂部控制列 -->
    <div class="top-toolbar">
        <div class="symbol-badge">
            <span class="symbol-name">微型台指期 10</span>
            <span class="symbol-code">TMFJ6</span>
            <span class="strategy-tag">動態布林突破策略 (嚴格拉回確認版)</span>
        </div>

        <!-- 週期切換按鈕群組 (1,2,3,5,15,30,45分 / 1,2,4小時 / 1天) -->
        <div class="timeframe-group">
            <button class="tf-btn" data-interval="1m">1分</button>
            <button class="tf-btn" data-interval="2m">2分</button>
            <button class="tf-btn" data-interval="3m">3分</button>
            <button class="tf-btn active" data-interval="5m">5分</button>
            <button class="tf-btn" data-interval="15m">15分</button>
            <button class="tf-btn" data-interval="30m">30分</button>
            <button class="tf-btn" data-interval="45m">45分</button>
            <div class="tf-divider"></div>
            <button class="tf-btn" data-interval="1h">1小時</button>
            <button class="tf-btn" data-interval="2h">2小時</button>
            <button class="tf-btn" data-interval="4h">4小時</button>
            <div class="tf-divider"></div>
            <button class="tf-btn" data-interval="1d">1日</button>
        </div>

        <div class="actions-group">
            <button class="toggle-btn active" id="toggle-markers-btn">
                <span>📍 交易標記: 顯示</span>
            </button>
            <button class="reset-btn" id="reset-zoom-btn">適配畫面 (Fit)</button>
        </div>
    </div>

    <!-- 雙窗格主工作區 -->
    <div class="workspace-split">

        <!-- 上圖：K 線圖主圖 (包含進出場點位標記) -->
        <div class="kline-section">
            <div class="ohlc-legend" id="ohlc-legend">
                <div class="legend-item legend-time" id="legend-time">--</div>
                <div class="legend-item"><span class="legend-label">開</span><span class="legend-value" id="legend-open">--</span></div>
                <div class="legend-item"><span class="legend-label">高</span><span class="legend-value" id="legend-high">--</span></div>
                <div class="legend-item"><span class="legend-label">低</span><span class="legend-value" id="legend-low">--</span></div>
                <div class="legend-item"><span class="legend-label">收</span><span class="legend-value" id="legend-close">--</span></div>
                <div class="legend-item"><span class="legend-label">漲跌</span><span class="legend-value" id="legend-change">--</span></div>
                <div class="legend-item"><span class="legend-label">量</span><span class="legend-value" id="legend-volume">--</span></div>
                <div class="legend-item trade-info-tip" id="legend-trade-info"></div>
            </div>

            <div id="kline-container"></div>
            <div class="watermark">TMF202610 • STRATEGY TESTER</div>
        </div>

        <!-- 下圖：策略績效全覽 (關鍵數據 + 紅綠 Baseline 走勢圖) -->
        <div class="performance-section">

            <!-- 關鍵數據卡片 -->
            <div class="metrics-card">
                <div class="metrics-title">關鍵數據</div>
                <div class="metrics-grid">
                    <!-- 總損益 -->
                    <div class="metric-col">
                        <div class="metric-label">總損益</div>
                        <div class="metric-main-value" id="metric-total-pnl">
                            <span id="metric-pnl-val">--</span>
                            <span class="currency">TWD</span>
                            <span class="pct" id="metric-return-pct">--</span>
                        </div>
                    </div>

                    <!-- 最大回撤 -->
                    <div class="metric-col">
                        <div class="metric-label">最大回撤</div>
                        <div class="metric-main-value neutral">
                            <span id="metric-mdd-val">--</span>
                            <span class="currency">TWD</span>
                            <span class="pct" id="metric-mdd-pct" style="color:#d1d4dc;">--</span>
                        </div>
                    </div>

                    <!-- 獲利交易 -->
                    <div class="metric-col">
                        <div class="metric-label">獲利交易</div>
                        <div class="metric-main-value neutral">
                            <span id="metric-win-rate">--</span>
                            <span class="sub-ratio" id="metric-win-ratio">--</span>
                        </div>
                    </div>

                    <!-- 獲利因子 -->
                    <div class="metric-col">
                        <div class="metric-label">獲利因子</div>
                        <div class="metric-main-value neutral">
                            <span id="metric-profit-factor">--</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 績效圖表頂部切換列 -->
            <div class="perf-header-bar">
                <div class="perf-title-group">
                    <div class="perf-title">績效儀表板 <span class="perf-title-info">ⓘ</span></div>
                    <div class="perf-tab-list">
                        <div class="perf-tab active" id="tab-trade-equity">📊 交易損益淨值圖 (固定Y軸次數 × X軸損益)</div>
                        <div class="perf-tab" id="tab-cum-pnl">📈 時間時序走勢 (Time-based)</div>
                        <div class="perf-tab" id="tab-trades-table">📋 交易明細清單 (64 筆)</div>
                    </div>
                </div>
                <div class="perf-tools">
                    <button class="perf-tool-btn" id="btn-flip-axes" title="翻轉軸向 (Y軸交易次數 ⇄ X軸交易次數)">🔄 翻轉軸向</button>
                    <button class="perf-tool-btn" id="btn-toggle-deltas" title="切換顯示單筆損益長條 (Delta Bars)">📊 單筆盈虧條: 開</button>
                    <button class="perf-tool-btn" id="btn-fit-trade-chart" title="適配全屏">⛶ 適配</button>
                </div>
            </div>

            <!-- 1. 新增：固定 Y 軸(交易次數) × X 軸(損益/淨值) 圖表容器 (預設顯示) -->
            <div class="trade-equity-container-wrapper" id="trade-equity-wrapper">
                <div class="trade-chart-header-info" id="trade-chart-info">
                    <span class="info-tag mode-tag" id="current-mode-tag">當前視圖: 縱軸 Y = 交易次數 (Trade #1 ~ #64) ｜ 橫軸 X = 累積損益 (TWD) & 帳戶淨值</span>
                    <span class="info-tag tip-tag">💡 支援滑鼠滾輪縮放/拖曳平移，Hover 檢視明細，點擊任一交易節點可定位上方 K 線圖</span>
                </div>
                <div class="canvas-scroll-viewport" id="canvas-scroll-viewport">
                    <canvas id="trade-equity-canvas"></canvas>
                </div>
                <div class="trade-tooltip" id="trade-tooltip"></div>
            </div>

            <!-- 2. 原累計損益圖表容器 (時間軸版，預設隱藏) -->
            <div class="perf-chart-container-wrapper" id="perf-chart-wrapper" style="display:none;">
                <div class="perf-legend" id="perf-legend">
                    <span style="color:#787b86;">累計損益:</span>
                    <span class="perf-legend-val" id="perf-legend-val">--</span>
                    <span style="color:#787b86; margin-left: 8px;">時間:</span>
                    <span id="perf-legend-time" style="color:#9299a8;">--</span>
                </div>
                <div id="perf-chart-container"></div>
            </div>

            <!-- 交易明細清單表格 (與 trades.csv 100% 逐行完全對齊) -->
            <div class="trades-table-container-wrapper" id="trades-table-wrapper">
                <table class="trades-table">
                    <thead>
                        <tr>
                            <th>編號</th>
                            <th>方向</th>
                            <th>進場時間</th>
                            <th>進場價格</th>
                            <th>出場時間</th>
                            <th>出場價格</th>
                            <th>口數</th>
                            <th>出場原因</th>
                            <th>手續費+稅</th>
                            <th>淨損益 (TWD)</th>
                            <th>帳戶總淨值</th>
                            <th>圖表定位</th>
                        </tr>
                    </thead>
                    <tbody id="trades-table-body">
                    </tbody>
                </table>
            </div>

            <!-- 底部盈虧狀態區段彩條 -->
            <div class="pnl-ribbon-bar" id="pnl-ribbon-bar"></div>
        </div>

    </div>

    <script>
        // 資料載入：K 線、交易 Markers、累計損益時序、指標、交易明細 (60筆)
        const KLINE_DATA = {data_json};
        const RAW_MARKERS = {markers_json};
        const CUMULATIVE_PNL = {pnl_json};
        const METRICS = {metrics_json};
        const TRADES_DATA = {trades_json};

        let currentInterval = '5m';
        let showMarkers = true;

        // 圖表物件
        let klineChart = null;
        let candlestickSeries = null;
        let volumeSeries = null;
        let markersPrimitive = null;

        let perfChart = null;
        let baselineSeries = null;

        // 同步狀態鎖定標記 (避免相互觸發無限迴圈)
        let isSyncingRange = false;

        // DOM 元素快取
        const klineContainer = document.getElementById('kline-container');
        const perfContainer = document.getElementById('perf-chart-container');
        const perfChartWrapper = document.getElementById('perf-chart-wrapper');
        const tradesTableWrapper = document.getElementById('trades-table-wrapper');
        const tabTradeEquity = document.getElementById('tab-trade-equity');
        const tabCumPnl = document.getElementById('tab-cum-pnl');
        const tabTradesTable = document.getElementById('tab-trades-table');

        const tradeEquityWrapper = document.getElementById('trade-equity-wrapper');
        const tradeEquityCanvas = document.getElementById('trade-equity-canvas');
        const canvasScrollViewport = document.getElementById('canvas-scroll-viewport');
        const tradeTooltip = document.getElementById('trade-tooltip');
        const btnFlipAxes = document.getElementById('btn-flip-axes');
        const btnToggleDeltas = document.getElementById('btn-toggle-deltas');
        const btnFitTradeChart = document.getElementById('btn-fit-trade-chart');
        const currentModeTag = document.getElementById('current-mode-tag');

        let tradeEquityChart = null;
        const legendTime = document.getElementById('legend-time');
        const legendOpen = document.getElementById('legend-open');
        const legendHigh = document.getElementById('legend-high');
        const legendLow = document.getElementById('legend-low');
        const legendClose = document.getElementById('legend-close');
        const legendChange = document.getElementById('legend-change');
        const legendVolume = document.getElementById('legend-volume');
        const legendTradeInfo = document.getElementById('legend-trade-info');

        const perfLegendVal = document.getElementById('perf-legend-val');
        const perfLegendTime = document.getElementById('perf-legend-time');
        const pnlRibbonBar = document.getElementById('pnl-ribbon-bar');

        // 渲染關鍵數據面板數值
        function renderKeyMetrics() {{
            const pnlCol = document.getElementById('metric-total-pnl');
            const pnlVal = document.getElementById('metric-pnl-val');
            const returnPct = document.getElementById('metric-return-pct');
            
            pnlVal.textContent = METRICS.totalNetProfitStr;
            returnPct.textContent = METRICS.totalReturnPctStr;
            pnlCol.className = METRICS.isProfit ? 'metric-main-value positive' : 'metric-main-value negative';

            document.getElementById('metric-mdd-val').textContent = METRICS.maxDrawdownStr;
            document.getElementById('metric-mdd-pct').textContent = METRICS.maxDrawdownPctStr;
            document.getElementById('metric-win-rate').textContent = METRICS.winRateStr;
            document.getElementById('metric-win-ratio').textContent = METRICS.tradesRatioStr;
            document.getElementById('metric-profit-factor').textContent = METRICS.profitFactorStr;
        }}

        // 初始化主 K 線圖
        function initKlineChart() {{
            klineChart = LightweightCharts.createChart(klineContainer, {{
                layout: {{
                    background: {{ color: '#0f0f11' }},
                    textColor: '#868993',
                    fontSize: 12,
                    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
                }},
                grid: {{
                    vertLines: {{ color: 'rgba(255, 255, 255, 0.04)' }},
                    horzLines: {{ color: 'rgba(255, 255, 255, 0.04)' }}
                }},
                crosshair: {{
                    mode: LightweightCharts.CrosshairMode.Normal,
                    vertLine: {{
                        color: '#555e70',
                        width: 1,
                        style: LightweightCharts.LineStyle.Dashed,
                        labelBackgroundColor: '#242735'
                    }},
                    horzLine: {{
                        color: '#555e70',
                        width: 1,
                        style: LightweightCharts.LineStyle.Dashed,
                        labelBackgroundColor: '#242735'
                    }}
                }},
                rightPriceScale: {{
                    borderColor: '#232733',
                    scaleMargins: {{
                        top: 0.08,
                        bottom: 0.22
                    }}
                }},
                timeScale: {{
                    borderColor: '#232733',
                    timeVisible: true,
                    secondsVisible: false,
                    shiftVisibleRangeOnNewBar: false
                }},
                autoSize: true
            }});

            // 支援 Lightweight Charts v5 與 v4
            if (typeof klineChart.addSeries === 'function' && window.LightweightCharts && window.LightweightCharts.CandlestickSeries) {{
                candlestickSeries = klineChart.addSeries(window.LightweightCharts.CandlestickSeries, {{
                    upColor: '#089981',
                    downColor: '#f23645',
                    borderVisible: true,
                    borderUpColor: '#089981',
                    borderDownColor: '#f23645',
                    wickVisible: true,
                    wickUpColor: '#089981',
                    wickDownColor: '#f23645'
                }});
                volumeSeries = klineChart.addSeries(window.LightweightCharts.HistogramSeries, {{
                    color: '#26a69a',
                    priceFormat: {{ type: 'volume' }},
                    priceScaleId: ''
                }});
                if (klineChart.priceScale) {{
                    klineChart.priceScale('').applyOptions({{
                        scaleMargins: {{ top: 0.82, bottom: 0 }}
                    }});
                }}
            }} else if (typeof klineChart.addCandlestickSeries === 'function') {{
                candlestickSeries = klineChart.addCandlestickSeries({{
                    upColor: '#089981',
                    downColor: '#f23645',
                    borderVisible: true,
                    borderUpColor: '#089981',
                    borderDownColor: '#f23645',
                    wickVisible: true,
                    wickUpColor: '#089981',
                    wickDownColor: '#f23645'
                }});
                volumeSeries = klineChart.addHistogramSeries({{
                    color: '#26a69a',
                    priceFormat: {{ type: 'volume' }},
                    priceScaleId: '',
                    scaleMargins: {{ top: 0.82, bottom: 0 }}
                }});
            }}

            // 滑鼠移動十字準星事件
            klineChart.subscribeCrosshairMove(param => {{
                if (
                    param.point === undefined ||
                    !param.time ||
                    param.point.x < 0 ||
                    param.point.x > klineContainer.clientWidth ||
                    param.point.y < 0 ||
                    param.point.y > klineContainer.clientHeight
                ) {{
                    showLastBarData();
                }} else {{
                    let bar = null;
                    let volBar = null;
                    if (param.seriesData && typeof param.seriesData.get === 'function') {{
                        bar = param.seriesData.get(candlestickSeries);
                        volBar = param.seriesData.get(volumeSeries);
                    }}
                    if (bar) {{
                        updateLegend(bar, volBar ? volBar.value : 0, param.time);
                    }}
                }}
            }});
        }}

        // 初始化下半部累計損益 Baseline 圖表
        function initPerfChart() {{
            perfChart = LightweightCharts.createChart(perfContainer, {{
                layout: {{
                    background: {{ color: '#0d0e12' }},
                    textColor: '#868993',
                    fontSize: 11,
                    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
                }},
                grid: {{
                    vertLines: {{ color: 'rgba(255, 255, 255, 0.03)' }},
                    horzLines: {{ color: 'rgba(255, 255, 255, 0.04)' }}
                }},
                crosshair: {{
                    mode: LightweightCharts.CrosshairMode.Normal,
                    vertLine: {{
                        color: '#555e70',
                        width: 1,
                        style: LightweightCharts.LineStyle.Dashed,
                        labelBackgroundColor: '#242735'
                    }},
                    horzLine: {{
                        color: '#555e70',
                        width: 1,
                        style: LightweightCharts.LineStyle.Dashed,
                        labelBackgroundColor: '#242735'
                    }}
                }},
                rightPriceScale: {{
                    borderColor: '#1e222c',
                    scaleMargins: {{
                        top: 0.12,
                        bottom: 0.12
                    }}
                }},
                timeScale: {{
                    borderColor: '#1e222c',
                    timeVisible: true,
                    secondsVisible: false,
                    shiftVisibleRangeOnNewBar: false
                }},
                autoSize: true
            }});

            // 建立 0 軸分界的 BaselineSeries (高於 0 翡翠綠，低於 0 玫瑰紅)
            const baselineOpts = {{
                baseValue: {{ type: 'price', price: 0 }},
                topLineColor: '#089981',
                bottomLineColor: '#f23645',
                topFillColor1: 'rgba(8, 153, 129, 0.32)',
                topFillColor2: 'rgba(8, 153, 129, 0.03)',
                bottomFillColor1: 'rgba(242, 54, 69, 0.03)',
                bottomFillColor2: 'rgba(242, 54, 69, 0.32)',
                lineWidth: 2,
                priceFormat: {{
                    type: 'price',
                    precision: 2,
                    minMove: 0.01
                }}
            }};

            if (typeof perfChart.addSeries === 'function' && window.LightweightCharts && window.LightweightCharts.BaselineSeries) {{
                baselineSeries = perfChart.addSeries(window.LightweightCharts.BaselineSeries, baselineOpts);
            }} else if (typeof perfChart.addBaselineSeries === 'function') {{
                baselineSeries = perfChart.addBaselineSeries(baselineOpts);
            }}

            // 填充累計損益資料
            const baselineData = CUMULATIVE_PNL.map(item => ({{
                time: item.time,
                value: item.value
            }}));
            baselineSeries.setData(baselineData);

            // 監聽損益圖十字準星移動
            perfChart.subscribeCrosshairMove(param => {{
                if (!param.point || !param.time || param.point.x < 0 || param.point.x > perfContainer.clientWidth) {{
                    updatePerfLegend(CUMULATIVE_PNL[CUMULATIVE_PNL.length - 1]);
                }} else {{
                    let val = null;
                    if (param.seriesData && typeof param.seriesData.get === 'function') {{
                        const pt = param.seriesData.get(baselineSeries);
                        if (pt) val = pt.value;
                    }}
                    updatePerfLegend({{ time: param.time, value: val !== null ? val : 0 }});
                }}
            }});

            updatePerfLegend(CUMULATIVE_PNL[CUMULATIVE_PNL.length - 1]);
            buildPnlRibbon();
        }}

        // 建立底部盈虧彩條
        function buildPnlRibbon() {{
            if (!CUMULATIVE_PNL || CUMULATIVE_PNL.length === 0) return;
            pnlRibbonBar.innerHTML = '';
            
            // 合併連續同狀態區間
            let currentStatus = CUMULATIVE_PNL[0].value >= 0 ? 'green' : 'red';
            let segmentCount = 1;
            const segments = [];

            for (let i = 1; i < CUMULATIVE_PNL.length; i++) {{
                const status = CUMULATIVE_PNL[i].value >= 0 ? 'green' : 'red';
                if (status === currentStatus) {{
                    segmentCount++;
                }} else {{
                    segments.push({{ status: currentStatus, count: segmentCount }});
                    currentStatus = status;
                    segmentCount = 1;
                }}
            }}
            segments.push({{ status: currentStatus, count: segmentCount }});

            const totalCount = CUMULATIVE_PNL.length;
            segments.forEach(seg => {{
                const div = document.createElement('div');
                div.className = `ribbon-segment ribbon-${{seg.status}}`;
                div.style.flex = seg.count;
                pnlRibbonBar.appendChild(div);
            }});
        }}

        // 更新績效懸浮數據
        function updatePerfLegend(item) {{
            if (!item) return;
            const val = Number(item.value || 0);
            const sign = val >= 0 ? '+' : '';
            perfLegendVal.textContent = `${{sign}}${{val.toLocaleString('zh-TW', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }})}} TWD`;
            perfLegendVal.style.color = val >= 0 ? '#089981' : '#f23645';

            if (item.time) {{
                const dt = new Date(item.time * 1000);
                perfLegendTime.textContent = dt.toLocaleString('zh-TW', {{ hour12: false }});
            }}
        }}

        // 雙圖時間軸同步聯動
        function setupTimeScaleSync() {{
            if (!klineChart || !perfChart) return;

            const klineTimeScale = klineChart.timeScale();
            const perfTimeScale = perfChart.timeScale();

            klineTimeScale.subscribeVisibleLogicalRangeChange(range => {{
                if (isSyncingRange || !range) return;
                isSyncingRange = true;
                perfTimeScale.setVisibleLogicalRange(range);
                isSyncingRange = false;
            }});

            perfTimeScale.subscribeVisibleLogicalRangeChange(range => {{
                if (isSyncingRange || !range) return;
                isSyncingRange = true;
                klineTimeScale.setVisibleLogicalRange(range);
                isSyncingRange = false;
            }});
        }}

        // 對齊 Markers 時間戳至當前週期的 K 棒時間
        function alignMarkersToBars(rawMarkers, currentBars) {{
            if (!rawMarkers || rawMarkers.length === 0 || !currentBars || currentBars.length === 0) return [];

            const barTimes = currentBars.map(b => Number(b.time));

            // 二分搜尋找不大於 targetTime 的最新 bar time
            function findNearestBarTime(target) {{
                let left = 0, right = barTimes.length - 1;
                let best = -1;
                while (left <= right) {{
                    const mid = Math.floor((left + right) / 2);
                    if (barTimes[mid] <= target) {{
                        best = barTimes[mid];
                        left = mid + 1;
                    }} else {{
                        right = mid - 1;
                    }}
                }}
                return best !== -1 ? best : barTimes[0];
            }}

            const aligned = rawMarkers.map(m => ({{
                ...m,
                time: findNearestBarTime(m.rawTime || m.time)
            }}));

            // 嚴格依照 time 排序
            aligned.sort((a, b) => {{
                if (a.time !== b.time) return a.time - b.time;
                return a.type === 'entry' ? -1 : 1;
            }});

            return aligned;
        }}

        // 更新左上角 K 線數據
        function updateLegend(bar, volume, timestamp) {{
            const dtStr = bar.datetime || (bar.time ? new Date(bar.time * 1000).toLocaleString('zh-TW', {{ hour12: false }}) : '');
            legendTime.textContent = dtStr;
            legendOpen.textContent = Number(bar.open).toFixed(1);
            legendHigh.textContent = Number(bar.high).toFixed(1);
            legendLow.textContent = Number(bar.low).toFixed(1);
            legendClose.textContent = Number(bar.close).toFixed(1);

            const diff = bar.close - bar.open;
            const pct = bar.open > 0 ? (diff / bar.open) * 100 : 0;
            const sign = diff >= 0 ? '+' : '';
            legendChange.textContent = `${{sign}}${{diff.toFixed(1)}} (${{sign}}${{pct.toFixed(2)}}%)`;

            if (diff >= 0) {{
                legendChange.className = 'legend-value up';
                legendClose.className = 'legend-value up';
            }} else {{
                legendChange.className = 'legend-value down';
                legendClose.className = 'legend-value down';
            }}

            legendVolume.textContent = Number(volume || bar.volume || 0).toLocaleString();

            // 檢查該時間戳是否有交易事件
            if (timestamp && RAW_MARKERS) {{
                const found = RAW_MARKERS.filter(m => Math.abs(m.rawTime - timestamp) < 300);
                if (found.length > 0) {{
                    const m = found[0];
                    legendTradeInfo.style.display = 'inline-block';
                    legendTradeInfo.textContent = `🎯 交易信號: [${{m.type === 'entry' ? '建倉' : '平倉'}}] ${{m.text}} @ ${{m.price}}`;
                }} else {{
                    legendTradeInfo.style.display = 'none';
                }}
            }} else {{
                legendTradeInfo.style.display = 'none';
            }}
        }}

        // 顯示最後一根 K 棒數據
        function showLastBarData() {{
            const bars = KLINE_DATA[currentInterval];
            if (bars && bars.length > 0) {{
                const last = bars[bars.length - 1];
                updateLegend(last, last.volume, last.time);
            }}
        }}

        // 載入指定週期的 K 線與標記
        function loadIntervalData(interval) {{
            const rawBars = KLINE_DATA[interval];
            if (!rawBars || rawBars.length === 0) {{
                console.warn('查無該週期資料:', interval);
                return;
            }}

            currentInterval = interval;

            const candleBars = rawBars.map(b => ({{
                time: Number(b.time),
                open: Number(b.open),
                high: Number(b.high),
                low: Number(b.low),
                close: Number(b.close)
            }}));

            const volumeBars = rawBars.map(b => ({{
                time: Number(b.time),
                value: Number(b.volume),
                color: b.close >= b.open ? 'rgba(8, 153, 129, 0.45)' : 'rgba(242, 54, 69, 0.45)'
            }}));

            candlestickSeries.setData(candleBars);
            volumeSeries.setData(volumeBars);

            // 更新建倉平倉 Markers
            if (showMarkers) {{
                const alignedMarkers = alignMarkersToBars(RAW_MARKERS, candleBars);
                applyMarkers(alignedMarkers);
            }} else {{
                applyMarkers([]);
            }}

            klineChart.timeScale().fitContent();
            if (perfChart) {{
                perfChart.timeScale().fitContent();
            }}

            showLastBarData();
        }}

        // 套用 Markers 至 K 線系列
        function applyMarkers(markers) {{
            try {{
                if (typeof LightweightCharts.createSeriesMarkers === 'function') {{
                    if (markersPrimitive) {{
                        markersPrimitive.setMarkers(markers);
                    }} else {{
                        markersPrimitive = LightweightCharts.createSeriesMarkers(candlestickSeries, markers);
                    }}
                }} else if (typeof candlestickSeries.setMarkers === 'function') {{
                    candlestickSeries.setMarkers(markers);
                }}
            }} catch (err) {{
                console.warn('Markers 套用警告 (嘗試直接設定):', err);
                try {{
                    if (candlestickSeries && typeof candlestickSeries.setMarkers === 'function') {{
                        candlestickSeries.setMarkers(markers);
                    }}
                }} catch (e) {{
                    console.error('Markers 設定失敗:', e);
                }}
            }}
        }}

        // 綁定事件監聽器
        function setupEventListeners() {{
            // 週期切換按鈕
            document.querySelectorAll('.tf-btn').forEach(btn => {{
                btn.addEventListener('click', () => {{
                    document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    const interval = btn.getAttribute('data-interval');
                    loadIntervalData(interval);
                }});
            }});

            // 交易標記切換開關
            const toggleMarkersBtn = document.getElementById('toggle-markers-btn');
            toggleMarkersBtn.addEventListener('click', () => {{
                showMarkers = !showMarkers;
                if (showMarkers) {{
                    toggleMarkersBtn.classList.add('active');
                    toggleMarkersBtn.innerHTML = '<span>📍 交易標記: 顯示</span>';
                    const rawBars = KLINE_DATA[currentInterval];
                    const candleBars = rawBars.map(b => ({{ time: Number(b.time) }}));
                    applyMarkers(alignMarkersToBars(RAW_MARKERS, candleBars));
                }} else {{
                    toggleMarkersBtn.classList.remove('active');
                    toggleMarkersBtn.innerHTML = '<span>📍 交易標記: 隱藏</span>';
                    applyMarkers([]);
                }}
            }});

        // ==============================================================
        // 固定 Y 軸 (交易次數) × X 軸 (損益 / 帳戶淨值) Canvas 高效互動圖表引擎
        // ==============================================================
        class TradeEquityChart {{
            constructor(canvas, viewport, tooltip) {{
                this.canvas = canvas;
                this.viewport = viewport;
                this.tooltip = tooltip;
                this.ctx = canvas.getContext('2d');
                
                // 預設模式：'Y_TRADES_X_PNL' (符合使用者文字指定：縱軸 Y 為交易次數，橫軸 X 為累積損益)
                this.mode = 'Y_TRADES_X_PNL';
                this.showDeltas = true;
                
                this.hoverIndex = -1;
                this.trades = TRADES_DATA || [];
                
                // 補入第 0 筆期初基準點 (Cum PnL = 0, Equity = 105,150)
                this.equityPoints = [
                    {{ trade_id: 0, cum_pnl: 0, equity_after: 105150, net_pnl: 0, exit_reason: '期初基準', side: '--', entry_time: '--', exit_time: '--' }},
                    ...this.trades
                ];

                this.initEvents();
            }}

            initEvents() {{
                if (!this.canvas) return;

                this.canvas.addEventListener('mousemove', (e) => {{
                    const rect = this.canvas.getBoundingClientRect();
                    const mouseX = e.clientX - rect.left;
                    const mouseY = e.clientY - rect.top;
                    this.handleMouseMove(mouseX, mouseY, e.clientX, e.clientY);
                }});

                this.canvas.addEventListener('mouseleave', () => {{
                    this.hoverIndex = -1;
                    if (this.tooltip) this.tooltip.style.display = 'none';
                    this.draw();
                }});

                this.canvas.addEventListener('click', () => {{
                    if (this.hoverIndex > 0 && this.hoverIndex <= this.trades.length) {{
                        const t = this.trades[this.hoverIndex - 1];
                        if (t && t.entry_ts && t.exit_ts) {{
                            focusTradeOnChart(t.entry_ts, t.exit_ts);
                        }}
                    }}
                }});
            }}

            handleMouseMove(mx, my, clientX, clientY) {{
                if (!this.layout) return;
                const L = this.layout;
                const total = this.equityPoints.length;

                let bestIdx = -1;
                let minDist = 999999;

                if (this.mode === 'Y_TRADES_X_PNL') {{
                    for (let i = 0; i < total; i++) {{
                        const py = L.getY(i);
                        const dist = Math.abs(my - py);
                        if (dist < minDist) {{
                            minDist = dist;
                            bestIdx = i;
                        }}
                    }}
                }} else {{
                    for (let i = 0; i < total; i++) {{
                        const px = L.getX(i);
                        const dist = Math.abs(mx - px);
                        if (dist < minDist) {{
                            minDist = dist;
                            bestIdx = i;
                        }}
                    }}
                }}

                if (bestIdx !== this.hoverIndex) {{
                    this.hoverIndex = bestIdx;
                    this.draw();
                }}

                this.showTooltip(bestIdx, clientX, clientY);
            }}

            showTooltip(idx, clientX, clientY) {{
                if (!this.tooltip || idx < 0 || idx >= this.equityPoints.length) {{
                    if (this.tooltip) this.tooltip.style.display = 'none';
                    return;
                }}

                const pt = this.equityPoints[idx];
                const isOrigin = (idx === 0);
                const isWin = pt.net_pnl >= 0;
                const pnlSign = isWin ? '+' : '';
                const cumSign = pt.cum_pnl >= 0 ? '+' : '';

                let html = '';
                if (isOrigin) {{
                    html = `
                        <div class="tt-title">
                            <span>🏁 期初基準狀態</span>
                            <span style="color:#787b86;">Trade #0</span>
                        </div>
                        <div class="tt-row"><span class="tt-label">初始資金:</span><span class="tt-val">105,150.0 TWD</span></div>
                        <div class="tt-row"><span class="tt-label">累積損益:</span><span class="tt-val">0.0 TWD</span></div>
                    `;
                }} else {{
                    const sideLabel = pt.side === 'LONG' ? '做多 (LONG)' : '放空 (SHORT)';
                    const sideColor = pt.side === 'LONG' ? '#089981' : '#f23645';
                    html = `
                        <div class="tt-title">
                            <span>🎯 第 #${{pt.trade_id}} 筆交易</span>
                            <span style="color:${{sideColor}}; font-weight:700;">${{sideLabel}}</span>
                        </div>
                        <div class="tt-row"><span class="tt-label">進場:</span><span class="tt-val">${{pt.entry_time}} @ ${{Number(pt.entry_price).toFixed(1)}}</span></div>
                        <div class="tt-row"><span class="tt-label">出場:</span><span class="tt-val">${{pt.exit_time}} @ ${{Number(pt.exit_price).toFixed(1)}}</span></div>
                        <div class="tt-row"><span class="tt-label">平倉原因:</span><span class="tt-val">${{pt.exit_reason}}</span></div>
                        <div class="tt-row"><span class="tt-label">單筆損益:</span><span class="tt-val ${{isWin ? 'win' : 'loss'}}">${{pnlSign}}${{Number(pt.net_pnl).toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}} TWD</span></div>
                        <div class="tt-row"><span class="tt-label">累積損益:</span><span class="tt-val ${{pt.cum_pnl >= 0 ? 'win' : 'loss'}}">${{cumSign}}${{Number(pt.cum_pnl).toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}} TWD</span></div>
                        <div class="tt-row"><span class="tt-label">帳戶總淨值:</span><span class="tt-val" style="color:#ffffff;">${{Number(pt.equity_after).toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}} TWD</span></div>
                        <div class="tt-footer">📍 點擊此處立即定位上方 K 線圖</div>
                    `;
                }}

                this.tooltip.innerHTML = html;
                this.tooltip.style.display = 'block';

                const tipW = 260;
                const tipH = 180;
                let left = clientX + 16;
                let top = clientY - 40;

                if (left + tipW > window.innerWidth) left = clientX - tipW - 16;
                if (top + tipH > window.innerHeight) top = window.innerHeight - tipH - 16;
                if (top < 10) top = 10;

                this.tooltip.style.left = left + 'px';
                this.tooltip.style.top = top + 'px';
            }}

            toggleMode() {{
                this.mode = (this.mode === 'Y_TRADES_X_PNL') ? 'X_TRADES_Y_PNL' : 'Y_TRADES_X_PNL';
                if (currentModeTag) {{
                    if (this.mode === 'Y_TRADES_X_PNL') {{
                        currentModeTag.textContent = '當前視圖: 縱軸 Y = 交易次數 (Trade #1 ~ #64) ｜ 橫軸 X = 累積損益 (TWD) & 帳戶淨值';
                    }} else {{
                        currentModeTag.textContent = '當前視圖: 橫軸 X = 交易次數 (Trade #1 ~ #64) ｜ 縱軸 Y = 累積損益 (TWD) & 帳戶淨值';
                    }}
                }}
                this.resizeAndDraw();
                return this.mode;
            }}

            toggleDeltas() {{
                this.showDeltas = !this.showDeltas;
                this.draw();
                return this.showDeltas;
            }}

            resizeAndDraw() {{
                if (!this.viewport || !this.canvas) return;
                const dpr = window.devicePixelRatio || 1;
                const rect = this.viewport.getBoundingClientRect();
                const width = rect.width;
                const height = rect.height;

                if (width <= 0 || height <= 0) return;

                this.canvas.width = Math.floor(width * dpr);
                this.canvas.height = Math.floor(height * dpr);
                this.canvas.style.width = width + 'px';
                this.canvas.style.height = height + 'px';

                this.ctx.resetTransform();
                this.ctx.scale(dpr, dpr);

                this.width = width;
                this.height = height;

                this.draw();
            }}

            draw() {{
                if (!this.width || !this.height) return;
                const ctx = this.ctx;
                const w = this.width;
                const h = this.height;

                ctx.clearRect(0, 0, w, h);

                if (this.mode === 'Y_TRADES_X_PNL') {{
                    this.drawYTradesXPnl(ctx, w, h);
                }} else {{
                    this.drawXTradesYPnl(ctx, w, h);
                }}
            }}

            drawYTradesXPnl(ctx, w, h) {{
                const totalTrades = this.trades.length;
                if (totalTrades === 0) return;

                const padL = 80;
                const padR = 40;
                const padT = 36;
                const padB = 36;

                const plotW = w - padL - padR;
                const plotH = h - padT - padB;

                const pnlVals = this.equityPoints.map(p => p.cum_pnl);
                let minPnl = Math.min(...pnlVals);
                let maxPnl = Math.max(...pnlVals);
                if (minPnl > 0) minPnl = 0;
                if (maxPnl < 0) maxPnl = 0;

                const span = (maxPnl - minPnl) || 1000;
                const margin = span * 0.12;
                const dispMin = minPnl - margin;
                const dispMax = maxPnl + margin;

                const getX = (val) => padL + ((val - dispMin) / (dispMax - dispMin)) * plotW;
                const getY = (idx) => padT + (idx / totalTrades) * plotH;
                const zeroX = getX(0);

                ctx.fillStyle = '#0d0e12';
                ctx.fillRect(0, 0, w, h);

                const numTicksX = Math.max(5, Math.floor(plotW / 130));
                const step = (dispMax - dispMin) / numTicksX;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto';

                for (let i = 0; i <= numTicksX; i++) {{
                    const val = dispMin + i * step;
                    const x = getX(val);
                    if (x < padL || x > w - padR) continue;

                    const isZero = Math.abs(val) < step * 0.35;
                    ctx.strokeStyle = isZero ? '#475569' : '#181b24';
                    ctx.lineWidth = isZero ? 1.5 : 1;
                    if (isZero) {{
                        ctx.setLineDash([4, 4]);
                    }} else {{
                        ctx.setLineDash([]);
                    }}
                    ctx.beginPath();
                    ctx.moveTo(x, padT);
                    ctx.lineTo(x, h - padB);
                    ctx.stroke();
                    ctx.setLineDash([]);

                    const sign = val > 0 ? '+' : '';
                    const labelText = `${{sign}}${{Math.round(val).toLocaleString()}} 元`;
                    const eqText = `(${{(105150 + Math.round(val)).toLocaleString()}})`;

                    ctx.fillStyle = isZero ? '#38bdf8' : '#64748b';
                    ctx.fillText(labelText, x, padT - 14);
                    ctx.fillStyle = '#475569';
                    ctx.fillText(eqText, x, h - padB + 14);
                }}

                const stepY = totalTrades > 50 ? 5 : (totalTrades > 20 ? 2 : 1);
                ctx.textAlign = 'right';
                for (let i = 0; i <= totalTrades; i += stepY) {{
                    const y = getY(i);
                    ctx.strokeStyle = '#161922';
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.moveTo(padL, y);
                    ctx.lineTo(w - padR, y);
                    ctx.stroke();

                    const isHover = (i === this.hoverIndex);
                    ctx.fillStyle = isHover ? '#ffffff' : ((i === totalTrades) ? '#089981' : '#787b86');
                    ctx.font = isHover ? 'bold 11px sans-serif' : '10px sans-serif';
                    ctx.fillText(i === 0 ? '期初 #0' : `Trade #${{i}}`, padL - 8, y);
                }}

                ctx.save();
                ctx.beginPath();
                ctx.moveTo(zeroX, getY(0));
                for (let i = 0; i <= totalTrades; i++) {{
                    const pt = this.equityPoints[i];
                    const px = getX(pt.cum_pnl);
                    const py = getY(i);
                    if (i > 0) {{
                        ctx.lineTo(px, getY(i - 1));
                    }}
                    ctx.lineTo(px, py);
                }}
                ctx.lineTo(zeroX, getY(totalTrades));
                ctx.closePath();

                const gradGreen = ctx.createLinearGradient(zeroX, 0, w - padR, 0);
                gradGreen.addColorStop(0, 'rgba(8, 153, 129, 0.05)');
                gradGreen.addColorStop(1, 'rgba(8, 153, 129, 0.35)');
                ctx.fillStyle = gradGreen;
                ctx.fill();
                ctx.restore();

                if (this.showDeltas) {{
                    for (let i = 1; i <= totalTrades; i++) {{
                        const prevPt = this.equityPoints[i - 1];
                        const curPt = this.equityPoints[i];
                        const x0 = getX(prevPt.cum_pnl);
                        const x1 = getX(curPt.cum_pnl);
                        const y = getY(i);
                        const isWin = curPt.net_pnl >= 0;

                        ctx.fillStyle = isWin ? 'rgba(8, 153, 129, 0.7)' : 'rgba(242, 54, 69, 0.7)';
                        const barH = 5;
                        const bx = Math.min(x0, x1);
                        const bw = Math.max(Math.abs(x1 - x0), 2);
                        ctx.fillRect(bx, y - barH / 2, bw, barH);
                    }}
                }}

                ctx.beginPath();
                ctx.strokeStyle = '#089981';
                ctx.lineWidth = 2.5;
                ctx.moveTo(getX(this.equityPoints[0].cum_pnl), getY(0));
                for (let i = 1; i <= totalTrades; i++) {{
                    const prevPt = this.equityPoints[i - 1];
                    const curPt = this.equityPoints[i];
                    const px = getX(curPt.cum_pnl);
                    const py = getY(i);
                    ctx.lineTo(px, getY(i - 1));
                    ctx.lineTo(px, py);
                }}
                ctx.stroke();

                for (let i = 0; i <= totalTrades; i++) {{
                    const pt = this.equityPoints[i];
                    const px = getX(pt.cum_pnl);
                    const py = getY(i);
                    const isWin = pt.net_pnl >= 0;

                    ctx.fillStyle = (i === 0) ? '#9299a8' : (isWin ? '#089981' : '#f23645');
                    ctx.beginPath();
                    ctx.arc(px, py, (i === this.hoverIndex) ? 6 : 3, 0, Math.PI * 2);
                    ctx.fill();

                    if (i === this.hoverIndex) {{
                        ctx.strokeStyle = '#ffffff';
                        ctx.lineWidth = 2;
                        ctx.stroke();
                    }}
                }}

                if (this.hoverIndex >= 0 && this.hoverIndex <= totalTrades) {{
                    const hPt = this.equityPoints[this.hoverIndex];
                    const hx = getX(hPt.cum_pnl);
                    const hy = getY(this.hoverIndex);

                    ctx.strokeStyle = 'rgba(255, 255, 255, 0.45)';
                    ctx.lineWidth = 1;
                    ctx.setLineDash([3, 3]);
                    ctx.beginPath();
                    ctx.moveTo(padL, hy);
                    ctx.lineTo(w - padR, hy);
                    ctx.stroke();

                    ctx.beginPath();
                    ctx.moveTo(hx, padT);
                    ctx.lineTo(hx, h - padB);
                    ctx.stroke();
                    ctx.setLineDash([]);
                }}

                this.layout = {{ mode: 'Y_TRADES_X_PNL', padL, padR, padT, padB, plotW, plotH, dispMin, dispMax, totalTrades, getX, getY }};
            }}

            drawXTradesYPnl(ctx, w, h) {{
                const totalTrades = this.trades.length;
                if (totalTrades === 0) return;

                const padL = 75;
                const padR = 40;
                const padT = 36;
                const padB = 36;

                const plotW = w - padL - padR;
                const plotH = h - padT - padB;

                const pnlVals = this.equityPoints.map(p => p.cum_pnl);
                let minPnl = Math.min(...pnlVals);
                let maxPnl = Math.max(...pnlVals);
                if (minPnl > 0) minPnl = 0;
                if (maxPnl < 0) maxPnl = 0;

                const span = (maxPnl - minPnl) || 1000;
                const margin = span * 0.12;
                const dispMin = minPnl - margin;
                const dispMax = maxPnl + margin;

                const getX = (idx) => padL + (idx / totalTrades) * plotW;
                const getY = (val) => padT + (1 - (val - dispMin) / (dispMax - dispMin)) * plotH;
                const zeroY = getY(0);

                ctx.fillStyle = '#0d0e12';
                ctx.fillRect(0, 0, w, h);

                const numTicksY = Math.max(5, Math.floor(plotH / 50));
                const step = (dispMax - dispMin) / numTicksY;
                ctx.textAlign = 'right';
                ctx.textBaseline = 'middle';
                ctx.font = '10px sans-serif';

                for (let i = 0; i <= numTicksY; i++) {{
                    const val = dispMin + i * step;
                    const y = getY(val);
                    if (y < padT || y > h - padB) continue;

                    const isZero = Math.abs(val) < step * 0.35;
                    ctx.strokeStyle = isZero ? '#475569' : '#181b24';
                    ctx.lineWidth = isZero ? 1.5 : 1;
                    if (isZero) {{
                        ctx.setLineDash([4, 4]);
                    }} else {{
                        ctx.setLineDash([]);
                    }}
                    ctx.beginPath();
                    ctx.moveTo(padL, y);
                    ctx.lineTo(w - padR, y);
                    ctx.stroke();
                    ctx.setLineDash([]);

                    const sign = val > 0 ? '+' : '';
                    ctx.fillStyle = isZero ? '#38bdf8' : '#64748b';
                    ctx.fillText(`${{sign}}${{Math.round(val).toLocaleString()}}`, padL - 8, y);
                }}

                const stepX = totalTrades > 50 ? 5 : (totalTrades > 20 ? 2 : 1);
                ctx.textAlign = 'center';
                for (let i = 0; i <= totalTrades; i += stepX) {{
                    const x = getX(i);
                    ctx.strokeStyle = '#161922';
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.moveTo(x, padT);
                    ctx.lineTo(x, h - padB);
                    ctx.stroke();

                    const isHover = (i === this.hoverIndex);
                    ctx.fillStyle = isHover ? '#ffffff' : ((i === totalTrades) ? '#089981' : '#787b86');
                    ctx.fillText(i === 0 ? '#0' : `#${{i}}`, x, h - padB + 14);
                }}

                ctx.save();
                ctx.beginPath();
                ctx.moveTo(getX(0), zeroY);
                for (let i = 0; i <= totalTrades; i++) {{
                    const pt = this.equityPoints[i];
                    const px = getX(i);
                    const py = getY(pt.cum_pnl);
                    if (i > 0) {{
                        ctx.lineTo(px, getY(this.equityPoints[i - 1].cum_pnl));
                    }}
                    ctx.lineTo(px, py);
                }}
                ctx.lineTo(getX(totalTrades), zeroY);
                ctx.closePath();

                const gradGreen = ctx.createLinearGradient(0, zeroY, 0, padT);
                gradGreen.addColorStop(0, 'rgba(8, 153, 129, 0.05)');
                gradGreen.addColorStop(1, 'rgba(8, 153, 129, 0.35)');
                ctx.fillStyle = gradGreen;
                ctx.fill();
                ctx.restore();

                ctx.beginPath();
                ctx.strokeStyle = '#089981';
                ctx.lineWidth = 2.5;
                ctx.moveTo(getX(0), getY(this.equityPoints[0].cum_pnl));
                for (let i = 1; i <= totalTrades; i++) {{
                    const prevPt = this.equityPoints[i - 1];
                    const curPt = this.equityPoints[i];
                    const px = getX(i);
                    const py = getY(curPt.cum_pnl);
                    ctx.lineTo(px, getY(prevPt.cum_pnl));
                    ctx.lineTo(px, py);
                }}
                ctx.stroke();

                for (let i = 0; i <= totalTrades; i++) {{
                    const pt = this.equityPoints[i];
                    const px = getX(i);
                    const py = getY(pt.cum_pnl);
                    const isWin = pt.net_pnl >= 0;

                    ctx.fillStyle = (i === 0) ? '#9299a8' : (isWin ? '#089981' : '#f23645');
                    ctx.beginPath();
                    ctx.arc(px, py, (i === this.hoverIndex) ? 6 : 3, 0, Math.PI * 2);
                    ctx.fill();

                    if (i === this.hoverIndex) {{
                        ctx.strokeStyle = '#ffffff';
                        ctx.lineWidth = 2;
                        ctx.stroke();
                    }}
                }}

                if (this.hoverIndex >= 0 && this.hoverIndex <= totalTrades) {{
                    const hPt = this.equityPoints[this.hoverIndex];
                    const hx = getX(this.hoverIndex);
                    const hy = getY(hPt.cum_pnl);

                    ctx.strokeStyle = 'rgba(255, 255, 255, 0.45)';
                    ctx.lineWidth = 1;
                    ctx.setLineDash([3, 3]);
                    ctx.beginPath();
                    ctx.moveTo(padL, hy);
                    ctx.lineTo(w - padR, hy);
                    ctx.stroke();

                    ctx.beginPath();
                    ctx.moveTo(hx, padT);
                    ctx.lineTo(hx, h - padB);
                    ctx.stroke();
                    ctx.setLineDash([]);
                }}

                this.layout = {{ mode: 'X_TRADES_Y_PNL', padL, padR, padT, padB, plotW, plotH, dispMin, dispMax, totalTrades, getX, getY }};
            }}
        }}

        // 綁定事件監聽器
        function setupEventListeners() {{
            // 週期切換按鈕
            document.querySelectorAll('.tf-btn').forEach(btn => {{
                btn.addEventListener('click', () => {{
                    document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    const interval = btn.getAttribute('data-interval');
                    loadIntervalData(interval);
                }});
            }});

            // 交易標記切換開關
            const toggleMarkersBtn = document.getElementById('toggle-markers-btn');
            toggleMarkersBtn.addEventListener('click', () => {{
                showMarkers = !showMarkers;
                if (showMarkers) {{
                    toggleMarkersBtn.classList.add('active');
                    toggleMarkersBtn.innerHTML = '<span>📍 交易標記: 顯示</span>';
                    const rawBars = KLINE_DATA[currentInterval];
                    const candleBars = rawBars.map(b => ({{ time: Number(b.time) }}));
                    applyMarkers(alignMarkersToBars(RAW_MARKERS, candleBars));
                }} else {{
                    toggleMarkersBtn.classList.remove('active');
                    toggleMarkersBtn.innerHTML = '<span>📍 交易標記: 隱藏</span>';
                    applyMarkers([]);
                }}
            }});

            // 適配畫面按鈕
            document.getElementById('reset-zoom-btn').addEventListener('click', () => {{
                if (klineChart) klineChart.timeScale().fitContent();
                if (perfChart) perfChart.timeScale().fitContent();
                if (tradeEquityChart) tradeEquityChart.resizeAndDraw();
            }});

            // 翻轉軸向按鈕
            if (btnFlipAxes) {{
                btnFlipAxes.addEventListener('click', () => {{
                    if (tradeEquityChart) {{
                        const newMode = tradeEquityChart.toggleMode();
                        btnFlipAxes.textContent = (newMode === 'Y_TRADES_X_PNL') ? '🔄 翻轉軸向 (Y軸交易次數)' : '🔄 翻轉軸向 (X軸交易次數)';
                    }}
                }});
            }}

            // 單筆盈虧條開關
            if (btnToggleDeltas) {{
                btnToggleDeltas.addEventListener('click', () => {{
                    if (tradeEquityChart) {{
                        const enabled = tradeEquityChart.toggleDeltas();
                        btnToggleDeltas.textContent = enabled ? '📊 單筆盈虧條: 開' : '📊 單筆盈虧條: 關';
                    }}
                }});
            }}

            // 適配全屏按鈕
            if (btnFitTradeChart) {{
                btnFitTradeChart.addEventListener('click', () => {{
                    if (tradeEquityChart) tradeEquityChart.resizeAndDraw();
                }});
            }}

            // 頁籤切換：交易損益淨值圖 (預設) vs 累計損益 (時間軸) vs 交易明細清單
            function switchTab(activeTab) {{
                [tabTradeEquity, tabCumPnl, tabTradesTable].forEach(tab => {{
                    if (tab) tab.classList.remove('active');
                }});
                activeTab.classList.add('active');

                if (tradeEquityWrapper) tradeEquityWrapper.style.display = (activeTab === tabTradeEquity) ? 'flex' : 'none';
                if (perfChartWrapper) perfChartWrapper.style.display = (activeTab === tabCumPnl) ? 'block' : 'none';
                if (tradesTableWrapper) tradesTableWrapper.style.display = (activeTab === tabTradesTable) ? 'block' : 'none';
                if (pnlRibbonBar) pnlRibbonBar.style.display = (activeTab === tabTradesTable) ? 'none' : 'flex';

                if (activeTab === tabTradeEquity && tradeEquityChart) {{
                    setTimeout(() => tradeEquityChart.resizeAndDraw(), 50);
                }} else if (activeTab === tabCumPnl && perfChart) {{
                    setTimeout(() => {{
                        perfChart.applyOptions({{
                            width: perfContainer.clientWidth,
                            height: perfContainer.clientHeight
                        }});
                    }}, 50);
                }}
            }}

            if (tabTradeEquity) tabTradeEquity.addEventListener('click', () => switchTab(tabTradeEquity));
            if (tabCumPnl) tabCumPnl.addEventListener('click', () => switchTab(tabCumPnl));
            if (tabTradesTable) tabTradesTable.addEventListener('click', () => switchTab(tabTradesTable));

            // 視窗 Resize 自適應
            window.addEventListener('resize', () => {{
                if (klineChart) {{
                    klineChart.applyOptions({{
                        width: klineContainer.clientWidth,
                        height: klineContainer.clientHeight
                    }});
                }}
                if (perfChart) {{
                    perfChart.applyOptions({{
                        width: perfContainer.clientWidth,
                        height: perfContainer.clientHeight
                    }});
                }}
                if (tradeEquityChart) {{
                    tradeEquityChart.resizeAndDraw();
                }}
            }});
        }}

        // 渲染交易明細清單表格 (100% 逐行完全對齊 trades.csv)
        function renderTradesTable() {{
            const tbody = document.getElementById('trades-table-body');
            if (!tbody || !TRADES_DATA) return;
            tbody.innerHTML = '';

            TRADES_DATA.forEach(t => {{
                const tr = document.createElement('tr');
                const isLong = t.side === 'LONG';
                const isProfit = t.net_pnl >= 0;
                const pnlSign = isProfit ? '+' : '';
                const pnlClass = isProfit ? 'pnl-positive' : 'pnl-negative';

                tr.innerHTML = `
                    <td style="font-weight:700; color:#9299a8;">#${{t.trade_id}}</td>
                    <td><span class="${{isLong ? 'badge-side-long' : 'badge-side-short'}}">${{isLong ? '做多 (LONG)' : '放空 (SHORT)'}}</span></td>
                    <td>${{t.entry_time}}</td>
                    <td style="font-weight:600;">${{Number(t.entry_price).toFixed(1)}}</td>
                    <td>${{t.exit_time}}</td>
                    <td style="font-weight:600;">${{Number(t.exit_price).toFixed(1)}}</td>
                    <td>${{t.qty}} 口</td>
                    <td>${{t.exit_reason}}</td>
                    <td style="color:#787b86;">${{Number(t.costs).toFixed(0)}} 元</td>
                    <td class="${{pnlClass}}">${{pnlSign}}${{Number(t.net_pnl).toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}}</td>
                    <td style="color:#ffffff; font-weight:600;">${{Number(t.equity_after).toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}}</td>
                    <td><button class="btn-focus-trade" title="點擊在上方圖表聚焦定位">📍 聚焦</button></td>
                `;

                tr.addEventListener('click', () => {{
                    focusTradeOnChart(t.entry_ts, t.exit_ts);
                }});

                tbody.appendChild(tr);
            }});
        }}

        // 在圖表上聚焦定位指定交易
        function focusTradeOnChart(entryTs, exitTs) {{
            if (!klineChart) return;
            const pad = 3600 * 2;
            const from = entryTs - pad;
            const to = exitTs + pad;
            try {{
                klineChart.timeScale().setVisibleRange({{ from: from, to: to }});
                if (perfChart) {{
                    perfChart.timeScale().setVisibleRange({{ from: from, to: to }});
                }}
            }} catch (e) {{
                console.warn('聚焦定位時序範圍自適應:', e);
            }}
        }}

        // 全域初始化入口
        function initDashboard() {{
            renderKeyMetrics();
            initKlineChart();
            initPerfChart();

            // 初始化固定 Y 軸(交易次數) × X 軸(損益) 圖表
            if (tradeEquityCanvas && canvasScrollViewport && tradeTooltip) {{
                tradeEquityChart = new TradeEquityChart(tradeEquityCanvas, canvasScrollViewport, tradeTooltip);
                setTimeout(() => {{
                    tradeEquityChart.resizeAndDraw();
                }}, 100);
            }}

            setupTimeScaleSync();
            setupEventListeners();
            renderTradesTable();
            loadIntervalData(currentInterval);
        }}

        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', initDashboard);
        }} else {{
            initDashboard();
        }}
    </script>
</body>
</html>
"""
    return html


def generate_dashboard(
    db_path: str = "data/market_data.db",
    output_html_path: str = "kline_dashboard.html"
) -> str:
    """
    執行多週期聚合、策略回測運算並產生獨立 HTML 儀表板檔案。

    參數:
        db_path (str): SQLite 資料庫路徑。
        output_html_path (str): 目標 HTML 輸出路徑。

    返回:
        str: 產出之 HTML 絕對路徑。
    """
    logger.info(f"開始從 SQLite 資料庫聚合全時序 K 線: {db_path}...")
    service = KlineService(db_path=db_path)

    # 聚合 11 種指定週期
    intervals = ["1m", "2m", "3m", "5m", "15m", "30m", "45m", "1h", "2h", "4h", "1d"]
    intervals_data: Dict[str, List[Dict[str, Any]]] = {}

    for key in intervals:
        bars = service.get_kbars(key)
        intervals_data[key] = bars
        logger.info(f"已收錄週期 {key}: {len(bars):,} 筆")

    # 執行回測以產出進出場點位標記 (Markers) 與累計損益時序 (Baseline)
    bars_5m = intervals_data.get("5m", [])
    if not bars_5m:
        raise ValueError("Status: ERROR | Root Cause: 缺少 5 分 K 資料無法進行策略回測 | Suggested Fix: 確認資料庫收錄時序。")

    metrics, markers, cumulative_pnl = run_strategy_backtest(bars_5m)

    # 讀取本機 Lightweight Charts 獨立生產版本腳本
    standalone_js = read_standalone_lightweight_charts()

    html_content = build_html_content(
        intervals_data=intervals_data,
        markers=markers,
        cumulative_pnl=cumulative_pnl,
        metrics=metrics,
        standalone_js=standalone_js
    )

    output_abs = os.path.abspath(output_html_path)
    with open(output_abs, "w", encoding="utf-8") as f:
        f.write(html_content)

    file_size_mb = os.path.getsize(output_abs) / (1024.0 * 1024.0)
    logger.info(f"K 線與策略回測互動儀表板成功產出: {output_abs} (檔案大小: {file_size_mb:.2f} MB)")
    return output_abs


if __name__ == "__main__":
    generate_dashboard()
