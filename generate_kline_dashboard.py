"""
K 線與布林_ATR基準策略回測互動網頁儀表板生成器 (generate_kline_dashboard.py)

從 services/storage/kline_service.py 讀取微台10在 SQLite 中的高頻時序資料，
聚合 11 種時間週期，並調用 services/backtest/engine.py 執行：
- 布林_ATR基準 (布林動能突破 + 動態 ATR 停利基準)

產出自給自足（Self-contained）、支援離線開啟與 TradingView 暗黑配色的互動式多圖儀表板：
1. 預設啟動【全畫面數據視圖】（Solo Data View）：
   - 隱藏上方 K 線圖，下方 KPI 矩陣與淨值曲線佔滿 100% 畫面高。
   - 頂部工具列提供一鍵展開/收合上方 K 線主圖之切換開關。
2. 單策略逐筆淨值圖採用【橫軸 X = 交易次數 × 縱軸 Y = 累積損益 (TWD) & 帳戶淨值】佈局。
3. 下圖五大分頁（全介面無 Emoji 純淨專業排版）：
   - 策略淨值與回撤總覽：量化 KPI 矩陣 + 累積損益 / 帳戶淨值 / 水下回撤曲線圖。
   - 深度結構分析：多空雙向拆解、出場機制歸因、日夜盤時段統計與單筆損益分佈直方圖。
   - 單策略逐筆淨值圖：固定 X 軸 (交易次數) × Y 軸 (累積損益 / 帳戶淨值) 之 Canvas 圖表。
   - 時間時序走勢：擬真 TradingView 0 軸分界紅綠漸層累計損益走勢圖 (BaselineSeries)。
   - 交易明細清單：完整 100% 對齊 CSV 之逐筆成交稽核表格，支援一鍵點擊定位上圖 K 線。

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


def compute_extended_quant_stats(
    trades_list: List[Dict[str, Any]],
    total_net_profit: float,
    max_drawdown: float
) -> Dict[str, Any]:
    """
    計算進階機構級量化統計指標（期望值、風報比、夏普比率、最大連勝連敗、
    持倉時間分佈、多空雙向拆解、出場原因歸因、日夜盤時段統計）。

    參數:
        trades_list (List[Dict[str, Any]]): 序列化後之逐筆交易字典列表。
        total_net_profit (float): 總淨損益 (TWD)。
        max_drawdown (float): 最大回撤金額 (TWD)。

    返回:
        Dict[str, Any]: 進階量化指標與多維度結構分析字典。
    """
    import math

    n_trades = len(trades_list)
    if n_trades == 0:
        return {
            "expectancy": 0.0,
            "expectancyStr": "0",
            "returnToMdd": 0.0,
            "returnToMddStr": "0.00",
            "sharpeRatio": 0.0,
            "sharpeRatioStr": "0.00",
            "maxConsecWins": 0,
            "maxConsecLosses": 0,
            "streakStr": "0連勝 / 0連敗",
            "avgHoldMins": 0.0,
            "avgWinHoldMins": 0.0,
            "avgLossHoldMins": 0.0,
            "avgHoldStr": "0 分",
            "longStats": {"count": 0, "wins": 0, "winRate": 0.0, "netPnl": 0.0, "pf": 0.0},
            "shortStats": {"count": 0, "wins": 0, "winRate": 0.0, "netPnl": 0.0, "pf": 0.0},
            "sessionStats": {
                "day": {"count": 0, "wins": 0, "winRate": 0.0, "netPnl": 0.0},
                "night": {"count": 0, "wins": 0, "winRate": 0.0, "netPnl": 0.0}
            },
            "exitBreakdown": []
        }

    # 1. 期望值與風報比
    expectancy = total_net_profit / n_trades
    exp_sign = "+" if expectancy >= 0 else ""
    abs_mdd = abs(max_drawdown)
    ret_to_mdd = (total_net_profit / abs_mdd) if abs_mdd > 0 else 0.0

    # 2. 單筆損益標準差與交易夏普比率 (Annualized Trade Sharpe Proxy)
    pnls = [t["net_pnl"] for t in trades_list]
    mean_pnl = sum(pnls) / n_trades
    variance = sum((x - mean_pnl) ** 2 for x in pnls) / max(1, n_trades - 1)
    std_pnl = math.sqrt(variance) if variance > 0 else 0.0
    sharpe = (mean_pnl / std_pnl) * math.sqrt(n_trades) if std_pnl > 0 else 0.0

    # 3. 最大連勝與最大連敗
    max_w, max_l, cur_w, cur_l = 0, 0, 0, 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
            if cur_w > max_w:
                max_w = cur_w
        elif p < 0:
            cur_l += 1
            cur_w = 0
            if cur_l > max_l:
                max_l = cur_l

    # 4. 持倉時間統計
    hold_all = [t.get("hold_mins", 0.0) for t in trades_list]
    hold_wins = [t.get("hold_mins", 0.0) for t in trades_list if t["net_pnl"] > 0]
    hold_losses = [t.get("hold_mins", 0.0) for t in trades_list if t["net_pnl"] <= 0]
    avg_hold = sum(hold_all) / len(hold_all) if hold_all else 0.0
    avg_win_hold = sum(hold_wins) / len(hold_wins) if hold_wins else 0.0
    avg_loss_hold = sum(hold_losses) / len(hold_losses) if hold_losses else 0.0

    # 5. 多空雙向拆解
    def _calc_side(sub_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        cnt = len(sub_list)
        if cnt == 0:
            return {"count": 0, "wins": 0, "winRate": 0.0, "netPnl": 0.0, "pf": 0.0}
        w_list = [x["net_pnl"] for x in sub_list if x["net_pnl"] > 0]
        l_list = [x["net_pnl"] for x in sub_list if x["net_pnl"] < 0]
        gw = sum(w_list)
        gl = abs(sum(l_list))
        pf = (gw / gl) if gl > 0 else (99.0 if gw > 0 else 0.0)
        return {
            "count": cnt,
            "wins": len(w_list),
            "winRate": round((len(w_list) / cnt) * 100.0, 2),
            "netPnl": round(sum(x["net_pnl"] for x in sub_list), 1),
            "pf": round(pf, 2)
        }

    long_trades = [t for t in trades_list if t["side"] == "LONG"]
    short_trades = [t for t in trades_list if t["side"] == "SHORT"]

    # 6. 日盤 (08:45-13:45) vs 夜盤 (15:00-05:00) 拆解
    day_trades: List[Dict[str, Any]] = []
    night_trades: List[Dict[str, Any]] = []
    for t in trades_list:
        ts_str = str(t.get("entry_time", ""))
        hour = 9
        if len(ts_str) >= 13:
            try:
                hour = int(ts_str[11:13])
            except ValueError:
                hour = 9
        if 8 <= hour <= 13:
            day_trades.append(t)
        else:
            night_trades.append(t)

    # 7. 出場原因歸因分析
    reason_map: Dict[str, List[float]] = {}
    for t in trades_list:
        r = t["exit_reason"]
        reason_map.setdefault(r, []).append(t["net_pnl"])

    exit_breakdown: List[Dict[str, Any]] = []
    for r_name, r_pnls in reason_map.items():
        r_cnt = len(r_pnls)
        r_tot = sum(r_pnls)
        r_wins = sum(1 for v in r_pnls if v > 0)
        exit_breakdown.append({
            "reason": r_name,
            "count": r_cnt,
            "ratioPct": round((r_cnt / n_trades) * 100.0, 1),
            "totalPnl": round(r_tot, 1),
            "avgPnl": round(r_tot / r_cnt, 1),
            "winRate": round((r_wins / r_cnt) * 100.0, 1)
        })

    return {
        "expectancy": round(expectancy, 1),
        "expectancyStr": f"{exp_sign}{expectancy:,.0f}",
        "returnToMdd": round(ret_to_mdd, 2),
        "returnToMddStr": f"{ret_to_mdd:.2f}",
        "sharpeRatio": round(sharpe, 2),
        "sharpeRatioStr": f"{sharpe:.2f}",
        "maxConsecWins": max_w,
        "maxConsecLosses": max_l,
        "streakStr": f"{max_w}連勝 / {max_l}連敗",
        "avgHoldMins": round(avg_hold, 1),
        "avgWinHoldMins": round(avg_win_hold, 1),
        "avgLossHoldMins": round(avg_loss_hold, 1),
        "avgHoldStr": f"{avg_hold:.0f} 分 ({avg_hold / 5.0:.1f} 根)",
        "longStats": _calc_side(long_trades),
        "shortStats": _calc_side(short_trades),
        "sessionStats": {
            "day": _calc_side(day_trades),
            "night": _calc_side(night_trades)
        },
        "exitBreakdown": exit_breakdown
    }


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
        Dict[str, Any]: 包含 metrics, markers, cumulative_pnl, trades 之字典。
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

    # 2. 建立時間軸累計損益曲線 (含即時回撤計算)
    cumulative_pnl: List[Dict[str, Any]] = []
    initial_cap = config.initial_capital
    running_peak = initial_cap
    for pt in metrics.equity_curve:
        ts_sec = int(pd.Timestamp(pt["ts"]).timestamp())
        eq_val = round(pt["equity"], 2)
        if eq_val > running_peak:
            running_peak = eq_val
        dd_val = round(eq_val - running_peak, 2)  # 負值或 0，代表水下回撤金額
        cum_pnl = round(eq_val - initial_cap, 2)
        cumulative_pnl.append({
            "time": ts_sec,
            "value": cum_pnl,
            "equity": eq_val,
            "drawdown": dd_val,
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
        else:
            exit_reason_zh = "結算"

        ent_ts = int(pd.Timestamp(t.entry_time).timestamp())
        ext_ts = int(pd.Timestamp(t.exit_time).timestamp())
        hold_mins = max(5.0, round((ext_ts - ent_ts) / 60.0, 1))

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
            "contract_value": float(t.contract_value),
            "effective_leverage": float(t.effective_leverage),
            "margin_ratio_pct": float(t.margin_ratio_pct),
            "entry_ts": ent_ts,
            "exit_ts": ext_ts,
            "hold_mins": hold_mins
        })

    # 4. 量化進階指標計算 (贏均、輸均、賺賠比 + 深度結構統計)
    wins = [t.net_pnl for t in metrics.trades if t.net_pnl > 0]
    losses = [t.net_pnl for t in metrics.trades if t.net_pnl < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    ext_stats = compute_extended_quant_stats(
        trades_list=trades_list,
        total_net_profit=float(metrics.total_net_profit),
        max_drawdown=float(metrics.max_drawdown)
    )

    net_pnl_sign = "+" if metrics.total_net_profit >= 0 else ""
    return_sign = "+" if metrics.total_return_pct >= 0 else ""
    metrics_dict = {
        "strategyName": config.strategy_name,
        "totalNetProfit": float(metrics.total_net_profit),
        "totalNetProfitStr": f"{net_pnl_sign}{metrics.total_net_profit:,.0f}",
        "totalReturnPct": float(metrics.total_return_pct),
        "totalReturnPctStr": f"{return_sign}{metrics.total_return_pct:.2f}%",
        "isProfit": metrics.total_net_profit >= 0,
        "maxDrawdown": float(metrics.max_drawdown),
        "maxDrawdownStr": f"-{abs(metrics.max_drawdown):,.0f}",
        "maxDrawdownPct": float(metrics.max_drawdown_pct),
        "maxDrawdownPctStr": f"-{abs(metrics.max_drawdown_pct):.2f}%",
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
        "finalEquityStr": f"{metrics.final_equity:,.0f}",
        "avgWin": float(avg_win),
        "avgWinStr": f"+{avg_win:,.0f}",
        "avgLoss": float(avg_loss),
        "avgLossStr": f"-{avg_loss:,.0f}",
        "payoffRatio": float(payoff_ratio),
        "payoffRatioStr": f"{payoff_ratio:.2f}",
        **ext_stats
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
        f"PF {metrics.profit_factor:.3f}, 期望值 {ext_stats['expectancyStr']} 元, 風報比 {ext_stats['returnToMddStr']}"
    )

    return {
        "metrics": metrics_dict,
        "markers": markers,
        "cumulative_pnl": cumulative_pnl,
        "trades": trades_list
    }


def run_all_strategies_backtest(bars_5m: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    針對 5 分 K 線執行布林動能突破 (ATR 基準 + 130% 整戶維持率控管) 策略回測。

    參數:
        bars_5m (List[Dict[str, Any]]): 5 分鐘 K 線資料列表。

    返回:
        Dict[str, Any]: 包含布林_ATR基準策略回測數據之字典。
    """
    df = pd.DataFrame(bars_5m)
    df["ts"] = pd.to_datetime(df["datetime"])

    cfg_atr = BacktestConfig(
        strategy_name="布林動能突破 (ATR基準)",
        exit_mode="atr",
        atr_length=14,
        atr_sig_length=5,
        bb_length=30,
        bb_mult=1.75,
        initial_capital=105150.0,
        margin_req=35050.0,
        maint_margin=26900.0,
        min_margin_ratio=1.30,
        position_sizing_mode="margin_ratio_130",
        commission_per_order=18.0,
        tax_rate=0.00002
    )
    res_atr = run_single_strategy_backtest(df, cfg_atr, "trades_atr.csv")

    # 複製預設 trades.csv
    try:
        import shutil
        shutil.copyfile("trades_atr.csv", "trades.csv")
    except Exception:
        pass

    return {
        "atr": {
            "id": "atr",
            "name": "布林_ATR基準",
            "displayName": "布林_ATR基準 (14, 5)",
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
    bars_5m_list = intervals_data.get("5m", [])
    bars_5m_count = len(bars_5m_list)
    bars_1m_list = intervals_data.get("1m", [])
    latest_bar_time = bars_1m_list[-1].get("datetime", "--") if bars_1m_list else "--"
    earliest_bar_date = bars_5m_list[0].get("datetime", "2026-06-01")[:10] if bars_5m_list else "2026-06-01"
    latest_bar_date = bars_5m_list[-1].get("datetime", "2026-10-15")[:10] if bars_5m_list else "2026-10-15"

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
            height: 46px;
            background-color: #141417;
            border-bottom: 1px solid #232733;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 12px;
            gap: 8px;
            user-select: none;
            z-index: 10;
            flex-shrink: 0;
            white-space: nowrap;
        }}
        .symbol-badge {{
            display: flex;
            align-items: center;
            gap: 8px;
            white-space: nowrap;
            flex-shrink: 0;
        }}
        .symbol-badge .symbol-name {{
            font-size: 14px;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: 0.3px;
            white-space: nowrap;
        }}
        .symbol-badge .symbol-code {{
            background: #242735;
            color: #089981;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
        }}

        /* 頂部策略選擇按鈕群組 */
        .strategy-selector-group {{
            display: flex;
            align-items: center;
            gap: 3px;
            background: #191b24;
            padding: 2px 4px;
            border-radius: 6px;
            border: 1px solid #282f42;
            white-space: nowrap;
        }}
        .strat-btn {{
            background: transparent;
            border: 1px solid transparent;
            color: #9299a8;
            font-size: 11px;
            font-weight: 600;
            padding: 3px 7px;
            border-radius: 4px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 4px;
            transition: all 0.15s;
            white-space: nowrap;
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
            flex-shrink: 0;
        }}

        .timeframe-group {{
            display: flex;
            align-items: center;
            gap: 2px;
            background: #191b24;
            padding: 2px 4px;
            border-radius: 6px;
            border: 1px solid #262a38;
            white-space: nowrap;
            flex-shrink: 0;
        }}
        .tf-divider {{
            width: 1px;
            height: 16px;
            background-color: #33384a;
            margin: 0 2px;
        }}
        .tf-btn {{
            background: transparent;
            border: none;
            color: #9299a8;
            font-size: 11px;
            font-weight: 600;
            padding: 3px 6px;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s ease-in-out;
            white-space: nowrap;
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
            gap: 5px;
            white-space: nowrap;
            flex-shrink: 0;
        }}
        .toggle-btn {{
            background: #1e2230;
            border: 1px solid #2e3448;
            color: #d1d4dc;
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 600;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s;
            display: flex;
            align-items: center;
            gap: 4px;
            white-space: nowrap;
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
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 600;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s;
            white-space: nowrap;
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

        /* 可拖曳上下分割器 (Resizable Split-Pane Bar) */
        .split-resizer {{
            height: 6px;
            background: #181b24;
            border-top: 1px solid #252b3b;
            border-bottom: 1px solid #252b3b;
            cursor: row-resize;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            z-index: 15;
            transition: background 0.15s;
        }}
        .split-resizer:hover, .split-resizer.dragging {{
            background: #2962ff;
            border-color: #2962ff;
        }}
        .split-resizer::after {{
            content: "";
            width: 36px;
            height: 2px;
            background: rgba(255, 255, 255, 0.25);
            border-radius: 1px;
        }}
        .workspace-split.solo-data-mode .split-resizer {{
            display: none !important;
        }}

        /* 下半部：策略績效全覽面板 (雙圖模式下佔約 48%，全畫面模式佔 100%) */
        .performance-section {{
            flex: 48;
            display: flex;
            flex-direction: column;
            background: #0d0e12;
            width: 100%;
            min-height: 200px;
            position: relative;
        }}

        /* 1. 關鍵數據頂部標題與六欄高密度機構指標 (Key Metrics Header) */
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
            background: rgba(0, 229, 255, 0.1);
            padding: 2px 8px;
            border-radius: 4px;
            border: 1px solid rgba(0, 229, 255, 0.25);
        }}
        .shortcut-hint-bar {{
            font-size: 11px;
            color: #787b86;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .kbd-tag {{
            background: #1c202e;
            border: 1px solid #2e354b;
            color: #a8b0c2;
            padding: 1px 5px;
            border-radius: 3px;
            font-family: monospace;
            font-size: 10px;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            gap: 12px;
        }}
        .metric-col {{
            display: flex;
            flex-direction: column;
            gap: 2px;
            padding-right: 8px;
            border-right: 1px solid rgba(255, 255, 255, 0.04);
        }}
        .metric-col:last-child {{
            border-right: none;
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
            flex-wrap: wrap;
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
            font-size: 11px;
            color: #9299a8;
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
            gap: 14px;
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
            gap: 6px;
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
            gap: 6px;
        }}
        .perf-tool-btn {{
            background: #191b24;
            border: 1px solid #282f42;
            color: #a0a6b5;
            cursor: pointer;
            font-size: 11px;
            font-weight: 600;
            padding: 3px 9px;
            border-radius: 4px;
            transition: all 0.15s;
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        .perf-tool-btn:hover {{
            color: #ffffff;
            background: #252a3a;
            border-color: #3b4560;
        }}
        .perf-tool-btn.highlight {{
            color: #00e5ff;
            border-color: rgba(0, 229, 255, 0.4);
            background: rgba(0, 229, 255, 0.08);
        }}
        .perf-tool-btn.gold-preset {{
            color: #f0b90b;
            border-color: rgba(240, 185, 11, 0.45);
            background: rgba(240, 185, 11, 0.1);
        }}
        .perf-tool-btn.gold-preset:hover {{
            background: rgba(240, 185, 11, 0.22);
            color: #ffffff;
        }}

        /* 3. 分頁 1: 策略淨值與回撤總覽視圖面板 */
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

        /* 策略 KPI 橫向量化矩陣卡片 */
        .multi-kpi-matrix {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 12px;
            padding: 8px 18px 8px 18px;
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
            cursor: default;
            transition: transform 0.15s, border-color 0.15s, box-shadow 0.15s;
            position: relative;
        }}
        .kpi-card.active-kpi-card {{
            background: #1b2030;
            box-shadow: 0 0 14px rgba(179, 136, 255, 0.14);
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
            grid-template-columns: repeat(8, 1fr);
            gap: 5px 12px;
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
            background: rgba(18, 21, 30, 0.96);
            border: 1px solid #313a52;
            border-radius: 6px;
            padding: 10px 14px;
            pointer-events: none;
            display: none;
            z-index: 50;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.85);
            font-size: 11px;
            min-width: 265px;
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

        /* 圖例核取控制方塊與子模式切換 */
        .multi-legend-bar {{
            height: 30px;
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
        .chart-mode-pills {{
            display: flex;
            align-items: center;
            gap: 4px;
            background: #171a24;
            padding: 2px;
            border-radius: 5px;
            border: 1px solid #262c3e;
        }}
        .mode-pill-btn {{
            background: transparent;
            border: none;
            color: #8b949e;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .mode-pill-btn:hover {{
            color: #ffffff;
        }}
        .mode-pill-btn.active {{
            background: #262f45;
            color: #00e5ff;
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

        /* 6. 分頁 4: 交易明細清單表格 + 互動篩選與匯出工具列 */
        .trades-table-container-wrapper {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            overflow: hidden;
            background: #0d0e12;
            display: none;
            flex-direction: column;
        }}
        .trades-filter-bar {{
            height: 36px;
            background: #12151e;
            border-bottom: 1px solid #202636;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 16px;
            flex-shrink: 0;
            font-size: 11px;
            gap: 10px;
        }}
        .filter-controls-group {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .filter-select {{
            background: #191d2b;
            border: 1px solid #2b3348;
            color: #d1d4dc;
            font-size: 11px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 4px;
            cursor: pointer;
            outline: none;
        }}
        .filter-select:hover, .filter-select:focus {{
            border-color: #00e5ff;
        }}
        .filter-summary-badge {{
            color: #9299a8;
            font-size: 11px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .btn-export-csv {{
            background: rgba(8, 153, 129, 0.16);
            border: 1px solid rgba(8, 153, 129, 0.45);
            color: #089981;
            font-size: 11px;
            font-weight: 700;
            padding: 3px 10px;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s;
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        .btn-export-csv:hover {{
            background: #089981;
            color: #ffffff;
        }}
        .trades-table-scroll {{
            flex: 1;
            overflow: auto;
            width: 100%;
        }}
        .trades-table {{
            width: 100%;
            min-width: 1080px;
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
            cursor: pointer;
            user-select: none;
        }}
        .trades-table th:hover {{
            color: #ffffff;
            background: #1b202e;
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

        /* 7. 分頁 5: 深度結構分析面板 (Quant Analytics View) */
        .quant-analytics-wrapper {{
            flex: 1;
            position: relative;
            width: 100%;
            height: 100%;
            overflow: auto;
            background: #0d0e12;
            display: none;
            padding: 14px 18px;
            gap: 14px;
            flex-direction: column;
        }}
        .analytics-top-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 14px;
            flex-shrink: 0;
        }}
        .analytics-card {{
            background: #141722;
            border: 1px solid #23293a;
            border-radius: 8px;
            padding: 12px 16px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}
        .analytics-card-title {{
            font-size: 12px;
            font-weight: 700;
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #1f2536;
            padding-bottom: 6px;
        }}
        .dual-bar-row {{
            display: flex;
            flex-direction: column;
            gap: 5px;
        }}
        .dual-bar-labels {{
            display: flex;
            justify-content: space-between;
            font-size: 11px;
            font-weight: 600;
        }}
        .dual-progress-track {{
            height: 8px;
            background: #232838;
            border-radius: 4px;
            overflow: hidden;
            display: flex;
        }}
        .dual-progress-fill-left {{
            background: #089981;
            height: 100%;
            transition: width 0.3s;
        }}
        .dual-progress-fill-right {{
            background: #f23645;
            height: 100%;
            transition: width 0.3s;
        }}
        .analytics-stat-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
        }}
        .analytics-stat-table th {{
            color: #787b86;
            font-weight: 600;
            text-align: left;
            padding: 4px 6px;
            border-bottom: 1px solid #1e2433;
        }}
        .analytics-stat-table td {{
            padding: 5px 6px;
            border-bottom: 1px solid rgba(255,255,255,0.03);
            font-weight: 600;
        }}
        .analytics-bottom-row {{
            flex: 1;
            min-height: 210px;
            background: #141722;
            border: 1px solid #23293a;
            border-radius: 8px;
            padding: 12px 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .hist-canvas-box {{
            flex: 1;
            position: relative;
            width: 100%;
            min-height: 160px;
        }}
        #pnl-dist-canvas {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            display: block;
        }}

        /* 交易特寫聚焦浮動橫幅 (含上/下一筆巡覽器) */
        .trade-focus-banner {{
            position: absolute;
            top: 48px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(18, 22, 34, 0.96);
            border: 1px solid #2962ff;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.7);
            border-radius: 6px;
            padding: 6px 12px;
            z-index: 25;
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 12px;
            color: #d1d4dc;
            backdrop-filter: blur(8px);
            white-space: nowrap;
        }}
        .trade-focus-banner .focus-badge {{
            color: #ffffff;
            padding: 2px 7px;
            border-radius: 4px;
            font-weight: 700;
            font-size: 11px;
        }}
        .btn-nav-trade {{
            background: #23293d;
            border: 1px solid #353f5c;
            color: #00e5ff;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .btn-nav-trade:hover {{
            background: #2962ff;
            border-color: #2962ff;
            color: #ffffff;
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

        /* 預設集快速切換按鈕列 (Modal 內) */
        .presets-bar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(240, 185, 11, 0.08);
            border: 1px solid rgba(240, 185, 11, 0.3);
            border-radius: 8px;
            padding: 8px 12px;
            gap: 8px;
        }}
        .preset-btn {{
            background: #22283a;
            border: 1px solid #36405c;
            color: #e1e4ea;
            font-size: 11px;
            font-weight: 700;
            padding: 5px 10px;
            border-radius: 5px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .preset-btn.gold {{
            background: linear-gradient(135deg, rgba(240, 185, 11, 0.25) 0%, rgba(240, 185, 11, 0.1) 100%);
            border-color: #f0b90b;
            color: #f0b90b;
        }}
        .preset-btn:hover {{
            background: #2962ff;
            border-color: #2962ff;
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
            white-space: nowrap;
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
            bottom: 18px;
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

        /* =========================================================
           RWD 響應式網頁設計 (支援平板 iPad / 手機 iPhone & Android)
           ========================================================= */
        @media (max-width: 1024px) {{
            .top-toolbar {{
                height: auto;
                min-height: 46px;
                flex-wrap: wrap;
                padding: 6px 10px;
                gap: 6px;
            }}
            .timeframe-group, .actions-group {{
                overflow-x: auto;
                max-width: 100%;
                -webkit-overflow-scrolling: touch;
                padding-bottom: 2px;
            }}
            .metrics-grid {{
                grid-template-columns: repeat(3, 1fr);
                gap: 8px;
            }}
            .metric-col:nth-child(3n) {{
                border-right: none;
            }}
            .kpi-card-body {{
                grid-template-columns: repeat(4, 1fr);
                gap: 6px 10px;
            }}
            .perf-header-bar {{
                height: auto;
                min-height: 38px;
                flex-wrap: wrap;
                padding: 6px 12px;
                gap: 8px;
            }}
            .perf-title-group, .perf-tab-list, .perf-tools {{
                overflow-x: auto;
                max-width: 100%;
                -webkit-overflow-scrolling: touch;
                flex-wrap: nowrap;
                white-space: nowrap;
            }}
            .analytics-top-grid {{
                grid-template-columns: 1fr;
            }}
            .multi-legend-bar, .trades-filter-bar, .trade-chart-header-info {{
                height: auto;
                min-height: 32px;
                flex-wrap: wrap;
                padding: 6px 12px;
                gap: 6px;
            }}
        }}

        @media (max-width: 768px) {{
            body {{
                height: auto;
                min-height: 100vh;
                overflow-y: auto;
                overflow-x: hidden;
            }}
            .workspace-split {{
                height: auto;
                min-height: calc(100vh - 90px);
                overflow: visible;
            }}
            .kline-section {{
                flex: none;
                height: 52vh;
                min-height: 340px;
            }}
            .split-resizer {{
                display: none !important;
            }}
            .performance-section {{
                flex: none;
                height: auto;
                min-height: 580px;
            }}
            .workspace-split.solo-data-mode .performance-section {{
                height: auto !important;
                min-height: calc(100vh - 90px) !important;
            }}
            .multi-canvas-viewport, .canvas-scroll-viewport, .perf-chart-container-wrapper {{
                min-height: 360px !important;
                height: 52vh !important;
                flex: none;
            }}
            .metrics-card {{
                padding: 8px 12px;
            }}
            .metrics-header-row {{
                flex-direction: column;
                align-items: flex-start;
                gap: 4px;
            }}
            .metrics-title {{
                flex-wrap: wrap;
                gap: 6px;
            }}
            .shortcut-hint-bar {{
                display: none;
            }}
            .metrics-grid {{
                grid-template-columns: repeat(2, 1fr);
                gap: 8px;
            }}
            .metric-col {{
                border-right: none;
                background: #151821;
                padding: 6px 8px;
                border-radius: 5px;
                border: 1px solid #1f2433;
            }}
            .metric-main-value {{
                font-size: 13px;
            }}
            .multi-kpi-matrix {{
                padding: 8px 12px;
            }}
            .kpi-card-header {{
                flex-wrap: wrap;
                gap: 4px;
            }}
            .kpi-card-body {{
                grid-template-columns: repeat(2, 1fr);
                gap: 6px 10px;
            }}
            .ohlc-legend {{
                top: 6px;
                left: 6px;
                right: 6px;
                padding: 4px 8px;
                font-size: 10px;
                gap: 5px;
                max-width: calc(100% - 12px);
            }}
            .trade-focus-banner {{
                top: auto;
                bottom: 8px;
                width: 94%;
                justify-content: space-between;
                font-size: 11px;
                padding: 5px 8px;
                overflow-x: auto;
            }}
            .params-modal-card {{
                width: 96vw;
                max-width: 96vw;
                max-height: 92vh;
                border-radius: 10px;
            }}
            .params-modal-header, .params-modal-body, .params-modal-footer {{
                padding: 12px 14px;
            }}
            .params-grid {{
                grid-template-columns: 1fr;
                gap: 10px;
            }}
            .presets-bar {{
                flex-direction: column;
                align-items: flex-start;
            }}
            .params-modal-footer {{
                flex-direction: column;
                gap: 8px;
                align-items: stretch;
            }}
            .params-actions, .btn-run-backtest, .btn-reset-params {{
                width: 100%;
                justify-content: center;
            }}
            .trades-table th:first-child,
            .trades-table td:first-child {{
                position: sticky;
                left: 0;
                background: #141720;
                z-index: 3;
            }}
            .toast-container {{
                right: 10px;
                left: 10px;
                bottom: 12px;
            }}
            .toast-card {{
                min-width: auto;
                width: 100%;
            }}
        }}
    </style>
</head>
<body>

    <!-- 頂部控制列 -->
    <div class="top-toolbar">
        <div class="symbol-badge">
            <span class="symbol-name">微型台指期 10</span>
            <span class="symbol-code">TMFR1</span>

            <!-- 當前策略標識 -->
            <div class="strategy-selector-group">
                <button class="strat-btn active" data-strat="atr" title="當前策略：布林動能突破 (動態 ATR 停利基準)">
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
            <!-- 全畫面純數據視圖 / 雙圖切換按鈕 -->
            <button class="toggle-btn mode-active" id="btn-toggle-layout" title="切換 全畫面數據視圖 / K線雙圖模式 (快捷鍵: Space)">
                <span id="txt-toggle-layout">數據全畫面 (作用中)</span>
            </button>
            <div id="live-status-badge" class="live-badge offline" title="即時行情 WebSocket 串流狀態">
                <span class="live-indicator-dot"></span>
                <span id="live-status-text">串流: 離線</span>
            </div>
            <button class="toggle-btn active" id="toggle-bb-btn" title="切換顯示布林通道上中下軌">
                <span id="txt-toggle-bb">布林軌道: 顯示</span>
            </button>
            <button class="toggle-btn active" id="toggle-exit-line-btn" title="切換顯示出場指標數值">
                <span id="txt-toggle-exit-line">出場指標: 顯示</span>
            </button>
            <button class="toggle-btn active" id="toggle-markers-btn" title="切換顯示買進與平倉標記">
                <span>交易標記: 顯示</span>
            </button>
            <button class="reset-btn" id="reset-zoom-btn" title="自動適配時間軸與價格縮放">適配畫面 (Fit)</button>
        </div>
    </div>

    <!-- 即時通知容器 -->
    <div class="toast-container" id="toast-container"></div>

    <!-- 主工作區：預設加入 solo-data-mode 類別 (全畫面只有數據部分) -->
    <div class="workspace-split solo-data-mode" id="workspace-split">

        <!-- 上圖：K 線圖主圖 (全畫面模式下自動隱藏，點擊按鈕可一鍵展開) -->
        <div class="kline-section" id="kline-section">
            <div class="ohlc-legend" id="ohlc-legend">
                <div class="legend-item legend-active-strat" id="legend-strat-tag">布林_ATR基準</div>
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
                <div class="legend-item legend-bb-group" id="legend-exit-group">
                    <span class="legend-label" id="legend-exit-label" style="color:#b388ff;">ATR指標</span>
                    <span class="legend-value" id="legend-exit-val" style="color:#b388ff;">--</span>
                </div>
            </div>

            <div id="kline-container"></div>
            <div class="watermark">TMFR1 NEAR-MONTH - QUANTITATIVE WORKBENCH</div>
        </div>

        <!-- 可拖曳上下分割條 (雙圖模式下可拉動調整 K 線與數據區高度比例) -->
        <div class="split-resizer" id="split-resizer" title="上下拖曳調整 K 線圖與數據面板高度比例"></div>

        <!-- 下半部：策略績效全覽面板 (全畫面模式下佔滿 100% 畫面高) -->
        <div class="performance-section" id="performance-section">

            <!-- 關鍵數據卡片 (擴充為 6 欄機構級指標，已依使用者要求移除頂部標題與策略收錄列) -->
            <div class="metrics-card">
                <div class="metrics-grid">
                    <!-- 1. 總損益 -->
                    <div class="metric-col">
                        <div class="metric-label">總淨損益 (Net Profit)</div>
                        <div class="metric-main-value" id="metric-total-pnl">
                            <span id="metric-pnl-val">--</span>
                            <span class="currency">TWD</span>
                            <span class="pct" id="metric-return-pct">--</span>
                        </div>
                    </div>

                    <!-- 2. 最大回撤 -->
                    <div class="metric-col">
                        <div class="metric-label">最大回撤 (Max Drawdown)</div>
                        <div class="metric-main-value neutral">
                            <span id="metric-mdd-val" style="color:#f23645;">--</span>
                            <span class="currency">TWD</span>
                            <span class="pct" id="metric-mdd-pct" style="color:#9299a8;">--</span>
                        </div>
                    </div>

                    <!-- 3. 獲利交易與勝率 -->
                    <div class="metric-col">
                        <div class="metric-label">勝率 (獲利 / 總筆數)</div>
                        <div class="metric-main-value neutral">
                            <span id="metric-win-rate">--</span>
                            <span class="sub-ratio" id="metric-win-ratio">--</span>
                        </div>
                    </div>

                    <!-- 4. 獲利因子與賺賠比 -->
                    <div class="metric-col">
                        <div class="metric-label">獲利因子 / 賺賠比</div>
                        <div class="metric-main-value neutral">
                            <span id="metric-profit-factor" style="color:#00e5ff;">--</span>
                            <span class="sub-ratio" id="metric-payoff-ratio">--</span>
                        </div>
                    </div>

                    <!-- 5. 單筆期望值與風報比 -->
                    <div class="metric-col">
                        <div class="metric-label">單筆期望值 / 風報比</div>
                        <div class="metric-main-value neutral">
                            <span id="metric-expectancy" style="color:#089981;">--</span>
                            <span class="sub-ratio" id="metric-calmar">--</span>
                        </div>
                    </div>

                    <!-- 6. 多空勝率與平均持倉 -->
                    <div class="metric-col">
                        <div class="metric-label">多空勝率 / 平均持倉</div>
                        <div class="metric-main-value neutral">
                            <span id="metric-ls-winrate" style="font-size:13px;">--</span>
                            <span class="sub-ratio" id="metric-avg-hold">--</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 績效圖表頂部功能切換列 (五個分頁) -->
            <div class="perf-header-bar">
                <div class="perf-title-group">
                    <div class="perf-title">績效分析儀表板</div>
                    <div class="perf-tab-list">
                        <div class="perf-tab active highlight" id="tab-multi-compare">策略淨值與回撤總覽</div>
                        <div class="perf-tab" id="tab-quant-analytics">深度結構分析</div>
                        <div class="perf-tab" id="tab-trade-equity">單策略逐筆淨值圖</div>
                        <div class="perf-tab" id="tab-cum-pnl">時間時序走勢</div>
                        <div class="perf-tab" id="tab-trades-table">交易明細清單</div>
                    </div>
                </div>
                <div class="perf-tools">
                    <button class="perf-tool-btn gold-preset" id="btn-quick-best-preset" title="一鍵套用布林_ATR基準最佳化參數組合 (基準A：130%維持率 + 動態浮動槓桿)">套用最佳化參數組合</button>
                    <button class="perf-tool-btn highlight" id="btn-open-params-modal" title="微調策略參數並即時重新撮合回測">策略參數調校與即時回測</button>
                    <button class="perf-tool-btn" id="btn-toggle-compare-mode" title="切換累積損益、帳戶淨值或水下回撤圖">切換視圖模式</button>
                    <button class="perf-tool-btn" id="btn-fit-chart" title="適配全屏">適配縮放</button>
                </div>
            </div>

            <!-- 回測區間與初始資金快捷控制列 -->
            <div class="multi-legend-bar" id="backtest-quick-bar" style="background:#131620; border-bottom:1px solid #202636; padding:4px 16px; height:auto; min-height:34px; flex-wrap:wrap; gap:10px;">
                <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                    <span style="color:#00e5ff; font-weight:700;">回測區間:</span>
                    <input type="date" id="quick-start-date" class="filter-select" value="{earliest_bar_date}" min="{earliest_bar_date}" max="{latest_bar_date}" style="padding:2px 6px; font-family:monospace;">
                    <span style="color:#787b86;">~</span>
                    <input type="date" id="quick-end-date" class="filter-select" value="{latest_bar_date}" min="{earliest_bar_date}" max="{latest_bar_date}" style="padding:2px 6px; font-family:monospace;">
                    <button class="perf-tool-btn" id="btn-range-all" style="padding:2px 8px; font-size:11px;" title="選取資料庫全部可用區間">全區間</button>
                    <button class="perf-tool-btn" id="btn-range-3m" style="padding:2px 8px; font-size:11px;" title="選取最近 3 個月">近 3 個月</button>
                    <button class="perf-tool-btn" id="btn-range-1m" style="padding:2px 8px; font-size:11px;" title="選取最近 1 個月">近 1 個月</button>
                </div>
                <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                    <span style="color:#f0b90b; font-weight:700;">初始資金 (TWD):</span>
                    <input type="number" id="quick-initial-capital" class="param-num-input" value="105150" min="35050" max="10000000" step="5000" style="width:105px; padding:3px 8px; font-weight:700; color:#f0b90b; border-color:#3a445e;">
                    <button class="perf-tool-btn highlight" id="btn-apply-quick-backtest" style="padding:3px 12px; font-weight:700;">套用區間與本金</button>
                </div>
            </div>

            <!-- 1. 分頁 1: 策略淨值與回撤總覽視圖面板 (預設顯示) -->
            <div class="multi-compare-wrapper" id="multi-compare-wrapper">
                <!-- 頂部策略橫向 KPI 矩陣卡片 -->
                <div class="multi-kpi-matrix" id="multi-kpi-matrix">
                    <!-- 卡片: 布林_ATR基準 -->
                    <div class="kpi-card card-atr active-kpi-card" id="kpi-card-atr">
                        <div class="kpi-card-header">
                            <div class="kpi-card-title">
                                <span id="kpi-title-atr">布林_ATR基準 (14, 5)</span>
                            </div>
                            <span class="kpi-card-badge" style="background:rgba(179,136,255,0.15); color:#b388ff;">動態 ATR 停利基準</span>
                        </div>
                        <div class="kpi-card-body">
                            <div class="kpi-sub-item"><span class="kpi-sub-label">總淨利</span><span class="kpi-sub-val win" id="kpi-atr-pnl">--</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">勝率 (筆數)</span><span class="kpi-sub-val" id="kpi-atr-winrate">--</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">獲利因子</span><span class="kpi-sub-val" id="kpi-atr-pf">--</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">單筆期望值</span><span class="kpi-sub-val highlight" id="kpi-atr-exp">--</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">最大回撤</span><span class="kpi-sub-val loss" id="kpi-atr-mdd">--</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">風報比 (Ret/MDD)</span><span class="kpi-sub-val" id="kpi-atr-calmar">--</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">贏均 / 輸均</span><span class="kpi-sub-val" id="kpi-atr-avgwl">--</span></div>
                            <div class="kpi-sub-item"><span class="kpi-sub-label">賺賠比</span><span class="kpi-sub-val" id="kpi-atr-payoff">--</span></div>
                        </div>
                    </div>
                </div>

                <!-- 圖例控制方塊與子模式切換 -->
                <div class="multi-legend-bar">
                    <div class="legend-checkbox-group">
                        <label class="legend-check-item" style="color:#b388ff;">
                            <input type="checkbox" id="chk-atr" checked>
                            <span>布林_ATR基準 (紫色)</span>
                        </label>
                    </div>

                    <div class="chart-mode-pills">
                        <button class="mode-pill-btn active" data-cmode="PNL" onclick="setMultiChartMode('PNL')">累積損益 (TWD)</button>
                        <button class="mode-pill-btn" data-cmode="EQUITY" onclick="setMultiChartMode('EQUITY')">帳戶總淨值</button>
                        <button class="mode-pill-btn" data-cmode="DRAWDOWN" onclick="setMultiChartMode('DRAWDOWN')">水下回撤曲線 (Drawdown)</button>
                    </div>

                    <div style="color:#787b86;">
                        <span id="txt-axis-hint">座標說明：橫軸 X = 時間時序推進 ｜ 縱軸 Y = 累積損益 (TWD)</span>
                    </div>
                </div>

                <!-- Canvas 淨值與回撤繪圖視窗 -->
                <div class="multi-canvas-viewport" id="multi-canvas-viewport">
                    <canvas id="multi-compare-canvas"></canvas>
                    <div class="multi-tooltip" id="multi-tooltip"></div>
                </div>
            </div>

            <!-- 2. 分頁 2: 深度結構分析面板 (多空拆解 / 出場歸因 / 日夜盤 / 損益分佈直方圖) -->
            <div class="quant-analytics-wrapper" id="quant-analytics-wrapper">
                <div class="analytics-top-grid">
                    <!-- 卡片 A: 多空雙向績效拆解 -->
                    <div class="analytics-card">
                        <div class="analytics-card-title">
                            <span>多空雙向績效拆解 (Long vs Short)</span>
                            <span id="qa-strat-badge" style="color:#b388ff; font-size:11px;">布林_ATR基準</span>
                        </div>
                        <div class="dual-bar-row">
                            <div class="dual-bar-labels">
                                <span style="color:#089981;" id="qa-long-ratio-txt">做多 (LONG): --</span>
                                <span style="color:#f23645;" id="qa-short-ratio-txt">放空 (SHORT): --</span>
                            </div>
                            <div class="dual-progress-track">
                                <div class="dual-progress-fill-left" id="qa-long-bar" style="width:50%;"></div>
                                <div class="dual-progress-fill-right" id="qa-short-bar" style="width:50%;"></div>
                            </div>
                        </div>
                        <table class="analytics-stat-table">
                            <thead>
                                <tr>
                                    <th>方向</th>
                                    <th>成交筆數</th>
                                    <th>勝率</th>
                                    <th>獲利因子</th>
                                    <th>累積淨損益</th>
                                </tr>
                            </thead>
                            <tbody id="qa-ls-tbody"></tbody>
                        </table>
                    </div>

                    <!-- 卡片 B: 出場原因歸因分析 -->
                    <div class="analytics-card">
                        <div class="analytics-card-title">
                            <span>出場機制歸因分析 (Exit Attribution)</span>
                            <span style="color:#8b949e; font-size:10px;">停利 vs 停損貢獻度</span>
                        </div>
                        <table class="analytics-stat-table">
                            <thead>
                                <tr>
                                    <th>出場原因</th>
                                    <th>筆數 (佔比)</th>
                                    <th>勝率</th>
                                    <th>平均單筆</th>
                                    <th>總損益貢獻</th>
                                </tr>
                            </thead>
                            <tbody id="qa-exit-tbody"></tbody>
                        </table>
                    </div>

                    <!-- 卡片 C: 日夜盤時段與持倉時間分析 -->
                    <div class="analytics-card">
                        <div class="analytics-card-title">
                            <span>日夜盤時段與持倉時間結構</span>
                            <span id="qa-streak-badge" style="color:#f0b90b; font-size:11px;">--</span>
                        </div>
                        <table class="analytics-stat-table">
                            <thead>
                                <tr>
                                    <th>交易時段</th>
                                    <th>成交筆數</th>
                                    <th>時段勝率</th>
                                    <th>獲利因子</th>
                                    <th>時段累積淨利</th>
                                </tr>
                            </thead>
                            <tbody id="qa-session-tbody"></tbody>
                        </table>
                        <div style="margin-top:auto; padding-top:6px; border-top:1px dashed #23293a; display:flex; justify-content:space-between; font-size:11px; color:#9299a8;">
                            <span>贏單平均持倉: <b id="qa-win-hold" style="color:#089981;">--</b></span>
                            <span>輸單平均持倉: <b id="qa-loss-hold" style="color:#f23645;">--</b></span>
                            <span>夏普指標: <b id="qa-sharpe" style="color:#00e5ff;">--</b></span>
                        </div>
                    </div>
                </div>

                <!-- 底部單筆損益分佈直方圖 -->
                <div class="analytics-bottom-row">
                    <div class="analytics-card-title" style="border-bottom:none; padding-bottom:0;">
                        <span>單筆交易損益分佈直方圖 (PnL Distribution Histogram - 檢驗右偏肥尾效應)</span>
                        <span style="font-size:11px; color:#8b949e;">左側紅色為虧損區間筆數，右側綠色為獲利區間筆數</span>
                    </div>
                    <div class="hist-canvas-box" id="hist-canvas-box">
                        <canvas id="pnl-dist-canvas"></canvas>
                    </div>
                </div>
            </div>

            <!-- 3. 分頁 3: 單策略逐筆淨值圖 (橫軸 X = 交易次數 × 縱軸 Y = 累積損益與帳戶淨值) -->
            <div class="trade-equity-container-wrapper" id="trade-equity-wrapper">
                <div class="trade-chart-header-info">
                    <span class="info-tag mode-tag" id="current-mode-tag">當前視圖: 橫軸 X = 交易次數 ｜ 縱軸 Y = 累積損益 (TWD) & 帳戶淨值</span>
                    <span class="info-tag tip-tag">操作提示：滑鼠懸停檢視明細，點擊任一交易節點可定位上方 K 線圖</span>
                </div>
                <div class="canvas-scroll-viewport" id="canvas-scroll-viewport">
                    <canvas id="trade-equity-canvas"></canvas>
                </div>
                <div class="trade-tooltip" id="trade-tooltip"></div>
            </div>

            <!-- 4. 分頁 4: 時間軸累計損益圖表容器 (BaselineSeries) -->
            <div class="perf-chart-container-wrapper" id="perf-chart-wrapper">
                <div class="perf-legend" id="perf-legend">
                    <span style="color:#787b86;">累計損益:</span>
                    <span class="perf-legend-val" id="perf-legend-val">--</span>
                    <span style="color:#787b86; margin-left: 8px;">時間:</span>
                    <span id="perf-legend-time" style="color:#9299a8;">--</span>
                </div>
                <div id="perf-chart-container"></div>
            </div>

            <!-- 5. 分頁 5: 交易明細清單表格 (含多維度篩選、排序與一鍵 CSV 匯出) -->
            <div class="trades-table-container-wrapper" id="trades-table-wrapper">
                <div class="trades-filter-bar">
                    <div class="filter-controls-group">
                        <span style="color:#8b949e; font-weight:600;">篩選條件:</span>
                        <select class="filter-select" id="filter-trade-side" onchange="renderTradesTable()">
                            <option value="ALL">全部方向 (多 + 空)</option>
                            <option value="LONG">僅做多 (LONG)</option>
                            <option value="SHORT">僅放空 (SHORT)</option>
                        </select>
                        <select class="filter-select" id="filter-trade-result" onchange="renderTradesTable()">
                            <option value="ALL">全部結果 (獲利 + 虧損)</option>
                            <option value="WIN">僅獲利單 (WIN &gt; 0)</option>
                            <option value="LOSS">僅虧損單 (LOSS &le; 0)</option>
                        </select>
                        <select class="filter-select" id="filter-trade-reason" onchange="renderTradesTable()">
                            <option value="ALL">全部出場原因</option>
                            <option value="PROFIT_EXIT">動態停利出場 (ATR 停利)</option>
                            <option value="STOP_LOSS">初始停損出場 (停損)</option>
                        </select>
                        <span style="color:#8b949e; font-weight:600; margin-left:6px;">排序:</span>
                        <select class="filter-select" id="sort-trade-by" onchange="renderTradesTable()">
                            <option value="ID_ASC">依交易編號 (由舊至新)</option>
                            <option value="ID_DESC">依交易編號 (由新至舊)</option>
                            <option value="PNL_DESC">依單筆淨損益 (最高獲利優先)</option>
                            <option value="PNL_ASC">依單筆淨損益 (最大虧損優先)</option>
                            <option value="HOLD_DESC">依持倉時間 (最長優先)</option>
                        </select>
                    </div>
                    <div class="filter-controls-group">
                        <div class="filter-summary-badge" id="trades-filter-summary">顯示 0 筆</div>
                        <button class="btn-export-csv" onclick="exportActiveTradesCsv()" title="將目前篩選之交易明細匯出為 Excel 相容之 UTF-8 CSV 檔案">匯出當前明細 CSV</button>
                    </div>
                </div>
                <div class="trades-table-scroll">
                    <table class="trades-table">
                        <thead>
                            <tr>
                                <th>編號</th>
                                <th>方向</th>
                                <th>進場時間</th>
                                <th>進場價格</th>
                                <th>出場時間</th>
                                <th>出場價格</th>
                                <th>持倉時間</th>
                                <th>口數</th>
                                <th>合約總價值</th>
                                <th>動態槓桿 (維持率)</th>
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
                    <span>策略參數調校與即時回測工作台</span>
                    <span style="font-size:11px; font-weight:normal; color:#8b949e;">(極速撮合 {bars_5m_count:,} 根 K 棒，即時刷新全視圖、軌道與結構分析)</span>
                </div>
                <button class="params-modal-close" id="btn-close-params-modal" title="關閉視窗 (Esc)">關閉</button>
            </div>
            <div class="params-modal-body">
                <!-- 快速參數預設集橫幅 -->
                <div class="presets-bar">
                    <span style="font-size:12px; font-weight:700; color:#f0b90b;">快速參數配置集 (Presets)：</span>
                    <div style="display:flex; gap:8px; flex-wrap:wrap;">
                        <button class="preset-btn gold" id="btn-apply-best-preset" title="套用布林_ATR基準最佳化參數組合 (基準A：130%維持率 + 動態浮動槓桿)">套用最佳化參數組合 (基準A 130%維持率)</button>
                        <button class="preset-btn" id="btn-apply-baseline-preset" title="恢復初始基準參數 (基準A：130%維持率)">恢復初始基準參數 (基準A 130%維持率)</button>
                    </div>
                </div>

                <!-- 策略選擇標籤 -->
                <div class="params-strat-selector">
                    <div class="params-strat-tab active" data-strat="atr">布林_ATR基準 (動態均線對照)</div>
                </div>

                <!-- 區塊一：進場條件 (布林動能突破 + 拉回確認) -->
                <div class="params-section">
                    <div class="params-section-title">
                        <span>進場型態條件 (布林通道突破與拉回濾網)</span>
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
                                <span class="param-val-badge" id="val-badge-pullback">0.25 (25% K棒)</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-pullback" min="0.10" max="0.50" step="0.05" value="0.25">
                                <input type="number" class="param-num-input" id="num-pullback" min="0.10" max="0.50" step="0.05" value="0.25">
                            </div>
                        </div>
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>部位控管模式 (Position Sizing)</span>
                                <span class="param-val-badge" id="val-badge-sizing">基準 A (130%維持率)</span>
                            </div>
                            <div class="param-input-row">
                                <select class="filter-select" id="sel-sizing-mode" style="width:100%; padding:5px 8px;">
                                    <option value="margin_ratio_130">基準 A：整戶原始保證金維持率控管 (動態浮動槓桿)</option>
                                    <option value="notional">名目合約槓桿上限模式 (Notional Cap)</option>
                                    <option value="fixed_one_lot">固定單口模式 (Fixed 1 Lot)</option>
                                </select>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 區塊二：動態追蹤出場條件 -->
                <div class="params-section">
                    <div class="params-section-title">
                        <span>動態追蹤出場機制 (<span id="txt-exit-strat-name">布林_ATR基準</span>)</span>
                    </div>

                    <!-- ATR 基準專屬參數 -->
                    <div class="params-grid strat-exit-panel" id="panel-exit-atr">
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

                <!-- 區塊三：回測區間、初始資金與保證金維持率風控 -->
                <div class="params-section">
                    <div class="params-section-title">
                        <span>回測區間、初始資金與保證金維持率風控 (原始保證金 35,050 元 ｜ 維持保證金 26,900 元)</span>
                    </div>
                    <div class="params-grid">
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>回測起訖區間 (Start ~ End Date)</span>
                                <span class="param-val-badge" id="val-badge-date-range">{earliest_bar_date} ~ {latest_bar_date}</span>
                            </div>
                            <div class="param-input-row">
                                <input type="date" class="filter-select" id="modal-start-date" value="{earliest_bar_date}" min="{earliest_bar_date}" max="{latest_bar_date}" style="flex:1; padding:4px 6px; font-family:monospace;">
                                <span style="color:#787b86;">~</span>
                                <input type="date" class="filter-select" id="modal-end-date" value="{latest_bar_date}" min="{earliest_bar_date}" max="{latest_bar_date}" style="flex:1; padding:4px 6px; font-family:monospace;">
                            </div>
                        </div>
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>初始資金 (Initial Capital TWD)</span>
                                <span class="param-val-badge" id="val-badge-initial-capital">105,150 元</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-initial-capital" min="45000" max="1000000" step="5000" value="105150">
                                <input type="number" class="param-num-input" id="num-initial-capital" min="35050" max="10000000" step="5000" value="105150" style="width:92px;">
                            </div>
                        </div>
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>最低整戶維持率門檻 (基準 A %)</span>
                                <span class="param-val-badge" id="val-badge-margin-ratio">130% (單口 45,565 元)</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-margin-ratio" min="100" max="250" step="5" value="130">
                                <input type="number" class="param-num-input" id="num-margin-ratio" min="100" max="250" step="5" value="130">
                            </div>
                        </div>
                        <div class="param-item">
                            <div class="param-label-row">
                                <span>最大槓桿倍數 (僅 Notional 模式)</span>
                                <span class="param-val-badge" id="val-badge-leverage">7.0x</span>
                            </div>
                            <div class="param-input-row">
                                <input type="range" class="param-range" id="range-leverage" min="1.0" max="15.0" step="0.5" value="7.0">
                                <input type="number" class="param-num-input" id="num-leverage" min="1.0" max="15.0" step="0.5" value="7.0">
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
                    <div style="font-weight:700; color:#b388ff; display:flex; align-items:center; gap:6px;">
                        <span>回測績效預覽：</span>
                        <span id="res-preview-title">布林_ATR基準</span>
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
                    <button class="btn-reset-params" id="btn-reset-params">重設當前策略</button>
                </div>
                <div class="params-actions">
                    <button class="btn-run-backtest" id="btn-run-backtest">立即回測當前策略 (Run Active)</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        // 資料載入：布林_ATR基準完整數據集合與 11 種週期 K 線
        const KLINE_DATA = {data_json};
        const STRATEGIES_DATA = {strategies_json};

        let currentInterval = '5m';
        let currentStrategyKey = 'atr'; // 預設且唯一策略：布林_ATR基準
        let showMarkers = true;
        let isSoloDataMode = true; // 預設全畫面數據模式
        let currentFocusedTradeId = null; // 當前聚焦特寫之 Trade ID

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

        // 出場軌道線
        let exitLinePrimarySeries = null;
        let exitLineSecondarySeries = null;
        let showExitOverlay = true;
        window.exitLookupMap = new Map();

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
        const quantAnalyticsWrapper = document.getElementById('quant-analytics-wrapper');

        const tabMultiCompare = document.getElementById('tab-multi-compare');
        const tabQuantAnalytics = document.getElementById('tab-quant-analytics');
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
        const btnToggleExitLine = document.getElementById('toggle-exit-line-btn');
        const txtToggleExitLine = document.getElementById('txt-toggle-exit-line');

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
        window.currentFilteredTrades = [];

        // 取得當前活躍策略之完整資料物件
        function getActiveStrategy() {{
            return STRATEGIES_DATA[currentStrategyKey] || STRATEGIES_DATA['atr'];
        }}

        // 渲染關鍵數據面板數值 (與當前活躍策略聯動，支援 6 欄機構級指標)
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

            // 第 5 欄：單筆期望值與風報比
            const expEl = document.getElementById('metric-expectancy');
            const calmarEl = document.getElementById('metric-calmar');
            if (expEl) {{
                expEl.textContent = `${{m.expectancyStr || '+0'}} TWD`;
                expEl.style.color = (m.expectancy || 0) >= 0 ? '#089981' : '#f23645';
            }}
            if (calmarEl) {{
                calmarEl.textContent = `風報比 ${{m.returnToMddStr || '0.00'}} ｜ 夏普 ${{m.sharpeRatioStr || '0.00'}}`;
            }}

            // 第 6 欄：多空勝率與平均持倉
            const lsWinEl = document.getElementById('metric-ls-winrate');
            const avgHoldEl = document.getElementById('metric-avg-hold');
            if (lsWinEl && m.longStats && m.shortStats) {{
                lsWinEl.innerHTML = `<span style="color:#089981;">多 ${{m.longStats.winRate}}%</span> <span style="color:#555e70;">/</span> <span style="color:#f23645;">空 ${{m.shortStats.winRate}}%</span>`;
            }}
            if (avgHoldEl) {{
                avgHoldEl.textContent = `均持倉 ${{m.avgHoldStr || '--'}}`;
            }}

            if (currentStratIndicator) {{
                currentStratIndicator.textContent = `當前活躍策略: ${{strat.displayName}}`;
                currentStratIndicator.style.color = strat.themeColor;
            }}
            if (legendStratTag) {{
                legendStratTag.textContent = strat.name;
                legendStratTag.style.color = strat.themeColor;
                legendStratTag.style.borderColor = strat.themeColor;
            }}

            // 高亮當前選中之策略 KPI 卡片
            ['atr'].forEach(k => {{
                const card = document.getElementById(`kpi-card-${{k}}`);
                if (card) card.classList.toggle('active-kpi-card', k === currentStrategyKey);
            }});
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

            // 建立布林通道上中下軌線條與動態出場軌道線條
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
                exitLinePrimarySeries = klineChart.addSeries(window.LightweightCharts.LineSeries, {{
                    color: '#00e5ff',
                    lineWidth: 1.5,
                    lineStyle: 2,
                    priceLineVisible: false,
                    lastValueVisible: true,
                    title: '出場軌'
                }});
                exitLineSecondarySeries = klineChart.addSeries(window.LightweightCharts.LineSeries, {{
                    color: '#00e5ff',
                    lineWidth: 1.5,
                    lineStyle: 2,
                    priceLineVisible: false,
                    lastValueVisible: true,
                    title: 'DC下軌'
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
                exitLinePrimarySeries = klineChart.addLineSeries({{
                    color: '#00e5ff',
                    lineWidth: 1.5,
                    lineStyle: 2,
                    priceLineVisible: false,
                    lastValueVisible: true,
                    title: '出場軌'
                }});
                exitLineSecondarySeries = klineChart.addLineSeries({{
                    color: '#00e5ff',
                    lineWidth: 1.5,
                    lineStyle: 2,
                    priceLineVisible: false,
                    lastValueVisible: true,
                    title: 'DC下軌'
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

        window.barDatetimeMap = new Map();

        // 將 Lightweight Charts 時間戳轉換為台灣盤面時間字串 (避免瀏覽器本地時區重複 +8 小時偏移)
        function formatBarTimestamp(ts, fallbackDatetime) {{
            if (fallbackDatetime) return String(fallbackDatetime);
            const numTs = Number(ts);
            if (!numTs || isNaN(numTs)) return '';
            if (window.barDatetimeMap && window.barDatetimeMap.has(numTs)) {{
                return window.barDatetimeMap.get(numTs);
            }}
            const d = new Date(numTs * 1000);
            const yyyy = d.getUTCFullYear();
            const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
            const dd = String(d.getUTCDate()).padStart(2, '0');
            const hh = String(d.getUTCHours()).padStart(2, '0');
            const mi = String(d.getUTCMinutes()).padStart(2, '0');
            const ss = String(d.getUTCSeconds()).padStart(2, '0');
            return `${{yyyy}}-${{mm}}-${{dd}} ${{hh}}:${{mi}}:${{ss}}`;
        }}

        // 更新績效懸浮數據
        function updatePerfLegend(item) {{
            if (!item) return;
            const val = Number(item.value || 0);
            const sign = val >= 0 ? '+' : '';
            perfLegendVal.textContent = `${{sign}}${{val.toLocaleString('zh-TW', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }})}} TWD`;
            perfLegendVal.style.color = val >= 0 ? '#089981' : '#f23645';

            if (item.time || item.datetime) {{
                perfLegendTime.textContent = formatBarTimestamp(item.time, item.datetime);
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

        // 更新 K 線主圖上的動態出場指標數值 (ATR 基準)
        function updateExitOverlay(bars) {{
            if (!bars || bars.length === 0) return;
            const n = bars.length;
            const highs = bars.map(b => Number(b.high));
            const lows = bars.map(b => Number(b.low));
            const closes = bars.map(b => Number(b.close));
            const times = bars.map(b => Number(b.time));
            const lookup = new Map();

            const cfg = (typeof strategyConfigs !== 'undefined' && strategyConfigs[currentStrategyKey])
                ? strategyConfigs[currentStrategyKey]
                : {{ atr_length: 14, atr_sig_length: 5 }};

            const atrLen = Number(cfg.atr_length) || 14;
            const sigLen = Number(cfg.atr_sig_length) || 5;
            const tr = new Array(n).fill(0);
            tr[0] = highs[0] - lows[0];
            for (let i = 1; i < n; i++) {{
                tr[i] = Math.max(highs[i] - lows[i], Math.abs(highs[i] - closes[i - 1]), Math.abs(lows[i] - closes[i - 1]));
            }}
            const atrArr = calculateWilderRma(tr, atrLen);
            for (let i = sigLen - 1; i < n; i++) {{
                let sum = 0, cnt = 0;
                for (let j = i - sigLen + 1; j <= i; j++) {{
                    if (!isNaN(atrArr[j])) {{ sum += atrArr[j]; cnt++; }}
                }}
                if (cnt === sigLen && !isNaN(atrArr[i])) {{
                    const sigVal = sum / sigLen;
                    lookup.set(times[i], {{
                        label: `ATR(${{atrLen}},${{sigLen}})`,
                        text: `${{atrArr[i].toFixed(1)}} / 均 ${{sigVal.toFixed(1)}}`,
                        color: '#b388ff'
                    }});
                }}
            }}
            window.exitLookupMap = lookup;
            if (exitLinePrimarySeries && exitLineSecondarySeries) {{
                exitLinePrimarySeries.setData([]);
                exitLineSecondarySeries.setData([]);
            }}
        }}

        // 更新左上角 K 線數據 (包含布林軌道與出場軌道數值)
        function updateLegend(bar, volume, timestamp) {{
            const ts = Number(timestamp || bar.time);
            const dtStr = formatBarTimestamp(ts, bar.datetime);
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

            // 更新出場軌道數值
            const exitLabelEl = document.getElementById('legend-exit-label');
            const exitValEl = document.getElementById('legend-exit-val');
            if (exitLabelEl && exitValEl) {{
                if (window.exitLookupMap && window.exitLookupMap.has(ts)) {{
                    const ex = window.exitLookupMap.get(ts);
                    exitLabelEl.textContent = ex.label;
                    exitLabelEl.style.color = ex.color;
                    exitValEl.textContent = ex.text;
                    exitValEl.style.color = ex.color;
                }} else {{
                    exitLabelEl.textContent = 'ATR指標';
                    exitValEl.textContent = '--';
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

            const dtMap = new Map();
            rawBars.forEach(b => {{
                if (b.time && b.datetime) {{
                    dtMap.set(Number(b.time), String(b.datetime));
                }}
            }});
            window.barDatetimeMap = dtMap;

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

            // 更新布林通道三軌與出場軌道線
            updateBollingerBands(rawBars);
            updateExitOverlay(rawBars);

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
            currentFocusedTradeId = tradeId;

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

            // 3. 高亮表格當前行並平滑捲動該行進入視野
            document.querySelectorAll('#trades-table-body tr').forEach(r => r.classList.remove('selected-trade-row'));
            const targetRow = document.getElementById(`trade-row-${{tradeId}}`);
            if (targetRow) {{
                targetRow.classList.add('selected-trade-row');
                try {{ targetRow.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }}); }} catch (e) {{}}
            }}

            // 4. 延遲 (等待 DOM 展開與寬度計算完成後) 精確縮放可視區間
            const delayMs = layoutSwitched ? 150 : 30;
            setTimeout(() => {{
                if (!klineChart) return;
                const duration = Math.max(0, exitTs - entryTs);
                const padSec = Math.max(3600 * 1.5, Math.min(3600 * 4, duration * 1.5));
                const fromSec = entryTs - padSec;
                const toSec = exitTs + padSec;

                try {{
                    klineChart.timeScale().setVisibleRange({{ from: fromSec, to: toSec }});
                }} catch (e) {{
                    console.warn('定位時間範圍失敗:', e);
                }}

                // 5. 顯示頂部交易特寫橫幅 Banner
                showTradeFocusBanner(trade);
            }}, delayMs);
        }}

        // 逐筆復盤巡覽器：跳轉上一筆 (-1) 或下一筆 (+1) 交易
        function navigateFocusedTrade(delta) {{
            const strat = getActiveStrategy();
            const list = (window.currentFilteredTrades && window.currentFilteredTrades.length > 0)
                ? window.currentFilteredTrades
                : (strat.trades || []);
            if (list.length === 0) return;

            let idx = list.findIndex(t => t.trade_id === currentFocusedTradeId);
            if (idx === -1) {{
                idx = delta > 0 ? 0 : list.length - 1;
            }} else {{
                idx = (idx + delta + list.length) % list.length;
            }}
            const target = list[idx];
            if (target) {{
                focusTradeOnChart(target.entry_ts, target.exit_ts, target.trade_id);
            }}
        }}

        // 清除交易聚焦狀態並恢復全景
        function clearTradeFocus() {{
            currentFocusedTradeId = null;
            document.querySelectorAll('#trades-table-body tr').forEach(r => r.classList.remove('selected-trade-row'));
            if (klineChart) {{
                klineChart.timeScale().fitContent();
            }}
            showToast('已恢復全景檢視', 'info');
        }}

        // 交易特寫橫幅已依使用者要求移除，僅保留 K 線區間自動縮放定位
        function showTradeFocusBanner(trade) {{
            return;
        }}

        // 渲染交易明細表格 (支援方向/結果/原因篩選與多欄位排序)
        function renderTradesTable() {{
            const tbody = document.getElementById('trades-table-body');
            if (!tbody) return;
            tbody.innerHTML = '';

            const strat = getActiveStrategy();
            const rawTrades = strat.trades || [];

            const sideFilter = (document.getElementById('filter-trade-side') || {{}}).value || 'ALL';
            const resFilter = (document.getElementById('filter-trade-result') || {{}}).value || 'ALL';
            const reasonFilter = (document.getElementById('filter-trade-reason') || {{}}).value || 'ALL';
            const sortBy = (document.getElementById('sort-trade-by') || {{}}).value || 'ID_ASC';

            let filtered = rawTrades.filter(t => {{
                if (sideFilter !== 'ALL' && t.side !== sideFilter) return false;
                if (resFilter === 'WIN' && t.net_pnl <= 0) return false;
                if (resFilter === 'LOSS' && t.net_pnl > 0) return false;
                if (reasonFilter === 'STOP_LOSS' && t.exit_reason !== '停損') return false;
                if (reasonFilter === 'PROFIT_EXIT' && t.exit_reason === '停損') return false;
                return true;
            }});

            filtered.sort((a, b) => {{
                const holdA = a.hold_mins !== undefined ? a.hold_mins : ((a.exit_ts - a.entry_ts) / 60);
                const holdB = b.hold_mins !== undefined ? b.hold_mins : ((b.exit_ts - b.entry_ts) / 60);
                if (sortBy === 'ID_DESC') return b.trade_id - a.trade_id;
                if (sortBy === 'PNL_DESC') return b.net_pnl - a.net_pnl;
                if (sortBy === 'PNL_ASC') return a.net_pnl - b.net_pnl;
                if (sortBy === 'HOLD_DESC') return holdB - holdA;
                return a.trade_id - b.trade_id;
            }});

            window.currentFilteredTrades = filtered;

            // 更新篩選摘要統計
            const summaryEl = document.getElementById('trades-filter-summary');
            if (summaryEl) {{
                const fWins = filtered.filter(t => t.net_pnl > 0).length;
                const fWinRate = filtered.length > 0 ? ((fWins / filtered.length) * 100).toFixed(1) : '0.0';
                const fSum = filtered.reduce((acc, t) => acc + t.net_pnl, 0);
                const fSign = fSum >= 0 ? '+' : '';
                const fColor = fSum >= 0 ? '#089981' : '#f23645';
                summaryEl.innerHTML = `
                    <span>顯示 <b>${{filtered.length}}</b> / ${{rawTrades.length}} 筆</span>
                    <span>｜</span>
                    <span>篩選勝率: <b style="color:#ffffff;">${{fWinRate}}%</b></span>
                    <span>｜</span>
                    <span>篩選小計: <b style="color:${{fColor}};">${{fSign}}${{Math.round(fSum).toLocaleString('zh-TW')}} TWD</b></span>
                `;
            }}

            filtered.forEach(t => {{
                const tr = document.createElement('tr');
                tr.id = `trade-row-${{t.trade_id}}`;
                if (currentFocusedTradeId === t.trade_id) tr.classList.add('selected-trade-row');
                const isLong = t.side === 'LONG';
                const isProfit = t.net_pnl >= 0;
                const pnlSign = isProfit ? '+' : '';
                const pnlClass = isProfit ? 'pnl-positive' : 'pnl-negative';
                const badgeClass = isLong ? 'badge-side-long' : 'badge-side-short';
                const sideText = isLong ? '做多' : '放空';
                const holdMins = Math.round(t.hold_mins !== undefined ? t.hold_mins : ((t.exit_ts - t.entry_ts) / 60));
                const contractVal = t.contract_value !== undefined ? t.contract_value : (t.entry_price * 10 * t.qty);
                const eqBefore = (t.equity_after - t.net_pnl) > 0 ? (t.equity_after - t.net_pnl) : 105150;
                const effLev = t.effective_leverage !== undefined ? t.effective_leverage : (contractVal / eqBefore);
                const marginRatio = t.margin_ratio_pct !== undefined ? t.margin_ratio_pct : ((eqBefore / (t.qty * 35050)) * 100);

                tr.innerHTML = `
                    <td>#${{t.trade_id}}</td>
                    <td><span class="${{badgeClass}}">${{sideText}}</span></td>
                    <td>${{t.entry_time}}</td>
                    <td style="font-weight:600;">${{t.entry_price.toFixed(1)}}</td>
                    <td>${{t.exit_time}}</td>
                    <td style="font-weight:600;">${{t.exit_price.toFixed(1)}}</td>
                    <td style="color:#9299a8;">${{holdMins}} 分</td>
                    <td>${{t.qty}}口</td>
                    <td style="color:#b388ff; font-weight:600;">${{Math.round(contractVal).toLocaleString('zh-TW')}}</td>
                    <td style="color:#00e5ff; font-weight:600;">${{effLev.toFixed(2)}}x <span style="color:#9299a8; font-weight:normal;">(${{marginRatio.toFixed(0)}}%)</span></td>
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

        // 匯出當前篩選之交易明細為 UTF-8 BOM CSV
        function exportActiveTradesCsv() {{
            const strat = getActiveStrategy();
            const rows = window.currentFilteredTrades || strat.trades || [];
            if (rows.length === 0) {{
                showToast('目前無符合條件之交易紀錄可供匯出', 'danger');
                return;
            }}
            const headers = ['交易編號', '策略名稱', '方向', '進場時間', '進場價格', '出場時間', '出場價格', '持倉時間(分)', '口數', '合約總價值(TWD)', '動態真實槓桿(倍)', '整戶維持率(%)', '出場原因', '交易成本(TWD)', '單筆淨損益(TWD)', '累積損益(TWD)', '帳戶總淨值(TWD)'];
            const csvLines = [headers.join(',')];
            rows.forEach(t => {{
                const holdMins = Math.round(t.hold_mins !== undefined ? t.hold_mins : ((t.exit_ts - t.entry_ts) / 60));
                const contractVal = t.contract_value !== undefined ? t.contract_value : (t.entry_price * 10 * t.qty);
                const eqBefore = (t.equity_after - t.net_pnl) > 0 ? (t.equity_after - t.net_pnl) : 105150;
                const effLev = t.effective_leverage !== undefined ? t.effective_leverage : (contractVal / eqBefore);
                const marginRatio = t.margin_ratio_pct !== undefined ? t.margin_ratio_pct : ((eqBefore / (t.qty * 35050)) * 100);
                const line = [
                    t.trade_id,
                    `"${{strat.displayName}}"`,
                    t.side === 'LONG' ? '做多(LONG)' : '放空(SHORT)',
                    `"${{t.entry_time}}"`,
                    t.entry_price.toFixed(1),
                    `"${{t.exit_time}}"`,
                    t.exit_price.toFixed(1),
                    holdMins,
                    t.qty,
                    Math.round(contractVal),
                    effLev.toFixed(2),
                    marginRatio.toFixed(1),
                    `"${{t.exit_reason}}"`,
                    t.costs.toFixed(0),
                    t.net_pnl.toFixed(1),
                    t.cum_pnl.toFixed(1),
                    t.equity_after.toFixed(1)
                ];
                csvLines.push(line.join(','));
            }});

            const blob = new Blob(['\\uFEFF' + csvLines.join('\\n')], {{ type: 'text/csv;charset=utf-8;' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `trades_${{currentStrategyKey}}_${{rows.length}}筆.csv`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast(`已成功匯出 ${{rows.length}} 筆交易明細 CSV！`, 'success');
        }}

        // 渲染分頁 2：「深度結構分析」面板與單筆損益分佈直方圖
        function renderQuantAnalytics() {{
            const strat = getActiveStrategy();
            const m = strat.metrics;
            if (!m) return;

            const badge = document.getElementById('qa-strat-badge');
            if (badge) {{
                badge.textContent = strat.displayName;
                badge.style.color = strat.themeColor;
            }}

            // 1. 多空雙向拆解
            const ls = m.longStats || {{ count: 0, wins: 0, winRate: 0, netPnl: 0, pf: 0 }};
            const ss = m.shortStats || {{ count: 0, wins: 0, winRate: 0, netPnl: 0, pf: 0 }};
            const totalCnt = Math.max(1, ls.count + ss.count);
            const longPct = Math.round((ls.count / totalCnt) * 100);
            const shortPct = 100 - longPct;

            const lTxt = document.getElementById('qa-long-ratio-txt');
            const sTxt = document.getElementById('qa-short-ratio-txt');
            const lBar = document.getElementById('qa-long-bar');
            const sBar = document.getElementById('qa-short-bar');
            if (lTxt) lTxt.textContent = `做多 (LONG): ${{ls.count}} 筆 (${{longPct}}%)`;
            if (sTxt) sTxt.textContent = `放空 (SHORT): ${{ss.count}} 筆 (${{shortPct}}%)`;
            if (lBar) lBar.style.width = `${{longPct}}%`;
            if (sBar) sBar.style.width = `${{shortPct}}%`;

            const lsTbody = document.getElementById('qa-ls-tbody');
            if (lsTbody) {{
                const fmtPnl = (v) => `${{v >= 0 ? '+' : ''}}${{Math.round(v).toLocaleString('zh-TW')}} TWD`;
                lsTbody.innerHTML = `
                    <tr>
                        <td><span class="badge-side-long">做多 LONG</span></td>
                        <td>${{ls.count}} 筆 (${{ls.wins}} 勝)</td>
                        <td>${{ls.winRate}}%</td>
                        <td style="color:#00e5ff;">${{Number(ls.pf).toFixed(2)}}</td>
                        <td class="${{ls.netPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}}">${{fmtPnl(ls.netPnl)}}</td>
                    </tr>
                    <tr>
                        <td><span class="badge-side-short">放空 SHORT</span></td>
                        <td>${{ss.count}} 筆 (${{ss.wins}} 勝)</td>
                        <td>${{ss.winRate}}%</td>
                        <td style="color:#00e5ff;">${{Number(ss.pf).toFixed(2)}}</td>
                        <td class="${{ss.netPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}}">${{fmtPnl(ss.netPnl)}}</td>
                    </tr>
                    <tr style="border-top:1px solid #2b3348;">
                        <td style="color:#9299a8;">全向合計</td>
                        <td>${{m.totalTrades}} 筆 (${{m.winningTrades}} 勝)</td>
                        <td>${{m.winRateStr}}</td>
                        <td style="color:#00e5ff;">${{m.profitFactorStr}}</td>
                        <td class="${{m.isProfit ? 'pnl-positive' : 'pnl-negative'}}">${{m.totalNetProfitStr}} TWD</td>
                    </tr>
                `;
            }}

            // 2. 出場原因歸因表
            const exitTbody = document.getElementById('qa-exit-tbody');
            if (exitTbody && m.exitBreakdown) {{
                exitTbody.innerHTML = m.exitBreakdown.map(eb => {{
                    const sign = eb.totalPnl >= 0 ? '+' : '';
                    const avgSign = eb.avgPnl >= 0 ? '+' : '';
                    const cls = eb.totalPnl >= 0 ? 'pnl-positive' : 'pnl-negative';
                    return `
                        <tr>
                            <td style="color:#ffffff;">${{eb.reason}}</td>
                            <td>${{eb.count}} 筆 (${{eb.ratioPct}}%)</td>
                            <td>${{eb.winRate}}%</td>
                            <td class="${{eb.avgPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}}">${{avgSign}}${{Math.round(eb.avgPnl).toLocaleString('zh-TW')}}</td>
                            <td class="${{cls}}">${{sign}}${{Math.round(eb.totalPnl).toLocaleString('zh-TW')}} TWD</td>
                        </tr>
                    `;
                }}).join('');
            }}

            // 3. 日夜盤時段與持倉結構
            const sessTbody = document.getElementById('qa-session-tbody');
            if (sessTbody && m.sessionStats) {{
                const d = m.sessionStats.day || {{ count: 0, wins: 0, winRate: 0, netPnl: 0, pf: 0 }};
                const n = m.sessionStats.night || {{ count: 0, wins: 0, winRate: 0, netPnl: 0, pf: 0 }};
                const fmtPnl = (v) => `${{v >= 0 ? '+' : ''}}${{Math.round(v).toLocaleString('zh-TW')}} TWD`;
                sessTbody.innerHTML = `
                    <tr>
                        <td style="color:#ffb300;">日盤 (08:45-13:45)</td>
                        <td>${{d.count}} 筆 (${{d.wins}} 勝)</td>
                        <td>${{d.winRate}}%</td>
                        <td style="color:#00e5ff;">${{Number(d.pf || 0).toFixed(2)}}</td>
                        <td class="${{d.netPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}}">${{fmtPnl(d.netPnl)}}</td>
                    </tr>
                    <tr>
                        <td style="color:#b388ff;">夜盤 (15:00-05:00)</td>
                        <td>${{n.count}} 筆 (${{n.wins}} 勝)</td>
                        <td>${{n.winRate}}%</td>
                        <td style="color:#00e5ff;">${{Number(n.pf || 0).toFixed(2)}}</td>
                        <td class="${{n.netPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}}">${{fmtPnl(n.netPnl)}}</td>
                    </tr>
                `;
            }}

            const streakEl = document.getElementById('qa-streak-badge');
            const winHoldEl = document.getElementById('qa-win-hold');
            const lossHoldEl = document.getElementById('qa-loss-hold');
            const sharpeEl = document.getElementById('qa-sharpe');
            if (streakEl) streakEl.textContent = `極端紀錄: ${{m.streakStr || '--'}}`;
            if (winHoldEl) winHoldEl.textContent = `${{m.avgWinHoldMins || 0}} 分鐘`;
            if (lossHoldEl) lossHoldEl.textContent = `${{m.avgLossHoldMins || 0}} 分鐘`;
            if (sharpeEl) sharpeEl.textContent = m.sharpeRatioStr || '0.00';

            drawPnlHistogram();
        }}

        // 繪製單筆交易損益分佈直方圖 (PnL Distribution Histogram)
        function drawPnlHistogram() {{
            const canvas = document.getElementById('pnl-dist-canvas');
            const box = document.getElementById('hist-canvas-box');
            if (!canvas || !box) return;
            const rect = box.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return;

            const dpr = window.devicePixelRatio || 1;
            canvas.width = Math.floor(rect.width * dpr);
            canvas.height = Math.floor(rect.height * dpr);
            canvas.style.width = rect.width + 'px';
            canvas.style.height = rect.height + 'px';

            const ctx = canvas.getContext('2d');
            ctx.resetTransform();
            ctx.scale(dpr, dpr);

            const w = rect.width;
            const h = rect.height;
            ctx.clearRect(0, 0, w, h);

            const strat = getActiveStrategy();
            const trades = strat.trades || [];
            if (trades.length === 0) return;

            const pnls = trades.map(t => Number(t.net_pnl));
            const minPnl = Math.min(...pnls, -1000);
            const maxPnl = Math.max(...pnls, 1500);
            const binCount = 12;
            const step = (maxPnl - minPnl) / binCount || 1;

            const bins = new Array(binCount).fill(0).map((_, i) => ({{
                low: minPnl + i * step,
                high: minPnl + (i + 1) * step,
                mid: minPnl + (i + 0.5) * step,
                count: 0
            }}));

            pnls.forEach(p => {{
                let idx = Math.floor((p - minPnl) / step);
                if (idx >= binCount) idx = binCount - 1;
                if (idx < 0) idx = 0;
                bins[idx].count++;
            }});

            const maxCount = Math.max(...bins.map(b => b.count), 1);
            const padTop = 22, padBottom = 28, padLeft = 36, padRight = 24;
            const chartW = w - padLeft - padRight;
            const chartH = h - padTop - padBottom;
            const barSlotW = chartW / binCount;
            const barW = Math.max(12, barSlotW * 0.72);

            // 背景橫向網格線
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
            ctx.lineWidth = 1;
            for (let g = 0; g <= 4; g++) {{
                const gy = padTop + (chartH / 4) * g;
                ctx.beginPath();
                ctx.moveTo(padLeft, gy);
                ctx.lineTo(padLeft + chartW, gy);
                ctx.stroke();
            }}

            // 繪製直方柱
            ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
            ctx.textAlign = 'center';
            bins.forEach((b, i) => {{
                const cx = padLeft + (i + 0.5) * barSlotW;
                const bh = (b.count / maxCount) * chartH;
                const bx = cx - barW / 2;
                const by = padTop + chartH - bh;
                const isPos = b.mid >= 0;

                ctx.fillStyle = isPos ? 'rgba(8, 153, 129, 0.78)' : 'rgba(242, 54, 69, 0.78)';
                ctx.strokeStyle = isPos ? '#089981' : '#f23645';
                ctx.lineWidth = 1.2;
                if (b.count > 0) {{
                    ctx.fillRect(bx, by, barW, bh);
                    ctx.strokeRect(bx, by, barW, bh);
                    ctx.fillStyle = '#ffffff';
                    ctx.fillText(`${{b.count}}筆`, cx, by - 5);
                }}

                // X 軸損益區間標籤
                const lbl = `${{b.mid >= 0 ? '+' : ''}}${{Math.round(b.mid)}}`;
                ctx.fillStyle = isPos ? '#26a69a' : '#ef5350';
                ctx.fillText(lbl, cx, padTop + chartH + 16);
            }});
        }}

        // ==============================================================
        // 核心一：策略淨值與回撤繪圖引擎 (MultiStrategyCompareChart)
        // 支援三種模式：'PNL' (累積損益) ｜ 'EQUITY' (帳戶總淨值) ｜ 'DRAWDOWN' (水下回撤曲線)
        // ==============================================================
        class MultiStrategyCompareChart {{
            constructor(canvas, viewport, tooltip) {{
                this.canvas = canvas;
                this.viewport = viewport;
                this.tooltip = tooltip;
                this.ctx = canvas.getContext('2d');

                this.mode = 'PNL';
                this.axisMode = 'X_TIME_Y_PNL';
                this.visibleSeries = {{
                    atr: true
                }};

                this.hoverIndex = -1;
                this.initData();
                this.initEvents();
            }}

            initData() {{
                const bars5m = KLINE_DATA['5m'] || [];
                const stratAtr = STRATEGIES_DATA['atr'];
                const initCap = (stratAtr && stratAtr.metrics && stratAtr.metrics.initialCapital)
                    ? Number(stratAtr.metrics.initialCapital)
                    : 105150;
                this.initialCapital = initCap;

                const cfg = (typeof strategyConfigs !== 'undefined' && strategyConfigs.atr) ? strategyConfigs.atr : {{}};
                const startBound = cfg.start_date ? (String(cfg.start_date).trim() + ' 00:00:00') : '';
                const endBound = cfg.end_date ? (String(cfg.end_date).trim() + ' 23:59:59') : '';

                const filteredBars = bars5m.filter(b => {{
                    if (startBound && b.datetime < startBound) return false;
                    if (endBound && b.datetime > endBound) return false;
                    return true;
                }});

                this.timePoints = (filteredBars.length > 0 ? filteredBars : bars5m).map(b => ({{
                    time: Number(b.time),
                    datetime: b.datetime
                }}));

                this.seriesData = {{}};
                const keys = ['atr'];

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

                this.alignedPoints = [];
                let lastVals = {{
                    atr: {{ pnl: 0, equity: initCap, drawdown: 0 }}
                }};

                for (let i = 0; i < this.timePoints.length; i++) {{
                    const t = this.timePoints[i].time;
                    const dt = this.timePoints[i].datetime;

                    const row = {{
                        index: i,
                        time: t,
                        datetime: dt,
                        atr: {{ ...lastVals.atr }}
                    }};

                    keys.forEach(k => {{
                        if (this.seriesData[k]) {{
                            const pt = this.seriesData[k].get(t);
                            if (pt) {{
                                const ddVal = pt.drawdown !== undefined ? pt.drawdown : 0;
                                row[k] = {{
                                    pnl: pt.value,
                                    equity: pt.equity,
                                    drawdown: ddVal
                                }};
                                lastVals[k] = {{
                                    pnl: pt.value,
                                    equity: pt.equity,
                                    drawdown: ddVal
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
                const aVal = pt.atr;

                const formatVal = (v) => {{
                    if (this.mode === 'PNL') {{
                        const sign = v.pnl >= 0 ? '+' : '';
                        return `${{sign}}${{Math.round(v.pnl).toLocaleString('zh-TW')}} TWD`;
                    }} else if (this.mode === 'EQUITY') {{
                        return `${{Math.round(v.equity).toLocaleString('zh-TW')}} TWD`;
                    }} else {{
                        return `${{Math.round(v.drawdown).toLocaleString('zh-TW')}} TWD`;
                    }}
                }};

                let html = `
                    <div class="mt-header">
                        <span>${{pt.datetime || 'K線時序'}}</span>
                        <span>Bar #${{idx + 1}} / ${{this.alignedPoints.length}}</span>
                    </div>
                `;

                if (this.visibleSeries.atr) {{
                    html += `
                        <div class="mt-row">
                            <span class="mt-strat-label">
                                <span class="mt-strat-dot" style="background:#b388ff;"></span>
                                <span>布林_ATR基準</span>
                            </span>
                            <span class="mt-strat-val" style="color:#b388ff;">${{formatVal(aVal)}}</span>
                        </div>
                    `;
                }}

                this.tooltip.innerHTML = html;
                this.tooltip.style.display = 'block';

                const tipW = 275;
                const tipH = 110;
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

            setMode(newMode) {{
                this.mode = newMode;
                this.draw();
                return this.mode;
            }}

            toggleMode() {{
                const order = ['PNL', 'EQUITY', 'DRAWDOWN'];
                const nextIdx = (order.indexOf(this.mode) + 1) % order.length;
                this.mode = order[nextIdx];
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

                const compKey = this.mode === 'PNL' ? 'pnl' : (this.mode === 'EQUITY' ? 'equity' : 'drawdown');
                let minVal = Infinity;
                let maxVal = -Infinity;

                const keys = ['atr'].filter(k => this.visibleSeries[k]);
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
                }} else if (this.mode === 'EQUITY') {{
                    if (minVal > 100000) minVal = 100000;
                    if (maxVal < 122000) maxVal = 122000;
                }} else {{
                    // 水下回撤曲線：頂部 0 軸，向下延伸至最大回撤
                    maxVal = 500;
                    if (minVal > -5000) minVal = -5000;
                }}

                const margin = (maxVal - minVal) * 0.08;
                minVal -= margin;
                if (this.mode !== 'DRAWDOWN') maxVal += margin;

                const valRange = (maxVal - minVal) || 1;
                const seriesConfigs = [
                    {{ key: 'atr', color: '#b388ff', fill: 'rgba(179, 136, 255, 0.12)', name: '布林_ATR基準' }}
                ];

                const padTop = 22;
                const padBottom = 34;
                const padLeft = 24;
                const padRight = 88;
                const chartW = w - padLeft - padRight;
                const chartH = h - padTop - padBottom;

                this.layout = {{
                    padTop, padBottom, padLeft, padRight,
                    chartWidth: chartW,
                    chartHeight: chartH
                }};

                const getY = (val) => padTop + chartH - ((val - minVal) / valRange) * chartH;
                const getX = (idx) => padLeft + (idx / Math.max(1, total - 1)) * chartW;

                // 1. 橫向網格線與右側 Y 軸刻度
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

                    const sign = (curVal > 0 && this.mode === 'PNL') ? '+' : '';
                    const label = `${{sign}}${{Math.round(curVal).toLocaleString('zh-TW')}} TWD`;
                    ctx.fillStyle = (curVal > 0) ? '#26a69a' : (curVal < 0 ? '#ef5350' : '#8b949e');
                    ctx.fillText(label, padLeft + chartW + 8, y + 4);
                }}

                // 2. 垂直時間網格線與底部 X 軸時間刻度
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

                // 3. 基準線 (0 基準線 / 期初資金 / 水下 0 軸水面線)
                const curInitCap = this.initialCapital || 105150;
                const baseVal = (this.mode === 'EQUITY') ? curInitCap : 0;
                if (baseVal >= minVal && baseVal <= maxVal) {{
                    const baseY = getY(baseVal);
                    ctx.save();
                    ctx.setLineDash([4, 4]);
                    ctx.strokeStyle = this.mode === 'DRAWDOWN' ? 'rgba(0, 229, 255, 0.45)' : 'rgba(255, 255, 255, 0.35)';
                    ctx.lineWidth = 1.5;
                    ctx.beginPath();
                    ctx.moveTo(padLeft, baseY);
                    ctx.lineTo(padLeft + chartW, baseY);
                    ctx.stroke();

                    ctx.fillStyle = '#ffffff';
                    ctx.font = '10px sans-serif';
                    ctx.textAlign = 'left';
                    const baseTitle = this.mode === 'PNL'
                        ? '0 損益基準線'
                        : (this.mode === 'EQUITY' ? `期初資金基準線 (${{Math.round(curInitCap).toLocaleString('zh-TW')}} TWD)` : '歷史最高淨值基準面 (Drawdown = 0)');
                    ctx.fillText(baseTitle, padLeft + 6, baseY - 6);
                    ctx.restore();
                }}

                // 4. 繪製策略曲線 (水下回撤模式下附加半透明水下陰影)
                seriesConfigs.forEach(sc => {{
                    if (!this.visibleSeries[sc.key]) return;

                    if (this.mode === 'DRAWDOWN') {{
                        const zeroY = getY(0);
                        ctx.save();
                        ctx.fillStyle = sc.fill;
                        ctx.beginPath();
                        ctx.moveTo(getX(0), zeroY);
                        for (let i = 0; i < total; i++) {{
                            ctx.lineTo(getX(i), getY(this.alignedPoints[i][sc.key][compKey]));
                        }}
                        ctx.lineTo(getX(total - 1), zeroY);
                        ctx.closePath();
                        ctx.fill();
                        ctx.restore();
                    }}

                    ctx.save();
                    ctx.lineWidth = 2.6;
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

                // 5. 十字準星
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

        // 切換策略圖表顯示模式 (PNL / EQUITY / DRAWDOWN)
        function setMultiChartMode(mode) {{
            if (!multiCompareChart) return;
            multiCompareChart.setMode(mode);
            document.querySelectorAll('.mode-pill-btn').forEach(btn => {{
                btn.classList.toggle('active', btn.dataset.cmode === mode);
            }});
            const strat = getActiveStrategy();
            const initCapStr = (strat && strat.metrics && strat.metrics.initialCapitalStr) ? strat.metrics.initialCapitalStr : '105,150';
            if (txtAxisHint) {{
                if (mode === 'PNL') txtAxisHint.textContent = '橫軸 X = 時間時序推進 ｜ 縱軸 Y = 累積淨損益 (TWD)';
                else if (mode === 'EQUITY') txtAxisHint.textContent = `橫軸 X = 時間時序推進 ｜ 縱軸 Y = 帳戶總淨值 (TWD，含期初本金 ${{initCapStr}})`;
                else txtAxisHint.textContent = '橫軸 X = 時間時序推進 ｜ 縱軸 Y = 水下回撤深度 (Drawdown TWD，越靠近 0 軸代表抗跌與修復力越強)';
            }}
        }}

        // ==============================================================
        // 核心二：橫軸 X = 交易次數 × 縱軸 Y = 累積損益 (TWD) & 帳戶淨值 Canvas 引擎
        // ==============================================================
        class TradeEquityChart {{
            constructor(canvas, viewport, tooltip) {{
                this.canvas = canvas;
                this.viewport = viewport;
                this.tooltip = tooltip;
                this.ctx = canvas.getContext('2d');
                this.mode = 'X_TRADES_Y_PNL';
                this.showDeltas = true;
                this.hoverIndex = -1;
                this.setTrades();
                this.initEvents();
            }}

            setTrades() {{
                const strat = getActiveStrategy();
                const initCap = (strat && strat.metrics && strat.metrics.initialCapital)
                    ? Number(strat.metrics.initialCapital)
                    : 105150;
                this.initialCapital = initCap;
                this.trades = strat.trades || [];
                this.equityPoints = [
                    {{ trade_id: 0, cum_pnl: 0, equity_after: initCap, net_pnl: 0, exit_reason: '期初基準', side: '--', entry_time: '--', exit_time: '--' }},
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
                    const px = L.getX(i);
                    const dist = Math.abs(mx - px);
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
                const curInitCap = this.initialCapital || 105150;

                let html = '';
                if (isOrigin) {{
                    html = `
                        <div class="tt-title">
                            <span>期初基準狀態</span>
                            <span style="color:#787b86;">Trade #0</span>
                        </div>
                        <div class="tt-row"><span class="tt-label">初始資金:</span><span class="tt-val">${{Number(curInitCap).toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}} TWD</span></div>
                        <div class="tt-row"><span class="tt-label">累積損益:</span><span class="tt-val">0.0 TWD</span></div>
                    `;
                }} else {{
                    const sideLabel = pt.side === 'LONG' ? '做多 (LONG)' : '放空 (SHORT)';
                    const sideColor = pt.side === 'LONG' ? '#089981' : '#f23645';
                    html = `
                        <div class="tt-title">
                            <span>第 #${{pt.trade_id}} 筆交易</span>
                            <span style="color:${{sideColor}}; font-weight:700;">${{sideLabel}}</span>
                        </div>
                        <div class="tt-row"><span class="tt-label">進場:</span><span class="tt-val">${{pt.entry_time}} @ ${{Number(pt.entry_price).toFixed(1)}}</span></div>
                        <div class="tt-row"><span class="tt-label">出場:</span><span class="tt-val">${{pt.exit_time}} @ ${{Number(pt.exit_price).toFixed(1)}}</span></div>
                        <div class="tt-row"><span class="tt-label">平倉原因:</span><span class="tt-val">${{pt.exit_reason}}</span></div>
                        <div class="tt-row"><span class="tt-label">單筆損益:</span><span class="tt-val ${{isWin ? 'win' : 'loss'}}">${{pnlSign}}${{Number(pt.net_pnl).toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}} TWD</span></div>
                        <div class="tt-row"><span class="tt-label">累積損益:</span><span class="tt-val ${{pt.cum_pnl >= 0 ? 'win' : 'loss'}}">${{cumSign}}${{Number(pt.cum_pnl).toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}} TWD</span></div>
                        <div class="tt-row"><span class="tt-label">帳戶總淨值:</span><span class="tt-val" style="color:#ffffff;">${{Number(pt.equity_after).toLocaleString('zh-TW', {{minimumFractionDigits: 1}})}} TWD</span></div>
                        <div class="tt-footer">點擊此處立即定位上方 K 線圖</div>
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
                if (total === 0) return;

                const padTop = 22;
                const padBottom = 34;
                const padLeft = 28;
                const padRight = 104;
                const chartW = w - padLeft - padRight;
                const chartH = h - padTop - padBottom;

                let minPnl = 0;
                let maxPnl = 0;
                this.equityPoints.forEach(p => {{
                    if (p.cum_pnl < minPnl) minPnl = p.cum_pnl;
                    if (p.cum_pnl > maxPnl) maxPnl = p.cum_pnl;
                }});

                minPnl = Math.min(minPnl - 1000, -3000);
                maxPnl = Math.max(maxPnl + 1500, 6000);
                const pnlRange = (maxPnl - minPnl) || 1;

                // 橫軸 X = 交易次數 (第 #0 .. #N 筆)，縱軸 Y = 累積損益 (TWD) & 帳戶淨值
                const getX = (idx) => padLeft + (idx / Math.max(1, total - 1)) * chartW;
                const getY = (pnl) => padTop + chartH - ((pnl - minPnl) / pnlRange) * chartH;

                this.layout = {{ padTop, padBottom, padLeft, padRight, chartWidth: chartW, chartHeight: chartH, getX, getY }};

                // 1. 橫向網格線與右側 Y 軸累積損益 / 淨值刻度
                ctx.lineWidth = 1;
                ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
                ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
                ctx.textAlign = 'left';

                const gridSteps = 5;
                for (let i = 0; i <= gridSteps; i++) {{
                    const curPnl = minPnl + (pnlRange / gridSteps) * i;
                    const y = getY(curPnl);

                    ctx.beginPath();
                    ctx.moveTo(padLeft, y);
                    ctx.lineTo(padLeft + chartW, y);
                    ctx.stroke();

                    const sign = curPnl > 0 ? '+' : '';
                    const lbl = `${{sign}}${{Math.round(curPnl).toLocaleString('zh-TW')}} TWD`;
                    ctx.fillStyle = curPnl > 0 ? '#26a69a' : (curPnl < 0 ? '#ef5350' : '#8b949e');
                    ctx.fillText(lbl, padLeft + chartW + 8, y + 4);
                }}

                // 2. 垂直網格線與底部 X 軸交易次數刻度 (#0 .. #N)
                ctx.textAlign = 'center';
                ctx.fillStyle = '#8b949e';
                const xSteps = Math.min(10, Math.max(1, total - 1));
                for (let s = 0; s <= xSteps; s++) {{
                    const idx = Math.round((total - 1) * (s / xSteps));
                    const x = getX(idx);

                    ctx.save();
                    ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
                    ctx.beginPath();
                    ctx.moveTo(x, padTop);
                    ctx.lineTo(x, padTop + chartH);
                    ctx.stroke();
                    ctx.restore();

                    const trId = this.equityPoints[idx].trade_id;
                    ctx.fillText(`#${{trId}}`, x, padTop + chartH + 18);
                }}

                // 3. 0 損益基準橫線
                const zeroY = getY(0);
                const curInitCap = this.initialCapital || 105150;
                if (zeroY >= padTop && zeroY <= padTop + chartH) {{
                    ctx.save();
                    ctx.setLineDash([4, 4]);
                    ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
                    ctx.lineWidth = 1.4;
                    ctx.beginPath();
                    ctx.moveTo(padLeft, zeroY);
                    ctx.lineTo(padLeft + chartW, zeroY);
                    ctx.stroke();

                    ctx.fillStyle = '#d1d4dc';
                    ctx.font = '10px sans-serif';
                    ctx.textAlign = 'left';
                    ctx.fillText(`0 損益基準線 (本金 ${{Math.round(curInitCap).toLocaleString('zh-TW')}} TWD)`, padLeft + 6, zeroY - 6);
                    ctx.restore();
                }}

                // 4. 繪製逐筆累積損益折線
                ctx.save();
                ctx.lineWidth = 2.2;
                ctx.strokeStyle = '#089981';
                ctx.beginPath();
                for (let i = 0; i < total; i++) {{
                    const px = getX(i);
                    const py = getY(this.equityPoints[i].cum_pnl);
                    if (i === 0) ctx.moveTo(px, py);
                    else ctx.lineTo(px, py);
                }}
                ctx.stroke();
                ctx.restore();

                // 5. 繪製每筆交易節點圓點
                for (let i = 0; i < total; i++) {{
                    const px = getX(i);
                    const py = getY(this.equityPoints[i].cum_pnl);
                    const isWin = this.equityPoints[i].net_pnl >= 0;

                    ctx.fillStyle = (i === 0) ? '#ffffff' : (isWin ? '#089981' : '#f23645');
                    ctx.beginPath();
                    ctx.arc(px, py, (i === this.hoverIndex) ? 5.5 : 3.2, 0, Math.PI * 2);
                    ctx.fill();
                }}

                // 6. 懸停十字準星
                if (this.hoverIndex >= 0 && this.hoverIndex < total) {{
                    const hx = getX(this.hoverIndex);
                    const hy = getY(this.equityPoints[this.hoverIndex].cum_pnl);
                    ctx.save();
                    ctx.setLineDash([3, 3]);
                    ctx.strokeStyle = 'rgba(255, 255, 255, 0.45)';
                    ctx.beginPath();
                    ctx.moveTo(hx, padTop);
                    ctx.lineTo(hx, padTop + chartH);
                    ctx.moveTo(padLeft, hy);
                    ctx.lineTo(padLeft + chartW, hy);
                    ctx.stroke();
                    ctx.restore();
                }}
            }}
        }}

        // 切換全畫面數據視圖 / 雙圖模式
        function toggleLayoutMode(skipFitContent = false) {{
            isSoloDataMode = !isSoloDataMode;
            workspaceSplit.classList.toggle('solo-data-mode', isSoloDataMode);

            const btn = document.getElementById('btn-toggle-layout');
            const txt = document.getElementById('txt-toggle-layout');

            if (isSoloDataMode) {{
                if (btn) btn.classList.add('mode-active');
                if (txt) txt.textContent = '數據全畫面 (作用中)';
                showToast('已切換為：數據全畫面視圖 (僅顯示量化數據與淨值圖)', 'info');
            }} else {{
                if (btn) btn.classList.remove('mode-active');
                if (txt) txt.textContent = '展開 K 線雙圖';
                showToast('已切換為：雙圖聯動視圖 (K 線主圖 + 數據面板)', 'info');
            }}

            setTimeout(() => {{
                if (klineChart && !isSoloDataMode && !skipFitContent) klineChart.timeScale().fitContent();
                if (perfChart) perfChart.timeScale().fitContent();
                if (multiCompareChart) multiCompareChart.resizeAndDraw();
                if (tradeEquityChart) tradeEquityChart.resizeAndDraw();
                drawPnlHistogram();
            }}, 100);
        }}

        // ==============================================================
        // 核心三：前端高解析度即時回測引擎 (In-Browser Backtest Engine)
        // 100% 對齊後端 Python BacktestEngine 狀態機撮合邏輯與進階統計指標
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

        // 前端同步計算進階機構級統計指標
        function computeJsExtendedStats(trades, totalNetProfit, maxDd) {{
            const nTrades = trades.length;
            if (nTrades === 0) {{
                return {{
                    expectancy: 0, expectancyStr: '0',
                    returnToMdd: 0, returnToMddStr: '0.00',
                    sharpeRatio: 0, sharpeRatioStr: '0.00',
                    maxConsecWins: 0, maxConsecLosses: 0, streakStr: '0連勝 / 0連敗',
                    avgHoldMins: 0, avgWinHoldMins: 0, avgLossHoldMins: 0, avgHoldStr: '0 分',
                    longStats: {{ count: 0, wins: 0, winRate: 0, netPnl: 0, pf: 0 }},
                    shortStats: {{ count: 0, wins: 0, winRate: 0, netPnl: 0, pf: 0 }},
                    sessionStats: {{
                        day: {{ count: 0, wins: 0, winRate: 0, netPnl: 0, pf: 0 }},
                        night: {{ count: 0, wins: 0, winRate: 0, netPnl: 0, pf: 0 }}
                    }},
                    exitBreakdown: []
                }};
            }}

            const expectancy = totalNetProfit / nTrades;
            const expSign = expectancy >= 0 ? '+' : '';
            const absMdd = Math.abs(maxDd);
            const retToMdd = absMdd > 0 ? (totalNetProfit / absMdd) : 0;

            const pnls = trades.map(t => t.net_pnl);
            const meanPnl = pnls.reduce((a, b) => a + b, 0) / nTrades;
            const variance = pnls.reduce((acc, x) => acc + Math.pow(x - meanPnl, 2), 0) / Math.max(1, nTrades - 1);
            const stdPnl = variance > 0 ? Math.sqrt(variance) : 0;
            const sharpe = stdPnl > 0 ? (meanPnl / stdPnl) * Math.sqrt(nTrades) : 0;

            let maxW = 0, maxL = 0, curW = 0, curL = 0;
            for (const p of pnls) {{
                if (p > 0) {{ curW++; curL = 0; if (curW > maxW) maxW = curW; }}
                else if (p < 0) {{ curL++; curW = 0; if (curL > maxL) maxL = curL; }}
            }}

            const holdAll = trades.map(t => t.hold_mins || 0);
            const holdWins = trades.filter(t => t.net_pnl > 0).map(t => t.hold_mins || 0);
            const holdLosses = trades.filter(t => t.net_pnl <= 0).map(t => t.hold_mins || 0);
            const avgHold = holdAll.length > 0 ? holdAll.reduce((a, b) => a + b, 0) / holdAll.length : 0;
            const avgWinHold = holdWins.length > 0 ? holdWins.reduce((a, b) => a + b, 0) / holdWins.length : 0;
            const avgLossHold = holdLosses.length > 0 ? holdLosses.reduce((a, b) => a + b, 0) / holdLosses.length : 0;

            function calcSide(sub) {{
                const cnt = sub.length;
                if (cnt === 0) return {{ count: 0, wins: 0, winRate: 0, netPnl: 0, pf: 0 }};
                const wList = sub.filter(x => x.net_pnl > 0).map(x => x.net_pnl);
                const lList = sub.filter(x => x.net_pnl < 0).map(x => x.net_pnl);
                const gw = wList.reduce((a, b) => a + b, 0);
                const gl = Math.abs(lList.reduce((a, b) => a + b, 0));
                const pf = gl > 0 ? (gw / gl) : (gw > 0 ? 99.0 : 0);
                return {{
                    count: cnt,
                    wins: wList.length,
                    winRate: Math.round((wList.length / cnt) * 10000) / 100,
                    netPnl: Math.round(sub.reduce((a, x) => a + x.net_pnl, 0) * 10) / 10,
                    pf: Math.round(pf * 100) / 100
                }};
            }}

            const longTrades = trades.filter(t => t.side === 'LONG');
            const shortTrades = trades.filter(t => t.side === 'SHORT');

            const dayTrades = [];
            const nightTrades = [];
            for (const t of trades) {{
                const tsStr = String(t.entry_time || '');
                let hr = 9;
                if (tsStr.length >= 13) {{
                    const parsed = parseInt(tsStr.substring(11, 13), 10);
                    if (!isNaN(parsed)) hr = parsed;
                }}
                if (hr >= 8 && hr <= 13) dayTrades.push(t);
                else nightTrades.push(t);
            }}

            const reasonMap = new Map();
            for (const t of trades) {{
                if (!reasonMap.has(t.exit_reason)) reasonMap.set(t.exit_reason, []);
                reasonMap.get(t.exit_reason).push(t.net_pnl);
            }}
            const exitBreakdown = [];
            for (const [rName, rPnls] of reasonMap.entries()) {{
                const rCnt = rPnls.length;
                const rTot = rPnls.reduce((a, b) => a + b, 0);
                const rWins = rPnls.filter(v => v > 0).length;
                exitBreakdown.push({{
                    reason: rName,
                    count: rCnt,
                    ratioPct: Math.round((rCnt / nTrades) * 1000) / 10,
                    totalPnl: Math.round(rTot * 10) / 10,
                    avgPnl: Math.round((rTot / rCnt) * 10) / 10,
                    winRate: Math.round((rWins / rCnt) * 1000) / 10
                }});
            }}

            return {{
                expectancy: Math.round(expectancy * 10) / 10,
                expectancyStr: `${{expSign}}${{Math.round(expectancy).toLocaleString('zh-TW')}}`,
                returnToMdd: Math.round(retToMdd * 100) / 100,
                returnToMddStr: retToMdd.toFixed(2),
                sharpeRatio: Math.round(sharpe * 100) / 100,
                sharpeRatioStr: sharpe.toFixed(2),
                maxConsecWins: maxW,
                maxConsecLosses: maxL,
                streakStr: `${{maxW}}連勝 / ${{maxL}}連敗`,
                avgHoldMins: Math.round(avgHold * 10) / 10,
                avgWinHoldMins: Math.round(avgWinHold * 10) / 10,
                avgLossHoldMins: Math.round(avgLossHold * 10) / 10,
                avgHoldStr: `${{Math.round(avgHold)}} 分 (${{(avgHold / 5).toFixed(1)}} 根)`,
                longStats: calcSide(longTrades),
                shortStats: calcSide(shortTrades),
                sessionStats: {{
                    day: calcSide(dayTrades),
                    night: calcSide(nightTrades)
                }},
                exitBreakdown
            }};
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

            // 3. 撮合回測狀態機 (基準 A: 130% 整戶原始保證金維持率 + 動態浮動槓桿 + 自訂回測區間與初始資金)
            const initialCap = Math.max(35050.0, Number(config.initial_capital) || 105150.0);
            const startDateStr = (config.start_date || '').trim();
            const endDateStr = (config.end_date || '').trim();
            const startBoundary = startDateStr ? `${{startDateStr}} 00:00:00` : '';
            const endBoundary = endDateStr ? `${{endDateStr}} 23:59:59` : '';
            const pointValue = 10.0;
            const marginReq = 35050.0;
            const maintMargin = 26900.0;
            const minMarginRatio = Math.max(1.0, Number(config.min_margin_ratio) || 1.30);
            const requiredPerLot = marginReq * minMarginRatio;
            const maxLeverage = Number(config.max_leverage) || 7.0;
            const commissionPerOrder = Number(config.commission) || 18.0;
            const taxRate = 0.00002;
            const slippage = Number(config.slippage) || 0.0;
            const sizingMode = config.position_sizing_mode || 'margin_ratio_130';

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
            let runningPeakEq = initialCap;

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

                // 回測區間邊界過濾 (指標已在全量 bars 預先計算完畢，確保區間首根 K 棒具備完整暖機)
                if (startBoundary && curTimeStr < startBoundary) {{
                    continue;
                }}
                if (endBoundary && curTimeStr > endBoundary) {{
                    break;
                }}

                // A. 進場執行 (Bar 3)：嚴格執行原始保證金開倉門檻與基準 A (130% 整戶維持率)
                if (entryReadySide !== 'NONE' && posSide === 'NONE') {{
                    let safeQty = 0;
                    if (equity < marginReq || curOpen <= 0) {{
                        safeQty = 0;
                    }} else if (sizingMode === 'fixed_one_lot') {{
                        safeQty = (equity >= requiredPerLot) ? 1 : 0;
                    }} else if (sizingMode === 'notional') {{
                        const maxContractVal = equity * maxLeverage;
                        const singleContractVal = curOpen * pointValue;
                        const theoreticalQty = maxContractVal / singleContractVal;
                        const marginQty = equity / marginReq;
                        safeQty = Math.max(0, Math.floor(Math.min(theoreticalQty, marginQty)));
                    }} else {{
                        // 基準 A (margin_ratio_130 / margin)：維持率不得低於設定門檻 (預設 130% 即單口 45,565 元)，槓桿隨盤勢動態浮動
                        safeQty = (equity >= requiredPerLot) ? Math.max(0, Math.floor(equity / requiredPerLot)) : 0;
                    }}

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

                // B. 出場檢核 (ATR 基準)
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
                    }} else {{
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
                        const equityBefore = equity;
                        const actualExitPrice = (posSide === 'LONG') ? (rawExitPrice - slippage) : (rawExitPrice + slippage);
                        const grossPnl = (posSide === 'LONG')
                            ? (actualExitPrice - posEntryPrice) * pointValue * posQty
                            : (posEntryPrice - actualExitPrice) * pointValue * posQty;

                        const feeTotal = commissionPerOrder * 2.0 * posQty;
                        const entryNotional = posEntryPrice * pointValue * posQty;
                        const exitNotional = actualExitPrice * pointValue * posQty;
                        const taxTotal = Math.ceil(entryNotional * taxRate) + Math.ceil(exitNotional * taxRate);
                        const netPnl = grossPnl - feeTotal - taxTotal;
                        equity += netPnl;
                        runningCumPnl += netPnl;

                        const effLeverage = equityBefore > 0 ? Math.round((entryNotional / equityBefore) * 100) / 100 : 0;
                        const marginRatioPct = (posQty > 0 && marginReq > 0) ? Math.round((equityBefore / (posQty * marginReq)) * 1000) / 10 : 0;

                        let exitReasonZh = '結算';
                        if (exitReason === 'STOP_LOSS') exitReasonZh = '停損';
                        else if (exitReason === 'ATR_TAKE_PROFIT') exitReasonZh = 'ATR停利';

                        const holdMins = Math.max(5, Math.round((curTs - times[posEntryIdx]) / 60));

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
                            contract_value: Math.round(entryNotional),
                            effective_leverage: effLeverage,
                            margin_ratio_pct: marginRatioPct,
                            entry_ts: times[posEntryIdx],
                            exit_ts: curTs,
                            hold_mins: holdMins
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

                // D. 記錄每根 K 線浮動淨值與水下回撤
                let curEq = equity;
                if (posSide !== 'NONE') {{
                    const unrealized = (posSide === 'LONG')
                        ? (curClose - posEntryPrice) * pointValue * posQty
                        : (posEntryPrice - curClose) * pointValue * posQty;
                    curEq += unrealized;
                }}
                if (curEq > runningPeakEq) runningPeakEq = curEq;
                const ddNow = curEq - runningPeakEq;

                equityCurve.push({{
                    time: curTs,
                    value: Math.round((curEq - initialCap) * 100) / 100,
                    equity: Math.round(curEq * 100) / 100,
                    drawdown: Math.round(ddNow * 100) / 100,
                    close: curClose,
                    in_position: (posSide !== 'NONE')
                }});
            }}

            // 4. 量化指標彙總
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
            const extStats = computeJsExtendedStats(trades, totalNetProfit, maxDd);

            const metrics = {{
                strategyName: config.strategy_name || '布林_ATR基準',
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
                startDate: startDateStr || (datetimes[0] ? datetimes[0].slice(0, 10) : ''),
                endDate: endDateStr || (datetimes[n - 1] ? datetimes[n - 1].slice(0, 10) : ''),
                avgWin: Math.round(avgWin),
                avgWinStr: `+${{Math.round(avgWin).toLocaleString('zh-TW')}}`,
                avgLoss: Math.round(avgLoss),
                avgLossStr: `-${{Math.round(avgLoss).toLocaleString('zh-TW')}}`,
                payoffRatio: Math.round(payoff * 100) / 100,
                payoffRatioStr: payoff.toFixed(2),
                ...extStats
            }};

            // 5. 產生買賣 Markers
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

        // 策略參數集中配置庫 (僅保留布林_ATR基準 + 基準 A 130% 整戶維持率控管 + 自訂區間與本金)
        const EARLIEST_BAR_DATE = '{earliest_bar_date}';
        const LATEST_BAR_DATE = '{latest_bar_date}';
        const strategyConfigs = {{
            atr: {{
                strategy_name: '布林_ATR基準',
                exit_mode: 'atr',
                bb_length: 30,
                bb_mult: 1.75,
                pullback_ratio: 0.25,
                atr_length: 14,
                atr_sig_length: 5,
                initial_capital: 105150.0,
                start_date: '{earliest_bar_date}',
                end_date: '{latest_bar_date}',
                min_margin_ratio: 1.30,
                max_leverage: 7.0,
                commission: 18.0,
                slippage: 0.0,
                position_sizing_mode: 'margin_ratio_130'
            }}
        }};
        const defaultStrategyConfigs = JSON.parse(JSON.stringify(strategyConfigs));
        let activeModalStrat = 'atr';

        // 刷新頂部策略橫向 KPI 矩陣卡片
        function updateKpiCard(stratKey) {{
            const strat = STRATEGIES_DATA[stratKey];
            if (!strat) return;
            const m = strat.metrics;
            const titleEl = document.getElementById(`kpi-title-${{stratKey}}`);
            const pnlEl = document.getElementById(`kpi-${{stratKey}}-pnl`);
            const winrateEl = document.getElementById(`kpi-${{stratKey}}-winrate`);
            const pfEl = document.getElementById(`kpi-${{stratKey}}-pf`);
            const expEl = document.getElementById(`kpi-${{stratKey}}-exp`);
            const mddEl = document.getElementById(`kpi-${{stratKey}}-mdd`);
            const calmarEl = document.getElementById(`kpi-${{stratKey}}-calmar`);
            const avgwlEl = document.getElementById(`kpi-${{stratKey}}-avgwl`);
            const payoffEl = document.getElementById(`kpi-${{stratKey}}-payoff`);

            if (titleEl) titleEl.textContent = strat.displayName;
            if (pnlEl) {{
                pnlEl.textContent = m.totalNetProfitStr;
                pnlEl.className = `kpi-sub-val ${{m.isProfit ? 'win' : 'loss'}}`;
            }}
            if (winrateEl) winrateEl.textContent = `${{m.winRateStr}} (${{m.totalTrades}}筆)`;
            if (pfEl) pfEl.textContent = m.profitFactorStr;
            if (expEl) expEl.textContent = m.expectancyStr || '+0';
            if (mddEl) mddEl.textContent = m.maxDrawdownStr;
            if (calmarEl) calmarEl.textContent = m.returnToMddStr || '0.00';
            if (avgwlEl) avgwlEl.textContent = `${{m.avgWinStr}} / ${{m.avgLossStr}}`;
            if (payoffEl) payoffEl.textContent = m.payoffRatioStr;
        }}

        // 執行動態回測並動態連動刷新所有組件
        function executeDynamicBacktest(stratKey, customConfig, silentToast = false) {{
            const bars5m = KLINE_DATA['5m'] || [];
            if (bars5m.length === 0) return;

            const t0 = performance.now();
            const res = runFastBacktest(bars5m, customConfig);
            const t1 = performance.now();
            const elapsedMs = Math.round((t1 - t0) * 10) / 10;

            STRATEGIES_DATA[stratKey].displayName = `布林_ATR基準 (${{customConfig.atr_length}}, ${{customConfig.atr_sig_length}})`;
            STRATEGIES_DATA[stratKey].metrics = res.metrics;
            STRATEGIES_DATA[stratKey].trades = res.trades;
            STRATEGIES_DATA[stratKey].cumulative_pnl = res.cumulative_pnl;
            STRATEGIES_DATA[stratKey].markers = res.markers;

            updateKpiCard(stratKey);

            if (multiCompareChart) {{
                multiCompareChart.initData();
                multiCompareChart.draw();
            }}

            if (currentStrategyKey === stratKey) {{
                renderKeyMetrics();
                renderTradesTable();
                renderQuantAnalytics();
                if (tradeEquityChart) {{
                    tradeEquityChart.setTrades();
                    tradeEquityChart.draw();
                }}
                updateBaselineData();
                const curBars = KLINE_DATA[currentInterval] || [];
                updateExitOverlay(curBars);
                if (showMarkers) {{
                    applyMarkersToChart(alignMarkersToBars(res.markers, curBars));
                }}
            }}

            if (customConfig.bb_length || customConfig.bb_mult) {{
                currentBBConfig.length = Number(customConfig.bb_length) || currentBBConfig.length;
                currentBBConfig.mult = Number(customConfig.bb_mult) || currentBBConfig.mult;
                updateBollingerBands(KLINE_DATA[currentInterval] || []);
            }}

            if (activeModalStrat === stratKey) {{
                updateModalPreviewBox(res.metrics, elapsedMs);
            }}

            if (!silentToast) {{
                showToast(`回測完成！耗時 ${{elapsedMs}} ms ｜ ${{STRATEGIES_DATA[stratKey].displayName}} 淨利: ${{res.metrics.totalNetProfitStr}} TWD ｜ 勝率: ${{res.metrics.winRateStr}} ｜ PF: ${{res.metrics.profitFactorStr}}`, 'success');
            }}
        }}

        // 同步主畫面回測區間與初始資金快捷控制列
        function syncQuickBacktestBar(stratKey) {{
            const cfg = strategyConfigs[stratKey || 'atr'];
            if (!cfg) return;
            const qStart = document.getElementById('quick-start-date');
            const qEnd = document.getElementById('quick-end-date');
            const qCap = document.getElementById('quick-initial-capital');
            if (qStart) qStart.value = cfg.start_date || EARLIEST_BAR_DATE;
            if (qEnd) qEnd.value = cfg.end_date || LATEST_BAR_DATE;
            if (qCap) qCap.value = Math.round(Number(cfg.initial_capital) || 105150);
        }}

        // 一鍵套用最佳化參數組合或恢復基準配置
        function applyPresetProfile(profileName) {{
            if (profileName === 'best_margin') {{
                const curStart = strategyConfigs.atr.start_date || EARLIEST_BAR_DATE;
                const curEnd = strategyConfigs.atr.end_date || LATEST_BAR_DATE;
                const curCap = Number(strategyConfigs.atr.initial_capital) || 105150.0;
                strategyConfigs.atr = {{
                    strategy_name: '布林_ATR基準',
                    exit_mode: 'atr',
                    bb_length: 30,
                    bb_mult: 1.80,
                    pullback_ratio: 0.40,
                    atr_length: 14,
                    atr_sig_length: 4,
                    initial_capital: curCap,
                    start_date: curStart,
                    end_date: curEnd,
                    min_margin_ratio: 1.30,
                    max_leverage: 8.0,
                    commission: 18.0,
                    slippage: 0.0,
                    position_sizing_mode: 'margin_ratio_130'
                }};
                executeDynamicBacktest('atr', strategyConfigs.atr, true);
                populateModalInputs('atr');
                const atrPnl = STRATEGIES_DATA.atr ? STRATEGIES_DATA.atr.metrics.totalNetProfitStr : '';
                showToast(`已一鍵套用【布林_ATR基準最佳化參數組合 (基準A：130%維持率 + 動態浮動槓桿)】！ATR 淨利 ${{atrPnl}} TWD`, 'success');
            }} else {{
                strategyConfigs.atr = JSON.parse(JSON.stringify(defaultStrategyConfigs.atr));
                executeDynamicBacktest('atr', strategyConfigs.atr, true);
                populateModalInputs('atr');
                showToast('已恢復布林_ATR基準策略為【初始基準參數 (基準A：130%維持率)】', 'info');
            }}
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
            if (timeEl && elapsedMs !== undefined) timeEl.textContent = `運算耗時: ${{elapsedMs}} ms`;
        }}

        function openParamsModal(stratKey) {{
            activeModalStrat = 'atr';
            const modal = document.getElementById('params-modal-overlay');
            if (!modal) return;
            modal.classList.add('open');

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

            const panelAtr = document.getElementById('panel-exit-atr');
            if (panelAtr) panelAtr.style.display = 'grid';

            setFieldSync('bb-len', cfg.bb_length);
            setFieldSync('bb-mult', cfg.bb_mult);
            setFieldSync('pullback', cfg.pullback_ratio);

            const selSizing = document.getElementById('sel-sizing-mode');
            const badgeSizing = document.getElementById('val-badge-sizing');
            const modeVal = (cfg.position_sizing_mode === 'margin') ? 'margin_ratio_130' : (cfg.position_sizing_mode || 'margin_ratio_130');
            if (selSizing) {{
                selSizing.value = modeVal;
            }}
            if (badgeSizing) {{
                badgeSizing.textContent = modeVal === 'margin_ratio_130'
                    ? '基準 A (維持率控管)'
                    : (modeVal === 'fixed_one_lot' ? '固定 1 口' : '名目槓桿上限');
            }}

            setFieldSync('atr-len', cfg.atr_length);
            setFieldSync('atr-sig', cfg.atr_sig_length);

            const mStart = document.getElementById('modal-start-date');
            const mEnd = document.getElementById('modal-end-date');
            const bDate = document.getElementById('val-badge-date-range');
            const sDate = cfg.start_date || EARLIEST_BAR_DATE;
            const eDate = cfg.end_date || LATEST_BAR_DATE;
            if (mStart) mStart.value = sDate;
            if (mEnd) mEnd.value = eDate;
            if (bDate) bDate.textContent = `${{sDate}} ~ ${{eDate}}`;

            setFieldSync('initial-capital', Math.round(Number(cfg.initial_capital) || 105150));

            const ratioPct = Math.round((Number(cfg.min_margin_ratio) || 1.30) * 100);
            setFieldSync('margin-ratio', ratioPct);
            setFieldSync('leverage', cfg.max_leverage);
            document.getElementById('num-commission').value = cfg.commission;
            document.getElementById('val-badge-commission').textContent = `${{cfg.commission}} 元`;
            setFieldSync('slippage', cfg.slippage);

            syncQuickBacktestBar(stratKey);

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
                else if (idPrefix === 'initial-capital') badge.textContent = `${{Math.round(Number(val) || 0).toLocaleString('zh-TW')}} 元`;
                else if (idPrefix === 'margin-ratio') {{
                    const reqPerLot = Math.round(35050 * (Number(val) / 100));
                    badge.textContent = `${{val}}% (單口 ${{reqPerLot.toLocaleString('zh-TW')}} 元)`;
                }}
                else if (idPrefix === 'leverage') badge.textContent = `${{val}}x`;
                else if (idPrefix === 'slippage') badge.textContent = `${{val}} 點`;
                else badge.textContent = val;
            }}
        }}

        // 套用主畫面快捷回測區間與初始資金
        function applyQuickBacktestSettings(customStart, customEnd) {{
            const qStart = document.getElementById('quick-start-date');
            const qEnd = document.getElementById('quick-end-date');
            const qCap = document.getElementById('quick-initial-capital');

            let sDate = customStart || (qStart ? qStart.value : EARLIEST_BAR_DATE) || EARLIEST_BAR_DATE;
            let eDate = customEnd || (qEnd ? qEnd.value : LATEST_BAR_DATE) || LATEST_BAR_DATE;
            if (sDate > eDate) {{
                const tmp = sDate;
                sDate = eDate;
                eDate = tmp;
            }}

            let capVal = qCap ? Number(qCap.value) : 105150;
            if (isNaN(capVal) || capVal < 35050) {{
                capVal = 35050;
                showToast('初始資金最低不得低於 1 口原始保證金 (35,050 元)，已自動校正為 35,050 元', 'info');
            }}

            const cfg = strategyConfigs.atr;
            cfg.start_date = sDate;
            cfg.end_date = eDate;
            cfg.initial_capital = capVal;

            populateModalInputs('atr');
            executeDynamicBacktest('atr', cfg, true);

            const m = STRATEGIES_DATA.atr.metrics;
            showToast(
                `已套用回測區間 [${{sDate}} ~ ${{eDate}}] ｜ 初始資金: ${{Math.round(capVal).toLocaleString('zh-TW')}} TWD ｜ 成交 ${{m.totalTrades}} 筆 ｜ 淨利: ${{m.totalNetProfitStr}} TWD`,
                'success'
            );
        }}

        function initParamsModal() {{
            document.querySelectorAll('.params-strat-tab').forEach(tab => {{
                tab.addEventListener('click', () => {{
                    activeModalStrat = 'atr';
                    document.querySelectorAll('.params-strat-tab').forEach(t => t.classList.remove('active'));
                    tab.classList.add('active');
                    populateModalInputs(activeModalStrat);
                }});
            }});

            const fieldKeys = [
                'bb-len', 'bb-mult', 'pullback',
                'atr-len', 'atr-sig', 'initial-capital', 'margin-ratio', 'leverage', 'slippage'
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
                            else if (k === 'initial-capital') badge.textContent = `${{Math.round(Number(range.value) || 0).toLocaleString('zh-TW')}} 元`;
                            else if (k === 'margin-ratio') {{
                                const reqPerLot = Math.round(35050 * (Number(range.value) / 100));
                                badge.textContent = `${{range.value}}% (單口 ${{reqPerLot.toLocaleString('zh-TW')}} 元)`;
                            }}
                            else if (k === 'leverage') badge.textContent = `${{range.value}}x`;
                            else if (k === 'slippage') badge.textContent = `${{range.value}} 點`;
                            else badge.textContent = range.value;
                        }}
                    }});
                    num.addEventListener('input', () => {{
                        range.value = num.value;
                        if (badge) {{
                            if (k === 'pullback') badge.textContent = `${{num.value}} (${{Math.round(num.value * 100)}}% K棒)`;
                            else if (k === 'initial-capital') badge.textContent = `${{Math.round(Number(num.value) || 0).toLocaleString('zh-TW')}} 元`;
                            else if (k === 'margin-ratio') {{
                                const reqPerLot = Math.round(35050 * (Number(num.value) / 100));
                                badge.textContent = `${{num.value}}% (單口 ${{reqPerLot.toLocaleString('zh-TW')}} 元)`;
                            }}
                            else if (k === 'leverage') badge.textContent = `${{num.value}}x`;
                            else if (k === 'slippage') badge.textContent = `${{num.value}} 點`;
                            else badge.textContent = num.value;
                        }}
                    }});
                }}
            }});

            const mStart = document.getElementById('modal-start-date');
            const mEnd = document.getElementById('modal-end-date');
            const bDate = document.getElementById('val-badge-date-range');
            const updateDateBadge = () => {{
                if (mStart && mEnd && bDate) {{
                    bDate.textContent = `${{mStart.value || EARLIEST_BAR_DATE}} ~ ${{mEnd.value || LATEST_BAR_DATE}}`;
                }}
            }};
            if (mStart) mStart.addEventListener('change', updateDateBadge);
            if (mEnd) mEnd.addEventListener('change', updateDateBadge);

            const selSizing = document.getElementById('sel-sizing-mode');
            if (selSizing) {{
                selSizing.addEventListener('change', () => {{
                    const badgeSizing = document.getElementById('val-badge-sizing');
                    if (badgeSizing) {{
                        badgeSizing.textContent = selSizing.value === 'margin_ratio_130'
                            ? '基準 A (維持率控管)'
                            : (selSizing.value === 'fixed_one_lot' ? '固定 1 口' : '名目槓桿上限');
                    }}
                }});
            }}

            const numComm = document.getElementById('num-commission');
            if (numComm) {{
                numComm.addEventListener('input', () => {{
                    const b = document.getElementById('val-badge-commission');
                    if (b) b.textContent = `${{numComm.value}} 元`;
                }});
            }}

            if (btnRunBacktest) {{
                btnRunBacktest.addEventListener('click', () => {{
                    const cfg = strategyConfigs[activeModalStrat];
                    cfg.bb_length = Number(document.getElementById('num-bb-len').value);
                    cfg.bb_mult = Number(document.getElementById('num-bb-mult').value);
                    cfg.pullback_ratio = Number(document.getElementById('num-pullback').value);
                    if (selSizing) cfg.position_sizing_mode = selSizing.value;

                    cfg.atr_length = Number(document.getElementById('num-atr-len').value);
                    cfg.atr_sig_length = Number(document.getElementById('num-atr-sig').value);

                    let sDate = mStart ? mStart.value : EARLIEST_BAR_DATE;
                    let eDate = mEnd ? mEnd.value : LATEST_BAR_DATE;
                    if (sDate > eDate) {{
                        const tmp = sDate;
                        sDate = eDate;
                        eDate = tmp;
                        if (mStart) mStart.value = sDate;
                        if (mEnd) mEnd.value = eDate;
                        updateDateBadge();
                    }}
                    cfg.start_date = sDate;
                    cfg.end_date = eDate;

                    const capInput = document.getElementById('num-initial-capital');
                    cfg.initial_capital = Math.max(35050, Number(capInput ? capInput.value : 105150) || 105150);
                    setFieldSync('initial-capital', cfg.initial_capital);

                    const ratioPctEl = document.getElementById('num-margin-ratio');
                    if (ratioPctEl) cfg.min_margin_ratio = Math.max(1.0, Number(ratioPctEl.value) / 100.0);
                    cfg.max_leverage = Number(document.getElementById('num-leverage').value);
                    cfg.commission = Number(document.getElementById('num-commission').value);
                    cfg.slippage = Number(document.getElementById('num-slippage').value);

                    syncQuickBacktestBar(activeModalStrat);
                    executeDynamicBacktest(activeModalStrat, cfg);
                }});
            }}

            if (btnResetParams) {{
                btnResetParams.addEventListener('click', () => {{
                    strategyConfigs[activeModalStrat] = JSON.parse(JSON.stringify(defaultStrategyConfigs[activeModalStrat]));
                    populateModalInputs(activeModalStrat);
                    executeDynamicBacktest(activeModalStrat, strategyConfigs[activeModalStrat]);
                    showToast(`已重設 [${{STRATEGIES_DATA[activeModalStrat].displayName}}] 為初始預設參數`, 'info');
                }});
            }}

            const btnApplyBest = document.getElementById('btn-apply-best-preset');
            const btnApplyBase = document.getElementById('btn-apply-baseline-preset');
            const btnQuickBest = document.getElementById('btn-quick-best-preset');
            if (btnApplyBest) btnApplyBest.addEventListener('click', () => applyPresetProfile('best_margin'));
            if (btnQuickBest) btnQuickBest.addEventListener('click', () => applyPresetProfile('best_margin'));
            if (btnApplyBase) btnApplyBase.addEventListener('click', () => applyPresetProfile('baseline_notional'));

            // 綁定主畫面回測區間與初始資金快捷控制列事件
            const btnApplyQuick = document.getElementById('btn-apply-quick-backtest');
            if (btnApplyQuick) {{
                btnApplyQuick.addEventListener('click', () => applyQuickBacktestSettings());
            }}
            const qCapInput = document.getElementById('quick-initial-capital');
            if (qCapInput) {{
                qCapInput.addEventListener('keydown', (e) => {{
                    if (e.key === 'Enter') applyQuickBacktestSettings();
                }});
            }}

            const shiftMonthsFromLatest = (monthsBack) => {{
                const parts = LATEST_BAR_DATE.split('-').map(Number);
                const d = new Date(parts[0], parts[1] - 1 - monthsBack, parts[2]);
                const yyyy = d.getFullYear();
                const mm = String(d.getMonth() + 1).padStart(2, '0');
                const dd = String(d.getDate()).padStart(2, '0');
                const target = `${{yyyy}}-${{mm}}-${{dd}}`;
                return target < EARLIEST_BAR_DATE ? EARLIEST_BAR_DATE : target;
            }};

            const btnRangeAll = document.getElementById('btn-range-all');
            const btnRange3m = document.getElementById('btn-range-3m');
            const btnRange1m = document.getElementById('btn-range-1m');
            if (btnRangeAll) {{
                btnRangeAll.addEventListener('click', () => applyQuickBacktestSettings(EARLIEST_BAR_DATE, LATEST_BAR_DATE));
            }}
            if (btnRange3m) {{
                btnRange3m.addEventListener('click', () => applyQuickBacktestSettings(shiftMonthsFromLatest(3), LATEST_BAR_DATE));
            }}
            if (btnRange1m) {{
                btnRange1m.addEventListener('click', () => applyQuickBacktestSettings(shiftMonthsFromLatest(1), LATEST_BAR_DATE));
            }}
        }}

        // 切換活躍策略
        function switchActiveStrategy(stratKey) {{
            if (!STRATEGIES_DATA[stratKey]) return;
            currentStrategyKey = stratKey;
            currentFocusedTradeId = null;
            const banner = document.getElementById('trade-focus-banner');
            if (banner) banner.style.display = 'none';

            document.querySelectorAll('.strat-btn').forEach(btn => {{
                btn.classList.toggle('active', btn.dataset.strat === stratKey);
            }});

            renderKeyMetrics();
            renderQuantAnalytics();

            const bars = KLINE_DATA[currentInterval] || [];
            const strat = getActiveStrategy();
            updateExitOverlay(bars);
            if (showMarkers) {{
                const alignedMarkers = alignMarkersToBars(strat.markers, bars);
                applyMarkersToChart(alignedMarkers);
            }}
            showLastBarData();

            updateBaselineData();
            renderTradesTable();

            if (tradeEquityChart) {{
                tradeEquityChart.setTrades();
                tradeEquityChart.draw();
            }}
            if (multiCompareChart) {{
                multiCompareChart.draw();
            }}

            showToast(`已切換活躍策略為: ${{strat.displayName}}`, 'success');
        }}

        // 切換下半部五個分頁標籤
        function setupTabEvents() {{
            const tabs = [
                {{ tab: tabMultiCompare, wrapper: multiCompareWrapper }},
                {{ tab: tabQuantAnalytics, wrapper: quantAnalyticsWrapper }},
                {{ tab: tabTradeEquity, wrapper: tradeEquityWrapper }},
                {{ tab: tabCumPnl, wrapper: perfChartWrapper }},
                {{ tab: tabTradesTable, wrapper: tradesTableWrapper }}
            ].filter(item => item.tab && item.wrapper);

            tabs.forEach(({{ tab, wrapper }}) => {{
                tab.addEventListener('click', () => {{
                    tabs.forEach(t => {{
                        t.tab.classList.remove('active');
                        t.wrapper.style.display = 'none';
                    }});

                    tab.classList.add('active');
                    wrapper.style.display = 'flex';

                    if (tab === tabMultiCompare && multiCompareChart) {{
                        setTimeout(() => multiCompareChart.resizeAndDraw(), 50);
                    }} else if (tab === tabQuantAnalytics) {{
                        setTimeout(() => renderQuantAnalytics(), 50);
                    }} else if (tab === tabTradeEquity && tradeEquityChart) {{
                        setTimeout(() => tradeEquityChart.resizeAndDraw(), 50);
                    }} else if (tab === tabCumPnl && perfChart) {{
                        setTimeout(() => perfChart.timeScale().fitContent(), 50);
                    }}
                }});
            }});
        }}

        // 可拖曳上下分割條初始化
        function initSplitResizer() {{
            const resizer = document.getElementById('split-resizer');
            const perfSec = document.getElementById('performance-section');
            if (!resizer || !klineSection || !perfSec || !workspaceSplit) return;

            let isDragging = false;
            resizer.addEventListener('mousedown', (e) => {{
                if (isSoloDataMode) return;
                isDragging = true;
                resizer.classList.add('dragging');
                document.body.style.cursor = 'row-resize';
                e.preventDefault();
            }});

            window.addEventListener('mousemove', (e) => {{
                if (!isDragging) return;
                const rect = workspaceSplit.getBoundingClientRect();
                const relY = e.clientY - rect.top;
                const pct = Math.max(22, Math.min(78, (relY / rect.height) * 100));
                klineSection.style.flex = `${{pct}}`;
                perfSec.style.flex = `${{100 - pct}}`;
                if (multiCompareChart) multiCompareChart.resizeAndDraw();
                if (tradeEquityChart) tradeEquityChart.resizeAndDraw();
                drawPnlHistogram();
            }});

            window.addEventListener('mouseup', () => {{
                if (!isDragging) return;
                isDragging = false;
                resizer.classList.remove('dragging');
                document.body.style.cursor = '';
            }});
        }}

        // 全域鍵盤快捷鍵系統
        function initKeyboardShortcuts() {{
            window.addEventListener('keydown', (e) => {{
                const tag = (e.target && e.target.tagName) ? e.target.tagName.toUpperCase() : '';
                if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;

                if (e.key === 'Escape') {{
                    if (paramsModalOverlay && paramsModalOverlay.classList.contains('open')) {{
                        paramsModalOverlay.classList.remove('open');
                    }} else if (currentFocusedTradeId !== null) {{
                        clearTradeFocus();
                    }}
                }} else if (e.code === 'Space') {{
                    e.preventDefault();
                    toggleLayoutMode();
                }} else if (e.key === '1' || e.key === '2' || e.key === '3') {{
                    switchActiveStrategy('atr');
                }} else if (e.key === '[' || e.key === 'ArrowLeft') {{
                    navigateFocusedTrade(-1);
                }} else if (e.key === ']' || e.key === 'ArrowRight') {{
                    navigateFocusedTrade(1);
                }}
            }});
        }}

        // 事件監聽綁定
        function setupEventListeners() {{
            const btnToggleLayout = document.getElementById('btn-toggle-layout');
            if (btnToggleLayout) {{
                btnToggleLayout.addEventListener('click', () => toggleLayoutMode());
            }}

            if (btnToggleBb) {{
                btnToggleBb.addEventListener('click', () => {{
                    showBollingerBands = !showBollingerBands;
                    btnToggleBb.classList.toggle('active', showBollingerBands);
                    if (txtToggleBb) txtToggleBb.textContent = `布林軌道: ${{showBollingerBands ? '顯示' : '隱藏'}}`;
                    const bars = KLINE_DATA[currentInterval] || [];
                    updateBollingerBands(bars);
                    showToast(`布林通道已${{showBollingerBands ? '開啟顯示' : '隱藏'}}`, 'info');
                }});
            }}

            if (btnToggleExitLine) {{
                btnToggleExitLine.addEventListener('click', () => {{
                    showExitOverlay = !showExitOverlay;
                    btnToggleExitLine.classList.toggle('active', showExitOverlay);
                    if (txtToggleExitLine) txtToggleExitLine.textContent = `出場軌道: ${{showExitOverlay ? '顯示' : '隱藏'}}`;
                    const bars = KLINE_DATA[currentInterval] || [];
                    updateExitOverlay(bars);
                    showToast(`出場軌道線已${{showExitOverlay ? '開啟顯示' : '隱藏'}}`, 'info');
                }});
            }}

            if (btnOpenParamsModal) {{
                btnOpenParamsModal.addEventListener('click', () => openParamsModal());
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

            initParamsModal();
            initSplitResizer();
            initKeyboardShortcuts();

            document.querySelectorAll('.strat-btn').forEach(btn => {{
                btn.addEventListener('click', () => {{
                    switchActiveStrategy(btn.dataset.strat);
                }});
            }});

            document.querySelectorAll('.tf-btn').forEach(btn => {{
                btn.addEventListener('click', () => {{
                    document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    loadIntervalData(btn.dataset.interval);
                }});
            }});

            const toggleMarkersBtn = document.getElementById('toggle-markers-btn');
            if (toggleMarkersBtn) {{
                toggleMarkersBtn.addEventListener('click', () => {{
                    showMarkers = !showMarkers;
                    toggleMarkersBtn.classList.toggle('active', showMarkers);
                    toggleMarkersBtn.querySelector('span').textContent = `交易標記: ${{showMarkers ? '顯示' : '隱藏'}}`;
                    const bars = KLINE_DATA[currentInterval] || [];
                    const strat = getActiveStrategy();
                    applyMarkersToChart(showMarkers ? alignMarkersToBars(strat.markers, bars) : []);
                }});
            }}

            const fitBtn = document.getElementById('btn-fit-chart');
            if (fitBtn) {{
                fitBtn.addEventListener('click', () => {{
                    if (klineChart && !isSoloDataMode) klineChart.timeScale().fitContent();
                    if (perfChart) perfChart.timeScale().fitContent();
                    if (multiCompareChart) multiCompareChart.resizeAndDraw();
                    if (tradeEquityChart) tradeEquityChart.resizeAndDraw();
                    drawPnlHistogram();
                }});
            }}

            const resetZoomBtn = document.getElementById('reset-zoom-btn');
            if (resetZoomBtn) {{
                resetZoomBtn.addEventListener('click', () => {{
                    if (klineChart && !isSoloDataMode) klineChart.timeScale().fitContent();
                    if (perfChart) perfChart.timeScale().fitContent();
                }});
            }}

            const toggleCompareModeBtn = document.getElementById('btn-toggle-compare-mode');
            if (toggleCompareModeBtn) {{
                toggleCompareModeBtn.addEventListener('click', () => {{
                    if (multiCompareChart) {{
                        const m = multiCompareChart.toggleMode();
                        setMultiChartMode(m);
                        const labelMap = {{ PNL: '累積淨損益 (TWD)', EQUITY: '帳戶總權益 (TWD)', DRAWDOWN: '水下回撤曲線 (Drawdown)' }};
                        showToast(`已切換視圖模式為: ${{labelMap[m]}}`, 'success');
                    }}
                }});
            }}

            const chkAtr = document.getElementById('chk-atr');
            if (chkAtr) chkAtr.addEventListener('change', (e) => multiCompareChart && multiCompareChart.toggleSeries('atr', e.target.checked));

            window.addEventListener('resize', () => {{
                drawPnlHistogram();
            }});
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

        // WebSocket 客戶端 (本地即時串流與最新資料庫狀態同步)
        function initWebSocketClient() {{
            const badge = document.getElementById('live-status-badge');
            const text = document.getElementById('live-status-text');
            const bars5m = KLINE_DATA['5m'] || [];
            const latestBarStr = bars5m.length > 0 ? (bars5m[bars5m.length - 1].datetime || '').slice(5, 16) : '';

            // 預設先顯示已載入最新歷史與增量資料
            if (badge) badge.className = 'live-badge online';
            if (text) text.textContent = latestBarStr ? `資料已同步 (${{latestBarStr}})` : '資料已同步';

            if (window.location.protocol === 'https:') {{
                return;
            }}

            function connect() {{
                const host = (window.location.hostname && window.location.hostname !== '') ? window.location.hostname : '127.0.0.1';
                const wsUrl = `ws://${{host}}:8000/ws/market`;
                let ws = null;
                try {{
                    ws = new WebSocket(wsUrl);
                }} catch (e) {{
                    return;
                }}

                ws.onopen = () => {{
                    if (badge) badge.className = 'live-badge online';
                    if (text) text.textContent = latestBarStr ? `串流: 已連線 (${{latestBarStr}})` : '串流: 已連線';
                }};

                ws.onmessage = (event) => {{
                    try {{
                        const msg = JSON.parse(event.data);
                        if (msg.type === 'initial_state' && msg.db_summary && msg.db_summary.max_ts) {{
                            const dbMaxShort = String(msg.db_summary.max_ts).slice(5, 16);
                            if (badge) badge.className = 'live-badge online';
                            if (text) text.textContent = `串流: 已連線 (${{dbMaxShort}})`;
                        }} else if (msg.type === 'bar_update' && msg.bar) {{
                            const bar = msg.bar;
                            if (badge) badge.className = 'live-badge online';
                            if (text) text.textContent = `串流: ${{bar.close}} (${{String(bar.datetime || '').slice(11, 19)}})`;
                            if (candlestickSeries && currentInterval === '1m' && bar.time) {{
                                candlestickSeries.update({{
                                    time: bar.time,
                                    open: bar.open,
                                    high: bar.high,
                                    low: bar.low,
                                    close: bar.close
                                }});
                            }}
                        }}
                    }} catch (err) {{
                        // 忽略非 JSON 訊號
                    }}
                }};

                ws.onclose = () => {{
                    if (badge) badge.className = 'live-badge online';
                    if (text) text.textContent = latestBarStr ? `最新資料 (${{latestBarStr}})` : '最新資料已載入';
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
            ['atr'].forEach(k => updateKpiCard(k));
            renderKeyMetrics();
            renderQuantAnalytics();
            initKlineChart();
            initPerfChart();

            if (multiCanvas && multiViewport && multiTooltip) {{
                multiCompareChart = new MultiStrategyCompareChart(multiCanvas, multiViewport, multiTooltip);
                window.multiCompareChart = multiCompareChart;
                setTimeout(() => multiCompareChart.resizeAndDraw(), 100);
            }}

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


DASHBOARD_HISTORY_START: str = "2024-01-01"  # 載入 SQLite 內所有已回補與每日同步之完整歷史區間 (目前自 2026-06-01 起)


def generate_dashboard(
    db_path: str = "data/market_data.db",
    output_html_path: str = "kline_dashboard.html",
    since: str = DASHBOARD_HISTORY_START
) -> str:
    """
    執行多週期聚合、三大策略回測運算並產生支援直觀對比之 HTML 儀表板檔案。
    資料範圍含歷史回補區段，自 since 起至資料庫最新一筆。

    參數:
        db_path (str): SQLite 資料庫路徑。
        output_html_path (str): 目標 HTML 輸出路徑。
        since (str): 資料時間下界 (含)，預設 DASHBOARD_HISTORY_START。

    返回:
        str: 產出之 HTML 絕對路徑。
    """
    logger.info(f"開始從 SQLite 資料庫聚合全時序 K 線 (含歷史回補，自 {since} 起): {db_path}...")
    service = KlineService(db_path=db_path, include_history=True, since=since)

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

    # 執行布林_ATR基準策略回測運算
    logger.info("開始執行策略回測運算...")
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
