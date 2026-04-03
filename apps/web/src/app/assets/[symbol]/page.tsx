import { notFound } from "next/navigation";
import { fetchOpportunities, fetchReferenceAsset } from "@/lib/api";
import { Suspense } from "react";
import UmbrellaCockpitClient from "./UmbrellaCockpitClient";

export const revalidate = 60;

const VALID_UMBRELLAS = ["USD", "ETH", "BTC", "SOL", "HYPE", "OTHER"];

// Price reference symbol per umbrella — used for the header price display
const PRICE_REF: Record<string, string> = {
  ETH: "ETH",
  BTC: "BTC",
  SOL: "SOL",
  HYPE: "HYPE",
  USD: "USDC",
  OTHER: "",
};

interface PageProps {
  params: Promise<{ symbol: string }>;
}

export async function generateMetadata({ params }: PageProps) {
  const { symbol } = await params;
  const umbrella = symbol.toUpperCase();
  return { title: `${umbrella} Cockpit | Yield Cockpit` };
}

export default async function UmbrellaCockpitPage({ params }: PageProps) {
  const { symbol } = await params;
  const umbrella = symbol.toUpperCase();

  if (!VALID_UMBRELLAS.includes(umbrella)) {
    notFound();
  }

  const priceRef = PRICE_REF[umbrella];

  const [supplyData, borrowData, refAsset] = await Promise.all([
    fetchOpportunities({
      umbrella,
      side: "SUPPLY",
      limit: 500,
      exclude_amm_lp: true,
      sort_by: "total_apy_pct",
    }),
    fetchOpportunities({
      umbrella,
      side: "BORROW",
      limit: 500,
      exclude_amm_lp: true,
      sort_by: "total_apy_pct",
    }),
    priceRef ? fetchReferenceAsset(priceRef) : Promise.resolve(null),
  ]);

  const price = refAsset?.market?.current_price_usd ?? null;

  return (
    <Suspense>
      <UmbrellaCockpitClient
        umbrella={umbrella}
        supplyOpps={supplyData.data}
        borrowOpps={borrowData.data}
        price={price}
      />
    </Suspense>
  );
}
