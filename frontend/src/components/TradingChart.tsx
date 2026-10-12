import React, { useEffect, useRef } from 'react';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { createChart, ColorType, CandlestickSeries, HistogramSeries } from 'lightweight-charts';

export interface ChartData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  datetime?: string;
}

interface TradingChartProps {
  data: ChartData[];
  symbol: string;
  onCrosshairMove?: (bar: ChartData | null) => void;
}

const TradingChart: React.FC<TradingChartProps> = ({ data, symbol, onCrosshairMove }) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Initialize Chart with TradingView Dark theme matching sampled image
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0f0f0f' },
        textColor: '#868993',
        fontSize: 12,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
      },
      grid: {
        vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
        horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
      },
      crosshair: {
        mode: 1, // CrosshairMode.Normal
        vertLine: {
          color: '#758696',
          width: 1,
          style: 3, // Dashed
          labelBackgroundColor: '#2a2e39'
        },
        horzLine: {
          color: '#758696',
          width: 1,
          style: 3, // Dashed
          labelBackgroundColor: '#2a2e39'
        }
      },
      rightPriceScale: {
        borderColor: '#242731',
        scaleMargins: {
          top: 0.1,
          bottom: 0.22,
        },
      },
      timeScale: {
        borderColor: '#242731',
        timeVisible: true,
        secondsVisible: false,
      },
      autoSize: true,
    });

    chartRef.current = chart;

    // Candlestick Series: Teal Green (#089981) and Rose Red (#f23645)
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#089981',
      downColor: '#f23645',
      borderVisible: true,
      borderUpColor: '#089981',
      borderDownColor: '#f23645',
      wickVisible: true,
      wickUpColor: '#089981',
      wickDownColor: '#f23645',
    });
    candleSeriesRef.current = candleSeries;

    // Volume Series
    const volSeries = chart.addSeries(HistogramSeries, {
      color: '#26a69a',
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: '',
    });
    chart.priceScale('').applyOptions({
      scaleMargins: {
        top: 0.82,
        bottom: 0,
      },
    });
    volumeSeriesRef.current = volSeries;

    // Crosshair listener for top-left OHLC display
    chart.subscribeCrosshairMove((param) => {
      if (!onCrosshairMove) return;
      if (
        param.point === undefined ||
        !param.time ||
        !chartContainerRef.current ||
        param.point.x < 0 ||
        param.point.x > chartContainerRef.current.clientWidth ||
        param.point.y < 0 ||
        param.point.y > chartContainerRef.current.clientHeight
      ) {
        onCrosshairMove(null); // Revert to latest candle
      } else {
        const barData = param.seriesData.get(candleSeries) as any;
        const volData = param.seriesData.get(volSeries) as any;
        if (barData) {
          onCrosshairMove({
            time: Number(barData.time),
            open: barData.open,
            high: barData.high,
            low: barData.low,
            close: barData.close,
            volume: volData ? volData.value : 0,
          });
        }
      }
    });

    return () => {
      chart.remove();
    };
  }, []);

  // Update Data
  useEffect(() => {
    if (candleSeriesRef.current && data.length > 0) {
      const candleBars = data.map((d) => ({
        time: d.time as Time,
        open: d.open,
        high: d.high,
        low: d.low,
        close: d.close,
      }));

      const volBars = data.map((d) => ({
        time: d.time as Time,
        value: d.volume || 0,
        color: d.close >= d.open ? 'rgba(8, 153, 129, 0.45)' : 'rgba(242, 54, 69, 0.45)',
      }));

      candleSeriesRef.current.setData(candleBars as any);
      if (volumeSeriesRef.current) {
        volumeSeriesRef.current.setData(volBars as any);
      }

      if (candleBars.length > 150) {
        chartRef.current?.timeScale().setVisibleLogicalRange({
          from: candleBars.length - 120,
          to: candleBars.length,
        });
      } else {
        chartRef.current?.timeScale().fitContent();
      }
    }
  }, [data, symbol]);

  return <div ref={chartContainerRef} style={{ width: '100%', height: '100%' }} />;
};

export default TradingChart;
