import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineStyle,
  LineSeries,
  createChart,
} from "lightweight-charts";
import type {
  CandlestickData,
  HistogramData,
  IChartApi,
  LineData,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";

export interface MarketCandleBar {
  time: string | number;
  label: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  amount?: number;
}

interface MovingAverageValue {
  window: number;
  value: number | null;
  color: string;
}

const UP_COLOR = "#e63535";
const DOWN_COLOR = "#08915f";
const MA_CONFIG = [
  { window: 5, color: "#d49b00" },
  { window: 10, color: "#7c5ce7" },
  { window: 20, color: "#2477d4" },
] as const;

export function MarketKLineChart({
  bars,
  emptyLabel,
  mode,
  referencePrice,
}: {
  bars: MarketCandleBar[];
  emptyLabel: string;
  mode: "daily" | "intraday";
  referencePrice?: number | null;
}) {
  const chartContainerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [activeBar, setActiveBar] = useState<MarketCandleBar | null>(null);
  const orderedBars = useMemo(
    () => [...bars].sort((left, right) => chartTimeKey(left.time).localeCompare(chartTimeKey(right.time))),
    [bars],
  );
  const latestBar = orderedBars[orderedBars.length - 1] ?? null;
  const displayedBar = activeBar ?? latestBar;
  const displayedIndex = displayedBar
    ? orderedBars.findIndex((item) => chartTimeKey(item.time) === chartTimeKey(displayedBar.time))
    : -1;
  const intradayReferencePrice = mode === "intraday" && referencePrice && referencePrice > 0
    ? referencePrice
    : null;
  const comparisonPrice = mode === "intraday"
    ? intradayReferencePrice
    : displayedIndex > 0
      ? orderedBars[displayedIndex - 1].close
      : null;
  const changePct =
    displayedBar && comparisonPrice
      ? ((displayedBar.close / comparisonPrice) - 1) * 100
      : null;
  const intradayAverage = mode === "intraday"
    ? cumulativeAverageAt(orderedBars, displayedIndex)
    : null;
  const movingAverages = useMemo(
    () => movingAverageReadout(orderedBars, displayedIndex),
    [displayedIndex, orderedBars],
  );
  const volumeRatios = useMemo(
    () => rollingVolumeRatios(orderedBars, 5),
    [orderedBars],
  );
  const displayedVolumeRatio =
    displayedIndex >= 0 ? volumeRatios[displayedIndex] : null;

  useEffect(() => {
    setActiveBar(null);
  }, [bars, mode]);

  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container || orderedBars.length === 0) {
      return undefined;
    }

    const chart = createChart(container, {
      autoSize: true,
      height: 470,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        attributionLogo: false,
        textColor: "#667085",
        fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif",
        fontSize: 11,
        panes: {
          enableResize: false,
          separatorColor: "#edf0f4",
          separatorHoverColor: "#edf0f4",
        },
      },
      grid: {
        vertLines: { color: "#f1f3f6" },
        horzLines: { color: "#edf0f4" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: "#98a2b3",
          width: 1,
          labelBackgroundColor: "#344054",
        },
        horzLine: {
          color: "#98a2b3",
          width: 1,
          labelBackgroundColor: "#344054",
        },
      },
      rightPriceScale: {
        borderColor: "#e4e7ec",
        entireTextOnly: true,
        minimumWidth: 62,
      },
      timeScale: {
        borderColor: "#e4e7ec",
        timeVisible: mode === "intraday",
        secondsVisible: false,
        rightOffset: mode === "daily" ? 2 : 1,
        barSpacing: mode === "daily" ? 8 : 6,
        minBarSpacing: 3,
        fixLeftEdge: true,
        tickMarkFormatter: (time: Time) => formatAxisTime(time, mode),
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: false,
      localization: {
        locale: "zh-CN",
        priceFormatter: (price: number) => price.toFixed(2),
        timeFormatter: (time: Time) => formatCrosshairTime(time, mode),
      },
    });
    chartRef.current = chart;

    if (mode === "daily") {
      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: UP_COLOR,
        downColor: DOWN_COLOR,
        borderUpColor: UP_COLOR,
        borderDownColor: DOWN_COLOR,
        wickUpColor: UP_COLOR,
        wickDownColor: DOWN_COLOR,
        priceLineVisible: true,
        priceLineColor: "#98a2b3",
        priceLineWidth: 1,
        lastValueVisible: true,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      });
      const candleData: CandlestickData<Time>[] = orderedBars.map((bar) => ({
        time: toChartTime(bar.time),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      }));
      candleSeries.setData(candleData);

      for (const config of MA_CONFIG) {
        const series = chart.addSeries(LineSeries, {
          color: config.color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        series.setData(movingAverageLine(orderedBars, config.window));
      }
    } else {
      const priceSeries = chart.addSeries(LineSeries, {
        color: "#246bfd",
        lineWidth: 2,
        priceLineVisible: true,
        priceLineColor: "#246bfd",
        lastValueVisible: true,
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 4,
        crosshairMarkerBorderColor: "#ffffff",
        crosshairMarkerBackgroundColor: "#246bfd",
        priceFormat: intradayReferencePrice
          ? {
              type: "custom",
              minMove: 0.01,
              formatter: (price: number) => formatPercent(
                ((price / intradayReferencePrice) - 1) * 100,
              ),
            }
          : { type: "price", precision: 2, minMove: 0.01 },
      });
      priceSeries.setData(orderedBars.map((bar) => ({
        time: toChartTime(bar.time),
        value: bar.close,
      })));
      if (intradayReferencePrice) {
        priceSeries.createPriceLine({
          price: intradayReferencePrice,
          color: "#98a2b3",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: "0%",
        });
      }

      const averageSeries = chart.addSeries(LineSeries, {
        color: "#e5a000",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        priceFormat: intradayReferencePrice
          ? {
              type: "custom",
              minMove: 0.01,
              formatter: (price: number) => formatPercent(
                ((price / intradayReferencePrice) - 1) * 100,
              ),
            }
          : { type: "price", precision: 2, minMove: 0.01 },
      });
      averageSeries.setData(intradayAverageLine(orderedBars));

      const bounds = intradayPriceBounds(orderedBars, intradayReferencePrice);
      const scaleGuardSeries = chart.addSeries(LineSeries, {
        color: "rgba(0, 0, 0, 0)",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      scaleGuardSeries.setData([
        { time: toChartTime(orderedBars[0].time), value: bounds.low },
        { time: toChartTime(orderedBars[orderedBars.length - 1].time), value: bounds.high },
      ]);
    }

    const secondarySeries = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: mode === "daily"
          ? {
              type: "custom",
              minMove: 0.01,
              formatter: (value: number) => `${value.toFixed(2)}x`,
            }
          : { type: "volume" },
        priceLineVisible: false,
        lastValueVisible: false,
      },
      1,
    );
    const secondaryData: HistogramData<Time>[] = mode === "daily"
      ? orderedBars.flatMap((bar, index) => {
          const ratio = volumeRatios[index];
          if (ratio === null) {
            return [];
          }
          return [{
            time: toChartTime(bar.time),
            value: ratio,
            color: ratio >= 1 ? `${UP_COLOR}b3` : `${DOWN_COLOR}b3`,
          }];
        })
      : orderedBars.map((bar) => ({
          time: toChartTime(bar.time),
          value: bar.volume,
          color: bar.close >= bar.open ? `${UP_COLOR}b3` : `${DOWN_COLOR}b3`,
        }));
    secondarySeries.setData(secondaryData);
    if (mode === "daily") {
      secondarySeries.createPriceLine({
        price: 1,
        color: "#98a2b3",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "1.0",
      });
    }

    const panes = chart.panes();
    panes[0]?.setStretchFactor(4);
    panes[1]?.setStretchFactor(1.15);
    chart.timeScale().fitContent();

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        setActiveBar(null);
        return;
      }
      const key = chartTimeKey(param.time);
      setActiveBar(orderedBars.find((bar) => chartTimeKey(bar.time) === key) ?? null);
    });

    return () => {
      chartRef.current = null;
      chart.remove();
    };
  }, [intradayReferencePrice, mode, orderedBars, volumeRatios]);

  if (orderedBars.length === 0) {
    return <div className="chart-state">{emptyLabel}</div>;
  }

  const directionClass = changePct === null ? "neutral" : changePct >= 0 ? "up" : "down";
  return (
    <div className="market-kline-shell">
      <div className="market-kline-readout">
        <div className="market-kline-ohlc">
          <strong>{displayedBar?.label}</strong>
          {mode === "daily" ? (
            <>
              <span>开 <b>{formatPrice(displayedBar?.open)}</b></span>
              <span>高 <b className="up">{formatPrice(displayedBar?.high)}</b></span>
              <span>低 <b className="down">{formatPrice(displayedBar?.low)}</b></span>
              <span>收 <b className={directionClass}>{formatPrice(displayedBar?.close)}</b></span>
              <span>涨幅 <b className={directionClass}>{formatPercent(changePct)}</b></span>
              <span>5日量比 <b>{formatVolumeRatio(displayedVolumeRatio)}</b></span>
            </>
          ) : (
            <>
              <span>价格 <b className={directionClass}>{formatPrice(displayedBar?.close)}</b></span>
              <span>涨幅 <b className={directionClass}>{formatPercent(changePct)}</b></span>
              <span>均价 <b>{formatPrice(intradayAverage ?? undefined)}</b></span>
              <span>成交量 <b>{formatVolume(displayedBar?.volume)}</b></span>
              <span>成交额 <b>{formatAmount(displayedBar?.amount)}</b></span>
            </>
          )}
        </div>
        {mode === "daily" ? (
          <div className="market-kline-ma" aria-label="移动平均线">
            {movingAverages.map((item) => (
              <span key={item.window} style={{ color: item.color }}>
                MA{item.window} {item.value === null ? "--" : item.value.toFixed(2)}
              </span>
            ))}
          </div>
        ) : (
          <div className="market-kline-ma" aria-label="分时图例">
            <span style={{ color: "#246bfd" }}>价格线</span>
            <span style={{ color: "#e5a000" }}>均价线</span>
            {intradayReferencePrice ? (
              <span style={{ color: "#667085" }}>
                0% 昨收 {formatPrice(intradayReferencePrice)}
              </span>
            ) : null}
            <span>VOL {formatVolume(displayedBar?.volume)}</span>
          </div>
        )}
      </div>
      <div className="market-kline-canvas" ref={chartContainerRef} />
    </div>
  );
}

