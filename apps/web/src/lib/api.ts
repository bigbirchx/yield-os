import type {
  ApiUsage,
  AssetDetail,
  AssetHistory,
  AssetMarketHistory,
  BorrowDemandAnalysis,
  DerivativesOverview,
  DerivativesSnapshot,
  GlobalMarket,
  LendingOverview,
  MarketOpportunity,
  MarketSnapshot,
  OpportunityFilters,
  OpportunityRatePoint,
  OpportunitySummary,
  PaginatedResponse,
  ProtocolRiskParams,
  RefreshResult,
  RouteOptimizerResult,
  SourceStatus,
  StakingSnapshot,
  Token,
  TokenDetail,
  TokenFilters,
  WorkerHealth,
} from "@/types/api";

// Server Components run inside the Docker network and must use the internal
// hostname. Browser (client) fetches use the public host-accessible URL.
const API_URL =
  (typeof window === "undefined" ? process.env.API_INTERNAL_URL : null) ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000";

// Shared fetch helper — throws on non-2xx, returns null on network errors
// so pages can render empty states instead of hard-crashing.
async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      cache: "no-store",
      ...options,
    });
    if (!res.ok) {
      console.error(`API ${path} returned ${res.status}`);
      return null;
    }
    return res.json() as Promise<T>;
  } catch (err) {
    console.error(`API ${path} failed:`, err);
    return null;
  }
}

export async function fetchHealth() {
  return apiFetch<{ status: string; timestamp: string; db: string }>(
    "/api/health"
  );
}

export async function fetchDerivativesOverview(
  symbols = ["BTC", "ETH", "SOL"]
): Promise<DerivativesOverview[]> {
  const qs = symbols.map((s) => `symbols=${s}`).join("&");
  return (
    (await apiFetch<DerivativesOverview[]>(
      `/api/derivatives/overview?${qs}`
    )) ?? []
  );
}

export async function fetchLendingOverview(
  symbols = ["USDC", "USDT", "ETH", "WBTC", "SOL", "DAI"]
): Promise<LendingOverview[]> {
  const qs = symbols.map((s) => `symbols=${s}`).join("&");
  return (
    (await apiFetch<LendingOverview[]>(`/api/lending/overview?${qs}`)) ?? []
  );
}

export async function fetchAssetLending(symbol: string): Promise<LendingOverview | null> {
  const data = await apiFetch<LendingOverview[]>(
    `/api/lending/overview?symbols=${symbol}`
  );
  return data?.[0] ?? null;
}

export async function fetchAssetDerivatives(
  symbol: string
): Promise<DerivativesSnapshot[]> {
  return (
    (await apiFetch<DerivativesSnapshot[]>(`/api/derivatives/${symbol}`)) ?? []
  );
}

export async function fetchAssetDerivativesHistory(
  symbol: string,
  days = 30
): Promise<DerivativesSnapshot[]> {
  return (
    (await apiFetch<DerivativesSnapshot[]>(
      `/api/derivatives/${symbol}/history?days=${days}`
    )) ?? []
  );
}

export async function fetchAssetStaking(
  symbol: string
): Promise<StakingSnapshot[]> {
  return (
    (await apiFetch<StakingSnapshot[]>(`/api/staking/${symbol}`)) ?? []
  );
}

export async function fetchAssetHistory(
  symbol: string,
  days = 30
): Promise<AssetHistory | null> {
  return apiFetch<AssetHistory>(
    `/api/assets/${symbol}/history?days=${days}`
  );
}

