"""Turns raw candles into story lines, key levels, status, liquidity and the three setups."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import config, zones
from .engine import (LTF_OF, STORY_TFS, TF_SEC, Bar, Level, Params, Rejection, Story, find_confirmations,
                     find_qms, scan, storyline_events)
from .state import SymbolState

GRADE_RANK = {"A": 3, "B": 2, "C": 1}


def pip_for(symbol: str, meta: dict) -> float:
    s = symbol.upper()
    for pref, v in config.PIP_OVERRIDES.items():
        if s.startswith(pref.upper()):
            return float(v)
    if s.startswith(("XAU", "GOLD")):
        return 0.1       # course convention: 1 pip = $0.10 on gold
    if s.startswith(("XAG", "SILVER")):
        return 0.01
    if "JPY" in s:
        return 0.01
    if s.startswith(("NAS", "US100", "USTEC", "NDX", "US30", "DJ", "DE", "GER", "UK100", "US500", "SPX")):
        return 1.0
    digits = int(meta.get("digits", 5))
    point = float(meta.get("point", 0.00001))
    return point * 10 if digits in (3, 5) else point


@dataclass
class Analysis:
    symbol: str
    price: float
    last_t: int
    pip: float
    digits: int
    meta: dict
    series: Dict[str, List[Bar]]
    stories: Dict[str, Optional[Story]]
    story_prev: Dict[str, Optional[Story]]
    story_rejected: Dict[str, Story]
    levels: Dict[str, List[Level]]
    qm_prices: Dict[str, Set[float]]
    recent_rej: Dict[str, List[Rejection]]
    limits: dict
    selected: Dict[str, list] = field(default_factory=dict)
    zones: List[dict] = field(default_factory=list)
    status: Dict[str, Optional[dict]] = field(default_factory=dict)
    liquidity: dict = field(default_factory=dict)
    setups: List[dict] = field(default_factory=list)


def story_key(tf: str, s: Story) -> str:
    return f"{tf}|{s.dir}|{s.eff}"


def grade_for(a: Analysis, d: str) -> str:
    want = "bear" if d == "sell" else "bull"
    d1, w1 = a.stories.get("D1"), a.stories.get("W1")
    d_ok = bool(d1 and d1.dir == want)
    w_ok = bool(w1 and w1.dir == want)
    return "A" if d_ok and w_ok else "B" if d_ok else "C"


def analyze(st: SymbolState, rejected: Optional[Set[Tuple[str, str, int]]] = None,
            limits: Optional[dict] = None) -> Optional[Analysis]:
    if not st.ready():
        return None
    pip = pip_for(st.symbol, st.meta)
    p = Params(pip=pip, pivot_n=config.PIVOT_N, qm_w=config.QM_W, tap_tol_pips=config.TAP_TOL_PIPS)
    tfs = ("MN1", "W1", "D1", "H4", "H1")
    s = {tf: st.series(tf) for tf in tfs}
    price = s["H1"][-1].c
    last_t = s["H1"][-1].t + TF_SEC["H1"]

    stories: Dict[str, Optional[Story]] = {}
    prev: Dict[str, Optional[Story]] = {}
    rej_by_you: Dict[str, Story] = {}
    for tf in STORY_TFS:
        if tf == "MN1" and not config.ENABLE_MN1:
            continue
        ltf = LTF_OF[tf]
        evs = storyline_events(tf, ltf, s[tf], s[ltf], p, start=0)
        now = s[ltf][-1].t + TF_SEC[ltf] if s[ltf] else 0
        valid = [e for e in evs if e.eff <= now]
        cur = valid[-1] if valid else None
        pv = valid[-2] if len(valid) >= 2 else None
        if cur and rejected and (tf, cur.dir, cur.eff) in rejected:      # you rejected it: story line ended
            rej_by_you[tf] = cur
            cur = None
        stories[tf], prev[tf] = cur, pv

    levels: Dict[str, List[Level]] = {}
    qmp: Dict[str, Set[float]] = {}
    rrej: Dict[str, List[Rejection]] = {}
    for tf in (["MN1"] if config.ENABLE_MN1 and len(s["MN1"]) > 5 else []) + ["W1", "D1", "H4", "H1"]:
        rj, _, act = scan(tf, s[tf], p, start=max(0, len(s[tf]) - 6))
        qset = {q.level for q in find_qms(s[tf], config.QM_W.get(tf, 1))}
        for lv in act:
            lv.qm = lv.price in qset
        levels[tf], qmp[tf], rrej[tf] = act, qset, rj

    a = Analysis(st.symbol, price, last_t, pip, int(st.meta.get("digits", 2)), st.meta, s, stories, prev,
                 rej_by_you, levels, qmp, rrej, limits or {})
    zones.select_all(a)
    a.liquidity = zones.liquidity(a)
    for tf in STORY_TFS:
        if tf in stories:
            a.status[tf] = zones.story_status(a, tf)
    confirms = find_confirmations(s["H4"], s["H1"], p, last_t, config.CONFIRM_WINDOW_H)
    confirms = [c for c in confirms if not zones.is_asia(s["H4"], c.break_t - 1)]
    a.setups = zones.build_setups(a, confirms)
    return a


def analyze_live(st: SymbolState) -> Optional[Analysis]:
    """analyze() with your story-line rejections and the daily trade limits applied."""
    try:
        from . import db
        rejected, limits = db.rejected_storylines(st.symbol), db.daily_limits()
    except Exception:
        rejected, limits = set(), {}
    return analyze(st, rejected=rejected, limits=limits)
