"use client";
/**
 * Book Analysis — portfolio overlay for a CreditDesk WACC Export.
 *
 * Tabs: Summary | DeFi Positions | Bilateral Book | Collateral | Optimization | Maturities
 */
import { useCallback, useState, useRef } from "react";
import {
  uploadBook,
  fetchBook,
  fetchBookCollateral,
  refreshBookMatching,
  analyzeBook,
  fetchDefiVsMarket,
  fetchBilateralPricing,
  fetchCollateralEfficiency,
  fetchMaturityCalendar,
} from "@/lib/api";
import { formatAPY, formatUSD } from "@/lib/theme";
import type {
  BookImportResult,
  BookMeta,
  BookSummary,
  BookCollateralData,
  BookAnalysisResult,
  DefiVsMarketRow,
  BilateralPricingRow,
  CollateralEfficiencyRow,
  MaturityCalendarRow,
} from "@/types/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

type Tab = "summary" | "defi" | "bilateral" | "collateral" | "optimization" | "maturities";

const TABS: { key: Tab; label: string }[] = [
  { key: "summary", label: "Summary" },
  { key: "defi", label: "DeFi Positions" },
  { key: "bilateral", label: "Bilateral Book" },
  { key: "collateral", label: "Collateral" },
  { key: "optimization", label: "Optimization" },
  { key: "maturities", label: "Maturities" },
];

const CATEGORY_LABELS: Record<string, string> = {
  DEFI_SUPPLY: "DeFi Supply",
  DEFI_BORROW: "DeFi Borrow",
  NATIVE_STAKING: "Native Staking",
  BILATERAL_LOAN_OUT: "Bilateral Lending",
  BILATERAL_BORROW_IN: "Bilateral Borrowing",
  INTERNAL: "Internal",
  OFF_PLATFORM: "Off-Platform",
};

const CATEGORY_COLORS: Record<string, string> = {
  DEFI_SUPPLY: "#3b82f6",
  DEFI_BORROW: "#ef4444",
  NATIVE_STAKING: "#8b5cf6",
  BILATERAL_LOAN_OUT: "#22c55e",
  BILATERAL_BORROW_IN: "#f97316",
  INTERNAL: "#64748b",
  OFF_PLATFORM: "#94a3b8",
};

const SUGGESTION_ICONS: Record<string, string> = {
  DEFI_RATE_IMPROVEMENT: "\u2191",
  DEFI_NEW_OPPORTUNITY: "\u2605",
  DEFI_BORROW_OPTIMIZATION: "\u2193",
  BILATERAL_PRICING_CHECK: "\u2696",
  STAKING_RATE_CHECK: "\u25CE",
  CAPACITY_WARNING: "\u26A0",
  COLLATERAL_EFFICIENCY: "\u25A3",
  RATE_DEGRADATION: "\u2198",
  CONVERSION_OPPORTUNITY: "\u21C4",
  MATURITY_ACTION: "\u23F0",
};

const SUGGESTION_LABELS: Record<string, string> = {
  DEFI_RATE_IMPROVEMENT: "Rate Improvement",
  DEFI_NEW_OPPORTUNITY: "New Opportunity",
  DEFI_BORROW_OPTIMIZATION: "Borrow Optimization",
  BILATERAL_PRICING_CHECK: "Pricing Check",
  STAKING_RATE_CHECK: "Staking Check",
  CAPACITY_WARNING: "Capacity Warning",
  COLLATERAL_EFFICIENCY: "Collateral Efficiency",
  RATE_DEGRADATION: "Rate Degradation",
  CONVERSION_OPPORTUNITY: "Conversion Route",
  MATURITY_ACTION: "Maturity Action",
};

const ASSESSMENT_COLORS: Record<string, string> = {
  well_priced: "var(--green)",
  thin_premium: "var(--yellow)",
  underpriced: "var(--red)",
  market_rate: "var(--text-secondary)",
  overpriced: "var(--red)",
  no_defi_data: "var(--text-muted)",
};

