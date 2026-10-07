"""Own process, CLEAN real-mode database: empty-data pages must not crash, loaders must validate, LUC works on loaded data, weather parser works."""
import os, sys, re, subprocess, tempfile
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, root)
os.environ["KIZA_DB"] = os.path.join(tempfile.mkdtemp(), "real.db"); os.environ["KIZA_ML_URL"] = "http://127.0.0.1:59999"
out = subprocess.run([sys.executable, os.path.join(root, "db.py"), "--real"], capture_output=True, text=True, env=os.environ).stdout
pw = {l.split()[0]: l.split()[2] for l in out.splitlines() if "@kiza.rw" in l}; assert set(pw) == {"admin", "government", "extension"}, out
import app as A, loaders, weather_live
A.app.app_context().push()
tok = lambda c, p: re.search(r'name="_csrf" value="(\w+)"', c.get(p).data.decode()).group(1)
def sess(email, p):
    c = A.app.test_client(); assert c.post("/login", data={"ident": email, "password": p, "_csrf": tok(c, "/login")}).status_code == 302; c.get("/"); return c
assert A.Q1("SELECT COUNT(*) c FROM districts")["c"] == 30 and A.Q1("SELECT COUNT(*) c FROM crops")["c"] == 0 and A.Q1("SELECT COUNT(*) c FROM users WHERE role='farmer'")["c"] == 0
for em, role, pages in (("admin@kiza.rw", "admin", ["/gov", "/imihigo", "/api/imihigo", "/distress/inbox", "/admin/data", "/audit", "/prices", "/market", "/weather", "/rules", "/fertilizer", "/soil", "/api/live/Musanze"]), ("gov@kiza.rw", "government", ["/gov", "/imihigo"])):
    c = sess(em, pw[role])
    for p in pages: assert c.get(p).status_code == 200, (role, p)
f = A.app.test_client(); assert f.post("/register", data={"role": "farmer", "name": "Real Farmer", "phone": "0722123456", "district": "Musanze", "sector": "Muhoza", "password": "longpass1", "password2": "longpass1", "_csrf": tok(f, "/register")}).status_code == 302; f.get("/")
assert f.post("/farms", data={"name": "F", "size_ha": "1", "district": "Musanze", "_csrf": tok(f, "/farms")}).status_code == 302
fid = A.Q1("SELECT id FROM farms")["id"]
for p in ["/farmer", "/farms", f"/farms/{fid}", "/advisor", "/weather", "/fertilizer", "/distress", "/market", "/prices", "/soil"]: assert f.get(p).status_code == 200, p
C = lambda k, rows: loaders.load(k, ",".join(loaders.KINDS[k]) + "\n" + "\n".join(rows) + "\n", 1)
assert C("districts", ["Musanze,,-1.5,29.6", "Atlantis,,1,29", "Huye,,50,29"])["bad"] == 2
assert C("crops", ["Maize,3,400,650000,18,30,15,60,5.5,7,\"A,B\",Medium,0.3,120", "Potato,15,350,1500000,10,22,20,60,5,6.5,\"A,B,C\",High,0.5,100", "Bad,3,400,650000,30,18,15,60,5.5,7,A,Medium,0.3,120"])["ok"] == 2
assert C("markets", ["Musanze Market,Musanze,-1.5,29.6", "X,Nowhere,0,0"])["bad"] == 1
days = [str(__import__("datetime").date.today() - __import__("datetime").timedelta(7 * k)) for k in (3, 2, 1, 0)]
r = C("market_prices", [f"Musanze Market,Maize,{d},{400+i*20},TEST bulletin" for i, d in enumerate(days)] + [f"Musanze Market,Maize,{days[0]},400,DEMO", "Musanze Market,Maize,2999-01-01,400,TEST", "Musanze Market,Maize,2026-09-30,-4,TEST", "Musanze Market,Beans,2026-09-30,400,TEST"]); assert (r["ok"], r["bad"]) == (4, 4), r
e = A.Q1("SELECT * FROM esoko_prices WHERE district='Musanze' AND crop='Maize'"); assert e["current_price_rwf"] == 460 and e["predicted_trend"] == "rising", e
assert C("fertilizer_subsidies", ["Maize,planting,DAP,100,1100,750,TEST notice,pending", "Maize,planting,DAP,110,1100,750,TEST notice,approved", "Maize,top_dressing,Urea,100,900,950,TEST,pending", "Maize,top_dressing,Urea,100,900,600,,pending"])["bad"] == 2
assert A.Q1("SELECT kg_per_ha,approval FROM fertilizer_subsidies WHERE crop='Maize'") == {"kg_per_ha": 110.0, "approval": "approved"}
assert C("luc_zones", ["Musanze,Muhoza,Maize", "Musanze,Muhoza,Potato", "Nowhere,X,Maize", "Musanze,Y,Banana"])["bad"] == 2 and A.Q1("SELECT mandated_crop m FROM luc_zones")["m"] == "Potato"
assert C("imihigo_targets", ["Musanze,Maize,100", "Musanze,Potato,-5"])["bad"] == 1
# real data now drives the pages; LUC guardrail enforced on loaded zone (priority = Potato)
f.post(f"/farms/{fid}/fields", data={"name": "A", "size_ha": "1", "crop": "Maize", "status": "Planted", "_csrf": tok(f, f"/farms/{fid}")}); assert A.Q1("SELECT COUNT(*) c FROM fields")["c"] == 0
f.post(f"/farms/{fid}/fields", data={"name": "B", "size_ha": "1", "crop": "Potato", "status": "Planted", "_csrf": tok(f, f"/farms/{fid}")}); assert A.Q1("SELECT luc_status s FROM fields")["s"] == "aligned"
for p in ["/advisor", "/prices?crop=Maize", "/fertilizer"]: assert f.get(p).status_code == 200, p
g = sess("gov@kiza.rw", pw["government"]); assert "Musanze" in g.get("/imihigo").data.decode() and "DEMO DATA" not in g.get("/gov").data.decode().split("<footer>")[0]
daily = {"time": ["2026-10-%02d" % d for d in range(1, 11)], "temperature_2m_max": [24.0] * 10, "temperature_2m_min": [12.0] * 10, "precipitation_sum": [2.0] * 7 + [0.0, 10.0, None], "precipitation_probability_max": [None] * 7 + [80, 20, None], "wind_speed_10m_max": [12.5] * 10}
rows = weather_live.parse({"daily": daily}); assert len(rows) == 3 and rows[0]["rain_7d"] == 14.0 and rows[0]["temp"] == 18.0 and rows[0]["rain_prob"] == 80, rows
fake = lambda url: {"results": [{"latitude": -1.5, "longitude": 29.6, "country_code": "RW"}]} if "geocoding" in url else {"daily": daily}
cn = A.db.conn(); ok, fail = weather_live.refresh(cn, fake); assert len(ok) == 30 and not fail
ok, fail = weather_live.refresh(cn, lambda u: (_ for _ in ()).throw(OSError("offline"))); assert len(fail) == 30 and cn.execute("SELECT COUNT(*) FROM weather").fetchone()[0] == 90
assert "LIVE" in f.get("/weather").data.decode()
print("REAL MODE OK")
