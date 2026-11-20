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
from .engine import (
    LTF_OF,
    PARENT_OF,
    RECENT_WINDOW,
    TF_NAME,
    TF_SEC,
    Level,
    Story,
    level_label,
    strength,
)

H4S, D1S = TF_SEC["H4"], TF_SEC["D1"]
PAIRS = [("MN1", "W1"), ("W1", "D1"), ("D1", "H4"), ("D1", "H1")]  # (higher, lower)
SERVES = {
    "MN1": ["MN1"],
    "W1": ["MN1", "W1"],
    "D1": ["W1", "D1"],
    "H4": ["D1", "H4"],
    "H1": ["D1", "H4"],
}
N_KEY = {"MN1": 2, "W1": 3, "D1": 3, "H4": 3, "H1": 3}
N_OBS = {"MN1": 2, "W1": 2, "D1": 2, "H4": 2, "H1": 2}


@dataclass
class KeyLevel:
    tf: str
    role: str  # "R" resistance / "S" support
    label: str  # QM | OCL | Classic A/V
    price: float
    strength: int
    cls: str = "context"  # key level | obstacle | context
    serves: str = ""  # the story line these levels belong to (tf code)
    strong: bool = False
    overlap: str = ""  # what it overlaps, e.g. "Daily resistance (OCL) at 1.32224"
    newest: bool = False
    formed_t: int = 0
    touches: int = 0
    flips: int = 0
    overlap_tfs: List[str] = field(
        default_factory=list
    )  # which higher timeframes it overlaps

    @property
    def name(self) -> str:
        return f"{TF_NAME[self.tf]} {'resistance' if self.role == 'R' else 'support'} ({self.label})"

    def as_dict(self) -> dict:
        return dict(
            tf=self.tf,
            name=self.name,
            role=self.role,
            label=self.label,
            price=self.price,
            strength=self.strength,
            cls=self.cls,
            serves=self.serves,
            strong=self.strong,
            overlap=self.overlap,
            overlap_tfs=list(self.overlap_tfs),
            newest=self.newest,
            touches=self.touches,
            flips=self.flips,
            formed_t=self.formed_t,
        )


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
def pick(
    a, tf: str, direction: Optional[str], n_key: int = 0, n_obs: int = 0
) -> Dict[str, List[KeyLevel]]:
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
        return KeyLevel(
            tf,
            lv.role,
            level_label(lv),
            lv.price,
            strength(lv, n),
            cls,
            "",
            False,
            "",
            False,
            lv.t,
            lv.touches,
            lv.flips,
        )

    pool = [lv for lv in levels if n - 1 - lv.idx <= RECENT_WINDOW.get(tf, 100)]
    keys, obs = [], []
    for lv in pool:
        if direction is None:
            (keys if lv.price >= price else obs).append(mk(lv, "context"))
        elif lv.role == key_role:  # supports the story line
            ok = (
                lv.price >= price - min_gap
                if direction == "bear"
                else lv.price <= price + min_gap
            )
            if ok:
                keys.append(mk(lv, "key level"))
        else:  # opposes it = roadblock / target
            ok = (
                lv.price <= price - min_tgt
                if direction == "bear"
                else lv.price >= price + min_tgt
            )
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
        hi_pool = [
            lv
            for lv in (a.levels.get(hi_tf) or [])
            if len(a.series[hi_tf]) - 1 - lv.idx <= RECENT_WINDOW.get(hi_tf, 100)
        ]
        for kl in selected.get(lo_tf, []):
            best = None
            for lv in hi_pool:
                if lv.role == kl.role and abs(lv.price - kl.price) <= ovl:
                    sc = strength(lv, len(a.series[hi_tf]))
                    if best is None or sc > best[0]:
                        best = (sc, lv)
            if best:
                lv = best[1]
                hi_name = (
                    f"{TF_NAME[hi_tf]} {'resistance' if lv.role == 'R' else 'support'} "
                    f"({level_label(lv)}) {lv.price:.{a.digits}f}"
                )
                if not kl.strong:
                    kl.strength = min(100, kl.strength + 10)
                kl.strong = True
                if hi_tf not in kl.overlap_tfs:
                    kl.overlap_tfs.append(hi_tf)
                kl.overlap = (kl.overlap + " + " if kl.overlap else "") + hi_name
                zones.append(
                    dict(
                        low=min(kl.price, lv.price),
                        high=max(kl.price, lv.price),
                        tfs=[hi_tf, lo_tf],
                        role=kl.role,
                    )
                )
    # merge overlapping zones
    zones.sort(key=lambda z: z["low"])
    merged: List[dict] = []
    for z in zones:
        if (
            merged
            and z["low"] <= merged[-1]["high"] + ovl
            and z["role"] == merged[-1]["role"]
        ):
            m = merged[-1]
            m["high"] = max(m["high"], z["high"])
            m["tfs"] = sorted(
                set(m["tfs"] + z["tfs"]), key=lambda t: list(TF_NAME).index(t)
            )
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


