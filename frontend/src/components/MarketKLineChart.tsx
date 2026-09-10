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
  session?: string;
  referencePrice?: number;
}

type ChartMode = "daily" | "intraday" | "intraday5d";

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

/**
 * Render daily or intraday market bars with mode-specific indicators and crosshair readouts.
 * Chart instances and subscriptions are owned by the effect and disposed when their inputs
 * change or the component unmounts.
 */
export function MarketKLineChart({
  bars,
  emptyLabel,
  mode,
  referencePrice,
}: {
  bars: MarketCandleBar[];
  emptyLabel: string;
  mode: ChartMode;
  referencePrice?: number | null;
}) {
  const chartContainerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [activeBar, setActiveBar] = useState<MarketCandleBar | null>(null);
  const orderedBars = useMemo(
    /* Derive orderedBars from the listed dependencies, reusing it until those dependencies change. */ () => [...bars].sort(/* Compare two entries using the explicit tie-break order for MarketKLineChart. */ (left, right) => chartTimeKey(left.time).localeCompare(chartTimeKey(right.time))),
    [bars],
  );
  const latestBar = orderedBars[orderedBars.length - 1] ?? null;
  const displayedBar = activeBar ?? latestBar;
  const displayedIndex = displayedBar
    ? orderedBars.findIndex(/* Locate the entry matching the active identity/time used by MarketKLineChart. */ (item) => chartTimeKey(item.time) === chartTimeKey(displayedBar.time))
    : -1;
  const isIntraday = mode !== "daily";
  const intradayReferencePrice = isIntraday && referencePrice && referencePrice > 0
    ? referencePrice
    : null;
  const comparisonPrice = isIntraday
    ? displayedBar?.referencePrice ?? intradayReferencePrice
    : displayedIndex > 0
      ? orderedBars[displayedIndex - 1].close
      : null;
  const changePct =
    displayedBar && comparisonPrice
      ? ((displayedBar.close / comparisonPrice) - 1) * 100
      : null;
  const intradayAverages = useMemo(
    /* Derive intradayAverages from the listed dependencies, reusing it until those dependencies change. */ () => intradayAverageValues(orderedBars, mode === "intraday5d"),
    [mode, orderedBars],
  );
  const intradayAverage = isIntraday && displayedIndex >= 0
    ? intradayAverages[displayedIndex]
    : null;
  const barsByTime = useMemo(
    /* Derive barsByTime from the listed dependencies, reusing it until those dependencies change. */ () => new Map(orderedBars.map(/* Transform each entry in orderedBars into the result used by MarketKLineChart. */ (bar) => [chartTimeKey(bar.time), bar])),
    [orderedBars],
  );
  const movingAverages = useMemo(
    /* Derive movingAverages from the listed dependencies, reusing it until those dependencies change. */ () => movingAverageReadout(orderedBars, displayedIndex),
    [displayedIndex, orderedBars],
  );
  const volumeRatios = useMemo(
    /* Derive volumeRatios from the listed dependencies, reusing it until those dependencies change. */ () => rollingVolumeRatios(orderedBars, 5),
    [orderedBars],
  );
  const displayedVolumeRatio =
    displayedIndex >= 0 ? volumeRatios[displayedIndex] : null;

  useEffect(/* Synchronize MarketKLineChart with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    setActiveBar(null);
  }, [bars, mode]);

  useEffect(/* Synchronize MarketKLineChart with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    const container = chartContainerRef.current;
    if (!container || orderedBars.length === 0) {
      return undefined;
    }

    const renderStartedAt = performance.now();
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
        timeVisible: isIntraday,
        secondsVisible: false,
        rightOffset: mode === "daily" ? 2 : mode === "intraday5d" ? 0.5 : 1,
        barSpacing: mode === "daily" ? 8 : mode === "intraday5d" ? 1 : 6,
        minBarSpacing: mode === "intraday5d" ? 0.5 : 3,
        fixLeftEdge: true,
        tickMarkFormatter: /* Format a time-axis tick according to the active daily/intraday mode. */ (time: Time) => formatAxisTime(time, mode),
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
        priceFormatter: /* Format a chart price using the current display precision. */ (price: number) => price.toFixed(2),
        timeFormatter: /* Format the crosshair time according to the active chart mode. */ (time: Time) => formatCrosshairTime(time, mode),
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
      const candleData: CandlestickData<Time>[] = orderedBars.map(/* Transform each entry in orderedBars into the result used by MarketKLineChart. */ (bar) => ({
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
              formatter: /* Format this chart series' value for its scale or readout. */ (price: number) => formatPercent(
                ((price / intradayReferencePrice) - 1) * 100,
              ),
            }
          : { type: "price", precision: 2, minMove: 0.01 },
      });
      priceSeries.setData(orderedBars.map(/* Transform each entry in orderedBars into the result used by MarketKLineChart. */ (bar) => ({
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
              formatter: /* Format this chart series' value for its scale or readout. */ (price: number) => formatPercent(
                ((price / intradayReferencePrice) - 1) * 100,
              ),
            }
          : { type: "price", precision: 2, minMove: 0.01 },
      });
      averageSeries.setData(intradayAverageLine(orderedBars, intradayAverages));

      const bounds = intradayPriceBounds(
        orderedBars,
        intradayReferencePrice,
        intradayAverages,
      );
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
              formatter: /* Format this chart series' value for its scale or readout. */ (value: number) => `${value.toFixed(2)}x`,
            }
          : { type: "volume" },
        priceLineVisible: false,
        lastValueVisible: false,
      },
      1,
    );
    const secondaryData: HistogramData<Time>[] = mode === "daily"
      ? orderedBars.flatMap(/* Handle the callback from orderedBars.flatMap within MarketKLineChart. */ (bar, index) => {
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
      : orderedBars.map(/* Transform each entry in orderedBars into the result used by MarketKLineChart. */ (bar) => ({
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
    container.dataset.renderMs = (performance.now() - renderStartedAt).toFixed(2);
    container.dataset.pointCount = String(orderedBars.length);
    const paintFrame = requestAnimationFrame(/* Handle the callback from requestAnimationFrame within MarketKLineChart. */ () => {
      container.dataset.paintMs = (performance.now() - renderStartedAt).toFixed(2);
    });

    chart.subscribeCrosshairMove(/* Handle the callback from chart.subscribeCrosshairMove within MarketKLineChart. */ (param) => {
      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        setActiveBar(null);
        return;
      }
      const key = chartTimeKey(param.time);
      setActiveBar(barsByTime.get(key) ?? null);
    });

    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => {
      cancelAnimationFrame(paintFrame);
      // Disposal releases the old canvas and subscriptions when chart inputs change.
      chartRef.current = null;
      chart.remove();
    };
  }, [barsByTime, intradayAverages, intradayReferencePrice, isIntraday, mode, orderedBars, volumeRatios]);

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
            {movingAverages.map(/* Transform each entry in movingAverages into the result used by MarketKLineChart. */ (item) => (
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

/**
 * Build the closing-price moving-average series, omitting points without a full window.
 */
function movingAverageLine(bars: MarketCandleBar[], window: number): LineData<Time>[] {
  return bars.flatMap(/* Handle the callback from bars.flatMap within movingAverageLine. */ (bar, index) => {
    if (index + 1 < window) {
      return [];
    }
    const values = bars.slice(index + 1 - window, index + 1);
    const value = values.reduce(/* Accumulate the entries into the derived value used by movingAverageLine. */ (total, item) => total + item.close, 0) / window;
    return [{ time: toChartTime(bar.time), value: Number(value.toFixed(3)) }];
  });
}

/**
 * Compute cumulative intraday average prices, resetting at day boundaries when requested.
 */
function intradayAverageValues(
  bars: MarketCandleBar[],
  resetBySession: boolean,
): Array<number | null> {
  let totalAmount = 0;
  let totalVolume = 0;
  let activeSession: string | undefined;
  return bars.map(/* Transform each entry in bars into the result used by intradayAverageValues. */ (bar) => {
    if (resetBySession && bar.session !== activeSession) {
      activeSession = bar.session;
      totalAmount = 0;
      totalVolume = 0;
    }
    totalAmount += bar.amount ?? 0;
    totalVolume += bar.volume;
    if (totalAmount <= 0 || totalVolume <= 0) {
      return null;
    }
    return Number((totalAmount / totalVolume).toFixed(3));
  });
}

/**
 * Convert the computed intraday averages into chart-series points.
 */
function intradayAverageLine(
  bars: MarketCandleBar[],
  averages: Array<number | null>,
): LineData<Time>[] {
  return bars.flatMap(/* Handle the callback from bars.flatMap within intradayAverageLine. */ (bar, index) => {
    const value = averages[index];
    return value === null ? [] : [{ time: toChartTime(bar.time), value }];
  });
}

/**
 * Choose chart bounds around the available intraday prices and reference price.
 */
function intradayPriceBounds(
  bars: MarketCandleBar[],
  referencePrice: number | null,
  averages: Array<number | null>,
): { low: number; high: number } {
  const averageValues = averages.filter(/* Keep only entries satisfying this predicate for intradayPriceBounds. */ (value): value is number => value !== null);
  const prices = [...bars.map(/* Transform each entry in bars into the result used by intradayPriceBounds. */ (bar) => bar.close), ...averageValues];
  const reference = referencePrice ?? bars[0]?.open ?? prices[0] ?? 1;
  const halfRange = Math.max(
    reference * 0.015,
    ...prices.map(/* Transform each entry in prices into the result used by intradayPriceBounds. */ (price) => Math.abs(price - reference)),
  );
  return {
    low: reference - halfRange,
    high: reference + halfRange,
  };
}

/**
 * Read the supported moving averages at the currently displayed bar index.
 */
function movingAverageReadout(
  bars: MarketCandleBar[],
  selectedIndex: number,
): MovingAverageValue[] {
  return MA_CONFIG.map(/* Transform each entry in MA_CONFIG into the result used by movingAverageReadout. */ (config) => {
    if (selectedIndex + 1 < config.window) {
      return { ...config, value: null };
    }
    const values = bars.slice(selectedIndex + 1 - config.window, selectedIndex + 1);
    const value = values.reduce(/* Accumulate the entries into the derived value used by movingAverageReadout. */ (total, item) => total + item.close, 0) / config.window;
    return { ...config, value };
  });
}

/**
 * Compare each bar's volume with its trailing baseline when enough history is available.
 */
function rollingVolumeRatios(
  bars: MarketCandleBar[],
  window: number,
): Array<number | null> {
  return bars.map(/* Transform each entry in bars into the result used by rollingVolumeRatios. */ (bar, index) => {
    if (index < window) {
      return null;
    }
    const baseline = bars
      .slice(index - window, index)
      .reduce(/* Accumulate the entries into the derived value used by rollingVolumeRatios. */ (total, item) => total + item.volume, 0) / window;
    if (baseline <= 0) {
      return null;
    }
    return Number((bar.volume / baseline).toFixed(3));
  });
}

/**
 * Convert the application's bar time to the chart library's Time representation.
 */
function toChartTime(value: string | number): Time {
  return typeof value === "number" ? (value as UTCTimestamp) : value;
}

/**
 * Normalize a chart time into a stable key for ordering and crosshair lookup.
 */
function chartTimeKey(value: Time | string | number): string {
  if (typeof value === "number") {
    return String(value);
  }
  if (typeof value === "string") {
    return value;
  }
  return `${value.year}-${String(value.month).padStart(2, "0")}-${String(value.day).padStart(2, "0")}`;
}

/**
 * Render compact daily or intraday labels for the chart's horizontal axis.
 */
function formatAxisTime(value: Time, mode: ChartMode): string {
  if (mode !== "daily" && typeof value === "number") {
    const options: Intl.DateTimeFormatOptions = mode === "intraday5d"
      ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }
      : { hour: "2-digit", minute: "2-digit", hour12: false };
    return new Intl.DateTimeFormat("zh-CN", {
      ...options,
      timeZone: "Asia/Shanghai",
    }).format(new Date(value * 1000));
  }
  const key = chartTimeKey(value);
  return key.length >= 10 ? key.slice(5) : key;
}

/**
 * Render the detailed date/time shown for the active crosshair bar.
 */
function formatCrosshairTime(value: Time, mode: ChartMode): string {
  if (mode !== "daily" && typeof value === "number") {
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

/**
 * Render an optional chart price to the configured decimal precision.
 */
function formatPrice(value: number | undefined): string {
  return value === undefined ? "--" : value.toFixed(2);
}

/**
 * Format an already-percent-valued chart change, retaining its sign and missing-value state.
 */
function formatPercent(value: number | null): string {
  return value === null ? "--" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/**
 * Render an optional volume ratio without disguising a missing baseline as zero.
 */
function formatVolumeRatio(value: number | null): string {
  return value === null ? "--" : `${value.toFixed(2)}x`;
}

/**
 * Choose readable units for a chart volume value.
 */
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

/**
 * Choose readable chart amount units, retaining an explicit unavailable display for missing
 * values.
 */
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
