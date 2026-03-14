You are building a yield dashboard as a new Streamlit application located at:
  /home/ec2-user/cdb_workspace/yield-os/
This project READS FROM but NEVER MODIFIES the reference codebase at:
  /home/ec2-user/workspace/
⚠️  ABSOLUTE RULE: You must NEVER edit, modify, create, delete, move, or in any 
way alter ANY file or directory under /home/ec2-user/workspace/. That is a shared 
production codebase. Treat it as read-only source library. All new files go 
exclusively inside /home/ec2-user/cdb_workspace/yield-os/.
---
## 1. REFERENCE CODEBASE — READ ONLY, NEVER MODIFY
Before writing any code, read these files from the reference repo (read-only):
1. `/home/ec2-user/workspace/exodus/analytics_frontend/streamlit/migration/lib/unified_apis.py`
2. `/home/ec2-user/workspace/exodus/analytics_frontend/streamlit/migration/lib/binance_funcs.py`
3. `/home/ec2-user/workspace/exodus/analytics_frontend/streamlit/migration/lib/okx_funcs.py`
4. `/home/ec2-user/workspace/exodus/api_wrappers/mongo_funcs.py`
5. `/home/ec2-user/workspace/traders/src/wrappers/perps.py`
6. `/home/ec2-user/workspace/exodus/analytics_frontend/streamlit/pages/Funding_Rates_New.py`
7. `/home/ec2-user/workspace/exodus/analytics_frontend/streamlit/pages/Term_Structure_Metrics.py`
   (read function signatures and stats/percentile logic only)
8. `/home/ec2-user/workspace/exodus/modules/key_function.py`
9. `/home/ec2-user/workspace/exodus/analytics_frontend/streamlit/migration/.streamlit/secrets.toml`
   (for the shape/structure of secrets only — DO NOT log, print, or expose any values)
