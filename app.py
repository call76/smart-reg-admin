"""
KIZA-AGRI - Rwanda smart agriculture platform (Flask + SQLite trial). Grow Smarter. Sell Better. Plan Together.
"""
import os, re, json, time, secrets, datetime as dt, logging
from functools import wraps
from flask import Flask, Response, render_template, request, redirect, session, flash, abort, jsonify, g
from markupsafe import Markup
from werkzeug.security import generate_password_hash, check_password_hash
import statistics
import db, rules, mlclient, loaders, weather_live, esoko, sync

app = Flask(__name__)

def _secret():  # stable key so sessions/forms survive restarts
    if os.environ.get("KIZA_SECRET"): return os.environ["KIZA_SECRET"]
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
    if not os.path.exists(p): open(p, "w").write(secrets.token_hex(32))
    return open(p).read().strip()

app.secret_key = _secret()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=5 * 1024 * 1024
)
logging.basicConfig(filename=os.path.join(os.path.dirname(os.path.abspath(__file__)), "kiza.log"), level=logging.ERROR)
db.init()

HOME = {"farmer": "/farmer", "buyer": "/market", "extension": "/distress/inbox", "government": "/gov", "admin": "/gov"}
STAFF = ("extension", "government", "admin")
TODAY = lambda: str(dt.date.today())

# ---------- helpers ----------
def _c():
    if "db" not in g: g.db = db.conn()
    return g.db

def Q(sql, a=()): return [dict(r) for r in _c().execute(sql, a).fetchall()]
def Q1(sql, a=()): r = _c().execute(sql, a).fetchone(); return dict(r) if r else None
def X(sql, a=()): cur = _c().execute(sql, a); _c().commit(); return cur.lastrowid

@app.teardown_appcontext
def _close(e):
    if "db" in g: g.db.close()

def audit(action, detail=""): 
    X("INSERT INTO audit_logs(user_id,action,detail,ip) VALUES(?,?,?,?)", (session.get("uid"), action, detail, request.remote_addr))

def notify(uid, kind, msg, link="/notifications"): 
    X("INSERT INTO notifications(user_id,kind,message,link) VALUES(?,?,?,?)", (uid, kind, msg, link))

def me(): return Q1("SELECT * FROM users WHERE id=?", (session["uid"],)) if session.get("uid") else None

