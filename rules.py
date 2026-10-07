"""Transparent, rule-based agronomy & market logic. Every score returns its reasons and the data used."""
import math, datetime as dt

KM_COST = 0.35  # DEMO assumption: RWF per kg per km transport cost

def season(d=None):
    m = (d or dt.date.today()).month
    return "A" if m >= 9 or m == 1 else ("B" if 2 <= m <= 6 else "C")

def haversine(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))

def _band(v, lo, hi):
    """1.0 inside [lo,hi]; linear fall-off to 0 at one band-width outside."""
    if v is None: return None
    if lo <= v <= hi: return 1.0
    w = max(hi - lo, 1e-6)
    return max(0.0, 1 - (lo - v if v < lo else v - hi) / w)

def readiness(crop, wx, farm):
    """Planting readiness 0-100 = weighted temp/rain/soil pH/season/water. Returns score, parts, reasons, risks, data used."""
    parts, reasons, risks, used = [], [], [], []
    def add(label, weight, val, why_ok, why_bad, data):
        if val is None: return
        parts.append((label, weight, val)); used.append(data)
        (reasons if val >= .7 else risks).append(why_ok if val >= .7 else why_bad)
    if wx:
        add("Temperature", 25, _band(wx["temp"], crop["temp_min"], crop["temp_max"]), f"Temperature {wx['temp']:.0f}°C suits {crop['name']}",
            f"Temperature {wx['temp']:.0f}°C is outside the ideal {crop['temp_min']:.0f}-{crop['temp_max']:.0f}°C", "weather.temp")
        rv = _band(wx["rain_7d"], crop["rain_min"], crop["rain_max"])
        if rv is not None and farm["irrigation"] and rv < .7: rv = max(rv, .8); reasons.append("Irrigation available offsets low rain")
        add("Rainfall", 30, rv, f"Rainfall {wx['rain_7d']:.0f} mm/week is adequate", f"Rainfall {wx['rain_7d']:.0f} mm/week is outside {crop['rain_min']:.0f}-{crop['rain_max']:.0f} mm", "weather.rain_7d")
    add("Soil pH", 20, _band(farm["soil_ph"], crop["ph_min"], crop["ph_max"]), f"Soil pH {farm['soil_ph']} is suitable",
        f"Soil pH {farm['soil_ph']} is outside {crop['ph_min']}-{crop['ph_max']}; consider liming/advice", "farm.soil_ph")
    sv = 1.0 if season() in crop["seasons"].split(",") else 0.2
    add("Season", 15, sv, f"Season {season()} is a recommended planting season", f"Season {season()} is not a main season for {crop['name']}", "calendar.season")
    add("Water", 10, 1.0 if farm["irrigation"] or crop["water_need"] != "High" else 0.4, "Water supply is sufficient for this crop", f"{crop['name']} needs high water and no irrigation is recorded", "farm.irrigation + crop.water_need")
    tot = sum(w for _, w, _ in parts) or 1
    return {"score": round(sum(w * v for _, w, v in parts) / tot * 100), "parts": parts, "reasons": reasons, "risks": risks, "used": used}

def profit(crop, ha, price=None, extra=0):
    price = price or crop["price_kg"]
    cost = crop["cost_ha"] * ha + extra
    qty = crop["yield_t_ha"] * 1000 * ha
    return {"cost": round(cost), "yield_kg": round(qty), "revenue": round(qty * price), "profit": round(qty * price - cost), "price": round(price)}

def advise(crops, farm, wx, prices):
    """Rank crops: 70% agronomic readiness + 30% profit margin. prices: {crop: current avg price}."""
    out = []
    for c in crops:
        r = readiness(c, wx, farm); p = profit(c, farm["size_ha"], prices.get(c["name"]))
        margin = max(0, min(1, p["profit"] / p["cost"] / 3.0)) if p["cost"] else 0  # profit/cost ratio, 300% = full marks
        total = round(.7 * r["score"] + .3 * margin * 100)
        risks = list(r["risks"]) + ([f"{c['name']} has elevated disease risk"] if c["disease_risk"] >= .5 else [])
        out.append({"crop": c["name"], "score": total, "agro": r["score"], "profit": p, "reasons": r["reasons"], "risks": risks,
                    "used": r["used"] + ["market_prices (avg)", "crop yield estimate (DEMO)"], "water": c["water_need"], "disease": c["disease_risk"]})
    out.sort(key=lambda x: -x["score"])
    return out

