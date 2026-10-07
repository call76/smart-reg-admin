"""Validated CSV/Excel loaders for REAL data. Bad rows are rejected and logged to data_quality_logs."""
import csv, io, zipfile, datetime as dt
import xml.etree.ElementTree as ET
import db, esoko

KINDS = {
 "districts": ["name", "province", "lat", "lng"],
 "crops": ["name", "yield_t_ha", "price_kg", "cost_ha", "temp_min", "temp_max", "rain_min", "rain_max", "ph_min", "ph_max", "seasons", "water_need", "disease_risk", "cycle_days"],
 "markets": ["name", "district", "lat", "lng"],
 "market_prices": ["market", "crop", "day", "price_kg", "source"],
 "fertilizer_subsidies": ["crop", "stage", "fertilizer_type", "kg_per_ha", "market_price_per_kg", "subsidized_price_per_kg", "source", "approval"],
 "luc_zones": ["district", "sector", "mandated_crop"],
 "imihigo_targets": ["district", "crop", "target_ha"],
 "soil_profiles": ["district", "soil_type", "ph_level", "main_nutrient_deficit", "recommended_fertilizer", "source"],
}
HELP = {
 "districts": "Optional: refine district coordinates (lat -3..0, lng 28..31). The 30 districts already exist.",
 "crops": "One row per crop. seasons like A,B or A,B,C. water_need = Low/Medium/High. disease_risk 0-1. rain_* = mm per week. Use RAB/NISR values.",
 "markets": "Market name, its district and coordinates (used for net price after transport).",
 "market_prices": "E-Soko price sheets (CSV or Excel). day = YYYY-MM-DD. source REQUIRED. Rows with DEMO in source are rejected. 14-day trends are recomputed after upload.",
 "fertilizer_subsidies": "Nkunganire inputs: kg per hectare plus open-market and subsidized RWF/kg. stage = planting or top_dressing. source REQUIRED. approval = pending or approved.",
 "luc_zones": "Land Use Consolidation: the mandated/priority crop for each district + sector.",
 "imihigo_targets": "District Imihigo target in hectares per crop.",
 "soil_profiles": "OFFICIAL soil table per district (RAB / NISR): soil type, pH 3-9, main nutrient deficit, recommended fertilizer. source REQUIRED. Official rows are never overwritten by the SoilGrids model.",
}

def _n(v, lo=None, hi=None, req=True):
    v = (v or "").strip().replace(",", "")
    if v == "":
        if req: raise ValueError("missing number")
        return None
    x = float(v)
    if (lo is not None and x < lo) or (hi is not None and x > hi): raise ValueError(f"{x} out of range")
    return x

def _s(v, req=True):
    v = (v or "").strip()
    if req and not v: raise ValueError("missing text")
    return v

def _day(v):
    v = _s(v)
    if v.replace(".", "").isdigit() and float(v) > 20000: return dt.date(1899, 12, 30) + dt.timedelta(days=int(float(v)))  # Excel serial date
    return dt.date.fromisoformat(v)

def _has(c, table, col, val): return c.execute(f"SELECT 1 FROM {table} WHERE {col}=?", (val,)).fetchone() is not None

