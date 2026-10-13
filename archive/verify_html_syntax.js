const fs = require('fs');
const vm = require('vm');

console.log('=== 開始檢驗 kline_dashboard.html 語法與結構完整性 ===');

if (!fs.existsSync('kline_dashboard.html')) {
    console.error('ERROR: kline_dashboard.html 檔案不存在!');
    process.exit(1);
}

const html = fs.readFileSync('kline_dashboard.html', 'utf8');
console.log('檔案大小:', (html.length / (1024 * 1024)).toFixed(2), 'MB');

// 檢查關鍵 DOM 元素 ID
const requiredElementIds = [
    'top-toolbar',
    'kline-container',
    'perf-chart-wrapper',
    'perf-chart-container',
    'trade-equity-wrapper',
    'trade-equity-canvas',
    'trades-table-wrapper',
    'tab-trade-equity',
    'tab-cum-pnl',
    'tab-trades-table',
    'btn-flip-axes',
    'btn-toggle-deltas',
    'btn-fit-trade-chart',
    'trade-tooltip'
];

console.log('\n[1] 檢查關鍵 DOM 元素是否存在於 HTML 中:');
let missingDom = 0;
requiredElementIds.forEach(id => {
    const exists = html.includes(`id="${id}"`) || html.includes(`class="${id}"`);
    console.log(`  - 元素 #${id}: ${exists ? 'OK (存在)' : 'FAILED (缺失)'}`);
    if (!exists) missingDom++;
});

if (missingDom > 0) {
    console.error(`ERROR: 發現 ${missingDom} 個關鍵 DOM 元素缺失!`);
    process.exit(1);
}

// 提取內嵌腳本並檢驗語法
console.log('\n[2] 檢驗內嵌 JavaScript 腳本語法 (Node vm.Script 編譯):');
const scriptRegex = /<script(?![^>]*src=)[\s\S]*?>([\s\S]*?)<\/script>/gi;
let match;
let scriptIndex = 1;
let hasError = false;

while ((match = scriptRegex.exec(html)) !== null) {
    const code = match[1];
    console.log(`  檢查 Script #${scriptIndex} (長度: ${code.length.toLocaleString()} 字元)...`);
    try {
        new vm.Script(code, { filename: `inline_script_${scriptIndex}.js` });
        console.log(`  -> Script #${scriptIndex}: 語法編譯完全正常 (No SyntaxError)`);
    } catch (err) {
        console.error(`  -> Script #${scriptIndex} 語法錯誤 (SyntaxError):`, err.message);
        console.error(`     行號位置: ${err.stack.split('\n')[0]}`);
        hasError = true;
    }
    scriptIndex++;
}

if (hasError) {
    console.error('ERROR: JavaScript 代碼中存在語法錯誤!');
    process.exit(1);
}

// 檢驗關鍵全域變數資料完整性
console.log('\n[3] 檢驗關鍵全域資料 JSON 結構 (KLINE_DATA, RAW_MARKERS, TRADES_DATA 等):');

function checkJsonVariable(varName) {
    const pattern = new RegExp(`(?:const|let|var)\\s+${varName}\\s*=\\s*([\\{\\[][\\s\\S]*?[\\}\\]]);`, 'i');
    const m = pattern.exec(html);
    if (!m) {
        console.error(`  - ${varName}: 未找到宣告或賦值!`);
        return false;
    }
    try {
        const parsed = JSON.parse(m[1]);
        const count = Array.isArray(parsed) ? parsed.length : Object.keys(parsed).length;
        console.log(`  - ${varName}: 成功解析為有效 JSON! (項目筆數/維度: ${count})`);
        return true;
    } catch (e) {
        console.error(`  - ${varName}: JSON 解析失敗:`, e.message);
        return false;
    }
}

const varsToCheck = ['KLINE_DATA', 'RAW_MARKERS', 'CUMULATIVE_PNL', 'METRICS', 'TRADES_DATA'];
let jsonErrors = 0;
varsToCheck.forEach(v => {
    if (!checkJsonVariable(v)) jsonErrors++;
});

if (jsonErrors > 0) {
    console.error('ERROR: 資料結構存在 JSON 格式錯誤!');
    process.exit(1);
}

console.log('\n=== 靜態語法與資料結構驗證 100% 通過！ ===');
