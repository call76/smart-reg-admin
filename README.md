# KIZA-AGRI — Hackathon build
**Grow Smarter. Sell Better. Plan Together.** A farm-to-national-decision platform for Rwanda (NST2 aligned).

## Run (2 minutes)
1. Install Python 3.10+, then once: `pip install -r requirements.txt`
2. Windows: double-click **START-KIZA-AGRI.bat** · Mac/Linux: `./start.sh`
3. Open http://127.0.0.1:5000 (database is created and seeded automatically).

## Demo logins — password `Kiza@2026-demo`
You can sign in with **phone or email**.
| Role | Phone | Email |
|---|---|---|
| Farmer (Musanze / Muhoza) | 0788000001 | farmer@kiza.rw |
| Buyer | 0788000002 | buyer@kiza.rw |
| Extension officer | 0788000003 | officer@kiza.rw |
| Government | 0788000004 | gov@kiza.rw |
| Admin | 0788000005 | admin@kiza.rw |
Anyone can also register with a Rwandan phone number (email optional). English / Kinyarwanda switch (EN | RW) in the menu.

## The 7 core deliverables
1. **Digital Farm Passport** — farmer → farm → fields, linked to province / district / sector.
2. **Smart Crop Advisor + LUC guardrail** — explained crop score; a field whose crop is not the sector's Land Use Consolidation priority crop is blocked until the farmer confirms with the sector agronomist; every field stores its LUC status.
3. **Weather Smart Alerting** — forecast turned into plain farming instructions (live from Open-Meteo, or clearly-labelled DEMO).
4. **Smart Nkunganire Fertilizer Engine** — kg needed, cost at **open-market vs subsidized price**, and the saving.
5. **Distress Harvest Registry** — farmers report gluts / price manipulation / spoilage; officers triage (New → Reviewing → Resolved) and the farmer is notified.
6. **E-Soko Pricing Pipeline** — CSV **or Excel** price sheets are validated and loaded; district prices and a recent trend (14-day window for weekly data, previous reading for monthly data) are computed; Python service gives an experimental 2-week range.
7. **Imihigo Command Map** — Leaflet map of planted hectares vs district targets (colour-coded), plus government Command Center (LUC compliance, distress alerts, price movers).
Plus: marketplace with offers (phone shared only after acceptance), 8-attempt login throttle, CSRF, role-based access, audit log, notifications.

## Live data (market · weather · soil)
Needs internet. Runs automatically while the app is open (turn off with `KIZA_AUTO_SYNC=0`) and from **Data → Live data sync → Run now** (admin / government).
| Layer | Source | Refresh | What it does |
|---|---|---|---|
| Market prices | WFP "Rwanda - Food Prices" on HDX (the app finds the current CSV through the HDX API) | checked daily; **publisher updates monthly** | On a clean real-mode database with no crops it first adds 6 BASELINE crop estimates (flagged in the sync log; replace via Data → crops). Loads RWF/kg retail prices for Maize, Beans, Potato, Cassava, Rice, Tomato into markets + market prices, recomputes district trends, **replaces the DEMO prices** |
| Weather | Open-Meteo (free, no key) | every 3 h | 3-day forecast + 7-day rain per district, turned into farming advice |
| Soil | ISRIC SoilGrids 2.0 (modelled 250 m, topsoil 0-30 cm) at each district centroid | monthly (throttled, ~6 min) | pH, nitrogen, organic carbon, texture → likely limiting nutrient + suggested fertilizer |
| Soil (official) | RAB / NISR table uploaded on the Data page (`soil_profiles`) | when you upload | Official rows are **never overwritten** by SoilGrids |

**Fusion:** `/soil` and `GET /api/live/<district>` combine soil + weather + prices ("heavy rain tomorrow on acidic Musanze soil: do not spread fertilizer today; lime first"). A farm registered without a pH takes the district soil pH instead of a made-up number.
Every run is logged (Data page). A failed run keeps the previous data and the pages say how old it is. Prices are the **latest published** WFP prices, not a live tick; with monthly data the app shows a trend but not the 2-week ML forecast.
Soil from SoilGrids is a model estimate for screening, not a lab test. The 4 DEMO soil rows shipped for offline demos are labelled DEMO and are replaced by SoilGrids / official data.
No API keys are needed. Upgrading an old `kiza.db` is automatic (new tables are created on start).

## Real data (no demo)
Run **SETUP-REAL.bat** (clean DB: 30 districts + staff accounts; passwords shown once) → log in as admin → **Data** page → download each template, fill from official sources, upload (.csv/.xlsx). Order: crops → markets → market prices → fertilizer subsidies → LUC zones → Imihigo targets. Bad rows are rejected and listed. `DEMO DATA` labels vanish automatically.

## Removed as unnecessary (kept the product focused)
Digital-twin cards, "what-if" crop compare, ML yield card, harvest-record step, surplus/deficit & fertilizer-stock modules, Simple/Advanced toggle, enterprise-style extras.

## Honest status
- Built with **Flask + SQLite** (not Laravel/MySQL or FastAPI): the build environment had no PHP/Composer/internet. Schema is relational and maps 1:1 to MySQL; the Python service exposes a documented REST API and can be ported to FastAPI.
- **All seeded numbers are DEMO**: yields, prices, Nkunganire fertilizer prices, LUC priority crops, Imihigo targets, weather. They must be replaced with RAB / MINAGRI / NISR data; nothing here states real subsidy rates or legal rules.
- LUC guardrail asks for confirmation rather than refusing outright, because enforcement rules must come from your sector authorities.
- Leaflet map needs internet once (or copy Leaflet into `static/vendor/leaflet`); otherwise district tiles are shown. District points are approximate centroids, not boundaries.
- Kinyarwanda covers menus, login/register, dashboard and key screens; wording should be reviewed by a native speaker. Live provider calls (Open-Meteo, HDX, SoilGrids) could not be run in the build sandbox: parsers, loaders, fallbacks and scheduling are tested with realistic fake payloads - run **Data → Run now** once with internet to confirm each source.
- Tests: `python -m unittest discover tests` (23 tests, all offline: the 7 deliverables, security, real-data mode, live price / soil / weather-fusion layers).