def _row(kind, r, c, uid):
    today = dt.date.today()
    if kind == "districts":
        n = _s(r["name"])
        if not _has(c, "districts", "name", n): raise ValueError("unknown district")
        c.execute("UPDATE districts SET province=COALESCE(NULLIF(?,''),province),lat=COALESCE(?,lat),lng=COALESCE(?,lng) WHERE name=?", (_s(r["province"], False), _n(r["lat"], -3, 0, False), _n(r["lng"], 28, 31, False), n))
    elif kind == "crops":
        v = [_s(r["name"]), _n(r["yield_t_ha"], .01, 200), _n(r["price_kg"], 1, 1e6), _n(r["cost_ha"], 0, 1e9), _n(r["temp_min"], -5, 45), _n(r["temp_max"], -5, 50),
             _n(r["rain_min"], 0, 500), _n(r["rain_max"], 0, 1000), _n(r["ph_min"], 3, 9), _n(r["ph_max"], 3, 10)]
        if v[4] >= v[5] or v[6] >= v[7] or v[8] >= v[9]: raise ValueError("min must be below max")
        se = ",".join(sorted({x.strip().upper() for x in r["seasons"].split(",") if x.strip()}))
        if not se or set(se.split(",")) - {"A", "B", "C"}: raise ValueError("seasons must be from A,B,C")
        w = _s(r["water_need"]).title()
        if w not in ("Low", "Medium", "High"): raise ValueError("water_need must be Low/Medium/High")
        c.execute("INSERT INTO crops VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET yield_t_ha=excluded.yield_t_ha,price_kg=excluded.price_kg,cost_ha=excluded.cost_ha,temp_min=excluded.temp_min,"
                  "temp_max=excluded.temp_max,rain_min=excluded.rain_min,rain_max=excluded.rain_max,ph_min=excluded.ph_min,ph_max=excluded.ph_max,seasons=excluded.seasons,water_need=excluded.water_need,"
                  "disease_risk=excluded.disease_risk,cycle_days=excluded.cycle_days", (*v, se, w, _n(r["disease_risk"], 0, 1), int(_n(r["cycle_days"], 20, 800))))
    elif kind == "markets":
        n, d = _s(r["name"]), _s(r["district"])
        if not _has(c, "districts", "name", d): raise ValueError("unknown district")
        lat, lng = _n(r["lat"], -3, 0, False), _n(r["lng"], 28, 31, False)
        if _has(c, "markets", "name", n): c.execute("UPDATE markets SET district=?,lat=?,lng=? WHERE name=?", (d, lat, lng, n))
        else: c.execute("INSERT INTO markets(name,district,lat,lng) VALUES(?,?,?,?)", (n, d, lat, lng))
    elif kind == "market_prices":
        m = c.execute("SELECT id FROM markets WHERE name=?", (_s(r["market"]),)).fetchone()
        if not m: raise ValueError("unknown market (load markets first)")
        crop = _s(r["crop"])
        if not _has(c, "crops", "name", crop): raise ValueError("unknown crop (load crops first)")
        day = _day(r["day"])
        if day > today: raise ValueError("date in the future")
        src = _s(r["source"])
        if "DEMO" in src.upper(): raise ValueError("DEMO source not allowed")
        c.execute("INSERT OR REPLACE INTO market_prices(market_id,crop,day,price_kg,source) VALUES(?,?,?,?,?)", (m[0], crop, str(day), _n(r["price_kg"], .01, 1e6), src))
    elif kind == "fertilizer_subsidies":
        crop, stage, fert = _s(r["crop"]), _s(r["stage"]).lower(), _s(r["fertilizer_type"])
        if not _has(c, "crops", "name", crop): raise ValueError("unknown crop (load crops first)")
        if stage not in ("planting", "top_dressing"): raise ValueError("stage must be planting or top_dressing")
        src = _s(r["source"]); ap = (r["approval"] or "pending").strip().lower()
        if ap not in ("pending", "approved"): raise ValueError("approval must be pending or approved")
        kg, mp, sp = _n(r["kg_per_ha"], .1, 5000), _n(r["market_price_per_kg"], 1, 1e6, False), _n(r["subsidized_price_per_kg"], 1, 1e6, False)
        if mp and sp and sp > mp: raise ValueError("subsidized price above market price")
        who = uid if ap == "approved" else None
        ex = c.execute("SELECT id FROM fertilizer_subsidies WHERE crop=? AND stage=? AND fertilizer_type=?", (crop, stage, fert)).fetchone()
        if ex: c.execute("UPDATE fertilizer_subsidies SET kg_per_ha=?,market_price_per_kg=?,subsidized_price_per_kg=?,source=?,approval=?,approved_by=?,effective_date=? WHERE id=?", (kg, mp, sp, src, ap, who, str(today), ex[0]))
        else: c.execute("INSERT INTO fertilizer_subsidies(crop,stage,fertilizer_type,kg_per_ha,market_price_per_kg,subsidized_price_per_kg,source,approval,approved_by,effective_date) VALUES(?,?,?,?,?,?,?,?,?,?)", (crop, stage, fert, kg, mp, sp, src, ap, who, str(today)))
    elif kind == "luc_zones":
        d, s, cr = _s(r["district"]), _s(r["sector"]), _s(r["mandated_crop"])
        if not _has(c, "districts", "name", d): raise ValueError("unknown district")
        if not _has(c, "crops", "name", cr): raise ValueError("unknown crop (load crops first)")
        c.execute("INSERT INTO luc_zones(district,sector,mandated_crop) VALUES(?,?,?) ON CONFLICT(district,sector) DO UPDATE SET mandated_crop=excluded.mandated_crop", (d, s, cr))
    elif kind == "soil_profiles":
        d = _s(r["district"]); src = _s(r["source"])
        if not _has(c, "districts", "name", d): raise ValueError("unknown district")
        if "DEMO" in src.upper(): raise ValueError("DEMO source not allowed")
        c.execute("INSERT INTO soil_profiles(district,soil_type,ph_level,main_nutrient_deficit,recommended_fertilizer,source,is_demo,fetched_at) VALUES(?,?,?,?,?,?,0,?) ON CONFLICT(district) DO UPDATE SET "
                  "soil_type=excluded.soil_type,ph_level=excluded.ph_level,nitrogen_g_kg=NULL,organic_c_g_kg=NULL,clay_pct=NULL,sand_pct=NULL,main_nutrient_deficit=excluded.main_nutrient_deficit,"
                  "recommended_fertilizer=excluded.recommended_fertilizer,source=excluded.source,is_demo=0,fetched_at=excluded.fetched_at",
                  (d, _s(r["soil_type"], False), _n(r["ph_level"], 3, 9), _s(r["main_nutrient_deficit"], False), _s(r["recommended_fertilizer"], False), src, str(today)))
    elif kind == "imihigo_targets":
        d, cr = _s(r["district"]), _s(r["crop"])
        if not _has(c, "districts", "name", d): raise ValueError("unknown district")
        if not _has(c, "crops", "name", cr): raise ValueError("unknown crop (load crops first)")
        c.execute("INSERT INTO imihigo_targets(district,crop,target_ha) VALUES(?,?,?) ON CONFLICT(district,crop) DO UPDATE SET target_ha=excluded.target_ha", (d, cr, _n(r["target_ha"], .1, 1e7)))

