"use client";
/**
 * Opportunities Explorer — interactive client layer.
 *
 * Renders stat cards, filter bar, and a sortable DataTable of every yield
 * opportunity. Data auto-refreshes via SWR every 60 seconds; rows whose APY
 * changed since the last refresh flash briefly to signal the update.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import DataTable, { type DataColumn } from "@/components/DataTable";
import FilterBar from "@/components/FilterBar";
import StatCard from "@/components/StatCard";
import AssetBadge from "@/components/AssetBadge";
import CapacityBar from "@/components/CapacityBar";
import VenueLogo from "@/components/VenueLogo";
import { useOpportunities, useOpportunitySummary } from "@/lib/hooks";
import {
  apyColor,
  formatAPY,
  formatUSD,
  getUmbrellaColor,
} from "@/lib/theme";
import type {
  MarketOpportunity,
  OpportunityFilters,
  OpportunitySummary,
  PaginatedResponse,
} from "@/types/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LiquidityInfo {
  has_lockup?: boolean;
  has_withdrawal_queue?: boolean;
  lockup_days?: number | null;
  current_queue_length_days?: number | null;
  available_liquidity_usd?: number | null;
}

interface Props {
  initialData: PaginatedResponse<MarketOpportunity>;
  initialSummary: OpportunitySummary | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const PAGE_SIZE = 100;

function relativeTime(isoString: string): string {
  const delta = (Date.now() - new Date(isoString).getTime()) / 1000;
  if (delta < 60) return "<1m";
  if (delta < 3600) return `${Math.floor(delta / 60)}m`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h`;
  return `${Math.floor(delta / 86400)}d`;
}

function liquidityLabel(raw: unknown): { label: string; color: string } {
  if (!raw || typeof raw !== "object") return { label: "Liquid", color: "var(--green)" };
  const liq = raw as LiquidityInfo;
  if (liq.has_lockup && liq.lockup_days) {
    return { label: `Locked ${liq.lockup_days}d`, color: "var(--red)" };
  }
  if (liq.has_withdrawal_queue && liq.current_queue_length_days) {
    return {
      label: `Queue ${liq.current_queue_length_days.toFixed(1)}d`,
      color: "var(--yellow)",
    };
  }
  return { label: "Liquid", color: "var(--green)" };
}

function durationLabel(row: MarketOpportunity): string {
  if (row.effective_duration === "FIXED_TERM") {
    if (row.days_to_maturity != null) {
      if (row.days_to_maturity < 1) return "Expires today";
      return `${Math.round(row.days_to_maturity)}d`;
    }
    return "Fixed";
  }
  if (row.effective_duration === "OVERNIGHT") return "Overnight";
  return "Variable";
}

function typeLabel(type: string): string {
  switch (type) {
    case "LENDING": return "Lending";
    case "VAULT": return "Vault";
    case "STAKING": return "Staking";
    case "FUNDING_RATE": return "Funding";
    case "BASIS_TRADE": return "Basis";
    case "PENDLE_PT": return "PT";
    case "PENDLE_YT": return "YT";
    case "CEX_EARN": return "CEX Earn";
    case "SAVINGS": return "Savings";
    default: return type;
  }
}

function rewardSum(row: MarketOpportunity): number {
  return row.total_apy_pct - row.base_apy_pct;
}

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------

function buildColumns(
  flashedIds: Set<string>
): DataColumn<MarketOpportunity>[] {
  void flashedIds; // used via getRowClassName — listed here for clarity
  return [
    {
      header: "Asset",
      accessorKey: "asset_symbol",
      width: "120px",
      sortable: true,
      cell: (_val, row) => (
        <AssetBadge
          symbol={row.asset_symbol}
          umbrella={row.umbrella_group}
          subType={row.asset_sub_type}
          size="sm"
        />
      ),
    },
    {
      header: "Type",
      accessorKey: "opportunity_type",
      width: "80px",
      cell: (_val, row) => (
        <span className="opp-type-badge">{typeLabel(row.opportunity_type)}</span>
      ),
    },
    {
      header: "Venue / Chain",
      accessorKey: "venue",
      width: "140px",
      cell: (_val, row) => (
        <div className="opp-venue-cell">
          <VenueLogo venue={row.venue} size="sm" />
          <span className="opp-chain-badge">{row.chain}</span>
        </div>
      ),
    },
    {
      header: "Side",
      accessorKey: "side",
      width: "60px",
      align: "center",
      cell: (_val, row) => (
        <span
          className="opp-side-pill"
          style={{
            color: row.side === "SUPPLY" ? "var(--green)" : "var(--red)",
            borderColor:
              row.side === "SUPPLY"
                ? "rgba(34,197,94,0.3)"
                : "rgba(239,68,68,0.3)",
          }}
        >
          {row.side === "SUPPLY" ? "SUP" : "BOR"}
        </span>
      ),
    },
    {
      header: "APY",
      accessorKey: "total_apy_pct",
      width: "80px",
      align: "right",
      mono: true,
      sortable: true,
      cell: (_val, row) => (
        <span
          className="opp-apy"
          style={{ color: apyColor(row.total_apy_pct) }}
        >
          {formatAPY(row.total_apy_pct)}
        </span>
      ),
    },
    {
      header: "Base",
      accessorKey: "base_apy_pct",
      width: "70px",
      align: "right",
      mono: true,
      sortable: true,
      cell: (_val, row) => (
        <span className="text-muted text-mono">{formatAPY(row.base_apy_pct)}</span>
      ),
    },
    {
      header: "Rewards",
      accessorFn: (row) => rewardSum(row),
      width: "70px",
      align: "right",
      mono: true,
      sortable: true,
      cell: (_val, row) => {
        const extra = rewardSum(row);
        if (extra <= 0) return <span className="dt-null">--</span>;
        return (
          <span style={{ color: "#4ade80" }} className="text-mono">
            +{formatAPY(extra)}
          </span>
        );
      },
    },
    {
      header: "TVL",
      accessorKey: "tvl_usd",
      width: "85px",
      align: "right",
      mono: true,
      sortable: true,
      cell: (_val, row) => (
        <span className="text-mono">{formatUSD(row.tvl_usd)}</span>
      ),
    },
    {
      header: "Capacity",
      accessorFn: (row) => row.capacity_cap ?? 0,
      width: "110px",
      sortable: true,
      cell: (_val, row) => {
        if (!row.is_capacity_capped || !row.capacity_cap) {
          return <span className="dt-null">--</span>;
        }
        const used = row.capacity_cap - (row.capacity_remaining ?? 0);
        return (
          <CapacityBar current={used} cap={row.capacity_cap} height={4} showLabel={false} />
        );
      },
    },
    {
      header: "Liquidity",
      accessorFn: (row) => {
        const liq = row.liquidity as LiquidityInfo;
        return liq?.has_lockup ? 1 : liq?.has_withdrawal_queue ? 2 : 0;
      },
      width: "90px",
      sortable: true,
      cell: (_val, row) => {
        const { label, color } = liquidityLabel(row.liquidity);
        return <span className="opp-liq-badge" style={{ color }}>{label}</span>;
      },
    },
    {
      header: "LTV",
      accessorKey: "as_collateral_max_ltv_pct",
      width: "58px",
      align: "right",
      mono: true,
      sortable: true,
      cell: (_val, row) => {
        if (!row.is_collateral_eligible || row.as_collateral_max_ltv_pct == null) {
          return <span className="dt-null">--</span>;
        }
        return (
          <span className="text-mono">
            {row.as_collateral_max_ltv_pct.toFixed(0)}%
          </span>
        );
      },
    },
    {
      header: "Duration",
      accessorFn: (row) => row.days_to_maturity ?? 0,
      width: "82px",
      sortable: false,
      cell: (_val, row) => (
        <span className="text-muted">{durationLabel(row)}</span>
      ),
    },
    {
      header: "Updated",
      accessorKey: "last_updated_at",
      width: "65px",
      sortable: true,
      cell: (_val, row) => (
        <span className="text-muted">{relativeTime(row.last_updated_at)}</span>
      ),
    },
  ];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function OpportunitiesClient({ initialData, initialSummary }: Props) {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Build filters from URL
  const filters: OpportunityFilters = useMemo(() => ({
    umbrella: searchParams.get("umbrella") ?? undefined,
    side: searchParams.get("side") ?? undefined,
    type: searchParams.get("type") ?? undefined,
    chain: searchParams.get("chain") ?? undefined,
    venue: searchParams.get("venue") ?? undefined,
    asset: searchParams.get("asset") ?? undefined,
    min_apy: searchParams.get("min_apy") ? Number(searchParams.get("min_apy")) : undefined,
    min_tvl: searchParams.get("min_tvl") ? Number(searchParams.get("min_tvl")) : undefined,
    exclude_amm_lp: searchParams.get("exclude_amm_lp") === "true" ? true : undefined,
    exclude_pendle: searchParams.get("exclude_pendle") === "true" ? true : undefined,
    sort_by: searchParams.get("sort_by") ?? undefined,
    limit: PAGE_SIZE,
    offset: searchParams.get("offset") ? Number(searchParams.get("offset")) : 0,
  }), [searchParams]);

  // SWR for live data
  const { data: liveData } = useOpportunities(filters);
  const { data: liveSummary } = useOpportunitySummary();

  const data = liveData ?? initialData;
  const summary = liveSummary ?? initialSummary;

  const opportunities = data.data;
  const pagination = data.pagination;

  // -------------------------------------------------------------------------
  // APY flash on change
  // -------------------------------------------------------------------------
  const prevApyMap = useRef<Map<string, number>>(new Map());
  const [flashedIds, setFlashedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!opportunities.length) return;
    const changed: string[] = [];
    for (const opp of opportunities) {
      const prev = prevApyMap.current.get(opp.opportunity_id);
      if (prev != null && Math.abs(opp.total_apy_pct - prev) > 0.005) {
        changed.push(opp.opportunity_id);
      }
    }
    // Update prev map
    for (const opp of opportunities) {
      prevApyMap.current.set(opp.opportunity_id, opp.total_apy_pct);
    }
    if (changed.length === 0) return;
    setFlashedIds(new Set(changed));
    const timer = setTimeout(() => setFlashedIds(new Set()), 700);
    return () => clearTimeout(timer);
  }, [opportunities]);

  // -------------------------------------------------------------------------
  // Pagination
  // -------------------------------------------------------------------------
  const offset = filters.offset ?? 0;

  const setOffset = useCallback((newOffset: number) => {
    const params = new URLSearchParams(searchParams.toString());
    if (newOffset === 0) params.delete("offset");
    else params.set("offset", String(newOffset));
    router.replace(`/opportunities?${params.toString()}`, { scroll: false });
  }, [searchParams, router]);

  // -------------------------------------------------------------------------
  // Venue list for FilterBar
  // -------------------------------------------------------------------------
  const venues = useMemo(
    () => summary ? Object.keys(summary.by_venue).sort() : [],
    [summary]
  );

  // -------------------------------------------------------------------------
  // Stat card values
  // -------------------------------------------------------------------------
  const topSupply = summary?.top_supply_apy?.[0];
  const topBorrow = summary?.top_borrow_apy?.[0];
  const totalTvl = useMemo(
    () => opportunities.reduce((acc, o) => acc + (o.tvl_usd ?? 0), 0),
    [opportunities]
  );

  // -------------------------------------------------------------------------
  // Row click → detail (page not yet built, navigates to /opportunities/[id])
  // -------------------------------------------------------------------------
  const handleRowClick = useCallback((row: MarketOpportunity) => {
    router.push(`/opportunities/${encodeURIComponent(row.opportunity_id)}`);
  }, [router]);

  // -------------------------------------------------------------------------
  // Columns
  // -------------------------------------------------------------------------
  const columns = useMemo(() => buildColumns(flashedIds), [flashedIds]);

  const getRowClassName = useCallback(
    (row: MarketOpportunity) =>
      flashedIds.has(row.opportunity_id) ? "opp-apy-flash" : "",
    [flashedIds]
  );

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  return (
    <div className="opp-page">
      {/* Page header */}
      <div className="opp-header">
        <h1 className="opp-title">Opportunities</h1>
        <span className="opp-count">
          {pagination.total.toLocaleString()} total
        </span>
      </div>

      {/* Stat cards */}
      <div className="opp-stats">
        <StatCard
          title="Total Opportunities"
          value={pagination.total.toLocaleString()}
          subtitle={`${Object.keys(summary?.by_venue ?? {}).length} venues`}
        />
        <StatCard
          title="Best Supply APY"
          value={topSupply ? formatAPY(topSupply.apy) : "--"}
          subtitle={topSupply ? `${topSupply.asset} · ${topSupply.venue}` : undefined}
        />
        <StatCard
          title="Best Borrow APY"
          value={topBorrow ? formatAPY(topBorrow.apy) : "--"}
          subtitle={topBorrow ? `${topBorrow.asset} · ${topBorrow.venue}` : undefined}
        />
        <StatCard
          title="TVL (visible)"
          value={formatUSD(totalTvl)}
          subtitle={`${opportunities.length} shown`}
        />
      </div>

      {/* Umbrella breakdown bar */}
      {summary && (
        <div className="opp-umbrella-bar">
          {Object.entries(summary.by_umbrella).map(([umb, count]) => (
            <button
              key={umb}
              className={`opp-umb-btn ${searchParams.get("umbrella") === umb ? "opp-umb-active" : ""}`}
              style={{
                borderColor:
                  searchParams.get("umbrella") === umb
                    ? getUmbrellaColor(umb)
                    : undefined,
                color:
                  searchParams.get("umbrella") === umb
                    ? getUmbrellaColor(umb)
                    : undefined,
              }}
              onClick={() => {
                const params = new URLSearchParams(searchParams.toString());
                if (params.get("umbrella") === umb) params.delete("umbrella");
                else params.set("umbrella", umb);
                params.delete("offset");
                router.replace(`/opportunities?${params.toString()}`, { scroll: false });
              }}
            >
              <span
                className="opp-umb-dot"
                style={{ backgroundColor: getUmbrellaColor(umb) }}
              />
              {umb}
              <span className="opp-umb-count">{count}</span>
            </button>
          ))}
        </div>
      )}

      {/* Filter bar */}
      <FilterBar venues={venues} showChain={true} showType={true} showVenue={true} />

      {/* Table */}
      <DataTable<MarketOpportunity>
        columns={columns}
        data={opportunities}
        onRowClick={handleRowClick}
        defaultSortKey="total_apy_pct"
        defaultSortDesc={true}
        getRowClassName={getRowClassName}
        emptyMessage="No opportunities match the current filters."
      />

      {/* Pagination */}
      <div className="pg-root">
        <span className="pg-info">
          {offset + 1}–{Math.min(offset + opportunities.length, pagination.total)} of{" "}
          {pagination.total.toLocaleString()}
        </span>
        <div className="pg-buttons">
          <button
            className="pg-btn"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            ← Prev
          </button>
          <button
            className="pg-btn"
            disabled={!pagination.has_more}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next →
          </button>
        </div>
      </div>
    </div>
  );
}
