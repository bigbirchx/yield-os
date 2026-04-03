"use client";
/**
 * Horizontal filter bar for opportunity / token tables.
 *
 * All filters sync to URL query params so views are bookmarkable.
 * Emits filter state changes via onFiltersChange callback.
 */
import { useCallback, useMemo } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import type { OpportunityFilters } from "@/types/api";

// ---------------------------------------------------------------------------
// Filter option sets
// ---------------------------------------------------------------------------

const UMBRELLA_OPTIONS = ["USD", "ETH", "BTC", "SOL", "HYPE", "OTHER"];
const SIDE_OPTIONS = ["SUPPLY", "BORROW"];
const TYPE_OPTIONS = ["LENDING", "VAULT", "STAKING", "FUNDING_RATE", "BASIS_TRADE", "PENDLE_PT", "PENDLE_YT", "CEX_EARN"];
const CHAIN_OPTIONS = [
  "ETHEREUM", "ARBITRUM", "OPTIMISM", "BASE", "POLYGON", "AVALANCHE",
  "BSC", "SOLANA", "TRON", "HYPERLIQUID",
];

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface FilterBarProps {
  onFiltersChange?: (filters: OpportunityFilters) => void;
  /** Show venue filter (default true) */
  showVenue?: boolean;
  /** Show chain filter (default true) */
  showChain?: boolean;
  /** Show type filter (default true) */
  showType?: boolean;
  /** Show toggle switches (default true) */
  showToggles?: boolean;
  /** Show min APY / TVL inputs (default true) */
  showMinInputs?: boolean;
  /** Show search box (default true) */
  showSearch?: boolean;
  /** Available venues (if not provided, no venue filter) */
  venues?: string[];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function FilterBar({
  onFiltersChange,
  showVenue = true,
  showChain = true,
  showType = true,
  showToggles = true,
  showMinInputs = true,
  showSearch = true,
  venues,
}: FilterBarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Read current filter state from URL
  const filters: OpportunityFilters = useMemo(() => ({
    umbrella: searchParams.get("umbrella") ?? undefined,
    side: searchParams.get("side") ?? undefined,
    type: searchParams.get("type") ?? undefined,
    chain: searchParams.get("chain") ?? undefined,
    venue: searchParams.get("venue") ?? undefined,
    asset: searchParams.get("asset") ?? undefined,
    min_apy: searchParams.get("min_apy") ? Number(searchParams.get("min_apy")) : undefined,
    min_tvl: searchParams.get("min_tvl") ? Number(searchParams.get("min_tvl")) : undefined,
    exclude_amm_lp: searchParams.get("exclude_amm_lp") === "true",
    exclude_pendle: searchParams.get("exclude_pendle") === "true",
    sort_by: searchParams.get("sort_by") ?? undefined,
  }), [searchParams]);

  // Update URL and notify parent
  const setFilter = useCallback(
    (key: string, value: string | number | boolean | undefined) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value == null || value === "" || value === false) {
        params.delete(key);
      } else {
        params.set(key, String(value));
      }
      // Reset offset when filters change
      params.delete("offset");
      const qs = params.toString();
      router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });

      // Build new filters and notify
      const newFilters: OpportunityFilters = {};
      for (const [k, v] of params.entries()) {
        (newFilters as Record<string, string>)[k] = v;
      }
      onFiltersChange?.(newFilters);
    },
    [searchParams, router, pathname, onFiltersChange]
  );

  const toggleFilter = useCallback(
    (key: string, value: string) => {
      const current = searchParams.get(key);
      setFilter(key, current === value ? undefined : value);
    },
    [searchParams, setFilter]
  );

  return (
    <div className="fb-root">
      {/* Search box */}
      {showSearch && (
        <div className="fb-search">
          <input
            type="text"
            className="fb-input"
            placeholder="Search asset..."
            defaultValue={filters.asset ?? ""}
            onChange={(e) => {
              const v = e.target.value.trim();
              setFilter("asset", v || undefined);
            }}
          />
        </div>
      )}

      {/* Umbrella chips */}
      <div className="fb-group">
        <span className="fb-label">Umbrella</span>
        <div className="fb-chips">
          {UMBRELLA_OPTIONS.map((u) => (
            <button
              key={u}
              className={`fb-chip ${filters.umbrella === u ? "fb-chip-active" : ""}`}
              onClick={() => toggleFilter("umbrella", u)}
            >
              {u}
            </button>
          ))}
        </div>
      </div>

      {/* Side chips */}
      <div className="fb-group">
        <span className="fb-label">Side</span>
        <div className="fb-chips">
          {SIDE_OPTIONS.map((s) => (
            <button
              key={s}
              className={`fb-chip ${filters.side === s ? "fb-chip-active" : ""}`}
              onClick={() => toggleFilter("side", s)}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Type chips */}
      {showType && (
        <div className="fb-group">
          <span className="fb-label">Type</span>
          <div className="fb-chips">
            {TYPE_OPTIONS.map((t) => (
              <button
                key={t}
                className={`fb-chip fb-chip-sm ${filters.type === t ? "fb-chip-active" : ""}`}
                onClick={() => toggleFilter("type", t)}
              >
                {t.replace(/_/g, " ")}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Chain chips */}
      {showChain && (
        <div className="fb-group">
          <span className="fb-label">Chain</span>
          <div className="fb-chips">
            {CHAIN_OPTIONS.map((c) => (
              <button
                key={c}
                className={`fb-chip fb-chip-sm ${filters.chain === c ? "fb-chip-active" : ""}`}
                onClick={() => toggleFilter("chain", c)}
              >
                {c}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Venue chips */}
      {showVenue && venues && venues.length > 0 && (
        <div className="fb-group">
          <span className="fb-label">Venue</span>
          <div className="fb-chips">
            {venues.map((v) => (
              <button
                key={v}
                className={`fb-chip fb-chip-sm ${filters.venue === v ? "fb-chip-active" : ""}`}
                onClick={() => toggleFilter("venue", v)}
              >
                {v}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Toggles and min inputs */}
      <div className="fb-controls">
        {showToggles && (
          <>
            <label className="fb-toggle">
              <input
                type="checkbox"
                checked={filters.exclude_amm_lp ?? true}
                onChange={(e) => setFilter("exclude_amm_lp", e.target.checked || undefined)}
              />
              <span>Exclude AMM LP</span>
            </label>
            <label className="fb-toggle">
              <input
                type="checkbox"
                checked={filters.exclude_pendle ?? false}
                onChange={(e) => setFilter("exclude_pendle", e.target.checked || undefined)}
              />
              <span>Exclude Pendle</span>
            </label>
          </>
        )}

        {showMinInputs && (
          <>
            <div className="fb-min-input">
              <span className="fb-label">Min APY</span>
              <input
                type="number"
                className="fb-input fb-input-sm"
                placeholder="0"
                step="0.5"
                min="0"
                defaultValue={filters.min_apy ?? ""}
                onBlur={(e) => {
                  const v = parseFloat(e.target.value);
                  setFilter("min_apy", isNaN(v) || v <= 0 ? undefined : v);
                }}
              />
            </div>
            <div className="fb-min-input">
              <span className="fb-label">Min TVL</span>
              <input
                type="number"
                className="fb-input fb-input-sm"
                placeholder="0"
                step="100000"
                min="0"
                defaultValue={filters.min_tvl ?? ""}
                onBlur={(e) => {
                  const v = parseFloat(e.target.value);
                  setFilter("min_tvl", isNaN(v) || v <= 0 ? undefined : v);
                }}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
