"""E-Soko pricing pipeline: weekly market prices -> per-district current price + 14-day trend (esoko_prices)."""
import datetime as dt

def refresh(c):
    rows = c.execute("SELECT m.district, p.crop, p.day, AVG(p.price_kg) pr FROM market_prices p JOIN markets m ON m.id=p.market_id GROUP BY m.district,p.crop,p.day ORDER BY p.day").fetchall()
    series = {}
    for r in rows: series.setdefault((r[0], r[1]), []).append((r[2], r[3]))
    c.execute("DELETE FROM esoko_prices")
    for (d, crop), pts in series.items():
        last = dt.date.fromisoformat(pts[-1][0]); win = [b for a, b in pts if (last - dt.date.fromisoformat(a)).days <= 14]
        if len(win) < 2:   # sparse data (e.g. monthly WFP prices): compare with the previous reading if it is under 70 days older
            prev = [b for a, b in pts[:-1] if (last - dt.date.fromisoformat(a)).days <= 70]
            if prev: win = [prev[-1], pts[-1][1]]
        if len(win) >= 2: ch = (win[-1] - win[0]) / win[0] * 100; tr = "rising" if ch > 3 else "falling" if ch < -3 else "stable"
        else: ch, tr = None, "insufficient data"
        c.execute("INSERT INTO esoko_prices(district,crop,current_price_rwf,predicted_trend,change_pct,as_of) VALUES(?,?,?,?,?,?)", (d, crop, round(pts[-1][1]), tr, None if ch is None else round(ch, 1), pts[-1][0]))
    c.commit(); return len(series)