function movingAverageLine(bars: MarketCandleBar[], window: number): LineData<Time>[] {
  return bars.flatMap((bar, index) => {
    if (index + 1 < window) {
      return [];
    }
    const values = bars.slice(index + 1 - window, index + 1);
    const value = values.reduce((total, item) => total + item.close, 0) / window;
    return [{ time: toChartTime(bar.time), value: Number(value.toFixed(3)) }];
  });
}

function intradayAverageLine(bars: MarketCandleBar[]): LineData<Time>[] {
  let totalAmount = 0;
  let totalVolume = 0;
  return bars.flatMap((bar) => {
    totalAmount += bar.amount ?? 0;
    totalVolume += bar.volume;
    if (totalAmount <= 0 || totalVolume <= 0) {
      return [];
    }
    return [{
      time: toChartTime(bar.time),
      value: Number((totalAmount / totalVolume).toFixed(3)),
    }];
  });
}

function cumulativeAverageAt(bars: MarketCandleBar[], selectedIndex: number): number | null {
  if (selectedIndex < 0) {
    return null;
  }
  const selectedBars = bars.slice(0, selectedIndex + 1);
  const totalAmount = selectedBars.reduce((total, item) => total + (item.amount ?? 0), 0);
  const totalVolume = selectedBars.reduce((total, item) => total + item.volume, 0);
  return totalAmount > 0 && totalVolume > 0 ? totalAmount / totalVolume : null;
}