def is_killzone(ts: int) -> bool:
    hour = (ts % 86400) // 3600
    return (6 <= hour < 10) or (11 <= hour < 16)


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
        if ps and (
            (ps.dir == "bear" and role == "support")
            or (ps.dir == "bull" and role == "resistance")
        ):
            clause = f" (a {TF_NAME[par]} story-line obstacle)"
        text = (
            f"{nm} is still {d}. It hit a {nm} {role} ({label_for(a, tf, r.price, r.kind)}) at "
            f"{px(a, r.price)}{clause} and closed {closed} it, but there is no {TF_NAME[ltf]} "
            f"breakout, so it remains {d}."
        )
        return dict(tf=tf, kind="at_risk", text=text)
    got = pick(a, ltf, s.dir)
    near = got["obstacles"][:1]
    if near:
        o = near[0]
        dist = abs(o.price - a.price)
        if dist <= 1.2 * max(atr(a.series[ltf]), a.pip * 3):
            return dict(
                tf=tf,
                kind="at_obstacle",
                text=(
                    f"{nm} remains {d}. Price is approaching the {o.name} at {px(a, o.price)}, an obstacle for "
                    f"this story line about {dist / a.pip:.0f} pips away: expect a possible pause or pullback "
                    f"there. The story line only ends if {TF_NAME[ltf]} gives a "
                    f"{'bullish' if s.dir == 'bear' else 'bearish'} breakout."
                ),
            )
    tail = f" Next obstacle: {near[0].name} at {px(a, near[0].price)}." if near else ""
    return dict(
        tf=tf,
        kind="active",
        text=f"{nm} remains {d}: price is moving with the story line.{tail}",
    )


def story_reason(a, tf: str, s: Story) -> str:
    """Why a story line started, mentor style."""
    nm, ltf = TF_NAME[tf], TF_NAME[LTF_OF[tf]]
    bear = s.dir == "bear"
    role = "resistance" if bear else "support"
    lab = label_for(a, tf, s.level, s.level_kind)
    side = "below" if bear else "above"
    when = "in the same candle" if s.same_bar else "in the following candle"
    return (
        f"{nm} rejected the {nm} {role} ({lab}) at {px(a, s.level)} with a wick and closed {side} it, "
        f"and {ltf} gave a {'bearish' if bear else 'bullish'} breakout (QM) at {px(a, s.qm_level)} {when}."
    )


# ------------------------------------------------------------------ setups
def _aligned(a, d: str) -> Dict[str, Optional[bool]]:
    want = "bear" if d == "sell" else "bull"
    out = {}
    for tf in ("MN1", "W1", "D1", "H4"):
        s = a.stories.get(tf)
        out[tf] = None if not s else (s.dir == want)
    return out


