"""
詳細檢驗前 10 筆與後 10 筆交易在 trades.csv 與 HTML 中的每一個細節
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json
import re
import pandas as pd

df = pd.read_csv('trades.csv')
with open('kline_dashboard.html', 'r', encoding='utf-8') as f:
    html = f.read()

m_markers = re.search(r'const RAW_MARKERS = (\[.*?\]);', html, re.DOTALL)
markers = json.loads(m_markers.group(1))

m_metrics = re.search(r'const METRICS = (\{.*?\});', html, re.DOTALL)
metrics = json.loads(m_metrics.group(1))

print("=== HTML 中的關鍵數據 ===")
for k, v in metrics.items():
    print(f"  {k}: {v}")

print("\n=== trades.csv 統計數據 ===")
wins = df[df['net_pnl'] > 0]
losses = df[df['net_pnl'] < 0]
print(f"  總交易筆數: {len(df)}")
print(f"  獲利筆數: {len(wins)}, 虧損筆數: {len(losses)}")
print(f"  勝率: {len(wins)/len(df)*100:.2f}% ({len(wins)}/{len(df)})")
print(f"  總淨損益: {df['net_pnl'].sum():,.2f}")
print(f"  總報酬率: {df['net_pnl'].sum() / 105150 * 100:.2f}%")
print(f"  獲利因子: {wins['net_pnl'].sum() / abs(losses['net_pnl'].sum()):.3f}")

# 逐筆打印前 5 筆
print("\n=== 前 5 筆交易逐項對比 ===")
for i in range(1, 6):
    r = df[df['trade_id'] == i].iloc[0]
    em = [m for m in markers if m.get('tradeId') == i and m.get('type') == 'entry'][0]
    xm = [m for m in markers if m.get('tradeId') == i and m.get('type') == 'exit'][0]
    print(f"Trade #{i}:")
    print(f"  [CSV]  side={r['side']}, entry={r['entry_time']} @ {r['entry_price']}, exit={r['exit_time']} @ {r['exit_price']}, pnl={r['net_pnl']}, reason={r['exit_reason']}")
    print(f"  [HTML] entry={em['timeStr']} @ {em['price']}, exit={xm['timeStr']} @ {xm['price']}, pnl={xm['pnl']}, reason={xm['reason']}")

# 逐筆打印後 5 筆
print("\n=== 後 5 筆交易逐項對比 ===")
for i in range(56, 61):
    r = df[df['trade_id'] == i].iloc[0]
    em = [m for m in markers if m.get('tradeId') == i and m.get('type') == 'entry'][0]
    xm = [m for m in markers if m.get('tradeId') == i and m.get('type') == 'exit'][0]
    print(f"Trade #{i}:")
    print(f"  [CSV]  side={r['side']}, entry={r['entry_time']} @ {r['entry_price']}, exit={r['exit_time']} @ {r['exit_price']}, pnl={r['net_pnl']}, reason={r['exit_reason']}")
    print(f"  [HTML] entry={em['timeStr']} @ {em['price']}, exit={xm['timeStr']} @ {xm['price']}, pnl={xm['pnl']}, reason={xm['reason']}")
