"""Check Setup 4 (15-min entry) and the grade / 15-min class fields without MT5 or a web server.
Run from backend/:   python scripts/check_m15.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("API_KEY", "test")
from app import config                                     # noqa: E402
from app.analysis import analyze                           # noqa: E402
from app.messages import fmt_report                        # noqa: E402
from app.state import SymbolState                          # noqa: E402
from scripts.simulate import build_payload, make_h1, make_m15   # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  {info}" if info != "" else ""))


class P:   # minimal stand-in for the ingest payload (meta only)
    gmt_offset = 7200; digits = 2; point = 0.01; tick_size = 0.01; tick_value = 1.0
    vol_min = 0.01; vol_step = 0.01; vol_max = 100.0; balance = 10000.0; currency = "USD"; server_time = 0


RH = int(os.environ.get("REPLAY_H", 100))
seeds = [int(x) for x in os.environ.get("SEEDS", "7,3,21").split(",")]
found, grades, classes, t0, n_an = [], set(), set(), time.time(), 0
for seed in seeds:
    h1 = make_h1(n=24 * 365, seed=seed)
    st = SymbolState("XAUUSD")
    st.set_meta(P)
    full = build_payload(h1[:-RH], True)
    st.upsert(full["tfs"])
    check(f"[seed {seed}] full load ready (M15 optional)", st.ready())
    # replay the last 400 hours, one 15-min candle at a time (as the EA would send them)
    steps = make_m15(h1[-(RH + 200):])
    start = len(steps) - 4 * RH
    for i in range(start, len(steps)):
        m = steps[i]
        if m[0] % 3600 == 0 and i > start:                 # a new hour has just started: previous H1 closed
            hh = [x for x in h1 if x[0] == m[0] - 3600]
            if hh:
                payload = build_payload(h1[:h1.index(hh[0]) + 1], False)
                st.upsert({k: v[-4:] for k, v in payload["tfs"].items() if k != "M15"})
        st.upsert({"M15": [m]})
        if (i - start) % 2:                                 # analyse every other 15-min candle to keep the test short
            continue
        a = analyze(st)
        n_an += 1
        if a is None:
            continue
        s4 = [su for su in a.setups if su["id"] == 4]
        if s4 and s4[0]["dir"] and s4[0]["status"] in ("ready", "late", "asia", "blocked") and s4[0]["entry"]:
            found.append((seed, i, a, s4[0]))
        for su in a.setups:
            if su["dir"]:
                grades.add(su["grade"])
print(f"analysed {n_an} candles in {time.time() - t0:.0f}s, Setup 4 seen {len(found)} times")

a_any = analyze(st)
check("Setup 4 is always listed", [su["id"] for su in a_any.setups] == [1, 2, 3, 4], [su["id"] for su in a_any.setups])
check("every setup with a direction carries grade A/B/C", grades and grades <= {"A", "B", "C"}, sorted(grades))
check("Setup 4 produced at least once on simulated data", len(found) >= 1, len(found))
if found:
    seed, i, a, su = found[-1]
    classes = {f[3]["grade15"] for f in found}
    check("15-min class is A/B/C", classes <= {"A", "B", "C"} and classes, sorted(classes))
    check("Setup 4 has a grade too", su["grade"] in ("A", "B", "C"), su["grade"])
    check("entry is an H1 or H4 level", su["entry"]["tf"] in ("H1", "H4"), su["entry"]["name"])
    check("targets come from H1/H4 levels or liquidity",
          all(t["kind"] in ("roadblock", "liquidity") for t in su["targets"]), [t["name"] for t in su["targets"]])
    check("targets are on the profit side", all((t["price"] < su["entry"]["price"]) == (su["dir"] == "sell") for t in su["targets"]))
    check("valid only ~M15_ENTRY_MIN after the break", 0 < su["enter_by"] - su["cf_break_t"] <= config.M15_ENTRY_MIN * 60 + 1)
    text = fmt_report(a, {}, ["TEST"])
    check("report header shows grade and 15-min class", f"Grade {su['grade']} · 15-min class {su['grade15']}" in text)
    check("report has 15-min class line", "15-min class :" in text)
    print("\n".join(l for l in text.split("\n") if l.startswith("SETUP 4"))[:300])
# without any 15-min candles Setup 4 just waits
st2 = SymbolState("EURUSD"); st2.set_meta(P)
st2.upsert({k: v for k, v in build_payload(make_h1(), True)["tfs"].items() if k != "M15"})
a2 = analyze(st2)
s4 = next(su for su in a2.setups if su["id"] == 4)
check("no M15 data: Setup 4 waits with a hint", s4["status"] == "waiting" and "15-min" in s4["reason"], s4["reason"])
print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
