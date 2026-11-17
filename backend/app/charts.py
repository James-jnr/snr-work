"""Charts: ONE timeframe alone per image, plus a zone map of the key levels of every timeframe.

A timeframe chart shows only the key levels and obstacles the engine selected for it (most relevant,
most recent, strongest), named in plain words with their strength score. Labels live in a side panel,
so nothing is clipped. Setup alerts draw the entry zone, the targets and the invalidation level.
Specs are plain snapshots (no live references) so rendering can happen outside the data lock.
"""
from __future__ import annotations

import io
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from . import config, zones  # noqa: E402
from .analysis import Analysis, analyze_live  # noqa: E402
from .engine import STORY_TFS, TF_NAME as _TF_NAME, Params, scan  # noqa: E402
from .state import STORE, SymbolState  # noqa: E402

TF_NAME = {**_TF_NAME, "ZM": "Zone map"}
DEFAULT_BARS = {"MN1": 30, "W1": 30, "D1": 30, "H4": 30, "H1": 30}
MAX_REJECTIONS = 12
TF_COLOR = {"MN1": "#7c3aed", "W1": "#2563eb", "D1": "#0d9488", "H4": "#ea580c", "H1": "#64748b"}
TF_WIDTH = {"MN1": 2.8, "W1": 2.3, "D1": 1.9, "H4": 1.5, "H1": 1.1}

THEMES = {
    "light": dict(bg="#ffffff", fg="#1b1f24", muted="#667085", grid="#eceff3", line="#1f2a44",
                  up="#0f9d58", down="#d93025", res="#d93025", sup="#0f9d58", price="#2563eb",
                  qm="#7c3aed", plan="#2563eb"),
    "dark": dict(bg="#14171c", fg="#e8eaed", muted="#9aa3af", grid="#262c33", line="#9db4ff",
                 up="#4cc38a", down="#ff7b72", res="#ff7b72", sup="#4cc38a", price="#6ea8fe",
                 qm="#c4a1ff", plan="#6ea8fe"),
}
_lock = threading.Lock()   # matplotlib is not fully thread-safe


@dataclass
class ChartSpec:
    symbol: str
    tf: str
    bars: list                      # [(t, o, h, l, c)] visible window, oldest first
    levels: List[dict]              # name, role, label, price, cls, strength, strong, t, flips
    rejections: list                # (visible_index, "bear"/"bull", price)
    story: Optional[dict]
    plan: Optional[dict]
    zones: List[dict]
    context: str
    pip: float
    digits: int
    gmt_offset: int
    price: float
    last_t: int
    style: str = "line"
    theme: str = "light"
    extra: dict = field(default_factory=dict)


@dataclass
class ZoneMapSpec:
    symbol: str
    bars: list                      # H4 window
    items: List[dict]               # key levels of every timeframe
    zones: List[dict]
    context: str
    digits: int
    gmt_offset: int
    price: float
    last_t: int
    theme: str = "light"
    tf: str = "ZM"


# ------------------------------------------------------------------ building specs
def _ctx(a: Analysis) -> str:
    parts = []
    for k in STORY_TFS:
        if k in a.stories:
            s = a.stories[k]
            parts.append(f"{TF_NAME[k]} " + ("no story line" if not s else ("BEARISH" if s.dir == "bear" else "BULLISH")))
    return "Story lines: " + "  |  ".join(parts) if parts else ""


def _ld(kl) -> dict:
    return dict(name=kl.name, role=kl.role, label=kl.label, price=kl.price, cls=kl.cls, strength=kl.strength,
                strong=kl.strong, t=kl.formed_t, flips=kl.flips, tf=kl.tf)


