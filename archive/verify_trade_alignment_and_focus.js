const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');

const EDGE_PATH = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9223;
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
    const tempProfile = path.join(__dirname, 'temp_edge_profile_verify_focus');
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

    console.log('正在載入儀表板頁面...');
    await sendCmd('Page.navigate', { url: TARGET_URL });
    await new Promise(r => setTimeout(r, 3000));

    // 1. 切換至交易明細清單分頁
    console.log('1. 切換至交易明細清單分頁...');
    await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const tab = document.getElementById('tab-trades-table');
            if (tab) tab.click();
        })()`
    });
    await new Promise(r => setTimeout(r, 500));

    // 2. 點擊 Trade #1 定位按鈕
    console.log('2. 點擊 Trade #1 定位按鈕...');
    const focusResult = await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const firstBtn = document.querySelector('#trades-table-body tr button.btn-focus-trade');
            if (!firstBtn) return { error: '找不到定位按鈕' };
            firstBtn.click();
            return { success: true };
        })()`,
        returnByValue: true
    });
    console.log('點擊結果:', focusResult.result.value);

    // 等待平滑展開與聚焦延遲
    await new Promise(r => setTimeout(r, 1200));

    // 3. 檢查聚焦狀態與可視範圍
    console.log('3. 檢查聚焦狀態與可視範圍...');
    const checkState = await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const chart = window.klineChart;
            const banner = document.getElementById('trade-focus-banner');
            const split = document.getElementById('workspace-split');
            const selectedRow = document.querySelector('#trades-table-body tr.selected-trade-row');
            const range = chart ? chart.timeScale().getVisibleRange() : null;

            return {
                isSoloDataMode: split.classList.contains('solo-data-mode'),
                bannerVisible: banner ? banner.style.display !== 'none' : false,
                bannerText: banner ? banner.innerText : '',
                selectedRowId: selectedRow ? selectedRow.id : null,
                visibleRange: range,
                barsSpanHours: range ? ((range.to - range.from) / 3600).toFixed(1) : null
            };
        })()`,
        returnByValue: true
    });
    console.log('聚焦狀態檢核結果:', checkState.result.value);

    // 4. 截圖存證：Trade #1 聚焦特寫視圖
    const shot1 = await sendCmd('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(ARTIFACT_DIR, 'trade_1_focused_verified.png'), Buffer.from(shot1.data, 'base64'));
    console.log('已儲存 Trade 1 聚焦特寫截圖: trade_1_focused_verified.png');

    // 5. 驗證前端即時回測引擎的 times[posEntryIdx] 時間戳吻合度
    console.log('5. 測試前端快速回測引擎時間戳...');
    const backtestTsCheck = await sendCmd('Runtime.evaluate', {
        expression: `(() => {
            const bars = KLINE_DATA['5m'];
            const res = runFastBacktest(bars, {
                exit_mode: 'donchian',
                donchian_length: 4,
                bb_length: 30,
                bb_mult: 1.75
            });

            // 隨機抽樣比對 5 筆交易之 entry_ts 是否精確等於 bars 對應 datetime 的時間戳
            const barMap = new Map();
            bars.forEach(b => barMap.set(b.datetime, Number(b.time)));

            const checks = [];
            for (let i = 0; i < Math.min(5, res.trades.length); i++) {
                const t = res.trades[i];
                const expectedEntryTs = barMap.get(t.entry_time);
                const expectedExitTs = barMap.get(t.exit_time);
                checks.push({
                    trade_id: t.trade_id,
                    entry_time: t.entry_time,
                    entry_ts: t.entry_ts,
                    expectedEntryTs: expectedEntryTs,
                    entryDiffSec: t.entry_ts - expectedEntryTs,
                    exitDiffSec: t.exit_ts - expectedExitTs
                });
            }
            return {
                totalTrades: res.trades.length,
                samples: checks
            };
        })()`,
        returnByValue: true
    });
    console.log('前端回測時間戳對齊結果 (差異秒數應為 0):', JSON.stringify(backtestTsCheck.result.value, null, 2));

    ws.close();
    edgeProcess.kill();
    console.log('真機驗證腳本執行完畢！');
}

main().catch(console.error);
