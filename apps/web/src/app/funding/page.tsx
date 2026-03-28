"use client";

import { FundingDashboard } from "@/components/funding/FundingDashboard";

export default function FundingPage() {
  return (
    <div className="fn-page">
      <div className="page-title">Perpetual Funding Rates</div>
      <FundingDashboard initialSymbol="BTC" showSymbolPicker />
    </div>
  );
}