const PRIORITY_COLORS: Record<string, string> = {
  high: "var(--red)",
  medium: "var(--yellow)",
  low: "var(--text-muted)",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtBps(value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(0)}bps`;
}

function deltaColor(bps: number | null | undefined): string {
  if (bps == null) return "var(--text-muted)";
  if (bps > 10) return "var(--green)";
  if (bps < -10) return "var(--red)";
  return "var(--text-secondary)";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function BookClient() {
  // ── State ──────────────────────────────────────────────────────────
  const [tab, setTab] = useState<Tab>("summary");
  const [bookId, setBookId] = useState<string | null>(null);
  const [bookMeta, setBookMeta] = useState<BookMeta | null>(null);
  const [importResult, setImportResult] = useState<BookImportResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // Tab data
  const [defiRows, setDefiRows] = useState<DefiVsMarketRow[] | null>(null);
  const [bilateralRows, setBilateralRows] = useState<BilateralPricingRow[] | null>(null);
  const [collateral, setCollateral] = useState<BookCollateralData | null>(null);
  const [collateralEff, setCollateralEff] = useState<CollateralEfficiencyRow[] | null>(null);
  const [analysis, setAnalysis] = useState<BookAnalysisResult | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [maturities, setMaturities] = useState<MaturityCalendarRow[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Expand states
  const [expandedDefi, setExpandedDefi] = useState<Set<number>>(new Set());
  const [expandedSuggestion, setExpandedSuggestion] = useState<Set<string>>(new Set());
  const [expandedCounterparty, setExpandedCounterparty] = useState<Set<number>>(new Set());

  // ── Upload handler ─────────────────────────────────────────────────
  const handleUpload = useCallback(async (file: File) => {
    if (!file.name.endsWith(".xlsx") && !file.name.endsWith(".xls")) {
      setUploadError("Please upload an Excel file (.xlsx)");
      return;
    }
    setUploading(true);
    setUploadError(null);
    try {
      const result = await uploadBook(file);
      if (!result) {
        setUploadError("Import failed. Check the file format.");
        return;
      }
      setImportResult(result);
      setBookId(result.book_id);
      // Fetch full book metadata
      const meta = await fetchBook(result.book_id);
      if (meta) setBookMeta(meta);
    } catch {
      setUploadError("Upload failed. Please try again.");
    } finally {
      setUploading(false);
    }
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const file = e.dataTransfer.files[0];
    if (file) handleUpload(file);
  }, [handleUpload]);

  const onFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleUpload(file);
  }, [handleUpload]);

  // ── Tab data loading ───────────────────────────────────────────────
  const loadTab = useCallback(async (t: Tab) => {
    setTab(t);
    if (!bookId) return;

    if (t === "defi" && !defiRows) {
      setDefiRows(await fetchDefiVsMarket(bookId));
    }
    if (t === "bilateral" && !bilateralRows) {
      setBilateralRows(await fetchBilateralPricing(bookId));
    }
    if (t === "collateral" && !collateral) {
      const [col, eff] = await Promise.all([
        fetchBookCollateral(bookId),
        fetchCollateralEfficiency(bookId),
      ]);
      setCollateral(col);
      setCollateralEff(eff);
    }
    if (t === "maturities" && !maturities) {
      setMaturities(await fetchMaturityCalendar(bookId));
    }
  }, [bookId, defiRows, bilateralRows, collateral, maturities]);

  // ── Refresh matching ───────────────────────────────────────────────
  const handleRefresh = useCallback(async () => {
    if (!bookId) return;
    setRefreshing(true);
    await refreshBookMatching(bookId);
    // Reload data
    const meta = await fetchBook(bookId);
    if (meta) setBookMeta(meta);
    setDefiRows(null);
    setBilateralRows(null);
    setRefreshing(false);
  }, [bookId]);

  // ── Analyze ────────────────────────────────────────────────────────
  const handleAnalyze = useCallback(async () => {
    if (!bookId) return;
    setAnalyzing(true);
    const result = await analyzeBook(bookId);
    if (result) setAnalysis(result);
    setAnalyzing(false);
  }, [bookId]);

  // ── Summary data from meta ─────────────────────────────────────────
  const summary = bookMeta?.summary ?? null;

  // ── Render ─────────────────────────────────────────────────────────
  return (
    <div className="bk-page">
      <div className="bk-header">
        <h1 className="bk-title">Book Analysis</h1>
        {bookId && (
          <div className="bk-header-actions">
            <button
              className="bk-btn bk-btn-secondary"
              onClick={handleRefresh}
              disabled={refreshing}
            >
              {refreshing ? "Refreshing..." : "Refresh Market Matching"}
            </button>
            <button
              className="bk-btn bk-btn-ghost"
              onClick={() => {
                setBookId(null);
                setBookMeta(null);
                setImportResult(null);
                setDefiRows(null);
                setBilateralRows(null);
                setCollateral(null);
                setCollateralEff(null);
                setAnalysis(null);
                setMaturities(null);
                setTab("summary");
              }}
            >
              New Import
            </button>
          </div>
        )}
      </div>

      {/* ── Upload Zone ──────────────────────────────────────────── */}
      {!bookId && (
        <div
          className={`bk-upload ${dragActive ? "bk-upload-active" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
          onDragLeave={() => setDragActive(false)}
          onDrop={onDrop}
          onClick={() => fileRef.current?.click()}
        >
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xls"
            onChange={onFileChange}
            style={{ display: "none" }}
          />
          <div className="bk-upload-icon">{uploading ? "\u23F3" : "\u2191"}</div>
          <div className="bk-upload-text">
            {uploading
              ? "Importing..."
              : "Drop your CreditDesk WACC Export here, or click to browse"}
          </div>
          <div className="bk-upload-hint">Accepts .xlsx files with Asset_Params, Trades_Raw, Observed_Collateral sheets</div>
          {uploadError && <div className="bk-upload-error">{uploadError}</div>}
        </div>
      )}

      {/* ── Import Result Banner ─────────────────────────────────── */}
      {importResult && !bookMeta?.summary && (
        <div className="bk-import-banner">
          <span className="bk-import-badge">Imported</span>
          <span>{importResult.total_positions} positions</span>
          <span className="bk-import-sep">|</span>
          <span>Net Book: {formatUSD(importResult.total_loan_out_usd - importResult.total_borrow_in_usd)}</span>
          <span className="bk-import-sep">|</span>
          <span>NIM: {formatAPY(importResult.net_interest_margin_pct)}</span>
          {Object.entries(importResult.category_breakdown).map(([cat, count]) => (
            <span key={cat} className="bk-import-cat">
              {CATEGORY_LABELS[cat] || cat}: {count}
            </span>
          ))}
        </div>
      )}

      {/* ── Tab bar ──────────────────────────────────────────────── */}
      {bookId && (
        <>
          <div className="bk-tabs">
            {TABS.map((t) => (
              <button
                key={t.key}
                className={`bk-tab ${tab === t.key ? "bk-tab-active" : ""}`}
                onClick={() => loadTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* ── Summary Tab ──────────────────────────────────────── */}
          {tab === "summary" && summary && <SummaryTab summary={summary} />}

          {/* ── DeFi Positions Tab ───────────────────────────────── */}
          {tab === "defi" && (
            <DefiTab
              rows={defiRows}
              expanded={expandedDefi}
              onToggle={(id) => {
                const next = new Set(expandedDefi);
                if (next.has(id)) next.delete(id); else next.add(id);
                setExpandedDefi(next);
              }}
            />
          )}

          {/* ── Bilateral Tab ────────────────────────────────────── */}
          {tab === "bilateral" && <BilateralTab rows={bilateralRows} />}

          {/* ── Collateral Tab ───────────────────────────────────── */}
          {tab === "collateral" && (
            <CollateralTab
              collateral={collateral}
              efficiency={collateralEff}
              expanded={expandedCounterparty}
              onToggle={(id) => {
                const next = new Set(expandedCounterparty);
                if (next.has(id)) next.delete(id); else next.add(id);
                setExpandedCounterparty(next);
              }}
            />
          )}

          {/* ── Optimization Tab ─────────────────────────────────── */}
          {tab === "optimization" && (
            <OptimizationTab
              analysis={analysis}
              analyzing={analyzing}
              onAnalyze={handleAnalyze}
              expanded={expandedSuggestion}
              onToggle={(id) => {
                const next = new Set(expandedSuggestion);
                if (next.has(id)) next.delete(id); else next.add(id);
                setExpandedSuggestion(next);
              }}
            />
          )}

          {/* ── Maturities Tab ───────────────────────────────────── */}
          {tab === "maturities" && <MaturityTab rows={maturities} />}
        </>
      )}
    </div>
  );
}

// ==========================================================================
// Sub-components
// ==========================================================================

// ── Summary Tab ──────────────────────────────────────────────────────

function SummaryTab({ summary }: { summary: BookSummary }) {
  const s = summary;
  const byCat = s.positions_by_category ?? {};
  const byAsset = s.positions_by_asset ?? {};
  const byCpty = s.positions_by_counterparty ?? {};

  // Top 10 counterparties
  const topCpty = Object.entries(byCpty)
    .sort(([, a], [, b]) => Math.abs(b) - Math.abs(a))
    .slice(0, 10);
  const maxCpty = topCpty.length > 0 ? Math.max(...topCpty.map(([, v]) => Math.abs(v))) : 1;

  // Top assets
  const topAssets = Object.entries(byAsset)
    .sort(([, a], [, b]) => Math.abs(b) - Math.abs(a))
    .slice(0, 12);
  const maxAsset = topAssets.length > 0 ? Math.max(...topAssets.map(([, v]) => Math.abs(v))) : 1;

  // Category total for ring proportions
  const catTotal = Object.values(byCat).reduce((a, b) => a + Math.abs(b), 0);

  return (
    <div className="bk-summary">
      {/* Stat cards */}
      <div className="bk-stat-grid">
        <StatCard label="Net Book" value={formatUSD(s.net_book_usd)} />
        <StatCard label="Loans Out" value={formatUSD(s.total_loan_out_usd)} color="var(--green)" />
        <StatCard label="Borrows In" value={formatUSD(s.total_borrow_in_usd)} color="var(--red)" />
        <StatCard label="DeFi Deployed" value={formatUSD(s.defi_deployed_usd)} color="#3b82f6" />
        <StatCard label="DeFi Borrowed" value={formatUSD(s.defi_borrowed_usd)} color="#ef4444" />
        <StatCard label="Staking" value={formatUSD(s.staking_deployed_usd)} color="#8b5cf6" />
        <StatCard label="Avg Lending Rate" value={formatAPY(s.weighted_avg_lending_rate_pct)} color="var(--green)" />
        <StatCard label="Avg Borrow Rate" value={formatAPY(s.weighted_avg_borrowing_rate_pct)} color="var(--red)" />
        <StatCard label="Net Interest Margin" value={formatAPY(s.net_interest_margin_pct)} />
        <StatCard label="Annual Income (est)" value={formatUSD(s.estimated_annual_income_usd)} color="var(--green)" />
      </div>

      {/* Charts area */}
      <div className="bk-charts">
        {/* Category breakdown */}
        <div className="bk-chart-card">
          <h3 className="bk-chart-title">By Category</h3>
          <div className="bk-ring-legend">
            {Object.entries(byCat).map(([cat, val]) => {
              const pct = catTotal > 0 ? (Math.abs(val) / catTotal * 100) : 0;
              return (
                <div key={cat} className="bk-ring-item">
                  <span
                    className="bk-ring-dot"
                    style={{ background: CATEGORY_COLORS[cat] ?? "var(--text-muted)" }}
                  />
                  <span className="bk-ring-label">{CATEGORY_LABELS[cat] || cat}</span>
                  <span className="bk-ring-value">{formatUSD(Math.abs(val))}</span>
                  <span className="bk-ring-pct">{pct.toFixed(1)}%</span>
                </div>
              );
            })}
          </div>
          {/* Simple stacked bar as ring chart substitute */}
          <div className="bk-stacked-bar">
            {Object.entries(byCat).map(([cat, val]) => {
              const pct = catTotal > 0 ? (Math.abs(val) / catTotal * 100) : 0;
              return (
                <div
                  key={cat}
                  className="bk-stacked-seg"
                  style={{
                    width: `${pct}%`,
                    background: CATEGORY_COLORS[cat] ?? "var(--text-muted)",
                  }}
                  title={`${CATEGORY_LABELS[cat] || cat}: ${formatUSD(Math.abs(val))}`}
                />
              );
            })}
          </div>
        </div>

        {/* By asset */}
        <div className="bk-chart-card">
          <h3 className="bk-chart-title">By Asset (Net Exposure)</h3>
          {topAssets.map(([asset, val]) => (
            <div key={asset} className="bk-hbar-row">
              <span className="bk-hbar-label">{asset}</span>
              <div className="bk-hbar-track">
                <div
                  className="bk-hbar-fill"
                  style={{
                    width: `${Math.abs(val) / maxAsset * 100}%`,
                    background: (val) >= 0 ? "var(--green)" : "var(--red)",
                  }}
                />
              </div>
              <span className="bk-hbar-value">{formatUSD(val)}</span>
            </div>
          ))}
        </div>

        {/* By counterparty */}
        <div className="bk-chart-card">
          <h3 className="bk-chart-title">Top Counterparties</h3>
          {topCpty.map(([name, val]) => (
            <div key={name} className="bk-hbar-row">
              <span className="bk-hbar-label bk-hbar-label-long" title={name}>
                {name.length > 30 ? name.slice(0, 28) + "..." : name}
              </span>
              <div className="bk-hbar-track">
                <div
                  className="bk-hbar-fill"
                  style={{
                    width: `${Math.abs(val) / maxCpty * 100}%`,
                    background: "var(--accent)",
                  }}
                />
              </div>
              <span className="bk-hbar-value">{formatUSD(Math.abs(val))}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bk-stat-card">
      <div className="bk-stat-label">{label}</div>
      <div className="bk-stat-value" style={color ? { color } : undefined}>{value}</div>
    </div>
  );
}

// ── DeFi Positions Tab ───────────────────────────────────────────────

function DefiTab({
  rows,
  expanded,
  onToggle,
}: {
  rows: DefiVsMarketRow[] | null;
  expanded: Set<number>;
  onToggle: (id: number) => void;
}) {
  if (!rows) return <div className="bk-loading">Loading DeFi positions...</div>;
  if (rows.length === 0) return <div className="bk-empty">No DeFi positions found in this book.</div>;

  // Summary banner
  const totalUsd = rows.reduce((a, r) => a + r.principal_usd, 0);
  const waOur = rows.reduce((a, r) => a + r.our_rate_pct * r.principal_usd, 0) / (totalUsd || 1);
  const withMarket = rows.filter((r) => r.best_market_rate_pct != null);
  const waMarket = withMarket.length > 0
    ? withMarket.reduce((a, r) => a + (r.best_market_rate_pct ?? 0) * r.principal_usd, 0)
      / withMarket.reduce((a, r) => a + r.principal_usd, 0)
    : 0;
  const deltaBps = Math.round((waOur - waMarket) * 100);

  return (
    <div className="bk-defi">
      <div className="bk-banner">
        DeFi positions earning <strong>{formatAPY(waOur)}</strong> weighted avg
        vs <strong>{formatAPY(waMarket)}</strong> market avg
        {" — "}
        <span style={{ color: deltaColor(deltaBps) }}>
          {fmtBps(deltaBps)} {deltaBps >= 0 ? "above" : "below"} market
        </span>
      </div>

      <div className="bk-table-wrap">
        <table className="bk-table">
          <thead>
            <tr>
              <th></th>
              <th>Protocol</th>
              <th>Chain</th>
              <th>Asset</th>
              <th>Side</th>
              <th className="bk-num">Our Rate</th>
              <th className="bk-num">Market Rate</th>
              <th className="bk-num">Best Rate</th>
              <th className="bk-num">Delta (bps)</th>
              <th className="bk-num">Principal</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isExpanded = expanded.has(r.loan_id);
              return (
                <tr key={r.loan_id}>
                  <td>
                    <button className="bk-expand-btn" onClick={() => onToggle(r.loan_id)}>
                      {isExpanded ? "\u25BC" : "\u25B6"}
                    </button>
                  </td>
                  <td>{r.protocol_name ?? "--"}</td>
                  <td>{r.protocol_chain ?? "--"}</td>
                  <td className="bk-mono">{r.asset}</td>
                  <td>
                    <span className={`bk-side-badge bk-side-${r.direction === "Loan_Out" ? "supply" : "borrow"}`}>
                      {r.direction === "Loan_Out" ? "Supply" : "Borrow"}
                    </span>
                  </td>
                  <td className="bk-num">{formatAPY(r.our_rate_pct)}</td>
                  <td className="bk-num" style={{ color: "var(--text-secondary)" }}>
                    {r.matched_market_rate_pct != null ? formatAPY(r.matched_market_rate_pct) : "--"}
                  </td>
                  <td className="bk-num">
                    {r.best_market_rate_pct != null ? (
                      <span title={r.best_market_protocol ?? ""}>
                        {formatAPY(r.best_market_rate_pct)}
                      </span>
                    ) : "--"}
                  </td>
                  <td className="bk-num" style={{ color: deltaColor(r.delta_vs_best_bps) }}>
                    {fmtBps(r.delta_vs_best_bps)}
                  </td>
                  <td className="bk-num">{formatUSD(r.principal_usd)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Bilateral Tab ────────────────────────────────────────────────────

function BilateralTab({ rows }: { rows: BilateralPricingRow[] | null }) {
  if (!rows) return <div className="bk-loading">Loading bilateral positions...</div>;
  if (rows.length === 0) return <div className="bk-empty">No bilateral positions found.</div>;

  const loanOuts = rows.filter((r) => r.direction === "Loan_Out");
  const totalLent = loanOuts.reduce((a, r) => a + r.principal_usd, 0);
  const waLending = totalLent > 0
    ? loanOuts.reduce((a, r) => a + r.our_rate_pct * r.principal_usd, 0) / totalLent
    : 0;
  const withDefi = loanOuts.filter((r) => r.best_defi_rate_pct != null);
  const waDefi = withDefi.length > 0
    ? withDefi.reduce((a, r) => a + (r.best_defi_rate_pct ?? 0) * r.principal_usd, 0)
      / withDefi.reduce((a, r) => a + r.principal_usd, 0)
    : 0;
  const premiumBps = Math.round((waLending - waDefi) * 100);

  return (
    <div className="bk-bilateral">
      <div className="bk-banner">
        Avg bilateral lending rate: <strong>{formatAPY(waLending)}</strong>
        {" | "}Avg DeFi alternative: <strong>{formatAPY(waDefi)}</strong>
        {" | "}Bilateral premium:{" "}
        <span style={{ color: deltaColor(premiumBps) }}>
          {fmtBps(premiumBps)}
        </span>
      </div>

      <div className="bk-table-wrap">
        <table className="bk-table">
          <thead>
            <tr>
              <th>Counterparty</th>
              <th>Asset</th>
              <th>Dir</th>
              <th className="bk-num">Rate</th>
              <th className="bk-num">Principal</th>
              <th>Tenor</th>
              <th>Collat.</th>
              <th className="bk-num">DeFi Rate</th>
              <th className="bk-num">Premium</th>
              <th>Assessment</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.loan_id}>
                <td title={r.counterparty_name}>
                  {r.counterparty_name.length > 28
                    ? r.counterparty_name.slice(0, 26) + "..."
                    : r.counterparty_name}
                </td>
                <td className="bk-mono">{r.asset}</td>
                <td>
                  <span className={`bk-side-badge bk-side-${r.direction === "Loan_Out" ? "supply" : "borrow"}`}>
                    {r.direction === "Loan_Out" ? "Out" : "In"}
                  </span>
                </td>
                <td className="bk-num">{formatAPY(r.our_rate_pct)}</td>
                <td className="bk-num">{formatUSD(r.principal_usd)}</td>
                <td>{r.tenor}</td>
                <td>{r.is_collateralized ? "Yes" : "No"}</td>
                <td className="bk-num">
                  {r.best_defi_rate_pct != null ? (
                    <span title={r.best_defi_protocol ?? ""}>
                      {formatAPY(r.best_defi_rate_pct)}
                    </span>
                  ) : "--"}
                </td>
                <td className="bk-num" style={{ color: deltaColor(r.premium_discount_bps) }}>
                  {fmtBps(r.premium_discount_bps)}
                </td>
                <td>
                  <span
                    className="bk-assessment-badge"
                    style={{ color: ASSESSMENT_COLORS[r.assessment] ?? "var(--text-muted)" }}
                  >
                    {r.assessment.replace(/_/g, " ")}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Collateral Tab ───────────────────────────────────────────────────

function CollateralTab({
  efficiency,
  expanded,
  onToggle,
}: {
  collateral: BookCollateralData | null;
  efficiency: CollateralEfficiencyRow[] | null;
  expanded: Set<number>;
  onToggle: (id: number) => void;
}) {
  if (!efficiency) return <div className="bk-loading">Loading collateral data...</div>;
  if (efficiency.length === 0) return <div className="bk-empty">No collateral data found.</div>;

  const totalCol = efficiency.reduce((a, r) => a + r.total_collateral_usd, 0);
  const totalReq = efficiency.reduce((a, r) => a + r.total_required_usd, 0);
  const totalExcess = totalCol - totalReq;
  const totalYield = efficiency.reduce((a, r) => a + r.potential_yield_usd, 0);

  return (
    <div className="bk-collateral">
      {/* Aggregate banner */}
      <div className="bk-banner">
        Total collateral: <strong>{formatUSD(totalCol)}</strong>
        {" | "}Required: <strong>{formatUSD(totalReq)}</strong>
        {" | "}Excess:{" "}
        <strong style={{ color: totalExcess >= 0 ? "var(--green)" : "var(--red)" }}>
          {formatUSD(totalExcess)}
        </strong>
        {totalYield > 0 && (
          <>
            {" | "}Potential yield on excess:{" "}
            <strong style={{ color: "var(--accent)" }}>{formatUSD(totalYield)}/yr</strong>
          </>
        )}
      </div>

      {/* Counterparty accordions */}
      <div className="bk-collateral-list">
        {efficiency.map((row) => {
          const isOpen = expanded.has(row.customer_id);
          return (
            <div key={row.customer_id} className="bk-cpty-card">
              <button
                className="bk-cpty-header"
                onClick={() => onToggle(row.customer_id)}
              >
                <span className="bk-cpty-expand">{isOpen ? "\u25BC" : "\u25B6"}</span>
                <span className="bk-cpty-name">{row.counterparty_name}</span>
                <span className={`bk-cpty-status bk-cpty-${row.status}`}>
                  {row.status.replace(/_/g, " ")}
                </span>
                <span className="bk-cpty-loans">{formatUSD(row.total_loans_usd)} loans</span>
                <span className="bk-cpty-col">{formatUSD(row.total_collateral_usd)} collateral</span>
                {row.excess_usd !== 0 && (
                  <span
                    className="bk-cpty-excess"
                    style={{ color: row.excess_usd >= 0 ? "var(--green)" : "var(--red)" }}
                  >
                    {row.excess_usd >= 0 ? "+" : ""}{formatUSD(row.excess_usd)} excess
                  </span>
                )}
                {row.rehypothecation_allowed && (
                  <span className="bk-cpty-rehyp">Rehyp</span>
                )}
              </button>

              {isOpen && (
                <div className="bk-cpty-detail">
                  <div className="bk-cpty-metrics">
                    <div>Required: {formatUSD(row.total_required_usd)}</div>
                    <div>Excess: {row.excess_pct.toFixed(1)}%</div>
                    <div>Assets: {row.collateral_assets.join(", ")}</div>
                    {row.potential_yield_usd > 0 && (
                      <div style={{ color: "var(--accent)" }}>
                        Potential yield: {formatUSD(row.potential_yield_usd)}/yr
                      </div>
                    )}
                  </div>
                  {row.potential_yield_details.length > 0 && (
                    <div className="bk-cpty-yields">
                      {row.potential_yield_details.map((d, i) => (
                        <div key={i} className="bk-cpty-yield-row">
                          <span className="bk-mono">{d.asset}</span>
                          <span>{d.protocol ?? "--"}</span>
                          <span>{formatAPY(d.best_rate_pct)}</span>
                          <span style={{ color: "var(--green)" }}>{formatUSD(d.estimated_yield_usd)}/yr</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Optimization Tab ─────────────────────────────────────────────────

function OptimizationTab({
  analysis,
  analyzing,
  onAnalyze,
  expanded,
  onToggle,
}: {
  analysis: BookAnalysisResult | null;
  analyzing: boolean;
  onAnalyze: () => void;
  expanded: Set<string>;
  onToggle: (id: string) => void;
}) {
  return (
    <div className="bk-optimization">
      <div className="bk-opt-header">
        <button
          className="bk-btn bk-btn-primary"
          onClick={onAnalyze}
          disabled={analyzing}
        >
          {analyzing ? "Analyzing..." : "Analyze Book"}
        </button>
        {analysis && (
          <span className="bk-opt-meta">
            {analysis.total_suggestions} suggestions | {analysis.total_positions_analyzed} positions |{" "}
            {analysis.total_opportunities_scanned} market opportunities scanned
          </span>
        )}
      </div>

      {analysis && analysis.total_suggestions > 0 && (
        <>
          <div className="bk-banner bk-banner-accent">
            Found <strong>{analysis.total_suggestions}</strong> suggestions with total potential annual impact of{" "}
            <strong style={{ color: "var(--green)" }}>
              {formatUSD(analysis.total_estimated_annual_impact_usd)}
            </strong>
            {" | "}
            {analysis.suggestions_by_priority.high ?? 0} high |{" "}
            {analysis.suggestions_by_priority.medium ?? 0} medium |{" "}
            {analysis.suggestions_by_priority.low ?? 0} low
          </div>

          <div className="bk-suggestions">
            {analysis.suggestions.map((s) => {
              const isOpen = expanded.has(s.suggestion_id);
              return (
                <div key={s.suggestion_id} className={`bk-suggestion bk-suggestion-${s.priority}`}>
                  <button
                    className="bk-suggestion-header"
                    onClick={() => onToggle(s.suggestion_id)}
                  >
                    <span className="bk-suggestion-icon">{SUGGESTION_ICONS[s.type] ?? "?"}</span>
                    <span className="bk-suggestion-type">{SUGGESTION_LABELS[s.type] ?? s.type}</span>
                    <span
                      className="bk-suggestion-priority"
                      style={{ color: PRIORITY_COLORS[s.priority] }}
                    >
                      {s.priority}
                    </span>
                    <span className="bk-suggestion-impact" style={{ color: "var(--green)" }}>
                      {s.estimated_annual_impact_usd > 0
                        ? `+${formatUSD(s.estimated_annual_impact_usd)}/yr`
                        : "--"}
                    </span>
                    {s.rate_improvement_bps > 0 && (
                      <span className="bk-suggestion-bps">+{s.rate_improvement_bps.toFixed(0)}bps</span>
                    )}
                    <span className="bk-suggestion-expand">{isOpen ? "\u25BC" : "\u25B6"}</span>
                  </button>

                  <div className="bk-suggestion-desc">{s.action_description}</div>

                  {isOpen && (
                    <div className="bk-suggestion-detail">
                      <div className="bk-suggestion-metrics">
                        <div>Current: {formatAPY(s.current_rate_pct)} | Market: {formatAPY(s.market_rate_pct)}</div>
                        {s.switching_cost_usd > 0 && (
                          <div>Switching cost: {formatUSD(s.switching_cost_usd)} | Break-even: {s.break_even_days}d</div>
                        )}
                      </div>
                      <div className="bk-suggestion-risk">{s.risk_assessment}</div>
                      <div className="bk-suggestion-steps">
                        <strong>Execution steps:</strong>
                        <ol>
                          {s.execution_steps.map((step, i) => (
                            <li key={i}>{step}</li>
                          ))}
                        </ol>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {analysis && analysis.total_suggestions === 0 && (
        <div className="bk-empty">No optimization suggestions found. Your book is well-positioned.</div>
      )}
    </div>
  );
}

// ── Maturity Tab ─────────────────────────────────────────────────────

function MaturityTab({ rows }: { rows: MaturityCalendarRow[] | null }) {
  if (!rows) return <div className="bk-loading">Loading maturity calendar...</div>;
  if (rows.length === 0) return <div className="bk-empty">No fixed-term positions with maturities.</div>;

  const STATUS_COLORS: Record<string, string> = {
    expired: "var(--red)",
    imminent: "var(--red)",
    upcoming: "var(--yellow)",
    scheduled: "var(--text-secondary)",
  };

  return (
    <div className="bk-maturities">
      <div className="bk-table-wrap">
        <table className="bk-table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Maturity</th>
              <th className="bk-num">Days</th>
              <th>Counterparty</th>
              <th>Asset</th>
              <th>Dir</th>
              <th className="bk-num">Principal</th>
              <th className="bk-num">Our Rate</th>
              <th className="bk-num">Market Rate</th>
              <th className="bk-num">Delta</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.loan_id} className={r.status === "expired" || r.status === "imminent" ? "bk-row-alert" : ""}>
                <td>
                  <span
                    className="bk-maturity-status"
                    style={{ color: STATUS_COLORS[r.status] ?? "var(--text-muted)" }}
                  >
                    {r.status}
                  </span>
                </td>
                <td className="bk-mono">{r.maturity_date}</td>
                <td className="bk-num">{r.days_to_maturity}</td>
                <td title={r.counterparty_name}>
                  {r.counterparty_name.length > 25
                    ? r.counterparty_name.slice(0, 23) + "..."
                    : r.counterparty_name}
                </td>
                <td className="bk-mono">{r.asset}</td>
                <td>
                  <span className={`bk-side-badge bk-side-${r.direction === "Loan_Out" ? "supply" : "borrow"}`}>
                    {r.direction === "Loan_Out" ? "Out" : "In"}
                  </span>
                </td>
                <td className="bk-num">{formatUSD(r.principal_usd)}</td>
                <td className="bk-num">{formatAPY(r.interest_rate_pct)}</td>
                <td className="bk-num">
                  {r.current_market_rate_pct != null ? (
                    <span title={r.market_protocol ?? ""}>
                      {formatAPY(r.current_market_rate_pct)}
                    </span>
                  ) : "--"}
                </td>
                <td className="bk-num" style={{ color: deltaColor(r.rate_delta_bps) }}>
                  {fmtBps(r.rate_delta_bps)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
