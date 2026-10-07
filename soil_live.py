"""Soil layer. Two honest sources, both labelled in the UI:
  1) LIVE/MODELLED: ISRIC SoilGrids 2.0 point query at each district centroid (free, no key, 250 m model, topsoil 0-30 cm).
     ISRIC allows ~5 calls/min, so the refresh is throttled (30 districts ~ 6 min) and runs in the background. Refreshed monthly.
  2) OFFICIAL: RAB / NISR soil tables uploaded as CSV on the Data page (kind soil_profiles). An official row is never overwritten by SoilGrids.
SoilGrids values are model estimates for screening - they do not replace a laboratory soil test."""
import json, time, urllib.request, urllib.parse, datetime as dt
import rules

API = "https://rest.isric.org/soilgrids/v2.0/properties/query"
PROPS = ("phh2o", "nitrogen", "soc", "clay", "sand"); DEPTHS = ("0-5cm", "5-15cm", "15-30cm")
SOURCE = "ISRIC SoilGrids 2.0 (modelled, topsoil 0-30 cm)"

def _get(url, timeout=45):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "KIZA-AGRI/1.0"}), timeout=timeout) as r: return json.loads(r.read())

def parse(data):
    """SoilGrids payload -> {property: topsoil mean in conventional units}. Integer values are divided by each layer's d_factor
    (pH*10 -> pH, cg/kg -> g/kg nitrogen, dg/kg -> g/kg carbon, g/kg -> % clay/sand)."""
    out = {}
    for layer in data["properties"]["layers"]:
        f = (layer.get("unit_measure") or {}).get("d_factor") or 1
        v = [d["values"]["mean"] for d in layer.get("depths", []) if d.get("label") in DEPTHS and (d.get("values") or {}).get("mean") is not None]
        if v: out[layer["name"]] = sum(v) / len(v) / f
    if "phh2o" not in out: raise ValueError("no pH in SoilGrids response")
    return out

def derive(v):
    ph, n, oc, cl, sa = (v.get(k) for k in PROPS); deficit, fert = rules.soil_needs(ph, n, oc)
    return dict(ph=round(ph, 1), n=None if n is None else round(n, 2), oc=None if oc is None else round(oc, 1), clay=None if cl is None else round(cl),
                sand=None if sa is None else round(sa), soil_type=rules.texture(cl, sa) or "Not available", deficit=deficit, fert=fert)

def url_for(lat, lng):
    q = [("lon", lng), ("lat", lat)] + [("property", p) for p in PROPS] + [("depth", d) for d in DEPTHS] + [("value", "mean")]
    return API + "?" + urllib.parse.urlencode(q)

def refresh(c, getter=_get, pause=13, force=False):
    """Returns (ok_districts, [(district, reason)]). Official uploads and fresh (<30 d) SoilGrids rows are skipped unless force. Stops early if the provider is unreachable."""
    ok, fail, called, bad, today = [], [], False, 0, dt.date.today(); now = dt.datetime.now().isoformat(timespec="minutes")
    for d in c.execute("SELECT name,lat,lng FROM districts ORDER BY name").fetchall():
        cur = c.execute("SELECT source,is_demo,fetched_at FROM soil_profiles WHERE district=?", (d["name"],)).fetchone()
        if cur and not cur["is_demo"] and not (cur["source"] or "").startswith("ISRIC"): continue
        if cur and not force and not cur["is_demo"] and (cur["fetched_at"] or "")[:10] >= str(today - dt.timedelta(30)): continue
        try:
            if d["lat"] is None or d["lng"] is None: raise ValueError("no coordinates")
            if called and pause: time.sleep(pause)
            called = True; p = derive(parse(getter(url_for(d["lat"], d["lng"]))))
            c.execute("INSERT OR REPLACE INTO soil_profiles(district,soil_type,ph_level,nitrogen_g_kg,organic_c_g_kg,clay_pct,sand_pct,main_nutrient_deficit,recommended_fertilizer,source,is_demo,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,0,?)",
                      (d["name"], p["soil_type"], p["ph"], p["n"], p["oc"], p["clay"], p["sand"], p["deficit"], p["fert"], SOURCE, now)); c.commit(); ok.append(d["name"]); bad = 0
        except Exception as e:
            fail.append((d["name"], str(e)[:70])); bad += 1
            if bad >= 3: fail.append(("(remaining districts)", "stopped after 3 failures in a row - provider unreachable, will retry later")); break
    return ok, fail
