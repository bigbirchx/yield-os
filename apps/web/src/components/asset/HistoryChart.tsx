"use client";

import ReactECharts from "echarts-for-react";
import type { LendingHistoryMarket } from "@/types/api";

interface HistoryChartProps {
  markets: LendingHistoryMarket[];
  metric: "supply_apy" | "borrow_apy";
  title: string;
}

const COLORS = [
  "#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a78bfa",
  "#06b6d4", "#f97316", "#ec4899",
];

export function HistoryChart({ markets, metric, title }: HistoryChartProps) {
  if (markets.length === 0) {
    return <div className="chart-empty">No historical data available</div>;
  }

  const series = markets.map((mkt, i) => ({
    name: `${mkt.protocol}${mkt.chain ? ` (${mkt.chain})` : ""}`,
    type: "line" as const,
    smooth: true,
    symbol: "none",
    lineStyle: { width: 1.5 },
    itemStyle: { color: COLORS[i % COLORS.length] },
    data: mkt.data
      .filter((d) => d[metric] != null)
      .map((d) => [
        new Date(d.snapshot_at).getTime(),
        parseFloat((d[metric] as number).toFixed(4)),
      ]),
  }));

  const option = {
    backgroundColor: "transparent",
    textStyle: { color: "#8b9099", fontSize: 11 },
    grid: { top: 32, right: 16, bottom: 40, left: 56 },
    tooltip: {
      trigger: "axis" as const,
      backgroundColor: "#111418",
      borderColor: "#1e2328",
      textStyle: { color: "#e8eaed", fontSize: 11 },
      formatter: (params: Array<{ seriesName: string; value: [number, number] }>) => {
        const date = new Date(params[0].value[0]).toLocaleDateString();
        const lines = params.map(
          (p) => `<div>${p.seriesName}: <b>${p.value[1].toFixed(2)}%</b></div>`
        );
        return `<div style="font-size:11px"><div style="color:#8b9099">${date}</div>${lines.join("")}</div>`;
      },
    },
    legend: {
      top: 4,
      textStyle: { color: "#8b9099", fontSize: 10 },
      itemWidth: 12,
      itemHeight: 2,
    },
    xAxis: {
      type: "time" as const,
      axisLine: { lineStyle: { color: "#1e2328" } },
      axisLabel: { color: "#4a5060", fontSize: 10 },
      splitLine: { lineStyle: { color: "#161a1f" } },
    },
    yAxis: {
      type: "value" as const,
      axisLabel: {
        color: "#4a5060",
        fontSize: 10,
        formatter: (v: number) => `${v.toFixed(2)}%`,
      },
      splitLine: { lineStyle: { color: "#161a1f" } },
    },
    series,
  };

  return (
    <div className="chart-wrap">
      <div className="chart-title">{title}</div>
      <ReactECharts
        option={option}
        style={{ height: 220, width: "100%" }}
        opts={{ renderer: "svg" }}
      />
    </div>
  );
}
