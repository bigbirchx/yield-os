"use client";
/**
 * Time-series rate chart built on ECharts.
 *
 * Renders APY / funding-rate / price history as a multi-series line chart.
 * Supports time range selection (7d, 30d, 90d) and tooltip hover.
 */
import { useState, useMemo, type CSSProperties } from "react";
import ReactEChartsCore from "echarts-for-react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
} from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import { chartColors, chartTheme, colors, fonts, formatAPY } from "@/lib/theme";

// Register ECharts components
echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  SVGRenderer,
]);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ChartSeries {
  name: string;
  data: { date: string; value: number }[];
  color?: string;
}

interface RateChartProps {
  series: ChartSeries[];
  /** Chart height in px (default 280) */
  height?: number;
  /** Y-axis label (default "APY %") */
  yAxisLabel?: string;
  /** Custom value formatter for tooltip/axis (default formatAPY) */
  valueFormatter?: (value: number) => string;
  /** Show time range selector (default true) */
  showRangeSelector?: boolean;
  /** Default time range in days */
  defaultDays?: number;
  /** Additional style overrides */
  style?: CSSProperties;
}

// ---------------------------------------------------------------------------
// Time range filter
// ---------------------------------------------------------------------------

const RANGE_OPTIONS = [
  { label: "7D", days: 7 },
  { label: "30D", days: 30 },
  { label: "90D", days: 90 },
];

function filterByDays(
  data: { date: string; value: number }[],
  days: number
): { date: string; value: number }[] {
  const cutoff = Date.now() - days * 86400_000;
  return data.filter((d) => new Date(d.date).getTime() >= cutoff);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function RateChart({
  series,
  height = 280,
  yAxisLabel = "APY %",
  valueFormatter = formatAPY,
  showRangeSelector = true,
  defaultDays = 30,
  style,
}: RateChartProps) {
  const [days, setDays] = useState(defaultDays);

  const option = useMemo(() => {
    const filteredSeries = series.map((s, i) => {
      const data = filterByDays(s.data, days);
      return {
        name: s.name,
        type: "line" as const,
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 1.5 },
        color: s.color ?? chartColors[i % chartColors.length],
        data: data.map((d) => [d.date, d.value]),
      };
    });

    return {
      ...chartTheme,
      legend: {
        ...chartTheme.legend,
        show: series.length > 1,
        bottom: 0,
        data: series.map((s) => s.name),
      },
      tooltip: {
        ...chartTheme.tooltip,
        trigger: "axis" as const,
        formatter: (params: { seriesName: string; value: [string, number]; color: string }[]) => {
          if (!Array.isArray(params) || params.length === 0) return "";
          const date = new Date(params[0].value[0]).toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          });
          let html = `<div style="font-family:${fonts.sans};font-size:11px;color:${colors.text.secondary};margin-bottom:4px">${date}</div>`;
          for (const p of params) {
            const color = p.color;
            const val = valueFormatter(p.value[1]);
            html += `<div style="display:flex;align-items:center;gap:6px;font-family:${fonts.mono};font-size:12px">`;
            html += `<span style="width:8px;height:8px;border-radius:50%;background:${color};display:inline-block"></span>`;
            html += `<span style="color:${colors.text.secondary};flex:1">${p.seriesName}</span>`;
            html += `<span style="color:${colors.text.primary};font-weight:600">${val}</span>`;
            html += `</div>`;
          }
          return html;
        },
      },
      xAxis: {
        ...chartTheme.xAxis,
        type: "time" as const,
      },
      yAxis: {
        ...chartTheme.yAxis,
        type: "value" as const,
        name: yAxisLabel,
        nameTextStyle: { color: colors.text.muted, fontSize: 10, fontFamily: fonts.sans },
        axisLabel: {
          ...chartTheme.yAxis.axisLabel,
          formatter: (v: number) => valueFormatter(v),
        },
      },
      grid: {
        left: 70,
        right: 16,
        top: 24,
        bottom: series.length > 1 ? 36 : 24,
      },
      series: filteredSeries,
    };
  }, [series, days, yAxisLabel, valueFormatter]);

  return (
    <div className="rc-root" style={style}>
      {showRangeSelector && (
        <div className="rc-range-bar">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.days}
              className={`rc-range-btn ${days === opt.days ? "rc-range-active" : ""}`}
              onClick={() => setDays(opt.days)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
      <ReactEChartsCore
        echarts={echarts}
        option={option}
        style={{ height, width: "100%" }}
        notMerge
        lazyUpdate
        opts={{ renderer: "svg" }}
      />
    </div>
  );
}
