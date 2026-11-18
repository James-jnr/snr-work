"""Alert text in the mentor's wording. One report layout is used for every alert:
story lines, where we are now, key levels, liquidity, then the setups below a heavy divider."""
import textwrap
from datetime import datetime, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from . import config, zones
from .analysis import Analysis
from .engine import LTF_OF, STORY_TFS, TF_NAME

W = 76
HEAVY = "═" * 54
LIGHT = "─" * 54
SETUP_BAR = "▓" * 54
CLS_TXT = {"key level": "Key level", "obstacle": "Obstacle", "context": "Level"}


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
    return utc.astimezone(_tz()).strftime("%d %b")


def px(a: Analysis, v: float) -> str:
    return f"{v:.{a.digits}f}"


def wrap(text: str, indent: int = 2) -> str:
    pad = " " * indent
    return textwrap.fill(text, width=W, initial_indent=pad, subsequent_indent=pad)


def dword(d: str) -> str:
    return "bearish" if d == "bear" else "bullish"


def alignment(a: Analysis) -> str:
    order = [tf for tf in ("MN1", "W1", "D1") if a.stories.get(tf)]
    if not order:
        return "No Monthly, Weekly or Daily story line is active."
    dirs = {tf: a.stories[tf].dir for tf in order}
    if len(order) >= 2 and len(set(dirs.values())) == 1:
        names = ", ".join(TF_NAME[t] for t in order[:-1]) + " and " + TF_NAME[order[-1]]
        return f"{names} all agree ({dword(dirs[order[0]])}): strong momentum expected, bigger moves are possible."
    top = order[0]
    opp = [TF_NAME[t] for t in order[1:] if dirs[t] != dirs[top]]
    if opp:
        verb = "is" if len(opp) == 1 else "are"
        other = dword("bull" if dirs[top] == "bear" else "bear")
        return (f"{TF_NAME[top]} is {dword(dirs[top])}, but {' and '.join(opp)} {verb} {other}, so the lower "
                f"timeframes are pulling back against the {TF_NAME[top]} move: expect consolidation and prefer "
                f"the higher timeframe.")
    return f"Only the {TF_NAME[top]} story line is active ({dword(dirs[top])})."


def _mark(a: Analysis, tf: str, s, verify: Optional[Dict[str, str]]) -> str:
    st = (verify or {}).get(f"{a.symbol}|{tf}|{s.dir}|{s.eff}", "unverified")
    return {"verified": "[VERIFIED by you]", "rejected": "[REJECTED by you]"}.get(st, "[UNVERIFIED: tick it on the alert page]")


def story_block(a: Analysis, verify=None) -> List[str]:
    out = ["STORY LINES", LIGHT]
    for tf in STORY_TFS:
        if tf not in a.stories:
            continue
        s = a.stories[tf]
        nm = TF_NAME[tf].upper()
        if not s:
            if tf in a.story_rejected:
                out += [f"{nm}: ended by you (rejected the {dword(a.story_rejected[tf].dir)} story line)", ""]
            elif tf != "H4":
                out += [f"{nm}: no active story line", ""]
            continue
        out.append(f"{nm} – {dword(s.dir).upper()} since {fmt_d(s.eff, a.meta)}   {_mark(a, tf, s, verify)}")
        out.append(wrap(zones.story_reason(a, tf, s)))
        pv = a.story_prev.get(tf)
        if pv:
            same = pv.dir == s.dir
            out.append(wrap(f"{'Renewed' if same else 'This ended the previous'} {TF_NAME[tf]} "
                            f"{dword(pv.dir)} story line (since {fmt_d(pv.eff, a.meta)})"
                            f"{': same direction.' if same else '.'}"))
        out.append("")
    if not a.stories.get("H4") and "H4" in a.stories:
        out += ["4-HOUR: no active story line yet", ""]
    return out


def where_block(a: Analysis) -> List[str]:
    out = ["WHERE WE ARE NOW"]
    for tf in STORY_TFS:
        st = a.status.get(tf)
        if st:
            out.append(wrap(st["text"]))
    out.append(wrap(alignment(a)))
    return out + [""]


def levels_block(a: Analysis) -> List[str]:
    out = ["KEY LEVELS (only the relevant, recent ones)", LIGHT]
    any_story = False
    for tf in STORY_TFS:
        s = a.stories.get(tf)
        if not s:
            continue
        any_story = True
        sel = zones.story_levels(a, tf, s.dir)
        names = " and ".join(TF_NAME[t] for t in sel) or TF_NAME[LTF_OF[tf]]
        out.append(f"{TF_NAME[tf]} story line ({dword(s.dir)}) uses {names} levels:")
        rows = [kl for t in sel for kl in sel[t]]
        if not rows:
            out.append("  none near price")
        for kl in sorted(rows, key=lambda k: (k.cls != "key level", abs(k.price - a.price))):
            extra = "   (newest)" if kl.newest and kl.cls == "key level" else ""
            out.append(f"  {CLS_TXT[kl.cls]:<9} {kl.name:<36} {px(a, kl.price)}   strength {kl.strength}/100{extra}")
            if kl.strong:
                out.append(f"  {'':<9} STRONG zone: overlaps {kl.overlap}")
        out.append("")
    if not any_story:
        out += ["  No story line is active, so no levels are classified yet.", ""]
    if a.zones:
        near = sorted(a.zones, key=lambda z: abs((z["low"] + z["high"]) / 2 - a.price))[:4]
        out.append("STRONG ZONES (timeframes overlapping):")
        for z in near:
            role = "resistance" if z["role"] == "R" else "support"
            out.append(f"  {px(a, z['low'])} – {px(a, z['high'])}   {role}   {z['label']}")
        out.append("")
    return out


