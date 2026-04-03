import { fetchOpportunities, fetchOpportunitySummary } from "@/lib/api";
import { Suspense } from "react";
import OpportunitiesClient from "./OpportunitiesClient";

// ---------------------------------------------------------------------------
// Server component — fetch initial data, then hand off to client
// ---------------------------------------------------------------------------

export const metadata = {
  title: "Opportunities | Yield Cockpit",
};

export default async function OpportunitiesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string>>;
}) {
  const params = await searchParams;

  const filters = {
    umbrella: params.umbrella,
    side: params.side,
    type: params.type,
    chain: params.chain,
    venue: params.venue,
    asset: params.asset,
    min_apy: params.min_apy ? Number(params.min_apy) : undefined,
    min_tvl: params.min_tvl ? Number(params.min_tvl) : undefined,
    exclude_amm_lp: params.exclude_amm_lp === "true" ? true : undefined,
    exclude_pendle: params.exclude_pendle === "true" ? true : undefined,
    sort_by: params.sort_by,
    limit: 100,
    offset: params.offset ? Number(params.offset) : 0,
  };

  const [initialData, initialSummary] = await Promise.all([
    fetchOpportunities(filters),
    fetchOpportunitySummary(),
  ]);

  return (
    <Suspense>
      <OpportunitiesClient
        initialData={initialData}
        initialSummary={initialSummary}
      />
    </Suspense>
  );
}