def xlsx_to_csv(data):
    """Minimal stdlib reader for the first sheet of an .xlsx (strings + numbers)."""
    z = zipfile.ZipFile(io.BytesIO(data)); M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"; ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = ["".join(t.text or "" for t in si.iter(M + "t")) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(M + "si")]
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]; out = io.StringIO(); w = csv.writer(out)
    for row in ET.fromstring(z.read(sheet)).iter(M + "row"):
        cells = {}
        for c in row.iter(M + "c"):
            col = 0
            for ch in "".join(x for x in c.get("r") if x.isalpha()): col = col * 26 + ord(ch) - 64
            v = c.find(M + "v"); t = c.get("t")
            cells[col - 1] = ss[int(v.text)] if t == "s" and v is not None else "".join(x.text or "" for x in c.iter(M + "t")) if t == "inlineStr" else (v.text if v is not None else "")
        if cells: w.writerow([cells.get(i, "") for i in range(max(cells) + 1)])
    return out.getvalue()

def load(kind, text, uid=None, conn=None):
    c = conn or db.conn(); rd = csv.DictReader(io.StringIO(text))
    miss = [h for h in KINDS[kind] if h not in (rd.fieldnames or [])]
    if miss: return {"ok": 0, "bad": 0, "issues": [(1, "missing columns: " + ", ".join(miss))]}
    ok, issues = 0, []
    for i, r in enumerate(rd, start=2):
        r = {k: (v or "") for k, v in r.items() if k}
        try: _row(kind, r, c, uid); ok += 1
        except Exception as e: issues.append((i, str(e)[:90]))
    for i, m in issues: c.execute("INSERT INTO data_quality_logs(source,row_no,issue) VALUES(?,?,?)", (kind, i, m))
    c.commit()
    if kind == "market_prices" and ok: esoko.refresh(c)
    return {"ok": ok, "bad": len(issues), "issues": issues[:15]}