def liquidity_block(a: Analysis) -> List[str]:
    lq, out = a.liquidity, []
    if lq.get("prev_day"):
        out.append(f"Previous day   high {px(a, lq['prev_day'][0])}   low {px(a, lq['prev_day'][1])}")
    if lq.get("prev_week"):
        out.append(f"Previous week  high {px(a, lq['prev_week'][0])}   low {px(a, lq['prev_week'][1])}")
    if lq.get("asia"):
        out.append(f"Asian range    high {px(a, lq['asia'][0])}   low {px(a, lq['asia'][1])}   (first two 4-hour candles)")
    return (["LIQUIDITY"] + out + [""]) if out else []


def _al(a, su) -> str:
    parts = []
    for tf in ("W1", "D1", "MN1", "H4"):
        v = su["aligned"].get(tf)
        s = a.stories.get(tf)
        if v is None:
            parts.append(f"{TF_NAME[tf]} –")
        else:
            parts.append(f"{TF_NAME[tf]} {dword(s.dir)} {'✔' if v else '✘'}")
    return "  |  ".join(parts)


def setup_block(a: Analysis) -> List[str]:
    out = [SETUP_BAR, "SETUPS", SETUP_BAR, ""]
    if a.limits.get("blocked"):
        out += [f"⛔ {a.limits['blocked']}", ""]
    for su in a.setups:
        d = (su["dir"] or "").upper()
        head = (f"SETUP {su['id']} · " + (f"{d} · " if d else "") + (f"Grade {su['grade']} · " if su.get("grade") else "")
                + (f"15-min class {su['grade15']} · " if su.get("grade15") else "") + su["title"])
        out.append(head)
        if su["status"] == "waiting" or not su["entry"]:
            out += [wrap("Waiting: " + (su["reason"] or "conditions not met yet.")), ""]
            continue
        if su["status"] != "ready":
            out.append(wrap({"late": "Late: ", "asia": "Asia: ", "blocked": "Blocked: ",
                             "upgraded": "Upgraded: "}.get(su["status"], "") + su["reason"]))
        if su.get("cf_text"):
            out.append(wrap(su["cf_text"] + "."))
        out.append(f"  Aligned with : {_al(a, su)}")
        if su.get("grade"):
            out.append(f"  Grade        : {su['grade']}  (A = Weekly + Daily agree, B = Daily agrees, C = against Daily)")
        if su.get("grade15"):
            out.append(f"  15-min class : {su['grade15']}  ({su['grade15_why']})")
        e = su["entry"]
        tag = "  (newest)" if e["newest"] else ""
        out.append(f"  Entry zone   : {px(a, e['price'])}  {e['name']}{tag}")
        if e["strong"]:
            out.append(f"                 STRONG zone: overlaps {e['overlap']}")
        if su["refine"]:
            r = su["refine"]
            out.append(f"  Refine to    : {px(a, r['price'])}  {r['name']}" + ("  STRONG" if r["strong"] else ""))
        if su["invalid_if"] and config.SHOW_INVALIDATION:
            out.append(f"  Invalid if   : {su['invalid_if']['text']} {px(a, su['invalid_if']['price'])}")
        out.append(f"  Enter by     : {fmt_t(su['enter_by'], a.meta)}")
        out.append(textwrap.fill(su["stays_valid"], width=W, initial_indent="  Stays valid  : ",
                                 subsequent_indent=" " * 17))
        out.append("  Targets:")
        if not su["targets"]:
            out.append("    none found beyond the minimum distance")
        for t in su["targets"]:
            out.append(f"    T{t['n']}  {px(a, t['price'])}  {t['name']}  (~{t['pips']:.0f} pips)")
            out.append(f"          {t['advice']}")
        out.append("")
    return out


def fmt_report(a: Analysis, verify: Optional[Dict[str, str]] = None, headline: Optional[List[str]] = None) -> str:
    out = [f"{a.symbol}   price {px(a, a.price)}   {fmt_t(a.last_t, a.meta)}", HEAVY]
    if headline:
        out += headline + [""]
    out += story_block(a, verify) + where_block(a) + levels_block(a) + liquidity_block(a)
    out += ["⏰ London and New York kill zones are the best entry times.", ""]
    out += setup_block(a)
    return "\n".join(out).rstrip()