---
## 2. PATH INJECTION PATTERN
Your new code lives in `/home/ec2-user/cdb_workspace/yield-os/`. To access the 
reference libraries without copying them, inject the reference paths at runtime 
using `sys.path`. Use this boilerplate at the top of every new file:
```python
import sys
from pathlib import Path
# ── Reference library paths (READ ONLY — never write here) ──────────────────
_EXODUS_ROOT     = Path("/home/ec2-user/workspace/exodus")
_MIGRATION_LIB   = _EXODUS_ROOT / "analytics_frontend/streamlit/migration/lib"
_TRADERS_WRAPPERS = Path("/home/ec2-user/workspace/traders/src/wrappers")
_GRIFF_COMMON    = Path("/home/ec2-user/workspace/griff/common")
for _p in [_EXODUS_ROOT, _MIGRATION_LIB, _TRADERS_WRAPPERS, _GRIFF_COMMON]:
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
# ── All new files must be written inside /home/ec2-user/cdb_workspace/yield-os/
Then import with a graceful fallback so the app loads even if a path is unavailable:

try:
    from unified_apis import (
        get_perp_funding_rate_history,
        get_perp_mark_price_ohlc,
        get_futures_ohlcv,
        _calc_RV_from_df,
        get_rv,
    )
    from binance_funcs import (
        get_binance_predicted_funding_rate,
        get_binance_market_metrics,
        get_binance_funding_rate_history,
    )
    from okx_funcs import (
        get_okx_funding_rate,
        get_okx_funding_rate_history,
        get_okx_market_metrics,
    )
    from api_wrappers.mongo_funcs import (
        get_annualized_funding_rate_history,
        get_xccy_funding_rate_history,
    )
    from perps import PerpFuture
    _HAS_APIS = True
except Exception as _import_err:
    _HAS_APIS = False
    # assign None to each symbol so the rest of the file loads without crashing
    get_perp_funding_rate_history = get_perp_mark_price_ohlc = get_futures_ohlcv = None
    _calc_RV_from_df = get_rv = None
    get_binance_predicted_funding_rate = get_binance_market_metrics = None
    get_binance_funding_rate_history = None
    get_okx_funding_rate = get_okx_funding_rate_history = get_okx_market_metrics = None
    get_annualized_funding_rate_history = get_xccy_funding_rate_history = None
    PerpFuture = None
3. SECRETS / API KEY SETUP
Option A — Streamlit pages (preferred for your new app)
Create your own secrets file at: /home/ec2-user/cdb_workspace/yield-os/.streamlit/secrets.toml

Model the structure exactly on the reference file you read at: /home/ec2-user/workspace/exodus/analytics_frontend/streamlit/migration/.streamlit/secrets.toml

The structure is:

[api_keys]
amberdata_derivs = "<key>"
[exchanges.binance]
api_key    = "<key>"
api_secret = "<secret>"
[exchanges.okx]
api_key    = "<key>"
api_secret = "<secret>"
passphrase = "<passphrase>"
[exchanges.bybit]
api_key    = "<key>"
api_secret = "<secret>"
Access in code via st.secrets["api_keys"]["amberdata_derivs"] etc. The reference migration/lib/unified_apis.py already uses this exact pattern for its _get_amberdata_key() helper — your code can call that function directly once the path is injected.

Option B — Server-side / non-Streamlit scripts
Use AWS Secrets Manager (region ap-northeast-1). The helper already exists in the reference repo — just import it:

# Path already injected above via _EXODUS_ROOT
from modules.key_function import get_secret
all_keys      = get_secret()                         # full exodus_keys dict
amberdata_key = get_secret("amberdata")["api_key"]   # Amberdata
mongo_uri     = get_secret("mongo_cluster")["uri"]   # MongoDB
db_creds      = get_secret(None, 'prod/db/ro-user/credentials/falcon')
# db_creds keys: host, user, password — database name = "falcon"
4. DATA SOURCES — USE THESE, DO NOT REINVENT
A. Funding Rate History (perps)
PRIMARY — MongoDB-backed, daily annualized rates, Binance + OKX:

df = get_annualized_funding_rate_history(
    base_ccy='BTC',
    quote_ccy='USDT',     # only USDT supported
    day_count=365,
    exchange='',          # '' = all exchanges; 'binance' or 'okx' for single
    output_funding_rates_only=True
)
# Returns: DataFrame indexed by datetime, columns = exchange names
# Also available: get_xccy_funding_rate_history('ETH', 'BTC', day_count=365)
SECONDARY — Direct REST, tick-level, supports Binance / OKX / Bybit / Deribit:

perp = PerpFuture(base_ccy='BTC', quote_ccy='USDT')
df = perp.get_funding_rate_history(
    exchange='binance',   # 'binance', 'okx', 'bybit', 'deribit'
    start_date=...,
    end_date=...
)
# Columns: timestamp, funding_rate, mark_price, annualized_funding_rate,
#          base_ccy, quote_ccy, pair, exchange_symbol, exchange
TERTIARY — Lightweight REST wrappers (no extra dependencies):

# Binance FAPI, auto-paginates up to 3 years:
df = get_perp_funding_rate_history('BTC', 'USDT', day_count=365)
# Columns: symbol, timestamp, funding_rate, annualized_funding_rate, exchange
# OKX REST (3-month limit):
df = get_okx_funding_rate_history('BTC', 'USDT')
# Columns: timestamp, funding_rate, realized_rate, method, annualized_funding_rate, symbol
B. Current / Predicted Funding Rates
rate = get_binance_predicted_funding_rate('BTC', 'USDT', annualized=True)
rate = get_okx_funding_rate('BTC', 'USDT', annualized=True, details=False)
# details=True returns dict with funding_rate_ann, funding_rate, premium_index
C. Mark Price / OHLC
# Perp mark price — tries Binance FAPI, falls back to Bybit:
df = get_perp_mark_price_ohlc('BTC', 'USDT', days_lookback=365)
# Columns: timestamp (UTC-aware), open, high, low, close
# Futures OHLCV via Amberdata (requires amberdata_derivs key in secrets):
df = get_futures_ohlcv('BTC', 'USDT', days_lookback=90)
# Columns: exchangeTimestamp, open, high, low, close, volume
# Max lookback: 731 days. Tries Binance first, then Bybit.
D. Statistical / Modeling Tools
# Add realized vol columns to any OHLC DataFrame:
df = _calc_RV_from_df(df.copy(), day_counts=[7, 30, 90], c2c_only=False)
# Adds: log_returns, c2c_vol_N (annualized), parkinson_vol_N (annualized)
# NOTE: pass .copy() — function modifies df in-place AND returns it
# Full fetch + RV pipeline in one call:
df = get_rv('BTC', 'USDT', day_counts=[7, 30, 90])
# Rolling MAs (funding rate smoothing):
df['MA_30'] = df['annualized_funding_rate'].rolling(30).mean()
# Percentile scoring (copy pattern from Term_Structure_Metrics.py):
from scipy.stats import percentileofscore
pct = percentileofscore(series.dropna(), current_value, kind='rank')
# KDE distribution (copy pattern from Funding_Rates_New.py):
from scipy.stats import gaussian_kde
kde = gaussian_kde(series.dropna())
E. Market Metrics (OI, Volume)
metrics = get_binance_market_metrics('BTC', 'USDT')
# Keys: perpetual_open_interest, perpetual_open_interest_USD,
#       perpetual_volume_24h, perpetual_volume_24h_USD,
#       spot_volume_24h, spot_volume_24h_USD, success
metrics = get_okx_market_metrics('BTC', 'USDT')
# Same key shape
5. CACHING PATTERN
import streamlit as st
@st.cache_data(show_spinner=False, ttl=300)
def fetch_funding_history(base_ccy: str, exchange: str, day_count: int):
    if not _HAS_APIS or get_annualized_funding_rate_history is None:
        return pd.DataFrame()
    try:
        return get_annualized_funding_rate_history(base_ccy, day_count=day_count, exchange=exchange)
    except Exception:
        return pd.DataFrame()
For expensive Amberdata calls, cache to disk in your own project directory:

CACHE_DIR = Path("/home/ec2-user/cdb_workspace/yield-os/.cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
# Write: pickle.dump(df, open(CACHE_DIR / "key.pkl", "wb"))
# Read:  pickle.load(open(CACHE_DIR / "key.pkl", "rb"))
6. KEY CONSTRAINTS / GOTCHAS
MongoDB cm_source_funding only has Binance + OKX, USDT pairs only, and only for actively tracked tokens. Always wrap in try/except for MongoCollectionNotFoundError.

get_binance_funding_rate_history auto-paginates back 3 years via a while loop. For get_binance_mark_price_history, limit is the number of DAYS (not records).

OKX get_okx_funding_rate_history is capped at ~3 months by the OKX API. For longer OKX history, use the MongoDB source.

Amberdata key must be accessible before calling get_futures_ohlcv(). Always guard with: if not api_key: st.error("..."); st.stop()

All Binance timestamps are milliseconds epoch. Always convert with pd.to_datetime(..., unit='ms').dt.tz_localize('UTC'). Ensure all timestamp columns are UTC-aware before merging across exchanges.

_calc_RV_from_df modifies the DataFrame in-place — always pass .copy().

PerpFuture.__init__ does NOT fetch live data by default (the fetch calls are commented out). It is safe to instantiate cheaply and then call methods on demand.

The reference Streamlit server runs at port 8020 with baseUrlPath "analytics2". Your new app in cdb_workspace/yield-os should use a DIFFERENT port to avoid conflicts. Configure this in your own .streamlit/config.toml.

7. OUTPUT LOCATION REMINDER
Every file you create or modify must be under: /home/ec2-user/cdb_workspace/yield-os/

If you ever find yourself about to write to /home/ec2-user/workspace/ — STOP. That is a violation of the project rules. Create an equivalent file in cdb_workspace/yield-os/ instead and import the reference code via sys.path injection.