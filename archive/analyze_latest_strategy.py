"""
微台10動能布林突破策略 - 最新全維度量化診斷分析腳本 (analyze_latest_strategy.py)
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import numpy as np

def main():
    df = pd.read_csv('trades.csv')
    n = len(df)
    wins = df[df['net_pnl'] > 0]
    losses = df[df['net_pnl'] < 0]
    ties = df[df['net_pnl'] == 0]

    gross_profit = wins['net_pnl'].sum()
    gross_loss = abs(losses['net_pnl'].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else 0
    payoff = (wins['net_pnl'].mean()) / abs(losses['net_pnl'].mean()) if len(losses) > 0 else 0

    print("=" * 65)
    print("      微台10動態布林突破策略 (嚴格拉回確認版) - 最新量化績效報表")
    print("=" * 65)
    print(f"總交易筆數          : {n} 筆")
    print(f"獲利交易次數 (Wins) : {len(wins)} 筆 ({len(wins)/n*100:.2f}%)")
    print(f"虧損交易次數 (Loss) : {len(losses)} 筆 ({len(losses)/n*100:.2f}%)")
    print(f"平手交易次數 (Ties) : {len(ties)} 筆 ({len(ties)/n*100:.2f}%)")
    print(f"策略毛利 (未扣成本) : {df['gross_pnl'].sum():>10,.1f} 元")
    print(f"總手續費 (Fee)      : {df['fee_total'].sum():>10,.1f} 元")
    print(f"總期交稅 (Tax)      : {df['tax_total'].sum():>10,.1f} 元")
    total_costs = df['fee_total'].sum() + df['tax_total'].sum()
    print(f"總交易摩擦成本      : {total_costs:>10,.1f} 元 (佔毛利 {total_costs/df['gross_pnl'].sum()*100:.2f}%)")
    print(f"策略淨損益 (Net PnL): {df['net_pnl'].sum():>10,.1f} 元")
    print(f"獲利因子 (Profit Factor) : {pf:.3f}")
    print(f"賺賠比 (Payoff Ratio)    : {payoff:.2f}")
    print(f"平均每筆獲利 (Avg Win)   : +{wins['net_pnl'].mean():,.1f} 元")
    print(f"平均每筆虧損 (Avg Loss)  : -{abs(losses['net_pnl'].mean()):,.1f} 元")
    print(f"數學期望值 (Expectancy)  : {df['net_pnl'].mean():+,.1f} 元 / 筆")

    print("\n" + "=" * 65)
    print("  出場原因 (Exit Reason) 分類表現剖析")
    print("=" * 65)
    for reason, group in df.groupby('exit_reason'):
        gw = group[group['net_pnl'] > 0]
        gl = group[group['net_pnl'] < 0]
        gw_pnl = gw['net_pnl'].sum() if len(gw) > 0 else 0
        gl_pnl = gl['net_pnl'].sum() if len(gl) > 0 else 0
        r_pf = gw_pnl / abs(gl_pnl) if abs(gl_pnl) > 0 else 99.0
        print(f"[{reason}]")
        print(f"  總筆數: {len(group)} 筆 ({len(group)/n*100:.1f}%) | 勝率: {len(gw)/len(group)*100:.2f}%")
        print(f"  總淨利: {group['net_pnl'].sum():+,.1f} 元 | 獲利因子: {r_pf:.2f} | 均筆損益: {group['net_pnl'].mean():+,.1f} 元")
        if len(gw) > 0:
            print(f"  贏單均利: +{gw['net_pnl'].mean():,.1f} 元 (最大: +{gw['net_pnl'].max():,.1f} 元)")
        if len(gl) > 0:
            print(f"  輸單均虧: -{abs(gl['net_pnl'].mean()):,.1f} 元 (最大: -{abs(gl['net_pnl'].min()):,.1f} 元)")

    print("\n" + "=" * 65)
    print("  多空方向 (Long vs. Short) 分類表現剖析")
    print("=" * 65)
    for side, group in df.groupby('side'):
        gw = group[group['net_pnl'] > 0]
        gl = group[group['net_pnl'] < 0]
        gw_pnl = gw['net_pnl'].sum() if len(gw) > 0 else 0
        gl_pnl = gl['net_pnl'].sum() if len(gl) > 0 else 0
        s_pf = gw_pnl / abs(gl_pnl) if abs(gl_pnl) > 0 else 99.0
        s_payoff = (gw['net_pnl'].mean()) / abs(gl['net_pnl'].mean()) if len(gl) > 0 else 0
        print(f"[{side}]")
        print(f"  總筆數: {len(group)} 筆 ({len(group)/n*100:.1f}%) | 勝率: {len(gw)/len(group)*100:.2f}%")
        print(f"  總淨利: {group['net_pnl'].sum():+,.1f} 元 | 獲利因子: {s_pf:.2f} | 賺賠比: {s_payoff:.2f}")
        print(f"  均筆損益: {group['net_pnl'].mean():+,.1f} 元 | 贏單均利: +{gw['net_pnl'].mean():,.1f} 元 | 輸單均虧: -{abs(gl['net_pnl'].mean()):,.1f} 元")

    df['entry_dt'] = pd.to_datetime(df['entry_time'])
    df['exit_dt'] = pd.to_datetime(df['exit_time'])
    df['duration_min'] = (df['exit_dt'] - df['entry_dt']).dt.total_seconds() / 60.0

    print("\n" + "=" * 65)
    print("  持倉時間 (Holding Duration) 特徵分析")
    print("=" * 65)
    print(f"全體平均持倉時長    : {df['duration_min'].mean():.1f} 分鐘 (中位數: {df['duration_min'].median():.1f} 分鐘)")
    print(f"獲利單平均持倉時長  : {df[df['net_pnl'] > 0]['duration_min'].mean():.1f} 分鐘")
    print(f"虧損單平均持倉時長  : {df[df['net_pnl'] < 0]['duration_min'].mean():.1f} 分鐘")
    quick_sl = df[(df['exit_reason'] == 'STOP_LOSS') & (df['duration_min'] <= 10)]
    print(f"10 分鐘內快速停損筆數: {len(quick_sl)} 筆 (佔全體停損 {len(quick_sl)/len(df[df['exit_reason']=='STOP_LOSS'])*100:.1f}%)")

    print("\n" + "=" * 65)
    print("  最新納入夜盤之交易明細 (Trade 61 ~ 64)")
    print("=" * 65)
    tail_df = df.tail(4)
    for _, r in tail_df.iterrows():
        pnl_symbol = "+" if r['net_pnl'] > 0 else ""
        print(f"Trade #{r['trade_id']:02d} | {r['side']:<5} | 進: {r['entry_time']} @ {r['entry_price']} | 出: {r['exit_time']} @ {r['exit_price']} | 原因: {r['exit_reason']:<16} | 口數: {r['qty']} | 淨利: {pnl_symbol}{r['net_pnl']:,.1f} 元")

    # 計算最大回撤 MDD
    cum_pnl = df['net_pnl'].cumsum()
    peak = cum_pnl.cummax()
    drawdown = peak - cum_pnl
    max_dd = drawdown.max()
    print("\n" + "=" * 65)
    print(f"  歷史最大權益回撤 (Max Drawdown, 封閉交易): {max_dd:,.1f} 元")
    print("=" * 65)

if __name__ == '__main__':
    main()