def _validity(
    a, story_tf: str, ctx: dict, enter_by: int, ceiling: Optional["KeyLevel"]
) -> dict:
    nm = TF_NAME[story_tf]
    if ctx["kind"] == "retracement":
        pn = TF_NAME[ctx["parent_tf"]]
        cl = f" ({ceiling.name} at {px(a, ceiling.price)})" if ceiling else ""
        txt = (
            f"until price reaches the retracement ceiling{cl} or the {pn} story line ends. This trade goes against "
            f"the {pn} story line, so it is a retracement: take profit at the ceiling and do not hold beyond it "
            f"unless the {pn} story line itself reverses"
        )
    elif ctx["kind"] == "with_parent":
        pn = TF_NAME[ctx["parent_tf"]]
        cl = f" ({ceiling.name} at {px(a, ceiling.price)})" if ceiling else ""
        txt = (
            f"until the {nm} candle closes, then re-check. It agrees with the {TF_NAME[ctx['gov_tf']]} story line, "
            f"but the {pn} story line is against it higher up: take profit at the ceiling{cl} and do not expect "
            f"more than the {pn} story line's entry zone"
        )
    elif ctx["kind"] == "with":
        txt = (
            f"until a new opposite {nm} story line starts. It agrees with every higher story line, so holding "
            f"toward the higher key levels is reasonable"
        )
    else:
        txt = "for the entry window above only: there is no higher story line to confirm it, so treat it as intraday"
    return dict(enter_by=enter_by, stays_valid=txt)