def need(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = me()
            if not u or u["status"] != "active": session.clear(); return redirect("/login")
            if roles and u["role"] not in roles: abort(403)
            g.user = u; return f(*a, **k)
        return w
    return deco

def num(name, lo=None, hi=None, default=None, opt=False):
    try:
        v = float(request.form.get(name, ""))
        assert (lo is None or v >= lo) and (hi is None or v <= hi)
        return v
    except Exception:
        if opt: return None
        if default is not None: return default
        raise ValueError(name)

# ---------- security + i18n ----------
@app.before_request
def csrf():
    if "csrf" not in session: session["csrf"] = secrets.token_hex(16)
    if request.method == "POST" and request.form.get("_csrf") != session["csrf"]:
        flash("Your page expired. Please try again.", "err")
        return redirect(request.path if request.path in ("/login", "/register") else (request.referrer or "/login"))

app.jinja_env.globals["csrf_input"] = lambda: Markup('<input type="hidden" name="_csrf" value="%s">' % session.get("csrf", ""))

RW = {
    "Today": "Uyu munsi", "My Farms": "Imirima yanjye", "Crop Advisor": "Inama ku bihingwa", "Weather": "Ikirere", 
    "Fertilizer": "Ifumbire (Nkunganire)", "Report a problem": "Menyesha ikibazo", "Marketplace": "Isoko", 
    "Offers": "Ibyifuzo", "Prices": "Ibiciro", "Government": "Leta", "Login": "Injira", "Register": "Iyandikishe",
    "Logout": "Sohoka", "Get started": "Tangira", "Phone number": "Numero ya telefone", "Email (optional)": "Imeri (si ngombwa)", 
    "Password": "Ijambo ry'ibanga", "Confirm password": "Subiramo ijambo ry'ibanga", "Full name": "Amazina yombi", 
    "District": "Akarere", "Sector": "Umurenge", "Create account": "Fungura konti", "I am a": "Ndi", 
    "Farmer": "Umuhinzi", "Buyer / Trader": "Umuguzi / Umucuruzi", "Phone or email": "Telefone cyangwa imeri", 
    "What to do now": "Icyo gukora nonaha", "Welcome back": "Murakaza neza", "Show": "Erekana", "Hide": "Hisha", 
    "My fields": "Imirima yanjye", "Sell my harvest": "Gurisha umusaruro", "Report a problem to the government": "Menyesha Leta ikibazo", 
    "Send report": "Ohereza", "Calculate": "Kubara", "Analyse": "Isesengura", "Language": "Ururimi", 
    "Create your account": "Fungura konti yawe", "Already have an account?": "Usanzwe ufite konti?", "New here?": "Uri mushya hano?", 
    "Save": "Bika", "Open market price": "Igiciro ku isoko risanzwe", "Subsidized price": "Igiciro cya Nkunganire", 
    "You save": "Uzigama", "Command Center": "Ikigo cy'ubugenzuzi", "Data": "Amakuru", "Soil": "Ubutaka"
}

def tr(k): return RW.get(k, k) if session.get("lang") == "rw" else k

@app.route("/lang/<code>")
def lang(code):
    session["lang"] = "rw" if code == "rw" else "en"
    if session.get("uid"): X("UPDATE users SET lang=? WHERE id=?", (session["lang"], session["uid"]))
    return redirect(request.referrer or "/")

@app.context_processor
def ctx():
    u = me()
    n = Q1("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0", (u["id"],))["c"] if u else 0
    demo = bool(Q1("SELECT 1 x FROM market_prices WHERE source LIKE '%DEMO%' LIMIT 1") or 
                Q1("SELECT 1 x FROM weather WHERE is_demo=1 LIMIT 1") or 
                Q1("SELECT 1 x FROM soil_profiles WHERE is_demo=1 LIMIT 1"))
    return {"is_demo": demo, "user": u, "unread": n, "today": TODAY(), "t": tr, "lang": session.get("lang", "en"), "homes": HOME}

@app.errorhandler(403)
def e403(e): return render_template("msg.html", title="No access", msg="Your role cannot open this page."), 403

@app.errorhandler(404)
def e404(e): return render_template("msg.html", title="Not found", msg="This page does not exist."), 404

@app.errorhandler(Exception)
def e500(e):
    if hasattr(e, "code"): return e
    logging.exception(e)
    return render_template("msg.html", title="Something went wrong", msg="We logged the problem. Please try again."), 500

# ---------- public + auth ----------
@app.get("/")
def index(): 
    return render_template("landing.html", s=Q1("SELECT (SELECT COUNT(*) FROM users WHERE role='farmer') f,(SELECT COUNT(*) FROM farms) fa,(SELECT COALESCE(SUM(size_ha),0) FROM fields WHERE status NOT IN ('Planned','Prepared','Failed')) ha,(SELECT COUNT(*) FROM listings WHERE status IN ('Available','Offer Received','Reserved')) l"))

def norm_phone(p):
    d = re.sub(r"\D", "", p or "")
    if d.startswith("250"): d = "0" + d[3:]
    if len(d) == 9 and d[0] == "7": d = "0" + d
    return d if re.fullmatch(r"07[2389]\d{7}", d) else None

FAILS = {}

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr
        FAILS[ip] = [t for t in FAILS.get(ip, []) if time.time() - t < 600]
        ident = request.form.get("ident", "").strip()
        if len(FAILS[ip]) >= 8:
            flash("Too many attempts. Please wait 10 minutes.", "err")
        else:
            u = Q1("SELECT * FROM users WHERE email=?", (ident.lower(),)) if "@" in ident else (Q1("SELECT * FROM users WHERE phone=?", (norm_phone(ident),)) if norm_phone(ident) else None)
            if u and u["status"] == "active" and check_password_hash(u["pw_hash"], request.form.get("password", "")):
                lang_ = session.get("lang") or u["lang"]
                session.clear()
                session["uid"] = u["id"]
                session["lang"] = lang_
                audit("login", u["email"])
                return redirect(HOME[u["role"]])
            FAILS[ip].append(time.time())
            flash("Wrong phone/email or password.", "err")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    ds = Q("SELECT name FROM districts ORDER BY name")
    if request.method == "POST":
        f = request.form
        role = f.get("role")
        phone = norm_phone(f.get("phone"))
        email = f.get("email", "").strip().lower()
        pw = f.get("password", "")
        err = None
        if role not in ("farmer", "buyer"): err = "Choose Farmer or Buyer."
        elif not f.get("name", "").strip(): err = "Please enter your full name."
        elif not phone: err = "Enter a valid Rwandan phone number (e.g. 0788 123 456)."
        elif email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): err = "That email does not look right."
        elif f.get("district") not in [d["name"] for d in ds]: err = "Choose your district."
        elif role == "farmer" and not f.get("sector", "").strip(): err = "Enter your sector (umurenge)."
        elif len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw): err = "Password needs 8+ characters with letters and numbers."
        elif pw != f.get("password2"): err = "The two passwords do not match."
        elif Q1("SELECT id FROM users WHERE phone=?", (phone,)): err = "This phone number is already registered. Try logging in."
        elif email and Q1("SELECT id FROM users WHERE email=?", (email,)): err = "This email is already registered."
        
        if err:
            flash(err, "err")
        else:
            uid = X("INSERT INTO users(name,email,phone,pw_hash,role,district,sector,lang) VALUES(?,?,?,?,?,?,?,?)",
                    (f["name"].strip()[:80], email or f"p{phone}@phone.kiza", phone, generate_password_hash(pw), role, f["district"], f.get("sector", "").strip()[:60], f.get("lang", "en")))
            lg = f.get("lang", "en")
            session.clear()
            session["uid"] = uid
            session["lang"] = lg
            audit("register", phone)
            return redirect(HOME[role])
    return render_template("register.html", ds=ds, f=request.form)

