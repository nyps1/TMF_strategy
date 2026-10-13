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
    const tempProfile = path.join(__dirname, 'temp_edge_profile_zoom1');
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

    // 切換雙圖
    await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const btn = document.getElementById('btn-toggle-layout');
            if (btn) btn.click();
        })()`
    });
    await new Promise(r => setTimeout(r, 1000));

    // 專門把時間範圍放大到 Trade 1 附近: 2026-09-15 21:00:00 (1789506000) 到 2026-09-16 01:00:00 (1789520400)
    console.log('設定時間範圍到 Trade 1 (2026-09-15 21:00 ~ 2026-09-16 01:00)...');
    const rangeRes = await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const chart = window.klineChart;
            if (!chart) return { error: 'no chart' };
            const fromSec = 1789506000;
            const toSec = 1789520400;
            chart.timeScale().setVisibleRange({ from: fromSec, to: toSec });
            return {
                setRange: { from: fromSec, to: toSec },
                actualRange: chart.timeScale().getVisibleRange()
            };
        })()`,
        returnByValue: true
    });
    console.log('設定結果:', rangeRes.result.value);

    await new Promise(r => setTimeout(r, 500));

    // 截圖: Trade 1 特寫
    const shot = await sendCmd('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(ARTIFACT_DIR, 'trade_1_zoomed_view.png'), Buffer.from(shot.data, 'base64'));
    console.log('已儲存 Trade 1 特寫截圖: trade_1_zoomed_view.png');

    ws.close();
    edgeProcess.kill();
}

main().catch(console.error);