def weather_advice(wx_days):
    tips = []
    for i, w in enumerate(wx_days):
        when = "today" if i == 0 else ("tomorrow" if i == 1 else "in 2 days")
        if w["rain_prob"] >= 70: tips.append(("warn", f"Heavy rain likely {when} ({w['rain_prob']}%) - check drainage and avoid spraying."))
        elif w["rain_prob"] >= 40: tips.append(("info", f"Rain possible {when} ({w['rain_prob']}%) - avoid spraying in the afternoon."))
        elif w["rain_prob"] < 20 and w["rain_7d"] < 25: tips.append(("info", f"Dry spell {when} - monitor soil moisture."))
        if w["wind"] > 25: tips.append(("warn", f"Strong wind {when} ({w['wind']:.0f} km/h) - do not spray (drift)."))
    return tips[:4] or [("ok", "No weather risks in the next 3 days.")]

def fert_calc(rows, ha):
    """Nkunganire engine: kg needed and cost at open-market vs subsidized price (None when a price is not loaded)."""
    out = []
    for r in rows:
        kg = r["kg_per_ha"] * ha; mp, sp = r["market_price_per_kg"], r["subsidized_price_per_kg"]
        out.append({"stage": r["stage"], "fertilizer": r["fertilizer_type"], "kg_ha": r["kg_per_ha"], "kg": round(kg, 1), "market_cost": round(kg * mp) if mp else None,
                    "subsidy_cost": round(kg * sp) if sp else None, "saving": round(kg * (mp - sp)) if mp and sp else None, "approval": r["approval"], "source": r["source"]})
    return out, any(r["approval"] != "approved" for r in rows)

def texture(clay, sand):
    """Approximate USDA texture class from clay / sand percentages (None if unknown)."""
    if clay is None or sand is None: return None
    silt = 100 - clay - sand
    if clay >= 40: return "Clay"
    if clay >= 27: return "Sandy clay loam" if sand > 45 else "Clay loam"
    if clay >= 20 and sand > 45: return "Sandy clay loam"
    if sand >= 85: return "Sand"
    if sand >= 70: return "Sandy loam"
    return "Silt loam" if silt >= 50 else "Loam"

def soil_needs(ph, n=None, oc=None):
    """Screening heuristics from topsoil values (NOT a lab test): returns (main deficit text, suggested fertilizer text)."""
    d, f = [], []
    if ph is not None and ph < 5.5: d.append("Soil acidity"); f.append("Agricultural lime first, then DAP/NPK")
    if ph is not None and ph > 7.5: d.append("High pH (alkaline)"); f.append("Organic compost; confirm with a soil test")
    if n is not None and n < 1.5: d.append("Nitrogen"); f.append("Urea / compost at top-dressing")
    if oc is not None and oc < 10: d.append("Organic matter"); f.append("Compost or manure")
    return (" + ".join(d) or "No major deficit flagged"), (" · ".join(f) or "Standard NPK per crop (see Fertilizer page)")

def soil_advice(p):
    """Plain-language soil tips from a soil_profiles row (dict) -> [(level, text)]."""
    if not p: return []
    out, ph = [], p.get("ph_level")
    if ph is not None:
        if ph < 5.5: out.append(("warn", f"Soil pH {ph:.1f} is acidic - lime before planting; acid soil locks up phosphorus and wastes fertilizer."))
        elif ph > 7.5: out.append(("warn", f"Soil pH {ph:.1f} is alkaline - add compost; some nutrients become harder for crops to take up."))
        else: out.append(("ok", f"Soil pH {ph:.1f} is in a comfortable range for most crops."))
    dfc = p.get("main_nutrient_deficit")
    if dfc and not dfc.startswith("No major"): out.append(("info", f"Likely limiting nutrient: {dfc}."))
    return out

def field_advice(p, wx):
    """Soil + weather fusion: should fertilizer go on now? wx = weather rows (today first)."""
    tips = []; rainy = [w for w in wx[:2] if (w["rain_prob"] or 0) >= 70 or (w["rain_mm"] or 0) >= 8]
    if rainy:
        sandy = bool(p) and (p.get("sand_pct") or 0) >= 60
        tips.append(("warn", f"Heavy rain expected ({rainy[0]['rain_mm']} mm, {rainy[0]['rain_prob']}% chance) - do not spread fertilizer today; runoff and leaching will waste it." + (" Sandy soil loses nutrients fastest." if sandy else "")))
    elif wx: tips.append(("ok", "No heavy rain in the next 2 days - a good window to apply fertilizer."))
    tips += soil_advice(p)
    if p and p.get("recommended_fertilizer"): tips.append(("info", f"Suggested for {p['district']}: {p['recommended_fertilizer']}. Confirm rates on the Fertilizer page / with your extension officer."))
    return tips
