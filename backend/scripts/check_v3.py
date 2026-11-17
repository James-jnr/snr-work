"""End-to-end check of the v3 features on simulated data (no MT5 needed).
Run from backend/:  API_KEY=test python scripts/check_v3.py"""
import logging, os, re, sys, tempfile, time

tmp = tempfile.mkdtemp()
os.environ.setdefault("API_KEY", "test")
os.environ["UI_PUBLIC"] = "true"
os.environ["DB_PATH"] = os.path.join(tmp, "t.db")
os.environ["SETUPS_DIR"] = os.path.join(tmp, "setups")
os.environ.setdefault("DISPLAY_TZ", "Africa/Nairobi")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from fastapi.testclient import TestClient            # noqa: E402
import app.main as M                                  # noqa: E402
from app import db, notify as N                       # noqa: E402
from scripts.simulate import build_payload, make_h1   # noqa: E402

sent = {"tg": 0, "photos": 0}
N._telegram = lambda t: sent.__setitem__("tg", sent["tg"] + 1)
N._telegram_photos = lambda imgs: sent.__setitem__("photos", sent["photos"] + len(imgs))
N._email = lambda *a: None
ok = True


def check(name, cond, info=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  {info}" if info != "" else ""))


c = TestClient(M.app)
K = {"X-API-Key": os.environ["API_KEY"]}
h1 = make_h1(n=24 * 365 * 2)
N_REPLAY = 150

r = c.post("/ingest", json=build_payload(h1[:-N_REPLAY], True), headers=K).json()
check("full load accepted", r["ok"] and not r["need_full"] and r["sync"] is False, r)
time.sleep(4)
for i in range(len(h1) - N_REPLAY, len(h1) - N_REPLAY + 60):
    p = build_payload(h1[:i + 1], False)
    p["tfs"] = {k: v[-4:] for k, v in p["tfs"].items()}
    assert c.post("/ingest", json=p, headers=K).status_code == 200
time.sleep(5)

rows = c.get("/api/alerts?limit=300").json()["alerts"]
kinds = {}
for a in rows:
    kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
check("alerts were created", len(rows) >= 1, kinds)
snap = next(a for a in rows if a["kind"] == "snapshot")
body = snap["body"]
for needle in ("STORY LINES", "WHERE WE ARE NOW", "KEY LEVELS", "LIQUIDITY", "SETUPS", "SETUP 1", "SETUP 2", "SETUP 3",
               "London and New York kill zones"):
    check(f"report has '{needle}'", needle in body)
check("glossary line removed", "Glossary" not in body and "RBS/SBR" not in body)
check("setup section sits below the heavy divider", body.index("▓▓▓") > body.index("LIQUIDITY"))
check("mentor wording: rejected ... with a wick and closed", re.search(r"rejected\s+the\s+.*?with\s+a\s+wick\s+and\s+closed\s+(above|below)\s+it", body, re.S) is not None)
check("mentor wording: gave a ... breakout (QM)", re.search(r"gave\s+a\s+(bullish|bearish)\s+breakout\s+\(QM\)\s+at", body, re.S) is not None)
check("names like 'Daily support (Classic A/V)'", re.search(r"(Daily|Weekly|Monthly|4-hour|1-hour) (support|resistance) \((QM|OCL|Classic A/V)\)", body) is not None)
check("no stop loss / lots in the report", "SL " not in body and "lots" not in body.lower())
check("4-hour story line exists", "4-HOUR" in body or "4-hour story line" in body)
check("no 16-digit prices", re.search(r"\d+\.\d{7,}", body) is None)
check("snapshot has charts incl. zone map", [x["tf"] for x in snap["meta"].get("charts", [])][:1] == ["ZM"], [x["tf"] for x in snap["meta"].get("charts", [])])
check("charts sent to Telegram", sent["photos"] >= 1, sent)
check("snapshot carries story lines for verification", len(snap["meta"].get("stories", [])) >= 1)

a1 = c.get(f"/api/alerts/{snap['id']}").json()
check("single alert endpoint", a1["id"] == snap["id"])
for page in ("/ui", f"/alert/{snap['id']}", "/charts", "/static/ui.js", "/static/ui.css"):
    check(f"page {page}", c.get(page).status_code == 200)
check("static whitelist", c.get("/static/main.py").status_code == 404)

# verification: reject a story line -> engine treats it as ended
s = snap["meta"]["stories"][0]
before = c.get("/state?symbol=XAUUSD", headers=K).json()["stories"][s["tf"]]
c.post("/api/story-status", json={"symbol": "XAUUSD", "tf": s["tf"], "dir": s["dir"], "eff": s["eff"], "status": "rejected"})
after = c.get("/state?symbol=XAUUSD", headers=K).json()["stories"][s["tf"]]
check("rejecting a story line ends it", before is not None and (after is None or after["eff"] != s["eff"]), s["tf"])
c.post("/api/story-status", json={"symbol": "XAUUSD", "tf": s["tf"], "dir": s["dir"], "eff": s["eff"], "status": "verified"})
check("verified status stored", c.get("/api/story-status?symbol=XAUUSD").json().get(f"XAUUSD|{s['tf']}|{s['dir']}|{s['eff']}") == "verified")
c.post("/api/story-status", json={"symbol": "XAUUSD", "tf": s["tf"], "dir": s["dir"], "eff": s["eff"], "status": "unverified"})
check("unverified removes the mark", c.get("/api/story-status?symbol=XAUUSD").json() == {})
check("bad status refused", c.post("/api/story-status", json={"symbol": "X", "tf": "D1", "dir": "bull", "eff": 1, "status": "maybe"}).status_code == 400)

# daily limits: mark 3 taken setups as lost
setups = [a for a in rows if a["kind"] == "setup"] or rows[:3]
for a in (setups * 3)[:3] if len(setups) < 3 else setups[:3]:
    c.post(f"/api/alerts/{a['id']}/taken", json={"taken": True})
    c.post(f"/api/alerts/{a['id']}/notes", json={"outcome": "lost"})
lim = c.get("/api/status").json()["limits"]
check("limits: stop after losses/trades", bool(lim["blocked"]), lim)
check("setup reports show the block", "⛔" in c.get("/brief?symbol=XAUUSD", headers=K).json()["text"])

# status / heart / next 4-hour close
st = c.get("/api/status").json()
check("status has next 4-hour close", st["next_h4_close_utc"] is not None and st["next_h4_close_utc"] > time.time())
check("heart is live right after data", st["live"] is True, st["symbols"][0]["age"])

# sync handshake (what the EA does)
sy = c.post("/api/sync").json()
check("sync requested", sy["requested"] == 1, sy)
hb = c.post("/ingest", json={"symbol": "XAUUSD", "tfs": {}}, headers=K).json()
check("heartbeat answer tells the EA to resend", hb["sync"] is True, hb)
n_before = len(c.get("/api/alerts?limit=300").json()["alerts"])
full = c.post("/ingest", json=build_payload(h1, True), headers=K).json()
time.sleep(5)
check("full resend clears the request", full["sync"] is False and c.get("/api/status").json()["sync_pending"] is False)
check("sync produces a fresh snapshot", len(c.get("/api/alerts?limit=300").json()["alerts"]) == n_before + 1)
check("sync without feed is refused", (M.STORE._d.clear() or c.post("/api/sync").status_code) == 409)

ph = c.get("/api/phone").json()
check("phone link + QR", ph["url"].startswith("http://") and "/ui" in ph["url"] and "<svg" in ph["qr_svg"], ph["url"])

# charts
c.post("/ingest", json=build_payload(h1, True), headers=K)
time.sleep(3)
for tf in ("ZM", "MN1", "W1", "D1", "H4", "H1"):
    r = c.get(f"/api/chart.png?symbol=XAUUSD&tf={tf}")
    check(f"chart {tf}", r.status_code == 200 and r.content[:4] == b"\x89PNG", len(r.content) // 1024)
lv = c.get("/api/levels?symbol=XAUUSD&tf=D1").json()
check("levels table has names and strength", lv["levels"] and "strength" in lv["levels"][0] and "(" in lv["levels"][0]["name"])
print("\nALL CHECKS PASSED" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
