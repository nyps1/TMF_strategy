"""
策略深度診斷與勝率歸因分析腳本 (diagnose_strategy.py)
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import numpy as np

df = pd.read_csv('trades.csv')

print("=" * 60)
print("  策略交易全域回測數據深度統計分析")
print("=" * 60)
total_trades = len(df)
wins = df[df['net_pnl'] > 0]
losses = df[df['net_pnl'] < 0]
ties = df[df['net_pnl'] == 0]

print(f"總交易筆數          : {total_trades}")
print(f"獲利交易次數 (Wins) : {len(wins)} ({len(wins)/total_trades*100:.2f}%)")
print(f"虧損交易次數 (Loss) : {len(losses)} ({len(losses)/total_trades*100:.2f}%)")
print(f"平手次數 (Ties)     : {len(ties)} ({len(ties)/total_trades*100:.2f}%)")
print(f"總淨損益 (Net PnL)  : {df['net_pnl'].sum():,.1f} 元")
print(f"期望值 (Expectancy) : {df['net_pnl'].mean():,.2f} 元 / 筆")

avg_win = wins['net_pnl'].mean() if len(wins) > 0 else 0
avg_loss = abs(losses['net_pnl'].mean()) if len(losses) > 0 else 0
payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 0
profit_factor = wins['net_pnl'].sum() / abs(losses['net_pnl'].sum()) if len(losses) > 0 else 0

print(f"平均獲利 (Avg Win)  : +{avg_win:,.2f} 元")
print(f"平均虧損 (Avg Loss) : -{avg_loss:,.2f} 元")
print(f"實質賺賠比 (Payoff) : {payoff_ratio:.2f}")
print(f"獲利因子 (PF)       : {profit_factor:.3f}")

print("\n" + "=" * 60)
print("  出場原因 (Exit Reason) 分類統計")
print("=" * 60)
for reason, group in df.groupby('exit_reason'):
    g_wins = group[group['net_pnl'] > 0]
    g_win_rate = len(g_wins) / len(group) * 100
    print(f"[{reason}]")
    print(f"  筆數: {len(group)} ({len(group)/total_trades*100:.1f}%) | 勝率: {g_win_rate:.2f}% | 總淨利: {group['net_pnl'].sum():,.1f} 元 | 均筆損益: {group['net_pnl'].mean():,.1f} 元")
    if reason == 'ATR_TAKE_PROFIT':
        atr_losses = group[group['net_pnl'] < 0]
        print(f"  --> [警訊] 名為「停利(ATR_TAKE_PROFIT)」，但實質虧損筆數為: {len(atr_losses)} 筆! (佔該類別 {len(atr_losses)/len(group)*100:.1f}%)")

print("\n" + "=" * 60)
print("  多空方向 (Long vs Short) 分類統計")
print("=" * 60)
for side, group in df.groupby('side'):
    g_wins = group[group['net_pnl'] > 0]
    print(f"[{side}]")
    print(f"  筆數: {len(group)} | 勝率: {len(g_wins)/len(group)*100:.2f}% | 總淨利: {group['net_pnl'].sum():,.1f} 元 | 均筆: {group['net_pnl'].mean():,.1f} 元")

df['entry_dt'] = pd.to_datetime(df['entry_time'])
df['exit_dt'] = pd.to_datetime(df['exit_time'])
df['duration_min'] = (df['exit_dt'] - df['entry_dt']).dt.total_seconds() / 60

print("\n" + "=" * 60)
print("  持倉時長 (Holding Duration) 分析")
print("=" * 60)
print(f"全體平均持倉時間    : {df['duration_min'].mean():.1f} 分鐘")
print(f"獲利單平均持倉時間  : {wins.merge(df[['trade_id', 'duration_min']], on='trade_id')['duration_min'].mean():.1f} 分鐘")
print(f"虧損單平均持倉時間  : {losses.merge(df[['trade_id', 'duration_min']], on='trade_id')['duration_min'].mean():.1f} 分鐘")
print(f"持有 0~5 分鐘出場筆數: {(df['duration_min'] <= 5).sum()} 筆 ({(df['duration_min'] <= 5).mean()*100:.1f}%)")

print("\n" + "=" * 60)
print("  交易摩擦成本 (Transaction Costs) 衝擊")
print("=" * 60)
total_fee = df['fee_total'].sum()
total_tax = df['tax_total'].sum()
total_costs = total_fee + total_tax
gross_pnl = df['gross_pnl'].sum()
net_pnl = df['net_pnl'].sum()
print(f"策略毛利 (Gross PnL): {gross_pnl:,.1f} 元")
print(f"總手續費 (Fee)      : {total_fee:,.1f} 元")
print(f"總期交稅 (Tax)      : {total_tax:,.1f} 元")
print(f"總摩擦成本 (Total)  : {total_costs:,.1f} 元")
print(f"摩擦成本佔毛利比例  : {total_costs / gross_pnl * 100:.2f}%")
print(f"每筆平均摩擦成本    : {total_costs / total_trades:.2f} 元 (每口單邊約 28 元，來回 56 元)")
print("=" * 60)
