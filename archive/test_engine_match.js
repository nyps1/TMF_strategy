const fs = require('fs');

// 從現有 kline_dashboard.html 中萃取 5m 資料進行回測對齊檢驗
const html = fs.readFileSync('kline_dashboard.html', 'utf-8');
const dataMatch = html.match(/const KLINE_DATA = ({.*?});/s);
if (!dataMatch) {
    console.error('無法提取 KLINE_DATA');
    process.exit(1);
}

const KLINE_DATA = JSON.parse(dataMatch[1]);
const bars = KLINE_DATA['5m'];
console.log('已讀取 5m K線:', bars.length, '根');

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
    const times = bars.map(b => b.time);
    const datetimes = bars.map(b => b.datetime);

    // 1. 布林通道
    const bbLen = config.bb_length || 30;
    const bbMult = config.bb_mult || 1.75;
    const bbUpper = new Array(n).fill(NaN);
    const bbLower = new Array(n).fill(NaN);

    for (let i = bbLen - 1; i < n; i++) {
        let sum = 0;
        for (let j = i - bbLen + 1; j <= i; j++) sum += closes[j];
        const mean = sum / bbLen;
        let varSum = 0;
        for (let j = i - bbLen + 1; j <= i; j++) varSum += Math.pow(closes[j] - mean, 2);
        const std = Math.sqrt(varSum / bbLen); // ddof=0
        bbUpper[i] = mean + bbMult * std;
        bbLower[i] = mean - bbMult * std;
    }

    // 2. True Range 與 ATR
    const tr = new Array(n).fill(NaN);
    tr[0] = highs[0] - lows[0];
    for (let i = 1; i < n; i++) {
        const tr1 = highs[i] - lows[i];
        const tr2 = Math.abs(highs[i] - closes[i - 1]);
        const tr3 = Math.abs(lows[i] - closes[i - 1]);
        tr[i] = Math.max(tr1, tr2, tr3);
    }
    const atrLen = config.atr_length || 14;
    const atrVal = calculateWilderRma(tr, atrLen);

    // ATR Signal
    const atrSigLen = config.atr_sig_length || 5;
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
    const donchianLen = config.donchian_length || 4;
    const donchianHigh = new Array(n).fill(NaN);
    const donchianLow = new Array(n).fill(NaN);
    for (let i = donchianLen; i < n; i++) {
        let maxH = -Infinity;
        let minL = Infinity;
        for (let j = i - donchianLen; j < i; j++) {
            if (highs[j] > maxH) maxH = highs[j];
            if (lows[j] < minL) minL = lows[j];
        }
        donchianHigh[i] = maxH;
        donchianLow[i] = minL;
    }

    // 4. 超級趨勢線
    const stLen = config.supertrend_length || 14;
    const stMult = config.supertrend_multiplier || 2.0;
    const stVal = new Array(n).fill(NaN);
    const stDir = new Array(n).fill(1);
    let prevUpper = 0, prevLower = 0, direction = 1;

    for (let i = 1; i < n; i++) {
        if (isNaN(atrVal[i]) || atrVal[i] <= 0) continue;
        const median = (highs[i] + lows[i]) / 2.0;
        const basicUpper = median + stMult * atrVal[i];
        const basicLower = median - stMult * atrVal[i];

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
    const initialCap = config.initial_capital || 105150.0;
    const pointValue = 10.0;
    const marginReq = 35050.0;
    const maxLeverage = config.max_leverage || 7.0;
    const commissionPerOrder = config.commission || 18.0;
    const taxRate = 0.00002;
    const slippage = config.slippage || 0.0;
    const exitMode = config.exit_mode || 'donchian';

    let equity = initialCap;
    let posSide = 'NONE'; // 'LONG', 'SHORT', 'NONE'
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

    function isThirdWednesday(tsStr) {
        if (!tsStr) return false;
        const d = new Date(tsStr);
        if (isNaN(d.getTime())) return false;
        if (d.getDay() !== 3) return false; // 星期三
        const day = d.getDate();
        if (day < 15 || day > 21) return false; // 第三個星期三在 15~21 號
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

                trades.push({
                    trade_id: tradeCounter,
                    side: posSide,
                    entry_time: posEntryTime,
                    entry_price: posEntryPrice,
                    exit_time: curTimeStr,
                    exit_price: actualExitPrice,
                    qty: posQty,
                    exit_reason: exitReason,
                    costs: feeTotal + taxTotal,
                    net_pnl: netPnl,
                    equity_after: equity,
                    entry_ts: Math.floor(new Date(posEntryTime).getTime() / 1000),
                    exit_ts: Math.floor(new Date(curTimeStr).getTime() / 1000)
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
                    signalBarQ1 = curClose - (body * 0.25);
                    signalBarExtreme = curLow;
                } else if (curClose < bbLower[i] && curClose < curOpen) {
                    const body = curOpen - curClose;
                    pendingSignalSide = 'SHORT';
                    signalBarIdx = i;
                    signalBarClose = curClose;
                    signalBarQ1 = curClose + (body * 0.25);
                    signalBarExtreme = curHigh;
                }
            }
        }

        // D. 記錄淨值
        let curEq = equity;
        if (posSide !== 'NONE') {
            const unrealized = (posSide === 'LONG')
                ? (curClose - posEntryPrice) * pointValue * posQty
                : (posEntryPrice - curClose) * pointValue * posQty;
            curEq += unrealized;
        }
        equityCurve.push({
            time: curTs,
            value: Math.round((curEq - initialCap) * 100) / 100,
            equity: Math.round(curEq * 100) / 100
        });
    }

    const wins = trades.filter(t => t.net_pnl > 0).map(t => t.net_pnl);
    const losses = trades.filter(t => t.net_pnl < 0).map(t => t.net_pnl);
    const totalNetProfit = equity - initialCap;
    const winRatePct = trades.length > 0 ? (wins.length / trades.length) * 100 : 0;
    const grossWin = wins.reduce((a, b) => a + b, 0);
    const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0));
    const profitFactor = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? 99.0 : 0);
    const avgWin = wins.length > 0 ? (grossWin / wins.length) : 0;
    const avgLoss = losses.length > 0 ? (grossLoss / losses.length) : 0;
    const payoff = avgLoss > 0 ? (avgWin / avgLoss) : 0;

    return {
        tradesCount: trades.length,
        totalNetProfit: Math.round(totalNetProfit),
        winRatePct: Math.round(winRatePct * 100) / 100,
        profitFactor: Math.round(profitFactor * 1000) / 1000,
        avgWin: Math.round(avgWin),
        avgLoss: Math.round(avgLoss),
        payoff: Math.round(payoff * 100) / 100,
        trades: trades,
        equityCurve: equityCurve
    };
}

