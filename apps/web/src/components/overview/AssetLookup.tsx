"use client";

/**
 * AssetLookup
 *
 * Search input on the Overview page. Validates the symbol against a known
 * list (or accepts any uppercase input) and opens the asset page in a new
 * tab when submitted.
 */

import { useState } from "react";

const KNOWN_ASSETS = [
  "BTC", "ETH", "SOL", "USDC", "USDT", "WBTC", "CBBTC", "DAI",
  "WETH", "stETH", "wstETH", "BTCB",
];

function normalize(s: string): string {
  return s.trim().toUpperCase();
}

export function AssetLookup() {
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  const suggestions = query.length >= 1
    ? KNOWN_ASSETS.filter((a) =>
        a.startsWith(normalize(query)) && a !== normalize(query)
      )
    : [];

  function open(symbol: string) {
    const sym = normalize(symbol);
    if (!sym) { setError("Enter a symbol"); return; }
    setError(null);
    window.open(`/assets/${sym}`, "_blank", "noopener,noreferrer");
    setQuery("");
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    open(query);
  }

  return (
    <div className="al-wrapper">
      <form className="al-form" onSubmit={handleSubmit} autoComplete="off">
        <label className="al-label" htmlFor="al-input">Asset Lookup</label>
        <div className="al-row">
          <div className="al-input-wrap">
            <input
              id="al-input"
              className="al-input"
              type="text"
              placeholder="BTC, ETH, SOL…"
              value={query}
              spellCheck={false}
              onChange={(e) => {
                setQuery(e.target.value);
                setError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") setQuery("");
              }}
            />
            {suggestions.length > 0 && (
              <ul className="al-suggestions">
                {suggestions.slice(0, 6).map((s) => (
                  <li key={s}>
                    <button
                      type="button"
                      className="al-suggestion-item"
                      onClick={() => open(s)}
                    >
                      {s}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button type="submit" className="al-btn">
            Open ↗
          </button>
        </div>
        {error && <span className="al-error">{error}</span>}
      </form>
    </div>
  );
}
