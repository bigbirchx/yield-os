import type {
  DerivativesOverview,
  LendingOverview,
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
