/**
 * Design system constants for the Yield Cockpit dashboard.
 *
 * Dark-mode-primary, data-dense, institutional finance aesthetic.
 * Bloomberg Terminal meets modern web. NOT a colorful DeFi dapp.
 *
 * These constants mirror the CSS custom properties in globals.css
 * but are available for JS-driven styling (ECharts, inline, etc.).
 */

// ---------------------------------------------------------------------------
// Colors
// ---------------------------------------------------------------------------

export const colors = {
  bg: {
    primary: "#0a0f1e",
    surface: "#111827",
    surfaceAlt: "#1a2035",
    hover: "#1e293b",
    elevated: "#1f2937",
  },
  text: {
    primary: "#e2e8f0",
    secondary: "#94a3b8",
    muted: "#64748b",
    dim: "#475569",
  },
  accent: {
    blue: "#3b82f6",
    blueLight: "#60a5fa",
    green: "#22c55e",
    greenDim: "#166534",
    red: "#ef4444",
    redDim: "#7f1d1d",
    amber: "#f59e0b",
    amberDim: "#78350f",
    orange: "#f97316",
    orangeDim: "#7c2d12",
  },
  border: {
    default: "#1e293b",
    subtle: "#0f172a",
    focus: "#3b82f6",
  },
} as const;

// Umbrella group color mapping
export const umbrellaColors: Record<string, string> = {
  USD: "#22c55e",
  ETH: "#627eea",
  BTC: "#f7931a",
  SOL: "#9945ff",
  HYPE: "#00d4aa",
  OTHER: "#94a3b8",
};

// Venue brand colors
export const venueColors: Record<string, string> = {
  AAVE_V3: "#b6509e",
  MORPHO: "#2470e5",
  COMPOUND_V3: "#00d395",
  EULER_V2: "#e83f6f",
  SPARK: "#f76b1c",
  KAMINO: "#4e44ce",
  JUPITER: "#19fb9b",
  JUSTLEND: "#cf0e36",
  PENDLE: "#0ea5e9",
  LIDO: "#00a3ff",
  SKY: "#1fc7d4",
  BINANCE: "#f0b90b",
  OKX: "#000000",
  BYBIT: "#f7a600",
  DERIBIT: "#5bc53f",
  DEFILLAMA: "#6366f1",
};

// Side colors
export const sideColors = {
  SUPPLY: "#22c55e",
  BORROW: "#ef4444",
} as const;

// ---------------------------------------------------------------------------
// Typography
// ---------------------------------------------------------------------------

export const fonts = {
  mono: '"JetBrains Mono", "Fira Code", "Cascadia Code", ui-monospace, monospace',
  sans: '"Inter", "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
} as const;

// ---------------------------------------------------------------------------
// ECharts theme (pass to ReactEChartsCore or option)
// ---------------------------------------------------------------------------

export const chartColors = [
  "#3b82f6", // blue
  "#22c55e", // green
  "#f59e0b", // amber
  "#ef4444", // red
  "#8b5cf6", // purple
  "#06b6d4", // cyan
  "#f97316", // orange
  "#ec4899", // pink
  "#14b8a6", // teal
  "#a855f7", // violet
];

export const chartTheme = {
  backgroundColor: "transparent",
  textStyle: { fontFamily: fonts.mono, color: colors.text.secondary },
  title: { textStyle: { color: colors.text.primary, fontFamily: fonts.sans } },
  legend: { textStyle: { color: colors.text.secondary, fontSize: 11 } },
  tooltip: {
    backgroundColor: colors.bg.elevated,
    borderColor: colors.border.default,
    textStyle: { color: colors.text.primary, fontFamily: fonts.mono, fontSize: 12 },
  },
  xAxis: {
    axisLine: { lineStyle: { color: colors.border.default } },
    axisTick: { lineStyle: { color: colors.border.default } },
    axisLabel: { color: colors.text.muted, fontSize: 10 },
    splitLine: { lineStyle: { color: colors.border.subtle, type: "dashed" as const } },
  },
  yAxis: {
    axisLine: { lineStyle: { color: colors.border.default } },
    axisTick: { lineStyle: { color: colors.border.default } },
    axisLabel: { color: colors.text.muted, fontSize: 10, fontFamily: fonts.mono },
    splitLine: { lineStyle: { color: colors.border.subtle, type: "dashed" as const } },
  },
  grid: { left: 60, right: 16, top: 32, bottom: 28 },
} as const;