@app.post("/logout")
def logout(): session.clear(); return redirect("/")

# ---------- farmer: passport, LUC, advisor ----------
def own_farm(fid):
    f = Q1("SELECT * FROM farms WHERE id=?", (fid,))
    if not f or f["user_id"] != g.user["id"]: abort(403)
    return f

def wx3(d): return Q("SELECT * FROM weather WHERE district=? ORDER BY day LIMIT 3", (d,))

def avg_prices(): return {r["crop"]: r["p"] for r in Q("SELECT crop, AVG(price_kg) p FROM market_prices m WHERE day=(SELECT MAX(day) FROM market_prices WHERE crop=m.crop) GROUP BY crop")}

def luc(district, sector, crop):
    z = Q1("SELECT mandated_crop FROM luc_zones WHERE district=? AND lower(sector)=lower(?)", (district, (sector or "").strip()))
    return ("unknown", None) if not z else (("aligned" if z["mandated_crop"] == crop else "not_aligned"), z["mandated_crop"])

@app.get("/farmer")
@need("farmer")
def farmer():
    u = g.user
    farms = Q("SELECT * FROM farms WHERE user_id=?", (u["id"],))
    fields = Q("SELECT f.*, fa.name farm FROM fields f JOIN farms fa ON fa.id=f.farm_id WHERE fa.user_id=?", (u["id"],))
    wx = wx3(u["district"])
    acts = [(k, t, "/weather") for k, t in rules.weather_advice(wx)] if wx else []
    
    for f in fields:
        if f["luc_status"] == "not_aligned":
            acts.append(("warn", f"{f['crop']} in {f['name']} is not your sector's LUC priority crop - confirm with your agronomist.", "/farms"))
        if f["status"] in ("Growing", "Flowering", "Planted") and f["expected_harvest"]:
            d = (dt.date.fromisoformat(f["expected_harvest"]) - dt.date.today()).days
            if d <= 14: acts.append(("info", f"Harvest reminder: {f['crop']} in {f['name']} ready in about {max(d,0)} days.", "/market"))
            
    for k, t in rules.soil_advice(Q1("SELECT * FROM soil_profiles WHERE district=?", (u["district"],)))[:2]:
        if k != "ok": acts.append((k, t, "/soil"))
        
    pend = Q1("SELECT COUNT(*) c FROM offers o JOIN listings l ON l.id=o.listing_id WHERE l.seller_id=? AND o.status='Pending'", (u["id"],))["c"]
    if pend: acts.insert(0, ("ok", f"{pend} buyer offer(s) waiting for your answer.", "/offers"))
    
    for r in Q("SELECT crop, predicted_trend, change_pct FROM esoko_prices WHERE district=?", (u["district"],)):
        if r["crop"] in {f["crop"] for f in fields} and r["predicted_trend"] in ("rising", "falling"):
            acts.append(("ok" if r["predicted_trend"] == "rising" else "warn", f"{r['crop']} price is {r['predicted_trend']} ({r['change_pct']:+.1f}% recent change) in {u['district']}.", "/prices?crop=" + r["crop"]))
            
    return render_template("farmer.html", farms=farms, fields=fields, wx=wx, acts=acts[:8], notes=Q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 4", (u["id"],)))

@app.route("/farms", methods=["GET", "POST"])
@need("farmer")
def farms():
    ds = Q("SELECT * FROM districts ORDER BY name")
    if request.method == "POST":
        try:
            d = request.form.get("district")
            dd = next(x for x in ds if x["name"] == d)
            sp = num("soil_ph", 3, 9, opt=True)
            if sp is None: 
                pr = Q1("SELECT ph_level FROM soil_profiles WHERE district=?", (d,))
                sp = pr["ph_level"] if pr else None
            fid = X("INSERT INTO farms(user_id,name,size_ha,lat,lng,district,sector,soil_type,soil_ph,irrigation) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (g.user["id"], request.form["name"].strip()[:80] or "My farm", num("size_ha", .01, 10000), num("lat", -3, 0, opt=True) or dd["lat"], num("lng", 28, 31, opt=True) or dd["lng"], d,
                     request.form.get("sector", "").strip()[:60] or g.user["sector"], request.form.get("soil_type", "")[:40], sp, 1 if request.form.get("irrigation") else 0))
            audit("farm.create", str(fid))
            flash("Farm registered. Now add a field.", "ok")
            return redirect(f"/farms/{fid}")
        except Exception: 
            flash("Please check the farm details (name, size above 0, district).", "err")
    return render_template("farms.html", farms=Q("SELECT * FROM farms WHERE user_id=?", (g.user["id"],)), ds=ds)

