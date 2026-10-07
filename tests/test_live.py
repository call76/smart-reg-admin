"""Live-data layers, tested OFFLINE with fake provider payloads: WFP/HDX prices, SoilGrids soil, soil+weather fusion, sync log, pages.
Run: python -m unittest discover tests"""
import os, sys, io, re, json, sqlite3, tempfile, unittest, datetime as dt
os.environ.setdefault("KIZA_DB", os.path.join(tempfile.mkdtemp(), "t.db")); os.environ.setdefault("KIZA_ML_URL", "http://127.0.0.1:59999")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db, rules, loaders, live_prices, soil_live, sync

def fresh():
    """Own throw-away database (never the app's): schema + 30 districts + crops + one DEMO market with a DEMO price."""
    c = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "x.db")); c.row_factory = sqlite3.Row; c.executescript(db.SCHEMA); c.executescript(db.MIGRATE)
    for d in db.RW30: c.execute("INSERT INTO districts VALUES(?,?,?,?)", d)
    c.executemany("INSERT INTO crops VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", db.CROPS)
    c.execute("INSERT INTO markets(id,name,district,lat,lng) VALUES(99,'Musanze Market','Musanze',-1.5,29.6)")
    c.execute("INSERT INTO market_prices(market_id,crop,day,price_kg,source) VALUES(99,'Maize',?,400,'DEMO DATA')", (str(dt.date.today()),)); c.commit(); return c

T = dt.date.today(); D_OLD, D_NEW = T - dt.timedelta(45), T - dt.timedelta(15)
HEAD = "date,admin1,admin2,market,latitude,longitude,category,commodity,unit,priceflag,pricetype,currency,price,usdprice\n"
HXL = "#date,#adm1+name,#adm2+name,#loc+market+name,#geo+lat,#geo+lon,#item+type,#item+name,#item+unit,#item+price+flag,#item+price+type,#currency,#value,#value+usd\n"
def row(day, mk, adm, lat, lng, com, unit, ptype, cur, price): return f"{day},Northern,{adm},{mk},{lat},{lng},cereals and tubers,{com},{unit},actual,{ptype},{cur},{price},1\n"
WFP = HEAD + HXL + "".join([
    row(D_OLD, "Musanze", "Musanze", -1.50, 29.63, "Maize", "KG", "Retail", "RWF", 400),
    row(D_NEW, "Musanze", "Musanze", -1.50, 29.63, "Maize", "KG", "Retail", "RWF", 440),
    row(D_NEW, "Musanze", "Musanze", -1.50, 29.63, "Beans (dry)", "KG", "Retail", "RWF", 800),
    row(D_NEW, "Musanze", "Musanze", -1.50, 29.63, "Beans", "KG", "Retail", "RWF", 900),
    row(D_NEW, "Musanze", "Musanze", -1.50, 29.63, "Potatoes (Irish)", "100 KG", "Retail", "RWF", 35000),
    row(D_NEW, "Musanze", "Musanze", -1.50, 29.63, "Rice (imported)", "KG", "Retail", "RWF", 1200),
    row(D_NEW, "Musanze", "Musanze", -1.50, 29.63, "Maize flour", "KG", "Retail", "RWF", 999),            # processed -> ignored
    row(D_NEW, "Musanze", "Musanze", -1.50, 29.63, "Maize", "KG", "Wholesale", "RWF", 1),                  # wholesale -> ignored
    row(D_NEW, "Musanze", "Musanze", -1.50, 29.63, "Maize", "KG", "Retail", "USD", 5),                     # not RWF -> ignored
    row(D_NEW, "Musanze", "Musanze", -1.50, 29.63, "Cassava (dry)", "KG", "Retail", "RWF", 700),           # dried product -> ignored
    row(T + dt.timedelta(30), "Musanze", "Musanze", -1.50, 29.63, "Maize", "KG", "Retail", "RWF", 777),    # future -> ignored
    row(D_NEW, "Kimironko", "Kigali City", -1.89, 30.13, "Maize", "KG", "Retail", "RWF", 500),             # no district name match -> nearest centroid (Gasabo)
    row(D_NEW, "Atlantis", "Atlantis", 10.0, 10.0, "Maize", "KG", "Retail", "RWF", 500)])                  # unplaceable -> skipped