def make_spec(st: SymbolState, a: Analysis, tf: str, style: Optional[str] = None,
              theme: Optional[str] = None, plan: Optional[dict] = None, all_levels: bool = False) -> ChartSpec:
    allbars = a.series.get(tf) or st.series(tf)
    if len(allbars) < 8:
        raise LookupError(f"{TF_NAME.get(tf, tf)} history is too short ({len(allbars)} candles)")
    n = DEFAULT_BARS.get(tf, 30)
    n = max(20, min(n, len(allbars))) if len(allbars) >= 20 else len(allbars)
    first = len(allbars) - n
    vis = allbars[first:]
    p = Params(a.pip, config.PIVOT_N, config.QM_W, config.TAP_TOL_PIPS)
    rej, _, act = scan(tf, allbars, p, start=first)

    sel = list(a.selected.get(tf, []))
    if plan and tf in a.levels:                   # classify by the SETUP's direction, not the Daily story line
        got = zones.pick(a, tf, "bear" if plan["dir"] == "sell" else "bull")
        sel = got["key"] + got["obstacles"]
        zones.mark_strong(a, {tf: sel})
    elif not sel:                                 # no story line serves this timeframe: strongest recent ones
        got = zones.pick(a, tf, None)
        sel = got["key"] + got["obstacles"]
    levels = [_ld(k) for k in sel]
    if all_levels:
        have = {round(x["price"], 8) for x in levels}
        from .engine import level_label, strength
        for lv in act:
            if round(lv.price, 8) not in have and len(allbars) - 1 - lv.idx <= 150:
                levels.append(dict(name=f"{TF_NAME[tf]} {'resistance' if lv.role == 'R' else 'support'} ({level_label(lv)})",
                                   role=lv.role, label=level_label(lv), price=lv.price, cls="context",
                                   strength=strength(lv, len(allbars)), strong=False, t=lv.t, flips=lv.flips, tf=tf))
    s = a.stories.get(tf)
    story = None
    if s:
        story = dict(dir=s.dir, bar_t=s.bar_t, eff=s.eff, level=s.level, qm=s.qm_level, ltf=s.ltf, same=s.same_bar,
                     reason=zones.story_reason(a, tf, s))
        if not any(abs(x["price"] - s.level) < 1e-9 for x in levels):
            lab = zones.label_for(a, tf, s.level, s.level_kind)
            role = "R" if s.dir == "bear" else "S"
            levels.append(dict(name=f"{TF_NAME[tf]} {'resistance' if role == 'R' else 'support'} ({lab})", role=role,
                               label=lab, price=s.level, cls="key level", strength=0, strong=False, t=vis[0].t,
                               flips=0, tf=tf))
    shown = {round(x["price"], 8) for x in levels}
    rows = [(r.k - first, r.dir, r.price) for r in rej if r.k >= first and round(r.price, 8) in shown]
    total = len(rows)
    rows = rows[-MAX_REJECTIONS:]
    zs = [z for z in a.zones if (not z.get("tfs") or tf in z["tfs"])]
    srv = zones.serving_story(a.stories, tf) if not plan else None
    serves = (f"levels serve the {TF_NAME[srv.tf]} story line ({'bearish' if srv.dir == 'bear' else 'bullish'}): "
              f"KEY = {'resistances' if srv.dir == 'bear' else 'supports'}, OBS = {'supports' if srv.dir == 'bear' else 'resistances'}"
              if srv else "")
    return ChartSpec(
        symbol=a.symbol, tf=tf, bars=[tuple(b) for b in vis], levels=levels, rejections=rows, story=story,
        plan=plan, zones=zs, context=_ctx(a), pip=a.pip, digits=a.digits,
        gmt_offset=int(a.meta.get("gmt_offset", 0)), price=a.price, last_t=a.last_t,
        style=(style or config.CHART_STYLE) if (style or config.CHART_STYLE) in ("line", "candles") else "line",
        theme=(theme or config.CHART_THEME) if (theme or config.CHART_THEME) in THEMES else "light",
        extra={"rejections_total": total, "serves": serves})


CAPS = {"MN1": 3, "W1": 4, "D1": 4, "H4": 4, "H1": 3}