@app.get("/farms/<int:fid>")
@need("farmer")
def farm(fid):
    f = own_farm(fid)
    z = Q1("SELECT mandated_crop FROM luc_zones WHERE district=? AND lower(sector)=lower(?)", (f["district"], f["sector"] or ""))
    return render_template("farm.html", f=f, fields=Q("SELECT * FROM fields WHERE farm_id=?", (fid,)), crops=Q("SELECT name FROM crops ORDER BY name"), season=rules.season(), lucz=z)

@app.post("/farms/<int:fid>/fields")
@need("farmer")
def add_field(fid):
    f = own_farm(fid)
    try:
        crop = Q1("SELECT * FROM crops WHERE name=?", (request.form.get("crop"),))
        assert crop
        ha = num("size_ha", .01, 10000)
        pd = request.form.get("planting_date") or TODAY()
        status = request.form.get("status", "Planned")
        assert status in ("Planned", "Prepared", "Planted", "Growing")
        ls, mand = luc(f["district"], f["sector"], crop["name"])
        if ls == "not_aligned" and not request.form.get("confirm"):
            flash(f"LUC guardrail: the priority crop for {f['sector']} sector is {mand}, not {crop['name']}. Check with your sector agronomist, then tick the confirmation box to continue.", "err")
            return redirect(f"/farms/{fid}")
        X("INSERT INTO fields(farm_id,name,size_ha,crop,season,planting_date,expected_harvest,luc_status,status) VALUES(?,?,?,?,?,?,?,?,?)",
          (fid, request.form["name"].strip()[:60] or "Field", ha, crop["name"], rules.season(), pd, str(dt.date.fromisoformat(pd) + dt.timedelta(crop["cycle_days"])), ls, status))
        audit("field.create", f"farm {fid} {crop['name']} luc={ls}")
        flash("Field created." + (" LUC: aligned with your sector's priority crop." if ls == "aligned" else ""), "ok")
    except Exception:
        flash("Please check the field details.", "err")
    return redirect(f"/farms/{fid}")

@app.post("/fields/<int:fid>/status")
@need("farmer")
def field_status(fid):
    fl = Q1("SELECT f.*, fa.user_id FROM fields f JOIN farms fa ON fa.id=f.farm_id WHERE f.id=?", (fid,))
    if not fl or fl["user_id"] != g.user["id"]: abort(403)
    s = request.form.get("status")
    if s in ("Planned", "Prepared", "Planted", "Growing", "Flowering", "Harvesting", "Harvested", "Failed"):
        X("UPDATE fields SET status=? WHERE id=?", (s, fid))
        audit("field.status", f"{fid}->{s}")
    return redirect(f"/farms/{fl['farm_id']}")

@app.get("/advisor")
@need("farmer")
def advisor():
    fs = Q("SELECT * FROM farms WHERE user_id=?", (g.user["id"],))
    if not fs: flash("Register a farm first.", "err"); return redirect("/farms")
    farm = next((f for f in fs if str(f["id"]) == request.args.get("farm")), fs[0])
    wx = wx3(farm["district"])
    crops = Q("SELECT * FROM crops")
    if not crops: return render_template("msg.html", title="Crop data not loaded yet", msg="An administrator must load the crops dataset first (Data page).")
    res = rules.advise(crops, farm, wx[0] if wx else None, avg_prices())
    for r in res: r["luc"], r["luc_crop"] = luc(farm["district"], farm["sector"], r["crop"])
    mand = next((r["luc_crop"] for r in res if r["luc_crop"]), None)
    return render_template("advisor.html", farms=fs, farm=farm, res=res, top=res[0], mand=mand, wx=wx[0] if wx else None, season=rules.season())