export async function fetchRouteOptimizer(
  symbol: string,
  requestSizeUsd = 10_000_000
): Promise<RouteOptimizerResult | null> {
  return apiFetch<RouteOptimizerResult>(
    `/api/assets/${symbol}/route-optimizer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_size_usd: requestSizeUsd }),
    }
  );
}

export async function fetchBorrowDemand(
  symbol: string,
  days = 30
): Promise<BorrowDemandAnalysis | null> {
  return apiFetch<BorrowDemandAnalysis>(
    `/api/assets/${symbol}/borrow-demand?days=${days}`
  );
}

export async function fetchLtvMatrix(
  assets?: string[],
  protocols?: string[]
): Promise<ProtocolRiskParams[]> {
  const body: Record<string, string[]> = {};
  if (assets?.length) body.assets = assets;
  if (protocols?.length) body.protocols = protocols;

  return (
    (await apiFetch<ProtocolRiskParams[]>("/api/lending/ltv-matrix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })) ?? []
  );
}

// -----------------------------------------------------------------------
// CoinGecko reference layer
// -----------------------------------------------------------------------

export async function fetchReferenceAssets(
  symbols?: string[]
): Promise<MarketSnapshot[]> {
  const qs = symbols?.map((s) => `symbols=${s}`).join("&") ?? "";
  return (
    (await apiFetch<MarketSnapshot[]>(
      `/api/reference/assets${qs ? `?${qs}` : ""}`
    )) ?? []
  );
}

export async function fetchReferenceAsset(
  symbol: string
): Promise<AssetDetail | null> {
  return apiFetch<AssetDetail>(`/api/reference/assets/${symbol}`);
}

export async function fetchReferenceHistory(
  symbol: string,
  days = 90
): Promise<AssetMarketHistory | null> {
  return apiFetch<AssetMarketHistory>(
    `/api/reference/history/${symbol}?days=${days}`
  );
}

export async function fetchGlobalMarket(): Promise<GlobalMarket | null> {
  return apiFetch<GlobalMarket>("/api/reference/global");
}

export async function fetchApiUsage(): Promise<ApiUsage | null> {
  return apiFetch<ApiUsage>("/api/reference/usage");
}

/**
 * Fetch the current freshness status of every configured data source.
 * Always uses the public API URL so this is safe for client-side calls.
 */
export async function fetchSources(): Promise<SourceStatus[]> {
  const publicUrl =
    process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${publicUrl}/api/admin/sources`, {
      cache: "no-store",
    });
    if (!res.ok) return [];
    return res.json() as Promise<SourceStatus[]>;
  } catch {
    return [];
  }
}

// -----------------------------------------------------------------------
// Basis (dated futures term structure)
// -----------------------------------------------------------------------

export interface BasisTermRow {
  venue: string;
  contract: string;
  expiry: string;
  days_to_expiry: number;
  futures_price: number;
  index_price: number;
  basis_usd: number;
  basis_pct_ann: number | null;
  oi_coin: number | null;
  oi_usd: number | null;
  volume_24h_usd: number | null;
}

export interface BasisSnapshot {
  symbol: string;
  as_of: string;
  term_structure: BasisTermRow[];
}

export interface BasisHistoryPoint {
  timestamp: string;
  basis_usd: number | null;
  basis_pct_ann: number | null;
  futures_price: number | null;
  index_price: number | null;
  days_to_expiry: number | null;
}

export interface BasisHistory {
  symbol: string;
  venue: string;
  contract: string;
  expiry: string | null;
  series: BasisHistoryPoint[];
}

export async function fetchBasisSnapshot(symbol = "BTC"): Promise<BasisSnapshot | null> {
  return apiFetch<BasisSnapshot>(`/api/basis/snapshot?symbol=${symbol}`);
}

export async function fetchBasisHistory(
  symbol: string,
  venue: string,
  contract: string,
  days = 89
): Promise<BasisHistory | null> {
  const qs = new URLSearchParams({
    symbol,
    venue,
    contract,
    days: String(days),
  });
  return apiFetch<BasisHistory>(`/api/basis/history?${qs}`);
}

/**
 * Trigger a full data refresh across all connectors (DeFiLlama, Aave,
 * Morpho, Kamino, internal exchange).  Always uses the public API URL so
 * this is safe to call from client components.
 *
 * Returns the ingest result payload on success, or null on failure.
 */
