/**
 * 提取 kline_dashboard.html 內的 5m K 線資料與 runFastBacktest 邏輯，
 * 針對三張圖的參數進行極速回測，產出完全對齊前端預覽的逐筆交易明細 JSON
 */
const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, '..', 'kline_dashboard.html');
const content = fs.readFileSync(htmlPath, 'utf8');

// 提取 KLINE_DATA
const dataMatch = content.match(/const KLINE_DATA\s*=\s*(\{[\s\S]*?\});\s*const MULTI_STRATEGIES_DATA/);
if (!dataMatch) {
    console.error('無法提取 KLINE_DATA');
    process.exit(1);
}

const KLINE_DATA = JSON.parse(dataMatch[1]);
const bars5m = KLINE_DATA['5m'];
console.log(`已成功載入 5m K 線資料: ${bars5m.length} 根`);

// 提取 runFastBacktest 相關函式
function calculateWilderRma(series, length) {
    const n = series.length;
    const rma = new Array(n).fill(NaN);
    if (n < length) return rma;
    let sum = 0;
    for (let i = 0; i < length; i++) sum += series[i];
    rma[length - 1] = sum / length;
    const alpha = 1.0 / length;
    for (let i = length; i < n; i++) {
        rma[i] = alpha * series[i] + (1.0 - alpha) * rma[i - 1];
    }
    return rma;
}