@app.get("/weather")
@need("farmer", "extension", "government", "admin")
def weather():
    d = request.args.get("district") or g.user["district"]
    wx = wx3(d)
    live = bool(wx) and not wx[0]["is_demo"]
    stale = live and (wx[0]["fetched_at"] or "")[:10] < str(dt.date.today() - dt.timedelta(1))
    return render_template("weather.html", live=live, stale=stale, wx=wx, tips=rules.weather_advice(wx) if wx else [], ds=Q("SELECT name FROM districts ORDER BY name"), d=d)

@app.route("/fertilizer", methods=["GET", "POST"])
@need("farmer", "extension", "government", "admin")
def fertilizer():
    out = None
    crops = Q("SELECT name FROM crops ORDER BY name")
    fields = Q("SELECT f.*, fa.name farm FROM fields f JOIN farms fa ON fa.id=f.farm_id WHERE fa.user_id=?", (g.user["id"],)) if g.user["role"] == "farmer" else []
    if request.method == "POST":
        try:
            crop = request.form["crop"]
            ha = num("ha", .01, 10000)
            rs = Q("SELECT * FROM fertilizer_subsidies WHERE crop=? ORDER BY stage", (crop,))
            rows, warn = rules.fert_calc(rs, ha)
            tot = lambda k: sum(r[k] for r in rows if r[k] is not None)
            out = {"rows": rows, "warn": warn, "crop": crop, "ha": ha, "market": tot("market_cost"), "subsidy": tot("subsidy_cost"), "saving": tot("saving"), "kg": round(tot("kg"), 1)}
            if not rs: flash("No fertilizer data is loaded for this crop yet.", "err")
        except Exception:
            flash("Please choose a crop and a field size above 0.", "err")
    return render_template("fertilizer.html", out=out, crops=crops, fields=fields)

# ---------- distress registry ----------
KINDS_D = ["Glut / no buyers", "Price manipulation by middlemen", "Spoilage / storage problem", "Other"]

@app.route("/distress", methods=["GET", "POST"])
@need("farmer")
def distress():
    if request.method == "POST":
        try:
            kind = request.form["kind"]
            assert kind in KINDS_D
            crop = request.form["crop"]
            assert Q1("SELECT 1 x FROM crops WHERE name=?", (crop,))
            rid = X("INSERT INTO distress_reports(farmer_id,district,crop,kind,qty_kg,details) VALUES(?,?,?,?,?,?)",
                    (g.user["id"], g.user["district"], crop, kind, num("qty_kg", 1, 1e8), request.form.get("details", "")[:600]))
            for s in Q("SELECT id FROM users WHERE role IN ('extension','government','admin')"):
                notify(s["id"], "distress", f"New distress report: {kind} - {crop} in {g.user['district']}.", "/distress/inbox")
            audit("distress.create", str(rid))
            flash("Report sent to the government team. Thank you.", "ok")
        except Exception:
            flash("Please choose a crop, a problem type and enter the quantity in kg.", "err")
        return redirect("/distress")
    return render_template("distress.html", kinds=KINDS_D, crops=Q("SELECT name FROM crops ORDER BY name"), mine=Q("SELECT * FROM distress_reports WHERE farmer_id=? ORDER BY id DESC", (g.user["id"],)))

@app.route("/distress/inbox", methods=["GET", "POST"])
@need(*STAFF)
def distress_inbox():
    if request.method == "POST":
        r = Q1("SELECT * FROM distress_reports WHERE id=?", (request.form.get("id"),))
        st = request.form.get("status")
        if r and st in ("New", "Reviewing", "Resolved"):
            X("UPDATE distress_reports SET status=?, officer_note=? WHERE id=?", (st, request.form.get("note", "")[:300], r["id"]))
            audit("distress.update", f"{r['id']}->{st}")
            if st != "New": notify(r["farmer_id"], "distress", f"Your {r['crop']} report is now: {st}.", "/distress")
        return redirect("/distress/inbox")
    rows = Q("SELECT d.*, u.name farmer, u.phone FROM distress_reports d JOIN users u ON u.id=d.farmer_id ORDER BY CASE d.status WHEN 'New' THEN 0 WHEN 'Reviewing' THEN 1 ELSE 2 END, d.id DESC")
    return render_template("distress_inbox.html", rows=rows)