def opener(url, *a, **k):
    if "package_show" in url: return io.BytesIO(json.dumps({"result": {"resources": [{"format": "CSV", "name": "QuickCharts: Rwanda - Food Prices", "url": "https://x/q.csv"}, {"format": "CSV", "name": "Rwanda - Food Prices", "url": "https://x/wfp.csv"}]}}).encode())
    assert url == "https://x/wfp.csv", url; return io.BytesIO(WFP.encode())

class Prices(unittest.TestCase):
    def test_parse_filters_and_units(self):
        p, geo = live_prices.parse(io.StringIO(WFP))
        self.assertEqual(p[("Musanze", "Maize", D_NEW)], 440); self.assertEqual(p[("Musanze", "Beans", D_NEW)], 850)   # variants averaged
        self.assertEqual(p[("Musanze", "Potato", D_NEW)], 350)                                                       # 100 KG unit converted
        self.assertEqual(len([k for k in p if k[0] == "Musanze"]), 5); self.assertEqual(set(geo), {"Musanze", "Kimironko", "Atlantis"})
    def test_refresh_loads_replaces_demo_and_trends(self):
        c = fresh(); r = live_prices.refresh(c, opener)
        self.assertEqual((r["rows"], r["markets"], r["skipped_markets"], r["latest"]), (6, 2, 1, str(D_NEW)))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM market_prices WHERE source LIKE '%DEMO%'").fetchone()[0], 0)   # demo prices gone
        self.assertEqual(c.execute("SELECT COUNT(*) FROM markets WHERE id=99").fetchone()[0], 0)                          # demo-only market gone
        self.assertEqual(c.execute("SELECT district FROM markets WHERE name='Kimironko'").fetchone()[0], "Gasabo")
        e = c.execute("SELECT current_price_rwf,predicted_trend,change_pct FROM esoko_prices WHERE district='Musanze' AND crop='Maize'").fetchone()
        self.assertEqual((e[0], e[1], e[2]), (440, "rising", 10.0))                                                       # monthly data still gives a trend
        live_prices.refresh(c, opener); self.assertEqual(c.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0], 6)  # idempotent
    def test_failure_keeps_old_data(self):
        c = fresh(); bad = lambda u, *a, **k: (_ for _ in ()).throw(OSError("offline"))
        with self.assertRaises(OSError): live_prices.refresh(c, bad)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM market_prices WHERE source='DEMO DATA'").fetchone()[0], 1)
    def test_empty_crops_are_seeded_with_baseline_and_flagged(self):
        c = fresh(); c.execute("DELETE FROM market_prices"); c.execute("DELETE FROM crops"); c.commit(); r = live_prices.refresh(c, opener)
        self.assertTrue(r["seeded_crops"]); self.assertEqual(c.execute("SELECT COUNT(*) FROM crops").fetchone()[0], 6); self.assertEqual(r["rows"], 6)
        self.assertFalse(live_prices.refresh(c, opener)["seeded_crops"])                                                  # only the first time

def layer(name, f, vals): return {"name": name, "unit_measure": {"d_factor": f}, "depths": [{"label": l, "values": {"mean": v}} for l, v in zip(("0-5cm", "5-15cm", "15-30cm", "30-60cm"), vals)]}
PAYLOAD = {"properties": {"layers": [layer("phh2o", 10, [52, 54, 56, 70]), layer("nitrogen", 100, [200, 180, 160, 50]), layer("soc", 10, [300, 250, 200, 50]), layer("clay", 10, [450, 450, 450, 500]), layer("sand", 10, [250, 250, 250, 200])]}}