def _ceil_h4(a, ts: int) -> int:
    anchor = a.series["H4"][-1].t
    k = -(-(ts - anchor) // H4S)  # ceil
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
                elif kind == "weekly target zone":
                    adv = "Weekly obstacle: macro target zone from Monthly/Weekly storyline"
                else:
                    adv = f"{TF_NAME[tf]} key level in the story-line direction: main target when the higher story lines agree"

                cands.append(
                    dict(
                        name=kl.name,
                        price=kl.price,
                        kind=kind,
                        strength=kl.strength,
                        advice=adv,
                    )
                )

    liq = a.liquidity
    side = 1 if bear else 0  # low for sells, high for buys
    for key, nm in (
        ("prev_day", "Previous day"),
        ("prev_week", "Previous week"),
        ("asia", "Asian"),
    ):
        v = liq.get(key)
        if v:
            p = v[side]
            if beyond(p):
                cands.append(
                    dict(
                        name=f"{nm} {'low' if bear else 'high'}",
                        price=p,
                        kind="liquidity",
                        strength=0,
                        advice="liquidity: expected to be swept because of the story line",
                    )
                )
    cands.sort(key=lambda c: abs(c["price"] - entry))
    out: List[dict] = []
    for c in cands:
        if all(abs(c["price"] - o["price"]) > 2 * pip for o in out):
            c["pips"] = round(abs(c["price"] - entry) / pip, 1)
            out.append(c)
    for i, c in enumerate(out[:4], 1):
        c["n"] = i
    return out[:4]


def _entry_zone(
    a, d: str, entry_tf: str, refine_tf: Optional[str], extra_tfs: List[str]
) -> Optional[dict]:
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
        return (
            kl.strength
            + (25 if kl.strong else 0)
            + (15 if kl.newest else 0)
            - 10 * abs(kl.price - a.price) / a14
        )

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


def build_setups(a, confirms) -> List[dict]:
    """Setup 1: Daily story line | Setup 2: 4-hour story line | Setup 3: confirmation entry."""
    h4 = a.series["H4"]
    asia = is_asia(h4, a.last_t)
    out: List[dict] = []

    def base(i, title):
        return dict(
            id=i,
            title=title,
            status="waiting",
            reason="",
            dir=None,
            aligned={},
            entry=None,
            refine=None,
            targets=[],
            enter_by=None,
            stays_valid="",
            invalid_if=None,
            asia=asia,
            key="",
            story_tf="",
        )

    def finish(su, d, entry_info, enter_by, groups, extra=None):
        su["dir"] = d
        su["aligned"] = _aligned(a, d)
        if not entry_info:
            su["reason"] = "no valid entry level near price yet"
            return su
        lvl = entry_info["level"]
        su["entry"], su["refine"] = lvl, entry_info["refine"]
        ep = (entry_info["refine"] or lvl)["price"]
        ctx = context(a, su["story_tf"], d, [lvl, entry_info["refine"]])
        ceil = _ceiling(a, d, ctx)
        su["context"], su["ceiling"] = ctx, (ceil.as_dict() if ceil else None)
        su["targets"] = _cap(a, d, _targets(a, d, ep, groups), ceil, ctx, ep)
        is_gold = a.symbol.upper().startswith(("XAU", "GOLD"))
        max_sl_pips = config.GOLD_SL_MAX_PIPS if is_gold else config.FX_SL_MAX_PIPS
        default_tp_pips = config.GOLD_TP_PIPS if is_gold else config.FX_TP_PIPS
        sl_dist = max_sl_pips * a.pip
        sl_price = ep + sl_dist if d == "sell" else ep - sl_dist
        fixed_tp = ep - (default_tp_pips * a.pip) if d == "sell" else ep + (default_tp_pips * a.pip)
        su["sl"] = round(sl_price, a.digits)
        su["sl_pips"] = round(sl_dist / a.pip, 1)
        su["fixed_tp"] = round(fixed_tp, a.digits)
        su["invalid_if"] = dict(
            price=su["sl"],
            text=f"SL ({su['sl_pips']} pips max) or 1-hour close {'above' if d == 'sell' else 'below'}",
        )
        v = _validity(a, su["story_tf"], ctx, enter_by, ceil)
        su["enter_by"], su["stays_valid"] = v["enter_by"], v["stays_valid"]
        su["status"] = "ready" if a.last_t <= enter_by else "late"
        if su["status"] == "late":
            su["reason"] = (
                "the entry window has passed: wait for the next pullback or rejection"
            )
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
        groups = [("H4", "roadblock"), ("D1", "daily key level")]
        want = "bear" if d == "sell" else "bull"
        if any(a.stories.get(t) and a.stories[t].dir == want for t in ("MN1", "W1")):
            groups.append(("W1", "weekly target zone"))
        finish(s1, d, info, enter_by, groups)

        s1["key"] = f"S1|{d}|{ds.eff}|{round((s1['entry'] or {}).get('price', 0), 6)}"
    out.append(s1)

    # ---- Setup 2: 4-hour story line, 1-hour entry
    s2 = base(2, "4-hour story line: 1-hour entry")
    hs = a.stories.get("H4")
    if not hs:
        s2["reason"] = (
            "no active 4-hour story line yet: it needs a 4-hour rejection of a 4-hour level, "
            "then a 1-hour breakout (QM)"
        )
    else:
        d = "sell" if hs.dir == "bear" else "buy"
        info = _entry_zone(a, d, "H1", None, ["H4"])
        s2["story_tf"] = "H4"
        finish(
            s2,
            d,
            info,
            _ceil_h4(a, hs.eff) + H4S,
            [("H1", "roadblock"), ("H4", "roadblock"), ("D1", "daily key level")],
        )
        s2["key"] = f"S2|{d}|{hs.eff}"
    out.append(s2)

    # ---- Setup 3: confirmation = 4-hour rejection + 1-hour break (QM or open-close level)
    s3 = base(3, "Confirmation entry: 4-hour rejection, then 1-hour break")
    cf = max(confirms, key=lambda c: c.break_t) if confirms else None
    if not cf:
        s3["reason"] = "no 4-hour rejection followed by a 1-hour break yet"
    elif a.last_t > cf.h4_t + 2 * H4S:
        s3["reason"] = (
            "last confirmation expired (it must happen inside the next 4-hour candle)"
        )
    else:
        d = cf.dir
        info = _entry_zone(a, d, "H4", "H1", ["H1"])
        s3["story_tf"] = "H4"
        brk = (
            "a structure break (QM)"
            if cf.break_type == "QM"
            else "an open-close level (OCL) break"
        )
        kz = is_killzone(a.last_t)
        finish(
            s3,
            d,
            info,
            cf.h4_t + 2 * H4S,
            [("H1", "roadblock"), ("H4", "roadblock"), ("D1", "daily key level")],
            extra=dict(
                cf_text=(
                    f"4-hour rejected the 4-hour {'resistance' if d == 'sell' else 'support'} at "
                    f"{px(a, cf.rej_price)} and 1-hour gave {brk} at {px(a, cf.break_level)}"
                    + (" [Killzone]" if kz else " [Outside Killzone]")
                ),
                cf_break_t=cf.break_t,
                cf_h4_t=cf.h4_t,
                in_killzone=kz,
            ),
        )
        s3["key"] = f"S3|{d}|{cf.h4_t}|{cf.break_t}"
    out.append(s3)
    return out


# ------------------------------------------------------------------ levels listed per story line
STORY_LEVEL_TFS = {"MN1": ["W1"], "W1": ["D1"], "D1": ["H4", "H1"], "H4": ["H1"]}


def story_levels(
    a, story_tf: str, direction: str, n_key: int = 2, n_obs: int = 1
) -> Dict[str, List[KeyLevel]]:
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


# ================================================================== hierarchy: trend or retracement?
CHAIN = {
    "W1": ["MN1"],
    "D1": ["W1", "MN1"],
    "H4": ["D1", "W1", "MN1"],
}  # higher story lines, nearest first
GRADE_OF = {"with": "A", "free": "B", "with_parent": "B", "retracement": "C"}


OWNER = {
    "W1": "MN1",
    "D1": "W1",
    "H4": "D1",
    "H1": "D1",
}  # which story line uses a tier's levels
RANK = {"MN1": 0, "W1": 1, "D1": 2, "H4": 3}


def _governing(a, tf: str, entries) -> str:
    """The story line that governs a trade: the one that OWNS the level tiers of the entry zone
    (Daily levels belong to the Weekly story, 4-hour/1-hour levels to the Daily story). A strong zone that
    overlaps a Daily level is Weekly territory, so the Weekly story governs it."""
    cands = []
    for e in entries or []:
        if not e:
            continue
        for t in [e.get("tf")] + list(e.get("overlap_tfs") or []):
            o = OWNER.get(t)
            if o and a.stories.get(o):
                cands.append(o)
    return min(cands, key=lambda t: RANK[t]) if cands else tf


def context(a, tf: str, trade_dir: str, entries=None) -> dict:
    """Is this trade WITH the story lines above it, or a retracement of one of them?
    A story line is a retracement for as long as its parent story line is still active and opposite."""
    want = "bear" if trade_dir == "sell" else "bull"
    gov = _governing(a, tf, entries)
    chain = [(t, a.stories[t]) for t in [gov] + CHAIN.get(gov, []) if a.stories.get(t)]
    base = dict(
        gov_tf=gov,
        parent_tf=None,
        parent_dir=None,
        dominant_tf=None,
        dominant_dir=None,
        retracement=False,
    )
    if not chain:
        return dict(base, kind="free", label="no higher story line to compare with")
    base.update(dominant_tf=chain[-1][0], dominant_dir=chain[-1][1].dir)
    first_opp = next((i for i, (_, st_) in enumerate(chain) if st_.dir != want), None)
    if first_opp is None and len(chain) == 1 and chain[0][0] == gov:
        return dict(
            base,
            kind="free",
            label=(
                f"agrees with the {TF_NAME[gov]} story line; there is no higher "
                f"story line to compare with"
            ),
        )
    if first_opp is None:
        names = " and ".join(TF_NAME[t] for t, _ in chain)
        return dict(
            base, kind="with", label=f"with the trend: {names} agree ({dir_word(want)})"
        )
    otf, ost = chain[first_opp]
    base.update(parent_tf=otf, parent_dir=ost.dir)
    if first_opp <= 1:
        label = f"retracement of the {TF_NAME[otf]} story line ({dir_word(ost.dir)})"
        if first_opp == 1 and gov != tf:
            label += (
                f": this trade follows the {TF_NAME[gov]} {dir_word(want)} story line, which is itself a "
                f"retracement of the {TF_NAME[otf]}"
            )
        dom = chain[-1]
        if len(chain) > first_opp + 1 and dom[1].dir == ost.dir and dom[0] != otf:
            label += f", inside the {TF_NAME[dom[0]]} {dir_word(dom[1].dir)} trend"
        return dict(base, kind="retracement", retracement=True, label=label)
    return dict(
        base,
        kind="with_parent",
        label=(
            f"with the {TF_NAME[chain[0][0]]} {dir_word(want)} story line, but the {TF_NAME[otf]} story line "
            f"({dir_word(ost.dir)}) is against it higher up"
        ),
    )


def grade_from(ctx: dict) -> str:
    """A = with every higher story line, B = with the parent but inside a bigger retracement (or no parent),
    C = a retracement against the parent."""
    return GRADE_OF.get(ctx["kind"], "B")


def _ceiling(a, trade_dir: str, ctx: dict) -> Optional[KeyLevel]:
    """Retracement ceiling: the nearest key level of the PARENT story line on the trade's target side,
    i.e. where the dominant trend's entries start. A counter-trend move should be paid there."""
    if ctx["kind"] not in ("retracement", "with_parent") or not ctx.get("parent_tf"):
        return None
    tfs = STORY_LEVEL_TFS.get(ctx["parent_tf"]) or []
    if not tfs or tfs[0] not in a.levels:
        return None
    pool = pick(a, tfs[0], ctx["parent_dir"], 4, 0)["key"]
    mark_strong(a, {tfs[0]: pool})
    gap = config.MIN_TARGET_PIPS * a.pip
    cands = sorted(
        [
            k
            for k in pool
            if (
                k.price <= a.price - gap
                if trade_dir == "sell"
                else k.price >= a.price + gap
            )
        ],
        key=lambda k: abs(k.price - a.price),
    )
    if not cands:
        return None
    near = abs(cands[0].price - a.price)
    strong = next(
        (k for k in cands if k.strong and abs(k.price - a.price) <= 2 * near), None
    )
    return strong or cands[0]


def _cap(
    a,
    trade_dir: str,
    targets: List[dict],
    ceil: Optional[KeyLevel],
    ctx: dict,
    entry: float,
) -> List[dict]:
    """Retracement trades stop at the ceiling: anything beyond it would assume a reversal."""
    if not ceil:
        return targets
    bear = trade_dir == "sell"
    kept = [
        t
        for t in targets
        if (t["price"] >= ceil.price if bear else t["price"] <= ceil.price)
    ]
    pn = TF_NAME[ctx["parent_tf"]]
    if not kept or abs(kept[-1]["price"] - ceil.price) > 2 * a.pip:
        kept.append(
            dict(
                name=f"{ceil.name} (retracement ceiling)",
                price=ceil.price,
                kind="ceiling",
                strength=ceil.strength,
                pips=round(abs(ceil.price - entry) / a.pip, 1),
                advice=f"retracement ceiling: the {pn} story line's entry zone starts here. Take profit: "
                f"going further would assume a {pn} reversal that has not happened",
            )
        )
    else:
        kept[-1]["advice"] += (
            f"  (this is also the retracement ceiling of the {pn} story line)"
        )
    kept.sort(key=lambda t: abs(t["price"] - entry))
    for i, t in enumerate(kept, 1):
        t["n"] = i
    return kept


# ================================================================== two-sided scenarios + thesis
def _score(a14: float, price: float):
    return lambda k: (
        k.strength
        + (25 if k.strong else 0)
        + (15 if k.newest else 0)
        - 10 * abs(k.price - price) / a14
    )


def _refine_near(
    a, zone: KeyLevel, trade_dir: str, rtf: Optional[str]
) -> Optional[dict]:
    if not rtf or rtf not in a.levels:
        return None
    ovl = overlap_pips(a.symbol, a.pip) * a.pip
    rf = pick(a, rtf, "bear" if trade_dir == "sell" else "bull")["key"]
    mark_strong(a, {rtf: rf})
    close = [k for k in rf if abs(k.price - zone.price) <= ovl]
    return max(close, key=lambda k: (k.strong, k.strength)).as_dict() if close else None


def _conditions(
    a,
    d: str,
    zone_name: str,
    zone_price: float,
    etf: str,
    rtf: Optional[str],
    reached: bool,
    confirms,
) -> List[dict]:
    ovl = overlap_pips(a.symbol, a.pip) * a.pip * 1.5
    want = "bear" if d == "sell" else "bull"
    rej_met = brk_met = None
    if etf == "H4":
        n4 = len(a.series["H4"])
        rej_met = any(
            r.dir == want and r.k >= n4 - 3 and abs(r.price - zone_price) <= ovl
            for r in a.recent_rej.get("H4", [])
        )
        brk_met = any(
            c.dir == d and abs(c.rej_price - zone_price) <= ovl for c in confirms
        )
    side = "below" if d == "sell" else "above"
    asia = is_asia(a.series["H4"], a.last_t)
    return [
        dict(text=f"Price reaches {zone_name} at {px(a, zone_price)}", met=reached),
        dict(
            text=f"{TF_NAME[etf]} rejects a {TF_NAME[etf]} level in that zone (a wick into it, then a close back {side} it)",
            met=rej_met,
        ),
        dict(
            text=(
                f"{TF_NAME[rtf]} then breaks structure (QM) or an open-close level (OCL) {'down' if d == 'sell' else 'up'}"
                if rtf
                else "A lower-timeframe breakout follows"
            ),
            met=brk_met,
        ),
        dict(text="Outside Asia (the first two 4-hour candles)", met=not asia),
        dict(
            text="Inside the London or New York kill zone (best timing)",
            met=is_killzone(a.last_t),
        ),
        dict(text="Daily trade limit not reached", met=not a.limits.get("blocked")),
    ]


def build_scenarios(a, confirms) -> dict:
    """If price reaches the continuation point or the reversal point, what would we look for?
    Two scenarios (one sell, one buy), each with its own conditions, cancel level and targets."""
    anchor = next((tf for tf in ("D1", "W1") if a.stories.get(tf)), None)
    res = dict(anchor=anchor, items=[], note="")
    if not anchor:
        res["note"] = (
            "No Daily or Weekly story line is active, so there is nothing to plan around yet."
        )
        return res
    s = a.stories[anchor]
    sd = s.dir
    d_a = "sell" if sd == "bear" else "buy"
    d_b = "buy" if d_a == "sell" else "sell"
    etf = LTF_OF[anchor]
    rtf = LTF_OF.get(etf)
    got = pick(a, etf, sd, 3, 3)
    mark_strong(a, {etf: got["key"] + got["obstacles"]})
    a14 = max(atr(a.series[etf]), a.pip * 3)
    sc_fn = _score(a14, a.price)
    key_zone = max(got["key"], key=sc_fn) if got["key"] else None
    obs_zone = max(got["obstacles"], key=sc_fn) if got["obstacles"] else None
    approach = (config.APPROACH_PIPS or 1.5 * overlap_pips(a.symbol, a.pip)) * a.pip
    asia = is_asia(a.series["H4"], a.last_t)
    ovl = overlap_pips(a.symbol, a.pip) * a.pip
    an = TF_NAME[anchor]

    for sid, kind, d, zone in (
        ("A", "continuation", d_a, key_zone),
        ("B", "reversal", d_b, obs_zone),
    ):
        sc = dict(
            id=sid,
            kind=kind,
            dir=d,
            anchor=anchor,
            zone=None,
            refine=None,
            reached=False,
            status="waiting",
            reason="",
            conditions=[],
            cancel=None,
            targets=[],
            context=None,
            ceiling=None,
            entry=None,
            distance_pips=None,
            key="",
            upgrade="",
            label=(
                f"Continuation: with the {an} story line ({dir_word(sd)})"
                if kind == "continuation"
                else f"Reversal / retracement: against the {an} story line ({dir_word(sd)})"
            ),
        )
        if not zone:
            sc["reason"] = "no suitable key level / obstacle near price yet"
            res["items"].append(sc)
            continue
        ref = _refine_near(a, zone, d, rtf)
        ep = (ref or zone.as_dict())["price"]
        sc.update(
            zone=zone.as_dict(),
            refine=ref,
            entry=ep,
            distance_pips=round((ep - a.price) / a.pip, 1),
            reached=abs(ep - a.price) <= approach,
        )
        ctx = context(a, anchor, d, [zone.as_dict(), ref])
        ceil = _ceiling(a, d, ctx)
        sc["context"], sc["ceiling"] = ctx, (ceil.as_dict() if ceil else None)
        groups = [(etf, "roadblock"), (anchor, "key level")]
        sc["targets"] = _cap(a, d, _targets(a, d, ep, groups), ceil, ctx, ep)
        sc["conditions"] = _conditions(
            a, d, zone.name, ep, etf, rtf, sc["reached"], confirms
        )
        sc["cancel"] = dict(
            price=ep + (1 if d == "sell" else -1) * ovl,
            text=f"a 1-hour candle closes {'above' if d == 'sell' else 'below'}",
        )
        c = sc["conditions"]
        trig = (
            sc["reached"]
            and c[1]["met"]
            and c[2]["met"]
            and not asia
            and not a.limits.get("blocked")
        )
        sc["status"] = (
            "triggered" if trig else ("zone reached" if sc["reached"] else "waiting")
        )
        if kind == "reversal":
            sc["upgrade"] = (
                f"It becomes a full reversal (not just a retracement) only when {an} rejects a {an} "
                f"{'support' if d_b == 'buy' else 'resistance'} and {TF_NAME[etf]} gives a "
                f"{'bullish' if d_b == 'buy' else 'bearish'} breakout: a new {an} story line."
            )
        sc["key"] = f"SC|{sid}|{d}|{round(ep, 6)}|{s.eff}"
        res["items"].append(sc)
    return res


def build_thesis(a) -> dict:
    """The current plan for this pair, small enough to store and compare with the previous one."""
    order = [tf for tf in ("MN1", "W1", "D1", "H4") if a.stories.get(tf)]
    stories = {tf: dict(dir=a.stories[tf].dir, eff=a.stories[tf].eff) for tf in order}
    sc = a.scenarios
    anchor = sc["anchor"]
    parts = []
    if order:
        dom = order[0]
        parts.append(f"Dominant trend: {TF_NAME[dom]} {dir_word(stories[dom]['dir'])}.")
    if anchor:
        sd = a.stories[anchor].dir
        ctx = context(a, anchor, "sell" if sd == "bear" else "buy")
        parts.append(
            f"Anchor: the {TF_NAME[anchor]} story line is {dir_word(sd)}, {ctx['label']}."
        )
    items = []
    for it in sc["items"]:
        if it["zone"]:
            parts.append(
                f"{'Continuation' if it['kind'] == 'continuation' else 'Reversal'} {it['dir'].upper()} "
                f"at {px(a, it['entry'])} ({it['status']})."
            )
        items.append(
            dict(
                id=it["id"],
                kind=it["kind"],
                dir=it["dir"],
                price=it["entry"],
                status=it["status"],
                zone=(it["zone"] or {}).get("name"),
                cancel=(it["cancel"] or {}).get("price"),
                context=(it["context"] or {}).get("label"),
            )
        )
    sig = "|".join(
        [",".join(f"{t}{v['dir']}{v['eff']}" for t, v in stories.items()), str(anchor)]
        + [
            f"{i['id']}{i['dir']}{round(i['price'], a.digits) if i['price'] else ''}{i['status']}"
            for i in items
        ]
    )
    return dict(
        symbol=a.symbol,
        ts=a.last_t,
        price=a.price,
        stories=stories,
        anchor=anchor,
        scenarios=items,
        headline=" ".join(parts) or sc["note"],
        sig=sig,
    )


def thesis_changes(old: Optional[dict], new: dict, digits: int = 2) -> List[str]:
    """What changed since the previous thesis, in words (this is the 'review' of layer 1)."""
    if not old:
        return ["First thesis stored for this pair."]
    ch: List[str] = []
    for tf in ("MN1", "W1", "D1", "H4"):
        o, n = old.get("stories", {}).get(tf), new["stories"].get(tf)
        nm = TF_NAME[tf]
        if o and not n:
            ch.append(f"{nm} story line ended ({dir_word(o['dir'])}).")
        elif n and not o:
            ch.append(f"{nm} story line started ({dir_word(n['dir'])}).")
        elif o and n and o["dir"] != n["dir"]:
            ch.append(
                f"{nm} story line flipped from {dir_word(o['dir'])} to {dir_word(n['dir'])}."
            )
        elif o and n and o["eff"] != n["eff"]:
            ch.append(f"{nm} story line renewed ({dir_word(n['dir'])}).")
    if old.get("anchor") != new.get("anchor"):
        ch.append(
            f"Anchor changed from {old.get('anchor') or 'none'} to {new.get('anchor') or 'none'}."
        )
    om = {i["id"]: i for i in old.get("scenarios", [])}
    for i in new["scenarios"]:
        o = om.get(i["id"])
        name = "Continuation" if i["kind"] == "continuation" else "Reversal"
        if not o:
            continue
        if o.get("price") and i["price"] and abs(o["price"] - i["price"]) > 0:
            ch.append(
                f"{name} zone moved from {o['price']:.{digits}f} to {i['price']:.{digits}f}."
            )
        if o.get("status") != i["status"]:
            ch.append(
                f"{name} scenario is now '{i['status']}' (was '{o.get('status')}')."
            )
    return ch or ["No material change."]