console.log('--- 測試 JS 回測引擎與 Python 對齊 ---');
const tDonchian = runFastBacktest(bars, { exit_mode: 'donchian', donchian_length: 4, bb_length: 30, bb_mult: 1.75 });
console.log('唐奇安 (N=4): 筆數 =', tDonchian.tradesCount, '淨利 =', tDonchian.totalNetProfit, '勝率 =', tDonchian.winRatePct + '%', 'PF =', tDonchian.profitFactor, '贏均 =', tDonchian.avgWin, '賺賠比 =', tDonchian.payoff);

const tSupertrend = runFastBacktest(bars, { exit_mode: 'supertrend', supertrend_length: 14, supertrend_multiplier: 2.0, bb_length: 30, bb_mult: 1.75 });
console.log('超級趨勢 (14, 2.0): 筆數 =', tSupertrend.tradesCount, '淨利 =', tSupertrend.totalNetProfit, '勝率 =', tSupertrend.winRatePct + '%', 'PF =', tSupertrend.profitFactor, '贏均 =', tSupertrend.avgWin, '賺賠比 =', tSupertrend.payoff);

const tAtr = runFastBacktest(bars, { exit_mode: 'atr', atr_length: 14, atr_sig_length: 5, bb_length: 30, bb_mult: 1.75 });
console.log('ATR基準 (14, 5): 筆數 =', tAtr.tradesCount, '淨利 =', tAtr.totalNetProfit, '勝率 =', tAtr.winRatePct + '%', 'PF =', tAtr.profitFactor, '贏均 =', tAtr.avgWin, '賺賠比 =', tAtr.payoff);