function runFastBacktest(bars, config) {
    const n = bars.length;
    const opens = bars.map(b => Number(b.open));
    const highs = bars.map(b => Number(b.high));
    const lows = bars.map(b => Number(b.low));
    const closes = bars.map(b => Number(b.close));
    const times = bars.map(b => Number(b.time));
    const datetimes = bars.map(b => b.datetime);

    // 1. 布林通道 (SMA + ddof=0 StdDev)
    const bbLen = Number(config.bb_length) || 30;
    const bbMult = Number(config.bb_mult) || 1.75;
    const pullbackRatio = Number(config.pullback_ratio) || 0.25;
    const bbUpper = new Array(n).fill(NaN);
    const bbLower = new Array(n).fill(NaN);

    for (let i = bbLen - 1; i < n; i++) {
        let sum = 0;
        for (let j = i - bbLen + 1; j <= i; j++) sum += closes[j];
        const mean = sum / bbLen;
        let varSum = 0;
        for (let j = i - bbLen + 1; j <= i; j++) varSum += Math.pow(closes[j] - mean, 2);
        const std = Math.sqrt(varSum / bbLen);
        bbUpper[i] = mean + bbMult * std;
        bbLower[i] = mean - bbMult * std;
    }

    // 2. ATR 及 Signal 線 (Wilder RMA)
    const tr = new Array(n).fill(0);
    tr[0] = highs[0] - lows[0];
    for (let i = 1; i < n; i++) {
        const tr1 = highs[i] - lows[i];
        const tr2 = Math.abs(highs[i] - closes[i - 1]);
        const tr3 = Math.abs(lows[i] - closes[i - 1]);
        tr[i] = Math.max(tr1, tr2, tr3);
    }
    const atrLen = Number(config.atr_length) || 14;
    const atrVal = calculateWilderRma(tr, atrLen);

    // ATR Signal (MA of ATR)
    const atrSigLen = Number(config.atr_sig_length) || 5;
    const atrSig = new Array(n).fill(NaN);
    for (let i = atrSigLen - 1; i < n; i++) {
        let sum = 0;
        let count = 0;
        for (let j = i - atrSigLen + 1; j <= i; j++) {
            if (!isNaN(atrVal[j])) {
                sum += atrVal[j];
                count++;
            }
        }
        if (count === atrSigLen) atrSig[i] = sum / atrSigLen;
    }

    // 3. 唐奇安通道 (shift 1)
    const donchianLen = Number(config.donchian_length) || 4;
    const donchianHigh = new Array(n).fill(NaN);
    const donchianLow = new Array(n).fill(NaN);
    for (let i = donchianLen; i < n; i++) {
        let hMax = -Infinity;
        let lMin = Infinity;
        for (let j = i - donchianLen; j < i; j++) {
            if (highs[j] > hMax) hMax = highs[j];
            if (lows[j] < lMin) lMin = lows[j];
        }
        donchianHigh[i] = hMax;
        donchianLow[i] = lMin;
    }

    // 4. 超級趨勢線 SuperTrend (Wilder RMA ATR)
    const stLen = Number(config.supertrend_length) || 14;
    const stMult = Number(config.supertrend_multiplier) || 2.0;
    const stAtr = calculateWilderRma(tr, stLen);
    const stVal = new Array(n).fill(NaN);
    const stDir = new Array(n).fill(1);

    let prevUpper = NaN;
    let prevLower = NaN;
    let direction = 1;

    for (let i = 1; i < n; i++) {
        if (isNaN(stAtr[i])) continue;
        const hl2 = (highs[i] + lows[i]) / 2.0;
        const basicUpper = hl2 + (stMult * stAtr[i]);
        const basicLower = hl2 - (stMult * stAtr[i]);

        let finalUpper, finalLower;
        if (i === 1) {
            finalUpper = basicUpper;
            finalLower = basicLower;
        } else {
            finalUpper = (basicUpper < prevUpper || closes[i - 1] > prevUpper) ? basicUpper : prevUpper;
            finalLower = (basicLower > prevLower || closes[i - 1] < prevLower) ? basicLower : prevLower;
        }

        let curSt;
        if (direction === 1) {
            if (closes[i] < finalLower) {
                direction = -1;
                curSt = finalUpper;
            } else {
                curSt = finalLower;
            }
        } else {
            if (closes[i] > finalUpper) {
                direction = 1;
                curSt = finalLower;
            } else {
                curSt = finalUpper;
            }
        }
        stVal[i] = curSt;
        stDir[i] = direction;
        prevUpper = finalUpper;
        prevLower = finalLower;
    }

    // 5. 撮合回測狀態機
    const initialCap = Number(config.initial_capital) || 105150.0;
    const pointValue = 10.0;
    const marginReq = 35050.0;
    const maxLeverage = Number(config.max_leverage) || 7.0;
    const commissionPerOrder = Number(config.commission) || 18.0;
    const taxRate = 0.00002;
    const slippage = Number(config.slippage) || 0.0;
    const exitMode = config.exit_mode || 'donchian';

    let equity = initialCap;
    let posSide = 'NONE';
    let posQty = 0;
    let posEntryPrice = 0;
    let posEntryTime = '';
    let posSlPrice = 0;
    let posEntryIdx = 0;

    let pendingSignalSide = 'NONE';
    let signalBarIdx = -1;
    let signalBarClose = 0;
    let signalBarQ1 = 0;
    let signalBarExtreme = 0;

    let entryReadySide = 'NONE';
    let entrySlPrice = 0;

    const trades = [];
    const equityCurve = [];
    let tradeCounter = 0;
    let runningCumPnl = 0;

    function isThirdWednesday(tsStr) {
        if (!tsStr) return false;
        const d = new Date(tsStr);
        if (isNaN(d.getTime())) return false;
        if (d.getDay() !== 3) return false;
        const day = d.getDate();
        if (day < 15 || day > 21) return false;
        const hour = d.getHours();
        const min = d.getMinutes();
        return (hour === 13 && min === 0);
    }

    for (let i = 1; i < n; i++) {
        const curOpen = opens[i];
        const curHigh = highs[i];
        const curLow = lows[i];
        const curClose = closes[i];
        const curTimeStr = datetimes[i];
        const curTs = times[i];

        // A. 進場執行 (Bar 3)
        if (entryReadySide !== 'NONE' && posSide === 'NONE') {
            const rawCapQty = Math.floor(equity / marginReq);
            const rawLevQty = Math.floor((equity * maxLeverage) / marginReq);
            const safeQty = Math.max(1, Math.min(rawCapQty, rawLevQty));

            if (safeQty >= 1) {
                const fillPrice = (entryReadySide === 'LONG') ? (curOpen + slippage) : (curOpen - slippage);
                posSide = entryReadySide;
                posQty = safeQty;
                posEntryPrice = fillPrice;
                posEntryTime = curTimeStr;
                posSlPrice = entrySlPrice;
                posEntryIdx = i;
            }
            entryReadySide = 'NONE';
        }

        // B. 出場檢核
        if (posSide !== 'NONE') {
            let exitReason = null;
            let rawExitPrice = curClose;

            if (isThirdWednesday(curTimeStr)) {
                exitReason = 'ROLLOVER_SETTLEMENT';
                rawExitPrice = curClose;
            } else if (posSide === 'LONG' && curLow <= posSlPrice) {
                exitReason = 'STOP_LOSS';
                rawExitPrice = Math.min(curOpen, posSlPrice);
            } else if (posSide === 'SHORT' && curHigh >= posSlPrice) {
                exitReason = 'STOP_LOSS';
                rawExitPrice = Math.max(curOpen, posSlPrice);
            } else if (exitMode === 'donchian') {
                if (posSide === 'LONG' && !isNaN(donchianLow[i]) && curClose < donchianLow[i]) {
                    exitReason = 'DONCHIAN_EXIT';
                    rawExitPrice = curClose;
                } else if (posSide === 'SHORT' && !isNaN(donchianHigh[i]) && curClose > donchianHigh[i]) {
                    exitReason = 'DONCHIAN_EXIT';
                    rawExitPrice = curClose;
                }
            } else if (exitMode === 'supertrend') {
                if (posSide === 'LONG' && (stDir[i] === -1 || curClose < stVal[i])) {
                    exitReason = 'SUPERTREND_EXIT';
                    rawExitPrice = curClose;
                } else if (posSide === 'SHORT' && (stDir[i] === 1 || curClose > stVal[i])) {
                    exitReason = 'SUPERTREND_EXIT';
                    rawExitPrice = curClose;
                }
            } else if (exitMode === 'atr') {
                if (!isNaN(atrVal[i]) && !isNaN(atrSig[i]) && !isNaN(atrVal[i - 1]) && !isNaN(atrSig[i - 1])) {
                    const prevAbove = atrVal[i - 1] >= atrSig[i - 1];
                    const curBelow = atrVal[i] < atrSig[i];
                    if (prevAbove && curBelow) {
                        exitReason = 'ATR_TAKE_PROFIT';
                        rawExitPrice = curClose;
                    }
                }
            }

            if (exitReason !== null) {
                tradeCounter++;
                const actualExitPrice = (posSide === 'LONG') ? (rawExitPrice - slippage) : (rawExitPrice + slippage);
                const grossPnl = (posSide === 'LONG')
                    ? (actualExitPrice - posEntryPrice) * pointValue * posQty
                    : (posEntryPrice - actualExitPrice) * pointValue * posQty;

                const feeTotal = commissionPerOrder * 2.0 * posQty;
                const entryNotional = posEntryPrice * pointValue * posQty;
                const exitNotional = actualExitPrice * pointValue * posQty;
                const taxTotal = Math.round(entryNotional * taxRate) + Math.round(exitNotional * taxRate);
                const netPnl = grossPnl - feeTotal - taxTotal;
                equity += netPnl;
                runningCumPnl += netPnl;

                let exitReasonZh = '結算';
                if (exitReason === 'STOP_LOSS') exitReasonZh = '停損';
                else if (exitReason === 'ATR_TAKE_PROFIT') exitReasonZh = 'ATR停利';
                else if (exitReason === 'DONCHIAN_EXIT') exitReasonZh = '唐奇安出場';
                else if (exitReason === 'SUPERTREND_EXIT') exitReasonZh = '超級趨勢出場';

                trades.push({
                    trade_id: tradeCounter,
                    side: posSide,
                    entry_time: posEntryTime,
                    entry_price: posEntryPrice,
                    exit_time: curTimeStr,
                    exit_price: actualExitPrice,
                    qty: posQty,
                    exit_reason: exitReasonZh,
                    costs: feeTotal + taxTotal,
                    net_pnl: netPnl,
                    cum_pnl: Math.round(runningCumPnl * 100) / 100,
                    return_pct: Math.round(((actualExitPrice - posEntryPrice) / posEntryPrice * (posSide === 'LONG' ? 1 : -1) * 100) * 100) / 100,
                    equity_after: Math.round(equity * 100) / 100,
                    entry_ts: times[posEntryIdx],
                    exit_ts: curTs
                });

                posSide = 'NONE';
                posQty = 0;
            }
        }

        // C. 第二根確認棒與第一根訊號棒判斷
        if (posSide === 'NONE' && entryReadySide === 'NONE') {
            if (pendingSignalSide !== 'NONE' && (i - 1 === signalBarIdx)) {
                if (pendingSignalSide === 'LONG') {
                    const isConfirmed = (curClose <= signalBarClose) && (curClose >= signalBarQ1);
                    if (isConfirmed) {
                        entryReadySide = 'LONG';
                        entrySlPrice = signalBarExtreme;
                    }
                } else if (pendingSignalSide === 'SHORT') {
                    const isConfirmed = (curClose >= signalBarClose) && (curClose <= signalBarQ1);
                    if (isConfirmed) {
                        entryReadySide = 'SHORT';
                        entrySlPrice = signalBarExtreme;
                    }
                }
                pendingSignalSide = 'NONE';
            }

            if (!isNaN(bbUpper[i]) && !isNaN(bbLower[i])) {
                if (curClose > bbUpper[i] && curClose > curOpen) {
                    const body = curClose - curOpen;
                    pendingSignalSide = 'LONG';
                    signalBarIdx = i;
                    signalBarClose = curClose;
                    signalBarQ1 = curClose - (body * pullbackRatio);
                    signalBarExtreme = curLow;
                } else if (curClose < bbLower[i] && curClose < curOpen) {
                    const body = curOpen - curClose;
                    pendingSignalSide = 'SHORT';
                    signalBarIdx = i;
                    signalBarClose = curClose;
                    signalBarQ1 = curClose + (body * pullbackRatio);
                    signalBarExtreme = curHigh;
                }
            }
        }

        // D. 權益曲線記錄
        let currentEquity = equity;
        if (posSide !== 'NONE') {
            const unrealized = (posSide === 'LONG')
                ? (curClose - posEntryPrice) * pointValue * posQty
                : (posEntryPrice - curClose) * pointValue * posQty;
            currentEquity += unrealized;
        }
        equityCurve.push({
            time: curTs,
            value: Math.round((currentEquity - initialCap) * 100) / 100,
            equity: Math.round(currentEquity * 100) / 100,
            close: curClose,
            in_position: (posSide !== 'NONE')
        });
    }

    // 6. 計算量化指標
    const totalPnl = equity - initialCap;
    const returnPct = (totalPnl / initialCap) * 100;
    const wins = trades.filter(t => t.net_pnl > 0);
    const losses = trades.filter(t => t.net_pnl < 0);
    const winRate = trades.length > 0 ? (wins.length / trades.length) * 100 : 0;
    const totalGrossWin = wins.reduce((acc, t) => acc + t.net_pnl, 0);
    const totalGrossLoss = Math.abs(losses.reduce((acc, t) => acc + t.net_pnl, 0));
    const profitFactor = totalGrossLoss > 0 ? (totalGrossWin / totalGrossLoss) : (totalGrossWin > 0 ? 99.9 : 0);

    let peak = -Infinity;
    let maxDd = 0;
    for (const pt of equityCurve) {
        if (pt.equity > peak) peak = pt.equity;
        const dd = peak - pt.equity;
        if (dd > maxDd) maxDd = dd;
    }

    const avgWin = wins.length > 0 ? totalGrossWin / wins.length : 0;
    const avgLoss = losses.length > 0 ? totalGrossLoss / losses.length : 0;
    const payoff = avgLoss > 0 ? avgWin / avgLoss : 0;

    return {
        metrics: {
            totalNetProfit: Math.round(totalPnl),
            winRatePct: Math.round(winRate * 100) / 100,
            profitFactor: Math.round(profitFactor * 1000) / 1000,
            totalTrades: trades.length,
            maxDrawdown: -Math.round(maxDd),
            payoffRatio: Math.round(payoff * 100) / 100
        },
        trades
    };
}

