"""Live weather from Open-Meteo (free, no API key). Needs internet. On failure the previous (cached) rows are kept and flagged stale by the UI."""
import json, urllib.request, urllib.parse, datetime as dt
API = "https://api.open-meteo.com/v1/forecast"; GEO = "https://geocoding-api.open-meteo.com/v1/search"

def _get(url, timeout=12):
    with urllib.request.urlopen(url, timeout=timeout) as r: return json.loads(r.read())

def parse(data):
    """Open-Meteo daily payload (past_days=7, forecast_days=3) -> 3 rows for today..+2 with rain over the previous 7 days."""
    d = data["daily"]; t = d["time"]; pr = [x or 0 for x in d["precipitation_sum"]]
    if len(t) < 10: raise ValueError("unexpected forecast length")
    rows = []
    for i in range(7, 10):
        if d["temperature_2m_max"][i] is None or d["temperature_2m_min"][i] is None: continue
        rows.append(dict(day=t[i], temp=(d["temperature_2m_max"][i] + d["temperature_2m_min"][i]) / 2, rain_prob=int(d["precipitation_probability_max"][i] or 0),
                         rain_mm=round(pr[i], 1), rain_7d=round(sum(pr[i - 7:i]), 1), wind=round(d["wind_speed_10m_max"][i] or 0, 1)))
    if not rows: raise ValueError("no usable forecast rows")
    return rows

def refresh(c, getter=_get):
    ok, fail = [], []
    for d in c.execute("SELECT * FROM districts").fetchall():
        try:
            lat, lng = d["lat"], d["lng"]
            if lat is None or lng is None:
                res = [x for x in getter(GEO + "?" + urllib.parse.urlencode({"name": d["name"], "count": 5, "country_code": "RW"})).get("results", []) if x.get("country_code") == "RW"]
                if not res: raise ValueError("coordinates not found")
                lat, lng = res[0]["latitude"], res[0]["longitude"]; c.execute("UPDATE districts SET lat=?,lng=? WHERE name=?", (lat, lng, d["name"]))
            q = {"latitude": lat, "longitude": lng, "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max",
                 "past_days": 7, "forecast_days": 3, "timezone": "Africa/Kigali"}
            rows = parse(getter(API + "?" + urllib.parse.urlencode(q)))
            c.execute("DELETE FROM weather WHERE district=?", (d["name"],)); now = dt.datetime.now().isoformat(timespec="minutes")
            for r in rows: c.execute("INSERT INTO weather(district,day,temp,rain_prob,rain_mm,rain_7d,humidity,wind,is_demo,fetched_at) VALUES(?,?,?,?,?,?,NULL,?,0,?)",
                                     (d["name"], r["day"], r["temp"], r["rain_prob"], r["rain_mm"], r["rain_7d"], r["wind"], now))
            ok.append(d["name"])
        except Exception as e: fail.append((d["name"], str(e)[:70]))
    c.commit(); return ok, fail
