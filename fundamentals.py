"""
Fundamental screening for the J Law / Minervini methodology.

The lecture's fundamental layer is explicit: look at REVENUE (not profit) so that
pre-profit high-growth leaders aren't excluded, require the most-recent quarter's
revenue growth to be meaningful (>5%), and require SALES ACCELERATION.

Data source: Yahoo's fundamentals-timeseries endpoint
(https://query2.finance.yahoo.com/ws/fundamentals-timeseries/...). This is the
CRUMB-FREE endpoint — unlike yfinance's `.info` (used for sector), it does not need
a cookie/crumb, so it keeps working from cloud IPs such as GitHub Actions runners.
It returns ~5 recent quarters and ~4 annual periods of Total Revenue.

Because quarterly history is capped at ~5 quarters, a true 2-quarter *year-over-year*
acceleration (which needs ~7 quarters) is not computable from free data. We therefore:
  * compute the latest-quarter YoY revenue growth (Q0 vs the same quarter one year
    earlier — seasonally clean), and
  * approximate SALES ACCELERATION from the 4 annual data points (latest full-year
    revenue growth faster than the prior year's) — a canonical CANSLIM-style annual
    acceleration check. This is clearly labelled as annual-based on the dashboard.
"""
import time
import requests

from config import MIN_REVENUE_GROWTH, REQUIRE_SALES_ACCELERATION

_FUND_URL = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_SESSION = requests.Session()

# In-memory cache so a ticker is fetched at most once per pipeline run.
_CACHE = {}


def _fetch_timeseries(ticker, years=6):
    """Return the parsed JSON for quarterly+annual Total Revenue, or None on failure."""
    now = int(time.time())
    params = {
        "symbol": ticker,
        "type": "quarterlyTotalRevenue,annualTotalRevenue",
        "period1": now - years * 365 * 86400,
        "period2": now,
        "merge": "false",
        "padTimeSeries": "true",
        "lang": "en-US",
        "region": "US",
    }
    url = _FUND_URL.format(sym=ticker)
    for attempt in range(3):
        try:
            r = _SESSION.get(url, params=params, headers=_HEADERS, timeout=15)
            if r.status_code == 200:
                return r.json()
            # 401/429 or transient error -> back off and retry.
        except Exception:
            pass
        time.sleep(1.0 * (attempt + 1))
    return None


def _extract(js, key):
    """Return [(asOfDate, value), ...] sorted oldest->newest for a timeseries key."""
    try:
        results = js["timeseries"]["result"]
    except (KeyError, TypeError):
        return []
    for res in results:
        arr = res.get(key)
        if not arr:
            continue
        out = []
        for item in arr:
            if not item:
                continue
            d = item.get("asOfDate")
            v = item.get("reportedValue")
            raw = v.get("raw") if isinstance(v, dict) else None
            if d is not None and raw is not None:
                out.append((d, float(raw)))
        out.sort(key=lambda x: x[0])
        return out
    return []


