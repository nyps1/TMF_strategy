"""
K 線與多策略回測互動網頁儀表板生成器 (generate_kline_dashboard.py)

從 services/storage/kline_service.py 讀取微台10在 SQLite 中的高頻時序資料，
聚合 11 種時間週期，並調用 services/backtest/engine.py 同步執行：
1. 布林_唐奇安 (Donchian Channel N=4 出場)
2. 布林_超級趨勢 (SuperTrend 14, 2.0 出場)
3. 布林動能突破 (動態 ATR 停利基準)

產出自給自足（Self-contained）、支援離線開啟與 TradingView 暗黑配色的互動式多圖儀表板：
1. 預設啟動【全畫面數據視圖】（Solo Data View）：
   - 隱藏上方 K 線圖，下方多策略 KPI 矩陣與淨值對比圖佔滿 100% 畫面高。
   - 頂部工具列提供一鍵展開/收合上方 K 線主圖之切換開關。
2. 淨值圖預設採用【X/Y 軸相反】佈局：
   - 橫軸 X = 累積損益 (TWD) / 帳戶淨值 (TWD)（高對比 0 軸垂直基準線）。
   - 縱軸 Y = 時間時序推進（由上向下延伸鋪開）。
   - 支援一鍵翻轉軸向（切換橫軸時間 vs 縱軸時間）。
3. 頂部工具列：支援一鍵切換當前活躍策略（唐奇安 / 超級趨勢 / ATR）。
4. 下圖四大分頁：
   - ⚔️ 雙策略淨值直觀對比：三策略量化 KPI 矩陣 + 同屏多曲線 Canvas 累計淨值對比圖。
   - 📊 單策略逐筆淨值圖：固定 Y 軸 (交易次數) × X 軸 (損益 / 淨值) 之 Canvas 圖表。
   - 📈 時間時序走勢：擬真 TradingView 0 軸分界紅綠漸層累計損益走勢圖 (BaselineSeries)。
   - 📋 交易明細清單：完整 100% 對齊 CSV 之逐筆成交稽核表格，支援一鍵點擊定位上圖 K 線。

遵循 PEP 8 與 Python Type Hints 規範。
"""

import os
import json
import logging
from typing import Dict, Any, List, Tuple
import pandas as pd

from services.storage import KlineService
from services.backtest import (
    BacktestEngine,
    BacktestConfig,
    BacktestMetrics,
    PositionSide,
    ExitReason,
    BacktestReporter
)

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


