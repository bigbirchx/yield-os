interface BorrowDemandCardProps {
  symbol: string;
  fundingRate?: number | null;
  basisAnnualized?: number | null;
  topBorrowApy?: number | null;
  topBorrowProtocol?: string | null;
  utilization?: number | null;
}

export function BorrowDemandCard({
  symbol,
  fundingRate,
  basisAnnualized,
  topBorrowApy,
  topBorrowProtocol,
  utilization,
}: BorrowDemandCardProps) {
  const signals: { label: string; value: string; elevated: boolean }[] = [];

  if (fundingRate != null) {
    const annualized = fundingRate * 3 * 365 * 100;
    signals.push({
      label: "Perp funding (ann.)",
      value: `${annualized.toFixed(2)}%`,
      elevated: annualized > 20,
    });
  }
  if (basisAnnualized != null) {
    signals.push({
      label: "Futures basis (ann.)",
      value: `${(basisAnnualized * 100).toFixed(2)}%`,
      elevated: basisAnnualized > 0.15,
    });
  }
  if (topBorrowApy != null && topBorrowProtocol) {
    signals.push({
      label: `Borrow rate (${topBorrowProtocol})`,
      value: `${topBorrowApy.toFixed(2)}%`,
      elevated: topBorrowApy > 10,
    });
  }
  if (utilization != null) {
    signals.push({
      label: "Peak market utilization",
      value: `${(utilization * 100).toFixed(1)}%`,
      elevated: utilization > 0.85,
    });
  }

  const anyElevated = signals.some((s) => s.elevated);

  return (
    <div className="borrow-demand-card">
      <div className="borrow-demand-header">
        <span className={`borrow-demand-status ${anyElevated ? "elevated" : "normal"}`}>
          {anyElevated ? "⬆ Elevated borrow demand" : "— Normal borrow demand"}
        </span>
        <span className="borrow-demand-label">PLACEHOLDER · Explainer engine in Prompt 7</span>
      </div>

      {signals.length > 0 ? (
        <div className="borrow-demand-signals">
          {signals.map((s) => (
            <div key={s.label} className="borrow-signal-row">
              <span className="borrow-signal-label">{s.label}</span>
              <span className={`borrow-signal-value ${s.elevated ? "elevated" : ""}`}>
                {s.value}
              </span>
              {s.elevated && (
                <span className="borrow-signal-tag">elevated</span>
              )}
            </div>
          ))}
        </div>
      ) : null}

      <p className="borrow-demand-note">
        Borrow demand for <strong>{symbol}</strong> is estimated from funding
        rates, futures basis, on-chain utilization, and lending market rates.
        A structured explanation engine (confidence scores, factor weights, and
        narrative output) will be connected in the next build step.
      </p>
    </div>
  );
}