def make_zonemap(st: SymbolState, a: Analysis, theme: Optional[str] = None) -> ZoneMapSpec:
    h4 = (a.series.get("H4") or [])[-100:]
    if len(h4) < 20:
        raise LookupError("4-hour history is too short for the zone map")
    lo, hi = min(b.l for b in h4), max(b.h for b in h4)
    pad = (hi - lo) * 0.18
    items = []
    for tf in ("MN1", "W1", "D1", "H4", "H1"):
        got = sorted(a.selected.get(tf, []), key=lambda k: (k.cls != "key level", -k.strength))[:CAPS[tf]]
        for kl in got:
            if lo - pad <= kl.price <= hi + pad:
                items.append(_ld(kl))
    zs = [z for z in a.zones if hi + pad >= z["low"] and lo - pad <= z["high"]]
    return ZoneMapSpec(a.symbol, [tuple(b) for b in h4], items, zs, _ctx(a), a.digits,
                       int(a.meta.get("gmt_offset", 0)), a.price, a.last_t,
                       (theme or config.CHART_THEME) if (theme or config.CHART_THEME) in THEMES else "light")


def spec_for(symbol: str, tf: str, style: Optional[str] = None, theme: Optional[str] = None,
             all_levels: bool = False):
    """Live spec for the web UI (takes the data lock briefly)."""
    with STORE.lock:
        st = STORE.peek(symbol)
        if st is None or not st.ready():
            raise LookupError(f"not enough data yet for {symbol}: is the MT5 EA running?")
        a = analyze_live(st)
        if a is None:
            raise LookupError("not enough data yet")
        if tf == "ZM":
            return make_zonemap(st, a, theme)
        return make_spec(st, a, tf, style, theme, None, all_levels)


def plan_from_setup(su: dict) -> Optional[dict]:
    e = su.get("refine") or su.get("entry")
    if not e:
        return None
    return dict(id=su["id"], dir=su["dir"], title=su["title"], entry=e["price"],
                zone=su["entry"]["price"],
                targets=[(t["n"], t["price"], t["name"]) for t in su.get("targets", [])],
                invalid=(su.get("invalid_if") or {}).get("price"))


def levels_for(symbol: str, tf: str, limit: int = 60) -> dict:
    """Table data for the charts page: selected key levels / obstacles first, then other recent levels."""
    from .engine import level_label, strength
    with STORE.lock:
        st = STORE.peek(symbol)
        if st is None or not st.ready():
            raise LookupError("not enough data yet")
        a = analyze_live(st)
        bars = a.series.get(tf) or []
        if len(bars) < 8:
            raise LookupError(f"{TF_NAME.get(tf, tf)} history is too short")
        price, pip, off, tz = a.price, a.pip, int(a.meta.get("gmt_offset", 0)), _tz()
        rows, have = [], set()
        for kl in sorted(a.selected.get(tf, []), key=lambda k: (k.cls != "key level", abs(k.price - price))):
            have.add(round(kl.price, 8))
            rows.append(dict(name=kl.name, price=kl.price, cls=kl.cls, strength=kl.strength, strong=kl.strong,
                             overlap=kl.overlap, flips=kl.flips, touches=kl.touches,
                             pips=round((kl.price - price) / pip, 1),
                             formed=_dt(kl.formed_t, off, tz).strftime("%d %b %Y %H:%M")))
        others = sorted([lv for lv in a.levels.get(tf, []) if round(lv.price, 8) not in have],
                        key=lambda l: abs(l.price - price))[:max(0, limit - len(rows))]
        for lv in others:
            rows.append(dict(name=f"{TF_NAME[tf]} {'resistance' if lv.role == 'R' else 'support'} ({level_label(lv)})",
                             price=lv.price, cls="other", strength=strength(lv, len(bars)), strong=False, overlap="",
                             flips=lv.flips, touches=lv.touches, pips=round((lv.price - price) / pip, 1),
                             formed=_dt(lv.t, off, tz).strftime("%d %b %Y %H:%M")))
        return {"symbol": symbol, "tf": tf, "price": price, "digits": a.digits, "levels": rows,
                "total_active": len(a.levels.get(tf, []))}


