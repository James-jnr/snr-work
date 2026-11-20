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
        if tf != "MN1":
            cx = zones.context(a, tf, "sell" if s.dir == "bear" else "buy")
            out.append(wrap("Context: " + cx["label"] + "." + (
                " Trades from this story line are retracement trades: targets stop at the parent's entry zone."
                if cx["kind"] == "retracement" else "")))
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


def thesis_block(a: Analysis) -> List[str]:
    out = ["THESIS (the plan for this pair)", LIGHT, wrap(a.thesis.get("headline", "No plan yet."))]
    ch = [c for c in (a.thesis_changes or []) if c != "No material change."]
    if a.thesis_prev and ch:
        out += ["", "  Changed since the previous thesis:"] + [wrap("- " + c, 4) for c in ch]
    elif a.thesis_prev:
        out += ["", "  No material change since the previous thesis."]
    return out + [""]


MARK = {True: "[x]", False: "[ ]", None: "[~]"}


def scenario_block(a: Analysis) -> List[str]:
    sc = a.scenarios or {}
    out = ["IF PRICE GOES THERE: TWO SCENARIOS", LIGHT]
    if not sc.get("items"):
        return out + [wrap(sc.get("note") or "No scenarios yet."), ""]
    out.append(wrap("Instead of predicting, wait for the trigger. If price goes to either point, this is what we "
                    "would look for. [x] = met now, [ ] = not yet, [~] = timing note."))
    out.append("")
    for it in sc["items"]:
        out.append(f"SCENARIO {it['id']} · {it['dir'].upper()} · {it['label']}")
        if not it["zone"]:
            out += [wrap("Waiting: " + it["reason"]), ""]
            continue
        z, d = it["zone"], it["distance_pips"]
        out.append(textwrap.fill(it["context"]["label"], width=W, initial_indent="  Context      : ", subsequent_indent=" " * 17))
        side = "above" if d > 0 else "below"
        out.append(f"  Where        : {px(a, z['price'])}  {z['name']}   ({abs(d):.0f} pips {side} price)")
        if z["strong"]:
            out.append(f"                 STRONG zone: overlaps {z['overlap']}")
        if it["refine"]:
            r = it["refine"]
            out.append(f"  Refine to    : {px(a, r['price'])}  {r['name']}" + ("  STRONG" if r["strong"] else ""))
        out.append("  Look for     :")
        for c in it["conditions"]:
            out.append(textwrap.fill(f"{MARK[c['met']]} {c['text']}", width=W, initial_indent="    ", subsequent_indent="        "))
        if it["cancel"] and config.SHOW_INVALIDATION:
            out.append(f"  Cancel if    : {it['cancel']['text']} {px(a, it['cancel']['price'])}")
        if it.get("ceiling"):
            out.append(f"  Ceiling      : {it['ceiling']['name']} at {px(a, it['ceiling']['price'])}  (retracement: targets stop here)")
        out.append("  Targets      :" + ("" if it["targets"] else " none found beyond the minimum distance"))
        for t in it["targets"]:
            out.append(f"    T{t['n']}  {px(a, t['price'])}  {t['name']}  (~{t['pips']:.0f} pips)")
            out.append(f"          {t['advice']}")
        status = {"waiting": f"Waiting for price: {abs(d):.0f} pips {side} the current price.",
                  "zone reached": "Zone reached: watching for the rejection and the breakout.",
                  "triggered": "TRIGGERED: every condition is met. Treat it as a live setup."}[it["status"]]
        out.append(f"  Status       : {status}")
        if it.get("upgrade"):
            out.append(textwrap.fill(it["upgrade"], width=W, initial_indent="  Full reversal: ", subsequent_indent=" " * 17))
        out.append("")
    return out


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
        head = f"SETUP {su['id']} · " + (f"{d} · " if d else "") + su["title"]
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
        if su.get("context"):
            out.append(textwrap.fill(su["context"]["label"], width=W, initial_indent="  Context      : ",
                                     subsequent_indent=" " * 17))
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
        if su.get("ceiling"):
            out.append(f"  Ceiling      : {su['ceiling']['name']} at {px(a, su['ceiling']['price'])}  (retracement: targets stop here)")
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
    out += thesis_block(a) + scenario_block(a)
    out += ["⏰ London and New York kill zones are the best entry times.", ""]
    out += setup_block(a)
    return "\n".join(out).rstrip()
