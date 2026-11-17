"""Turns raw candles into story lines, levels, obstacles and trade signals."""
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional

from . import config
from .engine import (LTF_OF, TF_SEC, Confirm, Level, Params, Story, current_story,
                     find_confirmations, scan, storyline_events)
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
class Signal:
    dir: str
    grade: str
    risk_pct: float
    confirm: Confirm
    zones: List[dict]
    entry: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    sl_pips: Optional[float]
    tp_pips: float
    obstacle: Optional[dict]
    lots: Optional[float]
    warnings: List[str]
    session: str
    key: str
    htf_zone: Optional[dict] = None


@dataclass
class Analysis:
    symbol: str
    price: float
    last_t: int
    pip: float
    meta: dict
    stories: Dict[str, Optional[Story]]
    levels: Dict[str, List[Level]]
    signals: List[Signal] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


def nearest(levels: List[Level], price: float, role: str, side: str, n: int = 2, merge: float = 0.0):
    pool = [l for l in levels if l.role == role and (l.price > price if side == "above" else l.price < price)]
    pool.sort(key=lambda l: l.price, reverse=(side == "below"))
    out = []
    for l in pool:
        if out and abs(l.price - out[-1].price) <= merge:
            continue
        out.append(l)
        if len(out) == n:
            break
    return out


def lots_for(meta: dict, risk_pct: float, sl_dist: float) -> Optional[float]:
    bal = float(meta.get("balance", 0) or 0)
    ts = float(meta.get("tick_size", 0) or 0)
    tv = float(meta.get("tick_value", 0) or 0)
    if bal <= 0 or ts <= 0 or tv <= 0 or sl_dist <= 0:
        return None
    loss_per_lot = sl_dist / ts * tv
    raw = bal * risk_pct / 100.0 / loss_per_lot
    step = float(meta.get("vol_step", 0.01)) or 0.01
    lots = math.floor(raw / step + 1e-9) * step
    vmin = float(meta.get("vol_min", 0.01))
    if lots < vmin:
        return None  # never round upward beyond the user's risk budget
    lots = min(lots, float(meta.get("vol_max", 100)))
    decimals = max(0, min(8, len(str(step).split(".")[-1].rstrip("0"))))
    return round(lots, decimals)


def server_dt(ts: int, meta: dict) -> datetime:
    # MT5 candle timestamps are broker-server timestamps encoded as epoch values.
    # Convert broker time to UTC using the offset sent by the EA, then to Nairobi.
    utc_ts = ts - int(meta.get("gmt_offset", 0) or 0)
    return datetime.fromtimestamp(utc_ts, timezone.utc).astimezone(ZoneInfo("Africa/Nairobi"))


def analyze(st: SymbolState) -> Optional[Analysis]:
    if not st.ready():
        return None
    pip = pip_for(st.symbol, st.meta)
    p = Params(pip=pip, pivot_n=config.PIVOT_N, structure_w=config.STRUCTURE_W, tap_tol_pips=config.TAP_TOL_PIPS)
    s = {tf: st.series(tf) for tf in ("MN1", "W1", "D1", "H4", "H1")}
    price = s["H1"][-1].c
    last_t = s["H1"][-1].t + TF_SEC["H1"]

    stories: Dict[str, Optional[Story]] = {}
    for htf in (["MN1"] if config.ENABLE_MN1 else []) + ["W1", "D1"]:
        ltf = LTF_OF[htf]
        evs = storyline_events(htf, ltf, s[htf], s[ltf], p, start=0)
        now = s[ltf][-1].t + TF_SEC[ltf] if s[ltf] else 0
        stories[htf] = current_story(evs, now)

    levels: Dict[str, List[Level]] = {}
    for tf in (["MN1"] if config.ENABLE_MN1 and len(s["MN1"]) > 5 else []) + ["W1", "D1", "H4", "H1"]:
        _, _, act = scan(tf, s[tf], p, start=len(s[tf]))
        levels[tf] = act

    a = Analysis(st.symbol, price, last_t, pip, st.meta, stories, levels)

    for c in find_confirmations(s["H4"], s["H1"], p, last_t, config.CONFIRM_WINDOW_H):
        sig = build_signal(a, c)
        if isinstance(sig, str):
            a.skipped.append(sig)
        elif sig:
            a.signals.append(sig)
    return a