export async function triggerIngest(): Promise<Record<string, unknown> | null> {
  const publicUrl =
    process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${publicUrl}/api/admin/ingest`, {
      method: "POST",
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json() as Promise<Record<string, unknown>>;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// DefiLlama free-tier helpers
// ---------------------------------------------------------------------------

export interface DLYieldPool {
  pool_id: string;
  project: string;
  chain: string;
  symbol: string;
  tvl_usd: number | null;
  apy: number | null;
  apy_base: number | null;
  apy_reward: number | null;
  stablecoin: boolean | null;
  il_risk: string | null;
  snapshot_at: string | null;
  source: string;
}

export interface DLProtocol {
  protocol_slug: string;
  protocol_name: string;
  category: string | null;
  chain: string | null;
  tvl_usd: number | null;
  change_1d: number | null;
  change_7d: number | null;
  change_1m: number | null;
  ts: string;
  source: string;
}

export interface DLStablecoin {
  stablecoin_id: string;
  symbol: string;
  circulating_usd: number | null;
  peg_type: string | null;
  peg_mechanism: string | null;
  chains: Record<string, unknown> | null;
  ts: string;
  source: string;
}

export interface DLMarketContext {
  source: string;
  as_of: string;
  context: {
    dex_volume?: { aggregate: number | null; protocols: { protocol: string; value_24h: number | null }[] };
    open_interest?: { aggregate: number | null; protocols: { protocol: string; value_24h: number | null }[] };
    fees_revenue?: { aggregate: number | null; protocols: { protocol: string; value_24h: number | null }[] };
  };
}

export async function fetchDLYields(
  symbol?: string,
  minTvl = 5_000_000,
  limit = 20
): Promise<DLYieldPool[]> {
  const qs = new URLSearchParams({ min_tvl: String(minTvl), limit: String(limit) });
  if (symbol) qs.set("symbol", symbol);
  return (await apiFetch<DLYieldPool[]>(`/api/defillama/yields?${qs}`)) ?? [];
}

export async function fetchDLProtocols(): Promise<DLProtocol[]> {
  return (await apiFetch<DLProtocol[]>("/api/defillama/protocols")) ?? [];
}

export async function fetchDLStablecoins(): Promise<DLStablecoin[]> {
  return (await apiFetch<DLStablecoin[]>("/api/defillama/stablecoins")) ?? [];
}

export async function fetchDLMarketContext(): Promise<DLMarketContext | null> {
  return apiFetch<DLMarketContext>("/api/defillama/market-context");
}

// ---------------------------------------------------------------------------
// Unified Opportunities
// ---------------------------------------------------------------------------

export async function fetchOpportunities(
  filters: OpportunityFilters = {}
): Promise<PaginatedResponse<MarketOpportunity>> {
  const qs = new URLSearchParams();
  for (const [key, val] of Object.entries(filters)) {
    if (val != null && val !== "" && val !== false) qs.set(key, String(val));
  }
  return (
    (await apiFetch<PaginatedResponse<MarketOpportunity>>(
      `/api/opportunities?${qs}`
    )) ?? { data: [], pagination: { total: 0, limit: 100, offset: 0, has_more: false } }
  );
}

export async function fetchOpportunity(
  id: string
): Promise<MarketOpportunity | null> {
  return apiFetch<MarketOpportunity>(`/api/opportunities/${encodeURIComponent(id)}`);
}

export async function fetchOpportunityHistory(
  id: string,
  days = 30
): Promise<OpportunityRatePoint[]> {
  return (
    (await apiFetch<OpportunityRatePoint[]>(
      `/api/opportunities/${encodeURIComponent(id)}/history?days=${days}`
    )) ?? []
  );
}

export async function fetchOpportunitySummary(): Promise<OpportunitySummary | null> {
  return apiFetch<OpportunitySummary>("/api/opportunities/summary");
}

export async function triggerOpportunityRefresh(): Promise<RefreshResult | null> {
  const publicUrl =
    process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${publicUrl}/api/opportunities/refresh`, {
      method: "POST",
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json() as Promise<RefreshResult>;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Token Universe
// ---------------------------------------------------------------------------

export async function fetchTokens(
  filters: TokenFilters = {}
): Promise<PaginatedResponse<Token>> {
  const qs = new URLSearchParams();
  for (const [key, val] of Object.entries(filters)) {
    if (val != null && val !== "") qs.set(key, String(val));
  }
  return (
    (await apiFetch<PaginatedResponse<Token>>(`/api/tokens?${qs}`)) ?? {
      data: [],
      pagination: { total: 0, limit: 50, offset: 0, has_more: false },
    }
  );
}

export async function fetchToken(
  canonicalId: string
): Promise<TokenDetail | null> {
  return apiFetch<TokenDetail>(`/api/tokens/${canonicalId}`);
}

// ---------------------------------------------------------------------------
// Yield Route Optimizer
// ---------------------------------------------------------------------------

import type {
  OptimizerResponse,
  OptimizerCompareResponse,
  OptimizerRequestConfig,
} from "@/types/api";

export async function fetchOptimizedRoutes(
  entryAsset: string,
  entryAmountUsd: number,
  holdingPeriodDays = 90,
  config: OptimizerRequestConfig = {}
): Promise<OptimizerResponse | null> {
  return apiFetch<OptimizerResponse>("/api/optimizer/routes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      entry_asset: entryAsset,
      entry_amount_usd: entryAmountUsd,
      holding_period_days: holdingPeriodDays,
      config,
    }),
  });
}

