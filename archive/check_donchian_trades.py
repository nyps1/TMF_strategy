import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pandas as pd
from services.storage import KlineService
from services.backtest import BacktestEngine, BacktestConfig

service = KlineService("data/market_data.db")
bars_5m = service.get_kbars("5m")
df = pd.DataFrame(bars_5m)
df["ts"] = pd.to_datetime(df["datetime"])

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

print(f"Python 回測交易總數: {len(m3.trades)}")
print("最後 5 筆交易:")
for t in m3.trades[-5:]:
    print(f"#{t.trade_id} {t.side.value} 進場: {t.entry_time} @ {t.entry_price} 出場: {t.exit_time} @ {t.exit_price} ({t.exit_reason.value}) 淨利: {t.net_pnl}")
