"""Key levels, obstacles, strong zones, story-line status and the three setups.

Everything here follows the mentorship rules:
  * levels used by a story line:  Weekly story -> Daily levels, Daily story -> 4-hour (and 1-hour)
    levels, Monthly story -> Weekly levels, 4-hour story -> 1-hour levels
  * a level that SUPPORTS the story line is a KEY LEVEL (resistance for a bearish story, support
    for a bullish one); levels that oppose it are OBSTACLES (roadblocks / take-profit spots)
  * only the most relevant and most recent levels are kept (recent window, strength score, distance)
  * zones are STRONG where adjacent timeframes overlap: Monthly+Weekly, Weekly+Daily, Daily+4-hour,
    Daily+1-hour; the newest 4-hour level near a Daily zone ranks highest
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config
from .engine import (LTF_OF, PARENT_OF, RECENT_WINDOW, TF_NAME, TF_SEC, Level, Story, level_label,
                     strength)

H4S, D1S = TF_SEC["H4"], TF_SEC["D1"]
M15_MIN = 60                  # 15-min candles needed (same as state.M15_MIN)
PAIRS = [("MN1", "W1"), ("W1", "D1"), ("D1", "H4"), ("D1", "H1")]          # (higher, lower)
SERVES = {"MN1": ["MN1"], "W1": ["MN1", "W1"], "D1": ["W1", "D1"], "H4": ["D1", "H4"], "H1": ["D1", "H4"]}
N_KEY = {"MN1": 2, "W1": 3, "D1": 3, "H4": 3, "H1": 3}
N_OBS = {"MN1": 2, "W1": 2, "D1": 2, "H4": 2, "H1": 2}


@dataclass
class KeyLevel:
    tf: str
    role: str                    # "R" resistance / "S" support
    label: str                   # QM | OCL | Classic A/V
    price: float
    strength: int
    cls: str = "context"         # key level | obstacle | context
    serves: str = ""             # the story line these levels belong to (tf code)
    strong: bool = False
    overlap: str = ""            # what it overlaps, e.g. "Daily resistance (OCL) at 1.32224"
    newest: bool = False
    formed_t: int = 0
    touches: int = 0
    flips: int = 0

    @property
    def name(self) -> str:
        return f"{TF_NAME[self.tf]} {'resistance' if self.role == 'R' else 'support'} ({self.label})"

    def as_dict(self) -> dict:
        return dict(tf=self.tf, name=self.name, role=self.role, label=self.label, price=self.price,
                    strength=self.strength, cls=self.cls, serves=self.serves, strong=self.strong,
                    overlap=self.overlap, newest=self.newest, touches=self.touches, flips=self.flips,
                    formed_t=self.formed_t)


# ------------------------------------------------------------------ helpers
def overlap_pips(symbol: str, pip: float) -> float:
    if config.ZONE_OVERLAP_PIPS > 0:
        return config.ZONE_OVERLAP_PIPS
    s = symbol.upper()
    if s.startswith(("XAU", "GOLD")) or pip >= 1:
        return 25.0
    return 15.0 if "JPY" in s else 12.0


def atr(bars, n: int = 14) -> float:
    tail = bars[-n:] or bars
    return sum(b.h - b.l for b in tail) / max(1, len(tail))


def dir_word(d: str) -> str:
    return "bearish" if d == "bear" else "bullish"


def serving_story(stories: Dict[str, Optional[Story]], tf: str) -> Optional[Story]:
    for t in SERVES.get(tf, []):
        if stories.get(t):
            return stories[t]
    return None


# ------------------------------------------------------------------ selecting levels
def pick(a, tf: str, direction: Optional[str], n_key: int = 0, n_obs: int = 0) -> Dict[str, List[KeyLevel]]:
    """Most relevant + most recent levels of one timeframe for a story direction ('bear'/'bull'/None).
    Returns {"key": [...], "obstacles": [...]} (both nearest-first)."""
    bars = a.series.get(tf) or []
    levels: List[Level] = a.levels.get(tf) or []
    if not bars or not levels:
        return {"key": [], "obstacles": []}
    price, pip = a.price, a.pip
    n = len(bars)
    a14 = max(atr(bars), pip * 3)
    min_gap = config.MIN_GAP_PIPS * pip
    min_tgt = config.MIN_TARGET_PIPS * pip
    merge = max(2 * pip, 0.4 * a14)
    key_role = None if direction is None else ("R" if direction == "bear" else "S")

    def mk(lv: Level, cls: str) -> KeyLevel:
        return KeyLevel(tf, lv.role, level_label(lv), lv.price, strength(lv, n), cls, "", False, "", False,
                        lv.t, lv.touches, lv.flips)

    pool = [lv for lv in levels if n - 1 - lv.idx <= RECENT_WINDOW.get(tf, 100)]
    keys, obs = [], []
    for lv in pool:
        if direction is None:
            (keys if lv.price >= price else obs).append(mk(lv, "context"))
        elif lv.role == key_role:                       # supports the story line
            ok = lv.price >= price - min_gap if direction == "bear" else lv.price <= price + min_gap
            if ok:
                keys.append(mk(lv, "key level"))
        else:                                           # opposes it = roadblock / target
            ok = lv.price <= price - min_tgt if direction == "bear" else lv.price >= price + min_tgt
            if ok:
                obs.append(mk(lv, "obstacle"))

    def rank(kl: KeyLevel) -> float:
        return kl.strength - min(40.0, 10.0 * abs(kl.price - price) / a14)

    def thin(items: List[KeyLevel], cap: int) -> List[KeyLevel]:
        out: List[KeyLevel] = []
        for kl in sorted(items, key=rank, reverse=True):
            if all(abs(kl.price - o.price) > merge for o in out):
                out.append(kl)
                if len(out) >= cap:
                    break
        return sorted(out, key=lambda k: abs(k.price - price))

    keys = thin(keys, n_key or N_KEY[tf])
    obs = thin(obs, n_obs or N_OBS[tf])
    if keys:
        max(keys, key=lambda k: k.formed_t).newest = True
    return {"key": keys, "obstacles": obs}


def mark_strong(a, selected: Dict[str, List[KeyLevel]]) -> List[dict]:
    """Strong = a lower-timeframe level overlapping a higher-timeframe level of the same role."""
    ovl = overlap_pips(a.symbol, a.pip) * a.pip
    zones: List[dict] = []
    for hi_tf, lo_tf in PAIRS:
        hi_pool = [lv for lv in (a.levels.get(hi_tf) or [])
                   if len(a.series[hi_tf]) - 1 - lv.idx <= RECENT_WINDOW.get(hi_tf, 100)]
        for kl in selected.get(lo_tf, []):
            best = None
            for lv in hi_pool:
                if lv.role == kl.role and abs(lv.price - kl.price) <= ovl:
                    sc = strength(lv, len(a.series[hi_tf]))
                    if best is None or sc > best[0]:
                        best = (sc, lv)
            if best:
                lv = best[1]
                hi_name = (f"{TF_NAME[hi_tf]} {'resistance' if lv.role == 'R' else 'support'} "
                           f"({level_label(lv)}) {lv.price:.{a.digits}f}")
                if not kl.strong:
                    kl.strength = min(100, kl.strength + 10)
                kl.strong = True
                kl.overlap = (kl.overlap + " + " if kl.overlap else "") + hi_name
                zones.append(dict(low=min(kl.price, lv.price), high=max(kl.price, lv.price),
                                  tfs=[hi_tf, lo_tf], role=kl.role))
    # merge overlapping zones
    zones.sort(key=lambda z: z["low"])
    merged: List[dict] = []
    for z in zones:
        if merged and z["low"] <= merged[-1]["high"] + ovl and z["role"] == merged[-1]["role"]:
            m = merged[-1]
            m["high"] = max(m["high"], z["high"])
            m["tfs"] = sorted(set(m["tfs"] + z["tfs"]), key=lambda t: list(TF_NAME).index(t))
        else:
            merged.append(dict(z))
    for z in merged:
        z["label"] = " + ".join(TF_NAME[t] for t in z["tfs"])
    return sorted(merged, key=lambda z: -z["high"])


def select_all(a) -> None:
    """Fill a.selected (per timeframe, classified by the story line it serves) and a.zones."""
    sel: Dict[str, List[KeyLevel]] = {}
    for tf in a.levels:
        s = serving_story(a.stories, tf)
        d = s.dir if s else None
        got = pick(a, tf, d)
        items = got["key"] + got["obstacles"]
        for kl in items:
            kl.serves = s.tf if s else ""
        sel[tf] = sorted(items, key=lambda k: -k.price)
    a.zones = mark_strong(a, sel)
    a.selected = sel


# ------------------------------------------------------------------ liquidity
def asia_window(h4, ts: int):
    """Asia = the first two 4-hour candles of the broker's day. Returns (start, end, bars)."""
    day = ts // 86400
    bars = [b for b in h4 if b.t // 86400 == day][:2]
    if bars:
        return bars[0].t, bars[0].t + 2 * H4S, bars
    return day * 86400, day * 86400 + 2 * H4S, []


def is_asia(h4, ts: int) -> bool:
    s, e, _ = asia_window(h4, ts)
    return s <= ts < e


def liquidity(a) -> dict:
    d1, w1, h4 = a.series["D1"], a.series["W1"], a.series["H4"]
    out = {"prev_day": None, "prev_week": None, "asia": None}
    if d1:
        out["prev_day"] = (d1[-1].h, d1[-1].l)
    if w1:
        out["prev_week"] = (w1[-1].h, w1[-1].l)
    if h4:
        _, _, bars = asia_window(h4, h4[-1].t)
        if len(bars) == 2:
            out["asia"] = (max(b.h for b in bars), min(b.l for b in bars))
    return out


# ------------------------------------------------------------------ labels and status
def label_for(a, tf: str, price: float, kind: str) -> str:
    qm = price in a.qm_prices.get(tf, ())
    return "QM" if qm else "OCL" if kind == "OC" else "Classic A/V"


def px(a, v: float) -> str:
    return f"{v:.{a.digits}f}"


def story_status(a, tf: str) -> Optional[dict]:
    """'Where we are now' for one story line, in the mentor's words."""
    s = a.stories.get(tf)
    if not s:
        return None
    ltf, nm, d = LTF_OF[tf], TF_NAME[tf], dir_word(s.dir)
    bars = a.series[tf]
    opp = [r for r in a.recent_rej.get(tf, []) if r.k > s.k and r.dir != s.dir]
    if opp and opp[-1].k >= len(bars) - 3:
        r = opp[-1]
        role = "support" if r.dir == "bull" else "resistance"
        closed = "above" if r.dir == "bull" else "below"
        par = PARENT_OF.get(tf)
        ps = a.stories.get(par) if par else None
        clause = ""
        if ps and ((ps.dir == "bear" and role == "support") or (ps.dir == "bull" and role == "resistance")):
            clause = f" (a {TF_NAME[par]} story-line obstacle)"
        text = (f"{nm} is still {d}. It hit a {nm} {role} ({label_for(a, tf, r.price, r.kind)}) at "
                f"{px(a, r.price)}{clause} and closed {closed} it, but there is no {TF_NAME[ltf]} "
                f"breakout, so it remains {d}.")
        return dict(tf=tf, kind="at_risk", text=text)
    got = pick(a, ltf, s.dir)
    near = got["obstacles"][:1]
    if near:
        o = near[0]
        dist = abs(o.price - a.price)
        if dist <= 1.2 * max(atr(a.series[ltf]), a.pip * 3):
            return dict(tf=tf, kind="at_obstacle", text=(
                f"{nm} remains {d}. Price is approaching the {o.name} at {px(a, o.price)}, an obstacle for "
                f"this story line about {dist / a.pip:.0f} pips away: expect a possible pause or pullback "
                f"there. The story line only ends if {TF_NAME[ltf]} gives a "
                f"{'bullish' if s.dir == 'bear' else 'bearish'} breakout."))
    tail = f" Next obstacle: {near[0].name} at {px(a, near[0].price)}." if near else ""
    return dict(tf=tf, kind="active", text=f"{nm} remains {d}: price is moving with the story line.{tail}")


def story_reason(a, tf: str, s: Story) -> str:
    """Why a story line started, mentor style."""
    nm, ltf = TF_NAME[tf], TF_NAME[LTF_OF[tf]]
    bear = s.dir == "bear"
    role = "resistance" if bear else "support"
    lab = label_for(a, tf, s.level, s.level_kind)
    side = "below" if bear else "above"
    when = "in the same candle" if s.same_bar else "in the following candle"
    return (f"{nm} rejected the {nm} {role} ({lab}) at {px(a, s.level)} with a wick and closed {side} it, "
            f"and {ltf} gave a {'bearish' if bear else 'bullish'} breakout (QM) at {px(a, s.qm_level)} {when}.")


# ------------------------------------------------------------------ setups
def grade_for(a, d: str) -> str:
    """Setup grade (same for every setup): A = Weekly and Daily story lines agree, B = only Daily agrees, C = otherwise."""
    want = "bear" if d == "sell" else "bull"
    d1, w1 = a.stories.get("D1"), a.stories.get("W1")
    d_ok = bool(d1 and d1.dir == want)
    w_ok = bool(w1 and w1.dir == want)
    return "A" if d_ok and w_ok else "B" if d_ok else "C"


def class15_for(a, d: str, entry: dict) -> tuple:
    """15-min class (Setup 4 only): A = the 4-hour story line agrees AND the entry zone is a STRONG zone,
    B = one of the two, C = neither. Returns (letter, short reason)."""
    want = "bear" if d == "sell" else "bull"
    hs = a.stories.get("H4")
    h4_ok = bool(hs and hs.dir == want)
    strong = bool(entry and entry.get("strong"))
    letter = "A" if h4_ok and strong else "B" if h4_ok or strong else "C"
    why = ("4-hour story line agrees" if h4_ok else "4-hour story line " + ("is against it" if hs else "not set")) + "; " + \
          ("entry is a STRONG zone" if strong else "entry zone is not an overlap zone")
    return letter, why


def _m15_entry(a, d: str, rej_price: float) -> Optional[dict]:
    """Entry level for Setup 4: the strongest H1 / H4 key level that the 1-hour rejection actually happened at."""
    story_d = "bear" if d == "sell" else "bull"
    got = {tf: pick(a, tf, story_d) for tf in ("H1", "H4")}
    mark_strong(a, {tf: got[tf]["key"] for tf in got})
    tol = (config.M15_ZONE_PIPS or 1.5 * overlap_pips(a.symbol, a.pip)) * a.pip
    pool: List[KeyLevel] = []
    for tf in got:
        for kl in got[tf]["key"]:
            kl.serves = "H1"
            if abs(kl.price - rej_price) <= tol:
                pool.append(kl)
    if not pool:
        return None
    best = max(pool, key=lambda k: (k.strong, k.tf == "H4", k.strength))
    return dict(level=best.as_dict(), refine=None)


def _aligned(a, d: str) -> Dict[str, Optional[bool]]:
    want = "bear" if d == "sell" else "bull"
    out = {}
    for tf in ("MN1", "W1", "D1", "H4"):
        s = a.stories.get(tf)
        out[tf] = None if not s else (s.dir == want)
    return out


def _validity(a, setup_id: int, d: str, al: Dict[str, Optional[bool]], enter_by: int) -> dict:
    d1_ok, w1_ok = al.get("D1") is True, al.get("W1") is True
    if d1_ok and w1_ok:
        txt = ("until a new opposite Daily story line starts. Weekly and Daily both agree, so holding "
               "toward the Daily key levels is reasonable")
    elif d1_ok:
        txt = ("until the Daily candle closes, then re-check. Daily agrees but Weekly is "
               + ("against it (this is a pullback)" if al.get("W1") is False else "not set"))
    else:
        txt = "only for the entry window above: it does not agree with the Daily story line, so take quick profit"
    return dict(enter_by=enter_by, stays_valid=txt)


def _ceil_h4(a, ts: int) -> int:
    anchor = a.series["H4"][-1].t
    k = -(-(ts - anchor) // H4S)          # ceil
    return anchor + k * H4S


def _targets(a, d: str, entry: float, groups: List[tuple]) -> List[dict]:
    """groups = [(source_tf, 'roadblock'|'daily key level'), ...] searched in order, then liquidity."""
    pip, bear = a.pip, d == "sell"
    min_tgt = config.MIN_TARGET_PIPS * pip
    story_d = "bear" if bear else "bull"
    cands: List[dict] = []

    def beyond(p: float) -> bool:
        return p <= entry - min_tgt if bear else p >= entry + min_tgt

    for tf, kind in groups:
        for kl in pick(a, tf, story_d)["obstacles"]:
            if beyond(kl.price):
                if kind == "roadblock":
                    adv = "roadblock: pay yourself here or move to break-even"
                else:
                    adv = "Daily key level in the story-line direction: main target when Daily (and Weekly) agree"
                cands.append(dict(name=kl.name, price=kl.price, kind=kind, strength=kl.strength, advice=adv))
    liq = a.liquidity
    side = 1 if bear else 0                  # low for sells, high for buys
    for key, nm in (("prev_day", "Previous day"), ("prev_week", "Previous week"), ("asia", "Asian")):
        v = liq.get(key)
        if v:
            p = v[side]
            if beyond(p):
                cands.append(dict(name=f"{nm} {'low' if bear else 'high'}", price=p, kind="liquidity",
                                  strength=0, advice="liquidity: expected to be swept because of the story line"))
    cands.sort(key=lambda c: abs(c["price"] - entry))
    out: List[dict] = []
    for c in cands:
        if all(abs(c["price"] - o["price"]) > 2 * pip for o in out):
            c["pips"] = round(abs(c["price"] - entry) / pip, 1)
            out.append(c)
    for i, c in enumerate(out[:4], 1):
        c["n"] = i
    return out[:4]


def _entry_zone(a, d: str, entry_tf: str, refine_tf: Optional[str], extra_tfs: List[str]) -> Optional[dict]:
    """Choose the entry level: strong + newest + strongest, close to price. Returns dict or None."""
    story_d = "bear" if d == "sell" else "bull"
    pool: List[KeyLevel] = []
    got = {entry_tf: pick(a, entry_tf, story_d)}
    for tf in extra_tfs:
        got[tf] = pick(a, tf, story_d)
    mark_strong(a, {tf: got[tf]["key"] for tf in got})
    for tf in got:
        for kl in got[tf]["key"]:
            kl.serves = entry_tf
            pool.append(kl)
    if not pool:
        return None
    a14 = max(atr(a.series[entry_tf]), a.pip * 3)
    max_dist = 2.5 * a14

    def score(kl: KeyLevel) -> float:
        return kl.strength + (25 if kl.strong else 0) + (15 if kl.newest else 0) - 10 * abs(kl.price - a.price) / a14

    pool = [k for k in pool if abs(k.price - a.price) <= max_dist] or []
    if not pool:
        return None
    best = max(pool, key=score)
    out = dict(level=best.as_dict(), refine=None)
    if refine_tf and best.tf != refine_tf:
        ovl = overlap_pips(a.symbol, a.pip) * a.pip
        rf = pick(a, refine_tf, story_d)["key"]
        mark_strong(a, {refine_tf: rf})
        close = [k for k in rf if abs(k.price - best.price) <= ovl]
        if close:
            r = max(close, key=lambda k: (k.strong, k.strength))
            out["refine"] = r.as_dict()
    return out


def build_setups(a, confirms, confirms15=None) -> List[dict]:
    """Setup 1: Daily story line | Setup 2: 4-hour story line | Setup 3: confirmation entry |
    Setup 4: 15-minute entry at an H1/H4 level (only when the feeder sends 15-min candles)."""
    h4 = a.series["H4"]
    asia = is_asia(h4, a.last_t)
    out: List[dict] = []

    def base(i, title):
        return dict(id=i, title=title, status="waiting", reason="", dir=None, aligned={}, entry=None,
                    refine=None, targets=[], enter_by=None, stays_valid="", invalid_if=None, asia=asia,
                    key="", story_tf="", grade="", grade15="", grade15_why="")

    def finish(su, d, entry_info, enter_by, groups, extra=None):
        su["dir"] = d
        su["grade"] = grade_for(a, d)
        su["aligned"] = _aligned(a, d)
        if not entry_info:
            su["reason"] = "no valid entry level near price yet"
            return su
        lvl = entry_info["level"]
        su["entry"], su["refine"] = lvl, entry_info["refine"]
        ep = (entry_info["refine"] or lvl)["price"]
        su["targets"] = _targets(a, d, ep, groups)
        su["invalid_if"] = dict(price=lvl["price"] + (1 if d == "sell" else -1) * overlap_pips(a.symbol, a.pip) * a.pip,
                                text=("a 1-hour candle closes " + ("above" if d == "sell" else "below")))
        v = _validity(a, su["id"], d, su["aligned"], enter_by)
        su["enter_by"], su["stays_valid"] = v["enter_by"], v["stays_valid"]
        su["status"] = "ready" if a.last_t <= enter_by else "late"
        if su["status"] == "late":
            su["reason"] = "the entry window has passed: wait for the next pullback or rejection"
        if asia:
            su["status"] = "asia"
            su["reason"] = "Asia (first two 4-hour candles): do not enter now"
        if a.limits.get("blocked"):
            su["status"] = "blocked"
            su["reason"] = a.limits["blocked"]
        if extra:
            su.update(extra)
        return su

    # ---- Setup 1: Daily story line, 4-hour entry, refine to 1-hour (D+H1 strong levels also allowed)
    s1 = base(1, "Daily story line: 4-hour entry, refine to 1-hour")
    ds = a.stories.get("D1")
    if not ds:
        s1["reason"] = "no active Daily story line"
    else:
        d = "sell" if ds.dir == "bear" else "buy"
        info = _entry_zone(a, d, "H4", "H1", ["H1"])
        s1["story_tf"] = "D1"
        d1b = a.series["D1"]
        enter_by = d1b[-1].t + 2 * D1S if d1b else a.last_t + D1S
        finish(s1, d, info, enter_by, [("H4", "roadblock"), ("D1", "daily key level")])
        s1["key"] = f"S1|{d}|{ds.eff}|{round((s1['entry'] or {}).get('price', 0), 6)}"
    out.append(s1)

    # ---- Setup 2: 4-hour story line, 1-hour entry
    s2 = base(2, "4-hour story line: 1-hour entry")
    hs = a.stories.get("H4")
    if not hs:
        s2["reason"] = ("no active 4-hour story line yet: it needs a 4-hour rejection of a 4-hour level, "
                        "then a 1-hour breakout (QM)")
    else:
        d = "sell" if hs.dir == "bear" else "buy"
        info = _entry_zone(a, d, "H1", None, ["H4"])
        s2["story_tf"] = "H4"
        finish(s2, d, info, _ceil_h4(a, hs.eff) + H4S, [("H1", "roadblock"), ("H4", "roadblock"), ("D1", "daily key level")])
        s2["key"] = f"S2|{d}|{hs.eff}"
    out.append(s2)

    # ---- Setup 3: confirmation = 4-hour rejection + 1-hour break (QM or open-close level)
    s3 = base(3, "Confirmation entry: 4-hour rejection, then 1-hour break")
    cf = max(confirms, key=lambda c: c.break_t) if confirms else None
    if not cf:
        s3["reason"] = "no 4-hour rejection followed by a 1-hour break yet"
    elif a.last_t > cf.h4_t + 2 * H4S:
        s3["reason"] = "last confirmation expired (it must happen inside the next 4-hour candle)"
    else:
        d = cf.dir
        info = _entry_zone(a, d, "H4", "H1", ["H1"])
        s3["story_tf"] = "H4"
        brk = "a structure break (QM)" if cf.break_type == "QM" else "an open-close level (OCL) break"
        finish(s3, d, info, cf.h4_t + 2 * H4S, [("H1", "roadblock"), ("H4", "roadblock"), ("D1", "daily key level")],
               extra=dict(cf_text=(f"4-hour rejected the 4-hour {'resistance' if d == 'sell' else 'support'} at "
                                   f"{px(a, cf.rej_price)} and 1-hour gave {brk} at {px(a, cf.break_level)}"),
                          cf_break_t=cf.break_t, cf_h4_t=cf.h4_t))
        s3["key"] = f"S3|{d}|{cf.h4_t}|{cf.break_t}"
        if hs and hs.bar_t == cf.h4_t and hs.dir == ("bear" if d == "sell" else "bull"):
            s3["status"] = "upgraded"
            s3["reason"] = "the 4-hour story line started from this rejection: see Setup 2"
    out.append(s3)

    # ---- Setup 4: 15-minute entry = 1-hour rejection at an H1/H4 key level, then a 15-min break (QM or OCL)
    if config.ENABLE_M15:
        s4 = base(4, "15-min entry: 1-hour rejection at an H1/H4 level, then 15-min break")
        cf4 = max(confirms15, key=lambda c: c.break_t) if confirms15 else None
        if len(a.series.get("M15") or []) < M15_MIN:
            s4["reason"] = "no 15-min candles from MT5 yet (use SNR_Feeder v1.2 and press Sync now)"
        elif not cf4:
            s4["reason"] = "no 1-hour rejection followed by a 15-min break yet"
        elif a.last_t > cf4.h4_t + 3600 + int(config.CONFIRM_WINDOW_M15_H * 3600):
            s4["reason"] = "last 15-min confirmation expired"
        else:
            d = cf4.dir
            s4["dir"], s4["grade"], s4["aligned"], s4["story_tf"] = d, grade_for(a, d), _aligned(a, d), "H1"
            info = _m15_entry(a, d, cf4.rej_price)
            if not info:
                s4["reason"] = (f"the 1-hour rejection at {px(a, cf4.rej_price)} was not at an H1/H4 key level "
                                f"for a {'sell' if d == 'sell' else 'buy'}: no 15-min setup")
            else:
                brk = "a structure break (QM)" if cf4.break_type == "QM" else "an open-close level (OCL) break"
                enter_by = cf4.break_t + config.M15_ENTRY_MIN * 60
                finish(s4, d, info, enter_by, [("H1", "roadblock"), ("H4", "roadblock")],
                       extra=dict(cf_text=(f"1-hour rejected the 1-hour {'resistance' if d == 'sell' else 'support'} at "
                                           f"{px(a, cf4.rej_price)} and 15-min gave {brk} at {px(a, cf4.break_level)}"),
                                  cf_break_t=cf4.break_t, cf_h4_t=cf4.h4_t,
                                  stays_valid=(f"for about {config.M15_ENTRY_MIN} minutes after the 15-min break, and only while "
                                               f"no 1-hour candle closes beyond the zone. Short-term trade: pay yourself at the "
                                               f"1-hour / 4-hour roadblocks.")))
                s4["grade15"], s4["grade15_why"] = class15_for(a, d, s4["entry"])
                s4["key"] = f"S4|{d}|{cf4.h4_t}|{cf4.break_t}"
        out.append(s4)
    return out


# ------------------------------------------------------------------ levels listed per story line
STORY_LEVEL_TFS = {"MN1": ["W1"], "W1": ["D1"], "D1": ["H4", "H1"], "H4": ["H1"]}


def story_levels(a, story_tf: str, direction: str, n_key: int = 2, n_obs: int = 1) -> Dict[str, List[KeyLevel]]:
    """The key levels and obstacles a story line uses, e.g. Daily story -> 4-hour and 1-hour levels."""
    sel: Dict[str, List[KeyLevel]] = {}
    for tf in STORY_LEVEL_TFS[story_tf]:
        if tf not in a.levels:
            continue
        got = pick(a, tf, direction, n_key, n_obs)
        for kl in got["key"] + got["obstacles"]:
            kl.serves = story_tf
        sel[tf] = got["key"] + got["obstacles"]
    mark_strong(a, sel)
    return {tf: sorted(v, key=lambda k: -k.price) for tf, v in sel.items()}
