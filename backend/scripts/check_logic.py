"""Fast unit checks of the hierarchy rules (no data needed).  Run from backend/:  python scripts/check_logic.py"""
import os, sys
os.environ.setdefault("API_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS                       # noqa: E402
from app import zones                                          # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  {info}" if info != "" else ""))


def A(**dirs):
    return NS(stories={tf: (NS(dir=d) if d else None) for tf, d in dirs.items()})


def ctx(a, tf, trade, entries=None):
    return zones.context(a, tf, trade, entries)


# 1. nothing above the story line
c = ctx(A(MN1=None, W1=None, D1="bear"), "D1", "sell")
check("no parents: free, grade B", c["kind"] == "free" and zones.grade_from(c) == "B", c["label"])
# 2. everything agrees
c = ctx(A(MN1="bear", W1="bear", D1="bear"), "D1", "sell")
check("all agree: with the trend, grade A", c["kind"] == "with" and zones.grade_from(c) == "A", c["label"])
# 3. Daily sell inside a bullish Weekly: retracement of the Weekly
c = ctx(A(MN1=None, W1="bull", D1="bear"), "D1", "sell")
check("Daily bearish under Weekly bullish: retracement of the Weekly, grade C",
      c["kind"] == "retracement" and c["parent_tf"] == "W1" and zones.grade_from(c) == "C", c["label"])
# 4. parent agrees but the dominant trend is against
c = ctx(A(MN1="bull", W1="bear", D1="bear"), "D1", "sell")
check("parent agrees, Monthly against: with_parent, grade B, ceiling source is the Monthly",
      c["kind"] == "with_parent" and c["parent_tf"] == "MN1" and zones.grade_from(c) == "B", c["label"])
# 5. the immediate parent opposes: retracement even if the Monthly agrees
c = ctx(A(MN1="bear", W1="bull", D1="bear"), "D1", "sell")
check("Weekly bullish inside Monthly bearish, Daily sell: retracement of the Weekly",
      c["kind"] == "retracement" and c["parent_tf"] == "W1", c["label"])
# 6. same sell, same stories, but the entry zone tier decides the governing story line
stories = A(MN1="bull", W1="bear", D1="bull")
plain = ctx(stories, "H4", "sell", [{"tf": "H4", "overlap_tfs": []}])
strong = ctx(stories, "H4", "sell", [{"tf": "H4", "overlap_tfs": ["D1"]}])
check("plain 4-hour zone is governed by the Daily story line", plain["gov_tf"] == "D1" and plain["parent_tf"] == "D1", plain["label"])
check("4-hour zone overlapping a Daily level is governed by the Weekly story line",
      strong["gov_tf"] == "W1" and strong["parent_tf"] == "MN1", strong["label"])
check("governing-story wording explains why", "follows the Weekly" in strong["label"], strong["label"])
# 7. buy that agrees with every story line
c = ctx(A(MN1="bull", W1="bull", D1="bull"), "D1", "buy")
check("buy with all bullish: with, grade A", c["kind"] == "with" and zones.grade_from(c) == "A")
# 8. thesis diff in words
old = {"stories": {"W1": {"dir": "bear", "eff": 1}}, "anchor": "D1", "scenarios": [{"id": "A", "kind": "continuation", "price": 1.1, "status": "waiting"}]}
new = {"stories": {"W1": {"dir": "bull", "eff": 5}, "D1": {"dir": "bull", "eff": 6}}, "anchor": "D1",
       "scenarios": [{"id": "A", "kind": "continuation", "price": 1.2, "status": "zone reached"}]}
ch = zones.thesis_changes(old, new, 2)
check("thesis diff: story flip, new story line, zone move, status change",
      any("flipped from bearish to bullish" in x for x in ch) and any("Daily story line started" in x for x in ch)
      and any("zone moved" in x for x in ch) and any("zone reached" in x for x in ch), ch)
check("thesis diff: first thesis", zones.thesis_changes(None, new) == ["First thesis stored for this pair."])
check("thesis diff: unchanged", zones.thesis_changes(new, new) == ["No material change."])
print("\nALL LOGIC CHECKS PASSED" if ok else "\nSOME LOGIC CHECKS FAILED")
sys.exit(0 if ok else 1)
