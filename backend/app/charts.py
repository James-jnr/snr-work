"""Per-timeframe level charts: ONE timeframe alone per image (PNG).

What you see is exactly what the engine sees on that timeframe:
  * the price line (closes, as in the course) or candles
  * every active level it would use (red = resistance, green = support,
    dashed = open-close level, thick = flipped RBS/SBR level)
  * swing closes that created the levels (A peaks / V troughs)
  * rejections (arrows), the story line (shaded) with its rejected level and structural breakout level
  * on setup alerts: entry / SL / TP and the refined zones

Specs are plain snapshots (no live references), so rendering can happen outside the data lock.
"""
from __future__ import annotations

import io
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from . import config  # noqa: E402
from .analysis import Analysis, analyze  # noqa: E402
from .engine import Params, scan  # noqa: E402
from .state import STORE, SymbolState  # noqa: E402

TF_NAME = {"MN1": "Monthly", "W1": "Weekly", "D1": "Daily", "H4": "4-Hour", "H1": "1-Hour"}
DEFAULT_BARS = {"MN1": 40, "W1": 80, "D1": 90, "H4": 120, "H1": 120}
MERGE = {"MN1": 0.0015, "W1": 0.0010, "D1": 0.0006, "H4": 0.0004, "H1": 0.0003}  # of price
MAX_REJECTIONS = 15
KIND_TXT = {"A": "A", "V": "V", "OC": "OC"}

THEMES = {
    "light": dict(bg="#ffffff", fg="#1b1f24", muted="#667085", grid="#eceff3", line="#1f2a44",
                  up="#0f9d58", down="#d93025", res="#d93025", sup="#0f9d58", price="#2563eb",
                  breakout="#7c3aed", box="#ffffff"),
    "dark": dict(bg="#14171c", fg="#e8eaed", muted="#9aa3af", grid="#262c33", line="#9db4ff",
                 up="#4cc38a", down="#ff7b72", res="#ff7b72", sup="#4cc38a", price="#6ea8fe",
                 breakout="#c4a1ff", box="#14171c"),
}
_lock = threading.Lock()   # matplotlib is not fully thread-safe


@dataclass
class ChartSpec:
    symbol: str
    tf: str
    bars: list                      # [(t, o, h, l, c)] visible window, oldest first
    levels: List[dict]              # price, kind, role, t, flips
    rejections: list                # (visible_index, "bear"/"bull", price)
    story: Optional[dict]
    plan: Optional[dict]
    context: str
    pip: float
    digits: int
    gmt_offset: int
    price: float
    last_t: int
    style: str = "line"
    theme: str = "light"
    note: str = ""
    extra: dict = field(default_factory=dict)


