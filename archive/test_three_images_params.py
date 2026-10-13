import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pandas as pd
from services.storage import KlineService
from services.backtest import BacktestEngine, BacktestConfig

def test_three_images():
    service = KlineService("data/market_data.db")
    bars_5m = service.get_kbars("5m")
    df = pd.DataFrame(bars_5m)
    df["ts"] = pd.to_datetime(df["datetime"])

    print(f"5分K線資料筆數: {len(df):,}")

    # 圖一：布林_ATR基準
    # BB: 30, 1.8, Pullback: 0.4, ATR: 14, Signal MA: 4, Max Lev: 7, Comm: 18, Slip: 0
    cfg1 = BacktestConfig(
        strategy_name="圖一_布林_ATR基準",
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
    print("\n--- 圖一：布林_ATR基準 ---")
    print(f"總淨利: {m1.total_net_profit:,.2f} (預期 +43,202)")
    print(f"勝率: {m1.win_rate_pct:.2f}% (預期 43.33%)")
    print(f"獲利因子: {m1.profit_factor:.3f} (預期 1.767)")
    print(f"總交易: {m1.total_trades} (預期 90 筆)")
    print(f"最大回撤: {m1.max_drawdown:,.2f} (預期 -13,654)")

    # 圖二：布林_超級趨勢
    # BB: 30, 1.8, Pullback: 0.3, ST Length: 10, ST Mult: 1.0, Max Lev: 7, Comm: 18, Slip: 0
    cfg2 = BacktestConfig(
        strategy_name="圖二_布林_超級趨勢",
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
    print("\n--- 圖二：布林_超級趨勢 ---")
    print(f"總淨利: {m2.total_net_profit:,.2f} (預期 +17,193)")
    print(f"勝率: {m2.win_rate_pct:.2f}% (預期 43.94%)")
    print(f"獲利因子: {m2.profit_factor:.3f} (預期 1.353)")
    print(f"總交易: {m2.total_trades} (預期 66 筆)")
    print(f"最大回撤: {m2.max_drawdown:,.2f} (預期 -13,316)")

    # 圖三：布林_唐奇安
    # BB: 31, 1.75, Pullback: 0.3, Donchian Length: 2, Max Lev: 7.5, Comm: 18, Slip: 0
    cfg3 = BacktestConfig(
        strategy_name="圖三_布林_唐奇安",
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
    print("\n--- 圖三：布林_唐奇安 ---")
    print(f"總淨利: {m3.total_net_profit:,.2f} (預期 +20,839)")
    print(f"勝率: {m3.win_rate_pct:.2f}% (預期 40.58%)")
    print(f"獲利因子: {m3.profit_factor:.3f} (預期 1.410)")
    print(f"總交易: {m3.total_trades} (預期 69 筆)")
    print(f"最大回撤: {m3.max_drawdown:,.2f} (預期 -13,468)")

if __name__ == "__main__":
    test_three_images()
