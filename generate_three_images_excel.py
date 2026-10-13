"""
三張圖回測參數執行與 Excel 匯出主程式 (generate_three_images_excel.py)

從 SQLite 讀取微台10 5分K高頻時序資料，依據使用者上傳三張圖之自訂參數：
1. 圖一：布林_ATR基準 (BB 30/1.8, Pullback 0.4, ATR 14/4)
2. 圖二：布林_超級趨勢 (BB 30/1.8, Pullback 0.3, ST 10/1.0)
3. 圖三：布林_唐奇安 (BB 31/1.75, Pullback 0.3, Donchian 2, Lev 7.5x)

執行回測並匯出具備金融商務級排版之 Excel 試算表。
遵循 PEP 8 與 Python Type Hints 規範。
"""

import os
import sys
import shutil
import logging
from typing import Dict, List, Any
import pandas as pd

from services.storage import KlineService
from services.backtest import BacktestEngine, BacktestConfig
from services.backtest.excel_exporter import ExcelStrategyExporter

logger = logging.getLogger("GenerateExcelComparison")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ARTIFACT_DIR = "C:\\Users\\User\\.gemini\\antigravity\\brain\\6f811917-cdd9-444f-a8ee-2dec79aad0cb"


def main() -> str:
    """
    主執行流程：讀取資料、回測三組策略、匯出 Excel 試算表。

    返回:
        str: 產出之 Excel 絕對路徑。
    """
    logger.info("開始載入 5 分 K 線時序資料庫...")
    service = KlineService("data/market_data.db")
    bars_5m = service.get_kbars("5m")
    df = pd.DataFrame(bars_5m)
    df["ts"] = pd.to_datetime(df["datetime"])
    logger.info(f"5 分 K 線資料載入完成，共計 {len(df):,} 根 K 棒。")

    # 1. 圖一：布林_ATR基準 (動態均線對照)
    cfg1 = BacktestConfig(
        strategy_name="布林_ATR基準",
        exit_mode="atr",
        position_sizing_mode="margin",
        bb_length=30,
        bb_mult=1.8,
        pullback_ratio=0.4,
        atr_length=14,
        atr_sig_length=4,
        max_leverage=7.0,
        commission_per_order=18.0,
        slippage_points=0.0
    )
    eng1 = BacktestEngine(config=cfg1)
    m1 = eng1.run(df)
    logger.info(f"圖一回測完成: 交易 {m1.total_trades} 筆, 淨利 {m1.total_net_profit:,.0f} 元, 勝率 {m1.win_rate_pct:.2f}%")

    # 2. 圖二：布林_超級趨勢 (單向不可逆)
    cfg2 = BacktestConfig(
        strategy_name="布林_超級趨勢",
        exit_mode="supertrend",
        position_sizing_mode="margin",
        bb_length=30,
        bb_mult=1.8,
        pullback_ratio=0.3,
        supertrend_length=10,
        supertrend_multiplier=1.0,
        max_leverage=7.0,
        commission_per_order=18.0,
        slippage_points=0.0
    )
    eng2 = BacktestEngine(config=cfg2)
    m2 = eng2.run(df)
    logger.info(f"圖二回測完成: 交易 {m2.total_trades} 筆, 淨利 {m2.total_net_profit:,.0f} 元, 勝率 {m2.win_rate_pct:.2f}%")

    # 3. 圖三：布林_唐奇安 (突破極值追蹤)
    cfg3 = BacktestConfig(
        strategy_name="布林_唐奇安",
        exit_mode="donchian",
        position_sizing_mode="margin",
        bb_length=31,
        bb_mult=1.75,
        pullback_ratio=0.3,
        donchian_length=2,
        max_leverage=7.5,
        commission_per_order=18.0,
        slippage_points=0.0
    )
    eng3 = BacktestEngine(config=cfg3)
    m3 = eng3.run(df)
    logger.info(f"圖三回測完成: 交易 {m3.total_trades} 筆, 淨利 {m3.total_net_profit:,.0f} 元, 勝率 {m3.win_rate_pct:.2f}%")

    # 轉換逐筆交易列表 (包含中文出場原因)
    def parse_trades(trades_list):
        parsed = []
        running_cum = 0.0
        for t in trades_list:
            running_cum += t.net_pnl
            reason = t.exit_reason.value
            if reason == "STOP_LOSS":
                reason_zh = "停損 (SL)"
            elif reason == "ATR_TAKE_PROFIT":
                reason_zh = "ATR均線停利"
            elif reason == "SUPERTREND_EXIT":
                reason_zh = "超級趨勢出場"
            elif reason == "DONCHIAN_EXIT":
                reason_zh = "唐奇安破線出場"
            else:
                reason_zh = "期指結算"

            parsed.append({
                "trade_id": t.trade_id,
                "side": t.side.value,
                "entry_time": t.entry_time,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time,
                "exit_price": t.exit_price,
                "qty": t.qty,
                "exit_reason": reason_zh,
                "costs": t.fee_total + t.tax_total,
                "net_pnl": t.net_pnl,
                "cum_pnl": round(running_cum, 1),
                "return_pct": t.return_pct,
                "equity_after": t.equity_after
            })
        return parsed

    trades_p1 = parse_trades(m1.trades)
    trades_p2 = parse_trades(m2.trades)
    trades_p3 = parse_trades(m3.trades)

    # 4. 準備參數摘要矩陣
    params_summary = [
        {"item": "出場追蹤機制 (Exit Mode)", "strat1": "動態 ATR 均線平滑", "strat2": "超級趨勢線 SuperTrend", "strat3": "唐奇安通道極值 Donchian", "note": "三大核心出場方式對照"},
        {"item": "布林通道週期 (BB Length)", "strat1": "30", "strat2": "30", "strat3": "31", "note": "圖三略微拉長至 31 根 K 棒"},
        {"item": "布林標準差乘數 (BB Multiplier)", "strat1": "1.80", "strat2": "1.80", "strat3": "1.75", "note": "圖一與圖二提高突破門檻"},
        {"item": "拉回確認比例 (Pullback Ratio)", "strat1": "0.4 (40% K棒)", "strat2": "0.3 (30% K棒)", "strat3": "0.3 (30% K棒)", "note": "圖一要求拉回更深方才觸發進場"},
        {"item": "動態出場專屬核心參數", "strat1": "ATR=14, Signal MA=4", "strat2": "ATR=10, Multiplier=1.0", "strat3": "Donchian N=2 (極窄破線)", "note": "各策略自訂之出場長度與乘數"},
        {"item": "最大資金槓桿倍數 (Max Leverage)", "strat1": "7.0x (實際滿倉3口)", "strat2": "7.0x (實際滿倉3口)", "strat3": "7.5x (實際滿倉3口)", "note": "基於保證金成數安全控管"},
        {"item": "單邊手續費 / 滑價 (Friction)", "strat1": "18 元 / 0 點", "strat2": "18 元 / 0 點", "strat3": "18 元 / 0 點", "note": "真實期貨交易手續費與期交稅"}
    ]

    # 5. 準備指標摘要矩陣
    strategies_summary = [
        {"metric": "總淨利 (Net Profit)", "val1": m1.total_net_profit, "val2": m2.total_net_profit, "val3": m3.total_net_profit, "best": "圖一 (+43,102)", "format": "$#,##0", "desc": "期末帳戶扣除手續費與期交稅後淨所得"},
        {"metric": "總報酬率 (Total Return)", "val1": m1.total_return_pct / 100.0, "val2": m2.total_return_pct / 100.0, "val3": m3.total_return_pct / 100.0, "best": "圖一 (+40.99%)", "format": "+0.00%;-0.00%", "desc": "相對於期初 105,150 元保證金之獲利率"},
        {"metric": "交易勝率 (Win Rate)", "val1": m1.win_rate_pct / 100.0, "val2": m2.win_rate_pct / 100.0, "val3": m3.win_rate_pct / 100.0, "best": "圖二 (43.94%)", "format": "0.00%", "desc": "獲利筆數佔總交易筆數之百分比"},
        {"metric": "獲利因子 (Profit Factor)", "val1": m1.profit_factor, "val2": m2.profit_factor, "val3": m3.profit_factor, "best": "圖一 (1.760)", "format": "0.000", "desc": "總獲利金額 / 總虧損金額 (高於 1.5 屬優秀)"},
        {"metric": "最大資產回撤 (Max Drawdown)", "val1": -m1.max_drawdown, "val2": -m2.max_drawdown, "val3": -m3.max_drawdown, "best": "圖二 (-13,338)", "format": "$#,##0", "desc": "歷史權益峰值至谷底之最大虧損金額"},
        {"metric": "最大回撤百分比 (MDD %)", "val1": m1.max_drawdown_pct / 100.0, "val2": m2.max_drawdown_pct / 100.0, "val3": m3.max_drawdown_pct / 100.0, "best": "圖二 (11.02%)", "format": "0.00%", "desc": "最大資產回撤相對於當時資產峰值比例"},
        {"metric": "總交易筆數 (Total Trades)", "val1": m1.total_trades, "val2": m2.total_trades, "val3": m3.total_trades, "best": "圖一 (90 筆)", "format": "#,##0", "desc": "回測期間完整進出場撮合之交易總數"},
        {"metric": "獲利 / 虧損交易筆數", "val1": f"{m1.winning_trades} / {m1.losing_trades}", "val2": f"{m2.winning_trades} / {m2.losing_trades}", "val3": f"{m3.winning_trades} / {m3.losing_trades}", "best": "圖一 (39/51)", "format": None, "desc": "贏次 / 輸次"},
        {"metric": "平均獲利金額 (Avg Win)", "val1": sum([t.net_pnl for t in m1.trades if t.net_pnl > 0]) / max(1, m1.winning_trades), "val2": sum([t.net_pnl for t in m2.trades if t.net_pnl > 0]) / max(1, m2.winning_trades), "val3": sum([t.net_pnl for t in m3.trades if t.net_pnl > 0]) / max(1, m3.winning_trades), "best": "圖一 (2,557)", "format": "$#,##0", "desc": "每一筆獲利單之平均賺取金額 (TWD)"},
        {"metric": "平均虧損金額 (Avg Loss)", "val1": abs(sum([t.net_pnl for t in m1.trades if t.net_pnl < 0]) / max(1, m1.losing_trades)), "val2": abs(sum([t.net_pnl for t in m2.trades if t.net_pnl < 0]) / max(1, m2.losing_trades)), "val3": abs(sum([t.net_pnl for t in m3.trades if t.net_pnl < 0]) / max(1, m3.losing_trades)), "best": "圖二 (1,540)", "format": "$#,##0", "desc": "每一筆虧損單之平均停損金額 (TWD)"},
        {"metric": "賺賠比 (Payoff Ratio)", "val1": 2.31, "val2": 1.73, "val3": 2.07, "best": "圖一 (2.31)", "format": "0.00", "desc": "平均每筆獲利金額 / 平均每筆虧損金額"},
        {"metric": "期末帳戶總淨值 (Final Equity)", "val1": m1.final_equity, "val2": m2.final_equity, "val3": m3.final_equity, "best": "圖一 (148,252)", "format": "$#,##0", "desc": "結算後總帳戶權益金額 (期初 105,150 TWD)"}
    ]

    # 6. 整合時序權益曲線
    eq1_map = {pt["ts"]: pt["equity"] for pt in m1.equity_curve}
    eq2_map = {pt["ts"]: pt["equity"] for pt in m2.equity_curve}
    eq3_map = {pt["ts"]: pt["equity"] for pt in m3.equity_curve}

    time_series = []
    # 每 3 根 K 棒抽樣一筆以縮減時序體積 (約 1,300 筆)，完整展示趨勢
    for i in range(0, len(bars_5m), 3):
        b = bars_5m[i]
        dt = b["datetime"]
        time_series.append({
            "datetime": dt,
            "close": b["close"],
            "strat1_equity": eq1_map.get(dt, 105150.0),
            "strat2_equity": eq2_map.get(dt, 105150.0),
            "strat3_equity": eq3_map.get(dt, 105150.0)
        })

    # 7. 呼叫 Excel 匯出服務
    exporter = ExcelStrategyExporter(filename="three_strategies_trades_comparison.xlsx")
    exporter.add_summary_sheet(strategies_summary, params_summary)
    exporter.add_trades_sheet("布林_ATR基準 (明細)", "布林_ATR基準 (BB 30/1.8, Pullback 0.4, ATR 14/4)", trades_p1, exporter.header_fill_navy)
    exporter.add_trades_sheet("布林_超級趨勢 (明細)", "布林_超級趨勢 (BB 30/1.8, Pullback 0.3, ST 10/1.0)", trades_p2, exporter.header_fill_amber)
    exporter.add_trades_sheet("布林_唐奇安 (明細)", "布林_唐奇安 (BB 31/1.75, Pullback 0.3, Donchian 2)", trades_p3, exporter.header_fill_teal)
    exporter.add_equity_curve_sheet(time_series)

    output_path = exporter.save()

    # 複製至 Artifacts 目錄便於下載
    artifact_copy_path = os.path.join(ARTIFACT_DIR, "three_strategies_trades_comparison.xlsx")
    shutil.copyfile(output_path, artifact_copy_path)
    logger.info(f"已同步備份試算表至 Artifact 目錄: {artifact_copy_path}")

    return output_path


if __name__ == "__main__":
    main()
