"use client";

/**
 * FundingRatesDrawer — collapsible perpetual funding section for asset pages.
 * Data is only fetched once the user expands the drawer.
 */

import dynamic from "next/dynamic";
import { useState } from "react";

const FundingDashboard = dynamic(
  () =>
    import("@/components/funding/FundingDashboard").then(
      (m) => m.FundingDashboard
    ),
  {
    ssr: false,
    loading: () => <div className="frd-loading">Loading funding data…</div>,
  }
);

interface FundingRatesDrawerProps {
  symbol: string;
}

export function FundingRatesDrawer({ symbol }: FundingRatesDrawerProps) {
  const [open, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);

  function toggle() {
    if (!open && !everOpened) setEverOpened(true);
    setOpen((v) => !v);
  }

  return (
    <div className="frd-wrapper">
      <button className="frd-header" onClick={toggle} aria-expanded={open}>
        <span className="frd-title">
          <span className="frd-icon">⚡</span>
          Perpetual Funding Rates
          <span className="frd-sub">· {symbol}</span>
        </span>
        <span className="frd-chevron">{open ? "▲" : "▼"}</span>
      </button>

      <div className={`frd-body${open ? " frd-body--open" : ""}`}>
        {everOpened && (
          <FundingDashboard initialSymbol={symbol} showSymbolPicker={false} />
        )}
      </div>
    </div>
  );
}