def meta_for() -> dict:
    """Which symbols have data, and the current story lines (for the page headers)."""
    out = {}
    with STORE.lock:
        for st in STORE.all():
            if not st.ready():
                out[st.symbol] = {"ready": False}
                continue
            a = analyze_live(st)
            tz, off = _tz(), int(st.meta.get("gmt_offset", 0))
            out[st.symbol] = {
                "ready": True, "price": a.price, "digits": a.digits,
                "updated": _dt(a.last_t, off, tz).strftime("%a %d %b %H:%M %Z"),
                "bars": {tf: len(st.bars[tf]) for tf in _TF_NAME},
                "stories": {tf: (None if not s else dict(
                    dir=s.dir, since=_dt(s.eff, off, tz).strftime("%d %b %Y"), level=s.level,
                    qm=s.qm_level, same=s.same_bar)) for tf, s in a.stories.items()}}
    return out


# ------------------------------------------------------------------ rendering helpers
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
    for _ in range(120):
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


def _panel_labels(ax, labels: List[dict], span: float, ylo: float, yhi: float, fs: float):
    _spread(labels, span * 0.036, ylo, yhi)
    tr = ax.get_yaxis_transform()
    for it in labels:
        kw = dict(color=it["color"], fontsize=fs, va="center", ha="left",
                  fontweight="bold" if it.get("bold") else "normal", annotation_clip=False)
        if it.get("bg"):
            kw["bbox"] = dict(boxstyle="round,pad=0.22", facecolor=it["bg"], edgecolor="none")
        far = abs(it["y"] - it["price"]) > span * 0.004
        ax.annotate(it["text"], xy=(1.0, it["price"]), xycoords=tr, xytext=(1.018, it["y"]), textcoords=tr,
                    arrowprops=dict(arrowstyle="-", color=it.get("line") or it["color"], lw=0.6) if far else None, **kw)


def _lab(it: dict, fmt) -> str:
    tag = "KEY" if it["cls"] == "key level" else "OBS" if it["cls"] == "obstacle" else "···"
    txt = f"{tag}  {it['name']}  {fmt(it['price'])}"
    if it.get("strength"):
        txt += f"  [{it['strength']}]"
    if it.get("strong"):
        txt += "  STRONG"
    return txt


def _setup_axes(fig, pal, ylo, yhi, n, xr, fmt, ticks, tf_fmt, bars, off, tz):
    ax = fig.add_axes([0.05, 0.12, 0.60, 0.72], facecolor=pal["bg"])
    ax.set_xlim(-1, xr)
    ax.set_ylim(ylo, yhi)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt(v)))
    ax.grid(axis="y", color=pal["grid"], linewidth=0.8, zorder=0)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(pal["grid"])
    ax.tick_params(colors=pal["muted"], labelsize=8.5)
    ax.set_xticks(ticks)
    ax.set_xticklabels([_dt(bars[i][0], off, tz).strftime(tf_fmt) for i in ticks])
    return ax


def _header(fig, pal, title, sub, line3, price_txt):
    fig.text(0.05, 0.945, title, color=pal["fg"], fontsize=17, fontweight="bold", ha="left", va="center")
    fig.text(0.95, 0.945, price_txt, color=pal["muted"], fontsize=9.5, ha="right", va="center")
    fig.text(0.05, 0.907, sub, color=pal["muted"], fontsize=9, ha="left", va="center")
    if line3:
        fig.text(0.05, 0.874, line3, color=pal["fg"], fontsize=9, ha="left", va="center")


