"""
微台10動能布林突破策略 - 深度全維度量化診斷分析器 (run_deep_strategy_analysis.py)

嚴格計算策略全生命週期之關鍵指標：
1. 基礎收益率與成本摩擦分析
2. 多空分群績效
3. 出場類型分解
4. 日盤 vs 夜盤時段表現
5. 持倉時間分群特徵
6. 連續勝負與最大回撤水下週期
7. 風險調整後收益指標 (Sharpe, Sortino, Calmar, Expectancy)
"""

import sys
import io
import math
from datetime import datetime
import pandas as pd
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def run_analysis():
    df = pd.read_csv('trades.csv')
    df['entry_dt'] = pd.to_datetime(df['entry_time'])
    df['exit_dt'] = pd.to_datetime(df['exit_time'])
    df['duration_min'] = (df['exit_dt'] - df['entry_dt']).dt.total_seconds() / 60.0
    
    # 識別日夜盤
    # 夜盤: 15:00 ~ 05:00, 日盤: 08:45 ~ 13:45
    def get_session(dt):
        hour = dt.hour
        if 15 <= hour <= 23 or 0 <= hour < 5:
            return 'NIGHT (夜盤)'
        elif 8 <= hour <= 14:
            return 'DAY (日盤)'
        else:
            return 'OTHER'
    
    df['session'] = df['entry_dt'].apply(get_session)

    total_trades = len(df)
    wins = df[df['net_pnl'] > 0]
    losses = df[df['net_pnl'] < 0]
    ties = df[df['net_pnl'] == 0]
    
    initial_capital = 105150.0  # 起始保證金基準
    gross_profit = wins['net_pnl'].sum()
    gross_loss = abs(losses['net_pnl'].sum())
    net_pnl = df['net_pnl'].sum()
    pf = gross_profit / gross_loss if gross_loss > 0 else np.nan
    win_rate = len(wins) / total_trades
    payoff = (wins['net_pnl'].mean()) / abs(losses['net_pnl'].mean()) if len(losses) > 0 else np.nan
    expectancy = df['net_pnl'].mean()
    expectancy_ratio = (win_rate * payoff) - (1 - win_rate)
    
    # 交易摩擦成本
    total_fee = df['fee_total'].sum()
    total_tax = df['tax_total'].sum()
    total_cost = total_fee + total_tax
    cost_to_gross = (total_cost / df['gross_pnl'].sum()) * 100 if df['gross_pnl'].sum() > 0 else 0
    
    # 權益曲線與回撤動態
    df['cum_net_pnl'] = df['net_pnl'].cumsum()
    df['cum_equity'] = initial_capital + df['cum_net_pnl']
    df['peak'] = df['cum_equity'].cummax()
    df['drawdown'] = df['peak'] - df['cum_equity']
    df['dd_pct'] = (df['drawdown'] / df['peak']) * 100
    mdd = df['drawdown'].max()
    mdd_pct = df['dd_pct'].max()
    
    # 最長回撤持續時間 (水下期)
    underwater = df['drawdown'] > 0
    cur_streak = 0
    max_underwater_trades = 0
    for u in underwater:
        if u:
            cur_streak += 1
            if cur_streak > max_underwater_trades:
                max_underwater_trades = cur_streak
        else:
            cur_streak = 0
            
    # 連續盈虧
    signs = np.sign(df['net_pnl'].values)
    max_consec_wins = 0
    max_consec_losses = 0
    cur_w = 0
    cur_l = 0
    for s in signs:
        if s > 0:
            cur_w += 1
            cur_l = 0
            if cur_w > max_consec_wins: max_consec_wins = cur_w
        elif s < 0:
            cur_l += 1
            cur_w = 0
            if cur_l > max_consec_losses: max_consec_losses = cur_l
        else:
            cur_w = 0
            cur_l = 0

    # Sharpe 與 Sortino (以交易為單位年化，假設每年約 1,000 筆交易)
    rf = 0.015 / 1000  # 無風險利率每筆
    trade_returns = df['return_pct'] / 100.0
    mean_ret = trade_returns.mean()
    std_ret = trade_returns.std()
    sharpe = (mean_ret - rf) / std_ret * math.sqrt(66 * 12) if std_ret > 0 else 0  # 樣本標準化
    downside_returns = trade_returns[trade_returns < 0]
    downside_std = downside_returns.std()
    sortino = (mean_ret - rf) / downside_std * math.sqrt(66 * 12) if downside_std > 0 else 0
    calmar = (net_pnl / mdd) if mdd > 0 else 0

    print("--- RAW QUANT SUMMARY ---")
    print(f"Total Trades: {total_trades}")
    print(f"Wins: {len(wins)} ({win_rate*100:.2f}%), Losses: {len(losses)} ({len(losses)/total_trades*100:.2f}%), Ties: {len(ties)}")
    print(f"Net PnL: {net_pnl:.2f}, Gross Profit: {gross_profit:.2f}, Gross Loss: {gross_loss:.2f}")
    print(f"Profit Factor: {pf:.3f}, Payoff Ratio: {payoff:.3f}, Expectancy: {expectancy:.2f} TWD/trade, Expectancy Ratio: {expectancy_ratio:.3f}")
    print(f"Total Costs: {total_cost:.2f} (Fee: {total_fee:.2f}, Tax: {total_tax:.2f}, Ratio to Gross: {cost_to_gross:.2f}%)")
    print(f"Max Drawdown: {mdd:.2f} TWD ({mdd_pct:.2f}%), Max Underwater Trades: {max_underwater_trades} trades")
    print(f"Max Consec Wins: {max_consec_wins}, Max Consec Losses: {max_consec_losses}")
    print(f"Calmar Ratio: {calmar:.3f}, Return/MDD: {net_pnl/mdd:.3f}")
    print(f"Avg Win: {wins['net_pnl'].mean():.2f}, Max Win: {wins['net_pnl'].max():.2f}")
    print(f"Avg Loss: {losses['net_pnl'].mean():.2f}, Max Loss: {losses['net_pnl'].min():.2f}")
    print(f"Avg Duration: {df['duration_min'].mean():.1f} min (Median: {df['duration_min'].median():.1f} min)")
    print(f"Win Avg Duration: {wins['duration_min'].mean():.1f} min, Loss Avg Duration: {losses['duration_min'].mean():.1f} min")
    
    print("\n--- SIDE ANALYSIS ---")
    for side in ['LONG', 'SHORT']:
        s_df = df[df['side'] == side]
        s_wins = s_df[s_df['net_pnl'] > 0]
        s_losses = s_df[s_df['net_pnl'] < 0]
        s_gp = s_wins['net_pnl'].sum()
        s_gl = abs(s_losses['net_pnl'].sum())
        s_pf = s_gp / s_gl if s_gl > 0 else 0
        s_payoff = s_wins['net_pnl'].mean() / abs(s_losses['net_pnl'].mean()) if len(s_losses) > 0 else 0
        print(f"[{side}] Count: {len(s_df)} ({len(s_df)/total_trades*100:.1f}%), WinRate: {len(s_wins)/len(s_df)*100:.2f}%, NetPnL: {s_df['net_pnl'].sum():.2f}, PF: {s_pf:.2f}, Payoff: {s_payoff:.2f}, AvgPnL: {s_df['net_pnl'].mean():.2f}, AvgDur: {s_df['duration_min'].mean():.1f}m")

    print("\n--- EXIT REASON ANALYSIS ---")
    for r, r_df in df.groupby('exit_reason'):
        r_wins = r_df[r_df['net_pnl'] > 0]
        r_losses = r_df[r_df['net_pnl'] < 0]
        r_gp = r_wins['net_pnl'].sum()
        r_gl = abs(r_losses['net_pnl'].sum())
        r_pf = r_gp / r_gl if r_gl > 0 else (99.0 if r_gp > 0 else 0)
        print(f"[{r}] Count: {len(r_df)} ({len(r_df)/total_trades*100:.1f}%), WinRate: {len(r_wins)/len(r_df)*100:.2f}%, NetPnL: {r_df['net_pnl'].sum():.2f}, PF: {r_pf:.2f}, AvgPnL: {r_df['net_pnl'].mean():.2f}, AvgDur: {r_df['duration_min'].mean():.1f}m")

    print("\n--- SESSION ANALYSIS (DAY vs NIGHT) ---")
    for ses, ses_df in df.groupby('session'):
        ses_wins = ses_df[ses_df['net_pnl'] > 0]
        ses_losses = ses_df[ses_df['net_pnl'] < 0]
        ses_gp = ses_wins['net_pnl'].sum()
        ses_gl = abs(ses_losses['net_pnl'].sum())
        ses_pf = ses_gp / ses_gl if ses_gl > 0 else 0
        ses_payoff = ses_wins['net_pnl'].mean() / abs(ses_losses['net_pnl'].mean()) if len(ses_losses) > 0 else 0
        print(f"[{ses}] Count: {len(ses_df)} ({len(ses_df)/total_trades*100:.1f}%), WinRate: {len(ses_wins)/len(ses_df)*100:.2f}%, NetPnL: {ses_df['net_pnl'].sum():.2f}, PF: {ses_pf:.2f}, Payoff: {ses_payoff:.2f}, AvgPnL: {ses_df['net_pnl'].mean():.2f}, AvgDur: {ses_df['duration_min'].mean():.1f}m")

if __name__ == '__main__':
    run_analysis()