class Soil(unittest.TestCase):
    def test_parse_and_derive(self):
        v = soil_live.parse(PAYLOAD); self.assertAlmostEqual(v["phh2o"], 5.4); self.assertAlmostEqual(v["nitrogen"], 1.8); self.assertAlmostEqual(v["soc"], 25.0)   # 30-60 cm layer excluded
        p = soil_live.derive(v); self.assertEqual((p["ph"], p["soil_type"], p["deficit"]), (5.4, "Clay", "Soil acidity")); self.assertIn("lime", p["fert"].lower())
        with self.assertRaises(ValueError): soil_live.parse({"properties": {"layers": []}})
    def test_refresh_respects_official_and_freshness(self):
        c = fresh(); c.execute("INSERT INTO soil_profiles(district,soil_type,ph_level,source,is_demo) VALUES('Huye','Loam',6.1,'RAB 2025 survey',0)")
        c.execute("INSERT INTO soil_profiles(district,soil_type,ph_level,source,is_demo) VALUES('Musanze','Volcanic',5.8,'DEMO - indicative',1)"); c.commit(); urls = []
        def getter(u): urls.append(u); return PAYLOAD
        ok, fail = soil_live.refresh(c, getter, pause=0); self.assertEqual((len(ok), fail), (29, [])); self.assertNotIn("Huye", ok)       # official row untouched
        self.assertEqual(c.execute("SELECT source FROM soil_profiles WHERE district='Huye'").fetchone()[0], "RAB 2025 survey")
        m = c.execute("SELECT * FROM soil_profiles WHERE district='Musanze'").fetchone(); self.assertEqual((m["is_demo"], m["ph_level"], m["source"].startswith("ISRIC")), (0, 5.4, True))   # demo replaced
        self.assertTrue(all("rest.isric.org" in u and "phh2o" in u for u in urls)); n = len(urls)
        self.assertEqual(soil_live.refresh(c, getter, pause=0)[0], []); self.assertEqual(len(urls), n)                                      # fresh -> no new calls
        self.assertEqual(len(soil_live.refresh(c, getter, pause=0, force=True)[0]), 29)
    def test_provider_failure_is_reported_not_raised(self):
        c = fresh(); ok, fail = soil_live.refresh(c, lambda u: (_ for _ in ()).throw(OSError("offline")), pause=0); self.assertEqual((ok, len(fail)), ([], 4)); self.assertIn("stopped", fail[-1][1])   # gives up after 3 failures, no 6-minute wait
        self.assertEqual(c.execute("SELECT COUNT(*) FROM soil_profiles").fetchone()[0], 0)
    def test_official_csv_upload(self):
        c = fresh(); h = ",".join(loaders.KINDS["soil_profiles"]) + "\n"
        r = loaders.load("soil_profiles", h + "Huye,Clay loam,5.6,Phosphorus,DAP,RAB survey\nNowhere,Loam,6,N,Urea,RAB\nHuye,Loam,12,N,Urea,RAB\nHuye,Loam,6,N,Urea,DEMO\nHuye,Loam,6,N,Urea,\n", 1, c)
        self.assertEqual((r["ok"], r["bad"]), (1, 4)); self.assertEqual(c.execute("SELECT ph_level,is_demo FROM soil_profiles WHERE district='Huye'").fetchone()[:], (5.6, 0))