# ------------------------------------------------------------------ one timeframe
def render(spec) -> bytes:
    if isinstance(spec, ZoneMapSpec):
        return render_zonemap(spec)
    pal = THEMES.get(spec.theme, THEMES["light"])
    bars, n = spec.bars, len(spec.bars)
    tz, off, d = _tz(), spec.gmt_offset, spec.digits
    fmt = lambda v: f"{v:.{d}f}"
    tidx = {b[0]: i for i, b in enumerate(bars)}
    plan = spec.plan if spec.plan and spec.plan.get("entry") else None

    ys = [b[3] for b in bars] + [b[2] for b in bars] + [spec.price]
    if plan:
        ys += [plan["entry"]] + [t[1] for t in plan["targets"][:2]] + ([plan["invalid"]] if plan.get("invalid") else [])
    if spec.story:
        ys += [spec.story["level"]]
    ylo, yhi = min(ys), max(ys)
    span = (yhi - ylo) or 1.0
    ylo, yhi = ylo - span * 0.07, yhi + span * 0.07
    span = yhi - ylo
    xr = n + 1.5
    step = max(1, n // 8)
    tf_fmt = {"MN1": "%b %Y", "W1": "%d %b %y", "D1": "%d %b", "H4": "%d %b %H:%M", "H1": "%d %b %H:%M"}[spec.tf]

    with _lock:
        fig = Figure(figsize=(14, 7.4), dpi=100, facecolor=pal["bg"])
        FigureCanvasAgg(fig)
        ax = _setup_axes(fig, pal, ylo, yhi, n, xr, fmt, list(range(0, n, step)), tf_fmt, bars, off, tz)

        for z in spec.zones:                      # strong zones: shaded bands
            if yhi >= z["low"] and ylo <= z["high"]:
                c = pal["res"] if z["role"] == "R" else pal["sup"]
                lo, hi = z["low"], max(z["high"], z["low"] + span * 0.004)
                ax.axhspan(lo, hi, color=c, alpha=0.10, zorder=1, linewidth=0)

        if spec.story:                            # story-line shading + rejection candle
            s = spec.story
            col = pal["res"] if s["dir"] == "bear" else pal["sup"]
            xi = tidx.get(s["bar_t"], -1)
            ax.axvspan(xi - 0.5, xr, color=col, alpha=0.06, zorder=1, linewidth=0)
            if xi >= 0:
                b = bars[xi]
                bear = s["dir"] == "bear"
                ax.annotate("rejection", xy=(xi, b[2] if bear else b[3]), xytext=(0, 26 if bear else -26),
                            textcoords="offset points", ha="center", color=col, fontsize=8.5, fontweight="bold",
                            arrowprops=dict(arrowstyle="->", color=col, lw=1.2), zorder=8)

        if spec.style == "candles":               # price
            tiny = span * 0.0008
            for i, b in enumerate(bars):
                c = pal["up"] if b[4] >= b[1] else pal["down"]
                ax.vlines(i, b[3], b[2], colors=c, linewidth=1.0, zorder=4)
                ax.add_patch(Rectangle((i - 0.3, min(b[1], b[4])), 0.6, max(abs(b[4] - b[1]), tiny),
                                       facecolor=c, edgecolor=c, zorder=5))
        else:
            xs, cl = list(range(n)), [b[4] for b in bars]
            ax.plot(xs, cl, color=pal["line"], linewidth=1.6, zorder=5, solid_joinstyle="round")
            ax.scatter(xs, cl, s=7, color=pal["line"], zorder=5)

        labels = []                               # key levels and obstacles
        for lv in spec.levels:
            res = lv["role"] == "R"
            col = pal["res"] if res else pal["sup"]
            key = lv["cls"] == "key level"
            x0 = tidx.get(lv["t"], 0)
            ax.hlines(lv["price"], x0, xr, colors=col, linewidth=2.2 if key else 1.2,
                      linestyles="--" if lv["label"] == "OCL" else "-", alpha=0.95 if key else 0.75, zorder=3)
            if lv["label"] != "OCL" and lv["t"] in tidx:
                ax.scatter([tidx[lv["t"]]], [lv["price"]], s=40, marker="v" if res else "^", color=col,
                           edgecolor=pal["bg"], linewidth=0.8, zorder=7)
            labels.append(dict(price=lv["price"], text=_lab(lv, fmt), color=col, bold=key or lv.get("strong")))

        for xi, direction, price in spec.rejections:   # rejection arrows
            if 0 <= xi < n:
                b, o = bars[xi], span * 0.018
                if direction == "bear":
                    ax.scatter([xi], [b[2] + o], s=30, marker="v", color=pal["res"], zorder=8, linewidth=0)
                else:
                    ax.scatter([xi], [b[3] - o], s=30, marker="^", color=pal["sup"], zorder=8, linewidth=0)

        if spec.story:                            # QM level of the story line
            labels.append(dict(price=spec.story["qm"], text=f"QM breakout level  {fmt(spec.story['qm'])}",
                               color=pal["qm"]))
            ax.hlines(spec.story["qm"], max(tidx.get(spec.story["bar_t"], 0), 0), xr, colors=pal["qm"],
                      linewidth=1.2, linestyles=":", zorder=3)

        if plan:                                  # entry, targets, invalidation (no stop loss: you set it)
            xe = n - 1
            far = plan["targets"][-1][1] if plan["targets"] else plan["entry"]
            ax.add_patch(Rectangle((xe, min(plan["entry"], far)), xr - xe, abs(far - plan["entry"]),
                                   facecolor=pal["sup"], alpha=0.10, zorder=2, linewidth=0))
            ax.hlines(plan["entry"], xe - 6, xr, colors=pal["plan"], linewidth=2.0, zorder=6)
            labels.append(dict(price=plan["entry"], text=f"ENTRY (setup {plan['id']})  {fmt(plan['entry'])}",
                               color=pal["plan"], bold=True))
            for nn, p, nm in plan["targets"]:
                ax.hlines(p, xe, xr, colors=pal["sup"], linewidth=1.4, linestyles=":", zorder=6)
                labels.append(dict(price=p, text=f"T{nn}  {nm}  {fmt(p)}", color=pal["sup"], bold=True))
            if plan.get("invalid"):
                ax.hlines(plan["invalid"], xe - 6, xr, colors=pal["res"], linewidth=1.3, linestyles="--", zorder=6)
                labels.append(dict(price=plan["invalid"], text=f"Invalid if closes beyond  {fmt(plan['invalid'])}",
                                   color=pal["res"]))

        ax.hlines(spec.price, 0, xr, colors=pal["price"], linewidth=1.0, linestyles=(0, (2, 2)), zorder=6)
        labels.append(dict(price=spec.price, text=f"price  {fmt(spec.price)}", color="#ffffff", bold=True,
                           bg=pal["price"], line=pal["price"]))
        _panel_labels(ax, labels, span, ylo, yhi, 8.3)

        upd = _dt(spec.last_t, off, tz).strftime("%a %d %b %H:%M %Z")
        tot = spec.extra.get("rejections_total", 0)
        sub = (f"This timeframe alone · key levels and obstacles only · "
               f"{'candles' if spec.style == 'candles' else 'line of closes'} · last {n} candles"
               + (f" · last {len(spec.rejections)} of {tot} rejections shown" if tot > len(spec.rejections) else ""))
        if spec.extra.get("serves"):
            sub = f"This timeframe alone · {spec.extra['serves']} · last {n} candles"
        line3 = spec.context
        if spec.story:
            s = spec.story
            line3 = f"{TF_NAME[spec.tf]} story line {'BEARISH' if s['dir'] == 'bear' else 'BULLISH'} since {_dt(s['eff'], off, tz).strftime('%d %b %Y')}"
        if plan:
            line3 = f"{plan['dir'].upper()} · Setup {plan['id']}: {plan['title']}"
        _header(fig, pal, f"{spec.symbol}  ·  {TF_NAME[spec.tf]}", sub, line3, f"price {fmt(spec.price)}   ·   {upd}")

        handles = [Line2D([0], [0], color=pal["res"], lw=2.4, label="Resistance, key level (thick)"),
                   Line2D([0], [0], color=pal["sup"], lw=2.4, label="Support, key level (thick)"),
                   Line2D([0], [0], color=pal["muted"], lw=1.2, label="Obstacle (thin)"),
                   Line2D([0], [0], color=pal["muted"], lw=1.4, ls="--", label="Dashed = open-close level (OCL)"),
                   Line2D([0], [0], color=pal["muted"], marker="v", ls="", ms=6, label="Swing close (peak / trough)"),
                   Line2D([0], [0], color=pal["muted"], lw=6, alpha=0.25, label="Shaded band = strong zone")]
        fig.legend(handles=handles, loc="lower center", ncol=6, frameon=False, fontsize=8,
                   labelcolor=pal["muted"], bbox_to_anchor=(0.5, 0.005))
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=pal["bg"])
        return buf.getvalue()