// 測試三組參數
const p1 = {
    strategy_name: '布林動能突破 (ATR基準)',
    exit_mode: 'atr',
    bb_length: 30,
    bb_mult: 1.8,
    pullback_ratio: 0.4,
    atr_length: 14,
    atr_sig_length: 4,
    max_leverage: 7.0,
    commission: 18.0,
    slippage: 0.0
};

const p2 = {
    strategy_name: '布林_超級趨勢',
    exit_mode: 'supertrend',
    bb_length: 30,
    bb_mult: 1.8,
    pullback_ratio: 0.3,
    supertrend_length: 10,
    supertrend_multiplier: 1.0,
    max_leverage: 7.0,
    commission: 18.0,
    slippage: 0.0
};

const p3 = {
    strategy_name: '布林_唐奇安',
    exit_mode: 'donchian',
    bb_length: 31,
    bb_mult: 1.75,
    pullback_ratio: 0.3,
    donchian_length: 2,
    max_leverage: 7.5,
    commission: 18.0,
    slippage: 0.0
};

const res1 = runFastBacktest(bars5m, p1);
const res2 = runFastBacktest(bars5m, p2);
const res3 = runFastBacktest(bars5m, p3);

console.log('\n--- 圖一 回測結果 ---');
console.log(res1.metrics);
console.log('圖一預期: 淨利 +43,202, 勝率 43.33%, PF 1.767, 交易 90 筆, MDD -13,654, 賺賠比 2.31');

console.log('\n--- 圖二 回測結果 ---');
console.log(res2.metrics);
console.log('圖二預期: 淨利 +17,193, 勝率 43.94%, PF 1.353, 交易 66 筆, MDD -13,316, 賺賠比 1.73');

console.log('\n--- 圖三 回測結果 ---');
console.log(res3.metrics);
console.log('圖三預期: 淨利 +20,839, 勝率 40.58%, PF 1.410, 交易 69 筆, MDD -13,468, 賺賠比 2.06');

// 儲存為 JSON 供後續 Python 產生 Excel
fs.writeFileSync(path.join(__dirname, 'exact_trades_p1.json'), JSON.stringify(res1, null, 2));
fs.writeFileSync(path.join(__dirname, 'exact_trades_p2.json'), JSON.stringify(res2, null, 2));
fs.writeFileSync(path.join(__dirname, 'exact_trades_p3.json'), JSON.stringify(res3, null, 2));
console.log('\n三組策略之逐筆交易明細已成功輸出為 JSON 檔！');
