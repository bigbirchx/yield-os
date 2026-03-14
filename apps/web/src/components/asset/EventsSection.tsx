export function EventsSection({ symbol }: { symbol: string }) {
  return (
    <div className="placeholder-section">
      <p className="placeholder-label">EVENTS · Coming in Prompt 8</p>
      <p className="placeholder-desc">
        Governance decisions, protocol upgrades, token unlocks, and internal
        notes for {symbol} will appear here as manual overlays.
      </p>
    </div>
  );
}