// ---------------------------------------------------------------------------
// Number formatters
// ---------------------------------------------------------------------------

/** Format as percentage: 5.234 -> "5.23%" */
export function formatAPY(value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "--";
  if (Math.abs(value) >= 1000) return `${value > 0 ? ">" : "<"}999%`;
  return `${value.toFixed(2)}%`;
}

/** Format USD with appropriate suffix: 1234567 -> "$1.23M" */
export function formatUSD(value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "--";
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  if (abs >= 1) return `${sign}$${abs.toFixed(2)}`;
  return `${sign}$${abs.toFixed(4)}`;
}

/** Format USD with full precision: 1234567 -> "$1,234,567" */
export function formatUSDFull(value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "--";
  return "$" + value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Format token amount with sensible decimals */
export function formatTokenAmount(value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "--";
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  if (abs >= 1) return value.toFixed(2);
  if (abs >= 0.0001) return value.toFixed(4);
  return value.toExponential(2);
}

/** Format basis points: 0.0534 -> "5.34 bps" */
export function formatBps(value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "--";
  return `${(value * 10000).toFixed(2)} bps`;
}

/** Format percent change with sign: 5.23 -> "+5.23%", -2.1 -> "-2.10%" */
export function formatChange(value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

/** Format a number with commas: 1234567 -> "1,234,567" */
export function formatNumber(value: number | null | undefined, decimals = 0): string {
  if (value == null || !isFinite(value)) return "--";
  return value.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

// ---------------------------------------------------------------------------
// Color helpers
// ---------------------------------------------------------------------------

/** Return a green/red color based on APY sign and magnitude */
export function apyColor(value: number | null | undefined): string {
  if (value == null) return colors.text.muted;
  if (value > 10) return colors.accent.green;
  if (value > 0) return "#4ade80"; // lighter green for modest APYs
  if (value === 0) return colors.text.muted;
  return colors.accent.red;
}

/** Return green for positive change, red for negative */
export function changeColor(value: number | null | undefined): string {
  if (value == null || value === 0) return colors.text.muted;
  return value > 0 ? colors.accent.green : colors.accent.red;
}

/** Return umbrella group color */
export function getUmbrellaColor(umbrella: string): string {
  return umbrellaColors[umbrella] ?? umbrellaColors.OTHER;
}

// ---------------------------------------------------------------------------
// Sub-type display labels
// ---------------------------------------------------------------------------

export const subTypeLabels: Record<string, string> = {
  NATIVE: "Native",
  NATIVE_TOKEN: "Token",
  WRAPPED_NATIVE: "Wrapped",
  LIQUID_STAKING_TOKEN: "LST",
  LIQUID_RESTAKING_TOKEN: "LRT",
  TIER1_STABLE: "Stable",
  TIER2_STABLE: "Stable",
  TOKENIZED_YIELD_STRATEGY: "Yield",
  RECEIPT_TOKEN: "Receipt",
  SYNTHETIC: "Synth",
};

// ---------------------------------------------------------------------------
// Venue display names
// ---------------------------------------------------------------------------

export const venueLabels: Record<string, string> = {
  AAVE_V3: "Aave V3",
  MORPHO: "Morpho",
  COMPOUND_V3: "Compound V3",
  EULER_V2: "Euler V2",
  SPARK: "Spark",
  KAMINO: "Kamino",
  JUPITER: "Jupiter",
  JUSTLEND: "JustLend",
  PENDLE: "Pendle",
  LIDO: "Lido",
  SKY: "Sky",
  BINANCE: "Binance",
  OKX: "OKX",
  BYBIT: "Bybit",
  DERIBIT: "Deribit",
  DEFILLAMA: "DeFiLlama",
};