# ------------------------------------------------------------------ zone map
def render_zonemap(spec: ZoneMapSpec) -> bytes:
    pal = THEMES.get(spec.theme, THEMES["light"])
    bars, n = spec.bars, len(spec.bars)
    tz, off, d = _tz(), spec.gmt_offset, spec.digits
    fmt = lambda v: f"{v:.{d}f}"
    lo, hi = min(b[3] for b in bars), max(b[2] for b in bars)
    pad = (hi - lo) * 0.18
    ys = [lo - pad, hi + pad] + [it["price"] for it in spec.items]
    ylo, yhi = min(ys), max(ys)
    span = (yhi - ylo) or 1.0
    ylo, yhi = ylo - span * 0.03, yhi + span * 0.03
    span = yhi - ylo
    xr = n + 1.5
    step = max(1, n // 8)

    with _lock:
        fig = Figure(figsize=(14, 7.4), dpi=100, facecolor=pal["bg"])
        FigureCanvasAgg(fig)
        ax = _setup_axes(fig, pal, ylo, yhi, n, xr, fmt, list(range(0, n, step)), "%d %b %H:%M", bars, off, tz)
        for z in spec.zones:
            c = pal["res"] if z["role"] == "R" else pal["sup"]
            ax.axhspan(z["low"], max(z["high"], z["low"] + span * 0.004), color=c, alpha=0.13, zorder=1, linewidth=0)
        xs, cl = list(range(n)), [b[4] for b in bars]
        ax.plot(xs, cl, color=pal["line"], linewidth=1.5, zorder=5)
        labels = []
        for it in spec.items:
            col = TF_COLOR[it["tf"]]
            key = it["cls"] == "key level"
            ax.hlines(it["price"], 0, xr, colors=col, linewidth=TF_WIDTH[it["tf"]] if key else TF_WIDTH[it["tf"]] * 0.6,
                      linestyles="-" if key else "--", alpha=0.9, zorder=3)
            labels.append(dict(price=it["price"], text=_lab(it, fmt), color=col, bold=bool(it.get("strong"))))
        ax.hlines(spec.price, 0, xr, colors=pal["price"], linewidth=1.0, linestyles=(0, (2, 2)), zorder=6)
        labels.append(dict(price=spec.price, text=f"price  {fmt(spec.price)}", color="#ffffff", bold=True,
                           bg=pal["price"], line=pal["price"]))
        _panel_labels(ax, labels, span, ylo, yhi, 8.6)
        upd = _dt(spec.last_t, off, tz).strftime("%a %d %b %H:%M %Z")
        _header(fig, pal, f"{spec.symbol}  ·  Zone map",
                "Key levels of every timeframe, refined Monthly → Weekly → Daily → 4-hour → 1-hour · "
                "shaded bands = strong zones (timeframes overlap)", spec.context, f"price {fmt(spec.price)}   ·   {upd}")
        handles = [Line2D([0], [0], color=TF_COLOR[t], lw=3, label=_TF_NAME[t]) for t in ("MN1", "W1", "D1", "H4", "H1")]
        handles += [Line2D([0], [0], color=pal["muted"], lw=2, label="Key level (solid)"),
                    Line2D([0], [0], color=pal["muted"], lw=1.2, ls="--", label="Obstacle (dashed)")]
        fig.legend(handles=handles, loc="lower center", ncol=7, frameon=False, fontsize=8,
                   labelcolor=pal["muted"], bbox_to_anchor=(0.5, 0.005))
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=pal["bg"])
        return buf.getvalue()
