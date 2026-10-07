"""KIZA-AGRI Python intelligence service (port 8001). Documented HTTP API consumed by the main app.
Models here are statistical/rule-based and labelled EXPERIMENTAL until validated datasets exist - no random outputs."""
import numpy as np
from flask import Flask, request, jsonify

app = Flask(__name__)

def bad(msg): return jsonify({"error": msg}), 400

@app.get("/health")
def health(): return {"status": "ok", "models": ["price-trend-v0.1", "yield-baseline-v0.1"]}

@app.post("/ml/price-forecast")
def price_forecast():
    d = request.get_json(silent=True) or {}
    pts = d.get("prices", [])
    if not isinstance(pts, list) or len(pts) < 4: return bad("need at least 4 price points")
    try:
        y = np.array([float(p) for p in pts]); assert (y > 0).all()
    except Exception: return bad("prices must be positive numbers")
    x = np.arange(len(y)); slope, icpt = np.polyfit(x, y, 1)
    fit = slope * x + icpt; resid = y - fit; sd = float(resid.std())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0; r2 = 1 - float((resid ** 2).sum()) / ss_tot
    nxt = float(slope * (len(y) + 1) + icpt); band = max(1.5 * sd, 0.02 * nxt)
    direction = "rising" if slope / y.mean() > .004 else "falling" if slope / y.mean() < -.004 else "stable"
    conf = "medium" if (len(y) >= 8 and r2 > .6) else "low"
    return {"crop": d.get("crop"), "current": float(y[-1]), "forecast_2w": [round(nxt - band), round(nxt + band)], "direction": direction,
            "confidence": conf, "r2": round(r2, 2), "model": "price-trend", "version": "0.1", "status": "Experimental",
            "factors": ["historical price trend (linear)", f"{len(y)} weekly observations"],
            "note": "Forecast range, not a certainty. Supply/demand/fuel factors not yet modelled."}

@app.post("/ml/yield-prediction")
def yield_prediction():
    d = request.get_json(silent=True) or {}
    try:
        area = float(d["area_ha"]); base = float(d["base_yield_t_ha"]); ready = float(d["readiness"])
        assert area > 0 and base > 0 and 0 <= ready <= 100
    except Exception: return bad("area_ha, base_yield_t_ha, readiness(0-100) required")
    f = 0.6 + 0.5 * ready / 100        # transparent adjustment: readiness 0 -> 0.6x, 100 -> 1.1x
    mid = area * base * f
    return {"crop": d.get("crop"), "expected_t": [round(mid * .8, 2), round(mid * 1.1, 2)], "factor": round(f, 2), "confidence": "low",
            "model": "yield-baseline", "version": "0.1", "status": "Experimental",
            "factors": [f"baseline {base} t/ha (DEMO estimate)", f"conditions factor x{f:.2f} from planting readiness {ready:.0f}"]}

if __name__ == "__main__":
    app.run(port=8001)