def build_signal(a: Analysis, c: Confirm):
    want = "bear" if c.dir == "sell" else "bull"
    d1, w1 = a.stories.get("D1"), a.stories.get("W1")
    d_ok = bool(d1 and d1.dir == want)
    w_ok = bool(w1 and w1.dir == want)
    w_conflict = bool(w1 and w1.dir != want)
    grade = "A" if d_ok and w_ok else "B" if d_ok else "C"
    if GRADE_RANK[grade] < GRADE_RANK.get(config.MIN_GRADE, 2):
        return f"{c.dir.upper()} setup skipped: grade {grade} below MIN_GRADE {config.MIN_GRADE}"

    bt = server_dt(c.break_t, a.meta)
    if config.ASIA_START_HOUR_EAT <= bt.hour < config.ASIA_RESTRICT_END_HOUR_EAT:
        return f"{c.dir.upper()} confirmation skipped: first two Asian H4 candles (Nairobi {bt:%H:%M})"

    pip, price = a.pip, a.price
    # the H4 rejection must happen at a relevant higher-timeframe zone (W1 / recent D1 level)
    role_hit = "R" if c.dir == "sell" else "S"
    zone_hit = None
    for tf in ("MN1", "W1", "D1"):
        for lv in a.levels.get(tf, []):
            if lv.role != role_hit:
                continue
            if tf == "D1" and a.last_t - lv.t > config.HTF_LEVEL_AGE_D * 86400:
                continue
            d = abs(lv.price - c.rej_price) / pip
            if d <= config.ZONE_PIPS and (zone_hit is None or d < zone_hit["pips"]):
                zone_hit = {"tf": tf, "price": lv.price, "pips": d}
    if zone_hit is None:
        return f"{c.dir.upper()} setup skipped: H4 rejection not at a W1/D1 zone"
    maxd = config.ENTRY_MAX_DIST_PIPS * pip
    zones = []
    for tf in ("H4", "H1"):
        role, side = ("R", "above") if c.dir == "sell" else ("S", "below")
        for lv in nearest(a.levels.get(tf, []), price, role, side, n=3, merge=2 * pip):
            if abs(lv.price - price) <= maxd:
                zones.append({"price": lv.price, "tf": tf, "kind": lv.kind})
    zones.sort(key=lambda z: abs(z["price"] - price))
    zones = zones[:2]

    sl_pips = None  # user sets SL manually; lot sizing waits for actual SL input
    entry = sl = tp = None
    obstacle = None
    if c.break_type == "STRUCTURE_RETEST":
        entry = c.break_level
        sgn = 1 if c.dir == "buy" else -1
        sl = None
        tp = entry + sgn * config.TP_PIPS * pip
        orole, oside = ("S", "below") if c.dir == "sell" else ("R", "above")
        cands = []
        for tf in ("H4", "D1", "W1"):
            for lv in nearest(a.levels.get(tf, []), entry, orole, oside, n=3, merge=2 * pip):
                dist = abs(lv.price - entry) / pip
                if dist >= 30:
                    cands.append({"price": lv.price, "tf": tf, "pips": dist})
        cands.sort(key=lambda x: x["pips"])
        obstacle = cands[0] if cands else None

    risk = config.RISK_PCT if grade == "A" or (grade == "B" and not w_conflict) else config.RISK_PCT_REDUCED
    lots = None  # calculated only after the user supplies the SL price

    warn = []
    if grade == "B" and w_conflict:
        warn.append("Weekly story line is against this trade: pullback inside the weekly move, risk reduced")
    if grade == "B" and not w1:
        warn.append("No active weekly story line")
    if grade == "C":
        warn.append("Daily story line does not support this direction: counter-trend, reduced risk")
    if bt.strftime("%Y-%m-%d") in config.NO_TRADE_DATES:
        warn.append("Date is in NO_TRADE_DATES (news day?)")
    if not zones:
        warn.append(f"No refined H4/H1 zone within {config.ENTRY_MAX_DIST_PIPS:.0f} pips: wait for a retest manually")

    session = "London" if 10 <= bt.hour < 15 else "New York" if 15 <= bt.hour < 24 else "Off-session"
    key = f"CF|{c.dir}|{c.h4_t}|{c.break_t}"
    return Signal(c.dir, grade, risk, c, zones, entry, sl, tp, sl_pips, config.TP_PIPS,
                  obstacle, lots, warn, session, key, zone_hit)
