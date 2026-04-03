"use client";
/**
 * SWR-based data fetching hooks with auto-refresh for live dashboard data.
 *
 * All hooks poll at 60-second intervals by default so the dashboard stays
 * fresh without manual refresh. Health polling is faster (30s).
 */
import useSWR from "swr";
import type {
  MarketOpportunity,
  OpportunityFilters,
  OpportunityRatePoint,
  OpportunitySummary,
  PaginatedResponse,
  Token,
  TokenDetail,
  TokenFilters,
  WorkerHealth,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function jsonFetcher<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

function buildURL(path: string, params?: Record<string, unknown>): string {
  const url = new URL(path, API_URL);
  if (params) {
    for (const [key, val] of Object.entries(params)) {
      if (val != null && val !== "" && val !== false) {
        url.searchParams.set(key, String(val));
      }
    }
  }
  return url.toString();
}

// ---------------------------------------------------------------------------
// Opportunities
// ---------------------------------------------------------------------------

export function useOpportunities(filters: OpportunityFilters = {}) {
  const url = buildURL("/api/opportunities", filters as Record<string, unknown>);
  return useSWR<PaginatedResponse<MarketOpportunity>>(url, jsonFetcher, {
    refreshInterval: 60_000,
    fallbackData: { data: [], pagination: { total: 0, limit: 100, offset: 0, has_more: false } },
  });
}

export function useOpportunitySummary() {
  return useSWR<OpportunitySummary>(
    buildURL("/api/opportunities/summary"),
    jsonFetcher,
    { refreshInterval: 60_000 }
  );
}

// ---------------------------------------------------------------------------
// Tokens
// ---------------------------------------------------------------------------

export function useTokens(filters: TokenFilters = {}) {
  const url = buildURL("/api/tokens", filters as Record<string, unknown>);
  return useSWR<PaginatedResponse<Token>>(url, jsonFetcher, {
    refreshInterval: 60_000,
    fallbackData: { data: [], pagination: { total: 0, limit: 50, offset: 0, has_more: false } },
  });
}

export function useToken(canonicalId: string | null) {
  return useSWR<TokenDetail>(
    canonicalId ? buildURL(`/api/tokens/${canonicalId}`) : null,
    jsonFetcher,
    { refreshInterval: 60_000 }
  );
}

// ---------------------------------------------------------------------------
// Worker Health
// ---------------------------------------------------------------------------

export function useWorkerHealth() {
  return useSWR<WorkerHealth>(
    buildURL("/api/admin/worker-health"),
    jsonFetcher,
    { refreshInterval: 30_000 }
  );
}

// ---------------------------------------------------------------------------
// API Health
// ---------------------------------------------------------------------------

export function useHealth() {
  return useSWR<{ status: string; timestamp: string; db: string; worker_status?: string }>(
    buildURL("/api/health"),
    jsonFetcher,
    { refreshInterval: 30_000 }
  );
}

// ---------------------------------------------------------------------------
// Opportunity history — batched multi-ID fetch for rate-change tracking
// ---------------------------------------------------------------------------

/**
 * Fetch history for up to 10 opportunity IDs in parallel.
 * Returns a map of opportunityId → rate history points.
 * Refreshes every 5 minutes (history data changes slowly).
 */
export function useOpportunityHistories(ids: string[], days = 2) {
  const sliced = ids.slice(0, 10);
  const key =
    sliced.length > 0
      ? `opp-histories:${sliced.join(",")}:days=${days}`
      : null;

  return useSWR<Record<string, OpportunityRatePoint[]>>(
    key,
    async () => {
      const entries = await Promise.all(
        sliced.map(async (id) => {
          try {
            const url = buildURL(
              `/api/opportunities/${encodeURIComponent(id)}/history`,
              { days }
            );
            const data = await jsonFetcher<OpportunityRatePoint[]>(url);
            return [id, data] as const;
          } catch {
            return [id, [] as OpportunityRatePoint[]] as const;
          }
        })
      );
      return Object.fromEntries(entries);
    },
    { refreshInterval: 300_000 }
  );
}