class Fusion(unittest.TestCase):
    wx = lambda s, prob, mm: [{"rain_prob": prob, "rain_mm": mm}, {"rain_prob": 10, "rain_mm": 0}]
    def test_texture(self): self.assertEqual((rules.texture(45, 25), rules.texture(10, 80), rules.texture(15, 40), rules.texture(None, 5)), ("Clay", "Sandy loam", "Loam", None))
    def test_rain_blocks_fertilizer_and_soil_adds_advice(self):
        p = {"district": "Musanze", "ph_level": 5.2, "sand_pct": 70, "main_nutrient_deficit": "Soil acidity", "recommended_fertilizer": "Lime + DAP"}
        t = rules.field_advice(p, self.wx(85, 12)); self.assertEqual(t[0][0], "warn"); self.assertIn("do not spread fertilizer", t[0][1]); self.assertIn("Sandy", t[0][1])
        self.assertTrue(any("acidic" in x[1] for x in t)); self.assertTrue(any("Lime + DAP" in x[1] for x in t))
        t = rules.field_advice(p, self.wx(10, 0)); self.assertEqual(t[0][0], "ok"); self.assertEqual(rules.field_advice(None, []), [])
    def test_needs_no_deficit(self): self.assertEqual(rules.soil_needs(6.2, 2.5, 25)[0], "No major deficit flagged")

class Sync(unittest.TestCase):
    def test_log_and_due_logic(self):
        c = fresh(); self.assertTrue(sync._due(c, "weather"))                                                   # never ran -> due
        c.execute("INSERT INTO sync_log(kind,status,detail) VALUES('weather','ok','x')"); self.assertFalse(sync._due(c, "weather"))   # ran just now
        c.execute("UPDATE sync_log SET created_at=datetime('now','-4 hours')"); self.assertTrue(sync._due(c, "weather"))             # older than 3 h
        c.execute("INSERT INTO sync_log(kind,status,detail) VALUES('prices','failed','offline')"); self.assertFalse(sync._due(c, "prices"))   # failed <30 min ago -> back off
        self.assertEqual(sync.start_background.__name__, "start_background")
    def test_background_can_be_disabled(self):
        os.environ["KIZA_AUTO_SYNC"] = "0"
        try: self.assertFalse(sync.start_background())
        finally: del os.environ["KIZA_AUTO_SYNC"]

class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global A; import app as A
    def login(self, ident):
        c = A.app.test_client(); tok = re.search(r'name="_csrf" value="(\w+)"', c.get("/login").data.decode()).group(1)
        assert c.post("/login", data={"ident": ident, "password": "Kiza@2026-demo", "_csrf": tok}).status_code == 302; c.get("/"); return c
    def test_soil_page_and_nav(self):
        f = self.login("farmer@kiza.rw"); r = f.get("/soil").data.decode(); self.assertIn("Volcanic", r); self.assertIn("DEMO", r); self.assertIn('href="/soil"', r)
        self.assertEqual(f.get("/soil?district=Nyagatare").status_code, 200); self.assertEqual(self.login("buyer@kiza.rw").get("/soil").status_code, 403)
    def test_fused_api(self):
        j = json.loads(self.login("farmer@kiza.rw").get("/api/live/musanze").data); self.assertEqual(j["district"], "Musanze")
        self.assertTrue(j["weather"] and j["soil"]["district"] == "Musanze" and j["advice"] and j["prices"]); self.assertEqual(self.login("farmer@kiza.rw").get("/api/live/Atlantis").status_code, 404)
        self.assertEqual(A.app.test_client().get("/api/live/Musanze").status_code, 302)
    def test_data_page_and_roles(self):
        g = self.login("gov@kiza.rw"); r = g.get("/admin/data").data.decode(); self.assertIn("Live data sync", r); self.assertIn("soil_profiles", r)
        self.assertEqual(g.get("/admin/template/soil_profiles.csv").status_code, 200); self.assertEqual(self.login("farmer@kiza.rw").get("/admin/data").status_code, 403)
    def test_farm_without_ph_uses_district_soil(self):
        f = self.login("farmer@kiza.rw"); tok = re.search(r'name="_csrf" value="(\w+)"', f.get("/farms").data.decode()).group(1)
        f.post("/farms", data={"name": "PH-test", "size_ha": "1", "district": "Musanze", "_csrf": tok})
        self.assertEqual(db.conn().execute("SELECT soil_ph FROM farms WHERE name='PH-test'").fetchone()[0], 5.8)

if __name__ == "__main__": unittest.main()
