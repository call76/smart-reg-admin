"""Calls the Python ML service. Any failure returns None so the main app keeps working (spec: ML failure must not break the app)."""
import json, os, urllib.request
URL = os.environ.get("KIZA_ML_URL", "http://127.0.0.1:8001")

def call(path, payload=None, timeout=2):
    try:
        req = urllib.request.Request(URL + path, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read())
    except Exception: return None

def valid_forecast(r): return bool(r) and isinstance(r.get("forecast_2w"), list) and len(r["forecast_2w"]) == 2 and "direction" in r
def valid_yield(r): return bool(r) and isinstance(r.get("expected_t"), list) and len(r["expected_t"]) == 2