def get_fundamentals(ticker):
    """
    Return a dict describing the ticker's revenue fundamentals:

      {
        'last_q_rev_growth_yoy': float|None,   # e.g. 0.123 == +12.3% YoY
        'annual_rev_growth': float|None,       # latest full-year YoY growth
        'sales_accelerating': bool|None,       # annual growth accelerating
        'passes': bool|None,                   # None => could not verify
        'verified': bool,                      # did we get usable revenue data?
        'detail': str,
      }

    `passes` is True only on positive evidence (last-quarter YoY > MIN_REVENUE_GROWTH,
    and — when REQUIRE_SALES_ACCELERATION — not decelerating). It is None when revenue
    data is unavailable, so callers can distinguish "failed the screen" from
    "couldn't check" and avoid silently dropping everything on a data outage.
    """
    if ticker in _CACHE:
        return _CACHE[ticker]

    result = {
        "last_q_rev_growth_yoy": None,
        "annual_rev_growth": None,
        "sales_accelerating": None,
        "passes": None,
        "verified": False,
        "detail": "no data",
    }

    js = _fetch_timeseries(ticker)
    if js is None:
        _CACHE[ticker] = result
        return result

    quarters = _extract(js, "quarterlyTotalRevenue")
    annuals = _extract(js, "annualTotalRevenue")

    # Latest-quarter YoY growth: newest vs the same quarter ~4 periods earlier.
    last_q_growth = None
    if len(quarters) >= 5 and quarters[-5][1] > 0:
        last_q_growth = quarters[-1][1] / quarters[-5][1] - 1.0

    # Annual acceleration: latest full-year growth vs the prior year's growth.
    annual_growth = None
    accelerating = None
    if len(annuals) >= 3 and annuals[-2][1] > 0 and annuals[-3][1] > 0:
        g0 = annuals[-1][1] / annuals[-2][1] - 1.0   # most recent year
        g1 = annuals[-2][1] / annuals[-3][1] - 1.0   # prior year
        annual_growth = g0
        accelerating = g0 > g1

    result["last_q_rev_growth_yoy"] = last_q_growth
    result["annual_rev_growth"] = annual_growth
    result["sales_accelerating"] = accelerating

    if last_q_growth is None:
        # No usable revenue -> unverified (caller decides how to treat).
        result["verified"] = False
        result["detail"] = "revenue data unavailable"
        _CACHE[ticker] = result
        return result

    result["verified"] = True
    growth_ok = last_q_growth > MIN_REVENUE_GROWTH
    # Fail on acceleration only when we have positive evidence of deceleration.
    accel_ok = True
    if REQUIRE_SALES_ACCELERATION and accelerating is False:
        accel_ok = False

    result["passes"] = bool(growth_ok and accel_ok)
    result["detail"] = (
        f"last-Q rev {last_q_growth * 100:+.1f}% YoY"
        + (f", annual {annual_growth * 100:+.1f}%" if annual_growth is not None else "")
        + (f", accel={'yes' if accelerating else ('no' if accelerating is False else 'n/a')}")
    )
    _CACHE[ticker] = result
    return result


def apply_fundamental_filter(signals, require_when_unverified=False):
    """
    Attach fundamentals to each signal and drop the ones that fail on VERIFIED data.

    Policy (real-money-safe, outage-tolerant):
      * verified + passes  -> keep, tagged fundamentals_ok=True
      * verified + fails    -> DROP (not a J Law/Minervini fundamental fit)
      * unverified          -> keep, tagged fundamentals_ok=None (flagged, not hidden),
                               unless require_when_unverified is True
      * if the WHOLE batch is unverified (systemic data outage), keep all + warn,
        so a bad data day never silently empties the buy list.

    Returns the filtered list of signals (mutated in place with fundamentals fields).
    """
    if not signals:
        return signals

    verified_count = 0
    kept = []
    for sig in signals:
        f = get_fundamentals(sig["ticker"])
        sig["rev_growth_yoy"] = f["last_q_rev_growth_yoy"]
        sig["annual_rev_growth"] = f["annual_rev_growth"]
        sig["sales_accelerating"] = f["sales_accelerating"]
        sig["fundamentals_detail"] = f["detail"]
        sig["fundamentals_verified"] = f["verified"]
        sig["fundamentals_ok"] = f["passes"]
        if f["verified"]:
            verified_count += 1
        time.sleep(0.15)  # be polite to the endpoint

    outage = verified_count == 0
    if outage:
        print("[fundamentals] WARNING: no revenue data verified for any signal this run "
              "(likely a data outage) -- keeping all signals UNFILTERED and flagged.")
        return signals

    for sig in signals:
        passes = sig["fundamentals_ok"]
        verified = sig["fundamentals_verified"]
        if verified and passes is False:
            print(f"[fundamentals] DROP {sig['ticker']}: {sig['fundamentals_detail']}")
            continue
        if not verified and require_when_unverified:
            print(f"[fundamentals] DROP {sig['ticker']}: fundamentals unverifiable (strict mode)")
            continue
        kept.append(sig)

    print(f"[fundamentals] {verified_count}/{len(signals)} verified, "
          f"{len(kept)} kept after fundamental filter.")
    return kept
