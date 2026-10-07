"""
KIZA-AGRI database layer: schema + DEMO seed (SQLite for trial/dev; maps 1:1 to MySQL/PostgreSQL).
"""
import os
import sqlite3
import random
import datetime as dt
from werkzeug.security import generate_password_hash

DB = os.environ.get("KIZA_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "kiza.db"))
DEMO_PASSWORD = os.environ.get("KIZA_DEMO_PASSWORD", "Kiza@2026-demo")

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, phone TEXT, pw_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('farmer','buyer','extension','government','admin')), district TEXT, sector TEXT, 
    lang TEXT DEFAULT 'en', status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS districts(
    name TEXT PRIMARY KEY, province TEXT, lat REAL, lng REAL
);

CREATE TABLE IF NOT EXISTS crops(
    name TEXT PRIMARY KEY, yield_t_ha REAL, price_kg REAL, cost_ha REAL, temp_min REAL, temp_max REAL,
    rain_min REAL, rain_max REAL, ph_min REAL, ph_max REAL, seasons TEXT, water_need TEXT, disease_risk REAL, cycle_days INTEGER
);

CREATE TABLE IF NOT EXISTS farms(
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL,
    size_ha REAL NOT NULL CHECK(size_ha>0), lat REAL, lng REAL, district TEXT NOT NULL REFERENCES districts(name),
    sector TEXT, soil_type TEXT, soil_ph REAL, irrigation INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fields(
    id INTEGER PRIMARY KEY, farm_id INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE, name TEXT NOT NULL,
    size_ha REAL NOT NULL CHECK(size_ha>0), crop TEXT REFERENCES crops(name), season TEXT, planting_date TEXT, expected_harvest TEXT,
    luc_status TEXT DEFAULT 'unknown', status TEXT DEFAULT 'Planned' CHECK(status IN ('Planned','Prepared','Planted','Growing','Flowering','Harvesting','Harvested','Failed')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS luc_zones(
    id INTEGER PRIMARY KEY, district TEXT REFERENCES districts(name), sector TEXT NOT NULL, mandated_crop TEXT REFERENCES crops(name), UNIQUE(district,sector)
);

CREATE TABLE IF NOT EXISTS imihigo_targets(
    id INTEGER PRIMARY KEY, district TEXT REFERENCES districts(name), crop TEXT REFERENCES crops(name), target_ha REAL CHECK(target_ha>0), UNIQUE(district,crop)
);

CREATE TABLE IF NOT EXISTS weather(
    id INTEGER PRIMARY KEY, district TEXT REFERENCES districts(name), day TEXT, temp REAL, rain_prob INTEGER,
    rain_mm REAL, rain_7d REAL, humidity INTEGER, wind REAL, is_demo INTEGER DEFAULT 1, fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS markets(
    id INTEGER PRIMARY KEY, name TEXT, district TEXT REFERENCES districts(name), lat REAL, lng REAL
);

CREATE TABLE IF NOT EXISTS market_prices(
    id INTEGER PRIMARY KEY, market_id INTEGER REFERENCES markets(id) ON DELETE CASCADE, crop TEXT REFERENCES crops(name),
    day TEXT, price_kg REAL CHECK(price_kg>0), source TEXT, UNIQUE(market_id,crop,day)
);

CREATE TABLE IF NOT EXISTS esoko_prices(
    id INTEGER PRIMARY KEY, district TEXT, crop TEXT, current_price_rwf REAL, predicted_trend TEXT, change_pct REAL, as_of TEXT, UNIQUE(district,crop)
);

CREATE TABLE IF NOT EXISTS fertilizer_subsidies(
    id INTEGER PRIMARY KEY, crop TEXT REFERENCES crops(name), stage TEXT, fertilizer_type TEXT, kg_per_ha REAL,
    market_price_per_kg REAL, subsidized_price_per_kg REAL, source TEXT, approval TEXT DEFAULT 'pending', approved_by INTEGER REFERENCES users(id), effective_date TEXT
);

CREATE TABLE IF NOT EXISTS listings(
    id INTEGER PRIMARY KEY, seller_id INTEGER REFERENCES users(id) ON DELETE CASCADE, crop TEXT REFERENCES crops(name), qty_kg REAL CHECK(qty_kg>0),
    available_kg REAL, price_kg REAL CHECK(price_kg>0), district TEXT, quality TEXT, status TEXT DEFAULT 'Available', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS offers(
    id INTEGER PRIMARY KEY, listing_id INTEGER REFERENCES listings(id) ON DELETE CASCADE, buyer_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    qty_kg REAL CHECK(qty_kg>0), price_kg REAL CHECK(price_kg>0), status TEXT DEFAULT 'Pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS distress_reports(
    id INTEGER PRIMARY KEY, farmer_id INTEGER REFERENCES users(id) ON DELETE CASCADE, district TEXT, crop TEXT, kind TEXT, qty_kg REAL,
    details TEXT, status TEXT DEFAULT 'New', officer_note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications(
    id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, kind TEXT, message TEXT, link TEXT, is_read INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs(
    id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT, detail TEXT, ip TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_quality_logs(
    id INTEGER PRIMARY KEY, source TEXT, row_no INTEGER, issue TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_farms_user ON farms(user_id);
CREATE INDEX IF NOT EXISTS ix_fields_farm ON fields(farm_id);
CREATE INDEX IF NOT EXISTS ix_fields_status ON fields(status);
CREATE INDEX IF NOT EXISTS ix_prices ON market_prices(crop,day);
CREATE INDEX IF NOT EXISTS ix_listings ON listings(crop,status);
CREATE INDEX IF NOT EXISTS ix_weather ON weather(district,day);
"""

MIGRATE = """
CREATE TABLE IF NOT EXISTS soil_profiles(
    district TEXT PRIMARY KEY REFERENCES districts(name), soil_type TEXT, ph_level REAL, nitrogen_g_kg REAL, organic_c_g_kg REAL,
    clay_pct REAL, sand_pct REAL, main_nutrient_deficit TEXT, recommended_fertilizer TEXT, source TEXT NOT NULL, is_demo INTEGER DEFAULT 0, fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS sync_log(
    id INTEGER PRIMARY KEY, kind TEXT, status TEXT, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

SOIL_DEMO = [
    ("Musanze", "Volcanic loam", 5.8, 2.4, 18.5, 25.0, 35.0, "Potassium (K)", "NPK 17-17-17 + lime"),
    ("Bugesera", "Sandy clay loam", 6.5, 1.2, 11.0, 32.0, 48.0, "Phosphorus (P)", "DAP + organic compost"),
    ("Nyagatare", "Alluvial clay loam", 7.2, 1.8, 14.2, 38.0, 30.0, "Nitrogen (N)", "Urea"),
    ("Gasabo", "Lateritic clay", 5.2, 1.1, 9.8, 45.0, 25.0, "Phosphorus & Nitrogen", "NPK 15-15-15 + Lime"),
    ("Huye", "Acrisol loam", 5.4, 1.5, 12.0, 30.0, 40.0, "Phosphorus (P)", "DAP + Lime"),
    ("Rubavu", "Volcanic ash loam", 6.1, 2.8, 22.0, 20.0, 30.0, "Zinc & Potassium", "NPK 17-17-17"),
    ("Kirehe", "Ferralsol sandy loam", 6.0, 1.3, 10.5, 22.0, 58.0, "Nitrogen & Organic Matter", "Urea + Manure")
]

# Agronomic benchmarks for Rwandan priority crops
CROPS = [
    # Cereals & Grains
    ("Maize", 3.0, 400, 650000, 18, 30, 15, 60, 5.5, 7.0, "A,B", "Medium", 0.30, 120),
    ("Rice", 5.0, 700, 1100000, 22, 32, 40, 100, 5.5, 7.0, "A,B", "High", 0.40, 130),
    ("Wheat", 2.5, 550, 700000, 12, 24, 15, 55, 5.8, 7.0, "A,B", "Medium", 0.25, 110),
    ("Sorghum", 2.0, 380, 450000, 18, 32, 8, 45, 5.5, 7.5, "A", "Low", 0.20, 120),

    # Pulses & Legumes
    ("Beans", 1.2, 800, 500000, 16, 27, 10, 50, 5.8, 6.8, "A,B", "Low", 0.35, 90),
    ("Soybean", 1.5, 650, 520000, 18, 30, 12, 50, 6.0, 7.0, "A,B", "Medium", 0.25, 95),
    ("Groundnut", 1.4, 1100, 580000, 20, 30, 10, 45, 5.8, 6.5, "A,B", "Medium", 0.30, 110),
    ("Peas", 1.1, 900, 480000, 12, 22, 12, 50, 6.0, 7.0, "A,B", "Low", 0.30, 85),

    # Roots & Tubers
    ("Potato", 15.0, 350, 1500000, 10, 22, 20, 60, 5.0, 6.5, "A,B,C", "High", 0.50, 100),
    ("Cassava", 12.0, 250, 600000, 20, 32, 10, 60, 5.0, 7.0, "A,B", "Low", 0.25, 300),
    ("Sweet Potato", 10.0, 280, 450000, 18, 28, 12, 55, 5.5, 6.8, "A,B", "Low", 0.20, 120),
    ("Yam", 8.0, 450, 550000, 18, 30, 15, 60, 5.5, 6.8, "A,B", "Low", 0.25, 240),

    # Cash & Export Crops
    ("Coffee", 1.8, 1200, 1800000, 15, 26, 25, 70, 5.2, 6.2, "A,B,C", "Medium", 0.45, 365),
    ("Tea", 2.2, 1100, 2000000, 12, 25, 30, 80, 4.5, 5.6, "A,B,C", "High", 0.30, 365),
    ("Pyrethrum", 1.2, 2500, 1400000, 10, 20, 25, 70, 5.2, 6.5, "A,B,C", "Medium", 0.30, 365),
    ("Macadamia", 3.5, 3500, 3000000, 16, 28, 20, 70, 5.0, 6.5, "A,B,C", "Low", 0.25, 365),
    ("Sugarcane", 60.0, 45, 2200000, 20, 35, 30, 90, 6.0, 7.5, "A,B,C", "High", 0.20, 365),

    # Fruits & Commercial Horticulture
    ("Banana", 18.0, 300, 900000, 18, 30, 20, 80, 5.5, 7.0, "A,B,C", "High", 0.35, 365),
    ("Avocado", 18.0, 900, 1200000, 16, 28, 15, 65, 5.5, 6.8, "A,B,C", "Medium", 0.35, 365),
    ("Pineapple", 30.0, 400, 1600000, 20, 32, 15, 60, 4.5, 5.5, "A,B,C", "Low", 0.20, 540),
    ("Passion Fruit", 12.0, 1500, 2200000, 16, 26, 20, 65, 5.5, 6.5, "A,B,C", "High", 0.50, 365),
    ("Mango", 15.0, 700, 1100000, 20, 35, 10, 50, 5.5, 7.2, "A,B,C", "Low", 0.25, 365),
    ("Papaya", 25.0, 500, 1000000, 20, 32, 15, 60, 6.0, 6.8, "A,B,C", "Medium", 0.30, 270),

    # Vegetables & Oilseeds
    ("Tomato", 25.0, 450, 2500000, 18, 28, 15, 50, 6.0, 6.8, "B,C", "High", 0.55, 100),
    ("Onion", 14.0, 600, 1200000, 15, 28, 10, 45, 6.0, 7.0, "B,C", "Medium", 0.30, 110),
    ("Cabbage", 20.0, 250, 800000, 12, 24, 15, 50, 6.0, 6.8, "A,B,C", "Medium", 0.35, 90),
    ("Carrot", 15.0, 500, 950000, 14, 25, 12, 45, 5.8, 6.8, "A,B", "Low", 0.25, 85),
    ("Chili Pepper", 8.0, 1800, 1800000, 18, 30, 12, 50, 6.0, 7.0, "A,B,C", "Medium", 0.40, 120),
    ("Sunflower", 1.8, 750, 500000, 18, 32, 8, 40, 6.0, 7.2, "A,B", "Low", 0.20, 105)
]

RW30 = [
    ("Gasabo","Kigali",-1.89,30.13),("Kicukiro","Kigali",-1.99,30.10),("Nyarugenge","Kigali",-1.96,30.05),
    ("Nyanza","Southern",-2.35,29.75),("Gisagara","Southern",-2.60,29.85),("Nyaruguru","Southern",-2.72,29.52),
    ("Huye","Southern",-2.60,29.74),("Nyamagabe","Southern",-2.47,29.42),("Ruhango","Southern",-2.22,29.78),
    ("Muhanga","Southern",-2.08,29.75),("Kamonyi","Southern",-2.00,29.90),("Karongi","Western",-2.10,29.38),
    ("Rutsiro","Western",-1.95,29.35),("Rubavu","Western",-1.68,29.35),("Nyabihu","Western",-1.67,29.50),
    ("Ngororero","Western",-1.87,29.62),("Rusizi","Western",-2.48,28.95),("Nyamasheke","Western",-2.32,29.10),
    ("Rulindo","Northern",-1.75,29.97),("Gakenke","Northern",-1.70,29.78),("Musanze","Northern",-1.50,29.63),
    ("Burera","Northern",-1.45,29.85),("Gicumbi","Northern",-1.58,30.07),("Rwamagana","Eastern",-1.95,30.43),
    ("Nyagatare","Eastern",-1.30,30.33),("Gatsibo","Eastern",-1.60,30.40),("Kayonza","Eastern",-1.88,30.65),
    ("Kirehe","Eastern",-2.25,30.70),("Ngoma","Eastern",-2.15,30.50),("Bugesera","Eastern",-2.20,30.20)
]

FERT = [
    ("Maize","planting","DAP",100,1100,750), ("Maize","top_dressing","Urea",100,900,600),
    ("Beans","planting","DAP",50,1100,750), ("Beans","top_dressing","CAN",50,850,580),
    ("Potato","planting","NPK 17-17-17",300,1000,700), ("Potato","top_dressing","Urea",100,900,600),
    ("Cassava","planting","NPK 17-17-17",100,1000,700), ("Rice","planting","DAP",100,1100,750),
    ("Rice","top_dressing","Urea",100,900,600), ("Tomato","planting","NPK 17-17-17",250,1000,700),
    ("Tomato","top_dressing","Urea",100,900,600), ("Wheat","planting","DAP",100,1100,750)
]

def migrate():
    """Create tables added after the initial schema creation."""
    c = conn()
    c.executescript(MIGRATE)
    c.commit()
    c.close()

def init(force=False):
    if os.path.exists(DB):
        if not force:
            migrate()
            return
        os.remove(DB)

    c = conn()
    c.executescript(SCHEMA)
    c.executescript(MIGRATE)

    rnd = random.Random(2026)
    today = dt.date.today()
    pw = generate_password_hash(DEMO_PASSWORD)

    # 1. Crops & Districts
    c.executemany("INSERT INTO crops VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", CROPS)
    for d in RW30:
        c.execute("INSERT INTO districts VALUES(?,?,?,?)", d)

    # 2. Soil Profiles
    for s in SOIL_DEMO:
        c.execute(
            """INSERT INTO soil_profiles(
                district, soil_type, ph_level, nitrogen_g_kg, organic_c_g_kg, clay_pct, sand_pct,
                main_nutrient_deficit, recommended_fertilizer, source, is_demo, fetched_at
            ) VALUES(?,?,?,?,?,?,?,?,?,'DEMO - indicative, not verified',1,?)""",
            (*s, str(today))
        )

    # 3. Markets, Weather & Market Prices
    for d in RW30:
        c.execute("INSERT INTO markets(name,district,lat,lng) VALUES(?,?,?,?)", (f"{d[0]} Central Market", d[0], d[2] + .01, d[3] + .01))
        for k in range(5):
            c.execute(
                """INSERT INTO weather(district,day,temp,rain_prob,rain_mm,rain_7d,humidity,wind,is_demo,fetched_at)
                   VALUES(?,?,?,?,?,?,?,?,1,?)""",
                (d[0], str(today + dt.timedelta(k)), round(rnd.uniform(16, 26), 1), rnd.choice([10, 25, 45, 70, 85]),
                 round(rnd.uniform(0, 18), 1), round(rnd.uniform(15, 60), 1), rnd.randint(50, 92), round(rnd.uniform(5, 25), 1), str(today))
            )

    markets = c.execute("SELECT id FROM markets").fetchall()
    for m in markets:
        for cr in rnd.sample(CROPS, 10):
            base = cr[2] * (0.9 + rnd.random() * 0.25)
            trend = rnd.uniform(-0.015, 0.025)
            for w in range(8, -1, -1):
                c.execute(
                    "INSERT INTO market_prices(market_id,crop,day,price_kg,source) VALUES(?,?,?,?,?)",
                    (m["id"], cr[0], str(today - dt.timedelta(7 * w)), round(base * (1 + trend * (8 - w)) * (1 + rnd.uniform(-0.03, 0.03))), "DEMO DATA")
                )

    # 4. Subsidies
    for f in FERT:
        c.execute(
            """INSERT INTO fertilizer_subsidies(crop,stage,fertilizer_type,kg_per_ha,market_price_per_kg,subsidized_price_per_kg,source,approval,effective_date)
               VALUES(?,?,?,?,?,?,'Nkunganire Program - DEMO','approved',?)""",
            (*f, str(today))
        )

    # 5. Core Users
    core_users = [
        ("Uwase Marie", "farmer@kiza.rw", "farmer", "Musanze", "Muhoza", "0788000001"),
        ("Habimana Jean", "buyer@kiza.rw", "buyer", "Gasabo", "Remera", "0788000002"),
        ("Mukamana Alice", "officer@kiza.rw", "extension", "Musanze", "Muhoza", "0788000003"),
        ("Nsengiyumva Paul", "gov@kiza.rw", "government", "Gasabo", "Kacyiru", "0788000004"),
        ("System Admin", "admin@kiza.rw", "admin", "Gasabo", "Kacyiru", "0788000005")
    ]
    for n, e, r, d, sec, ph in core_users:
        c.execute("INSERT INTO users(name,email,phone,pw_hash,role,district,sector) VALUES(?,?,?,?,?,?,?)", (n, e, ph, pw, r, d, sec))

    # 6. District LUC & Imihigo targets
    cn = [x[0] for x in CROPS]
    mand = {}
    for d in RW30:
        mand[d[0]] = rnd.choice(cn[:10])
        c.execute("INSERT INTO luc_zones(district,sector,mandated_crop) VALUES(?,?,?)", (d[0], "Demo Sector", mand[d[0]]))
        for cr in rnd.sample(cn, 4):
            c.execute("INSERT INTO imihigo_targets(district,crop,target_ha) VALUES(?,?,?)", (d[0], cr, rnd.randint(15, 60)))
    c.execute("INSERT INTO luc_zones(district,sector,mandated_crop) VALUES('Musanze','Muhoza','Potato')")

    # 7. Seed Farmers, Farms, Fields, Listings, Offers
    for i, d in enumerate(RW30):
        for j in range(2):
            uid = c.execute(
                "INSERT INTO users(name,email,phone,pw_hash,role,district,sector) VALUES(?,?,?,?,'farmer',?,?)",
                (f"Farmer {d[0]} {j+1}", f"demo.{d[0].lower()}{j}@kiza.rw", f"07881{i:02d}{j:03d}", pw, d[0], "Demo Sector")
            ).lastrowid

            fid = c.execute(
                """INSERT INTO farms(user_id,name,size_ha,lat,lng,district,sector,soil_type,soil_ph,irrigation)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (uid, f"{d[0]} Model Farm {j+1}", round(rnd.uniform(1.5, 8.0), 1), d[2], d[3], d[0], "Demo Sector", "Loam", round(rnd.uniform(5.4, 6.8), 1), rnd.randint(0, 1))
            ).lastrowid

            for k in range(2):
                cr = rnd.choice(CROPS)
                c.execute(
                    """INSERT INTO fields(farm_id,name,size_ha,crop,season,planting_date,expected_harvest,luc_status,status)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (fid, f"Plot {k+1}", round(rnd.uniform(0.5, 3.5), 1), cr[0], "2026A",
                     str(today - dt.timedelta(40)), str(today + dt.timedelta(cr[13] - 40)),
                     "aligned" if mand[d[0]] == cr[0] else "not_aligned", rnd.choice(["Planted", "Growing", "Flowering"]))
                )

            # Marketplace Listings
            if j == 0:
                l_crop = rnd.choice(CROPS)
                qty = rnd.randint(500, 5000)
                list_id = c.execute(
                    """INSERT INTO listings(seller_id,crop,qty_kg,available_kg,price_kg,district,quality,status)
                       VALUES(?,?,?,?,?,?,'Grade A','Available')""",
                    (uid, l_crop[0], qty, qty, l_crop[2] * rnd.uniform(0.9, 1.1), d[0])
                ).lastrowid

                # Buyer Offer for listing
                buyer_id = c.execute("SELECT id FROM users WHERE email='buyer@kiza.rw'").fetchone()[0]
                c.execute(
                    """INSERT INTO offers(listing_id,buyer_id,qty_kg,price_kg,status)
                       VALUES(?,?,?,?,'Pending')""",
                    (list_id, buyer_id, rnd.randint(200, 1000), l_crop[2] * 0.95)
                )

    # 8. Core Demo User Farm Data
    fu = c.execute("SELECT id FROM users WHERE email='farmer@kiza.rw'").fetchone()[0]
    fid = c.execute(
        """INSERT INTO farms(user_id,name,size_ha,lat,lng,district,sector,soil_type,soil_ph,irrigation)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (fu, "Uwase Family Farm", 2.5, -1.50, 29.63, "Musanze", "Muhoza", "Volcanic loam", 6.0, 1)
    ).lastrowid

    c.execute(
        """INSERT INTO fields(farm_id,name,size_ha,crop,season,planting_date,expected_harvest,luc_status,status)
           VALUES(?,?,?,'Potato','2026A',?,?,'aligned','Growing')""",
        (fid, "North Plot", 1.2, str(today - dt.timedelta(35)), str(today + dt.timedelta(65)))
    )
    c.execute(
        """INSERT INTO fields(farm_id,name,size_ha,crop,season,planting_date,expected_harvest,luc_status,status)
           VALUES(?,?,?,'Maize','2026A',?,?,'aligned','Planted')""",
        (fid, "South Plot", 1.3, str(today - dt.timedelta(15)), str(today + dt.timedelta(105)))
    )

    # 9. Distress Reports & Notifications
    demo_farmer = c.execute("SELECT id FROM users WHERE email='demo.rubavu0@kiza.rw'").fetchone()[0]
    c.execute(
        """INSERT INTO distress_reports(farmer_id,district,crop,kind,qty_kg,details,status)
           VALUES(?, 'Rubavu', 'Potato', 'Market Surplus / Price Drop', 4000,
           'Bumper harvest in Rubavu led to steep local price drop. Need off-taker aggregation.', 'New')""",
        (demo_farmer,)
    )

    c.execute("INSERT INTO notifications(user_id,kind,message,link) VALUES(?,'system','Welcome to KIZA-AGRI. Start with the Crop Advisor.','/advisor')", (fu,))
    c.execute("INSERT INTO notifications(user_id,kind,message,link) VALUES(?,'offer','New buyer offer received for Potato listing.','/marketplace')", (fu,))

    # 10. Audit & Sync Logs
    c.execute("INSERT INTO audit_logs(user_id,action,detail,ip) VALUES(?,'SEED_INIT','Initialized demo database with seed schema','127.0.0.1')", (fu,))
    c.execute("INSERT INTO sync_log(kind,status,detail) VALUES('RAB_SOIL_DATA','SUCCESS','Soil profile baseline synced')")

    # 11. Refresh Esoko or seed fallback
    try:
        import esoko
        esoko.refresh(c)
    except Exception:
        trends = ['Rising', 'Stable', 'Falling']
        for d in RW30[:10]:
            for cr in CROPS[:8]:
                curr_p = round(cr[2] * rnd.uniform(0.85, 1.15))
                chg = round(rnd.uniform(-4.5, 4.5), 2)
                c.execute(
                    """INSERT OR REPLACE INTO esoko_prices(district,crop,current_price_rwf,predicted_trend,change_pct,as_of)
                       VALUES(?,?,?,?,?,?)""",
                    (d[0], cr[0], curr_p, rnd.choice(trends), chg, str(today))
                )

    c.commit()
    c.close()

def init_real(force=False):
    """Clean database for REAL deployment: schema + 30 districts + generated admin credentials."""
    import secrets
    if os.path.exists(DB):
        if not force:
            raise SystemExit(f"{DB} already exists. Re-run with --force to overwrite.")
        os.remove(DB)

    c = conn()
    c.executescript(SCHEMA)
    c.executescript(MIGRATE)

    for d in RW30:
        c.execute("INSERT INTO districts VALUES(?,?,?,?)", d)

    out = []
    roles = [
        ("admin", os.environ.get("KIZA_ADMIN_EMAIL", "admin@kiza.rw"), "0788000005"),
        ("government", os.environ.get("KIZA_GOV_EMAIL", "gov@kiza.rw"), "0788000004"),
        ("extension", os.environ.get("KIZA_OFFICER_EMAIL", "officer@kiza.rw"), "0788000003")
    ]

    for role, email, ph in roles:
        pw = secrets.token_urlsafe(10)
        c.execute(
            "INSERT INTO users(name,email,phone,pw_hash,role,district,sector) VALUES(?,?,?,?,?,'Gasabo','')",
            (role.title(), email, ph, generate_password_hash(pw), role)
        )
        out.append((role, email, pw))

    c.commit()
    c.close()

    print("Production database initialized. SAVE credentials immediately:")
    for r, e, p in out:
        print(f"  {r:12s} {e:25s} {p}")

if __name__ == "__main__":
    import sys
    if "--real" in sys.argv:
        init_real(force="--force" in sys.argv)
    else:
        init(force=True)
        print("Demo database successfully initialized at:", DB)