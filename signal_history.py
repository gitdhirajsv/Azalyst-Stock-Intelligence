"""Append-only signal history (S3).

azalyst.py rewrites signals.json with mode 'w' on every cycle, and the cron in
.github/workflows/run_azalyst.yml fires up to 8 times a day -- so the only
record of what the engine ever saw is the CURRENT run. A name screened
yesterday and not traded is unrecoverable, which makes screened-but-not-traded
hit-rate analysis impossible.

signals.json itself is deliberately NOT changed. generate_dashboard.py reads it
as a top-level JSON array of entry signals (`raw_signals = json.load(sf)`, then
`for sig in raw_signals`) and renders those as the dashboard's ACTIVE BUY
SIGNALS panel; that contract is the published product. History lives alongside
it as an append-only JSONL -- one line per (date, ticker, kind, timing)
observation. JSONL rather than one file per run date because appending is a
clean, conflict-free git diff for the workflow's commit step, while the
per-day de-duplication below keeps 8 identical intraday scans from padding the
file AND still records a name that genuinely CHANGES timing state during a day
(WAIT FOR BREAKOUT -> BUY ON BREAKOUT -> BUY NOW is the signal, not noise).
"""
import json
import os
from datetime import datetime, timezone

from minervini import TIMING_BUY_NOW

HISTORY_DIR = "history"
HISTORY_PATH = os.path.join(HISTORY_DIR, "signals.jsonl")


def _record(sig, kind, run_at, date):
    """Flatten one entry or watch signal into a history row.

    ENTRY rows carry the executable price/stop that was actually available;
    WATCH rows carry only the trigger and a reference stop, never an executable
    price -- exactly as the two lists themselves differ.
    """
    return {
        "date": date,
        "run_at": run_at,
        "kind": kind,
        "ticker": sig.get("ticker"),
        "timing": sig.get("timing") or (TIMING_BUY_NOW if kind == "ENTRY" else None),
        "trigger_price": sig.get("trigger_price"),
        "price": sig.get("price"),
        "stop_loss": sig.get("stop_loss"),
        "stop_reference": sig.get("stop_reference"),
        "pivot": sig.get("pivot"),
        "target": sig.get("target"),
        "risk_pct": sig.get("risk_pct") if kind == "ENTRY" else sig.get("projected_risk_pct"),
        "rs_rating": sig.get("rs_rating"),
        "source": sig.get("source") or "+".join(sig.get("sources") or []) or None,
        "reason": sig.get("reason"),
    }


def _key(row):
    return (row.get("date"), row.get("ticker"), row.get("kind"), row.get("timing"))


def load_history(path=HISTORY_PATH):
    """Every recorded observation, oldest first. Returns [] if nothing yet."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue        # one truncated line must never poison the history
    return rows


def append_observations(entries, watch, run_at=None, path=HISTORY_PATH):
    """Append this run's entry + watch observations; skip ones already recorded
    today. Returns the number of rows actually written."""
    now = run_at or datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")
    run_at_str = now.isoformat()

    rows = [_record(s, "ENTRY", run_at_str, date) for s in (entries or [])]
    rows += [_record(w, "WATCH", run_at_str, date) for w in (watch or [])]
    if not rows:
        return 0

    seen = {_key(r) for r in load_history(path) if r.get("date") == date}

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    written = 0
    with open(path, "a") as f:
        for row in rows:
            k = _key(row)
            if k in seen:
                continue
            f.write(json.dumps(row, default=str) + "\n")
            seen.add(k)
            written += 1
    return written
