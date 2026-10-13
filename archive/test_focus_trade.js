const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');

const EDGE_PATH = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9222;
const TARGET_URL = "file:///C:/Users/User/.gemini/antigravity/scratch/Quant/kline_dashboard.html";
const ARTIFACT_DIR = "C:\\Users\\User\\.gemini\\antigravity\\brain\\6f811917-cdd9-444f-a8ee-2dec79aad0cb";

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
    const tempProfile = path.join(__dirname, 'temp_edge_profile_focus');
    const edgeProcess = spawn(EDGE_PATH, [
        `--remote-debugging-port=${PORT}`,
        '--headless=new',
        '--disable-gpu',
        '--window-size=1920,1080',
        '--no-first-run',
        '--no-default-browser-check',
        '--user-data-dir=' + tempProfile
    ]);

    let targets = null;
    for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 200));
        try {
            targets = await fetchJson(`http://127.0.0.1:${PORT}/json`);
            if (targets && targets.length > 0) break;
        } catch (e) {}
    }

    const pageTarget = targets.find(t => t.type === 'page') || targets[0];
    const ws = new WebSocket(pageTarget.webSocketDebuggerUrl);

    let msgId = 1;
    const pendingCallbacks = new Map();
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
        }
    };

    await new Promise(r => ws.onopen = r);
    await sendCmd('Runtime.enable');
    await sendCmd('Page.enable');

    await sendCmd('Page.navigate', { url: TARGET_URL });
    await new Promise(r => setTimeout(r, 3000));

    // 1. 切換至交易明細清單分頁
    console.log('[1] 切換至交易明細清單分頁 (tab-trades-table)...');
    await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const tab = document.getElementById('tab-trades-table');
            if (tab) tab.click();
        })()`
    });
    await new Promise(r => setTimeout(r, 500));

    // 截圖: 交易明細表格
    const shotTable = await sendCmd('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(ARTIFACT_DIR, 'trades_table_view.png'), Buffer.from(shotTable.data, 'base64'));
    console.log('已儲存交易明細表格截圖: trades_table_view.png');

    // 2. 點擊第一筆交易的 [定位] 按鈕
    console.log('[2] 點擊第 1 筆交易的「定位」按鈕...');
    const focusResult = await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const firstBtn = document.querySelector('.btn-focus-trade');
            if (!firstBtn) return { error: '找不到定位按鈕' };
            firstBtn.click();
            
            // 檢查點擊後狀態
            const split = document.getElementById('workspace-split');
            const isSolo = split ? split.classList.contains('solo-data-mode') : null;
            const klineSec = document.getElementById('kline-section');
            const klineDisplay = klineSec ? window.getComputedStyle(klineSec).display : null;
            
            return {
                isSolo,
                klineDisplay
            };
        })()`,
        returnByValue: true
    });
    console.log('點擊定位後狀態:', focusResult.result.value);

    // 等待 1 秒
    await new Promise(r => setTimeout(r, 1000));

    // 檢查 K 線可視範圍
    const rangeResult = await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const range = window.klineChart ? window.klineChart.timeScale().getVisibleRange() : null;
            return { visibleRange: range };
        })()`,
        returnByValue: true
    });
    console.log('K 線當前可視範圍:', rangeResult.result.value);

    // 截圖: 點擊定位後的畫面
    const shotFocus = await sendCmd('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(ARTIFACT_DIR, 'after_focus_trade_1.png'), Buffer.from(shotFocus.data, 'base64'));
    console.log('已儲存點擊定位後截圖: after_focus_trade_1.png');

    ws.close();
    edgeProcess.kill();
}

main().catch(console.error);