# ---------- marketplace ----------
@app.get("/market")
@need("farmer", "buyer", *STAFF)
def market():
    a, p = [], "SELECT l.*, u.name seller FROM listings l JOIN users u ON u.id=l.seller_id WHERE l.status IN ('Available','Offer Received','Reserved')"
    for k, col in (("crop", "l.crop"), ("district", "l.district")):
        if request.args.get(k): 
            p += f" AND {col}=?"
            a.append(request.args[k])
    mine = Q("SELECT * FROM listings WHERE seller_id=? ORDER BY id DESC", (g.user["id"],)) if g.user["role"] == "farmer" else []
    return render_template("market.html", ls=Q(p + " ORDER BY l.id DESC", a), crops=Q("SELECT name FROM crops ORDER BY name"), ds=Q("SELECT name FROM districts ORDER BY name"), f=request.args, mine=mine)

@app.post("/market/sell")
@need("farmer")
def sell():
    try:
        crop = request.form["crop"]
        assert Q1("SELECT 1 x FROM crops WHERE name=?", (crop,))
        q = num("qty_kg", 1, 1e8)
        X("INSERT INTO listings(seller_id,crop,qty_kg,available_kg,price_kg,district,quality) VALUES(?,?,?,?,?,?,?)", 
          (g.user["id"], crop, q, q, num("price_kg", 1, 1e6), g.user["district"], request.form.get("quality", "Good")[:20]))
        audit("listing.create", f"{crop} {q}kg")
        flash("Your produce is now listed on the marketplace.", "ok")
    except Exception:
        flash("Choose a crop and enter a positive quantity and price.", "err")
    return redirect("/market")

@app.post("/market/<int:lid>/offer")
@need("buyer")
def make_offer(lid):
    l = Q1("SELECT * FROM listings WHERE id=? AND status IN ('Available','Offer Received')", (lid,))
    try: 
        assert l
        q = num("qty_kg", .1, l["available_kg"])
        pr = num("price_kg", 1, 1e6)
    except Exception: 
        flash("Offer not valid: quantity must be within what is available.", "err")
        return redirect("/market")
    X("INSERT INTO offers(listing_id,buyer_id,qty_kg,price_kg) VALUES(?,?,?,?)", (lid, g.user["id"], q, pr))
    X("UPDATE listings SET status='Offer Received' WHERE id=?", (lid,))
    notify(l["seller_id"], "offer", f"New offer: {q:.0f} kg {l['crop']} at {pr:.0f} RWF/kg from {g.user['name']}.", "/offers")
    audit("offer.create", f"listing {lid}")
    flash("Offer sent. The farmer will be notified.", "ok")
    return redirect("/offers")

@app.get("/offers")
@need("farmer", "buyer")
def offers():
    if g.user["role"] == "farmer": 
        os_ = Q("SELECT o.*, l.crop, l.price_kg list_price, u.name buyer, u.phone contact FROM offers o JOIN listings l ON l.id=o.listing_id JOIN users u ON u.id=o.buyer_id WHERE l.seller_id=? ORDER BY o.id DESC", (g.user["id"],))
    else: 
        os_ = Q("SELECT o.*, l.crop, l.price_kg list_price, u.name buyer, u.phone contact FROM offers o JOIN listings l ON l.id=o.listing_id JOIN users u ON u.id=l.seller_id WHERE o.buyer_id=? ORDER BY o.id DESC", (g.user["id"],))
    return render_template("offers.html", os=os_)

@app.post("/offers/<int:oid>/<act>")
@need("farmer")
def answer_offer(oid, act):
    o = Q1("SELECT o.*, l.seller_id, l.available_kg, l.crop FROM offers o JOIN listings l ON l.id=o.listing_id WHERE o.id=?", (oid,))
    if not o or o["seller_id"] != g.user["id"] or act not in ("accept", "reject") or o["status"] != "Pending": abort(403)
    if act == "reject":
        X("UPDATE offers SET status='Rejected' WHERE id=?", (oid,))
        notify(o["buyer_id"], "offer", f"Your {o['crop']} offer was declined.", "/offers")
    else:
        if o["qty_kg"] > o["available_kg"]: 
            flash("Not enough quantity left.", "err")
            return redirect("/offers")
        left = o["available_kg"] - o["qty_kg"]
        X("UPDATE offers SET status='Accepted' WHERE id=?", (oid,))
        X("UPDATE listings SET available_kg=?, status=? WHERE id=?", (left, "Sold" if left <= 0 else "Reserved", o["listing_id"]))
        notify(o["buyer_id"], "offer", f"Your offer for {o['qty_kg']:.0f} kg {o['crop']} was ACCEPTED.", "/offers")
    audit("offer." + act, str(oid))
    flash("Offer " + ("accepted." if act == "accept" else "declined."), "ok")
    return redirect("/offers")