function intradayPriceBounds(
  bars: MarketCandleBar[],
  referencePrice: number | null,
): { low: number; high: number } {
  const averageValues = intradayAverageLine(bars).map((item) => item.value);
  const prices = [...bars.map((bar) => bar.close), ...averageValues];
  const reference = referencePrice ?? bars[0]?.open ?? prices[0] ?? 1;
  const halfRange = Math.max(
    reference * 0.015,
    ...prices.map((price) => Math.abs(price - reference)),
  );
  return {
    low: reference - halfRange,
    high: reference + halfRange,
  };
}

function movingAverageReadout(
  bars: MarketCandleBar[],
  selectedIndex: number,
): MovingAverageValue[] {
  return MA_CONFIG.map((config) => {
    if (selectedIndex + 1 < config.window) {
      return { ...config, value: null };
    }
    const values = bars.slice(selectedIndex + 1 - config.window, selectedIndex + 1);
    const value = values.reduce((total, item) => total + item.close, 0) / config.window;
    return { ...config, value };
  });
}

function rollingVolumeRatios(
  bars: MarketCandleBar[],
  window: number,
): Array<number | null> {
  return bars.map((bar, index) => {
    if (index < window) {
      return null;
    }
    const baseline = bars
      .slice(index - window, index)
      .reduce((total, item) => total + item.volume, 0) / window;
    if (baseline <= 0) {
      return null;
    }
    return Number((bar.volume / baseline).toFixed(3));
  });
}