# ------------------------------------------------------------------ building a spec
def make_spec(st: SymbolState, a: Analysis, tf: str, bars: int = 0, style: Optional[str] = None,
              theme: Optional[str] = None, max_levels: int = 12, plan: Optional[dict] = None,
              all_levels: bool = False) -> ChartSpec:
    allbars = st.series(tf)
    if len(allbars) < 8:
        raise LookupError(f"{TF_NAME.get(tf, tf)} history is too short ({len(allbars)} candles)")
    n = bars or DEFAULT_BARS.get(tf, 100)
    n = max(20, min(n, len(allbars))) if len(allbars) >= 20 else len(allbars)
    first = len(allbars) - n
    vis = allbars[first:]
    p = Params(a.pip, config.PIVOT_N, config.STRUCTURE_W, config.TAP_TOL_PIPS)
    rej, _, act = scan(tf, allbars, p, start=first)

    last = vis[-1].c
    lo, hi = min(b.l for b in vis), max(b.h for b in vis)
    pad = (hi - lo) * 0.10
    cand = [l for l in act if lo - pad <= l.price <= hi + pad]
    merge = (2 * a.pip) if all_levels else max(MERGE.get(tf, 0.0005) * last, 0.022 * (hi - lo))

    def pick(pool, cap):
        out: List = []
        for l in sorted(pool, key=lambda l: abs(l.price - last)):
            if all(abs(l.price - c.price) > merge for c in out):
                out.append(l)
                if cap and len(out) >= cap:
                    break
        return out

    half = max(1, (max_levels + 1) // 2)      # same number above and below price
    cap = 0 if all_levels else half
    chosen = pick([l for l in cand if l.price >= last], cap) + pick([l for l in cand if l.price < last], cap)

    story = None
    s = a.stories.get(tf)
    levels = [dict(price=l.price, kind=l.kind, role=l.role, t=l.t, flips=l.flips) for l in chosen]
    if s:
        story = dict(dir=s.dir, bar_t=s.bar_t, eff=s.eff, level=s.level, kind=s.level_kind,
                     breakout=s.breakout_level, ltf=s.ltf, same=s.same_bar)
        if not any(abs(x["price"] - s.level) < 1e-9 for x in levels):   # always show the rejected level
            match = next((l for l in act if abs(l.price - s.level) < 1e-9), None)
            levels.append(dict(price=s.level, kind=s.level_kind, role=match.role if match else "R",
                               t=match.t if match else vis[0].t, flips=match.flips if match else 0))

    shown = {round(x["price"], 8) for x in levels}
    rows = [(r.k - first, r.dir, r.price) for r in rej if r.k >= first and round(r.price, 8) in shown]
    total_rej = len(rows)
    rows = rows[-MAX_REJECTIONS:]      # keep images readable; the engine still counts all of them

    ctx = []
    for k in ("MN1", "W1", "D1"):
        if k in a.stories:
            st_k = a.stories[k]
            ctx.append(f"{TF_NAME[k]} " + ("no story line" if not st_k else
                                          ("BEARISH" if st_k.dir == "bear" else "BULLISH")))
    return ChartSpec(
        symbol=st.symbol, tf=tf, bars=[tuple(b) for b in vis], levels=levels, rejections=rows,
        story=story, plan=plan, context="Story lines: " + "  |  ".join(ctx) if ctx else "",
        pip=a.pip, digits=int(a.meta.get("digits", 2)), gmt_offset=int(a.meta.get("gmt_offset", 0)),
        price=a.price, last_t=a.last_t, extra={"rejections_total": total_rej},
        style=(style or config.CHART_STYLE) if (style or config.CHART_STYLE) in ("line", "candles") else "line",
        theme=(theme or config.CHART_THEME) if (theme or config.CHART_THEME) in THEMES else "light")


def spec_for(symbol: str, tf: str, bars: int = 0, style: Optional[str] = None, theme: Optional[str] = None,
             max_levels: int = 12, all_levels: bool = False) -> ChartSpec:
    """Live spec for the web UI (takes the data lock briefly)."""
    with STORE.lock:
        st = STORE.peek(symbol)
        if st is None or not st.ready():
            raise LookupError(f"not enough data yet for {symbol}: is the MT5 EA running?")
        a = analyze(st)
        if a is None:
            raise LookupError("not enough data yet")
        return make_spec(st, a, tf, bars, style, theme, max_levels, None, all_levels)


def plan_from_signal(g) -> Optional[dict]:
    # SL is deliberately user-defined; do not draw a fictitious risk box.
    if g.entry is None or g.sl is None or g.tp is None:
        return None
    return dict(dir=g.dir, entry=g.entry, sl=g.sl, tp=g.tp, zones=g.zones, grade=g.grade)


def levels_for(symbol: str, tf: str, limit: int = 80) -> dict:
    """Table data: every active level on this timeframe, nearest first."""
    with STORE.lock:
        st = STORE.peek(symbol)
        if st is None or not st.ready():
            raise LookupError("not enough data yet")
        a = analyze(st)
        allbars = st.series(tf)
        if len(allbars) < 8:
            raise LookupError(f"{TF_NAME.get(tf, tf)} history is too short")
        p = Params(a.pip, config.PIVOT_N, config.STRUCTURE_W, config.TAP_TOL_PIPS)
        _, _, act = scan(tf, allbars, p, start=len(allbars))
        price, pip, off = allbars[-1].c, a.pip, int(a.meta.get("gmt_offset", 0))
        tz = _tz()
        rows = []
        for l in sorted(act, key=lambda l: abs(l.price - price))[:limit]:
            rows.append(dict(price=l.price, role="resistance" if l.role == "R" else "support",
                             kind={"A": "peak (A)", "V": "trough (V)", "OC": "open-close"}[l.kind],
                             flips=l.flips, pips=round((l.price - price) / pip, 1),
                             formed=_dt(l.t, off, tz).strftime("%d %b %Y %H:%M")))
        return {"symbol": symbol, "tf": tf, "price": price, "digits": int(a.meta.get("digits", 2)),
                "levels": rows, "total_active": len(act)}


def meta_for() -> dict:
    """Which symbols have data, and the current story lines (for the charts page header)."""
    out = {}
    with STORE.lock:
        for st in STORE.all():
            if not st.ready():
                out[st.symbol] = {"ready": False}
                continue
            a = analyze(st)
            tz, off = _tz(), int(st.meta.get("gmt_offset", 0))
            out[st.symbol] = {
                "ready": True, "price": a.price, "digits": int(st.meta.get("digits", 2)),
                "updated": _dt(a.last_t, off, tz).strftime("%a %d %b %H:%M %Z"),
                "bars": {tf: len(st.bars[tf]) for tf in TF_NAME},
                "stories": {tf: (None if not s else dict(
                    dir=s.dir, since=_dt(s.eff, off, tz).strftime("%d %b %Y"), level=s.level,
                    breakout=s.breakout_level, same=s.same_bar)) for tf, s in a.stories.items()}}
    return out


# ------------------------------------------------------------------ rendering
def _tz():
    try:
        return ZoneInfo(config.DISPLAY_TZ)
    except Exception:
        return timezone.utc


def _dt(ts: int, off: int, tz) -> datetime:
    return datetime.fromtimestamp(ts - off, timezone.utc).astimezone(tz)


def _spread(items: List[dict], gap: float, lo: float, hi: float):
    """Push label y-positions apart in both directions (lines stay at their true price)."""
    items.sort(key=lambda d: d["price"])
    for it in items:
        it["y"] = it["price"]
    for _ in range(80):
        moved = False
        for a, b in zip(items, items[1:]):
            d = b["y"] - a["y"]
            if d < gap - 1e-9:
                push = (gap - d) / 2
                a["y"] -= push
                b["y"] += push
                moved = True
        for it in items:
            it["y"] = min(hi - gap / 2, max(lo + gap / 2, it["y"]))
        if not moved:
            break


def render(spec: ChartSpec) -> bytes:
    pal = THEMES.get(spec.theme, THEMES["light"])
    bars, n = spec.bars, len(spec.bars)
    tz, off = _tz(), spec.gmt_offset
    d = spec.digits
    fmt = lambda v: f"{v:.{d}f}"
    tidx = {b[0]: i for i, b in enumerate(bars)}

    ys = [b[3] for b in bars] + [b[2] for b in bars] + [spec.price]
    plan = spec.plan if spec.plan and spec.plan.get("entry") else None
    if plan:
        ys += [plan["entry"], plan["sl"], plan["tp"]]
    if spec.story:
        ys += [spec.story["level"], spec.story["breakout"]]
    ylo, yhi = min(ys), max(ys)
    span = (yhi - ylo) or 1.0
    ylo, yhi = ylo - span * 0.06, yhi + span * 0.06
    span = yhi - ylo
    xr = n + 1.5   # right edge of axes (levels extend here)

    with _lock:
        fig = Figure(figsize=(12, 6.8), dpi=100, facecolor=pal["bg"])
        FigureCanvasAgg(fig)
        ax = fig.add_axes([0.055, 0.115, 0.775, 0.735], facecolor=pal["bg"])
        ax.set_xlim(-1, xr)
        ax.set_ylim(ylo, yhi)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt(v)))
        ax.grid(axis="y", color=pal["grid"], linewidth=0.8, zorder=0)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(pal["grid"])
        ax.tick_params(colors=pal["muted"], labelsize=8.5)

        # x ticks = dates
        step = max(1, n // 8)
        pos = list(range(0, n, step))
        tf_fmt = {"MN1": "%b %Y", "W1": "%d %b %y", "D1": "%d %b", "H4": "%d %b %H:%M", "H1": "%d %b %H:%M"}[spec.tf]
        ax.set_xticks(pos)
        ax.set_xticklabels([_dt(bars[i][0], off, tz).strftime(tf_fmt) for i in pos])

        # story shading + rejection candle
        if spec.story:
            s = spec.story
            col = pal["res"] if s["dir"] == "bear" else pal["sup"]
            xi = tidx.get(s["bar_t"], -1)
            ax.axvspan(xi - 0.5, xr, color=col, alpha=0.07, zorder=1, linewidth=0)
            if xi >= 0:
                b = bars[xi]
                bear = s["dir"] == "bear"
                ax.annotate("rejection", xy=(xi, b[2] if bear else b[3]), xytext=(0, 26 if bear else -26),
                            textcoords="offset points", ha="center", color=col, fontsize=8.5, fontweight="bold",
                            arrowprops=dict(arrowstyle="->", color=col, lw=1.2), zorder=8)

        # price
        if spec.style == "candles":
            w = 0.6
            tiny = span * 0.0008
            for i, b in enumerate(bars):
                c = pal["up"] if b[4] >= b[1] else pal["down"]
                ax.vlines(i, b[3], b[2], colors=c, linewidth=1.0, zorder=4)
                ax.add_patch(Rectangle((i - w / 2, min(b[1], b[4])), w, max(abs(b[4] - b[1]), tiny),
                                       facecolor=c, edgecolor=c, zorder=5))
        else:
            xs = list(range(n))
            cl = [b[4] for b in bars]
            ax.plot(xs, cl, color=pal["line"], linewidth=1.6, zorder=5, solid_joinstyle="round")
            ax.scatter(xs, cl, s=7, color=pal["line"], zorder=5)

        # levels
        labels = []
        for lv in spec.levels:
            res = lv["role"] == "R"
            col = pal["res"] if res else pal["sup"]
            x0 = tidx.get(lv["t"], 0)
            flipped = lv["flips"] > 0
            is_story = bool(spec.story and abs(lv["price"] - spec.story["level"]) < 1e-9)
            ax.hlines(lv["price"], x0, xr, colors=col, linewidth=2.6 if is_story else 1.3,
                      linestyles="--" if lv["kind"] == "OC" else "-", alpha=0.95 if is_story else 0.85, zorder=3)
            if lv["kind"] in ("A", "V") and lv["t"] in tidx:   # the swing close that created it
                ax.scatter([tidx[lv["t"]]], [lv["price"]], s=44, marker="v" if lv["kind"] == "A" else "^",
                           color=col, edgecolor=pal["bg"], linewidth=0.8, zorder=7)
            txt = f"{fmt(lv['price'])}  {'R' if res else 'S'}·{KIND_TXT[lv['kind']]}"
            if flipped:
                txt += " flip"
            if is_story:
                txt += f"  ◀ story (rej. {'S' if spec.story['dir'] == 'bull' else 'R'})"
            labels.append(dict(price=lv["price"], text=txt, color=col, bold=is_story, bbox=False))

        # rejections
        for xi, direction, price in spec.rejections:
            if 0 <= xi < n:
                b = bars[xi]
                off_y = span * 0.018
                if direction == "bear":
                    ax.scatter([xi], [b[2] + off_y], s=30, marker="v", color=pal["res"], zorder=8, linewidth=0)
                else:
                    ax.scatter([xi], [b[3] - off_y], s=30, marker="^", color=pal["sup"], zorder=8, linewidth=0)

        # Structural breakout level of the story line
        if spec.story:
            q = spec.story["breakout"]
            ax.hlines(q, max(tidx.get(spec.story["bar_t"], 0), 0), xr, colors=pal["breakout"], linewidth=1.3,
                      linestyles=":", zorder=3)
            labels.append(dict(price=q, text=f"{fmt(q)}  CHOCH ({spec.story['ltf']})", color=pal["breakout"], bold=False, bbox=False))

        # trade plan
        if plan:
            xe = n - 1
            e, sl, tp = plan["entry"], plan["sl"], plan["tp"]
            ax.add_patch(Rectangle((xe, min(e, sl)), xr - xe, abs(e - sl), facecolor=pal["res"], alpha=0.14, zorder=2, linewidth=0))
            ax.add_patch(Rectangle((xe, min(e, tp)), xr - xe, abs(e - tp), facecolor=pal["sup"], alpha=0.14, zorder=2, linewidth=0))
            for val, nm, c, ls in ((e, "ENTRY", pal["price"], "-"), (sl, "SL", pal["res"], "--"), (tp, "TP", pal["sup"], "--")):
                ax.hlines(val, xe, xr, colors=c, linewidth=1.6, linestyles=ls, zorder=6)
                labels.append(dict(price=val, text=f"{fmt(val)}  {nm}", color=c, bold=True, bbox=False))
            for z in plan.get("zones") or []:
                ax.hlines(z["price"], max(0, n - 12), xr, colors=pal["price"], linewidth=1.0, linestyles=":", zorder=3)
                labels.append(dict(price=z["price"], text=f"{fmt(z['price'])}  zone {z['tf']}", color=pal["price"], bold=False, bbox=False))

        # current price
        ax.hlines(spec.price, 0, xr, colors=pal["price"], linewidth=1.0, linestyles=(0, (2, 2)), zorder=6)
        labels.append(dict(price=spec.price, text=f"{fmt(spec.price)}  price", color="#ffffff", bold=True,
                           bbox=True, bg=pal["price"]))

        # right-hand labels (de-overlapped)
        _spread(labels, span * 0.034, ylo, yhi)
        tr = ax.get_yaxis_transform()
        for it in labels:
            kw = dict(color=it["color"], fontsize=8.4, va="center", ha="left",
                      fontweight="bold" if it["bold"] else "normal", annotation_clip=False)
            if it.get("bbox"):
                kw["bbox"] = dict(boxstyle="round,pad=0.22", facecolor=it["bg"], edgecolor="none")
            ax.annotate(it["text"], xy=(1.0, it["price"]), xycoords=tr, xytext=(1.012, it["y"]), textcoords=tr,
                        arrowprops=dict(arrowstyle="-", color=it["color"] if not it.get("bbox") else it["bg"], lw=0.6)
                        if abs(it["y"] - it["price"]) > span * 0.004 else None, **kw)

        # header / footer
        upd = _dt(spec.last_t, off, tz).strftime("%a %d %b %H:%M %Z")
        fig.text(0.055, 0.945, f"{spec.symbol}  ·  {TF_NAME[spec.tf]}", color=pal["fg"], fontsize=17,
                 fontweight="bold", ha="left", va="center")
        fig.text(0.945, 0.945, f"price {fmt(spec.price)}   ·   {upd}", color=pal["muted"], fontsize=9.5, ha="right", va="center")
        tot = spec.extra.get("rejections_total", 0)
        sub = (f"This timeframe alone · levels from closing prices · "
               f"{'candles' if spec.style == 'candles' else 'line of closes'} · last {n} candles"
               + (f" · last {len(spec.rejections)} of {tot} rejections shown" if tot > len(spec.rejections) else ""))
        fig.text(0.055, 0.905, sub, color=pal["muted"], fontsize=9, ha="left", va="center")
        line3 = spec.context
        if spec.story:
            s = spec.story
            side = "BEARISH" if s["dir"] == "bear" else "BULLISH"
            line3 = (f"{TF_NAME[spec.tf]} story line {side} since {_dt(s['eff'], off, tz).strftime('%d %b %Y')}: "
                     f"rejected {fmt(s['level'])}, {s['ltf']} structural breakout at {fmt(s['breakout'])} "
                     f"({'same candle' if s['same'] else 'following candle'})")
        if plan:
            line3 = f"{plan['dir'].upper()} setup (grade {plan.get('grade', '?')})   " + (spec.context or "")
        if line3:
            fig.text(0.055, 0.872, line3, color=pal["fg"], fontsize=9, ha="left", va="center")

        handles = [Line2D([0], [0], color=pal["res"], lw=1.6, label="Resistance (R)"),
                   Line2D([0], [0], color=pal["sup"], lw=1.6, label="Support (S)"),
                   Line2D([0], [0], color=pal["muted"], lw=1.4, ls="--", label="Open-close level (OC)"),
                   Line2D([0], [0], color="none", label="flip = broken, now reused (RBS/SBR)"),
                   Line2D([0], [0], color=pal["muted"], marker="v", ls="", ms=6, label="A / V swing close"),
                   Line2D([0], [0], color=pal["res"], marker="v", ls="", ms=6, label="Rejection")]
        fig.legend(handles=handles, loc="lower center", ncol=6, frameon=False, fontsize=8,
                   labelcolor=pal["muted"], bbox_to_anchor=(0.43, 0.005))

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=pal["bg"])
        return buf.getvalue()
