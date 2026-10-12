import React, { useState, useEffect } from 'react';
import axios from 'axios';
import TradingChart from './components/TradingChart';
import type { ChartData } from './components/TradingChart';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

interface IntervalOption {
  key: string;
  label: string;
  group: 'minute' | 'hour' | 'day';
}

const INTERVALS: IntervalOption[] = [
  { key: '1m', label: '1分', group: 'minute' },
  { key: '2m', label: '2分', group: 'minute' },
  { key: '3m', label: '3分', group: 'minute' },
  { key: '5m', label: '5分', group: 'minute' },
  { key: '15m', label: '15分', group: 'minute' },
  { key: '30m', label: '30分', group: 'minute' },
  { key: '45m', label: '45分', group: 'minute' },
  { key: '1h', label: '1小時', group: 'hour' },
  { key: '2h', label: '2小時', group: 'hour' },
  { key: '4h', label: '4小時', group: 'hour' },
  { key: '1d', label: '1日', group: 'day' },
];

const App: React.FC = () => {
  const [selectedInterval, setSelectedInterval] = useState<string>('5m');
  const [chartData, setChartData] = useState<ChartData[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [activeBar, setActiveBar] = useState<ChartData | null>(null);

  // 載入指定週期的 K 線資料
  const fetchKlineData = async (interval: string) => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE_URL}/api/kline?interval=${interval}`);
      if (res.data && res.data.data && res.data.data.bars) {
        const bars: ChartData[] = res.data.data.bars;
        setChartData(bars);
        if (bars.length > 0) {
          setActiveBar(bars[bars.length - 1]);
        }
      }
    } catch (error) {
      console.error('獲取 K 線資料失敗:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchKlineData(selectedInterval);
  }, [selectedInterval]);

  // 當滑鼠在圖表移動時更新 activeBar
  const handleCrosshairMove = (bar: ChartData | null) => {
    if (bar) {
      setActiveBar(bar);
    } else if (chartData.length > 0) {
      setActiveBar(chartData[chartData.length - 1]);
    }
  };

  const currentBar = activeBar || (chartData.length > 0 ? chartData[chartData.length - 1] : null);
  const diff = currentBar ? currentBar.close - currentBar.open : 0;
  const pct = currentBar && currentBar.open > 0 ? (diff / currentBar.open) * 100 : 0;
  const isUp = diff >= 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', backgroundColor: '#0f0f0f', color: '#d1d4dc', overflow: 'hidden' }}>
      {/* 頂部工具列 */}
      <header style={{
        height: '52px',
        backgroundColor: '#141416',
        borderBottom: '1px solid #242731',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 16px',
        userSelect: 'none',
        zIndex: 10
      }}>
        {/* 商品代號 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '16px', fontWeight: 700, color: '#ffffff' }}>微型台指期 10</span>
          <span style={{ background: '#242731', color: '#089981', padding: '2px 8px', borderRadius: '4px', fontSize: '12px', fontWeight: 600 }}>TMFJ6</span>
          <span style={{ fontSize: '12px', color: '#6c7383' }}>SQLite 本地高頻時序 (689,139 筆秒K)</span>
        </div>

        {/* 週期切換按鈕群組 (1,2,3,5,15,30,45分 | 1,2,4小時 | 1日) */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
          background: '#191b22',
          padding: '4px',
          borderRadius: '6px',
          border: '1px solid #282c38'
        }}>
          {INTERVALS.map((opt) => {
            const isGroupStart = (opt.key === '1h' || opt.key === '1d');
            const isActive = selectedInterval === opt.key;
            return (
              <React.Fragment key={opt.key}>
                {isGroupStart && (
                  <div style={{ width: '1px', height: '18px', backgroundColor: '#333846', margin: '0 4px' }} />
                )}
                <button
                  onClick={() => setSelectedInterval(opt.key)}
                  style={{
                    background: isActive ? '#089981' : 'transparent',
                    color: isActive ? '#ffffff' : '#9299a8',
                    border: 'none',
                    fontSize: '13px',
                    fontWeight: 600,
                    padding: '5px 10px',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    boxShadow: isActive ? '0 0 8px rgba(8, 153, 129, 0.4)' : 'none',
                    transition: 'all 0.15s ease'
                  }}
                >
                  {opt.label}
                </button>
              </React.Fragment>
            );
          })}
        </div>

        {/* 狀態指示 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: '#089981' }}>
          <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#089981' }} />
          <span>資料庫連線中</span>
        </div>
      </header>

      {/* 圖表主要區域 */}
      <main style={{ flex: 1, position: 'relative', width: '100%', height: 'calc(100vh - 52px)', background: '#0f0f0f' }}>
        {/* 左上角即時開高低收數據顯示 */}
        <div style={{
          position: 'absolute',
          top: '14px',
          left: '18px',
          zIndex: 5,
          pointerEvents: 'none',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '14px',
          fontSize: '13px',
          fontWeight: 500,
          background: 'rgba(15, 15, 15, 0.75)',
          padding: '6px 12px',
          borderRadius: '6px',
          border: '1px solid rgba(255, 255, 255, 0.08)',
          backdropFilter: 'blur(4px)'
        }}>
          {currentBar ? (
            <>
              <div style={{ color: '#9299a8', fontSize: '12px' }}>
                {currentBar.time ? new Date(currentBar.time * 1000).toLocaleString('zh-TW', { hour12: false }) : '--'}
              </div>
              <div><span style={{ color: '#787b86' }}>開 </span><span style={{ fontWeight: 600, color: '#ffffff' }}>{currentBar.open.toFixed(1)}</span></div>
              <div><span style={{ color: '#787b86' }}>高 </span><span style={{ fontWeight: 600, color: '#ffffff' }}>{currentBar.high.toFixed(1)}</span></div>
              <div><span style={{ color: '#787b86' }}>低 </span><span style={{ fontWeight: 600, color: '#ffffff' }}>{currentBar.low.toFixed(1)}</span></div>
              <div><span style={{ color: '#787b86' }}>收 </span><span style={{ fontWeight: 600, color: isUp ? '#089981' : '#f23645' }}>{currentBar.close.toFixed(1)}</span></div>
              <div>
                <span style={{ color: '#787b86' }}>漲跌 </span>
                <span style={{ fontWeight: 600, color: isUp ? '#089981' : '#f23645' }}>
                  {isUp ? '+' : ''}{diff.toFixed(1)} ({isUp ? '+' : ''}{pct.toFixed(2)}%)
                </span>
              </div>
              <div><span style={{ color: '#787b86' }}>量 </span><span style={{ fontWeight: 600, color: '#ffffff' }}>{(currentBar.volume || 0).toLocaleString()}</span></div>
            </>
          ) : (
            <div style={{ color: '#787b86' }}>載入 K 線數據中...</div>
          )}
        </div>

        {/* 載入中遮罩 */}
        {loading && (
          <div style={{
            position: 'absolute',
            inset: 0,
            background: 'rgba(15, 15, 15, 0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 10,
            color: '#868993',
            fontSize: '14px'
          }}>
            載入 {selectedInterval} K 線資料中...
          </div>
        )}

        {/* TradingChart 元件 */}
        <TradingChart
          data={chartData}
          symbol={`TMFJ6_${selectedInterval}`}
          onCrosshairMove={handleCrosshairMove}
        />
      </main>
    </div>
  );
};

export default App;
