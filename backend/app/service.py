"""Glue: run analysis when new candles arrive and decide what to alert."""
import logging
import time
from typing import List, Tuple
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import charts, config, db
from .analysis import analyze
from .engine import LTF_OF, TF_SEC
from .messages import fmt_brief, fmt_ranges, fmt_signal, fmt_story_alert
from .state import SymbolState

log = logging.getLogger("service")
Msg = tuple   # (subject, body) | (subject, body, meta) | (subject, body, meta, [ChartSpec, ...])


def _ranges_text(st: SymbolState, a) -> str:
    d1, w1, h4 = st.series("D1"), st.series("W1"), st.series("H4")
    asia = None
    if h4:
        # Convert broker timestamps to Nairobi local time before extracting 00:00-09:00.
        offset = int(st.meta.get("gmt_offset", 0) or 0)
        local = [(b, datetime.fromtimestamp(b.t-offset, timezone.utc).astimezone(ZoneInfo("Africa/Nairobi"))) for b in h4]
        last_day = local[-1][1].date()
        bars = [b for b, dt in local if dt.date() == last_day and 0 <= dt.hour < 9]
        if len(bars) >= 2:
            asia = (max(b.h for b in bars), min(b.l for b in bars))
    return fmt_ranges(a, d1[-1] if d1 else None, w1[-1] if w1 else None, asia)


def _specs(st: SymbolState, a, tfs, plan=None):
    """One chart per timeframe, each timeframe ALONE. Never lets a chart problem block an alert."""
    if not config.CHARTS_IN_ALERTS:
        return None
    out = []
    for tf in tfs:
        try:
            out.append(charts.make_spec(st, a, tf, plan=plan if tf in ("D1", "H4", "H1") else None,
                                        max_levels=8 if plan else 12))
        except Exception as e:      # e.g. monthly history still too short
            log.info("no %s chart: %s", tf, e)
    return out or None


def warmup(st: SymbolState) -> List[Msg]:
    """First full load: remember what already exists so we only alert on NEW events."""
    a = analyze(st)
    if a is None:
        return []
    for tf, s in a.stories.items():
        if s:
            st.notified.add(f"SL|{tf}|{s.dir}|{s.eff}")
            st.story_dirs[tf] = s.dir
    for g in a.signals:
        st.notified.add(g.key)
        st.last_signal[g.dir] = max(st.last_signal.get(g.dir, 0), g.confirm.break_t)
    msgs: List[Msg] = []
    if time.time() - st.last_online_msg > 3600:
        st.last_online_msg = time.time()
        body = fmt_brief(a, "Startup")
        rng = _ranges_text(st, a)
        msgs.append((f"📊 {st.symbol} feed online: snapshot", body + ("\n\n" + rng if rng else ""),
                     {"kind": "snapshot", "symbol": st.symbol},
                     _specs(st, a, ("MN1", "W1", "D1", "H4"))))
    return msgs


def on_new_bars(st: SymbolState, new_tfs: List[str]) -> List[Msg]:
    a = analyze(st)
    if a is None:
        return []
    msgs: List[Msg] = []
    for tf in ("MN1", "W1", "D1"):
        s = a.stories.get(tf)
        if s and (key := f"SL|{tf}|{s.dir}|{s.eff}") not in st.notified:
            st.notified.add(key)
            changed = st.story_dirs.get(tf) != s.dir
            st.story_dirs[tf] = s.dir
            if not changed:      # same-direction refresh: stay quiet, only flips are news
                continue
            word = "BEARISH" if s.dir == "bear" else "BULLISH"
            name = {"MN1": "Monthly", "W1": "Weekly", "D1": "Daily"}[tf]
            msgs.append((f"🧭 {st.symbol} {name} story line: {word}", fmt_story_alert(a, tf, s),
                         {"kind": "storyline", "symbol": st.symbol, "tf": tf,
                          "direction": "sell" if s.dir == "bear" else "buy",
                          "level": s.level, "breakout_level": s.breakout_level},
                         _specs(st, a, (tf, LTF_OF[tf]))))
    trade_state = db.mt5_trade_summary()
    risk_blocked = (trade_state.get("trades_started_today", 0) >= 3 or
                    trade_state.get("consecutive_losses", 0) >= 3)
    for g in a.signals:
        if g.key not in st.notified:
            st.notified.add(g.key)
            if risk_blocked:
                log.info("setup suppressed by account risk guard: %s", g.key)
                continue
            word = "SELL" if g.dir == "sell" else "BUY"
            msgs.append((f"🎯 {st.symbol} {word} setup (grade {g.grade})", fmt_signal(a, g),
                         {"kind": "setup", "symbol": st.symbol, "direction": g.dir, "grade": g.grade,
                          "entry": g.entry, "sl": g.sl, "tp": g.tp, "sl_pips": g.sl_pips,
                          "tp_pips": g.tp_pips, "lots": g.lots, "risk_pct": g.risk_pct,
                          "session": g.session, "confirm_time": g.confirm.break_t,
                          "zones": g.zones, "htf_zone": g.htf_zone, "obstacle": g.obstacle,
                          "warnings": g.warnings, "key": g.key},
                         _specs(st, a, ("D1", "H4", "H1"), plan=charts.plan_from_signal(g))))
    if "W1" in new_tfs or "D1" in new_tfs:
        title = "Weekly + daily briefing" if "W1" in new_tfs else "Daily briefing"
        rng = _ranges_text(st, a)
        tfs = ("MN1", "W1", "D1", "H4") if "W1" in new_tfs else ("W1", "D1", "H4")
        msgs.append((f"🗓 {st.symbol} {title}", fmt_brief(a, title) + ("\n\n" + rng if rng else ""),
                     {"kind": "brief", "symbol": st.symbol}, _specs(st, a, tfs)))
    if len(st.notified) > 2000:
        st.notified = set(list(st.notified)[-1000:])
    return msgs
