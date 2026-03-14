"""
sys.path injection for the internal production codebase.

READ ONLY — never write to /home/ec2-user/workspace/ under any circumstance.
All new files must be written inside the yield-os project.
"""
import sys
from pathlib import Path

# ── Reference library paths (READ ONLY — never write here) ──────────────────
_EXODUS_ROOT      = Path("/home/ec2-user/workspace/exodus")
_MIGRATION_LIB    = _EXODUS_ROOT / "analytics_frontend/streamlit/migration/lib"
_STREAMLIT_DIR    = _EXODUS_ROOT / "analytics_frontend/streamlit"  # bullish.py lives here
_TRADERS_WRAPPERS = Path("/home/ec2-user/workspace/traders/src/wrappers")
_GRIFF_COMMON     = Path("/home/ec2-user/workspace/griff/common")

for _p in [_EXODUS_ROOT, _MIGRATION_LIB, _STREAMLIT_DIR, _TRADERS_WRAPPERS, _GRIFF_COMMON]:
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
# ── All new files must be written inside the yield-os project ────────────────

try:
    from unified_apis import (  # type: ignore[import]
        get_perp_funding_rate_history,
        get_perp_mark_price_ohlc,
        get_futures_ohlcv,
        _calc_RV_from_df,
        get_rv,
    )
    from binance_funcs import (  # type: ignore[import]
        get_binance_predicted_funding_rate,
        get_binance_market_metrics,
        get_binance_funding_rate_history,
    )
    from okx_funcs import (  # type: ignore[import]
        get_okx_funding_rate,
        get_okx_funding_rate_history,
        get_okx_market_metrics,
    )
    from api_wrappers.mongo_funcs import (  # type: ignore[import]
        get_annualized_funding_rate_history,
        get_xccy_funding_rate_history,
    )
    from perps import PerpFuture  # type: ignore[import]
    from bullish import Bullish  # type: ignore[import]

    _HAS_APIS = True
except Exception as _import_err:
    _HAS_APIS = False
    # Assign None to each symbol so the rest of the codebase loads cleanly
    get_perp_funding_rate_history = None
    get_perp_mark_price_ohlc = None
    get_futures_ohlcv = None
    _calc_RV_from_df = None
    get_rv = None
    get_binance_predicted_funding_rate = None
    get_binance_market_metrics = None
    get_binance_funding_rate_history = None
    get_okx_funding_rate = None
    get_okx_funding_rate_history = None
    get_okx_market_metrics = None
    get_annualized_funding_rate_history = None
    get_xccy_funding_rate_history = None
    PerpFuture = None
    Bullish = None

__all__ = [
    "_HAS_APIS",
    "get_perp_funding_rate_history",
    "get_perp_mark_price_ohlc",
    "get_futures_ohlcv",
    "_calc_RV_from_df",
    "get_rv",
    "get_binance_predicted_funding_rate",
    "get_binance_market_metrics",
    "get_binance_funding_rate_history",
    "get_okx_funding_rate",
    "get_okx_funding_rate_history",
    "get_okx_market_metrics",
    "get_annualized_funding_rate_history",
    "get_xccy_funding_rate_history",
    "PerpFuture",
    "Bullish",
]
