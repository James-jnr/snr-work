"""Core SNR / story-line engine. Pure functions, no I/O.

Rules implemented (from the course):
  * levels are built from CLOSING prices (A/V pivots and open-close levels)
  * a resistance that closes broken becomes support (RBS) and vice versa (SBR)
  * rejection = candle taps a level and closes back on the original side
  * story line = HTF rejection + QM (break of structure) on the next lower TF
  * story line ends only when an opposite one starts
  * confirmation = H4 rejects an H4 level, then H1 QM / open-close break
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Tuple

TF_SEC = {"H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800, "MN1": 2678400}
LTF_OF = {
    "MN1": "W1",
    "W1": "D1",
    "D1": "H4",
    "H4": "H1",
}  # story-line TF -> breakout / obstacle TF
PARENT_OF = {
    "W1": "MN1",
    "D1": "W1",
    "H4": "D1",
}  # whose obstacles this TF's levels are
TF_NAME = {
    "MN1": "Monthly",
    "W1": "Weekly",
    "D1": "Daily",
    "H4": "4-hour",
    "H1": "1-hour",
}

STORY_TFS = ("MN1", "W1", "D1")


class Bar(NamedTuple):
    t: int
    o: float
    h: float
    l: float
    c: float


@dataclass
class Params:
    pip: float
    pivot_n: Dict[str, int]
    qm_w: Dict[str, int]
    tap_tol_pips: float = 1.0


@dataclass
class Level:
    tf: str
    kind: str  # "A" resistance peak, "V" support trough, "OC" open-close
    price: float
    t: int
    avail: int  # first bar index on which the level may be used
    role: str  # current role: "R" or "S"
    flips: int = 0
    idx: int = 0  # candle index where the level was formed
    touches: int = 0  # candles whose range reached the level
    qm: bool = (
        False  # this level was broken by a structure break (QM) on its own timeframe
    )


@dataclass
class Rejection:
    k: int
    dir: str  # "bear" (rejected resistance) / "bull" (rejected support)
    price: float
    kind: str
    lt: int
    tf: str
    flips: int


@dataclass
class QM:
    dir: str
    i: int
    level: float


@dataclass
class Story:
    tf: str
    ltf: str
    dir: str
    k: int
    bar_t: int  # open time of the rejecting HTF candle
    eff: int  # time the story line became valid (server time)
    level: float
    level_kind: str
    qm_level: float
    qm_close_t: int
    same_bar: bool  # breakout inside the same candle as the rejection (best)


@dataclass
class Confirm:
    dir: str  # "buy" / "sell"
    h4_t: int
    rej_price: float
    rej_kind: str
    break_type: str  # "QM" or "OC"
    break_level: float
    break_t: int  # close time of the H1 bar that broke


def _pivot(cl: List[float], i: int, n: int, hi: bool) -> bool:
    if i - n < 0 or i + n >= len(cl):
        return False
    v = cl[i]
    if hi:
        return all(v >= cl[j] for j in range(i - n, i)) and all(
            v > cl[j] for j in range(i + 1, i + n + 1)
        )
    return all(v <= cl[j] for j in range(i - n, i)) and all(
        v < cl[j] for j in range(i + 1, i + n + 1)
    )


def make_levels(tf: str, bars: List[Bar], n: int) -> List[Level]:
    cl = [b.c for b in bars]
    col = [(b.c > b.o) - (b.c < b.o) for b in bars]
    out, seen = [], set()

    def add(kind, price, i, avail, role):
        key = (price, role)
        if key in seen:
            return
        seen.add(key)
        out.append(Level(tf, kind, price, bars[i].t, avail, role, 0, i))

    for i in range(len(bars)):
        if _pivot(cl, i, n, True):
            add("A", cl[i], i, i + n + 1, "R")
        if _pivot(cl, i, n, False):
            add("V", cl[i], i, i + n + 1, "S")
        # open-close: two same-colour candles, level = close of the first,
        # taken at the start of a run only (keeps the level count sane)
        if (
            i + 1 < len(bars)
            and col[i] != 0
            and col[i] == col[i + 1]
            and (i == 0 or col[i - 1] != col[i])
        ):
            add("OC", cl[i], i, i + 2, "S" if col[i] > 0 else "R")
    return out


def scan(tf: str, bars: List[Bar], p: Params, start: int = 0):
    """Walk the candles once. Returns (rejections, breaks, active_levels_with_current_roles)."""
    tol = p.tap_tol_pips * p.pip
    by_avail = defaultdict(list)
    for lv in make_levels(tf, bars, p.pivot_n[tf]):
        by_avail[lv.avail].append(lv)
    active: List[Level] = []
    rej: List[Rejection] = []
    brk: List[Tuple[int, str, float, str]] = []
    for k, b in enumerate(bars):
        active.extend(by_avail.get(k, ()))
        for lv in active:
            if lv.t >= b.t:
                continue
            if b.l - tol <= lv.price <= b.h + tol:
                lv.touches += 1
            if k >= start:
                if lv.role == "R" and b.h >= lv.price - tol and b.c < lv.price:
                    rej.append(
                        Rejection(k, "bear", lv.price, lv.kind, lv.t, tf, lv.flips)
                    )
                elif lv.role == "S" and b.l <= lv.price + tol and b.c > lv.price:
                    rej.append(
                        Rejection(k, "bull", lv.price, lv.kind, lv.t, tf, lv.flips)
                    )
            if lv.role == "R" and b.c > lv.price:  # RBS
                lv.role = "S"
                lv.flips += 1
                brk.append((k, "bull", lv.price, lv.kind))
            elif lv.role == "S" and b.c < lv.price:  # SBR
                lv.role = "R"
                lv.flips += 1
                brk.append((k, "bear", lv.price, lv.kind))
    return rej, brk, active


def find_qms(bars: List[Bar], w: int) -> List[QM]:
    """Break of structure on closes: LH+LL then a close above the last LH (bull), mirror for bear."""
    cl = [b.c for b in bars]
    sh: List[Tuple[int, float]] = []
    sl: List[Tuple[int, float]] = []
    used_hi, used_lo, out = set(), set(), []
    for i in range(len(bars)):
        pi = i - w
        if pi >= w:
            if _pivot(cl, pi, w, True):
                sh.append((pi, cl[pi]))
            if _pivot(cl, pi, w, False):
                sl.append((pi, cl[pi]))
        if len(sh) >= 2 and len(sl) >= 2:
            if (
                sh[-1][1] < sh[-2][1]
                and sl[-1][1] < sl[-2][1]
                and cl[i] > sh[-1][1]
                and sh[-1][0] not in used_hi
            ):
                used_hi.add(sh[-1][0])
                out.append(QM("bull", i, sh[-1][1]))
            if (
                sh[-1][1] > sh[-2][1]
                and sl[-1][1] > sl[-2][1]
                and cl[i] < sl[-1][1]
                and sl[-1][0] not in used_lo
            ):
                used_lo.add(sl[-1][0])
                out.append(QM("bear", i, sl[-1][1]))
    return out


# ---------------------------------------------------------------- level strength (0-100)
TF_WEIGHT = {"MN1": 40, "W1": 32, "D1": 24, "H4": 16, "H1": 8}
RECENT_WINDOW = {
    "MN1": 24,
    "W1": 52,
    "D1": 90,
    "H4": 120,
    "H1": 150,
}  # candles that still count as "recent"


def level_label(lv: Level) -> str:
    """Mentor-style type name: QM level, OCL (open-close level) or Classic A/V (peak / trough)."""
    return "QM" if lv.qm else "OCL" if lv.kind == "OC" else "Classic A/V"


def strength(lv: Level, n_bars: int) -> int:
    """Timeframe weight + touches + role changes (RBS/SBR) + how recent it is."""
    age = max(0, n_bars - 1 - lv.idx)
    rec = max(0.0, 1.0 - age / RECENT_WINDOW.get(lv.tf, 100))
    s = (
        TF_WEIGHT.get(lv.tf, 8)
        + min(3, lv.touches) * 8
        + min(2, lv.flips) * 10
        + 16 * rec
        + (4 if lv.qm else 0)
    )
    return int(min(100, round(s)))


def _end(bars: List[Bar], k: int, tf: str) -> int:
    return bars[k + 1].t if k + 1 < len(bars) else bars[k].t + TF_SEC[tf]


def storyline_events(
    htf: str, ltf: str, hb: List[Bar], lb: List[Bar], p: Params, start: int = 0
) -> List[Story]:
    if len(hb) < 5 or len(lb) < 10:
        return []
    rej, _, _ = scan(htf, hb, p, start)
    ld = TF_SEC[ltf]
    qinfo = [(q, lb[q.i].t + ld) for q in find_qms(lb, p.qm_w[ltf])]
    byk = defaultdict(list)
    for r in rej:
        byk[r.k].append(r)
    out: List[Story] = []
    for k, rs in byk.items():
        t0 = hb[k].t
        end_k = _end(hb, k, htf)
        end_n = _end(hb, k + 1, htf) if k + 1 < len(hb) else end_k + TF_SEC[htf]
        cands = []
        for r in rs:
            for q, qc in qinfo:
                if q.dir == r.dir and t0 < qc <= end_n:
                    cands.append((abs(hb[k].c - r.price), r, q, qc))
                    break
        if not cands:
            continue
        cands.sort(
            key=lambda x: (0 if x[3] <= end_k else 1, x[0])
        )  # same-candle first, then nearest level
        _, r, q, qc = cands[0]
        out.append(
            Story(
                htf,
                ltf,
                r.dir,
                k,
                t0,
                max(qc, end_k),
                r.price,
                r.kind,
                q.level,
                qc,
                qc <= end_k,
            )
        )
    out.sort(key=lambda s: (s.eff, s.k))
    return out


def current_story(events: List[Story], now_ts: int) -> Optional[Story]:
    valid = [e for e in events if e.eff <= now_ts]
    return valid[-1] if valid else None


def find_confirmations(
    h4: List[Bar], h1: List[Bar], p: Params, now_ts: int, window_h: int
) -> List[Confirm]:
    if len(h4) < 10 or len(h1) < 20:
        return []
    rej, _, _ = scan("H4", h4, p, start=max(0, len(h4) - 4))
    qms = find_qms(h1, p.qm_w["H1"])
    _, brk, _ = scan("H1", h1, p)
    out, seen = [], set()
    for r in rej:
        close_t = h4[r.k].t + TF_SEC["H4"]
        if now_ts - close_t > window_h * 3600 or (r.k, r.dir) in seen:
            continue
        cands = []
        for q in qms:
            qc = h1[q.i].t + TF_SEC["H1"]
            if q.dir == r.dir and qc > close_t:
                cands.append((qc, "QM", q.level))
        for k, d, price, kind in brk:
            bc = h1[k].t + TF_SEC["H1"]
            if kind == "OC" and d == r.dir and bc > close_t:
                cands.append((bc, "OC", price))
        if not cands:
            continue
        cands.sort()
        bt, btype, blevel = cands[0]
        seen.add((r.k, r.dir))
        out.append(
            Confirm(
                "sell" if r.dir == "bear" else "buy",
                h4[r.k].t,
                r.price,
                r.kind,
                btype,
                blevel,
                bt,
            )
        )
    return out
