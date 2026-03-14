import type {
  AssetHistory,
  BorrowDemandAnalysis,
  DerivativesOverview,
  DerivativesSnapshot,
  LendingOverview,
  ProtocolRiskParams,
  StakingSnapshot,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
