"""
微型台指期動態布林突破策略 - CLI 回測執行主程式 (run_backtest.py)

提供命令列介面執行歷史回測，支援 Shioaji API 線上下載與本機擬真全日盤資料運算。
產出量化指標報表、CSV 交易清單以及 HTML 互動圖表。
遵循 PEP 8 與 Python Type Hints 規範。
"""

import os
import sys
import argparse
import logging
from typing import Optional
from dotenv import load_dotenv
import pandas as pd

from services.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestReporter,
    DataLoader,
)

# 設定日誌格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    """
    解析命令列輸入參數。

    返回:
        argparse.Namespace: 解析完成之參數物件。
    """
    parser = argparse.ArgumentParser(description="微型台指期動態布林突破策略回測工具")
    parser.add_argument("--source", type=str, default="auto", choices=["shioaji", "simulated", "auto"],
                        help="資料來源：shioaji (連線 API 下載)、simulated (離線全日盤擬真)、auto (優先連線 Shioaji，失敗則降級擬真)")
    parser.add_argument("--symbol", type=str, default="MXF",
                        help="回測合約代碼 (MXF: 小台指代理，TMF: 微台指真實合約)")
    parser.add_argument("--start", type=str, default="2024-01-01", help="回測起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2024-06-30", help="回測結束日期 (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=105150.0, help="期初本金 (新台幣)")
    parser.add_argument("--margin", type=float, default=35050.0, help="單口原始保證金 (新台幣)")
    parser.add_argument("--leverage", type=float, default=7.0, help="最高允許槓桿倍數")
    parser.add_argument("--bb-length", type=int, default=30, help="布林通道長度 (預設 30)")
    parser.add_argument("--bb-mult", type=float, default=1.75, help="布林通道標準差乘數 (預設 1.75)")
    parser.add_argument("--atr-length", type=int, default=14, help="ATR 週期 (預設 14)")
    parser.add_argument("--atr-sig-length", type=int, default=5, help="ATR Signal 週期 (預設 5)")
    parser.add_argument("--fee", type=float, default=18.0, help="單口單邊手續費 (新台幣，預設 18 元)")
    parser.add_argument("--tax-rate", type=float, default=0.00002, help="期交稅率 (預設 0.00002)")
    parser.add_argument("--slippage", type=float, default=0.0, help="單邊滑價點數 (預設 0.0 點)")
    parser.add_argument("--output-html", type=str, default="backtest_report.html", help="HTML 圖表輸出路徑")
    parser.add_argument("--output-csv", type=str, default="trades.csv", help="CSV 交易明細輸出路徑")
    return parser.parse_args()


def run_cli_backtest() -> None:
    """CLI 回測執行主入口函數。"""
    load_dotenv()
    args = parse_arguments()

    print("=======================================================")
    print("  微型台指期動態最大槓桿布林突破策略回測系統 (5分K)")
    print("=======================================================")
    print(f"  回測區間 : {args.start} 至 {args.end}")
    print(f"  標的代碼 : {args.symbol} (每點乘數: 10 元)")
    print(f"  初始資金 : {args.capital:,.0f} 元 | 單口保證金: {args.margin:,.0f} 元 | 槓桿上限: {args.leverage}x")
    print(f"  資料模式 : {args.source}")
    print("-------------------------------------------------------")

    data_loader = DataLoader(cache_dir="data/cache")
    kbars_df = pd.DataFrame()

    # 資料取得邏輯 (支援 Fail-Safe 與優雅降級)
    if args.source in ["shioaji", "auto"]:
        api_key = os.environ.get("SHIOAJI_API_KEY")
        secret_key = os.environ.get("SHIOAJI_SECRET_KEY")

        if not api_key or not secret_key:
            if args.source == "shioaji":
                print("Status: ERROR | Root Cause: 找不到 SHIOAJI_API_KEY 或 SHIOAJI_SECRET_KEY | Suggested Fix: 請確認 .env 檔案中的金鑰設定。")
                sys.exit(1)
            else:
                logger.warning("未偵測到完整 Shioaji 金鑰，自動降級為擬真全日盤數據回測模式。")
                kbars_df = data_loader.generate_simulated_kbars(start_date=args.start, end_date=args.end)
        else:
            try:
                import shioaji as sj
                # 優先以 simulation=True 模式連線 (使用者提供之金鑰具備模擬權限)
                api = sj.Shioaji(simulation=True)
                logger.info("正在連線登入永豐 Shioaji API 伺服器 (Simulation 模式)...")
                api.login(api_key, secret_key)
                logger.info("Shioaji 登入成功，準備檢索合約...")

                fut_group = getattr(api.Contracts.Futures, args.symbol, None)
                if fut_group:
                    contract_codes = [c.code for c in fut_group]
                    continuous_code = f"{args.symbol}R1"
                    if continuous_code in contract_codes:
                        logger.info(f"檢測到連續近月合約代碼: {continuous_code}，直接透過 API 獲取連續 K 線時序...")
                        target_contract = fut_group[continuous_code]
                        kbars_df = data_loader.fetch_from_shioaji(
                            api=api,
                            contract=target_contract,
                            start_date=args.start,
                            end_date=args.end,
                            resample_5min=True
                        )
                    else:
                        monthly_dfs = {}
                        for contract in fut_group:
                            if len(contract.code) >= 8 and contract.code.startswith(args.symbol):
                                month_str = contract.code[len(args.symbol):len(args.symbol)+6]
                                df_c = data_loader.fetch_from_shioaji(api, contract, args.start, args.end, resample_5min=True)
                                if not df_c.empty:
                                    monthly_dfs[month_str] = df_c

                        if monthly_dfs:
                            logger.info(f"成功下載 {len(monthly_dfs)} 個月份合約資料，正在進行結算日換月拼接...")
                            kbars_df = data_loader.splice_continuous_front_month(monthly_dfs)
                        else:
                            logger.warning("Shioaji 未回傳指定合約區間的 K 線數據。")
                else:
                    logger.warning(f"在 Shioaji 合約列表中未找到合約群組: {args.symbol}")

                api.logout()

            except Exception as e:
                logger.error(f"Status: ERROR | Root Cause: Shioaji API 連線或下載失敗 ({e}) | Suggested Fix: 檢查網路連線或 API 流量限制。")
                if args.source == "shioaji":
                    sys.exit(1)
                else:
                    logger.info("自動切換為全日盤擬真歷史數據進行回測運算...")
                    kbars_df = data_loader.generate_simulated_kbars(start_date=args.start, end_date=args.end)
    else:
        logger.info("使用全日盤擬真歷史數據生成模式...")
        kbars_df = data_loader.generate_simulated_kbars(start_date=args.start, end_date=args.end)

    if kbars_df.empty:
        logger.info("未取得有效 K 線，啟動預設全日盤時序資料生成器...")
        kbars_df = data_loader.generate_simulated_kbars(start_date=args.start, end_date=args.end)

    print(f"  取得 K 線總筆數 : {len(kbars_df):,} 根 5 分 K (涵蓋全日盤交易時段)")
    print("-------------------------------------------------------")

    # 執行回測運算
    config = BacktestConfig(
        initial_capital=args.capital,
        point_value=10.0,  # 微台指每點 10 元
        margin_req=args.margin,
        max_leverage=args.leverage,
        bb_length=args.bb_length,
        bb_mult=args.bb_mult,
        atr_length=args.atr_length,
        atr_sig_length=args.atr_sig_length,
        commission_per_order=args.fee,
        tax_rate=args.tax_rate,
        slippage_points=args.slippage
    )

    engine = BacktestEngine(config=config)
    print("  正在進行逐根事件驅動撮合運算...")
    metrics = engine.run(kbars_df)

    # 產出報告
    reporter = BacktestReporter(metrics)
    reporter.print_terminal_summary()
    reporter.export_trades_csv(args.output_csv)
    html_abs_path = reporter.generate_html_chart(args.output_html)
    print(f"  [完成] 互動式 HTML 報表已產出至: {html_abs_path}")
    print(f"  [完成] 交易明細 CSV 已儲存至    : {os.path.abspath(args.output_csv)}")
    print("=======================================================\n")


if __name__ == "__main__":
    run_cli_backtest()
