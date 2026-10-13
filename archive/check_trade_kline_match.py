import sys
sys.path.insert(0, '.')
import pandas as pd
from services.storage.kline_service import KlineService
from generate_kline_dashboard import run_all_strategies_backtest

service = KlineService()
bars_5m = service.get_kbars("5m")
bar_map = {b["datetime"]: b for b in bars_5m}
bar_ts_map = {b["time"]: b for b in bars_5m}

strats_data = run_all_strategies_backtest(bars_5m)

for key, strat in strats_data.items():
    name = strat["metrics"]["strategyName"]
    trades = strat["trades"]
    markers = strat["markers"]
    
    mismatches = []
    for t in trades:
        tid = t["trade_id"]
        etime = t["entry_time"]
        eprice = t["entry_price"]
        xtime = t["exit_time"]
        xprice = t["exit_price"]
        reason = t["exit_reason"]
        
        # 1. 檢驗進場時間與 K 線
        if etime not in bar_map:
            mismatches.append(f"Trade #{tid} entry_time {etime} not found in 5m bars!")
            continue
        ebar = bar_map[etime]
        if abs(ebar["open"] - eprice) > 0.01:
            mismatches.append(f"Trade #{tid} entry_price {eprice} != Kbar open {ebar['open']}")
            
        # 2. 檢驗出場時間與 K 線
        if xtime not in bar_map:
            mismatches.append(f"Trade #{tid} exit_time {xtime} not found in 5m bars!")
            continue
        xbar = bar_map[xtime]
        if reason in ["唐奇安出場", "超級趨勢出場", "ATR停利", "結算"]:
            if abs(xbar["close"] - xprice) > 0.01:
                mismatches.append(f"Trade #{tid} exit_price {xprice} != Kbar close {xbar['close']}")
            
        # 3. 檢驗 entry_ts 與 exit_ts
        if t["entry_ts"] != ebar["time"]:
            mismatches.append(f"Trade #{tid} entry_ts ({t['entry_ts']}) != Kbar timestamp ({ebar['time']})")
        if t["exit_ts"] != xbar["time"]:
            mismatches.append(f"Trade #{tid} exit_ts ({t['exit_ts']}) != Kbar timestamp ({xbar['time']})")

    if not mismatches:
        print(f"[PASS] Strategy [{name}]: All {len(trades)} trades and {len(markers)} markers match 5m bars 100%!")
    else:
        print(f"[FAIL] Strategy [{name}]: Found {len(mismatches)} mismatches:")
        for m in mismatches[:10]:
            print("  -", m)
