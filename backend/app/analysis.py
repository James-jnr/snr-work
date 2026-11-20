"""Turns raw candles into story lines, key levels, status, liquidity and the three setups."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import config, zones
from .engine import (
    LTF_OF,
    STORY_TFS,
    TF_SEC,
    Bar,
    Level,
    Params,
    Rejection,
    Story,
    find_confirmations,
    find_qms,
    scan,
)
from .state import SymbolState

GRADE_RANK = {"A": 3, "B": 2, "C": 1}


def pip_for(symbol: str, meta: dict) -> float:
    s = symbol.upper()
    for pref, v in config.PIP_OVERRIDES.items():
        if s.startswith(pref.upper()):
            return float(v)
    if s.startswith(("XAU", "GOLD")):
        return 0.1  # course convention: 1 pip = $0.10 on gold
    if s.startswith(("XAG", "SILVER")):
        return 0.01
        # In app/analysis.py inside pip_for():
    if "JPY" in s:
        return 0.01
    if (
        len(s) >= 6
        and s[:6].isalpha()
        and not s.startswith(
            (
                "XAU",
                "XAG",
                "GOLD",
                "SILV",
                "NAS",
                "US1",
                "US3",
                "US5",
                "UK1",
                "GER",
                "SPX",
            )
        )
    ):
        return 0.0001

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
    scenarios: dict = field(
        default_factory=dict
    )  # two-sided plan around the Daily/Weekly story line
    thesis: dict = field(
        default_factory=dict
    )  # the plan for this pair, as stored in memory
    thesis_prev: Optional[dict] = None  # previous stored thesis (filled by the service)
    thesis_changes: List[str] = field(default_factory=list)
    confirms: list = field(default_factory=list)


def story_key(tf: str, s: Story) -> str:
    return f"{tf}|{s.dir}|{s.eff}"


def grade_for(a: Analysis, d: str, story_tf: str = "D1") -> str:
    """A = with every higher story line, B = with the parent but inside a bigger retracement (or no parent),
    C = a retracement against the parent story line."""
    return zones.grade_from(zones.context(a, story_tf or "H4", d))


# Update analyze() signature in app/analysis.py:
def analyze(
    st: SymbolState,
    rejected: Optional[Set[Tuple[str, str, int]]] = None,
    limits: Optional[dict] = None,
    overrides: Optional[Dict[str, str]] = None,
) -> Optional[Analysis]:
    if not st.ready():
        return None
    pip = pip_for(st.symbol, st.meta)
    p = Params(
        pip=pip,
        pivot_n=config.PIVOT_N,
        qm_w=config.QM_W,
        tap_tol_pips=config.TAP_TOL_PIPS,
    )
    tfs = ("MN1", "W1", "D1", "H4", "H1")
    s = {tf: st.series(tf) for tf in tfs}
    price = s["H1"][-1].c
    last_t = s["H1"][-1].t + TF_SEC["H1"]

    stories: Dict[str, Optional[Story]] = {}
    prev: Dict[str, Optional[Story]] = {}
    rej_by_you: Dict[str, Story] = {}
    for tf, forced_dir in (overrides or {}).items():
        if tf not in STORY_TFS:
            continue
        if forced_dir == "none":
            stories[tf] = None
        elif forced_dir in ("bear", "bull"):
            existing = stories.get(tf)
            if existing and existing.dir == forced_dir:
                continue
            ltf = LTF_OF[tf]
            bar = s[tf][-1]
            stories[tf] = Story(
                tf=tf,
                ltf=ltf,
                dir=forced_dir,
                k=len(s[tf]) - 1,
                bar_t=bar.t,
                eff=bar.t,
                level=bar.c,
                level_kind="MANUAL",
                qm_level=bar.c,
                qm_close_t=bar.t,
                same_bar=True,
            )

    levels: Dict[str, List[Level]] = {}
    qmp: Dict[str, Set[float]] = {}
    rrej: Dict[str, List[Rejection]] = {}
    for tf in (["MN1"] if config.ENABLE_MN1 and len(s["MN1"]) > 5 else []) + [
        "W1",
        "D1",
        "H4",
        "H1",
    ]:
        rj, _, act = scan(tf, s[tf], p, start=max(0, len(s[tf]) - 6))
        qset = {q.level for q in find_qms(s[tf], config.QM_W.get(tf, 1))}
        for lv in act:
            lv.qm = lv.price in qset
        levels[tf], qmp[tf], rrej[tf] = act, qset, rj

    a = Analysis(
        st.symbol,
        price,
        last_t,
        pip,
        int(st.meta.get("digits", 2)),
        st.meta,
        s,
        stories,
        prev,
        rej_by_you,
        levels,
        qmp,
        rrej,
        limits or {},
    )
    zones.select_all(a)
    a.liquidity = zones.liquidity(a)
    for tf in STORY_TFS:
        if tf in stories:
            a.status[tf] = zones.story_status(a, tf)
    confirms = find_confirmations(s["H4"], s["H1"], p, last_t, config.CONFIRM_WINDOW_H)
    confirms = [c for c in confirms if not zones.is_asia(s["H4"], c.break_t - 1)]
    a.confirms = confirms
    a.setups = zones.build_setups(a, confirms)
    a.scenarios = zones.build_scenarios(a, confirms)
    a.thesis = zones.build_thesis(a)
    return a


def analyze_live(st: SymbolState) -> Optional[Analysis]:
    """analyze() with your story-line rejections, manual overrides, and daily trade limits applied."""
    try:
        from . import db

        rejected = db.rejected_storylines(st.symbol)
        limits = db.daily_limits()
        overrides = db.story_overrides(st.symbol)
    except Exception:
        rejected, limits, overrides = set(), {}, {}
    return analyze(st, rejected=rejected, limits=limits, overrides=overrides)
