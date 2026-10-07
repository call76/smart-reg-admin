"""Live market prices: WFP "Rwanda - Food Prices" on HDX (CC-BY-IGO; the publisher updates it monthly).
Needs internet. Flow: HDX package API -> current CSV link -> parse -> markets + market_prices (source-tagged) -> E-Soko district trends.
Rows are kept only if: currency RWF, retail, a unit we can convert to RWF/kg, a supported crop, date not in the future.
The HXL tag row (#date, #value ...) that HDX puts under the header is skipped automatically."""
import csv, io, json, urllib.request, datetime as dt
import db, rules, esoko

PKG = "https://data.humdata.org/api/3/action/package_show?id=wfp-food-prices-for-rwanda"
SRC = "WFP via HDX"
UNIT = {"KG": 1, "100 KG": 100, "MT": 1000, "G": .001}            # divisor -> RWF per kg
CROPS = (("Maize", "maize"), ("Beans", "bean"), ("Potato", "potato"), ("Cassava", "cassava"), ("Rice", "rice"), ("Tomato", "tomato"))
SKIP = ("flour", "meal", "sweet", "oil", "bread", "chips", "dried")  # processed products / sweet potato are not our crops

def crop_of(commodity):
    s = (commodity or "").lower()
    if any(w in s for w in SKIP): return None
    for crop, key in CROPS:
        if key in s: return None if (crop == "Cassava" and "dry" in s) else crop

def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def parse(fh, months=18, today=None):
    """CSV file object -> ({(market, crop, day): RWF/kg averaged over commodity variants}, {market: (admin2, lat, lng)})."""
    today = today or dt.date.today(); since = today - dt.timedelta(30 * months); agg, geo = {}, {}
    for r in csv.DictReader(fh):
        d = (r.get("date") or "").strip()
        if not d or d.startswith("#"): continue
        try:
            day = dt.date.fromisoformat(d)
            if day < since or day > today or (r.get("currency") or "").strip().upper() != "RWF": continue
            if "wholesale" in (r.get("pricetype") or "").lower(): continue
            crop = crop_of(r.get("commodity")); div = UNIT.get((r.get("unit") or "").strip().upper())
            if not crop or not div: continue
            p = float(r["price"]) / div
            if not 0 < p < 20000: continue
        except (ValueError, KeyError, TypeError): continue
        mk = (r.get("market") or "").strip()
        if not mk: continue
        agg.setdefault((mk, crop, day), []).append(p); geo.setdefault(mk, ((r.get("admin2") or "").strip(), _f(r.get("latitude")), _f(r.get("longitude"))))
    return {k: round(sum(v) / len(v)) for k, v in agg.items()}, geo

def district_of(admin2, lat, lng, districts):
    """Exact district-name match first; otherwise the nearest district centroid within 60 km; otherwise None (market skipped)."""
    names = {d["name"].lower(): d["name"] for d in districts}
    if admin2.lower() in names: return names[admin2.lower()]
    if lat is not None and lng is not None:
        near = [(rules.haversine(lat, lng, d["lat"], d["lng"]), d["name"]) for d in districts if d["lat"] is not None and d["lng"] is not None]
        if near and min(near)[0] < 60: return min(near)[1]
    return None

def _open(url, timeout=180): return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "KIZA-AGRI/1.0"}), timeout=timeout)

def csv_url(opener=_open):
    with opener(PKG) as r: res = json.loads(r.read())["result"]["resources"]
    for x in res:
        if (x.get("format") or "").upper() == "CSV" and "quickchart" not in ((x.get("name") or "") + (x.get("url") or "")).lower(): return x["url"]
    raise ValueError("HDX package has no CSV resource")

def refresh(c, opener=_open, months=18):
    """Download + load. Raises on any problem BEFORE touching data, so a failed run keeps the previous prices. Returns a summary dict."""
    crops = {x[0] for x in c.execute("SELECT name FROM crops")}; seeded = False
    with opener(csv_url(opener)) as r: prices, geo = parse(io.TextIOWrapper(r, encoding="utf-8-sig", newline=""), months)
    if not prices: raise ValueError("file downloaded but it has no usable RWF retail prices for the supported crops")
    if not crops:   # clean real-mode database: add the 6 supported crops with the built-in BASELINE agronomy estimates so prices can load; upload RAB/NISR crop values on the Data page to replace them
        c.executemany("INSERT INTO crops VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", db.CROPS); crops = {x[0] for x in db.CROPS}; seeded = True
    ds = [dict(x) for x in c.execute("SELECT name,lat,lng FROM districts")]; mid, skipped = {}, 0
    for mk, (adm, lat, lng) in geo.items():
        d = district_of(adm, lat, lng, ds)
        if not d: skipped += 1; continue
        row = c.execute("SELECT id FROM markets WHERE name=?", (mk,)).fetchone()
        if row: c.execute("UPDATE markets SET district=?,lat=COALESCE(?,lat),lng=COALESCE(?,lng) WHERE id=?", (d, lat, lng, row[0])); mid[mk] = row[0]
        else: mid[mk] = c.execute("INSERT INTO markets(name,district,lat,lng) VALUES(?,?,?,?)", (mk, d, lat, lng)).lastrowid
    demo_m = [x[0] for x in c.execute("SELECT DISTINCT market_id FROM market_prices WHERE source LIKE '%DEMO%'")]; n = 0; latest = None
    for (mk, crop, day), p in prices.items():
        if mk in mid and crop in crops:
            c.execute("INSERT OR REPLACE INTO market_prices(market_id,crop,day,price_kg,source) VALUES(?,?,?,?,?)", (mid[mk], crop, str(day), p, SRC)); n += 1; latest = max(latest or day, day)
    if not n: c.rollback(); raise ValueError("no price row matched a known market district and crop")
    c.execute("DELETE FROM market_prices WHERE source LIKE '%DEMO%'")                    # real prices replace the demo ones
    if demo_m: c.execute(f"DELETE FROM markets WHERE id IN ({','.join(str(i) for i in demo_m)}) AND id NOT IN (SELECT market_id FROM market_prices)")
    esoko.refresh(c); c.commit()
    return {"rows": n, "markets": len(mid), "skipped_markets": skipped, "latest": str(latest), "seeded_crops": seeded}
