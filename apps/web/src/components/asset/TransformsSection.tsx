export function TransformsSection({ symbol }: { symbol: string }) {
  return (
    <div className="placeholder-section">
      <p className="placeholder-label">TRANSFORMS · Coming in Prompt 8</p>
      <p className="placeholder-desc">
        This section will show mint / redeem / wrap / unwrap / stake / bridge
        paths for {symbol} — fees in bps, estimated latency, and fungibility class.
      </p>
    </div>
  );
}
