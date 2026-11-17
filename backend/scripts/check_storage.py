"""Offline self-test of alert storage (database + .txt copies). No network, no MT5, no server.

Usage (from the backend folder):  python scripts/check_storage.py
Uses a temporary folder, so it never touches your real snr.db.
"""
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

tmp = Path(tempfile.mkdtemp(prefix="snr_check_"))
os.environ["DB_PATH"] = str(tmp / "snr.db")
os.environ["SETUPS_DIR"] = str(tmp / "setups")
try:
    import httpx  # noqa: F401
except ImportError:                      # only needed for Telegram; not used here
    sys.modules["httpx"] = types.ModuleType("httpx")

from app import db, notify as nt, service          # noqa: E402
from app.state import STORE, TFS                   # noqa: E402
import simulate                                    # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))


class P:  # minimal stand-in for IngestPayload
    def __init__(self, d):
        self.__dict__.update(d)


def feed(payload, full):
    st = STORE.get(payload["symbol"])
    prev = {tf: st.last_time(tf) for tf in TFS}
    st.set_meta(P(payload))
    st.upsert(payload["tfs"])
    new = [tf for tf in payload["tfs"] if st.last_time(tf) > prev[tf]]
    if st.ready() and (full or not st.warmed):
        st.warmed = True
        return service.warmup(st)
    if st.ready() and new:
        return service.on_new_bars(st, new)
    return []


# 1) classification from subject alone (no meta)
c = db.classify("🎯 XAUUSD SELL setup (grade B)")
check("classify setup", (c["kind"], c["symbol"], c["direction"], c["grade"]) == ("setup", "XAUUSD", "sell", "B"), c)
c = db.classify("🧭 XAUUSD Weekly story line: BULLISH")
check("classify story line", (c["kind"], c["direction"]) == ("storyline", "buy"), c)
check("classify feed offline", db.classify("⚠️ XAUUSD: MT5 feed offline")["kind"] == "feed")
check("classify test", db.classify("✅ SNR backend test")["kind"] == "test")

# 2) replay synthetic history: warm up, then feed new H1 bars one at a time
h1 = simulate.make_h1()
cut = len(h1) - 1500
msgs = feed(simulate.build_payload(h1[:cut]), True)
for i in range(cut + 1, len(h1) + 1):
    msgs += feed(simulate.build_payload(h1[:i], full=False), False)
for m in msgs:
    nt.notify(*m)                         # same call the backend makes
rows = db.list_alerts(limit=500)
check("alerts reached the database", len(rows) == len(msgs) and len(rows) > 0, f"{len(rows)} rows")
txts = list((tmp / "setups" / "all").glob("*.txt"))
check("a .txt copy exists for every alert", len(txts) == len(rows), f"{len(txts)} files")
check("every row records its file name", all(r["txt_file"] for r in rows))
kinds = sorted({r["kind"] for r in rows})
print("     kinds stored:", kinds)

# 3) a setup stores real numbers, not just text
setups = [r for r in rows if r["kind"] == "setup"]
if setups:
    s = setups[0]
    check("setup keeps entry/SL/TP/grade", all(s[k] is not None for k in ("entry", "sl", "tp")) and s["grade"], 
          f"{s['grade']} {s['direction']} entry={s['entry']} sl={s['sl']} tp={s['tp']} lots={s['lots']}")
else:
    print("NOTE no setup was triggered by the synthetic data; inserting one by hand to test the rest")
    nt.notify("🎯 XAUUSD BUY setup (grade A)", "demo body",
              {"kind": "setup", "symbol": "XAUUSD", "direction": "buy", "grade": "A",
               "entry": 2000.0, "sl": 1998.0, "tp": 2010.0, "sl_pips": 20, "tp_pips": 100, "lots": 0.5})
    setups = db.list_alerts(kind="setup")

# 4) save, notes, outcome, filters, stats
sid = setups[0]["id"]
check("save", db.set_saved(sid, True) and db.get_alert(sid)["saved"])
check("saved filter", [r["id"] for r in db.list_alerts(saved_only=True)] == [sid])
check("notes + outcome", db.set_notes(sid, "took it, clean break", "won") and db.get_alert(sid)["outcome"] == "won")
try:
    db.set_notes(sid, None, "bogus")
    check("bad outcome rejected", False)
except ValueError:
    check("bad outcome rejected", True)
check("search in notes", any(r["id"] == sid for r in db.list_alerts(q="clean break")))
check("missing id returns False", db.set_saved(999999, True) is False)
st = db.stats()
check("stats win rate", st["overall"]["won"] == 1 and st["overall"]["win_rate"] == 100.0, st["overall"])
check("symbols list", "XAUUSD" in db.symbols())

# 5) a storage failure must never block the other channels
db.config.DB_PATH = Path("/nonexistent_dir/x/snr.db")
db._ready = False
sent = []
nt._telegram = lambda t: sent.append("tg")
nt._email = lambda s, t: sent.append("mail")
nt.notify("✅ SNR backend test", "x")
check("delivery still runs if the database is unavailable", sent == ["tg", "mail"], sent)

print("\nALL CHECKS PASSED" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
