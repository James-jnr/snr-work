"""Human-readable alert text (plain text so it works in Telegram and email)."""
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from . import config
from .analysis import Analysis, Signal, nearest
from .engine import LTF_OF, Story

NAMES = {"MN1": "Monthly", "W1": "Weekly", "D1": "Daily"}
KIND = {"A": "peak", "V": "trough", "OC": "open-close"}


def _tz():
    try:
        return ZoneInfo(config.DISPLAY_TZ)
    except Exception:
        return timezone.utc


def fmt_t(ts: int, meta: dict) -> str:
    utc = datetime.fromtimestamp(ts - int(meta.get("gmt_offset", 0)), timezone.utc)
    loc = utc.astimezone(_tz())
    return loc.strftime("%a %d %b %H:%M ") + (loc.tzname() or "UTC")


def fmt_d(ts: int, meta: dict) -> str:
    utc = datetime.fromtimestamp(ts - int(meta.get("gmt_offset", 0)), timezone.utc)
    return utc.astimezone(_tz()).strftime("%a %d %b")


def px(a: Analysis, v: float) -> str:
    return f"{v:.{int(a.meta.get('digits', 2))}f}"


def story_line(a: Analysis, tf: str, s: Optional[Story]) -> str:
    if s is None:
        return f"{NAMES[tf]}: no active story line"
    arrow = "BEARISH 🔻" if s.dir == "bear" else "BULLISH 🔺"
    q = "same candle (best)" if s.same_bar else "following candle (acceptable)"
    return (f"{NAMES[tf]}: {arrow} since {fmt_d(s.eff, a.meta)}\n"
            f"   rejected {px(a, s.level)} ({KIND[s.level_kind]}), "
            f"{LTF_OF[tf]} QM at {px(a, s.qm_level)}, breakout in {q}")


def alignment(a: Analysis) -> str:
    dirs = [s.dir for s in a.stories.values() if s]
    if len(dirs) >= 2 and len(set(dirs)) == 1 and len(dirs) == len([k for k in a.stories]):
        return "ALL story lines aligned: strong momentum expected, bigger moves possible"
    if len(set(dirs)) > 1:
        return "Story lines conflict: expect pullbacks / consolidation, prefer the higher timeframe"
    return "Partial picture: some timeframes have no active story line"


def _pips(a, x, y):
    return abs(x - y) / a.pip


def zones_block(a: Analysis, s: Story) -> str:
    bear = s.dir == "bear"
    entry_tf = LTF_OF[s.tf]
    lv_hi, lv_lo = a.levels.get(s.tf, []), a.levels.get(entry_tf, [])
    m = 2 * a.pip
    if bear:
        zones = nearest(lv_hi, a.price, "R", "above", 2, m)
        refine = nearest(lv_lo, a.price, "R", "above", 3, m)
        obs = nearest(lv_lo, a.price, "S", "below", 3, m)
        zl, ol = "Sell zones", "Obstacles / take-profit (supports)"
    else:
        zones = nearest(lv_hi, a.price, "S", "below", 2, m)
        refine = nearest(lv_lo, a.price, "S", "below", 3, m)
        obs = nearest(lv_lo, a.price, "R", "above", 3, m)
        zl, ol = "Buy zones", "Obstacles / take-profit (resistances)"
    f = lambda ls, tf: ", ".join(f"{px(a, l.price)} ({_pips(a, l.price, a.price):.0f}p)" for l in ls) or "none near price"
    return (f"{zl} on {NAMES[s.tf]}: {f(zones, s.tf)}\n"
            f"Refine on {entry_tf}: {f(refine, entry_tf)}\n"
            f"{ol} on {entry_tf}: {f(obs, entry_tf)}")


def fmt_story_alert(a: Analysis, tf: str, s: Story) -> str:
    side = "SELLS" if s.dir == "bear" else "BUYS"
    return (f"{a.symbol}  price {px(a, a.price)}\n\n"
            f"{story_line(a, tf, s)}\n\n"
            f"Plan: look for {side} from {NAMES[tf]} zones, refine on {LTF_OF[tf]}/H4/H1, "
            f"wait for the confirmation setup.\n\n{zones_block(a, s)}\n\n"
            f"{alignment(a)}")


