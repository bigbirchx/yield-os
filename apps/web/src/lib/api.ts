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
  MarketSnapshot,
  ProtocolRiskParams,
  RouteOptimizerResult,
  SourceStatus,
  StakingSnapshot,
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
