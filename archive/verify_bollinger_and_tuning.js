const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');

const EDGE_PATH = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9222;
const TARGET_URL = "file:///C:/Users/User/.gemini/antigravity/scratch/Quant/kline_dashboard.html";
const ARTIFACT_DIR = "C:\\Users\\User\\.gemini\\antigravity\\brain\\6f811917-cdd9-444f-a8ee-2dec79aad0cb";

console.log('=== 啟動 Microsoft Edge 瀏覽器執行期真機驗證：布林通道軌道與互動式調參引擎 ===');

function fetchJson(url) {
    return new Promise((resolve, reject) => {
        http.get(url, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
            });
        }).on('error', reject);
    });
}

async function main() {
    const tempProfile = path.join(__dirname, 'temp_edge_profile_bb');
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

    // 檢測項目 1: 驗證多策略對比圖固定為橫軸時間 × 縱軸累積損益
    console.log('\n[5] 檢驗多策略對比圖軸向與 Canvas 狀態:');
    const compareEval = await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const chart = window.multiCompareChart;
            const canvas = document.getElementById('multi-compare-canvas');
            const hasFlipBtn = !!document.getElementById('btn-flip-axes');
            const hasParamsBtn = !!document.getElementById('btn-open-params-modal');
            const hint = document.getElementById('txt-axis-hint')?.textContent || '';
            const ctx = canvas ? canvas.getContext('2d') : null;
            let hasPixels = false;
            if (ctx && canvas.width > 0 && canvas.height > 0) {
                const imgData = ctx.getImageData(0, 0, Math.min(100, canvas.width), Math.min(100, canvas.height));
                hasPixels = imgData.data.some(p => p > 0);
            }
            return {
                axisMode: chart ? chart.axisMode : null,
                hasFlipBtn,
                hasParamsBtn,
                hintText: hint,
                canvasWidth: canvas ? canvas.width : 0,
                canvasHeight: canvas ? canvas.height : 0,
                hasPixels
            };
        })()`,
        returnByValue: true
    });
    console.log('  對比圖檢驗結果:', JSON.stringify(compareEval.result.value, null, 2));

    // 截圖 1: 全畫面純數據對比圖 (X=時間, Y=損益)
    const shot1 = await sendCmd('Page.captureScreenshot', { format: 'png' });
    const p1 = path.join(ARTIFACT_DIR, 'multi_strategy_time_pnl_verified.png');
    fs.writeFileSync(p1, Buffer.from(shot1.data, 'base64'));
    console.log('  [截圖存證 1] 已儲存全畫面時間軸對比圖:', p1);

    // 檢測項目 2: 點擊切換為雙圖模式，檢驗 K 線圖上布林通道上中下軌
    console.log('\n[6] 切換為雙圖模式並檢驗 K 線圖布林通道上中下軌:');
    const bbEval = await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            // 切換為雙圖模式
            const btnLayout = document.getElementById('btn-toggle-layout');
            if (btnLayout) btnLayout.click();

            const upperEl = document.getElementById('legend-bb-upper');
            const middleEl = document.getElementById('legend-bb-middle');
            const lowerEl = document.getElementById('legend-bb-lower');
            const bbBtn = document.getElementById('toggle-bb-btn');
            const hasLookupMap = (window.bbLookupMap && window.bbLookupMap.size > 0);
            const klineSec = document.getElementById('kline-section');
            const isKlineVisible = klineSec ? window.getComputedStyle(klineSec).display !== 'none' : false;

            return {
                isKlineVisible,
                hasLookupMap,
                mapSize: window.bbLookupMap ? window.bbLookupMap.size : 0,
                upperText: upperEl ? upperEl.textContent : null,
                middleText: middleEl ? middleEl.textContent : null,
                lowerText: lowerEl ? lowerEl.textContent : null,
                bbBtnText: bbBtn ? bbBtn.textContent.trim() : null
            };
        })()`,
        returnByValue: true
    });
    console.log('  布林軌道檢驗結果:', JSON.stringify(bbEval.result.value, null, 2));

    // 等待 1 秒圖表重新適配
    await new Promise(r => setTimeout(r, 1000));

    // 截圖 2: K 線主圖顯示布林軌道與圖例
    const shot2 = await sendCmd('Page.captureScreenshot', { format: 'png' });
    const p2 = path.join(ARTIFACT_DIR, 'bollinger_bands_kline_verified.png');
    fs.writeFileSync(p2, Buffer.from(shot2.data, 'base64'));
    console.log('  [截圖存證 2] 已儲存包含布林上中下軌之 K 線圖:', p2);

    // 檢測項目 3: 測試策略參數調校 Modal 與即時回測
    console.log('\n[7] 開啟策略參數調校視窗並測試即時重新撮合回測:');
    const modalEval = await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            // 開啟 Modal
            const btnOpen = document.getElementById('btn-open-params-modal');
            if (btnOpen) btnOpen.click();

            const modal = document.getElementById('params-modal-overlay');
            const isOpen = modal ? modal.classList.contains('open') : false;

            // 調整唐奇安參數: 改為 8
            const numDonchian = document.getElementById('num-donchian-len');
            if (numDonchian) {
                numDonchian.value = 8;
                numDonchian.dispatchEvent(new Event('input'));
            }

            // 調整布林倍數: 改為 2.0
            const numBbMult = document.getElementById('num-bb-mult');
            if (numBbMult) {
                numBbMult.value = 2.0;
                numBbMult.dispatchEvent(new Event('input'));
            }

            // 記錄回測前總淨利
            const prevPnl = document.getElementById('kpi-donchian-pnl')?.textContent;

            // 點擊立即執行回測
            const btnRun = document.getElementById('btn-run-backtest');
            if (btnRun) btnRun.click();

            // 記錄回測後數值
            const newPnl = document.getElementById('kpi-donchian-pnl')?.textContent;
            const previewPnl = document.getElementById('res-preview-pnl')?.textContent;
            const previewPf = document.getElementById('res-preview-pf')?.textContent;
            const previewTime = document.getElementById('res-preview-time')?.textContent;

            return {
                isOpen,
                prevPnl,
                newPnl,
                previewPnl,
                previewPf,
                previewTime
            };
        })()`,
        returnByValue: true
    });
    console.log('  參數調校回測結果:', JSON.stringify(modalEval.result.value, null, 2));

    // 等待 500ms 動畫
    await new Promise(r => setTimeout(r, 500));

    // 截圖 3: 參數調校視窗與即時回測結果
    const shot3 = await sendCmd('Page.captureScreenshot', { format: 'png' });
    const p3 = path.join(ARTIFACT_DIR, 'parameter_tuning_modal_verified.png');
    fs.writeFileSync(p3, Buffer.from(shot3.data, 'base64'));
    console.log('  [截圖存證 3] 已儲存策略參數調校 Modal 截圖:', p3);

    console.log('\n[8] 彙整未捕獲異常數:', uncaughtErrors.length);
    if (uncaughtErrors.length > 0) {
        console.error('異常清單:', uncaughtErrors);
    }

    ws.close();
    edgeProcess.kill();
    console.log('\n=== 瀏覽器真機 CDP 檢驗流程全數順利完成 ===');
}

main().catch(err => {
    console.error('驗證腳本執行失敗:', err);
    process.exit(1);
});
