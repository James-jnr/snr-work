"""Glue: run analysis when new candles arrive and decide what to alert."""
import logging
import time
from typing import List, Optional, Tuple

from . import charts, config, db, zones
from .analysis import GRADE_RANK, Analysis, analyze_live, grade_for, story_key
from .engine import LTF_OF, STORY_TFS, TF_NAME
from .messages import dword, fmt_d, fmt_report, wrap
from .state import SymbolState

log = logging.getLogger("service")
Msg = tuple   # (subject, body) | (subject, body, meta) | (subject, body, meta, [ChartSpec, ...])


# ------------------------------------------------------------------ helpers
def run_analysis(st: SymbolState) -> Optional[Analysis]:
    return analyze_live(st)


def _verify(st: SymbolState) -> dict:
    try:
        return db.story_statuses(st.symbol)
    except Exception:
        return {}


def _specs(st: SymbolState, a: Analysis, tfs, plan=None):
    """Zone map + one chart per timeframe, each timeframe ALONE. A chart problem never blocks an alert."""
    if not config.CHARTS_IN_ALERTS:
        return None
    out = []
    for tf in tfs:
        try:
            if tf == "ZM":
                out.append(charts.make_zonemap(st, a))
            else:
                out.append(charts.make_spec(st, a, tf, plan=plan if tf in ("D1", "H4", "H1") else None))
        except Exception as e:      # e.g. monthly history still too short
            log.info("no %s chart: %s", tf, e)
    return out or None


def _stories_meta(a: Analysis) -> list:
    return [dict(tf=tf, dir=s.dir, eff=s.eff, level=s.level, qm_level=s.qm_level, same_bar=s.same_bar,
                 key=story_key(tf, s), name=TF_NAME[tf])
            for tf, s in a.stories.items() if s]


def _meta(st: SymbolState, a: Analysis, kind: str, **extra) -> dict:
    return {"kind": kind, "symbol": st.symbol, "stories": _stories_meta(a), "setups": a.setups,
            "limits": a.limits, "gmt_offset": int(a.meta.get("gmt_offset", 0)), **extra}


def _approach(a: Analysis) -> float:
    return config.APPROACH_PIPS or 1.5 * zones.overlap_pips(a.symbol, a.pip)


def _cooling(st: SymbolState, key: str, ts: int) -> bool:
    if ts - st.last_signal.get(key, 0) < config.SIGNAL_COOLDOWN_H * 3600:
        return True
    st.last_signal[key] = ts
    return False


def _setup_alert(st, a, su) -> Msg:
    word = "SELL" if su["dir"] == "sell" else "BUY"
    grade = grade_for(a, su["dir"])
    head = [f"SETUP {su['id']} READY: {word}  ({su['title']})"]
    if a.limits.get("blocked"):
        head.append(f"⛔ {a.limits['blocked']}")
    plan = charts.plan_from_setup(su)
    return (f"🎯 {st.symbol} {word} setup (grade {grade})", fmt_report(a, _verify(st), head),
            _meta(st, a, "setup", direction=su["dir"], grade=grade, setup_id=su["id"],
                  entry=(su["refine"] or su["entry"])["price"], key=su["key"]),
            _specs(st, a, ("ZM", "D1", "H4", "H1"), plan=plan))


# ------------------------------------------------------------------ first full load
def warmup(st: SymbolState) -> List[Msg]:
    """First full load: remember what already exists so we only alert on NEW events."""
    a = run_analysis(st)
    if a is None:
        return []
    for tf, s in a.stories.items():
        if s:
            st.notified.add(f"SL|{tf}|{s.dir}|{s.eff}")
            st.story_dirs[tf] = s.dir
    for su in a.setups:
        if su["key"]:
            st.notified.add(su["key"])
            if su["id"] == 3 and su.get("cf_break_t"):
                st.last_signal[su["dir"]] = max(st.last_signal.get(su["dir"], 0), su["cf_break_t"])
    msgs: List[Msg] = []
    force, st.force_snapshot = st.force_snapshot, False
    if force or time.time() - st.last_online_msg > 3600:
        st.last_online_msg = time.time()
        title = "Sync snapshot" if force else "Feed online snapshot"
        msgs.append((f"📊 {st.symbol} feed online: snapshot",
                     fmt_report(a, _verify(st), [f"{title.upper()}: the current picture from MT5."]),
                     _meta(st, a, "snapshot"), _specs(st, a, ("ZM", "MN1", "W1", "D1", "H4", "H1"))))
    return msgs


