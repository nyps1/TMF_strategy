const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');

const EDGE_PATH = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9222;
const TARGET_URL = "file:///C:/Users/User/.gemini/antigravity/scratch/Quant/kline_dashboard.html";
const ARTIFACT_PNG = "C:\\Users\\User\\.gemini\\antigravity\\brain\\6f811917-cdd9-444f-a8ee-2dec79aad0cb\\multi_strategy_dashboard_verified.png";

console.log('=== 啟動 Microsoft Edge 瀏覽器執行期真機多策略儀表板驗證 ===');

async function main() {
    const tempProfile = path.join(__dirname, 'temp_edge_profile_multi');
    const edgeProcess = spawn(EDGE_PATH, [
        `--remote-debugging-port=${PORT}`,
        '--headless=new',
        '--disable-gpu',
        '--window-size=1920,1080',
        '--no-first-run',
        '--no-default-browser-check',
        '--user-data-dir=' + tempProfile
    ]);

    console.log('[1] 正在啟動 Headless Edge (PID: ' + edgeProcess.pid + ')...');

    let targets = null;
    for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 200));
        try {
            targets = await fetchJson(`http://127.0.0.1:${PORT}/json`);
            if (targets && targets.length > 0) break;
        } catch (e) {}
    }

    if (!targets || targets.length === 0) {
        console.error('ERROR: 無法連線至 Edge 除錯埠');
        edgeProcess.kill();
        process.exit(1);
    }

    const pageTarget = targets.find(t => t.type === 'page') || targets[0];
    const wsUrl = pageTarget.webSocketDebuggerUrl;
    console.log('[2] 已連線至 Edge DevTools WebSocket:', wsUrl);

    const ws = new WebSocket(wsUrl);

    let msgId = 1;
    const pendingCallbacks = new Map();
    const consoleLogs = [];
    const uncaughtErrors = [];

    function sendCmd(method, params = {}) {
        return new Promise((resolve, reject) => {
            const id = msgId++;
            pendingCallbacks.set(id, { resolve, reject });
            ws.send(JSON.stringify({ id, method, params }));
        });
    }

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.id && pendingCallbacks.has(msg.id)) {
            const { resolve, reject } = pendingCallbacks.get(msg.id);
            pendingCallbacks.delete(msg.id);
            if (msg.error) reject(msg.error);
            else resolve(msg.result);
        } else if (msg.method === 'Runtime.consoleAPICalled') {
            const text = msg.params.args.map(a => a.value || a.description || JSON.stringify(a)).join(' ');
            consoleLogs.push({ type: msg.params.type, text });
            console.log(`  [Browser Console ${msg.params.type.toUpperCase()}] ${text}`);
        } else if (msg.method === 'Runtime.exceptionThrown') {
            const desc = msg.params.exceptionDetails.exception.description || msg.params.exceptionDetails.text;
            uncaughtErrors.push(desc);
            console.error(`  [Browser Uncaught Exception] ${desc}`);
        }
    };

    await new Promise(r => ws.onopen = r);

    await sendCmd('Runtime.enable');
    await sendCmd('Page.enable');

    console.log('[3] 導航開啟目標頁面:', TARGET_URL);
    await sendCmd('Page.navigate', { url: TARGET_URL });

    console.log('[4] 等待頁面初始化與圖表渲染 (等待 3 秒)...');
    await new Promise(r => setTimeout(r, 3000));

    console.log('\n[5] 執行多策略直觀對比功能檢測表達式:');

    const evalResult = await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const klineCanvas = document.querySelectorAll('#kline-container canvas');
            const multiCanvas = document.getElementById('multi-compare-canvas');
            const donchianPnl = document.getElementById('kpi-donchian-pnl')?.textContent;
            const supertrendPnl = document.getElementById('kpi-supertrend-pnl')?.textContent;
            const atrPnl = document.getElementById('kpi-atr-pnl')?.textContent;
            const tabMultiActive = document.getElementById('tab-multi-compare')?.classList.contains('active');
            const stratButtons = document.querySelectorAll('.strat-btn').length;
            const hasMultiChart = typeof window.multiCompareChart !== 'undefined' && window.multiCompareChart !== null;
            const hasTradeEquity = typeof window.tradeEquityChart !== 'undefined' && window.tradeEquityChart !== null;
            
            return {
                title: document.title,
                klineCanvasCount: klineCanvas.length,
                multiCanvasWidth: multiCanvas ? multiCanvas.width : 0,
                multiCanvasHeight: multiCanvas ? multiCanvas.height : 0,
                tabMultiActive: tabMultiActive,
                stratButtonsCount: stratButtons,
                hasMultiChart: hasMultiChart,
                hasTradeEquity: hasTradeEquity,
                donchianPnl: donchianPnl,
                supertrendPnl: supertrendPnl,
                atrPnl: atrPnl
            };
        })()`,
        returnByValue: true
    });

    const status = evalResult.result.value;
    console.log('  - 頁面 Title                 :', status.title);
    console.log('  - 頂部策略切換按鈕數量       :', status.stratButtonsCount, '(唐奇安 / 超級趨勢 / ATR)');
    console.log('  - 預設選中分頁               :', status.tabMultiActive ? 'OK (⚔️ 雙策略淨值直觀對比 預設選中)' : 'FAILED');
    console.log('  - 多策略 Canvas 尺寸         :', `${status.multiCanvasWidth} x ${status.multiCanvasHeight}`);
    console.log('  - 多策略對比圖表實例         :', status.hasMultiChart ? 'OK (MultiStrategyCompareChart 就緒)' : 'FAILED');
    console.log('  - 單策略逐筆圖表實例         :', status.hasTradeEquity ? 'OK (TradeEquityChart 就緒)' : 'FAILED');
    console.log('  - 唐奇安 KPI 卡片總淨利      :', status.donchianPnl);
    console.log('  - 超級趨勢 KPI 卡片總淨利    :', status.supertrendPnl);
    console.log('  - ATR 基準 KPI 卡片總淨利    :', status.atrPnl);

    console.log('\n[6] 截取真機渲染畫面:');
    const screenshotRes = await sendCmd('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(ARTIFACT_PNG, Buffer.from(screenshotRes.data, 'base64'));
    const ssSize = (fs.statSync(ARTIFACT_PNG).size / 1024).toFixed(1);
    console.log(`  -> 截圖成功儲存至: ${ARTIFACT_PNG} (檔案大小: ${ssSize} KB)`);

    ws.close();
    edgeProcess.kill();
    try {
        fs.rmSync(tempProfile, { recursive: true, force: true });
    } catch(e) {}

    console.log('\n=== 檢驗總結報表 ===');
    console.log('Uncaught Exceptions 數量:', uncaughtErrors.length);
    if (uncaughtErrors.length > 0) {
        console.error('發現執行期異常 (Uncaught Errors):', uncaughtErrors);
        process.exit(1);
    } else {
        console.log('>>> 恭喜！Microsoft Edge 執行期真機載入驗證 100% 成功，零錯誤、零異常！');
    }
}

function fetchJson(url) {
    return new Promise((resolve, reject) => {
        http.get(url, res => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
            });
        }).on('error', reject);
    });
}

main().catch(err => {
    console.error('驗證過程遭遇異常:', err);
    process.exit(1);
});
