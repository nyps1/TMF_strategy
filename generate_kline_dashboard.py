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
from services.backtest import BacktestEngine, BacktestConfig, BacktestMetrics, PositionSide, ExitReason

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

        /* 3. 累計損益圖表容器 */
        .perf-chart-container-wrapper {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            background: #0d0e12;
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
            <span class="strategy-tag">動態布林突破策略 (真實成本版)</span>
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
                    <div class="perf-title">績效 <span class="perf-title-info">ⓘ</span></div>
                    <div class="perf-tab-list">
                        <div class="perf-tab active" id="tab-cum-pnl">累計損益</div>
                        <div class="perf-tab" title="基準對比">買進並持有 <span class="perf-tab-icon">👁️‍🗨️</span></div>
                        <div class="perf-tab" title="交易風險">交易波動幅度 <span class="perf-tab-icon">👁️‍🗨️</span></div>
                        <div class="perf-tab">漲幅與回撤 <span class="perf-tab-icon">▾</span></div>
                    </div>
                </div>
                <div class="perf-tools">
                    <button class="perf-tool-btn" title="設定">⚙️</button>
                    <button class="perf-tool-btn" title="截圖">📷</button>
                    <button class="perf-tool-btn" title="全螢幕">⛶</button>
                </div>
            </div>

            <!-- 累計損益圖表容器 -->
            <div class="perf-chart-container-wrapper">
                <div class="perf-legend" id="perf-legend">
                    <span style="color:#787b86;">累計損益:</span>
                    <span class="perf-legend-val" id="perf-legend-val">--</span>
                    <span style="color:#787b86; margin-left: 8px;">時間:</span>
                    <span id="perf-legend-time" style="color:#9299a8;">--</span>
                </div>
                <div id="perf-chart-container"></div>
            </div>

            <!-- 底部盈虧狀態區段彩條 -->
            <div class="pnl-ribbon-bar" id="pnl-ribbon-bar"></div>
        </div>

    </div>

    <script>
        // 資料載入：K 線、交易 Markers、累計損益時序、指標
        const KLINE_DATA = {data_json};
        const RAW_MARKERS = {markers_json};
        const CUMULATIVE_PNL = {pnl_json};
        const METRICS = {metrics_json};

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

            // 適配畫面按鈕
            document.getElementById('reset-zoom-btn').addEventListener('click', () => {{
                if (klineChart) klineChart.timeScale().fitContent();
                if (perfChart) perfChart.timeScale().fitContent();
            }});

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
            }});
        }}

        // 全域初始化入口
        function initDashboard() {{
            renderKeyMetrics();
            initKlineChart();
            initPerfChart();
            setupTimeScaleSync();
            setupEventListeners();
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