def fmt_signal(a: Analysis, g: Signal) -> str:
    c = g.confirm
    word = "SELL 🔻" if g.dir == "sell" else "BUY 🔺"
    rej = "resistance" if g.dir == "sell" else "support"
    brk = "QM (structure break)" if c.break_type == "QM" else "open-close level break"
    d1, w1, mn = a.stories.get("D1"), a.stories.get("W1"), a.stories.get("MN1")
    want = "bear" if g.dir == "sell" else "bull"
    chk = lambda s: "n/a" if s is None else ("agrees ✅" if s.dir == want else "against ❌")
    zh = g.htf_zone
    lines = [f"{a.symbol}  {word}  grade {g.grade}  ({g.session})", "",
             f"At {zh['tf']} {rej} {px(a, zh['price'])} ({zh['pips']:.0f}p from the rejected level)",
             f"H4 rejected {rej} {px(a, c.rej_price)} ({KIND[c.rej_kind]}), then H1 {brk} "
             f"closed {fmt_t(c.break_t, a.meta)}",
             f"Story lines: D1 {chk(d1)} | W1 {chk(w1)}" + (f" | MN1 {chk(mn)}" if mn else ""), ""]
    if g.entry:
        zs = "  /  ".join(f"{px(a, z['price'])} ({z['tf']} {KIND[z['kind']]})" for z in g.zones)
        lines += [f"Entry zones (limit, enter on the NEXT candle into the zone): {zs}",
                  f"Plan from {px(a, g.entry)}: SL {g.sl_pips:.0f}p -> {px(a, g.sl)} | "
                  f"TP {g.tp_pips:.0f}p -> {px(a, g.tp)} (1:{g.tp_pips / g.sl_pips:.1f})"]
        if g.obstacle:
            o = g.obstacle
            lines.append(f"Nearest obstacle: {o['tf']} level {px(a, o['price'])} ({o['pips']:.0f}p): "
                         f"consider banking profit there")
        if g.lots:
            lines.append(f"Risk {g.risk_pct:.2f}% of {a.meta.get('balance', 0):.0f} "
                         f"{a.meta.get('currency', '')} -> about {g.lots} lots")
        else:
            lines.append(f"Suggested risk {g.risk_pct:.2f}% (no balance/tick data to size lots)")
    else:
        lines.append(f"Suggested risk {g.risk_pct:.2f}%")
    for w in g.warnings:
        lines.append(f"⚠️ {w}")
    lines += ["", "Manual checks: trend-line confluence (break & retest / continuation) at the zone, "
                  "news today?, still outside Asia, no catching a falling knife."]
    return "\n".join(lines)


def levels_map(a: Analysis) -> str:
    """Compact text version of the charts: nearest levels above/below price on each timeframe."""
    lines = ["Level map (R = resistance, S = support, pips from price):"]
    for tf in ("MN1", "W1", "D1", "H4"):
        lv = a.levels.get(tf)
        if not lv:
            continue
        merge = 3 * a.pip

        def pick(pool):
            out = []
            for l in pool:
                if not out or abs(l.price - out[-1].price) > merge:
                    out.append(l)
                if len(out) == 2:
                    break
            return out
        above = pick(sorted([l for l in lv if l.price > a.price], key=lambda l: l.price))
        below = pick(sorted([l for l in lv if l.price < a.price], key=lambda l: -l.price))
        f = lambda l: f"{l.role} {px(a, l.price)} ({_pips(a, l.price, a.price):.0f}p)"
        lines.append(f"{tf:>3}: " + "  ".join(f(l) for l in reversed(above)) + "  |  price  |  "
                     + "  ".join(f(l) for l in below))
    return "\n".join(lines)


def fmt_brief(a: Analysis, title: str) -> str:
    out = [f"{a.symbol}  price {px(a, a.price)}  ({fmt_t(a.last_t, a.meta)})", ""]
    for tf in ("MN1", "W1", "D1"):
        if tf in a.stories:
            out.append(story_line(a, tf, a.stories[tf]))
    out += ["", alignment(a), ""]
    for tf in ("W1", "D1"):
        s = a.stories.get(tf)
        if s:
            out += [f"[{NAMES[tf]} story: {'sell' if s.dir == 'bear' else 'buy'} bias]", zones_block(a, s), ""]
    out.append(levels_map(a))
    return "\n".join(out).strip()


def fmt_ranges(a: Analysis, d1_prev, w1_prev, asia) -> str:
    parts = []
    if d1_prev:
        parts.append(f"Prev day  H {px(a, d1_prev.h)}  L {px(a, d1_prev.l)}")
    if w1_prev:
        parts.append(f"Prev week H {px(a, w1_prev.h)}  L {px(a, w1_prev.l)}")
    if asia:
        parts.append(f"Asia range H {px(a, asia[0])}  L {px(a, asia[1])} (liquidity)")
    return "\n".join(parts)
