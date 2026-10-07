"""Live-data orchestration: weather (Open-Meteo), market prices (WFP via HDX), soil (ISRIC SoilGrids).
Run from the Data page ("Run now") or automatically in the background while the app runs (set KIZA_AUTO_SYNC=0 to turn that off).
Every run is written to sync_log. A failed run NEVER wipes data: the previous rows stay and the pages mark them stale."""
import os, threading, time, logging, datetime as dt
import db, weather_live, live_prices, soil_live

KINDS = ("weather", "prices", "soil")
EVERY_HOURS = {"weather": 3, "prices": 24, "soil": 24 * 30}   # prices: checked daily, but the source publishes monthly
RUNNING, AUTO = set(), False
_L = threading.Lock()

def log(kind, status, detail):
    c = db.conn(); c.execute("INSERT INTO sync_log(kind,status,detail) VALUES(?,?,?)", (kind, status, str(detail)[:300])); c.commit(); c.close()

def run(kind):
    """Run one sync now (blocking). Returns (status, detail); status is ok / partial / failed."""
    c = db.conn()
    try:
        if kind == "weather":
            ok, fail = weather_live.refresh(c); st = "ok" if ok and not fail else "partial" if ok else "failed"
            d = f"{len(ok)} districts updated" + (f", {len(fail)} failed ({fail[0][1]})" if fail else "")
        elif kind == "prices":
            r = live_prices.refresh(c); st = "ok"; d = f"{r['rows']} prices from {r['markets']} markets, latest {r['latest']}" + (f", {r['skipped_markets']} markets skipped (no district)" if r["skipped_markets"] else "") + ("; crops were empty, so 6 BASELINE crop estimates were added - replace with RAB/NISR values on the Data page" if r["seeded_crops"] else "")
        elif kind == "soil":
            ok, fail = soil_live.refresh(c); st = "ok" if not fail else "partial" if ok else "failed"
            d = f"{len(ok)} districts updated" + (f", {len(fail)} failed ({fail[0][1]})" if fail else "") if (ok or fail) else "nothing to update (all districts fresh or official)"
        else: raise ValueError("unknown sync kind")
    except Exception as e: logging.exception(e); st, d = "failed", str(e)[:200]
    finally: c.close()
    log(kind, st, d); return st, d

def start_async(kind):
    """Run in a background thread (prices/soil take a while). False if this kind is already running."""
    with _L:
        if kind in RUNNING or kind not in KINDS: return False
        RUNNING.add(kind)
    def job():
        try: run(kind)
        finally:
            with _L: RUNNING.discard(kind)
    threading.Thread(target=job, daemon=True).start(); return True

def _due(c, kind):
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None); fmt = "%Y-%m-%d %H:%M:%S"
    last_ok = c.execute("SELECT MAX(created_at) FROM sync_log WHERE kind=? AND status IN ('ok','partial')", (kind,)).fetchone()[0]
    last = c.execute("SELECT MAX(created_at) FROM sync_log WHERE kind=?", (kind,)).fetchone()[0]
    if last and now - dt.datetime.strptime(last, fmt) < dt.timedelta(minutes=30): return False      # do not hammer a provider that just failed
    return not last_ok or now - dt.datetime.strptime(last_ok, fmt) >= dt.timedelta(hours=EVERY_HOURS[kind])

def _loop():
    time.sleep(5)
    while True:
        try:
            c = db.conn(); due = [k for k in KINDS if _due(c, k)]; c.close()
            for k in due:
                with _L: RUNNING.add(k)
                try: run(k)
                finally:
                    with _L: RUNNING.discard(k)
        except Exception as e: logging.exception(e)
        time.sleep(600)

def start_background():
    """Start the auto-refresh thread once. Returns False when disabled (KIZA_AUTO_SYNC=0) or already started."""
    global AUTO
    if os.environ.get("KIZA_AUTO_SYNC", "1") == "0" or AUTO: return False
    AUTO = True; threading.Thread(target=_loop, daemon=True).start(); return True
