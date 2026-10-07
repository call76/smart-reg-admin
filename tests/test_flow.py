"""End-to-end tests of the 7 core deliverables + security. Run: python -m unittest discover tests"""
import os, sys, re, io, tempfile, unittest, json, datetime as dt
os.environ["KIZA_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["KIZA_ML_URL"] = "http://127.0.0.1:59999"  # ML offline on purpose
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A
PW = "Kiza@2026-demo"
TOK = lambda c, page="/login": re.search(r'name="_csrf" value="(\w+)"', c.get(page).data.decode()).group(1)
def login(ident, pw=PW):
    c = A.app.test_client(); r = c.post("/login", data={"ident": ident, "password": pw, "_csrf": TOK(c)}); assert r.status_code == 302, (ident, r.status_code); c.get("/"); return c
def db(sql, a=()): return A.db.conn().execute(sql, a).fetchall()

class Core(unittest.TestCase):
    def test_register_and_login_by_phone(self):
        c = A.app.test_client(); d = {"role": "farmer", "name": "Test Farmer", "phone": "+250 788 123 456", "district": "Huye", "sector": "Tumba", "password": "abcdef12", "password2": "abcdef12", "lang": "rw"}
        for bad, key in ((dict(d, phone="12345"), "phone"), (dict(d, password="short1", password2="short1"), "pw"), (dict(d, password="abcdefgh", password2="abcdefgh"), "pw"), (dict(d, password2="zzzzzz12"), "pw"), (dict(d, sector=""), "sector")):
            self.assertEqual(c.post("/register", data=dict(bad, _csrf=TOK(c, "/register"))).status_code, 200, key)
        self.assertEqual(c.post("/register", data=dict(d, _csrf=TOK(c, "/register"))).status_code, 302); self.assertEqual(c.get("/farmer").status_code, 200)
        self.assertEqual(db("SELECT phone,role,lang FROM users WHERE name='Test Farmer'")[0][:], ("0788123456", "farmer", "rw"))
        c2 = A.app.test_client(); self.assertEqual(c2.post("/register", data=dict(d, name="Dup", _csrf=TOK(c2, "/register"))).status_code, 200)  # duplicate phone rejected
        login("0788123456", "abcdef12"); login("farmer@kiza.rw"); login("0788000001")  # phone login works for demo farmer too
        bad = A.app.test_client(); self.assertEqual(bad.post("/login", data={"ident": "0788123456", "password": "nope", "_csrf": TOK(bad)}).status_code, 200)

    def test_luc_guardrail_nkunganire_and_imihigo(self):
        f = login("farmer@kiza.rw"); fid = db("SELECT f.id FROM farms f JOIN users u ON u.id=f.user_id WHERE u.email='farmer@kiza.rw'")[0][0]
        G = lambda: next(d for d in json.loads(login("gov@kiza.rw").get("/api/imihigo").data) if d["name"] == "Musanze")
        maize0 = next(r["actual"] for r in G()["rows"] if r["crop"] == "Maize")
        post = lambda crop, **x: f.post(f"/farms/{fid}/fields", data=dict({"name": "T-" + crop, "size_ha": "1", "crop": crop, "status": "Planted", "_csrf": TOK(f, f"/farms/{fid}")}, **x))
        post("Potato"); self.assertEqual(db("SELECT COUNT(*) FROM fields WHERE name='T-Potato'")[0][0], 0)            # blocked: sector priority crop is Maize
        post("Potato", confirm="on"); self.assertEqual(db("SELECT luc_status FROM fields WHERE name='T-Potato'")[0][0], "not_aligned")  # allowed after agronomist confirmation
        post("Maize"); self.assertEqual(db("SELECT luc_status FROM fields WHERE name='T-Maize'")[0][0], "aligned")
        page = f.get(f"/advisor?farm={fid}").data.decode(); self.assertIn("priority crop", page); self.assertIn("Maize", page)
        maize1 = next(r["actual"] for r in G()["rows"] if r["crop"] == "Maize"); self.assertEqual(round(maize1 - maize0, 1), 1.0)   # Imihigo tracker reacts to new planting
        r = f.post("/fertilizer", data={"crop": "Maize", "ha": "2", "_csrf": TOK(f, "/fertilizer")}).data.decode()
        for n in ("400,000", "270,000", "130,000"): self.assertIn(n, r)                                           # open market vs subsidized vs saving
        self.assertIn("pending", r)

    def test_distress_registry(self):
        f = login("0788000001"); g = login("gov@kiza.rw"); n0 = db("SELECT COUNT(*) FROM distress_reports")[0][0]
        f.post("/distress", data={"kind": "Price manipulation by middlemen", "crop": "Beans", "qty_kg": "900", "details": "buyers offering half price", "_csrf": TOK(f, "/distress")})
        self.assertEqual(db("SELECT COUNT(*) FROM distress_reports")[0][0], n0 + 1); self.assertIn("buyers offering half price", g.get("/distress/inbox").data.decode())
        rid = db("SELECT MAX(id) FROM distress_reports")[0][0]; g.post("/distress/inbox", data={"id": rid, "status": "Resolved", "note": "Cooperative contacted", "_csrf": TOK(g, "/distress/inbox")})
        self.assertIn("Cooperative contacted", f.get("/distress").data.decode()); self.assertIn("Resolved", f.get("/notifications").data.decode())

    def test_marketplace_flow(self):
        f = login("farmer@kiza.rw"); b = login("buyer@kiza.rw")
        f.post("/market/sell", data={"crop": "Potato", "qty_kg": "1000", "price_kg": "300", "_csrf": TOK(f, "/market")}); lid = db("SELECT MAX(id) FROM listings")[0][0]
        b.post(f"/market/{lid}/offer", data={"qty_kg": "400", "price_kg": "320", "_csrf": TOK(b, "/market")}); oid = db("SELECT MAX(id) FROM offers")[0][0]
        f.post(f"/offers/{oid}/accept", data={"_csrf": TOK(f, "/offers")}); self.assertEqual(tuple(db("SELECT status, available_kg FROM listings WHERE id=?", (lid,))[0]), ("Reserved", 600))
        self.assertIn("ACCEPTED", b.get("/notifications").data.decode()); self.assertIn("0788000001", b.get("/offers").data.decode())   # farmer's phone shown to buyer only after acceptance

    def test_esoko_excel_upload_and_ml_offline(self):
        import openpyxl
        g = login("gov@kiza.rw"); wb = openpyxl.Workbook(); ws = wb.active; ws.append(["market", "crop", "day", "price_kg", "source"]); today = dt.date.today()
        ws.append(["Musanze Market", "Maize", today - dt.timedelta(7), 500, "Test sheet"]); ws.append(["Musanze Market", "Maize", today, 560, "Test sheet"]); ws.append(["Nowhere Market", "Maize", today, 560, "Test sheet"])
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        r = g.post("/admin/data", data={"kind": "market_prices", "file": (buf, "prices.xlsx"), "_csrf": TOK(g, "/admin/data")}, content_type="multipart/form-data", follow_redirects=True).data.decode()
        self.assertIn("2 rows loaded, 1 rejected", r); e = db("SELECT current_price_rwf, predicted_trend FROM esoko_prices WHERE district='Musanze' AND crop='Maize'")[0]; self.assertEqual(e[0], 560)
        self.assertIn("Forecast unavailable", g.get("/prices?crop=Maize").data.decode())   # Python service offline -> page still works

    def test_security_and_roles(self):
        f = login("farmer@kiza.rw"); b = login("buyer@kiza.rw")
        for p in ("/gov", "/admin/data", "/audit", "/imihigo", "/distress/inbox"): self.assertEqual(f.get(p).status_code, 403, p)
        self.assertEqual(b.get("/farms").status_code, 403); self.assertEqual(A.app.test_client().get("/farmer").status_code, 302)
        self.assertEqual(f.post("/farms", data={"name": "evil"}).status_code, 302); self.assertEqual(db("SELECT COUNT(*) FROM farms WHERE name='evil'")[0][0], 0)   # CSRF enforced
        other = db("SELECT id FROM farms WHERE user_id!=(SELECT id FROM users WHERE email='farmer@kiza.rw') LIMIT 1")[0][0]; self.assertEqual(f.get(f"/farms/{other}").status_code, 403)
        o = login("officer@kiza.rw"); self.assertEqual(o.get("/distress/inbox").status_code, 200); o.post("/rules", data={"id": "1", "source": "MINAGRI notice", "_csrf": TOK(o, "/rules")})
        self.assertEqual(db("SELECT approval FROM fertilizer_subsidies WHERE id=1")[0][0], "approved")
        for p in ("/", "/login", "/register"): self.assertEqual(A.app.test_client().get(p).status_code, 200)
        self.assertIn("Injira", A.app.test_client().get("/lang/rw", headers={"Referer": "/login"}, follow_redirects=True).data.decode())  # Kinyarwanda UI

if __name__ == "__main__": unittest.main()
