"""
檢查 trades.csv 與 kline_dashboard.html 資料一致性
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json
import re
import pandas as pd

with open('kline_dashboard.html', 'r', encoding='utf-8') as f:
    html = f.read()

m_data = re.search(r'const KLINE_DATA = (\{.*?\});', html, re.DOTALL)
kline_data = json.loads(m_data.group(1))
bars_5m = kline_data['5m']
print(f"5m K線根數: {len(bars_5m)}")
print("前 3 根 5m K線:")
for b in bars_5m[:3]:
    print(f"  time={b['time']} | dt={b['datetime']} | O={b['open']} H={b['high']} L={b['low']} C={b['close']}")

df_trades = pd.read_csv('trades.csv')
print(f"\ntrades.csv 總筆數: {len(df_trades)}")

m_markers = re.search(r'const RAW_MARKERS = (\[.*?\]);', html, re.DOTALL)
raw_markers = json.loads(m_markers.group(1))
print(f"html 中的 RAW_MARKERS 總數: {len(raw_markers)}")

# 逐筆檢驗 trades.csv 中的 60 筆與 raw_markers 中的 60 對 entry/exit
print("\n=== 逐筆比對 trades.csv 與 html markers ===")
mismatch_count = 0
for idx, r in df_trades.iterrows():
    t_id = r['trade_id']
    entry_marker = [m for m in raw_markers if m.get('tradeId') == t_id and m.get('type') == 'entry']
    exit_marker = [m for m in raw_markers if m.get('tradeId') == t_id and m.get('type') == 'exit']
    
    if not entry_marker or not exit_marker:
        print(f"[錯誤] trade_id {t_id} 在 html markers 中缺失!")
        mismatch_count += 1
        continue
    
    em = entry_marker[0]
    xm = exit_marker[0]
    
    # 比對價格、時間字串、方向、損益
    price_match = (r['entry_price'] == em['price']) and (r['exit_price'] == xm['price'])
    time_match = (str(r['entry_time']) == str(em['timeStr'])) and (str(r['exit_time']) == str(xm['timeStr']))
    side_match = (r['side'] == em['side'])
    pnl_match = abs(r['net_pnl'] - xm['pnl']) < 1e-4
    
    if not (price_match and time_match and side_match and pnl_match):
        print(f"[不一致] trade_id {t_id}:")
        print(f"  CSV: side={r['side']}, entry={r['entry_time']} @ {r['entry_price']}, exit={r['exit_time']} @ {r['exit_price']}, pnl={r['net_pnl']}")
        print(f"  HTML: side={em['side']}, entry={em['timeStr']} @ {em['price']}, exit={xm['timeStr']} @ {xm['price']}, pnl={xm['pnl']}")
        mismatch_count += 1

if mismatch_count == 0:
    print(">>> 完美！所有 60 筆交易在 trades.csv 與 HTML RAW_MARKERS 中的價格、時間、方向、損益 100% 完全相符！")
else:
    print(f">>> 發現 {mismatch_count} 筆不一致！")

# 再檢查：這些交易的 entry/exit 時間戳在 5m K 棒上是否找得到對應的 K 棒
print("\n=== 檢查 entry/exit 時間是否精確落在 5m K 棒時間軸上 ===")
bar_time_set = set(b['time'] for b in bars_5m)
bar_dt_set = set(b['datetime'] for b in bars_5m)

unmatched_entry = 0
unmatched_exit = 0
for idx, r in df_trades.iterrows():
    if r['entry_time'] not in bar_dt_set:
        unmatched_entry += 1
        print(f"  [警訊] entry_time {r['entry_time']} 不在 5m bars 的 datetime 集合中!")
    if r['exit_time'] not in bar_dt_set:
        unmatched_exit += 1
        print(f"  [警訊] exit_time {r['exit_time']} 不在 5m bars 的 datetime 集合中!")

print(f"entry_time 未精確對齊 5m K棒開盤時間筆數: {unmatched_entry}")
print(f"exit_time 未精確對齊 5m K棒開盤時間筆數: {unmatched_exit}")
