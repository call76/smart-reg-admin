"""ETL: extract CSV -> validate -> clean -> load market_prices. Rejected rows are written to data_quality_logs.
CSV columns: market,crop,day(YYYY-MM-DD),price_kg,source.   Usage: python pipelines/import_prices.py data/sample_prices.csv"""
import sys, os, datetime as dt
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

def run(path):
    df = pd.read_csv(path, dtype=str); c = db.conn(); src = os.path.basename(path)
    markets = {r["name"]: r["id"] for r in c.execute("SELECT id,name FROM markets")}; crops = {r["name"] for r in c.execute("SELECT name FROM crops")}
    ok = bad = 0; seen = set()
    for i, r in df.iterrows():
        issue = None
        try:
            price = float(r["price_kg"]); day = dt.date.fromisoformat(str(r["day"]).strip()); key = (r["market"], r["crop"], str(day))
            if r["market"] not in markets: issue = "unknown market"
            elif r["crop"] not in crops: issue = "unknown crop"
            elif price <= 0: issue = "invalid price (<=0)"
            elif day > dt.date.today(): issue = "impossible date (future)"
            elif key in seen: issue = "duplicate record in file"
            seen.add(key)
        except Exception: issue = "missing or malformed value"
        if issue: c.execute("INSERT INTO data_quality_logs(source,row_no,issue) VALUES(?,?,?)", (src, i + 2, issue)); bad += 1
        else:
            c.execute("INSERT OR REPLACE INTO market_prices(market_id,crop,day,price_kg,source) VALUES(?,?,?,?,?)", (markets[r["market"]], r["crop"], str(day), price, r.get("source") or src)); ok += 1
    c.commit(); c.close(); return ok, bad

if __name__ == "__main__":
    db.init(); ok, bad = run(sys.argv[1]); print(f"loaded {ok} rows, rejected {bad} (see data_quality_logs)")