export async function fetchOptimizerCompare(
  entries: { entry_asset: string; entry_amount_usd: number }[],
  holdingPeriodDays = 90,
  config: OptimizerRequestConfig = {}
): Promise<OptimizerCompareResponse | null> {
  return apiFetch<OptimizerCompareResponse>("/api/optimizer/compare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      routes: entries,
      holding_period_days: holdingPeriodDays,
      config,
    }),
  });
}

export async function fetchQuickRoutes(
  asset: string,
  amount = 1_000_000
): Promise<OptimizerResponse | null> {
  return apiFetch<OptimizerResponse>(
    `/api/optimizer/quick?asset=${encodeURIComponent(asset)}&amount=${amount}`
  );
}

// ---------------------------------------------------------------------------
// Book / Portfolio
// ---------------------------------------------------------------------------

import type {
  BookImportResult,
  BookMeta,
  BookPosition,
  BookCollateralData,
  BookAnalysisResult,
  DefiVsMarketRow,
  BilateralPricingRow,
  CollateralEfficiencyRow,
  MaturityCalendarRow,
} from "@/types/api";

const PUBLIC_API =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function uploadBook(file: File): Promise<BookImportResult | null> {
  const formData = new FormData();
  formData.append("file", file);
  try {
    const res = await fetch(`${PUBLIC_API}/api/book/import`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) return null;
    return res.json() as Promise<BookImportResult>;
  } catch {
    return null;
  }
}

export async function fetchBook(bookId: string): Promise<BookMeta | null> {
  return apiFetch<BookMeta>(`/api/book/${bookId}`);
}

export async function fetchBookPositions(
  bookId: string,
  filters: { category?: string; asset?: string; counterparty?: string; min_rate?: number } = {}
): Promise<BookPosition[]> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v != null && v !== "") qs.set(k, String(v));
  }
  return (await apiFetch<BookPosition[]>(`/api/book/${bookId}/positions?${qs}`)) ?? [];
}

export async function fetchBookDefi(bookId: string): Promise<BookPosition[]> {
  return (await apiFetch<BookPosition[]>(`/api/book/${bookId}/defi`)) ?? [];
}

export async function fetchBookCollateral(bookId: string): Promise<BookCollateralData | null> {
  return apiFetch<BookCollateralData>(`/api/book/${bookId}/collateral`);
}

export async function fetchBookSummary(bookId: string): Promise<Record<string, unknown> | null> {
  return apiFetch<Record<string, unknown>>(`/api/book/${bookId}/summary`);
}

export async function refreshBookMatching(bookId: string): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(`${PUBLIC_API}/api/book/${bookId}/refresh-matching`, {
      method: "POST",
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json() as Promise<Record<string, unknown>>;
  } catch {
    return null;
  }
}

export async function analyzeBook(
  bookId: string,
  config: Record<string, unknown> = {}
): Promise<BookAnalysisResult | null> {
  try {
    const res = await fetch(`${PUBLIC_API}/api/book/${bookId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    });
    if (!res.ok) return null;
    return res.json() as Promise<BookAnalysisResult>;
  } catch {
    return null;
  }
}

export async function fetchDefiVsMarket(bookId: string): Promise<DefiVsMarketRow[]> {
  return (await apiFetch<DefiVsMarketRow[]>(`/api/book/${bookId}/defi-vs-market`)) ?? [];
}

export async function fetchBilateralPricing(bookId: string): Promise<BilateralPricingRow[]> {
  return (await apiFetch<BilateralPricingRow[]>(`/api/book/${bookId}/bilateral-pricing`)) ?? [];
}

export async function fetchCollateralEfficiency(bookId: string): Promise<CollateralEfficiencyRow[]> {
  return (await apiFetch<CollateralEfficiencyRow[]>(`/api/book/${bookId}/collateral-efficiency`)) ?? [];
}

export async function fetchMaturityCalendar(bookId: string): Promise<MaturityCalendarRow[]> {
  return (await apiFetch<MaturityCalendarRow[]>(`/api/book/${bookId}/maturity-calendar`)) ?? [];
}

// ---------------------------------------------------------------------------
// Worker Health
// ---------------------------------------------------------------------------

export async function fetchWorkerHealth(): Promise<WorkerHealth | null> {
  const publicUrl =
    process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${publicUrl}/api/admin/worker-health`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json() as Promise<WorkerHealth>;
  } catch {
    return null;
  }
}