# ------------------------------------------------------------------ every new candle
def on_new_bars(st: SymbolState, new_tfs: List[str]) -> List[Msg]:
    a = run_analysis(st)
    if a is None:
        return []
    msgs: List[Msg] = []
    verify = _verify(st)

    # 1) story lines: alert when a NEW one starts in a different direction (same direction = renewed, quiet)
    for tf in STORY_TFS:
        s = a.stories.get(tf)
        if s and (key := f"SL|{tf}|{s.dir}|{s.eff}") not in st.notified:
            st.notified.add(key)
            changed = st.story_dirs.get(tf) != s.dir
            st.story_dirs[tf] = s.dir
            if not changed:
                continue
            nm = TF_NAME[tf]
            head = [f"NEW {nm.upper()} STORY LINE: {dword(s.dir).upper()}", wrap(zones.story_reason(a, tf, s)),
                    wrap("Please verify it on the alert page (tick the box) or reject it, "
                         "so setups only build on story lines you agree with.")]
            pv = a.story_prev.get(tf)
            if pv and pv.dir != s.dir:
                head.insert(2, wrap(f"This ends the previous {nm} {dword(pv.dir)} story line "
                                    f"(since {fmt_d(pv.eff, a.meta)})."))
            su = next((x for x in a.setups if x["id"] == 2), None) if tf == "H4" else None
            plan = charts.plan_from_setup(su) if su and su["entry"] else None
            tfs = ("ZM", tf, LTF_OF[tf]) if tf != "H4" else ("ZM", "H4", "H1")
            msgs.append((f"🧭 {st.symbol} {nm} story line: {dword(s.dir).upper()}",
                         fmt_report(a, verify, head),
                         _meta(st, a, "storyline", tf=tf, direction="sell" if s.dir == "bear" else "buy",
                               level=s.level, qm_level=s.qm_level),
                         _specs(st, a, tfs, plan=plan)))

    # 2) confirmation setup (Setup 3) and Daily-story setup when price approaches its zone (Setup 1)
    for su in a.setups:
        if su["status"] != "ready" or not su["key"] or su["key"] in st.notified:
            continue
        if su["id"] == 3:
            st.notified.add(su["key"])
            if GRADE_RANK[grade_for(a, su["dir"])] < GRADE_RANK.get(config.MIN_GRADE, 2):
                continue         # counter-trend to the Daily story line: shown in reports, no alert
            if _cooling(st, su["dir"], su.get("cf_break_t", a.last_t)):
                continue
        elif su["id"] == 1:
            price = (su["refine"] or su["entry"])["price"]
            if abs(price - a.price) / a.pip > _approach(a):
                continue
            st.notified.add(su["key"])
            if _cooling(st, su["dir"], a.last_t):
                continue
        else:
            continue          # Setup 2 is delivered inside the 4-hour story-line alert
        msgs.append(_setup_alert(st, a, su))
    if config.SUPPRESS_WHEN_LIMIT and a.limits.get("blocked"):
        msgs = [m for m in msgs if not m[0].startswith("🎯")]

    # 3) briefings on a new Daily / Weekly candle
    if "W1" in new_tfs or "D1" in new_tfs:
        title = "Weekly + daily briefing" if "W1" in new_tfs else "Daily briefing"
        msgs.append((f"🗓 {st.symbol} {title}", fmt_report(a, verify, [title.upper()]),
                     _meta(st, a, "brief"), _specs(st, a, ("ZM", "MN1", "W1", "D1", "H4", "H1"))))
    if len(st.notified) > 2000:
        st.notified = set(list(st.notified)[-1000:])
    return msgs