function toChartTime(value: string | number): Time {
  return typeof value === "number" ? (value as UTCTimestamp) : value;
}

function chartTimeKey(value: Time | string | number): string {
  if (typeof value === "number") {
    return String(value);
  }
  if (typeof value === "string") {
    return value;
  }
  return `${value.year}-${String(value.month).padStart(2, "0")}-${String(value.day).padStart(2, "0")}`;
}

function formatAxisTime(value: Time, mode: "daily" | "intraday"): string {
  if (mode === "intraday" && typeof value === "number") {
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "Asia/Shanghai",
    }).format(new Date(value * 1000));
  }
  const key = chartTimeKey(value);
  return key.length >= 10 ? key.slice(5) : key;
}

function formatCrosshairTime(value: Time, mode: "daily" | "intraday"): string {
  if (mode === "intraday" && typeof value === "number") {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "Asia/Shanghai",
    }).format(new Date(value * 1000));
  }
  return chartTimeKey(value);
}

function formatPrice(value: number | undefined): string {
  return value === undefined ? "--" : value.toFixed(2);
}

function formatPercent(value: number | null): string {
  return value === null ? "--" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatVolumeRatio(value: number | null): string {
  return value === null ? "--" : `${value.toFixed(2)}x`;
}

function formatVolume(value: number | undefined): string {
  if (value === undefined) {
    return "--";
  }
  if (value >= 100_000_000) {
    return `${(value / 100_000_000).toFixed(2)}亿`;
  }
  if (value >= 10_000) {
    return `${(value / 10_000).toFixed(1)}万`;
  }
  return value.toFixed(0);
}

function formatAmount(value: number | undefined): string {
  if (value === undefined) {
    return "--";
  }
  if (value >= 100_000_000) {
    return `${(value / 100_000_000).toFixed(2)}亿`;
  }
  if (value >= 10_000) {
    return `${(value / 10_000).toFixed(1)}万`;
  }
  return `${value.toFixed(0)}元`;
}