def run_single_strategy_backtest(
    df: pd.DataFrame,
    config: BacktestConfig,
    csv_filename: str
) -> Dict[str, Any]:
    """
    執行單一策略回測運算，產出指標、進出場標記、累計損益與逐筆交易清單。

    參數:
        df (pd.DataFrame): 包含 datetime 與 ts 之 5 分 K 資料。
        config (BacktestConfig): 策略回測配置。
        csv_filename (str): 匯出交易明細之 CSV 檔名。

    返回:
        Dict[str, Any]: 包含 metrics, markers, cumulative_pnl, trades, summary 之字典。
    """
    logger.info(f"正在執行策略回測 [{config.strategy_name} | exit_mode={config.exit_mode}]...")
    engine = BacktestEngine(config=config)
    metrics = engine.run(df)

    # 1. 建立建倉與平倉 Markers
    markers: List[Dict[str, Any]] = []
    for trade in metrics.trades:
        # 進場標記
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

        # 出場標記
        exit_ts = int(pd.Timestamp(trade.exit_time).timestamp())
        is_profit = trade.net_pnl >= 0
        pnl_str = f"+{trade.net_pnl:,.0f}" if is_profit else f"{trade.net_pnl:,.0f}"

        if trade.exit_reason == ExitReason.STOP_LOSS:
            reason_label = "停損"
            marker_color = "#f23645"
        elif trade.exit_reason == ExitReason.ATR_TAKE_PROFIT:
            reason_label = "ATR停利"
            marker_color = "#089981"
        elif trade.exit_reason == ExitReason.DONCHIAN_EXIT:
            reason_label = "唐奇安出場"
            marker_color = "#00e5ff"
        elif trade.exit_reason == ExitReason.SUPERTREND_EXIT:
            reason_label = "超級趨勢出場"
            marker_color = "#f0b90b"
        else:
            reason_label = "結算"
            marker_color = "#8b949e"

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

    markers.sort(key=lambda m: (m["rawTime"], 0 if m["type"] == "entry" else 1))

    # 2. 建立時間軸累計損益曲線
    cumulative_pnl: List[Dict[str, Any]] = []
    initial_cap = config.initial_capital
    for pt in metrics.equity_curve:
        ts_sec = int(pd.Timestamp(pt["ts"]).timestamp())
        cum_pnl = round(pt["equity"] - initial_cap, 2)
        cumulative_pnl.append({
            "time": ts_sec,
            "value": cum_pnl,
            "equity": round(pt["equity"], 2),
            "close": pt["close"],
            "in_position": pt["in_position"]
        })

    # 3. 序列化逐筆交易明細
    trades_list: List[Dict[str, Any]] = []
    running_cum_pnl = 0.0
    for t in metrics.trades:
        running_cum_pnl += t.net_pnl
        if t.exit_reason == ExitReason.STOP_LOSS:
            exit_reason_zh = "停損"
        elif t.exit_reason == ExitReason.ATR_TAKE_PROFIT:
            exit_reason_zh = "ATR停利"
        elif t.exit_reason == ExitReason.DONCHIAN_EXIT:
            exit_reason_zh = "唐奇安出場"
        elif t.exit_reason == ExitReason.SUPERTREND_EXIT:
            exit_reason_zh = "超級趨勢出場"
        else:
            exit_reason_zh = "結算"

        trades_list.append({
            "trade_id": t.trade_id,
            "side": t.side.value,
            "entry_time": t.entry_time,
            "entry_price": float(t.entry_price),
            "exit_time": t.exit_time,
            "exit_price": float(t.exit_price),
            "qty": t.qty,
            "exit_reason": exit_reason_zh,
            "costs": float(t.fee_total + t.tax_total),
            "net_pnl": float(t.net_pnl),
            "cum_pnl": round(running_cum_pnl, 2),
            "return_pct": float(t.return_pct),
            "equity_after": float(t.equity_after),
            "entry_ts": int(pd.Timestamp(t.entry_time).timestamp()),
            "exit_ts": int(pd.Timestamp(t.exit_time).timestamp())
        })

    # 4. 量化進階指標計算 (贏均、輸均、賺賠比)
    wins = [t.net_pnl for t in metrics.trades if t.net_pnl > 0]
    losses = [t.net_pnl for t in metrics.trades if t.net_pnl < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    net_pnl_sign = "+" if metrics.total_net_profit >= 0 else ""
    return_sign = "+" if metrics.total_return_pct >= 0 else ""
    metrics_dict = {
        "strategyName": config.strategy_name,
        "totalNetProfit": float(metrics.total_net_profit),
        "totalNetProfitStr": f"{net_pnl_sign}{metrics.total_net_profit:,.2f}",
        "totalReturnPct": float(metrics.total_return_pct),
        "totalReturnPctStr": f"{return_sign}{metrics.total_return_pct:.2f}%",
        "isProfit": metrics.total_net_profit >= 0,
        "maxDrawdown": float(metrics.max_drawdown),
        "maxDrawdownStr": f"{metrics.max_drawdown:,.2f}",
        "maxDrawdownPct": float(metrics.max_drawdown_pct),
        "maxDrawdownPctStr": f"{metrics.max_drawdown_pct:.2f}%",
        "winRatePct": float(metrics.win_rate_pct),
        "winRateStr": f"{metrics.win_rate_pct:.2f}%",
        "totalTrades": metrics.total_trades,
        "winningTrades": metrics.winning_trades,
        "losingTrades": metrics.losing_trades,
        "tradesRatioStr": f"{metrics.winning_trades}/{metrics.total_trades}",
        "profitFactor": float(metrics.profit_factor),
        "profitFactorStr": f"{metrics.profit_factor:.3f}",
        "initialCapital": float(metrics.initial_capital),
        "initialCapitalStr": f"{metrics.initial_capital:,.0f}",
        "finalEquity": float(metrics.final_equity),
        "finalEquityStr": f"{metrics.final_equity:,.2f}",
        "avgWin": float(avg_win),
        "avgWinStr": f"+{avg_win:,.1f}",
        "avgLoss": float(avg_loss),
        "avgLossStr": f"-{avg_loss:,.1f}",
        "payoffRatio": float(payoff_ratio),
        "payoffRatioStr": f"{payoff_ratio:.2f}"
    }

    # 5. 匯出 CSV 交易明細
    try:
        reporter = BacktestReporter(metrics)
        reporter.export_trades_csv(csv_filename)
        logger.info(f"已成功匯出交易明細: {csv_filename}")
    except Exception as e:
        logger.warning(f"匯出 {csv_filename} 警告: {e}")

    logger.info(
        f"策略 [{config.strategy_name}] 回測完成: 交易 {metrics.total_trades} 筆, "
        f"淨利 {metrics.total_net_profit:,.0f} 元, 勝率 {metrics.win_rate_pct:.2f}%, "
        f"PF {metrics.profit_factor:.3f}, 贏均 {avg_win:,.0f} 元, 賺賠比 {payoff_ratio:.2f}"
    )

    return {
        "metrics": metrics_dict,
        "markers": markers,
        "cumulative_pnl": cumulative_pnl,
        "trades": trades_list
    }


def run_all_strategies_backtest(bars_5m: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    同時針對 5 分 K 線執行三種出場模式之回測比較：
    1. donchian (唐奇安通道 N=4)
    2. supertrend (超級趨勢線 14, 2.0)
    3. atr (動態 ATR 停利基準)

    參數:
        bars_5m (List[Dict[str, Any]]): 5 分鐘 K 線資料列表。

    返回:
        Dict[str, Any]: 包含三大策略全部回測數據之字典。
    """
    df = pd.DataFrame(bars_5m)
    df["ts"] = pd.to_datetime(df["datetime"])

    # 策略 1: 唐奇安通道
    cfg_donchian = BacktestConfig(
        strategy_name="布林_唐奇安",
        exit_mode="donchian",
        donchian_length=4,
        bb_length=30,
        bb_mult=1.75,
        initial_capital=105150.0,
        commission_per_order=18.0,
        tax_rate=0.00002
    )
    res_donchian = run_single_strategy_backtest(df, cfg_donchian, "trades_donchian.csv")

    # 策略 2: 超級趨勢線
    cfg_supertrend = BacktestConfig(
        strategy_name="布林_超級趨勢",
        exit_mode="supertrend",
        supertrend_length=14,
        supertrend_multiplier=2.0,
        bb_length=30,
        bb_mult=1.75,
        initial_capital=105150.0,
        commission_per_order=18.0,
        tax_rate=0.00002
    )
    res_supertrend = run_single_strategy_backtest(df, cfg_supertrend, "trades_supertrend.csv")

    # 策略 3: ATR 基準
    cfg_atr = BacktestConfig(
        strategy_name="布林動能突破 (ATR基準)",
        exit_mode="atr",
        atr_length=14,
        atr_sig_length=5,
        bb_length=30,
        bb_mult=1.75,
        initial_capital=105150.0,
        commission_per_order=18.0,
        tax_rate=0.00002
    )
    res_atr = run_single_strategy_backtest(df, cfg_atr, "trades_atr.csv")

    # 複製預設 trades.csv
    try:
        import shutil
        shutil.copyfile("trades_donchian.csv", "trades.csv")
    except Exception:
        pass

    return {
        "donchian": {
            "id": "donchian",
            "name": "布林_唐奇安",
            "displayName": "布林_唐奇安 (N=4)",
            "themeColor": "#00e5ff",
            **res_donchian
        },
        "supertrend": {
            "id": "supertrend",
            "name": "布林_超級趨勢",
            "displayName": "布林_超級趨勢 (14, 2.0)",
            "themeColor": "#f0b90b",
            **res_supertrend
        },
        "atr": {
            "id": "atr",
            "name": "布林_ATR基準",
            "displayName": "布林動能突破 (ATR基準)",
            "themeColor": "#b388ff",
            **res_atr
        }
    }


def build_html_content(
    intervals_data: Dict[str, List[Dict[str, Any]]],
    multi_strategies_data: Dict[str, Any],
    standalone_js: str
) -> str:
    """
    建構支援全畫面數據視圖、X/Y 軸相反淨值對比、TradingView 暗黑風格的量化儀表板 HTML。

    參數:
        intervals_data (Dict[str, List[Dict[str, Any]]]): 11 種週期的 K 線資料字典。
        multi_strategies_data (Dict[str, Any]): 三大策略之完整回測指標、點位與時序。
        standalone_js (str): 內嵌 Lightweight Charts 函式庫程式碼 (可為空)。

    返回:
        str: 完整 HTML 頁面內容。
    """
    data_json = json.dumps(intervals_data, ensure_ascii=False)
    strategies_json = json.dumps(multi_strategies_data, ensure_ascii=False)

    script_tag = f"<script>{standalone_js}</script>" if standalone_js else (
        '<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>'
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>微台10 多策略回測與淨值直觀對比儀表板 - 永豐 Shioaji</title>
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

        /* 頂部策略選擇按鈕群組 */
        .strategy-selector-group {{
            display: flex;
            align-items: center;
            gap: 4px;
            background: #191b24;
            padding: 2px 4px;
            border-radius: 6px;
            border: 1px solid #282f42;
        }}
        .strat-btn {{
            background: transparent;
            border: 1px solid transparent;
            color: #9299a8;
            font-size: 11px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 4px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 5px;
            transition: all 0.15s;
        }}
        .strat-btn:hover {{
            color: #ffffff;
            background: #252a3a;
        }}
        .strat-btn.active {{
            background: #22293a;
            color: #ffffff;
            border-color: rgba(255, 255, 255, 0.2);
            box-shadow: 0 0 8px rgba(0, 0, 0, 0.5);
        }}
        .strat-dot {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            display: inline-block;
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
        .toggle-btn.mode-active {{
            border-color: #2962ff;
            color: #2962ff;
            background: rgba(41, 98, 255, 0.15);
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
            transition: all 0.2s ease-in-out;
        }}

        /* 上半部：K 線圖容器 (雙圖模式下佔約 52%) */
        .kline-section {{
            flex: 52;
            position: relative;
            width: 100%;
            background: #0f0f11;
            border-bottom: 1px solid #232733;
            min-height: 220px;
        }}
        #kline-container {{
            width: 100%;
            height: 100%;
        }}

        /* 全畫面數據視圖 (Solo Data View) 核心樣式：隱藏上方 K 線，下方佔滿 100% */
        .workspace-split.solo-data-mode .kline-section {{
            display: none !important;
        }}
        .workspace-split.solo-data-mode .performance-section {{
            flex: 1 1 100% !important;
            height: 100% !important;
            min-height: auto;
        }}
        .workspace-split.solo-data-mode .multi-canvas-viewport {{
            flex: 1;
            height: 100% !important;
            min-height: 480px;
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
            gap: 10px;
            font-size: 12px;
            font-weight: 500;
            background: rgba(15, 15, 17, 0.90);
            padding: 5px 12px;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.10);
            backdrop-filter: blur(6px);
        }}
        .legend-bb-group {{
            display: flex;
            align-items: center;
            gap: 4px;
            padding-left: 8px;
            border-left: 1px solid #232838;
        }}

        /* 策略參數調整視窗 (Interactive Strategy Optimizer Modal) */
        .params-modal-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(6px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 9999;
        }}
        .params-modal-overlay.open {{
            display: flex;
        }}
        .params-modal-card {{
            width: 760px;
            max-width: 95vw;
            max-height: 90vh;
            background: #141722;
            border: 1px solid #28324a;
            border-radius: 12px;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.8), 0 0 25px rgba(0, 229, 255, 0.15);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            animation: modalFadeIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }}
        @keyframes modalFadeIn {{
            from {{ opacity: 0; transform: scale(0.96); }}
            to {{ opacity: 1; transform: scale(1); }}
        }}
        .params-modal-header {{
            padding: 16px 20px;
            background: #191d2c;
            border-bottom: 1px solid #242c40;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .params-modal-title {{
            font-size: 15px;
            font-weight: 700;
            color: #ffffff;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .params-modal-close {{
            background: transparent;
            border: none;
            color: #8b949e;
            font-size: 18px;
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 4px;
            transition: all 0.15s;
        }}
        .params-modal-close:hover {{
            color: #ffffff;
            background: rgba(255, 255, 255, 0.08);
        }}
        .params-modal-body {{
            padding: 18px 20px;
            overflow-y: auto;
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 14px;
        }}
        .params-strat-selector {{
            display: flex;
            gap: 8px;
            background: #0f1118;
            padding: 4px;
            border-radius: 8px;
            border: 1px solid #222839;
        }}
        .params-strat-tab {{
            flex: 1;
            text-align: center;
            padding: 7px 10px;
            font-size: 12px;
            font-weight: 600;
            color: #8b949e;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s;
            user-select: none;
        }}
        .params-strat-tab.active {{
            background: #1e2434;
            color: #ffffff;
            box-shadow: 0 2px 6px rgba(0,0,0,0.3);
        }}
        .params-strat-tab[data-strat="donchian"].active {{
            color: #00e5ff;
            border: 1px solid rgba(0, 229, 255, 0.35);
        }}
        .params-strat-tab[data-strat="supertrend"].active {{
            color: #f0b90b;
            border: 1px solid rgba(240, 185, 11, 0.35);
        }}
        .params-strat-tab[data-strat="atr"].active {{
            color: #b388ff;
            border: 1px solid rgba(179, 136, 255, 0.35);
        }}
        .params-section {{
            background: #171b26;
            border: 1px solid #232b3d;
            border-radius: 8px;
            padding: 12px 16px;
        }}
        .params-section-title {{
            font-size: 12px;
            font-weight: 700;
            color: #d1d4dc;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .params-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px 18px;
        }}
        .param-item {{
            display: flex;
            flex-direction: column;
            gap: 5px;
        }}
        .param-label-row {{
            display: flex;
            justify-content: space-between;
            font-size: 11px;
            color: #8b949e;
        }}
        .param-val-badge {{
            color: #00e5ff;
            font-weight: 700;
        }}
        .param-input-row {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .param-range {{
            flex: 1;
            accent-color: #00e5ff;
            height: 4px;
            border-radius: 2px;
            background: #252c3e;
            cursor: pointer;
        }}
        .param-num-input {{
            width: 68px;
            background: #0f1118;
            border: 1px solid #2a334a;
            border-radius: 4px;
            color: #ffffff;
            font-size: 12px;
            font-family: monospace;
            padding: 4px 6px;
            text-align: right;
        }}
        .params-result-box {{
            background: #11141e;
            border: 1px solid #1f2738;
            border-radius: 8px;
            padding: 10px 14px;
        }}
        .params-modal-footer {{
            padding: 12px 20px;
            background: #191c28;
            border-top: 1px solid #242b3e;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .params-actions {{
            display: flex;
            gap: 10px;
        }}
        .btn-run-backtest {{
            background: linear-gradient(135deg, #00b4d8 0%, #0077b6 100%);
            color: #ffffff;
            border: none;
            padding: 8px 18px;
            font-size: 12px;
            font-weight: 700;
            border-radius: 6px;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0, 180, 216, 0.35);
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .btn-run-backtest:hover {{
            background: linear-gradient(135deg, #0096c7 0%, #03045e 100%);
            transform: translateY(-1px);
            box-shadow: 0 6px 16px rgba(0, 180, 216, 0.5);
        }}
        .btn-reset-params {{
            background: #202636;
            border: 1px solid #2c354a;
            color: #a0a6b5;
            padding: 8px 14px;
            font-size: 12px;
            font-weight: 600;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .btn-reset-params:hover {{
            color: #ffffff;
            background: #2a3248;
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
        .legend-active-strat {{
            background: rgba(0, 229, 255, 0.15);
            color: #00e5ff;
            border: 1px solid rgba(0, 229, 255, 0.3);
            padding: 1px 6px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
        }}

        /* 下半部：策略績效全覽面板 (雙圖模式下佔約 48%，全畫面模式佔 100%) */
        .performance-section {{
            flex: 48;
            display: flex;
            flex-direction: column;
            background: #0d0e12;
            width: 100%;
            min-height: 220px;
            position: relative;
        }}

        /* 1. 關鍵數據頂部標題與四欄指標 (Key Metrics Header) */
        .metrics-card {{
            padding: 8px 18px 6px 18px;
            background: #111318;
            border-bottom: 1px solid #1e222c;
            flex-shrink: 0;
        }}
        .metrics-header-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 6px;
        }}
        .metrics-title {{
            font-size: 13px;
            font-weight: 700;
            color: #ffffff;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .metrics-strat-indicator {{
            font-size: 11px;
            color: #00e5ff;
            font-weight: 600;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
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
            font-size: 15px;
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
            font-size: 12px;
            font-weight: 600;
        }}
        .metric-main-value.positive {{
            color: #089981;
        }}
        .metric-main-value.negative {{
            color: #f23645;
        }}
        .metric-main-value.neutral {{
            color: #ffffff;
        }}
        .metric-main-value .sub-ratio {{
            font-size: 12px;
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
        .perf-tab-list {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .perf-tab {{
            font-size: 12px;
            font-weight: 600;
            color: #787b86;
            cursor: pointer;
            padding: 4px 10px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            gap: 4px;
            transition: all 0.15s;
        }}
        .perf-tab:hover {{
            color: #d1d4dc;
            background: #181c26;
        }}
        .perf-tab.active {{
            color: #ffffff;
            background: #242938;
            border: 1px solid #363d54;
            box-shadow: 0 2px 6px rgba(0,0,0,0.4);
        }}
        .perf-tab.highlight {{
            color: #00e5ff;
            border-color: rgba(0, 229, 255, 0.4);
        }}
        .perf-tools {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .perf-tool-btn {{
            background: #191b24;
            border: 1px solid #282f42;
            color: #a0a6b5;
            cursor: pointer;
            font-size: 11px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 4px;
            transition: all 0.15s;
        }}
        .perf-tool-btn:hover {{
            color: #ffffff;
            background: #252a3a;
            border-color: #3b4560;
        }}
        .perf-tool-btn.highlight {{
            color: #00e5ff;
            border-color: rgba(0, 229, 255, 0.4);
        }}

        /* 3. 分頁 1: ⚔️ 雙策略淨值直觀對比視圖面板 */
        .multi-compare-wrapper {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            background: #0d0e12;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}

        /* 多策略 KPI 橫向量化對比矩陣卡片 */
        .multi-kpi-matrix {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 12px;
            padding: 10px 18px 8px 18px;
            background: #12141c;
            border-bottom: 1px solid #1e222e;
            flex-shrink: 0;
        }}
        .kpi-card {{
            background: #161924;
            border-radius: 6px;
            padding: 8px 12px;
            border: 1px solid #262c3e;
            display: flex;
            flex-direction: column;
            gap: 6px;
            transition: transform 0.15s, border-color 0.15s;
        }}
        .kpi-card:hover {{
            transform: translateY(-1px);
        }}
        .kpi-card.card-donchian {{
            border-top: 3px solid #00e5ff;
        }}
        .kpi-card.card-supertrend {{
            border-top: 3px solid #f0b90b;
        }}
        .kpi-card.card-atr {{
            border-top: 3px solid #b388ff;
        }}
        .kpi-card-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .kpi-card-title {{
            font-size: 13px;
            font-weight: 700;
            color: #ffffff;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .kpi-card-badge {{
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 3px;
            font-weight: 600;
        }}
        .kpi-card-body {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 6px;
        }}
        .kpi-sub-item {{
            display: flex;
            flex-direction: column;
        }}
        .kpi-sub-label {{
            font-size: 10px;
            color: #787b86;
        }}
        .kpi-sub-val {{
            font-size: 12px;
            font-weight: 700;
            color: #e1e4ea;
        }}
        .kpi-sub-val.win {{
            color: #089981 !important;
        }}
        .kpi-sub-val.loss {{
            color: #f23645 !important;
        }}
        .kpi-sub-val.highlight {{
            color: #00e5ff !important;
        }}

        /* 對比 Canvas 容器 */
        .multi-canvas-viewport {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            overflow: hidden;
            background: #0d0e12;
        }}
        #multi-compare-canvas {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            display: block;
        }}

        /* 多策略懸浮 Tooltip */
        .multi-tooltip {{
            position: absolute;
            background: rgba(18, 21, 30, 0.95);
            border: 1px solid #313a52;
            border-radius: 6px;
            padding: 10px 14px;
            pointer-events: none;
            display: none;
            z-index: 50;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.8);
            font-size: 11px;
            min-width: 250px;
            backdrop-filter: blur(6px);
        }}
        .mt-header {{
            font-size: 11px;
            color: #9299a8;
            margin-bottom: 6px;
            padding-bottom: 4px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            display: flex;
            justify-content: space-between;
        }}
        .mt-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin: 4px 0;
        }}
        .mt-strat-label {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-weight: 600;
        }}
        .mt-strat-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }}
        .mt-strat-val {{
            font-weight: 700;
        }}
        .mt-leader-tag {{
            font-size: 9px;
            background: rgba(240, 185, 11, 0.2);
            color: #f0b90b;
            padding: 1px 4px;
            border-radius: 2px;
            margin-left: 4px;
        }}

        /* 圖例核取控制方塊 */
        .multi-legend-bar {{
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
        .legend-checkbox-group {{
            display: flex;
            align-items: center;
            gap: 14px;
        }}
        .legend-check-item {{
            display: flex;
            align-items: center;
            gap: 6px;
            cursor: pointer;
            font-weight: 600;
        }}
        .legend-check-item input[type="checkbox"] {{
            cursor: pointer;
            accent-color: #089981;
        }}

        /* 4. 分頁 2: 單策略逐筆淨值圖容器 */
        .trade-equity-container-wrapper {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            background: #0d0e12;
            display: none;
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
        }}
        .trade-tooltip {{
            position: absolute;
            background: rgba(18, 21, 30, 0.95);
            border: 1px solid #313a52;
            border-radius: 6px;
            padding: 10px 14px;
            pointer-events: none;
            display: none;
            z-index: 50;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.8);
            font-size: 11px;
            min-width: 240px;
            backdrop-filter: blur(6px);
        }}
        .tt-title {{
            font-weight: 700;
            color: #ffffff;
            margin-bottom: 6px;
            padding-bottom: 4px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            display: flex;
            justify-content: space-between;
        }}
        .tt-row {{
            display: flex;
            justify-content: space-between;
            margin: 3px 0;
        }}
        .tt-label {{
            color: #787b86;
        }}
        .tt-val {{
            font-weight: 600;
            color: #d1d4dc;
        }}
        .tt-val.win {{
            color: #089981 !important;
        }}
        .tt-val.loss {{
            color: #f23645 !important;
        }}
        .tt-footer {{
            margin-top: 6px;
            padding-top: 4px;
            border-top: 1px dashed rgba(255, 255, 255, 0.08);
            color: #089981;
            font-size: 10px;
            text-align: center;
        }}

        /* 5. 分頁 3: 時間軸累計損益圖表容器 (BaselineSeries) */
        .perf-chart-container-wrapper {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            display: none;
        }}
        #perf-chart-container {{
            width: 100%;
            height: 100%;
        }}
        .perf-legend {{
            position: absolute;
            top: 10px;
            left: 16px;
            z-index: 5;
            pointer-events: none;
            font-size: 12px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(15, 15, 17, 0.8);
            padding: 4px 10px;
            border-radius: 4px;
            border: 1px solid rgba(255, 255, 255, 0.06);
            backdrop-filter: blur(4px);
        }}
        .perf-legend-val {{
            color: #089981;
        }}
        .pnl-ribbon-bar {{
            position: absolute;
            bottom: 0;
            left: 0;
            width: 100%;
            height: 4px;
            display: flex;
            z-index: 4;
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

        /* 6. 分頁 4: 交易明細清單表格 */
        .trades-table-container-wrapper {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            overflow: auto;
            background: #0d0e12;
            display: none;
        }}
        .trades-table {{
            width: 100%;
            min-width: 1040px;
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
        .trades-table tr.selected-trade-row {{
            background: rgba(41, 98, 255, 0.18) !important;
            border-left: 3px solid #2962ff !important;
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
            border: 1px solid #2e3448;
            color: #2962ff;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .btn-focus-trade:hover {{
            background: #2962ff;
            color: #ffffff;
            border-color: #2962ff;
        }}

        /* 交易特寫聚焦浮動橫幅 */
        .trade-focus-banner {{
            position: absolute;
            top: 48px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(20, 24, 36, 0.95);
            border: 1px solid #2962ff;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6);
            border-radius: 6px;
            padding: 6px 14px;
            z-index: 25;
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 12px;
            color: #d1d4dc;
            backdrop-filter: blur(8px);
        }}
        .trade-focus-banner .focus-badge {{
            color: #ffffff;
            padding: 2px 7px;
            border-radius: 4px;
            font-weight: 700;
            font-size: 11px;
        }}
        .trade-focus-banner .btn-close-focus {{
            background: #2a3042;
            border: 1px solid #3d4660;
            color: #9299a8;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .trade-focus-banner .btn-close-focus:hover {{
            background: #f23645;
            border-color: #f23645;
            color: #ffffff;
        }}

        /* 浮水印與狀態指示徽章 */
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
        .live-badge {{
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            user-select: none;
        }}
        .live-badge.offline {{
            background: rgba(242, 54, 69, 0.12);
            color: #f23645;
            border: 1px solid rgba(242, 54, 69, 0.3);
        }}
        .live-badge.online {{
            background: rgba(8, 153, 129, 0.15);
            color: #089981;
            border: 1px solid rgba(8, 153, 129, 0.35);
        }}
        .live-indicator-dot {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background-color: currentColor;
            box-shadow: 0 0 6px currentColor;
        }}

        /* 即時通知 Toast */
        .toast-container {{
            position: fixed;
            top: 60px;
            right: 20px;
            z-index: 200;
            display: flex;
            flex-direction: column;
            gap: 8px;
            pointer-events: none;
        }}
        .toast-card {{
            background: rgba(17, 20, 28, 0.95);
            border-left: 4px solid #2962ff;
            border-radius: 4px;
            padding: 10px 14px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
            color: #ffffff;
            font-size: 12px;
            min-width: 240px;
            backdrop-filter: blur(6px);
            animation: slide-in 0.25s ease-out;
            pointer-events: auto;
        }}
        .toast-card.success {{
            border-left-color: #089981;
        }}
        .toast-card.danger {{
            border-left-color: #f23645;
        }}
        @keyframes slide-in {{
            from {{ transform: translateX(100%); opacity: 0; }}
            to {{ transform: translateX(0); opacity: 1; }}
        }}
    </style>
</head>
<body>

    <!-- 頂部控制列 -->
    <div class="top-toolbar">
        <div class="symbol-badge">
            <span class="symbol-name">微型台指期 10</span>
            <span class="symbol-code">TMFJ6</span>

            <!-- 策略即時切換按鈕群組 -->
            <div class="strategy-selector-group">
                <button class="strat-btn active" data-strat="donchian" title="切換為唐奇安通道出場 (N=4)">
                    <span class="strat-dot" style="background:#00e5ff;"></span>
                    <span>布林_唐奇安</span>
                </button>
                <button class="strat-btn" data-strat="supertrend" title="切換為超級趨勢線出場 (14, 2.0)">
                    <span class="strat-dot" style="background:#f0b90b;"></span>
                    <span>布林_超級趨勢</span>
                </button>
                <button class="strat-btn" data-strat="atr" title="切換為動態 ATR 停利 (基準對照)">
                    <span class="strat-dot" style="background:#b388ff;"></span>
                    <span>布林_ATR基準</span>
                </button>
            </div>
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
            <!-- 全畫面純數據視圖 ⇄ 雙圖切換按鈕 -->
            <button class="toggle-btn mode-active" id="btn-toggle-layout" title="切換 全畫面數據視圖 ⇄ K線雙圖模式">
                <span id="txt-toggle-layout">🗖 數據全畫面 (作用中)</span>
            </button>
            <div id="live-status-badge" class="live-badge offline" title="即時行情 WebSocket 串流狀態">
                <span class="live-indicator-dot"></span>
                <span id="live-status-text">串流: 離線</span>
            </div>
            <button class="toggle-btn active" id="toggle-bb-btn" title="切換顯示布林通道上中下軌">
                <span id="txt-toggle-bb">🎯 布林軌道: 顯示</span>
            </button>
            <button class="toggle-btn active" id="toggle-markers-btn">
                <span>📍 交易標記: 顯示</span>
            </button>
            <button class="reset-btn" id="reset-zoom-btn">適配畫面 (Fit)</button>
        </div>
    </div>

    <!-- 即時通知容器 -->
    <div class="toast-container" id="toast-container"></div>

    <!-- 主工作區：預設加入 solo-data-mode 類別 (全畫面只有數據部分) -->
    <div class="workspace-split solo-data-mode" id="workspace-split">

        <!-- 上圖：K 線圖主圖 (全畫面模式下自動隱藏，點擊按鈕可一鍵展開) -->
        <div class="kline-section" id="kline-section">
            <div class="ohlc-legend" id="ohlc-legend">
                <div class="legend-item legend-active-strat" id="legend-strat-tag">布林_唐奇安</div>
                <div class="legend-item legend-time" id="legend-time">--</div>
                <div class="legend-item"><span class="legend-label">開</span><span class="legend-value" id="legend-open">--</span></div>
                <div class="legend-item"><span class="legend-label">高</span><span class="legend-value" id="legend-high">--</span></div>
                <div class="legend-item"><span class="legend-label">低</span><span class="legend-value" id="legend-low">--</span></div>
                <div class="legend-item"><span class="legend-label">收</span><span class="legend-value" id="legend-close">--</span></div>
                <div class="legend-item"><span class="legend-label">漲跌</span><span class="legend-value" id="legend-change">--</span></div>
                <div class="legend-item"><span class="legend-label">量</span><span class="legend-value" id="legend-volume">--</span></div>
                <div class="legend-item legend-bb-group" id="legend-bb-group">
                    <span class="legend-label" style="color:#26a69a;">BB上</span><span class="legend-value" id="legend-bb-upper" style="color:#26a69a;">--</span>
                    <span class="legend-label" style="color:#ffb300; margin-left:4px;">中</span><span class="legend-value" id="legend-bb-middle" style="color:#ffb300;">--</span>
                    <span class="legend-label" style="color:#ef5350; margin-left:4px;">下</span><span class="legend-value" id="legend-bb-lower" style="color:#ef5350;">--</span>
                </div>
            </div>

            <!-- 交易特寫聚焦浮動橫幅 -->
            <div id="trade-focus-banner" class="trade-focus-banner" style="display: none;">
                <div id="trade-focus-content" style="display: flex; align-items: center; gap: 10px;"></div>
                <button class="btn-close-focus" onclick="clearTradeFocus()">✕ 恢復全景</button>
            </div>

            <div id="kline-container"></div>
            <div class="watermark">TMF202610 • MULTI-STRATEGY COMPARISON</div>
        </div>

        <!-- 下半部：策略績效全覽面板 (全畫面模式下佔滿 100% 畫面高) -->
        <div class="performance-section" id="performance-section">

            <!-- 關鍵數據卡片 (隨當前策略切換聯動) -->
            <div class="metrics-card">
                <div class="metrics-header-row">
                    <div class="metrics-title">
                        <span>關鍵數據全覽</span>
                        <span class="metrics-strat-indicator" id="current-strat-indicator">當前活躍策略: 布林_唐奇安</span>
                    </div>
                </div>
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

                    <!-- 獲利交易與勝率 -->
                    <div class="metric-col">
                        <div class="metric-label">獲利交易 (勝率)</div>
                        <div class="metric-main-value neutral">
                            <span id="metric-win-rate">--</span>
                            <span class="sub-ratio" id="metric-win-ratio">--</span>
                        </div>
                    </div>

                    <!-- 獲利因子與賺賠比 -->
                    <div class="metric-col">
                        <div class="metric-label">獲利因子 / 賺賠比</div>
                        <div class="metric-main-value neutral">
                            <span id="metric-profit-factor">--</span>
                            <span class="sub-ratio" id="metric-payoff-ratio">--</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 績效圖表頂部功能切換列 (四個分頁) -->
            <div class="perf-header-bar">
                <div class="perf-title-group">
                    <div class="perf-title">績效分析儀表板</div>
                    <div class="perf-tab-list">
                        <div class="perf-tab active highlight" id="tab-multi-compare">⚔️ 雙策略淨值直觀對比</div>
                        <div class="perf-tab" id="tab-trade-equity">📊 單策略逐筆淨值圖</div>
                        <div class="perf-tab" id="tab-cum-pnl">📈 時間時序走勢 (Time-based)</div>
                        <div class="perf-tab" id="tab-trades-table">📋 交易明細清單</div>
                    </div>
                </div>
                <div class="perf-tools">
                    <button class="perf-tool-btn highlight" id="btn-open-params-modal" title="微調策略參數並即時重新撮合回測">⚙️ 策略參數調校與即時回測</button>
                    <button class="perf-tool-btn" id="btn-toggle-compare-mode" title="切換累積損益或帳戶淨值">切換淨值/損益</button>
                    <button class="perf-tool-btn" id="btn-fit-chart" title="適配全屏">⛶ 適配縮放</button>
                </div>
            </div>

            <!-- 1. 分頁 1: ⚔️ 雙策略淨值直觀對比視圖面板 (預設顯示) -->
            <div class="multi-compare-wrapper" id="multi-compare-wrapper">
                <!-- 頂部多策略橫向 KPI 矩陣卡片 -->
                <div class="multi-kpi-matrix" id="multi-kpi-matrix">
                    <!-- 卡片 1: 唐奇安 -->
                    <div class="kpi-card card-donchian">
                        <div class="kpi-card-header">
                            <div class="kpi-card-title">
                                <span style="color:#00e5ff;">●</span>
                                <span>布林_唐奇安 (N=4)</span>
                            </div>
                            <span class="kpi-card-badge" style="background:rgba(0,229,255,0.15); color:#00e5ff;">突破極值追蹤</span>
                        </div>
                        <div class="kpi-card-body">
                            <div class="kpi-sub-item"><span class="kpi-sub-label">總淨利</span><span class="kpi-sub-val win" id="kpi-donchian-pnl">+5,878</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">勝率</span><span class="kpi-sub-val" id="kpi-donchian-winrate">37.93%</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">獲利因子</span><span class="kpi-sub-val" id="kpi-donchian-pf">1.310</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">贏單平均</span><span class="kpi-sub-val highlight" id="kpi-donchian-avgwin">+1,137</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">最大回撤</span><span class="kpi-sub-val loss" id="kpi-donchian-mdd">-7,410</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">總筆數</span><span class="kpi-sub-val" id="kpi-donchian-trades">58 筆</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">輸單平均</span><span class="kpi-sub-val" id="kpi-donchian-avgloss">-532</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">賺賠比</span><span class="kpi-sub-val highlight" id="kpi-donchian-payoff">2.14</span></div>
                        </div>
                    </div>

                    <!-- 卡片 2: 超級趨勢 -->
                    <div class="kpi-card card-supertrend">
                        <div class="kpi-card-header">
                            <div class="kpi-card-title">
                                <span style="color:#f0b90b;">●</span>
                                <span>布林_超級趨勢 (14, 2.0)</span>
                            </div>
                            <span class="kpi-card-badge" style="background:rgba(240,185,11,0.15); color:#f0b90b;">單向不可逆軌道</span>
                        </div>
                        <div class="kpi-card-body">
                            <div class="kpi-sub-item"><span class="kpi-sub-label">總淨利</span><span class="kpi-sub-val win" id="kpi-supertrend-pnl">+3,916</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">勝率</span><span class="kpi-sub-val" id="kpi-supertrend-winrate">36.36%</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">獲利因子</span><span class="kpi-sub-val" id="kpi-supertrend-pf">1.200</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">贏單平均</span><span class="kpi-sub-val highlight" id="kpi-supertrend-avgwin">+1,160</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">最大回撤</span><span class="kpi-sub-val loss" id="kpi-supertrend-mdd">-9,274</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">總筆數</span><span class="kpi-sub-val" id="kpi-supertrend-trades">55 筆</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">輸單平均</span><span class="kpi-sub-val" id="kpi-supertrend-avgloss">-551</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">賺賠比</span><span class="kpi-sub-val highlight" id="kpi-supertrend-payoff">2.11</span></div>
                        </div>
                    </div>

                    <!-- 卡片 3: ATR 基準 -->
                    <div class="kpi-card card-atr">
                        <div class="kpi-card-header">
                            <div class="kpi-card-title">
                                <span style="color:#b388ff;">●</span>
                                <span>布林_ATR基準 (14, 5)</span>
                            </div>
                            <span class="kpi-card-badge" style="background:rgba(179,136,255,0.15); color:#b388ff;">動態均線停利 (對照)</span>
                        </div>
                        <div class="kpi-card-body">
                            <div class="kpi-sub-item"><span class="kpi-sub-label">總淨利</span><span class="kpi-sub-val win" id="kpi-atr-pnl">+13,838</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">勝率</span><span class="kpi-sub-val" id="kpi-atr-winrate">48.48%</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">獲利因子</span><span class="kpi-sub-val" id="kpi-atr-pf">2.090</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">贏單平均</span><span class="kpi-sub-val" id="kpi-atr-avgwin">+829</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">最大回撤</span><span class="kpi-sub-val loss" id="kpi-atr-mdd">-2,980</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">總筆數</span><span class="kpi-sub-val" id="kpi-atr-trades">66 筆</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">輸單平均</span><span class="kpi-sub-val" id="kpi-atr-avgloss">-373</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">賺賠比</span><span class="kpi-sub-val" id="kpi-atr-payoff">2.22</span></div>
                        </div>
                    </div>
                </div>

                <!-- 圖例控制方塊 -->
                <div class="multi-legend-bar">
                    <div class="legend-checkbox-group">
                        <label class="legend-check-item" style="color:#00e5ff;">
                            <input type="checkbox" id="chk-donchian" checked>
                            <span>唐奇安通道 (青藍)</span>
                        </label>
                        <label class="legend-check-item" style="color:#f0b90b;">
                            <input type="checkbox" id="chk-supertrend" checked>
                            <span>超級趨勢線 (金黃)</span>
                        </label>
                        <label class="legend-check-item" style="color:#b388ff;">
                            <input type="checkbox" id="chk-atr" checked>
                            <span>ATR 基準 (紫色)</span>
                        </label>
                    </div>
                    <div style="color:#787b86;">
                        <span id="txt-axis-hint">💡 坐標軸向：橫軸 X = 時間時序推進 ｜ 縱軸 Y = 累積損益 (TWD)，滑鼠 Hover 查看即時對比數值</span>
                    </div>
                </div>

                <!-- Canvas 多策略繪圖視窗 -->
                <div class="multi-canvas-viewport" id="multi-canvas-viewport">
                    <canvas id="multi-compare-canvas"></canvas>
                    <div class="multi-tooltip" id="multi-tooltip"></div>
                </div>
            </div>

            <!-- 2. 分頁 2: 單策略逐筆淨值圖 (固定Y軸次數 × X軸損益) -->
            <div class="trade-equity-container-wrapper" id="trade-equity-wrapper">
                <div class="trade-chart-header-info">
                    <span class="info-tag mode-tag" id="current-mode-tag">當前視圖: 縱軸 Y = 交易次數 ｜ 橫軸 X = 累積損益 (TWD) & 帳戶淨值</span>
                    <span class="info-tag tip-tag">💡 Hover 檢視明細，點擊任一交易節點可定位上方 K 線圖</span>
                </div>
                <div class="canvas-scroll-viewport" id="canvas-scroll-viewport">
                    <canvas id="trade-equity-canvas"></canvas>
                </div>
                <div class="trade-tooltip" id="trade-tooltip"></div>
            </div>

            <!-- 3. 分頁 3: 時間軸累計損益圖表容器 (BaselineSeries) -->
            <div class="perf-chart-container-wrapper" id="perf-chart-wrapper">
                <div class="perf-legend" id="perf-legend">
                    <span style="color:#787b86;">累計損益:</span>
                    <span class="perf-legend-val" id="perf-legend-val">--</span>
                    <span style="color:#787b86; margin-left: 8px;">時間:</span>
                    <span id="perf-legend-time" style="color:#9299a8;">--</span>
                </div>
                <div id="perf-chart-container"></div>
            </div>

            <!-- 4. 分頁 4: 交易明細清單表格 -->
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

    <!-- 策略參數調校與即時回測視窗 (Interactive Strategy Optimizer Modal) -->
    <div class="params-modal-overlay" id="params-modal-overlay">
        <div class="params-modal-card">
            <div class="params-modal-header">
                <div class="params-modal-title">
                    <span>⚙️ 策略參數調校與即時回測</span>
                    <span style="font-size:11px; font-weight:normal; color:#8b949e;">(0毫秒極速撮合 3,947 根 K 棒，即時刷新淨值與布林軌道)</span>
                </div>
                <button class="params-modal-close" id="btn-close-params-modal" title="關閉視窗">✕</button>
            </div>
            <div class="params-modal-body">
                <!-- 策略選擇標籤 -->
                <div class="params-strat-selector">
                    <div class="params-strat-tab active" data-strat="donchian">布林_唐奇安 (突破極值追蹤)</div>
                    <div class="params-strat-tab" data-strat="supertrend">布林_超級趨勢 (單向不可逆)</div>
                    <div class="params-strat-tab" data-strat="atr">布林_ATR基準 (動態均線對照)</div>
                </div>

                <!-- 區塊一：進場條件 (布林動能突破 + 1/4 拉回確認) -->
                <div class="params-section">
                    <div class="params-section-title">
                        <span>🎯 進場型態條件 (布林通道突破與拉回濾網)</span>
                    </div>
                    <div class="params-grid">
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>布林通道週期 (BB Length)</span>
                                <span class="param-val-badge" id="val-badge-bb-len">30</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-bb-len" min="10" max="60" step="1" value="30">
                                <input type="number" class="param-num-input" id="num-bb-len" min="10" max="60" step="1" value="30">
                            </div>
                        </div>
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>布林標準差倍數 (BB Multiplier)</span>
                                <span class="param-val-badge" id="val-badge-bb-mult">1.75</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-bb-mult" min="1.0" max="3.0" step="0.05" value="1.75">
                                <input type="number" class="param-num-input" id="num-bb-mult" min="1.0" max="3.0" step="0.05" value="1.75">
                            </div>
                        </div>
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>拉回確認比例 (Pullback Ratio)</span>
                                <span class="param-val-badge" id="val-badge-pullback">0.25 (1/4 K棒)</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-pullback" min="0.10" max="0.50" step="0.05" value="0.25">
                                <input type="number" class="param-num-input" id="num-pullback" min="0.10" max="0.50" step="0.05" value="0.25">
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 區塊二：動態追蹤出場條件 (依選中策略動態顯示) -->
                <div class="params-section">
                    <div class="params-section-title">
                        <span>🛡️ 動態追蹤出場機制 (<span id="txt-exit-strat-name">布林_唐奇安</span>)</span>
                    </div>

                    <!-- 唐奇安專屬參數 -->
                    <div class="params-grid strat-exit-panel" id="panel-exit-donchian">
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>唐奇安通道週期 (Donchian Length N)</span>
                                <span class="param-val-badge" id="val-badge-donchian-len">4</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-donchian-len" min="2" max="30" step="1" value="4">
                                <input type="number" class="param-num-input" id="num-donchian-len" min="2" max="30" step="1" value="4">
                            </div>
                        </div>
                        <div class="param-item" style="justify-content:center; font-size:11px; color:#8b949e; line-height:1.5;">
                            💡 說明：多單在 5 分 K 收盤跌破前 N 根最低價平倉；空單在收盤突破前 N 根最高價平倉。
                        </div>
                    </div>

                    <!-- 超級趨勢專屬參數 -->
                    <div class="params-grid strat-exit-panel" id="panel-exit-supertrend" style="display:none;">
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>SuperTrend ATR 週期 (Length)</span>
                                <span class="param-val-badge" id="val-badge-st-len">14</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-st-len" min="5" max="30" step="1" value="14">
                                <input type="number" class="param-num-input" id="num-st-len" min="5" max="30" step="1" value="14">
                            </div>
                        </div>
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>SuperTrend 乘數 (Multiplier)</span>
                                <span class="param-val-badge" id="val-badge-st-mult">2.0</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-st-mult" min="1.0" max="5.0" step="0.1" value="2.0">
                                <input type="number" class="param-num-input" id="num-st-mult" min="1.0" max="5.0" step="0.1" value="2.0">
                            </div>
                        </div>
                    </div>

                    <!-- ATR 基準專屬參數 -->
                    <div class="params-grid strat-exit-panel" id="panel-exit-atr" style="display:none;">
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>ATR 週期 (ATR Length)</span>
                                <span class="param-val-badge" id="val-badge-atr-len">14</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-atr-len" min="5" max="30" step="1" value="14">
                                <input type="number" class="param-num-input" id="num-atr-len" min="5" max="30" step="1" value="14">
                            </div>
                        </div>
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>ATR 訊號平滑週期 (Signal MA)</span>
                                <span class="param-val-badge" id="val-badge-atr-sig">5</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-atr-sig" min="2" max="20" step="1" value="5">
                                <input type="number" class="param-num-input" id="num-atr-sig" min="2" max="20" step="1" value="5">
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 區塊三：資金與交易成本 -->
                <div class="params-section">
                    <div class="params-section-title">
                        <span>💰 資金槓桿與撮合摩擦成本</span>
                    </div>
                    <div class="params-grid">
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>最大槓桿倍數 (Max Leverage)</span>
                                <span class="param-val-badge" id="val-badge-leverage">7.0x</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-leverage" min="1.0" max="10.0" step="0.5" value="7.0">
                                <input type="number" class="param-num-input" id="num-leverage" min="1.0" max="10.0" step="0.5" value="7.0">
                            </div>
                        </div>
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>單邊手續費 (Commission TWD)</span>
                                <span class="param-val-badge" id="val-badge-commission">18 元</span>
                            </div>
                            <div class="param-input-row">
                                <input type="number" class="param-num-input" id="num-commission" min="0" max="100" step="1" value="18" style="width:100%;">
                            </div>
                        </div>
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>預期單邊滑價 (Slippage Points)</span>
                                <span class="param-val-badge" id="val-badge-slippage">0.0 點</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-slippage" min="0.0" max="5.0" step="0.5" value="0.0">
                                <input type="number" class="param-num-input" id="num-slippage" min="0.0" max="5.0" step="0.5" value="0.0">
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 回測即時績效摘要欄 -->
                <div class="params-result-box" id="params-result-box">
                    <div style="font-weight:700; color:#00e5ff; display:flex; align-items:center; gap:6px;">
                        <span>📊 回測績效預覽：</span>
                        <span id="res-preview-title">布林_唐奇安</span>
                        <span id="res-preview-time" style="font-size:11px; color:#8b949e; margin-left:auto;"></span>
                    </div>
                    <div style="display:flex; gap:16px; flex-wrap:wrap; font-size:12px; margin-top:6px;">
                        <div>總淨利: <b id="res-preview-pnl" style="color:#00e5ff;">--</b></div>
                        <div>勝率: <b id="res-preview-winrate">--</b></div>
                        <div>獲利因子: <b id="res-preview-pf">--</b></div>
                        <div>總交易: <b id="res-preview-trades">--</b></div>
                        <div>最大回撤: <b id="res-preview-mdd" style="color:#ef5350;">--</b></div>
                        <div>賺賠比: <b id="res-preview-payoff">--</b></div>
                    </div>
                </div>
            </div>
            <div class="params-modal-footer">
                <div class="params-actions">
                    <button class="btn-reset-params" id="btn-reset-params">🔄 重設為初始預設值</button>
                </div>
                <div class="params-actions">
                    <button class="btn-run-backtest" id="btn-run-backtest">⚡ 立即執行回測 (Run Dynamic Backtest)</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        // 資料載入：三大策略完整數據集合與 11 種週期 K 線
        const KLINE_DATA = {data_json};
        const STRATEGIES_DATA = {strategies_json};

        let currentInterval = '5m';
        let currentStrategyKey = 'donchian'; // 預設顯示唐奇安通道策略
        let showMarkers = true;
        let isSoloDataMode = true; // 預設全畫面數據模式

        // 圖表物件
        let klineChart = null;
        let candlestickSeries = null;
        let volumeSeries = null;
        let markersPrimitive = null;

        // 布林通道 (Upper, Middle, Lower)
        let bbUpperSeries = null;
        let bbMiddleSeries = null;
        let bbLowerSeries = null;
        let showBollingerBands = true;
        let currentBBConfig = {{
            length: 30,
            mult: 1.75
        }};
        window.bbLookupMap = new Map();

        let perfChart = null;
        let baselineSeries = null;
        let isSyncingRange = false;

        // DOM 元素快取
        const workspaceSplit = document.getElementById('workspace-split');
        const klineSection = document.getElementById('kline-section');
        const klineContainer = document.getElementById('kline-container');
        const perfContainer = document.getElementById('perf-chart-container');
        const perfChartWrapper = document.getElementById('perf-chart-wrapper');
        const tradesTableWrapper = document.getElementById('trades-table-wrapper');
        const tradeEquityWrapper = document.getElementById('trade-equity-wrapper');
        const multiCompareWrapper = document.getElementById('multi-compare-wrapper');

        const tabMultiCompare = document.getElementById('tab-multi-compare');
        const tabTradeEquity = document.getElementById('tab-trade-equity');
        const tabCumPnl = document.getElementById('tab-cum-pnl');
        const tabTradesTable = document.getElementById('tab-trades-table');

        const tradeEquityCanvas = document.getElementById('trade-equity-canvas');
        const canvasScrollViewport = document.getElementById('canvas-scroll-viewport');
        const tradeTooltip = document.getElementById('trade-tooltip');

        const multiCanvas = document.getElementById('multi-compare-canvas');
        const multiViewport = document.getElementById('multi-canvas-viewport');
        const multiTooltip = document.getElementById('multi-tooltip');
        const txtAxisHint = document.getElementById('txt-axis-hint');

        const legendTime = document.getElementById('legend-time');
        const legendOpen = document.getElementById('legend-open');
        const legendHigh = document.getElementById('legend-high');
        const legendLow = document.getElementById('legend-low');
        const legendClose = document.getElementById('legend-close');
        const legendChange = document.getElementById('legend-change');
        const legendVolume = document.getElementById('legend-volume');
        const legendStratTag = document.getElementById('legend-strat-tag');
        const currentStratIndicator = document.getElementById('current-strat-indicator');

        // 布林與參數調校 DOM 快取
        const legendBbUpper = document.getElementById('legend-bb-upper');
        const legendBbMiddle = document.getElementById('legend-bb-middle');
        const legendBbLower = document.getElementById('legend-bb-lower');
        const legendBbGroup = document.getElementById('legend-bb-group');
        const btnToggleBb = document.getElementById('toggle-bb-btn');
        const txtToggleBb = document.getElementById('txt-toggle-bb');

        const paramsModalOverlay = document.getElementById('params-modal-overlay');
        const btnOpenParamsModal = document.getElementById('btn-open-params-modal');
        const btnCloseParamsModal = document.getElementById('btn-close-params-modal');
        const btnRunBacktest = document.getElementById('btn-run-backtest');
        const btnResetParams = document.getElementById('btn-reset-params');

        const perfLegendVal = document.getElementById('perf-legend-val');
        const perfLegendTime = document.getElementById('perf-legend-time');
        const pnlRibbonBar = document.getElementById('pnl-ribbon-bar');

        let tradeEquityChart = null;
        let multiCompareChart = null;

        // 取得當前活躍策略之完整資料物件
        function getActiveStrategy() {{
            return STRATEGIES_DATA[currentStrategyKey] || STRATEGIES_DATA['donchian'];
        }}

        // 渲染關鍵數據面板數值 (與當前活躍策略聯動)
        function renderKeyMetrics() {{
            const strat = getActiveStrategy();
            const m = strat.metrics;
            const pnlCol = document.getElementById('metric-total-pnl');
            const pnlVal = document.getElementById('metric-pnl-val');
            const returnPct = document.getElementById('metric-return-pct');

            pnlVal.textContent = m.totalNetProfitStr;
            returnPct.textContent = m.totalReturnPctStr;
            pnlCol.className = m.isProfit ? 'metric-main-value positive' : 'metric-main-value negative';

            document.getElementById('metric-mdd-val').textContent = m.maxDrawdownStr;
            document.getElementById('metric-mdd-pct').textContent = m.maxDrawdownPctStr;
            document.getElementById('metric-win-rate').textContent = m.winRateStr;
            document.getElementById('metric-win-ratio').textContent = m.tradesRatioStr;
            document.getElementById('metric-profit-factor').textContent = m.profitFactorStr;
            document.getElementById('metric-payoff-ratio').textContent = `賺賠比 ${{m.payoffRatioStr}}`;

            if (currentStratIndicator) {{
                currentStratIndicator.textContent = `當前活躍策略: ${{strat.displayName}}`;
                currentStratIndicator.style.color = strat.themeColor;
            }}
            if (legendStratTag) {{
                legendStratTag.textContent = strat.name;
                legendStratTag.style.color = strat.themeColor;
                legendStratTag.style.borderColor = strat.themeColor;
            }}
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
            window.klineChart = klineChart;

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

            // 建立布林通道上中下軌線條
            if (typeof klineChart.addSeries === 'function' && window.LightweightCharts && window.LightweightCharts.LineSeries) {{
                bbUpperSeries = klineChart.addSeries(window.LightweightCharts.LineSeries, {{
                    color: '#26a69a',
                    lineWidth: 1.5,
                    lineStyle: 0,
                    priceLineVisible: false,
                    lastValueVisible: true,
                    title: 'BB 上軌'
                }});
                bbMiddleSeries = klineChart.addSeries(window.LightweightCharts.LineSeries, {{
                    color: '#ffb300',
                    lineWidth: 1,
                    lineStyle: 2,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    title: 'BB 中軌'
                }});
                bbLowerSeries = klineChart.addSeries(window.LightweightCharts.LineSeries, {{
                    color: '#ef5350',
                    lineWidth: 1.5,
                    lineStyle: 0,
                    priceLineVisible: false,
                    lastValueVisible: true,
                    title: 'BB 下軌'
                }});
            }} else if (typeof klineChart.addLineSeries === 'function') {{
                bbUpperSeries = klineChart.addLineSeries({{
                    color: '#26a69a',
                    lineWidth: 1.5,
                    lineStyle: 0,
                    priceLineVisible: false,
                    lastValueVisible: true,
                    title: 'BB 上軌'
                }});
                bbMiddleSeries = klineChart.addLineSeries({{
                    color: '#ffb300',
                    lineWidth: 1,
                    lineStyle: 2,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    title: 'BB 中軌'
                }});
                bbLowerSeries = klineChart.addLineSeries({{
                    color: '#ef5350',
                    lineWidth: 1.5,
                    lineStyle: 0,
                    priceLineVisible: false,
                    lastValueVisible: true,
                    title: 'BB 下軌'
                }});
            }}

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

        // 初始化時間軸累計損益 Baseline 圖表
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

            updateBaselineData();

            perfChart.subscribeCrosshairMove(param => {{
                const strat = getActiveStrategy();
                const pnlList = strat.cumulative_pnl;
                if (!param.point || !param.time || param.point.x < 0 || param.point.x > perfContainer.clientWidth) {{
                    updatePerfLegend(pnlList[pnlList.length - 1]);
                }} else {{
                    let val = null;
                    if (param.seriesData && typeof param.seriesData.get === 'function') {{
                        const pt = param.seriesData.get(baselineSeries);
                        if (pt) val = pt.value;
                    }}
                    updatePerfLegend({{ time: param.time, value: val !== null ? val : 0 }});
                }}
            }});
        }}

        // 更新 Baseline 數據
        function updateBaselineData() {{
            if (!baselineSeries) return;
            const strat = getActiveStrategy();
            const baselineData = strat.cumulative_pnl.map(item => ({{
                time: item.time,
                value: item.value
            }}));
            baselineSeries.setData(baselineData);
            updatePerfLegend(strat.cumulative_pnl[strat.cumulative_pnl.length - 1]);
            buildPnlRibbon();
        }}

        // 建立底部盈虧彩條
        function buildPnlRibbon() {{
            const strat = getActiveStrategy();
            const pnlList = strat.cumulative_pnl;
            if (!pnlList || pnlList.length === 0) return;
            pnlRibbonBar.innerHTML = '';

            let currentStatus = pnlList[0].value >= 0 ? 'green' : 'red';
            let segmentCount = 1;
            const segments = [];

            for (let i = 1; i < pnlList.length; i++) {{
                const status = pnlList[i].value >= 0 ? 'green' : 'red';
                if (status === currentStatus) {{
                    segmentCount++;
                }} else {{
                    segments.push({{ status: currentStatus, count: segmentCount }});
                    currentStatus = status;
                    segmentCount = 1;
                }}
            }}
            segments.push({{ status: currentStatus, count: segmentCount }});

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

            aligned.sort((a, b) => {{
                if (a.time !== b.time) return a.time - b.time;
                return a.type === 'entry' ? -1 : 1;
            }});

            return aligned;
        }}

        // 計算布林通道數據
        function calculateBollingerBands(bars, length, mult) {{
            const n = bars.length;
            const upperData = [];
            const middleData = [];
            const lowerData = [];
            const lookupMap = new Map();
            if (n < length) return {{ upperData, middleData, lowerData, lookupMap }};

            const closes = bars.map(b => Number(b.close));
            const times = bars.map(b => Number(b.time));

            let windowSum = 0;
            for (let i = 0; i < length; i++) {{
                windowSum += closes[i];
            }}

            for (let i = length - 1; i < n; i++) {{
                if (i >= length) {{
                    windowSum += closes[i] - closes[i - length];
                }}
                const mean = windowSum / length;
                let varSum = 0;
                for (let j = i - length + 1; j <= i; j++) {{
                    const diff = closes[j] - mean;
                    varSum += diff * diff;
                }}
                const std = Math.sqrt(varSum / length);
                const upper = mean + mult * std;
                const middle = mean;
                const lower = mean - mult * std;
                const t = times[i];

                upperData.push({{ time: t, value: Number(upper.toFixed(1)) }});
                middleData.push({{ time: t, value: Number(middle.toFixed(1)) }});
                lowerData.push({{ time: t, value: Number(lower.toFixed(1)) }});

                lookupMap.set(t, {{ upper, middle, lower }});
            }}

            return {{ upperData, middleData, lowerData, lookupMap }};
        }}

        // 更新當前週期之布林通道線條與圖例參照
        function updateBollingerBands(bars) {{
            if (!bars || bars.length === 0) return;
            const len = currentBBConfig.length || 30;
            const mult = currentBBConfig.mult || 1.75;
            const bbRes = calculateBollingerBands(bars, len, mult);
            window.bbLookupMap = bbRes.lookupMap;

            if (bbUpperSeries && bbMiddleSeries && bbLowerSeries) {{
                if (showBollingerBands) {{
                    bbUpperSeries.setData(bbRes.upperData);
                    bbMiddleSeries.setData(bbRes.middleData);
                    bbLowerSeries.setData(bbRes.lowerData);
                }} else {{
                    bbUpperSeries.setData([]);
                    bbMiddleSeries.setData([]);
                    bbLowerSeries.setData([]);
                }}
            }}
        }}

        // 更新左上角 K 線數據 (包含布林軌道數值)
        function updateLegend(bar, volume, timestamp) {{
            const dtStr = bar.datetime || (bar.time ? new Date(bar.time * 1000).toLocaleString('zh-TW', {{ hour12: false }}) : '');
            legendTime.textContent = dtStr;
            legendOpen.textContent = Number(bar.open).toFixed(1);
            legendHigh.textContent = Number(bar.high).toFixed(1);
            legendLow.textContent = Number(bar.low).toFixed(1);
            legendClose.textContent = Number(bar.close).toFixed(1);

            const chg = Number(bar.close) - Number(bar.open);
            const chgPct = (chg / Number(bar.open)) * 100;
            const sign = chg >= 0 ? '+' : '';
            legendChange.textContent = `${{sign}}${{chg.toFixed(1)}} (${{sign}}${{chgPct.toFixed(2)}}%)`;
            legendChange.className = `legend-value ${{chg >= 0 ? 'up' : 'down'}}`;

            legendVolume.textContent = Number(volume || bar.volume || 0).toLocaleString('zh-TW');

            // 更新布林數值
            if (legendBbUpper && legendBbMiddle && legendBbLower) {{
                const ts = Number(timestamp || bar.time);
                if (window.bbLookupMap && window.bbLookupMap.has(ts)) {{
                    const bbVal = window.bbLookupMap.get(ts);
                    legendBbUpper.textContent = bbVal.upper.toFixed(1);
                    legendBbMiddle.textContent = bbVal.middle.toFixed(1);
                    legendBbLower.textContent = bbVal.lower.toFixed(1);
                }} else {{
                    legendBbUpper.textContent = '--';
                    legendBbMiddle.textContent = '--';
                    legendBbLower.textContent = '--';
                }}
            }}
        }}

        function showLastBarData() {{
            const bars = KLINE_DATA[currentInterval] || [];
            if (bars.length > 0) {{
                const lastBar = bars[bars.length - 1];
                updateLegend(lastBar, lastBar.volume, lastBar.time);
            }}
        }}

        // 套用交易標記 Markers
        function applyMarkersToChart(markers) {{
            if (!candlestickSeries) return;
            try {{
                if (typeof LightweightCharts.createSeriesMarkers === 'function') {{
                    if (markersPrimitive) {{
                        try {{ markersPrimitive.detach(); }} catch (e) {{}}
                        markersPrimitive = null;
                    }}
                    markersPrimitive = LightweightCharts.createSeriesMarkers(candlestickSeries, markers);
                    return;
                }}
            }} catch (e) {{
                console.warn('createSeriesMarkers 嘗試失敗，降級至 setMarkers:', e);
            }}

            try {{
                if (typeof candlestickSeries.setMarkers === 'function') {{
                    candlestickSeries.setMarkers(markers);
                }}
            }} catch (err) {{
                console.error('Markers 設定失敗:', err);
            }}
        }}

        // 載入指定時間週期之 K 線資料
        function loadIntervalData(intervalKey) {{
            const rawBars = KLINE_DATA[intervalKey];
            if (!rawBars || rawBars.length === 0) {{
                console.warn(`週期 ${{intervalKey}} 無資料`);
                return;
            }}
            currentInterval = intervalKey;

            const cData = rawBars.map(b => ({{
                time: Number(b.time),
                open: Number(b.open),
                high: Number(b.high),
                low: Number(b.low),
                close: Number(b.close)
            }}));

            const vData = rawBars.map(b => ({{
                time: Number(b.time),
                value: Number(b.volume),
                color: Number(b.close) >= Number(b.open) ? '#089981' : '#f23645'
            }}));

            candlestickSeries.setData(cData);
            volumeSeries.setData(vData);

            // 更新布林通道三軌
            updateBollingerBands(rawBars);

            if (showMarkers) {{
                const strat = getActiveStrategy();
                const alignedMarkers = alignMarkersToBars(strat.markers, rawBars);
                applyMarkersToChart(alignedMarkers);
            }} else {{
                applyMarkersToChart([]);
            }}

            klineChart.timeScale().fitContent();
            if (perfChart) perfChart.timeScale().fitContent();
            showLastBarData();
        }}

        // 定位特定交易之時間範圍並放大特寫
        function focusTradeOnChart(entryTs, exitTs, tradeId) {{
            if (!entryTs || !exitTs) return;

            // 1. 若處於全畫面模式，點擊定位時自動切換回雙圖模式以便觀察，且避免 fitContent 覆蓋
            let layoutSwitched = false;
            if (isSoloDataMode) {{
                toggleLayoutMode(true);
                layoutSwitched = true;
            }}

            // 2. 獲取該筆交易資訊
            const strat = getActiveStrategy();
            const trade = (strat.trades || []).find(t => t.trade_id === tradeId) || {{
                trade_id: tradeId,
                side: 'TRADE',
                entry_time: '',
                entry_price: 0,
                exit_time: '',
                exit_price: 0,
                exit_reason: '出場',
                net_pnl: 0
            }};

            // 3. 高亮表格當前行
            document.querySelectorAll('#trades-table-body tr').forEach(r => r.classList.remove('selected-trade-row'));
            const targetRow = document.getElementById(`trade-row-${{tradeId}}`);
            if (targetRow) {{
                targetRow.classList.add('selected-trade-row');
            }}

            // 4. 平滑滾動至 K 線主圖
            const klineSectionEl = document.getElementById('kline-section');
            if (klineSectionEl) {{
                klineSectionEl.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            }}

            // 5. 延遲 120ms (等待 DOM 展開與寬度計算完成後) 精確縮放可視區間
            const delayMs = layoutSwitched ? 150 : 30;
            setTimeout(() => {{
                if (!klineChart) return;
                // 計算前後各約 1.5 ~ 2 小時 (約 18 ~ 24 根 5 分 K) 之特寫緩衝
                const duration = Math.max(0, exitTs - entryTs);
                const padSec = Math.max(3600 * 1.5, Math.min(3600 * 4, duration * 1.5));
                const fromSec = entryTs - padSec;
                const toSec = exitTs + padSec;

                try {{
                    klineChart.timeScale().setVisibleRange({{ from: fromSec, to: toSec }});
                }} catch (e) {{
                    console.warn('定位時間範圍失敗:', e);
                }}

                // 6. 顯示頂部交易特寫橫幅 Banner
                showTradeFocusBanner(trade);
            }}, delayMs);
        }}

        // 清除交易聚焦狀態並恢復全景
        function clearTradeFocus() {{
            const banner = document.getElementById('trade-focus-banner');
            if (banner) banner.style.display = 'none';
            document.querySelectorAll('#trades-table-body tr').forEach(r => r.classList.remove('selected-trade-row'));
            if (klineChart) {{
                klineChart.timeScale().fitContent();
            }}
            showToast('已恢復全景檢視', 'info');
        }}

        // 顯示交易特寫橫幅
        function showTradeFocusBanner(trade) {{
            const banner = document.getElementById('trade-focus-banner');
            const content = document.getElementById('trade-focus-content');
            if (!banner || !content) return;

            const isLong = trade.side === 'LONG';
            const isProfit = (trade.net_pnl || 0) >= 0;
            const pnlSign = isProfit ? '+' : '';
            const pnlColor = isProfit ? '#089981' : '#f23645';
            const sideColor = isLong ? '#089981' : '#f23645';
            const sideText = isLong ? '買進做多' : '放空賣出';

            content.innerHTML = `
                <span class="focus-badge" style="background:${{sideColor}};">#${{trade.trade_id}} ${{sideText}}</span>
                <span>進場: <b>${{trade.entry_time}}</b> @ <b>${{Number(trade.entry_price).toFixed(1)}}</b></span>
                <span style="color:#787b86;">➔</span>
                <span>出場: <b>${{trade.exit_time}}</b> @ <b>${{Number(trade.exit_price).toFixed(1)}}</b> (${{trade.exit_reason}})</span>
                <span>損益: <b style="color:${{pnlColor}}; font-size:13px;">${{pnlSign}}${{Number(trade.net_pnl).toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}} TWD</b></span>
            `;
            banner.style.display = 'flex';
        }}

        // 渲染交易明細表格
        function renderTradesTable() {{
            const tbody = document.getElementById('trades-table-body');
            if (!tbody) return;
            tbody.innerHTML = '';

            const strat = getActiveStrategy();
            const trades = strat.trades;

            trades.forEach(t => {{
                const tr = document.createElement('tr');
                tr.id = `trade-row-${{t.trade_id}}`;
                const isLong = t.side === 'LONG';
                const isProfit = t.net_pnl >= 0;
                const pnlSign = isProfit ? '+' : '';
                const pnlClass = isProfit ? 'pnl-positive' : 'pnl-negative';
                const badgeClass = isLong ? 'badge-side-long' : 'badge-side-short';
                const sideText = isLong ? '做多' : '放空';

                tr.innerHTML = `
                    <td>#${{t.trade_id}}</td>
                    <td><span class="${{badgeClass}}">${{sideText}}</span></td>
                    <td>${{t.entry_time}}</td>
                    <td style="font-weight:600;">${{t.entry_price.toFixed(1)}}</td>
                    <td>${{t.exit_time}}</td>
                    <td style="font-weight:600;">${{t.exit_price.toFixed(1)}}</td>
                    <td>${{t.qty}}口</td>
                    <td><span style="color:#d1d4dc; font-weight:600;">${{t.exit_reason}}</span></td>
                    <td style="color:#787b86;">${{t.costs.toFixed(0)}}</td>
                    <td class="${{pnlClass}}">${{pnlSign}}${{t.net_pnl.toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}}</td>
                    <td style="font-weight:600;">${{t.equity_after.toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}}</td>
                    <td><button class="btn-focus-trade" onclick="event.stopPropagation(); focusTradeOnChart(${{t.entry_ts}}, ${{t.exit_ts}}, ${{t.trade_id}})">定位</button></td>
                `;
                tr.onclick = () => focusTradeOnChart(t.entry_ts, t.exit_ts, t.trade_id);
                tbody.appendChild(tr);
            }});
        }}

        // ==============================================================
        // 核心一：同屏多策略淨值對比繪圖引擎 (MultiStrategyCompareChart)
        // 經典標準走勢軸向：橫軸 X = 時間時序推進 ｜ 縱軸 Y = 累積損益 / 淨值 (TWD)
        // 具備 0 軸水平基準虛線、底部日期時間刻度與右側損益刻度
        // ==============================================================
        class MultiStrategyCompareChart {{
            constructor(canvas, viewport, tooltip) {{
                this.canvas = canvas;
                this.viewport = viewport;
                this.tooltip = tooltip;
                this.ctx = canvas.getContext('2d');

                this.mode = 'PNL'; // 'PNL' (相對累積損益) 或 'EQUITY' (總淨值)
                this.axisMode = 'X_TIME_Y_PNL'; // 永久固定唯一：橫軸 X = 時間推進，縱軸 Y = 累積損益
                this.visibleSeries = {{
                    donchian: true,
                    supertrend: true,
                    atr: true
                }};

                this.hoverIndex = -1;
                this.initData();
                this.initEvents();
            }}

            initData() {{
                // 彙總全時序之時間戳集合 (以 5m 為基準)
                const bars5m = KLINE_DATA['5m'] || [];
                this.timePoints = bars5m.map(b => ({{
                    time: Number(b.time),
                    datetime: b.datetime
                }}));

                // 建立快速查詢索引 (各策略時間 -> 數值)
                this.seriesData = {{}};
                const keys = ['donchian', 'supertrend', 'atr'];
                
                keys.forEach(k => {{
                    const strat = STRATEGIES_DATA[k];
                    const map = new Map();
                    if (strat && strat.cumulative_pnl) {{
                        strat.cumulative_pnl.forEach(pt => {{
                            map.set(pt.time, pt);
                        }});
                    }}
                    this.seriesData[k] = map;
                }});

                // 預先對齊各時間點之三策略數值 (Forward-fill 平滑對齊)
                this.alignedPoints = [];
                let lastVals = {{
                    donchian: {{ pnl: 0, equity: 105150 }},
                    supertrend: {{ pnl: 0, equity: 105150 }},
                    atr: {{ pnl: 0, equity: 105150 }}
                }};

                for (let i = 0; i < this.timePoints.length; i++) {{
                    const t = this.timePoints[i].time;
                    const dt = this.timePoints[i].datetime;

                    const row = {{
                        index: i,
                        time: t,
                        datetime: dt,
                        donchian: {{ ...lastVals.donchian }},
                        supertrend: {{ ...lastVals.supertrend }},
                        atr: {{ ...lastVals.atr }}
                    }};

                    keys.forEach(k => {{
                        if (this.seriesData[k]) {{
                            const pt = this.seriesData[k].get(t);
                            if (pt) {{
                                row[k] = {{
                                    pnl: pt.value,
                                    equity: pt.equity
                                }};
                                lastVals[k] = {{
                                    pnl: pt.value,
                                    equity: pt.equity
                                }};
                            }}
                        }}
                    }});

                    this.alignedPoints.push(row);
                }}
            }}

            initEvents() {{
                if (!this.canvas) return;

                this.canvas.addEventListener('mousemove', (e) => {{
                    const rect = this.canvas.getBoundingClientRect();
                    const mx = e.clientX - rect.left;
                    const my = e.clientY - rect.top;
                    this.handleMouseMove(mx, my, e.clientX, e.clientY);
                }});

                this.canvas.addEventListener('mouseleave', () => {{
                    this.hoverIndex = -1;
                    if (this.tooltip) this.tooltip.style.display = 'none';
                    this.draw();
                }});

                window.addEventListener('resize', () => {{
                    this.resizeAndDraw();
                }});
            }}

            handleMouseMove(mx, my, clientX, clientY) {{
                if (!this.layout) return;
                const L = this.layout;
                const total = this.alignedPoints.length;
                if (total === 0) return;

                // 橫軸 X 為時間推進軸
                const ratio = Math.max(0, Math.min(1, (mx - L.padLeft) / L.chartWidth));
                const idx = Math.round(ratio * (total - 1));

                if (idx !== this.hoverIndex) {{
                    this.hoverIndex = idx;
                    this.draw();
                }}

                this.showTooltip(idx, clientX, clientY);
            }}

            showTooltip(idx, clientX, clientY) {{
                if (!this.tooltip || idx < 0 || idx >= this.alignedPoints.length) {{
                    if (this.tooltip) this.tooltip.style.display = 'none';
                    return;
                }}

                const pt = this.alignedPoints[idx];
                const dVal = pt.donchian;
                const sVal = pt.supertrend;
                const aVal = pt.atr;

                // 尋找領先策略
                const compKey = this.mode === 'PNL' ? 'pnl' : 'equity';
                let maxVal = -Infinity;
                let leader = '';
                if (this.visibleSeries.donchian && dVal[compKey] > maxVal) {{ maxVal = dVal[compKey]; leader = 'donchian'; }}
                if (this.visibleSeries.supertrend && sVal[compKey] > maxVal) {{ maxVal = sVal[compKey]; leader = 'supertrend'; }}
                if (this.visibleSeries.atr && aVal[compKey] > maxVal) {{ maxVal = aVal[compKey]; leader = 'atr'; }}

                const formatVal = (v) => {{
                    const sign = v.pnl >= 0 ? '+' : '';
                    if (this.mode === 'PNL') {{
                        return `${{sign}}${{Math.round(v.pnl).toLocaleString('zh-TW')}} TWD`;
                    }} else {{
                        return `${{Math.round(v.equity).toLocaleString('zh-TW')}} TWD`;
                    }}
                }};

                let html = `
                    <div class="mt-header">
                        <span>⏱️ ${{pt.datetime || 'K線時序'}}</span>
                        <span>Bar #${{idx + 1}} / ${{this.alignedPoints.length}}</span>
                    </div>
                `;

                if (this.visibleSeries.donchian) {{
                    const isLeader = leader === 'donchian';
                    html += `
                        <div class="mt-row">
                            <span class="mt-strat-label">
                                <span class="mt-strat-dot" style="background:#00e5ff;"></span>
                                <span>布林_唐奇安</span>
                                ${{isLeader ? '<span class="mt-leader-tag">領先 👑</span>' : ''}}
                            </span>
                            <span class="mt-strat-val" style="color:#00e5ff;">${{formatVal(dVal)}}</span>
                        </div>
                    `;
                }}

                if (this.visibleSeries.supertrend) {{
                    const isLeader = leader === 'supertrend';
                    html += `
                        <div class="mt-row">
                            <span class="mt-strat-label">
                                <span class="mt-strat-dot" style="background:#f0b90b;"></span>
                                <span>布林_超級趨勢</span>
                                ${{isLeader ? '<span class="mt-leader-tag">領先 👑</span>' : ''}}
                            </span>
                            <span class="mt-strat-val" style="color:#f0b90b;">${{formatVal(sVal)}}</span>
                        </div>
                    `;
                }}

                if (this.visibleSeries.atr) {{
                    const isLeader = leader === 'atr';
                    html += `
                        <div class="mt-row">
                            <span class="mt-strat-label">
                                <span class="mt-strat-dot" style="background:#b388ff;"></span>
                                <span>布林_ATR基準</span>
                                ${{isLeader ? '<span class="mt-leader-tag">領先 👑</span>' : ''}}
                            </span>
                            <span class="mt-strat-val" style="color:#b388ff;">${{formatVal(aVal)}}</span>
                        </div>
                    `;
                }}

                this.tooltip.innerHTML = html;
                this.tooltip.style.display = 'block';

                const tipW = 270;
                const tipH = 140;
                let left = clientX + 16;
                let top = clientY - 40;

                if (left + tipW > window.innerWidth) left = clientX - tipW - 16;
                if (top + tipH > window.innerHeight) top = window.innerHeight - tipH - 16;
                if (top < 10) top = 10;

                this.tooltip.style.left = left + 'px';
                this.tooltip.style.top = top + 'px';
            }}

            toggleSeries(stratKey, isVisible) {{
                this.visibleSeries[stratKey] = isVisible;
                this.draw();
            }}

            toggleMode() {{
                this.mode = this.mode === 'PNL' ? 'EQUITY' : 'PNL';
                this.draw();
                return this.mode;
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
                const total = this.alignedPoints.length;

                ctx.clearRect(0, 0, w, h);
                if (total === 0) return;

                const compKey = this.mode === 'PNL' ? 'pnl' : 'equity';
                let minVal = Infinity;
                let maxVal = -Infinity;

                const keys = ['donchian', 'supertrend', 'atr'].filter(k => this.visibleSeries[k]);
                if (keys.length === 0) return;

                this.alignedPoints.forEach(pt => {{
                    keys.forEach(k => {{
                        const v = pt[k][compKey];
                        if (v < minVal) minVal = v;
                        if (v > maxVal) maxVal = v;
                    }});
                }});

                if (this.mode === 'PNL') {{
                    if (minVal > -3000) minVal = -3000;
                    if (maxVal < 16000) maxVal = 16000;
                }} else {{
                    if (minVal > 100000) minVal = 100000;
                    if (maxVal < 122000) maxVal = 122000;
                }}

                // 上下留適度間隙
                const margin = (maxVal - minVal) * 0.08;
                minVal -= margin;
                maxVal += margin;

                const valRange = (maxVal - minVal) || 1;
                const seriesConfigs = [
                    {{ key: 'donchian', color: '#00e5ff', name: '唐奇安通道' }},
                    {{ key: 'supertrend', color: '#f0b90b', name: '超級趨勢線' }},
                    {{ key: 'atr', color: '#b388ff', name: 'ATR 基準' }}
                ];

                const padTop = 22;
                const padBottom = 34;
                const padLeft = 24;
                const padRight = 85;
                const chartW = w - padLeft - padRight;
                const chartH = h - padTop - padBottom;

                this.layout = {{
                    padTop, padBottom, padLeft, padRight,
                    chartWidth: chartW,
                    chartHeight: chartH
                }};

                const getY = (val) => padTop + chartH - ((val - minVal) / valRange) * chartH;
                const getX = (idx) => padLeft + (idx / Math.max(1, total - 1)) * chartW;

                // 1. 水平微弱損益網格線與右側 Y 軸刻度
                ctx.lineWidth = 1;
                ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
                ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
                ctx.textAlign = 'left';

                const gridSteps = 6;
                for (let i = 0; i <= gridSteps; i++) {{
                    const curVal = minVal + (valRange / gridSteps) * i;
                    const y = getY(curVal);

                    ctx.beginPath();
                    ctx.moveTo(padLeft, y);
                    ctx.lineTo(padLeft + chartW, y);
                    ctx.stroke();

                    const sign = (curVal >= 0 && this.mode === 'PNL') ? '+' : '';
                    const label = `${{sign}}${{Math.round(curVal).toLocaleString('zh-TW')}} TWD`;
                    ctx.fillStyle = (curVal > 0) ? '#26a69a' : (curVal < 0 ? '#ef5350' : '#8b949e');
                    ctx.fillText(label, padLeft + chartW + 8, y + 4);
                }}

                // 2. 垂直微弱時間網格線與底部 X 軸時間刻度
                ctx.textAlign = 'center';
                ctx.fillStyle = '#8b949e';
                const timeSteps = 8;
                for (let i = 0; i <= timeSteps; i++) {{
                    const idx = Math.min(total - 1, Math.round((total - 1) * (i / timeSteps)));
                    const x = getX(idx);

                    ctx.save();
                    ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
                    ctx.beginPath();
                    ctx.moveTo(x, padTop);
                    ctx.lineTo(x, padTop + chartH);
                    ctx.stroke();
                    ctx.restore();

                    const dtStr = this.alignedPoints[idx].datetime || '';
                    const shortDt = dtStr.length >= 16 ? dtStr.substring(5, 16) : dtStr;
                    ctx.fillText(shortDt, x, padTop + chartH + 18);
                }}

                // 3. 0 軸水平基準線 (或期初資金基準線)
                const baseVal = this.mode === 'PNL' ? 0 : 105150;
                if (baseVal >= minVal && baseVal <= maxVal) {{
                    const baseY = getY(baseVal);
                    ctx.save();
                    ctx.setLineDash([4, 4]);
                    ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
                    ctx.lineWidth = 1.5;
                    ctx.beginPath();
                    ctx.moveTo(padLeft, baseY);
                    ctx.lineTo(padLeft + chartW, baseY);
                    ctx.stroke();

                    ctx.fillStyle = '#ffffff';
                    ctx.font = '10px sans-serif';
                    ctx.textAlign = 'left';
                    ctx.fillText(this.mode === 'PNL' ? '0 基準線' : '期初資金 (105,150 TWD)', padLeft + 6, baseY - 6);
                    ctx.restore();
                }}

                // 4. 繪製三條策略曲線 (由左向右隨時間時序推進)
                seriesConfigs.forEach(sc => {{
                    if (!this.visibleSeries[sc.key]) return;

                    ctx.save();
                    ctx.lineWidth = 2.4;
                    ctx.strokeStyle = sc.color;
                    ctx.beginPath();

                    for (let i = 0; i < total; i++) {{
                        const x = getX(i);
                        const y = getY(this.alignedPoints[i][sc.key][compKey]);
                        if (i === 0) ctx.moveTo(x, y);
                        else ctx.lineTo(x, y);
                    }}
                    ctx.stroke();
                    ctx.restore();
                }});

                // 5. 十字準星 (跟隨滑鼠 X 軸時間時序位置)
                if (this.hoverIndex >= 0 && this.hoverIndex < total) {{
                    const hx = getX(this.hoverIndex);
                    ctx.save();
                    ctx.setLineDash([3, 3]);
                    ctx.strokeStyle = 'rgba(255, 255, 255, 0.5)';
                    ctx.beginPath();
                    ctx.moveTo(hx, padTop);
                    ctx.lineTo(hx, padTop + chartH);
                    ctx.stroke();
                    ctx.restore();

                    // 各策略圓點標記
                    seriesConfigs.forEach(sc => {{
                        if (!this.visibleSeries[sc.key]) return;
                        const hy = getY(this.alignedPoints[this.hoverIndex][sc.key][compKey]);
                        ctx.fillStyle = sc.color;
                        ctx.beginPath();
                        ctx.arc(hx, hy, 4.5, 0, Math.PI * 2);
                        ctx.fill();
                        ctx.strokeStyle = '#ffffff';
                        ctx.lineWidth = 1.5;
                        ctx.stroke();
                    }});
                }}
            }}
        }}

        // ==============================================================
        // 核心二：固定 Y 軸 (交易次數) × X 軸 (損益 / 帳戶淨值) Canvas 引擎
        // ==============================================================
        class TradeEquityChart {{
            constructor(canvas, viewport, tooltip) {{
                this.canvas = canvas;
                this.viewport = viewport;
                this.tooltip = tooltip;
                this.ctx = canvas.getContext('2d');
                this.mode = 'Y_TRADES_X_PNL';
                this.showDeltas = true;
                this.hoverIndex = -1;
                this.setTrades();
                this.initEvents();
            }}

            setTrades() {{
                const strat = getActiveStrategy();
                this.trades = strat.trades || [];
                this.equityPoints = [
                    {{ trade_id: 0, cum_pnl: 0, equity_after: 105150, net_pnl: 0, exit_reason: '期初基準', side: '--', entry_time: '--', exit_time: '--' }},
                    ...this.trades
                ];
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
                            focusTradeOnChart(t.entry_ts, t.exit_ts, t.trade_id);
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

                for (let i = 0; i < total; i++) {{
                    const py = L.getY(i);
                    const dist = Math.abs(my - py);
                    if (dist < minDist) {{
                        minDist = dist;
                        bestIdx = i;
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
                const total = this.equityPoints.length;

                ctx.clearRect(0, 0, w, h);

                const padTop = 15;
                const padBottom = 20;
                const padLeft = 45;
                const padRight = 35;
                const chartW = w - padLeft - padRight;
                const chartH = h - padTop - padBottom;

                let minPnl = 0;
                let maxPnl = 0;
                this.equityPoints.forEach(p => {{
                    if (p.cum_pnl < minPnl) minPnl = p.cum_pnl;
                    if (p.cum_pnl > maxPnl) maxPnl = p.cum_pnl;
                }});

                minPnl = Math.min(minPnl - 500, -2000);
                maxPnl = Math.max(maxPnl + 1000, 5000);
                const pnlRange = maxPnl - minPnl;

                const getX = (pnl) => padLeft + ((pnl - minPnl) / pnlRange) * chartW;
                const getY = (idx) => padTop + (idx / Math.max(1, total - 1)) * chartH;

                this.layout = {{ padTop, padBottom, padLeft, padRight, getX, getY }};

                // 網格線
                ctx.lineWidth = 1;
                ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
                ctx.fillStyle = '#787b86';
                ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';

                const zeroX = getX(0);
                ctx.save();
                ctx.setLineDash([4, 4]);
                ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
                ctx.beginPath();
                ctx.moveTo(zeroX, padTop);
                ctx.lineTo(zeroX, padTop + chartH);
                ctx.stroke();
                ctx.restore();

                // 逐筆累計損益折線
                ctx.save();
                ctx.lineWidth = 2;
                ctx.strokeStyle = '#089981';
                ctx.beginPath();
                for (let i = 0; i < total; i++) {{
                    const px = getX(this.equityPoints[i].cum_pnl);
                    const py = getY(i);
                    if (i === 0) ctx.moveTo(px, py);
                    else ctx.lineTo(px, py);
                }}
                ctx.stroke();
                ctx.restore();

                // 節點
                for (let i = 0; i < total; i++) {{
                    const px = getX(this.equityPoints[i].cum_pnl);
                    const py = getY(i);
                    const isWin = this.equityPoints[i].net_pnl >= 0;

                    ctx.fillStyle = (i === 0) ? '#ffffff' : (isWin ? '#089981' : '#f23645');
                    ctx.beginPath();
                    ctx.arc(px, py, (i === this.hoverIndex) ? 5.5 : 3, 0, Math.PI * 2);
                    ctx.fill();
                }}
            }}
        }}

        // ==============================================================
        // 互動事件與切換邏輯
        // ==============================================================

        // 切換全畫面數據視圖 ⇄ 雙圖模式
        function toggleLayoutMode(skipFitContent = false) {{
            isSoloDataMode = !isSoloDataMode;
            workspaceSplit.classList.toggle('solo-data-mode', isSoloDataMode);

            const btn = document.getElementById('btn-toggle-layout');
            const txt = document.getElementById('txt-toggle-layout');

            if (isSoloDataMode) {{
                if (btn) btn.classList.add('mode-active');
                if (txt) txt.textContent = '🗖 數據全畫面 (作用中)';
                showToast('已切換為：數據全畫面視圖 (僅顯示量化數據與淨值圖)', 'info');
            }} else {{
                if (btn) btn.classList.remove('mode-active');
                if (txt) txt.textContent = '📈 展開 K 線雙圖';
                showToast('已切換為：雙圖聯動視圖 (K 線主圖 + 數據面板)', 'info');
            }}

            // 觸發自適應適配重繪
            setTimeout(() => {{
                if (klineChart && !isSoloDataMode && !skipFitContent) klineChart.timeScale().fitContent();
                if (perfChart) perfChart.timeScale().fitContent();
                if (multiCompareChart) multiCompareChart.resizeAndDraw();
                if (tradeEquityChart) tradeEquityChart.resizeAndDraw();
            }}, 100);
        }}

        // ==============================================================
        // 核心三：前端高解析度即時回測引擎 (In-Browser Backtest Engine)
        // 100% 對齊後端 Python BacktestEngine 狀態機撮合邏輯
        // ==============================================================
        function calculateWilderRma(series, length) {{
            const n = series.length;
            const rma = new Array(n).fill(NaN);
            if (n < length) return rma;
            let sum = 0;
            for (let i = 0; i < length; i++) sum += series[i];
            rma[length - 1] = sum / length;
            const alpha = 1.0 / length;
            for (let i = length; i < n; i++) {{
                rma[i] = alpha * series[i] + (1.0 - alpha) * rma[i - 1];
            }}
            return rma;
        }}

        function runFastBacktest(bars, config) {{
            const n = bars.length;
            const opens = bars.map(b => Number(b.open));
            const highs = bars.map(b => Number(b.high));
            const lows = bars.map(b => Number(b.low));
            const closes = bars.map(b => Number(b.close));
            const times = bars.map(b => Number(b.time));
            const datetimes = bars.map(b => b.datetime);

            // 1. 布林通道 (SMA + ddof=0 StdDev)
            const bbLen = Number(config.bb_length) || 30;
            const bbMult = Number(config.bb_mult) || 1.75;
            const pullbackRatio = Number(config.pullback_ratio) || 0.25;
            const bbUpper = new Array(n).fill(NaN);
            const bbLower = new Array(n).fill(NaN);

            for (let i = bbLen - 1; i < n; i++) {{
                let sum = 0;
                for (let j = i - bbLen + 1; j <= i; j++) sum += closes[j];
                const mean = sum / bbLen;
                let varSum = 0;
                for (let j = i - bbLen + 1; j <= i; j++) varSum += Math.pow(closes[j] - mean, 2);
                const std = Math.sqrt(varSum / bbLen);
                bbUpper[i] = mean + bbMult * std;
                bbLower[i] = mean - bbMult * std;
            }}

            // 2. True Range 與 ATR
            const tr = new Array(n).fill(NaN);
            tr[0] = highs[0] - lows[0];
            for (let i = 1; i < n; i++) {{
                const tr1 = highs[i] - lows[i];
                const tr2 = Math.abs(highs[i] - closes[i - 1]);
                const tr3 = Math.abs(lows[i] - closes[i - 1]);
                tr[i] = Math.max(tr1, tr2, tr3);
            }}
            const atrLen = Number(config.atr_length) || 14;
            const atrVal = calculateWilderRma(tr, atrLen);

            // ATR Signal (MA of ATR)
            const atrSigLen = Number(config.atr_sig_length) || 5;
            const atrSig = new Array(n).fill(NaN);
            for (let i = atrSigLen - 1; i < n; i++) {{
                let sum = 0;
                let count = 0;
                for (let j = i - atrSigLen + 1; j <= i; j++) {{
                    if (!isNaN(atrVal[j])) {{
                        sum += atrVal[j];
                        count++;
                    }}
                }}
                if (count === atrSigLen) atrSig[i] = sum / atrSigLen;
            }}

            // 3. 唐奇安通道 (shift 1)
            const donchianLen = Number(config.donchian_length) || 4;
            const donchianHigh = new Array(n).fill(NaN);
            const donchianLow = new Array(n).fill(NaN);
            for (let i = donchianLen; i < n; i++) {{
                let maxH = -Infinity;
                let minL = Infinity;
                for (let j = i - donchianLen; j < i; j++) {{
                    if (highs[j] > maxH) maxH = highs[j];
                    if (lows[j] < minL) minL = lows[j];
                }}
                donchianHigh[i] = maxH;
                donchianLow[i] = minL;
            }}

            // 4. 超級趨勢線
            const stLen = Number(config.supertrend_length) || 14;
            const stMult = Number(config.supertrend_multiplier) || 2.0;
            const stVal = new Array(n).fill(NaN);
            const stDir = new Array(n).fill(1);
            let prevUpper = 0, prevLower = 0, direction = 1;

            for (let i = 1; i < n; i++) {{
                if (isNaN(atrVal[i]) || atrVal[i] <= 0) continue;
                const median = (highs[i] + lows[i]) / 2.0;
                const basicUpper = median + stMult * atrVal[i];
                const basicLower = median - stMult * atrVal[i];

                let finalUpper, finalLower;
                if (i === 1) {{
                    finalUpper = basicUpper;
                    finalLower = basicLower;
                }} else {{
                    finalUpper = (basicUpper < prevUpper || closes[i - 1] > prevUpper) ? basicUpper : prevUpper;
                    finalLower = (basicLower > prevLower || closes[i - 1] < prevLower) ? basicLower : prevLower;
                }}

                let curSt;
                if (direction === 1) {{
                    if (closes[i] < finalLower) {{
                        direction = -1;
                        curSt = finalUpper;
                    }} else {{
                        curSt = finalLower;
                    }}
                }} else {{
                    if (closes[i] > finalUpper) {{
                        direction = 1;
                        curSt = finalLower;
                    }} else {{
                        curSt = finalUpper;
                    }}
                }}
                stVal[i] = curSt;
                stDir[i] = direction;
                prevUpper = finalUpper;
                prevLower = finalLower;
            }}

            // 5. 撮合回測狀態機
            const initialCap = Number(config.initial_capital) || 105150.0;
            const pointValue = 10.0;
            const marginReq = 35050.0;
            const maxLeverage = Number(config.max_leverage) || 7.0;
            const commissionPerOrder = Number(config.commission) || 18.0;
            const taxRate = 0.00002;
            const slippage = Number(config.slippage) || 0.0;
            const exitMode = config.exit_mode || 'donchian';

            let equity = initialCap;
            let posSide = 'NONE';
            let posQty = 0;
            let posEntryPrice = 0;
            let posEntryTime = '';
            let posSlPrice = 0;
            let posEntryIdx = 0;

            let pendingSignalSide = 'NONE';
            let signalBarIdx = -1;
            let signalBarClose = 0;
            let signalBarQ1 = 0;
            let signalBarExtreme = 0;

            let entryReadySide = 'NONE';
            let entrySlPrice = 0;

            const trades = [];
            const equityCurve = [];
            let tradeCounter = 0;
            let runningCumPnl = 0;

            function isThirdWednesday(tsStr) {{
                if (!tsStr) return false;
                const d = new Date(tsStr);
                if (isNaN(d.getTime())) return false;
                if (d.getDay() !== 3) return false;
                const day = d.getDate();
                if (day < 15 || day > 21) return false;
                const hour = d.getHours();
                const min = d.getMinutes();
                return (hour === 13 && min === 0);
            }}

            for (let i = 1; i < n; i++) {{
                const curOpen = opens[i];
                const curHigh = highs[i];
                const curLow = lows[i];
                const curClose = closes[i];
                const curTimeStr = datetimes[i];
                const curTs = times[i];

                // A. 進場執行 (Bar 3)
                if (entryReadySide !== 'NONE' && posSide === 'NONE') {{
                    const rawCapQty = Math.floor(equity / marginReq);
                    const rawLevQty = Math.floor((equity * maxLeverage) / marginReq);
                    const safeQty = Math.max(1, Math.min(rawCapQty, rawLevQty));

                    if (safeQty >= 1) {{
                        const fillPrice = (entryReadySide === 'LONG') ? (curOpen + slippage) : (curOpen - slippage);
                        posSide = entryReadySide;
                        posQty = safeQty;
                        posEntryPrice = fillPrice;
                        posEntryTime = curTimeStr;
                        posSlPrice = entrySlPrice;
                        posEntryIdx = i;
                    }}
                    entryReadySide = 'NONE';
                }}

                // B. 出場檢核
                if (posSide !== 'NONE') {{
                    let exitReason = null;
                    let rawExitPrice = curClose;

                    if (isThirdWednesday(curTimeStr)) {{
                        exitReason = 'ROLLOVER_SETTLEMENT';
                        rawExitPrice = curClose;
                    }} else if (posSide === 'LONG' && curLow <= posSlPrice) {{
                        exitReason = 'STOP_LOSS';
                        rawExitPrice = Math.min(curOpen, posSlPrice);
                    }} else if (posSide === 'SHORT' && curHigh >= posSlPrice) {{
                        exitReason = 'STOP_LOSS';
                        rawExitPrice = Math.max(curOpen, posSlPrice);
                    }} else if (exitMode === 'donchian') {{
                        if (posSide === 'LONG' && !isNaN(donchianLow[i]) && curClose < donchianLow[i]) {{
                            exitReason = 'DONCHIAN_EXIT';
                            rawExitPrice = curClose;
                        }} else if (posSide === 'SHORT' && !isNaN(donchianHigh[i]) && curClose > donchianHigh[i]) {{
                            exitReason = 'DONCHIAN_EXIT';
                            rawExitPrice = curClose;
                        }}
                    }} else if (exitMode === 'supertrend') {{
                        if (posSide === 'LONG' && (stDir[i] === -1 || curClose < stVal[i])) {{
                            exitReason = 'SUPERTREND_EXIT';
                            rawExitPrice = curClose;
                        }} else if (posSide === 'SHORT' && (stDir[i] === 1 || curClose > stVal[i])) {{
                            exitReason = 'SUPERTREND_EXIT';
                            rawExitPrice = curClose;
                        }}
                    }} else if (exitMode === 'atr') {{
                        if (!isNaN(atrVal[i]) && !isNaN(atrSig[i]) && !isNaN(atrVal[i - 1]) && !isNaN(atrSig[i - 1])) {{
                            const prevAbove = atrVal[i - 1] >= atrSig[i - 1];
                            const curBelow = atrVal[i] < atrSig[i];
                            if (prevAbove && curBelow) {{
                                exitReason = 'ATR_TAKE_PROFIT';
                                rawExitPrice = curClose;
                            }}
                        }}
                    }}

                    if (exitReason !== null) {{
                        tradeCounter++;
                        const actualExitPrice = (posSide === 'LONG') ? (rawExitPrice - slippage) : (rawExitPrice + slippage);
                        const grossPnl = (posSide === 'LONG')
                            ? (actualExitPrice - posEntryPrice) * pointValue * posQty
                            : (posEntryPrice - actualExitPrice) * pointValue * posQty;

                        const feeTotal = commissionPerOrder * 2.0 * posQty;
                        const entryNotional = posEntryPrice * pointValue * posQty;
                        const exitNotional = actualExitPrice * pointValue * posQty;
                        const taxTotal = Math.round(entryNotional * taxRate) + Math.round(exitNotional * taxRate);
                        const netPnl = grossPnl - feeTotal - taxTotal;
                        equity += netPnl;
                        runningCumPnl += netPnl;

                        let exitReasonZh = '結算';
                        if (exitReason === 'STOP_LOSS') exitReasonZh = '停損';
                        else if (exitReason === 'ATR_TAKE_PROFIT') exitReasonZh = 'ATR停利';
                        else if (exitReason === 'DONCHIAN_EXIT') exitReasonZh = '唐奇安出場';
                        else if (exitReason === 'SUPERTREND_EXIT') exitReasonZh = '超級趨勢出場';

                        trades.push({{
                            trade_id: tradeCounter,
                            side: posSide,
                            entry_time: posEntryTime,
                            entry_price: posEntryPrice,
                            exit_time: curTimeStr,
                            exit_price: actualExitPrice,
                            qty: posQty,
                            exit_reason: exitReasonZh,
                            costs: feeTotal + taxTotal,
                            net_pnl: netPnl,
                            cum_pnl: Math.round(runningCumPnl * 100) / 100,
                            return_pct: Math.round(((actualExitPrice - posEntryPrice) / posEntryPrice * (posSide === 'LONG' ? 1 : -1) * 100) * 100) / 100,
                            equity_after: Math.round(equity * 100) / 100,
                            entry_ts: times[posEntryIdx],
                            exit_ts: curTs
                        }});

                        posSide = 'NONE';
                        posQty = 0;
                    }}
                }}

                // C. 第二根確認棒與第一根訊號棒判斷
                if (posSide === 'NONE' && entryReadySide === 'NONE') {{
                    if (pendingSignalSide !== 'NONE' && (i - 1 === signalBarIdx)) {{
                        if (pendingSignalSide === 'LONG') {{
                            const isConfirmed = (curClose <= signalBarClose) && (curClose >= signalBarQ1);
                            if (isConfirmed) {{
                                entryReadySide = 'LONG';
                                entrySlPrice = signalBarExtreme;
                            }}
                        }} else if (pendingSignalSide === 'SHORT') {{
                            const isConfirmed = (curClose >= signalBarClose) && (curClose <= signalBarQ1);
                            if (isConfirmed) {{
                                entryReadySide = 'SHORT';
                                entrySlPrice = signalBarExtreme;
                            }}
                        }}
                        pendingSignalSide = 'NONE';
                    }}

                    if (!isNaN(bbUpper[i]) && !isNaN(bbLower[i])) {{
                        if (curClose > bbUpper[i] && curClose > curOpen) {{
                            const body = curClose - curOpen;
                            pendingSignalSide = 'LONG';
                            signalBarIdx = i;
                            signalBarClose = curClose;
                            signalBarQ1 = curClose - (body * pullbackRatio);
                            signalBarExtreme = curLow;
                        }} else if (curClose < bbLower[i] && curClose < curOpen) {{
                            const body = curOpen - curClose;
                            pendingSignalSide = 'SHORT';
                            signalBarIdx = i;
                            signalBarClose = curClose;
                            signalBarQ1 = curClose + (body * pullbackRatio);
                            signalBarExtreme = curHigh;
                        }}
                    }}
                }}

                // D. 記錄每根 K 線浮動淨值
                let curEq = equity;
                if (posSide !== 'NONE') {{
                    const unrealized = (posSide === 'LONG')
                        ? (curClose - posEntryPrice) * pointValue * posQty
                        : (posEntryPrice - curClose) * pointValue * posQty;
                    curEq += unrealized;
                }}
                equityCurve.push({{
                    time: curTs,
                    value: Math.round((curEq - initialCap) * 100) / 100,
                    equity: Math.round(curEq * 100) / 100,
                    close: curClose,
                    in_position: (posSide !== 'NONE')
                }});
            }}

            // 6. 量化指標彙總
            const wins = trades.filter(t => t.net_pnl > 0).map(t => t.net_pnl);
            const losses = trades.filter(t => t.net_pnl < 0).map(t => t.net_pnl);
            const totalNetProfit = equity - initialCap;
            const winRatePct = trades.length > 0 ? (wins.length / trades.length) * 100 : 0;
            const grossWin = wins.reduce((a, b) => a + b, 0);
            const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0));
            const profitFactor = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? 99.0 : 0);
            const avgWin = wins.length > 0 ? (grossWin / wins.length) : 0;
            const avgLoss = losses.length > 0 ? (grossLoss / losses.length) : 0;
            const payoff = avgLoss > 0 ? (avgWin / avgLoss) : 0;

            // 計算 MDD
            let peak = -Infinity;
            let maxDd = 0;
            let maxDdPct = 0;
            for (const eq of equityCurve) {{
                if (eq.equity > peak) peak = eq.equity;
                const dd = peak - eq.equity;
                const ddPct = peak > 0 ? (dd / peak) * 100 : 0;
                if (dd > maxDd) maxDd = dd;
                if (ddPct > maxDdPct) maxDdPct = ddPct;
            }}

            const netPnlSign = totalNetProfit >= 0 ? '+' : '';
            const retPct = (totalNetProfit / initialCap) * 100;
            const retPctSign = retPct >= 0 ? '+' : '';

            const metrics = {{
                strategyName: config.strategy_name || '自訂策略',
                totalNetProfit: Math.round(totalNetProfit * 100) / 100,
                totalNetProfitStr: `${{netPnlSign}}${{Math.round(totalNetProfit).toLocaleString('zh-TW')}}`,
                totalReturnPct: Math.round(retPct * 100) / 100,
                totalReturnPctStr: `${{retPctSign}}${{retPct.toFixed(2)}}%`,
                isProfit: totalNetProfit >= 0,
                maxDrawdown: Math.round(maxDd * 100) / 100,
                maxDrawdownStr: `-${{Math.round(maxDd).toLocaleString('zh-TW')}}`,
                maxDrawdownPct: Math.round(maxDdPct * 100) / 100,
                maxDrawdownPctStr: `-${{maxDdPct.toFixed(2)}}%`,
                winRatePct: Math.round(winRatePct * 100) / 100,
                winRateStr: `${{winRatePct.toFixed(2)}}%`,
                totalTrades: trades.length,
                winningTrades: wins.length,
                losingTrades: losses.length,
                tradesRatioStr: `${{wins.length}}/${{trades.length}}`,
                profitFactor: Math.round(profitFactor * 1000) / 1000,
                profitFactorStr: profitFactor.toFixed(3),
                initialCapital: initialCap,
                initialCapitalStr: Math.round(initialCap).toLocaleString('zh-TW'),
                finalEquity: Math.round(equity * 100) / 100,
                finalEquityStr: Math.round(equity).toLocaleString('zh-TW'),
                avgWin: Math.round(avgWin),
                avgWinStr: `+${{Math.round(avgWin).toLocaleString('zh-TW')}}`,
                avgLoss: Math.round(avgLoss),
                avgLossStr: `-${{Math.round(avgLoss).toLocaleString('zh-TW')}}`,
                payoffRatio: Math.round(payoff * 100) / 100,
                payoffRatioStr: payoff.toFixed(2)
            }};

            // 7. 產生買賣 Markers
            const markers = [];
            for (const t of trades) {{
                const isLong = (t.side === 'LONG');
                markers.push({{
                    rawTime: t.entry_ts,
                    time: t.entry_ts,
                    position: isLong ? 'belowBar' : 'aboveBar',
                    color: isLong ? '#089981' : '#f23645',
                    shape: isLong ? 'arrowUp' : 'arrowDown',
                    text: isLong ? `買進 ${{t.qty}}口` : `放空 ${{t.qty}}口`,
                    tradeId: t.trade_id,
                    type: 'entry',
                    side: t.side,
                    price: t.entry_price,
                    qty: t.qty,
                    timeStr: t.entry_time
                }});

                let markerColor = '#8b949e';
                if (t.exit_reason === '停損') markerColor = '#f23645';
                else if (t.exit_reason === 'ATR停利') markerColor = '#089981';
                else if (t.exit_reason === '唐奇安出場') markerColor = '#00e5ff';
                else if (t.exit_reason === '超級趨勢出場') markerColor = '#f0b90b';

                const pnlSign = t.net_pnl >= 0 ? '+' : '';
                markers.push({{
                    rawTime: t.exit_ts,
                    time: t.exit_ts,
                    position: isLong ? 'aboveBar' : 'belowBar',
                    color: markerColor,
                    shape: 'circle',
                    text: `${{t.exit_reason}} (${{pnlSign}}${{Math.round(t.net_pnl).toLocaleString('zh-TW')}})`,
                    tradeId: t.trade_id,
                    type: 'exit',
                    side: t.side,
                    price: t.exit_price,
                    qty: t.qty,
                    pnl: t.net_pnl,
                    reason: t.exit_reason,
                    timeStr: t.exit_time
                }});
            }}
            markers.sort((a, b) => (a.rawTime !== b.rawTime ? a.rawTime - b.rawTime : (a.type === 'entry' ? -1 : 1)));

            return {{
                metrics,
                trades,
                cumulative_pnl: equityCurve,
                markers
            }};
        }}

        // 策略參數集中配置庫
        const strategyConfigs = {{
            donchian: {{
                strategy_name: '布林_唐奇安',
                exit_mode: 'donchian',
                bb_length: 30,
                bb_mult: 1.75,
                pullback_ratio: 0.25,
                donchian_length: 4,
                max_leverage: 7.0,
                commission: 18.0,
                slippage: 0.0
            }},
            supertrend: {{
                strategy_name: '布林_超級趨勢',
                exit_mode: 'supertrend',
                bb_length: 30,
                bb_mult: 1.75,
                pullback_ratio: 0.25,
                supertrend_length: 14,
                supertrend_multiplier: 2.0,
                max_leverage: 7.0,
                commission: 18.0,
                slippage: 0.0
            }},
            atr: {{
                strategy_name: '布林_ATR基準',
                exit_mode: 'atr',
                bb_length: 30,
                bb_mult: 1.75,
                pullback_ratio: 0.25,
                atr_length: 14,
                atr_sig_length: 5,
                max_leverage: 7.0,
                commission: 18.0,
                slippage: 0.0
            }}
        }};
        const defaultStrategyConfigs = JSON.parse(JSON.stringify(strategyConfigs));
        let activeModalStrat = 'donchian';

        // 刷新頂部多策略橫向 KPI 矩陣卡片
        function updateKpiCard(stratKey) {{
            const strat = STRATEGIES_DATA[stratKey];
            if (!strat) return;
            const m = strat.metrics;
            const pnlEl = document.getElementById(`kpi-${{stratKey}}-pnl`);
            const winrateEl = document.getElementById(`kpi-${{stratKey}}-winrate`);
            const pfEl = document.getElementById(`kpi-${{stratKey}}-pf`);
            const avgwinEl = document.getElementById(`kpi-${{stratKey}}-avgwin`);
            const mddEl = document.getElementById(`kpi-${{stratKey}}-mdd`);
            const tradesEl = document.getElementById(`kpi-${{stratKey}}-trades`);
            const avglossEl = document.getElementById(`kpi-${{stratKey}}-avgloss`);
            const payoffEl = document.getElementById(`kpi-${{stratKey}}-payoff`);

            if (pnlEl) {{
                pnlEl.textContent = m.totalNetProfitStr;
                pnlEl.className = `kpi-sub-val ${{m.isProfit ? 'win' : 'loss'}}`;
            }}
            if (winrateEl) winrateEl.textContent = m.winRateStr;
            if (pfEl) pfEl.textContent = m.profitFactorStr;
            if (avgwinEl) avgwinEl.textContent = m.avgWinStr;
            if (mddEl) mddEl.textContent = m.maxDrawdownStr;
            if (tradesEl) tradesEl.textContent = `${{m.totalTrades}} 筆`;
            if (avglossEl) avglossEl.textContent = m.avgLossStr;
            if (payoffEl) payoffEl.textContent = m.payoffRatioStr;
        }}

        // 執行動態回測並動態連動刷新所有組件
        function executeDynamicBacktest(stratKey, customConfig) {{
            const bars5m = KLINE_DATA['5m'] || [];
            if (bars5m.length === 0) return;

            const t0 = performance.now();
            const res = runFastBacktest(bars5m, customConfig);
            const t1 = performance.now();
            const elapsedMs = Math.round((t1 - t0) * 10) / 10;

            // 寫回全域資料庫
            STRATEGIES_DATA[stratKey].metrics = res.metrics;
            STRATEGIES_DATA[stratKey].trades = res.trades;
            STRATEGIES_DATA[stratKey].cumulative_pnl = res.cumulative_pnl;
            STRATEGIES_DATA[stratKey].markers = res.markers;

            // 更新多策略 KPI 卡片
            updateKpiCard(stratKey);

            // 更新多策略淨值圖
            if (multiCompareChart) {{
                multiCompareChart.initData();
                multiCompareChart.draw();
            }}

            // 若目前活躍策略為該策略，同步更新所有視圖
            if (currentStrategyKey === stratKey) {{
                renderKeyMetrics();
                renderTradesTable();
                if (tradeEquityChart) {{
                    tradeEquityChart.setTrades();
                    tradeEquityChart.draw();
                }}
                updateBaselineData();
                if (showMarkers) {{
                    const curBars = KLINE_DATA[currentInterval] || [];
                    applyMarkersToChart(alignMarkersToBars(res.markers, curBars));
                }}
            }}

            // 若布林長度或標準差有異動，動態更新主圖布林通道
            if (customConfig.bb_length || customConfig.bb_mult) {{
                currentBBConfig.length = Number(customConfig.bb_length) || currentBBConfig.length;
                currentBBConfig.mult = Number(customConfig.bb_mult) || currentBBConfig.mult;
                updateBollingerBands(KLINE_DATA[currentInterval] || []);
            }}

            // 更新 Modal 預覽數值
            updateModalPreviewBox(res.metrics, elapsedMs);

            showToast(`⚡ 回測完成！耗時 ${{elapsedMs}} ms ｜ ${{STRATEGIES_DATA[stratKey].displayName}} 淨利: ${{res.metrics.totalNetProfitStr}} TWD ｜ 勝率: ${{res.metrics.winRateStr}} ｜ PF: ${{res.metrics.profitFactorStr}}`, 'success');
        }}

        function updateModalPreviewBox(m, elapsedMs) {{
            const pnlEl = document.getElementById('res-preview-pnl');
            const winrateEl = document.getElementById('res-preview-winrate');
            const pfEl = document.getElementById('res-preview-pf');
            const tradesEl = document.getElementById('res-preview-trades');
            const mddEl = document.getElementById('res-preview-mdd');
            const payoffEl = document.getElementById('res-preview-payoff');
            const timeEl = document.getElementById('res-preview-time');

            if (pnlEl) {{
                pnlEl.textContent = m.totalNetProfitStr;
                pnlEl.style.color = m.isProfit ? '#00e5ff' : '#ef5350';
            }}
            if (winrateEl) winrateEl.textContent = m.winRateStr;
            if (pfEl) pfEl.textContent = m.profitFactorStr;
            if (tradesEl) tradesEl.textContent = `${{m.totalTrades}} 筆`;
            if (mddEl) mddEl.textContent = m.maxDrawdownStr;
            if (payoffEl) payoffEl.textContent = m.payoffRatioStr;
            if (timeEl && elapsedMs !== undefined) timeEl.textContent = `⚡ 運算耗時: ${{elapsedMs}} ms`;
        }}

        // 開啟 Modal 並設定輸入欄位
        function openParamsModal(stratKey) {{
            activeModalStrat = stratKey || currentStrategyKey || 'donchian';
            const modal = document.getElementById('params-modal-overlay');
            if (!modal) return;
            modal.classList.add('open');

            // 更新策略標籤
            document.querySelectorAll('.params-strat-tab').forEach(tab => {{
                tab.classList.toggle('active', tab.dataset.strat === activeModalStrat);
            }});

            populateModalInputs(activeModalStrat);
        }}

        function populateModalInputs(stratKey) {{
            const cfg = strategyConfigs[stratKey];
            if (!cfg) return;

            document.getElementById('txt-exit-strat-name').textContent = STRATEGIES_DATA[stratKey].displayName;
            document.getElementById('res-preview-title').textContent = STRATEGIES_DATA[stratKey].displayName;

            // 切換出場面板
            document.getElementById('panel-exit-donchian').style.display = (stratKey === 'donchian') ? 'grid' : 'none';
            document.getElementById('panel-exit-supertrend').style.display = (stratKey === 'supertrend') ? 'grid' : 'none';
            document.getElementById('panel-exit-atr').style.display = (stratKey === 'atr') ? 'grid' : 'none';

            // 進場欄位
            setFieldSync('bb-len', cfg.bb_length);
            setFieldSync('bb-mult', cfg.bb_mult);
            setFieldSync('pullback', cfg.pullback_ratio);

            // 出場欄位
            if (stratKey === 'donchian') {{
                setFieldSync('donchian-len', cfg.donchian_length);
            }} else if (stratKey === 'supertrend') {{
                setFieldSync('st-len', cfg.supertrend_length);
                setFieldSync('st-mult', cfg.supertrend_multiplier);
            }} else if (stratKey === 'atr') {{
                setFieldSync('atr-len', cfg.atr_length);
                setFieldSync('atr-sig', cfg.atr_sig_length);
            }}

            // 資金欄位
            setFieldSync('leverage', cfg.max_leverage);
            document.getElementById('num-commission').value = cfg.commission;
            document.getElementById('val-badge-commission').textContent = `${{cfg.commission}} 元`;
            setFieldSync('slippage', cfg.slippage);

            // 預覽當前已存在指標
            const curM = STRATEGIES_DATA[stratKey].metrics;
            if (curM) updateModalPreviewBox(curM);
        }}

        function setFieldSync(idPrefix, val) {{
            const range = document.getElementById(`range-${{idPrefix}}`);
            const num = document.getElementById(`num-${{idPrefix}}`);
            const badge = document.getElementById(`val-badge-${{idPrefix}}`);
            if (range) range.value = val;
            if (num) num.value = val;
            if (badge) {{
                if (idPrefix === 'pullback') badge.textContent = `${{val}} (${{Math.round(val * 100)}}% K棒)`;
                else if (idPrefix === 'leverage') badge.textContent = `${{val}}x`;
                else if (idPrefix === 'slippage') badge.textContent = `${{val}} 點`;
                else badge.textContent = val;
            }}
        }}

        function initParamsModal() {{
            // 策略標籤切換
            document.querySelectorAll('.params-strat-tab').forEach(tab => {{
                tab.addEventListener('click', () => {{
                    activeModalStrat = tab.dataset.strat;
                    document.querySelectorAll('.params-strat-tab').forEach(t => t.classList.remove('active'));
                    tab.classList.add('active');
                    populateModalInputs(activeModalStrat);
                }});
            }});

            // 綁定 Slider 與 Input 雙向連動
            const fieldKeys = [
                'bb-len', 'bb-mult', 'pullback',
                'donchian-len', 'st-len', 'st-mult',
                'atr-len', 'atr-sig', 'leverage', 'slippage'
            ];

            fieldKeys.forEach(k => {{
                const range = document.getElementById(`range-${{k}}`);
                const num = document.getElementById(`num-${{k}}`);
                const badge = document.getElementById(`val-badge-${{k}}`);

                if (range && num) {{
                    range.addEventListener('input', () => {{
                        num.value = range.value;
                        if (badge) {{
                            if (k === 'pullback') badge.textContent = `${{range.value}} (${{Math.round(range.value * 100)}}% K棒)`;
                            else if (k === 'leverage') badge.textContent = `${{range.value}}x`;
                            else if (k === 'slippage') badge.textContent = `${{range.value}} 點`;
                            else badge.textContent = range.value;
                        }}
                    }});
                    num.addEventListener('input', () => {{
                        range.value = num.value;
                        if (badge) {{
                            if (k === 'pullback') badge.textContent = `${{num.value}} (${{Math.round(num.value * 100)}}% K棒)`;
                            else if (k === 'leverage') badge.textContent = `${{num.value}}x`;
                            else if (k === 'slippage') badge.textContent = `${{num.value}} 點`;
                            else badge.textContent = num.value;
                        }}
                    }});
                }}
            }});

            // 手續費單獨輸入綁定
            const numComm = document.getElementById('num-commission');
            if (numComm) {{
                numComm.addEventListener('input', () => {{
                    const b = document.getElementById('val-badge-commission');
                    if (b) b.textContent = `${{numComm.value}} 元`;
                }});
            }}

            // 執行回測按鈕
            if (btnRunBacktest) {{
                btnRunBacktest.addEventListener('click', () => {{
                    const cfg = strategyConfigs[activeModalStrat];
                    cfg.bb_length = Number(document.getElementById('num-bb-len').value);
                    cfg.bb_mult = Number(document.getElementById('num-bb-mult').value);
                    cfg.pullback_ratio = Number(document.getElementById('num-pullback').value);

                    if (activeModalStrat === 'donchian') {{
                        cfg.donchian_length = Number(document.getElementById('num-donchian-len').value);
                    }} else if (activeModalStrat === 'supertrend') {{
                        cfg.supertrend_length = Number(document.getElementById('num-st-len').value);
                        cfg.supertrend_multiplier = Number(document.getElementById('num-st-mult').value);
                    }} else if (activeModalStrat === 'atr') {{
                        cfg.atr_length = Number(document.getElementById('num-atr-len').value);
                        cfg.atr_sig_length = Number(document.getElementById('num-atr-sig').value);
                    }}

                    cfg.max_leverage = Number(document.getElementById('num-leverage').value);
                    cfg.commission = Number(document.getElementById('num-commission').value);
                    cfg.slippage = Number(document.getElementById('num-slippage').value);

                    executeDynamicBacktest(activeModalStrat, cfg);
                }});
            }}

            // 重設為初始預設值按鈕
            if (btnResetParams) {{
                btnResetParams.addEventListener('click', () => {{
                    strategyConfigs[activeModalStrat] = JSON.parse(JSON.stringify(defaultStrategyConfigs[activeModalStrat]));
                    populateModalInputs(activeModalStrat);
                    executeDynamicBacktest(activeModalStrat, strategyConfigs[activeModalStrat]);
                    showToast(`已重設 [${{STRATEGIES_DATA[activeModalStrat].displayName}}] 為初始預設參數`, 'info');
                }});
            }}
        }}

        // 切換活躍策略
        function switchActiveStrategy(stratKey) {{
            if (!STRATEGIES_DATA[stratKey]) return;
            currentStrategyKey = stratKey;

            // 更新頂部策略按鈕狀態
            document.querySelectorAll('.strat-btn').forEach(btn => {{
                btn.classList.toggle('active', btn.dataset.strat === stratKey);
            }});

            // 更新關鍵數據卡片
            renderKeyMetrics();

            // 更新主圖 Markers
            const bars = KLINE_DATA[currentInterval] || [];
            const strat = getActiveStrategy();
            if (showMarkers) {{
                const alignedMarkers = alignMarkersToBars(strat.markers, bars);
                applyMarkersToChart(alignedMarkers);
            }}

            // 更新時間軸 Baseline 資料
            updateBaselineData();

            // 更新交易表格
            renderTradesTable();

            // 更新逐筆損益圖
            if (tradeEquityChart) {{
                tradeEquityChart.setTrades();
                tradeEquityChart.draw();
            }}

            showToast(`已切換活躍策略為: ${{strat.displayName}}`, 'success');
        }}

        // 切換下半部分頁標籤
        function setupTabEvents() {{
            const tabs = [
                {{ tab: tabMultiCompare, wrapper: multiCompareWrapper }},
                {{ tab: tabTradeEquity, wrapper: tradeEquityWrapper }},
                {{ tab: tabCumPnl, wrapper: perfChartWrapper }},
                {{ tab: tabTradesTable, wrapper: tradesTableWrapper }}
            ];

            tabs.forEach(({{ tab, wrapper }}) => {{
                tab.addEventListener('click', () => {{
                    tabs.forEach(t => {{
                        t.tab.classList.remove('active');
                        t.wrapper.style.display = 'none';
                    }});

                    tab.classList.add('active');
                    wrapper.style.display = 'flex';

                    // 觸發對應視圖重繪
                    if (tab === tabMultiCompare && multiCompareChart) {{
                        setTimeout(() => multiCompareChart.resizeAndDraw(), 50);
                    }} else if (tab === tabTradeEquity && tradeEquityChart) {{
                        setTimeout(() => tradeEquityChart.resizeAndDraw(), 50);
                    }} else if (tab === tabCumPnl && perfChart) {{
                        setTimeout(() => perfChart.timeScale().fitContent(), 50);
                    }}
                }});
            }});
        }}

        // 事件監聽綁定
        function setupEventListeners() {{
            // 頂部全畫面切換按鈕
            const btnToggleLayout = document.getElementById('btn-toggle-layout');
            if (btnToggleLayout) {{
                btnToggleLayout.addEventListener('click', toggleLayoutMode);
            }}

            // 布林通道開關
            if (btnToggleBb) {{
                btnToggleBb.addEventListener('click', () => {{
                    showBollingerBands = !showBollingerBands;
                    btnToggleBb.classList.toggle('active', showBollingerBands);
                    if (txtToggleBb) txtToggleBb.textContent = `🎯 布林軌道: ${{showBollingerBands ? '顯示' : '隱藏'}}`;
                    const bars = KLINE_DATA[currentInterval] || [];
                    updateBollingerBands(bars);
                    showToast(`布林通道已${{showBollingerBands ? '開啟顯示' : '隱藏'}}`, 'info');
                }});
            }}

            // 策略參數調校 Modal 開啟 / 關閉
            if (btnOpenParamsModal) {{
                btnOpenParamsModal.addEventListener('click', () => {{
                    openParamsModal();
                }});
            }}
            if (btnCloseParamsModal && paramsModalOverlay) {{
                btnCloseParamsModal.addEventListener('click', () => {{
                    paramsModalOverlay.classList.remove('open');
                }});
            }}
            if (paramsModalOverlay) {{
                paramsModalOverlay.addEventListener('click', (e) => {{
                    if (e.target === paramsModalOverlay) {{
                        paramsModalOverlay.classList.remove('open');
                    }}
                }});
            }}

            // 初始化參數調校控制器
            initParamsModal();

            // 頂部策略切換
            document.querySelectorAll('.strat-btn').forEach(btn => {{
                btn.addEventListener('click', () => {{
                    switchActiveStrategy(btn.dataset.strat);
                }});
            }});

            // 週期切換
            document.querySelectorAll('.tf-btn').forEach(btn => {{
                btn.addEventListener('click', () => {{
                    document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    loadIntervalData(btn.dataset.interval);
                }});
            }});

            // Markers 開關
            const toggleMarkersBtn = document.getElementById('toggle-markers-btn');
            if (toggleMarkersBtn) {{
                toggleMarkersBtn.addEventListener('click', () => {{
                    showMarkers = !showMarkers;
                    toggleMarkersBtn.classList.toggle('active', showMarkers);
                    toggleMarkersBtn.querySelector('span').textContent = `📍 交易標記: ${{showMarkers ? '顯示' : '隱藏'}}`;
                    const bars = KLINE_DATA[currentInterval] || [];
                    const strat = getActiveStrategy();
                    applyMarkersToChart(showMarkers ? alignMarkersToBars(strat.markers, bars) : []);
                }});
            }}

            // 適配縮放按鈕
            const fitBtn = document.getElementById('btn-fit-chart');
            if (fitBtn) {{
                fitBtn.addEventListener('click', () => {{
                    if (klineChart && !isSoloDataMode) klineChart.timeScale().fitContent();
                    if (perfChart) perfChart.timeScale().fitContent();
                    if (multiCompareChart) multiCompareChart.resizeAndDraw();
                    if (tradeEquityChart) tradeEquityChart.resizeAndDraw();
                }});
            }}

            const resetZoomBtn = document.getElementById('reset-zoom-btn');
            if (resetZoomBtn) {{
                resetZoomBtn.addEventListener('click', () => {{
                    if (klineChart && !isSoloDataMode) klineChart.timeScale().fitContent();
                    if (perfChart) perfChart.timeScale().fitContent();
                }});
            }}

            // 切換對比模式 (損益 / 總淨值)
            const toggleCompareModeBtn = document.getElementById('btn-toggle-compare-mode');
            if (toggleCompareModeBtn) {{
                toggleCompareModeBtn.addEventListener('click', () => {{
                    if (multiCompareChart) {{
                        const m = multiCompareChart.toggleMode();
                        showToast(`已切換對比模式為: ${{m === 'PNL' ? '累積淨損益 (TWD)' : '帳戶總權益 (TWD)'}}`, 'success');
                    }}
                }});
            }}

            // 圖例開關
            const chkDonchian = document.getElementById('chk-donchian');
            const chkSupertrend = document.getElementById('chk-supertrend');
            const chkAtr = document.getElementById('chk-atr');

            if (chkDonchian) chkDonchian.addEventListener('change', (e) => multiCompareChart && multiCompareChart.toggleSeries('donchian', e.target.checked));
            if (chkSupertrend) chkSupertrend.addEventListener('change', (e) => multiCompareChart && multiCompareChart.toggleSeries('supertrend', e.target.checked));
            if (chkAtr) chkAtr.addEventListener('change', (e) => multiCompareChart && multiCompareChart.toggleSeries('atr', e.target.checked));
        }}

        // Toast 通知
        function showToast(msg, type = 'info') {{
            const c = document.getElementById('toast-container');
            if (!c) return;
            const t = document.createElement('div');
            t.className = `toast-card ${{type}}`;
            t.textContent = msg;
            c.appendChild(t);
            setTimeout(() => {{
                t.style.opacity = '0';
                t.style.transform = 'translateX(100%)';
                t.style.transition = 'all 0.3s';
                setTimeout(() => t.remove(), 300);
            }}, 3000);
        }}

        // WebSocket 客戶端
        function initWebSocketClient() {{
            const badge = document.getElementById('live-status-badge');
            const text = document.getElementById('live-status-text');

            function connect() {{
                const wsUrl = `ws://${{window.location.hostname || '127.0.0.1'}}:8000/ws/market`;
                let ws = null;
                try {{
                    ws = new WebSocket(wsUrl);
                }} catch (e) {{
                    return;
                }}

                ws.onopen = () => {{
                    if (badge) {{ badge.className = 'live-badge online'; }}
                    if (text) {{ text.textContent = '串流: 連線中'; }}
                }};

                ws.onclose = () => {{
                    if (badge) {{ badge.className = 'live-badge offline'; }}
                    if (text) {{ text.textContent = '串流: 離線'; }}
                    setTimeout(connect, 5000);
                }};

                ws.onerror = () => {{
                    if (ws) ws.close();
                }};
            }}

            connect();
        }}

        // 全域初始化入口
        function initDashboard() {{
            renderKeyMetrics();
            initKlineChart();
            initPerfChart();

            // 初始化多策略同屏對比圖表
            if (multiCanvas && multiViewport && multiTooltip) {{
                multiCompareChart = new MultiStrategyCompareChart(multiCanvas, multiViewport, multiTooltip);
                window.multiCompareChart = multiCompareChart;
                setTimeout(() => multiCompareChart.resizeAndDraw(), 100);
            }}

            // 初始化單策略逐筆損益圖
            if (tradeEquityCanvas && canvasScrollViewport && tradeTooltip) {{
                tradeEquityChart = new TradeEquityChart(tradeEquityCanvas, canvasScrollViewport, tradeTooltip);
                window.tradeEquityChart = tradeEquityChart;
                setTimeout(() => tradeEquityChart.resizeAndDraw(), 100);
            }}

            setupTabEvents();
            setupTimeScaleSync();
            setupEventListeners();
            renderTradesTable();
            loadIntervalData(currentInterval);
            initWebSocketClient();

            // 若預設為全畫面數據視圖，確保 Canvas 適配容器全尺寸
            if (isSoloDataMode && multiCompareChart) {{
                setTimeout(() => multiCompareChart.resizeAndDraw(), 200);
            }}
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
    執行多週期聚合、三大策略回測運算並產生支援直觀對比之 HTML 儀表板檔案。

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

    bars_5m = intervals_data.get("5m", [])
    if not bars_5m:
        raise ValueError("Status: ERROR | Root Cause: 缺少 5 分 K 資料無法進行策略回測 | Suggested Fix: 確認資料庫收錄時序。")

    # 同步執行三大策略回測運算 (唐奇安 vs 超級趨勢 vs ATR基準)
    logger.info("開始執行多策略同步回測運算...")
    multi_strategies_data = run_all_strategies_backtest(bars_5m)

    # 讀取本機 Lightweight Charts 獨立生產版本腳本
    standalone_js = read_standalone_lightweight_charts()

    html_content = build_html_content(
        intervals_data=intervals_data,
        multi_strategies_data=multi_strategies_data,
        standalone_js=standalone_js
    )

    output_abs = os.path.abspath(output_html_path)
    with open(output_abs, "w", encoding="utf-8") as f:
        f.write(html_content)

    file_size_mb = os.path.getsize(output_abs) / (1024.0 * 1024.0)
    logger.info(f"K 線與多策略直觀對比儀表板成功產出: {output_abs} (檔案大小: {file_size_mb:.2f} MB)")
    return output_abs


if __name__ == "__main__":
    generate_dashboard()