# ---------- E-Soko prices ----------
@app.get("/prices")
@need("farmer", "buyer", *STAFF)
def prices():
    crops = Q("SELECT name FROM crops ORDER BY name")
    if not crops: return render_template("msg.html", title="No price data yet", msg="An administrator must load crops and market prices first (Data page).")
    crop = request.args.get("crop") or crops[0]["name"]
    trends = Q("SELECT e.*, d.province FROM esoko_prices e JOIN districts d ON d.name=e.district WHERE e.crop=? ORDER BY e.current_price_rwf DESC", (crop,))
    rows = Q("SELECT m.name market, m.district, m.lat, m.lng, p.day, p.price_kg FROM market_prices p JOIN markets m ON m.id=p.market_id WHERE p.crop=? ORDER BY p.day", (crop,))
    days = sorted({r["day"] for r in rows})
    origin = Q1("SELECT * FROM districts WHERE name=?", (g.user["district"],))
    cmp = []
    if days:
        for m in [r for r in rows if r["day"] == days[-1]]:
            ok = origin and None not in (origin["lat"], origin["lng"], m["lat"], m["lng"])
            km = rules.haversine(origin["lat"], origin["lng"], m["lat"], m["lng"]) if ok else None
            cmp.append(dict(m, km=round(km) if ok else "n/a", net=m["price_kg"] - rules.KM_COST * (km or 0)))
        cmp.sort(key=lambda x: -x["net"])
    series = [sum(r["price_kg"] for r in rows if r["day"] == d) / len([r for r in rows if r["day"] == d]) for d in days]
    gaps = [(dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days for a, b in zip(days, days[1:])]
    weekly = bool(gaps) and statistics.median(gaps) <= 10
    fc = mlclient.call("/ml/price-forecast", {"crop": crop, "prices": series}) if len(series) >= 4 and weekly else None
    age = (dt.date.today() - dt.date.fromisoformat(days[-1])).days if days else None
    srcs = [r["source"] for r in Q("SELECT DISTINCT source FROM market_prices WHERE crop=?", (crop,))]
    return render_template("prices.html", crop=crop, crops=crops, trends=trends, cmp=cmp[:8], series=series, fc=fc if mlclient.valid_forecast(fc) else None, mine=g.user["district"], asof=days[-1] if days else None, age=age, srcs=srcs, weekly=weekly)

# ---------- soil + live data fusion ----------
@app.get("/soil")
@need("farmer", "extension", "government", "admin")
def soil():
    d = request.args.get("district") or g.user["district"]
    p = Q1("SELECT * FROM soil_profiles WHERE district=?", (d,))
    wx = wx3(d)
    return render_template("soil.html", d=d, p=p, tips=rules.field_advice(p, wx), wx=wx, ds=Q("SELECT name FROM districts ORDER BY name"), allp=Q("SELECT * FROM soil_profiles ORDER BY district"))

@app.get("/api/live/<district>")
@need()
def live_api(district):
    d = Q1("SELECT name FROM districts WHERE lower(name)=lower(?)", (district,)) or abort(404)
    p = Q1("SELECT * FROM soil_profiles WHERE district=?", (d["name"],))
    wx = wx3(d["name"])
    return jsonify(
        district=d["name"], 
        weather=wx, 
        soil=p, 
        advice=[{"level": k, "text": t} for k, t in rules.field_advice(p, wx)],
        prices=Q("SELECT crop, current_price_rwf, predicted_trend, change_pct, as_of FROM esoko_prices WHERE district=? ORDER BY crop", (d["name"],))
    )

@app.get("/notifications")
@need()
def notifications():
    ns = Q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50", (g.user["id"],))
    X("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
    return render_template("notifications.html", ns=ns)

# ---------- government: Imihigo command center ----------
def imihigo_data():
    ds = Q("SELECT name, province, lat, lng FROM districts ORDER BY name")
    tg = Q("SELECT district, crop, target_ha FROM imihigo_targets ORDER BY crop")
    act = {(r["district"], r["crop"]): r["ha"] for r in Q("SELECT fa.district, f.crop, SUM(f.size_ha) ha FROM fields f JOIN farms fa ON fa.id=f.farm_id WHERE f.status IN ('Planted','Growing','Flowering','Harvesting','Harvested') GROUP BY fa.district, f.crop")}
    out = []
    for d in ds:
        rows = [{"crop": t["crop"], "target": t["target_ha"], "actual": round(act.get((d["name"], t["crop"]), 0), 1), "pct": round(act.get((d["name"], t["crop"]), 0) / t["target_ha"] * 100)} for t in tg if t["district"] == d["name"]]
        T, A = sum(r["target"] for r in rows), sum(r["actual"] for r in rows)
        pct = round(A / T * 100) if T else None
        out.append(dict(
            d, rows=rows, target=T, actual=round(A, 1), pct=pct,
            color="#9aa5a0" if pct is None else "#2e9e5b" if pct >= 90 else "#e6a118" if pct >= 60 else "#d64545",
            distress=Q1("SELECT COUNT(*) c FROM distress_reports WHERE district=? AND status!='Resolved'", (d["name"],))["c"]
        ))
    return out

@app.get("/gov")
@need("government", "admin", "extension")
def gov():
    D = imihigo_data()
    luc_ = Q1("SELECT SUM(luc_status='aligned') a, SUM(luc_status='not_aligned') n FROM fields")
    known = (luc_["a"] or 0) + (luc_["n"] or 0)
    k = Q1("SELECT (SELECT COUNT(*) FROM users WHERE role='farmer') farmers,(SELECT COUNT(*) FROM farms) farms,(SELECT COUNT(*) FROM distress_reports WHERE status!='Resolved') distress,(SELECT COUNT(*) FROM listings WHERE status IN ('Available','Offer Received','Reserved')) listings")
    k["ha"] = round(sum(d["actual"] for d in D), 1)
    k["luc"] = round((luc_["a"] or 0) / known * 100) if known else None
    withp = [d["pct"] for d in D if d["pct"] is not None]
    k["avg"] = round(sum(withp) / len(withp)) if withp else None
    movers = Q("SELECT * FROM esoko_prices WHERE change_pct IS NOT NULL ORDER BY ABS(change_pct) DESC LIMIT 6")
    return render_template("gov.html", D=D, k=k, movers=movers, dist=Q("SELECT d.*, u.name farmer FROM distress_reports d JOIN users u ON u.id=d.farmer_id WHERE d.status!='Resolved' ORDER BY d.id DESC LIMIT 5"), behind=sorted([d for d in D if d["pct"] is not None and d["pct"] < 60], key=lambda x: x["pct"])[:6])

@app.get("/imihigo")
@need("government", "admin", "extension")
def imihigo(): return render_template("imihigo.html", D=imihigo_data())

@app.get("/api/imihigo")
@need("government", "admin", "extension")
def imihigo_api(): return jsonify(imihigo_data())

@app.route("/rules", methods=["GET", "POST"])
@need("extension", "government", "admin")
def rules_page():
    if request.method == "POST" and request.form.get("id"):
        X("UPDATE fertilizer_subsidies SET approval='approved', approved_by=?, source=? WHERE id=?", 
          (g.user["id"], (request.form.get("source") or "Approved by officer")[:160], request.form["id"]))
        audit("subsidy.approve", request.form["id"])
        flash("Approved.", "ok")
        return redirect("/rules")
    return render_template("rules.html", rs=Q("SELECT * FROM fertilizer_subsidies ORDER BY crop, stage"))

# ---------- admin & data management ----------
@app.route("/admin/data", methods=["GET", "POST"])
@need("admin", "government")
def admin_data():
    if request.method == "POST":
        kind = request.form.get("kind")
        fl = request.files.get("file")
        if kind in ("prices", "soil"):
            started = sync.start_async(kind)
            audit("sync." + kind, "started" if started else "already running")
            flash(f"{'Started' if started else 'Already running'}: {kind} sync. It runs in the background (soil takes ~6 minutes because the provider limits requests). Reload this page to see the result.", "ok")
        elif kind == "weather":
            ok, fail = weather_live.refresh(_c())
            audit("weather.refresh", f"{len(ok)} ok, {len(fail)} failed")
            sync.log("weather", "ok" if ok and not fail else "partial" if ok else "failed", f"{len(ok)} districts updated" + (f", {len(fail)} failed" if fail else ""))
            flash(f"Weather refreshed: {len(ok)} districts updated, {len(fail)} failed.", "ok" if ok else "err")
        elif fl and fl.filename:
            try:
                content = fl.read().decode("utf-8")
                count = loaders.load_csv(kind, content, _c())
                audit("data.import", f"{kind} ({count} rows)")
                flash(f"Imported {count} records into {kind}.", "ok")
            except Exception as e:
                logging.exception(e)
                flash(f"Error importing CSV: {e}", "err")
        return redirect("/admin/data")
    
    logs = Q("SELECT * FROM sync_log ORDER BY id DESC LIMIT 10")
    dq = Q("SELECT * FROM data_quality_logs ORDER BY id DESC LIMIT 10")
    return render_template("data.html", logs=logs, dq=dq)

@app.get("/admin/audit")
@need("admin")
def admin_audit():
    logs = Q("SELECT a.*, u.name, u.email, u.role FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 100")
    return render_template("audit.html", logs=logs)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))