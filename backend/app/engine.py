"""Causal SNR engine: A/V and OCL levels, rejection, and storyline CHOCH.

All calculations use closed candles only. Structural breakouts are deliberately
separate from Quasimodo (QM) patterns; this module does not label CHOCH as QM.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Tuple

TF_SEC = {"H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800, "MN1": 2678400}
LTF_OF = {"MN1": "W1", "W1": "D1", "D1": "H4"}

class Bar(NamedTuple):
    t: int; o: float; h: float; l: float; c: float

@dataclass
class Params:
    pip: float
    pivot_n: Dict[str, int]  # retained for compatibility; A/V no longer uses pivots
    structure_w: Dict[str, int]
    tap_tol_pips: float = 1.0

@dataclass
class Level:
    tf: str
    kind: str       # A, V, OC
    price: float
    t: int
    avail: int
    role: str       # R or S
    flips: int = 0
    uses: int = 0

@dataclass
class Rejection:
    k: int; dir: str; price: float; kind: str; lt: int; tf: str; flips: int

@dataclass
class StructureBreak:
    dir: str        # bull: close above protected LH; bear: close below protected HL
    i: int
    level: float
    protected_index: int
    trend: str

@dataclass
class Story:
    tf: str; ltf: str; dir: str; k: int; bar_t: int; eff: int
    level: float; level_kind: str; breakout_level: float; breakout_close_t: int
    same_bar: bool

@dataclass
class Confirm:
    dir: str; h4_t: int; rej_price: float; rej_kind: str
    break_type: str; break_level: float; break_t: int
    breakout_t: int = 0; retest_t: int = 0


def _pivot(cl: List[float], i: int, n: int, hi: bool) -> bool:
    if i-n < 0 or i+n >= len(cl): return False
    v=cl[i]
    if hi: return all(v >= cl[j] for j in range(i-n,i)) and all(v > cl[j] for j in range(i+1,i+n+1))
    return all(v <= cl[j] for j in range(i-n,i)) and all(v < cl[j] for j in range(i+1,i+n+1))


def make_levels(tf: str, bars: List[Bar], n: int = 0) -> List[Level]:
    """A = bullish candle close before bearish candle; V = bearish close before bullish.
    OCL = close of first candle in a two-candle same-colour sequence.
    """
    out: List[Level] = []
    for i in range(len(bars)-1):
        a,b=bars[i],bars[i+1]
        ca=(a.c>a.o)-(a.c<a.o); cb=(b.c>b.o)-(b.c<b.o)
        if ca > 0 and cb < 0:
            out.append(Level(tf,"A",a.c,a.t,i+1,"R"))
        elif ca < 0 and cb > 0:
            out.append(Level(tf,"V",a.c,a.t,i+1,"S"))
        prev_colour = ((bars[i-1].c>bars[i-1].o)-(bars[i-1].c<bars[i-1].o)) if i else 0
        if ca != 0 and ca == cb and prev_colour != ca:
            out.append(Level(tf,"OC",a.c,a.t,i+2,"S" if ca>0 else "R"))
    # preserve distinct occurrences, even when prices coincide
    return out


def scan(tf: str, bars: List[Bar], p: Params, start: int = 0):
    """Return every rejection, every closing-price role flip, and active levels."""
    tol=p.tap_tol_pips*p.pip
    by_avail=defaultdict(list)
    for lv in make_levels(tf,bars,p.pivot_n.get(tf,0)):
        by_avail[lv.avail].append(lv)
    active=[]; rej=[]; brk=[]
    for k,b in enumerate(bars):
        active.extend(by_avail.get(k,()))
        for lv in active:
            if lv.t >= b.t: continue
            touched = b.h >= lv.price-tol if lv.role=="R" else b.l <= lv.price+tol
            rejected = b.c < lv.price if lv.role=="R" else b.c > lv.price
            if k>=start and touched and rejected:
                rej.append(Rejection(k,"bear" if lv.role=="R" else "bull",lv.price,lv.kind,lv.t,tf,lv.flips))
                lv.uses += 1
            # A closing-price break flips the level role; wick alone never flips it.
            if lv.role=="R" and b.c>lv.price:
                lv.role="S"; lv.flips+=1; brk.append((k,"bull",lv.price,lv.kind))
            elif lv.role=="S" and b.c<lv.price:
                lv.role="R"; lv.flips+=1; brk.append((k,"bear",lv.price,lv.kind))
    return rej,brk,active


def find_structure_breaks(bars: List[Bar], w: int = 2) -> List[StructureBreak]:
    """Detect CHOCH using the protected swing from the established HH/HL or LL/LH storyline.

    Swing points are confirmed only after `w` candles to their right have closed.
    In an uptrend, the protected low is the latest confirmed low formed after the
    latest higher high. A close below it is a bearish structural breakout. Mirror
    this for a downtrend. A wick through the level is not a break.
    """
    cl=[b.c for b in bars]; highs=[]; lows=[]; out=[]; used=set()
    for i in range(len(bars)):
        pi=i-w
        if pi>=w:
            if _pivot(cl,pi,w,True): highs.append((pi,cl[pi]))
            if _pivot(cl,pi,w,False): lows.append((pi,cl[pi]))
        if len(highs)>=2 and len(lows)>=2:
            h1,h2=highs[-2],highs[-1]; l1,l2=lows[-2],lows[-1]
            if h2[1]>h1[1] and l2[1]>l1[1]:
                protected=[x for x in lows if x[0]>h2[0]]
                if protected:
                    sw=protected[-1]
                    if cl[i]<sw[1] and ("bear",sw[0]) not in used:
                        out.append(StructureBreak("bear",i,sw[1],sw[0],"uptrend")); used.add(("bear",sw[0]))
            elif h2[1]<h1[1] and l2[1]<l1[1]:
                protected=[x for x in highs if x[0]>l2[0]]
                if protected:
                    sw=protected[-1]
                    if cl[i]>sw[1] and ("bull",sw[0]) not in used:
                        out.append(StructureBreak("bull",i,sw[1],sw[0],"downtrend")); used.add(("bull",sw[0]))
    return out


def _end(bars: List[Bar], k: int, tf: str) -> int:
    return bars[k+1].t if k+1<len(bars) else bars[k].t+TF_SEC[tf]


def storyline_events(htf: str, ltf: str, hb: List[Bar], lb: List[Bar], p: Params, start: int=0) -> List[Story]:
    if len(hb)<3 or len(lb)<10: return []
    rejections,_,_=scan(htf,hb,p,start)
    breaks=[(b,lb[b.i].t+TF_SEC[ltf]) for b in find_structure_breaks(lb,p.structure_w.get(ltf,2))]
    byk=defaultdict(list)
    for r in rejections: byk[r.k].append(r)
    out=[]
    for k,rs in byk.items():
        t0=hb[k].t; end_next=_end(hb,k+1,htf) if k+1<len(hb) else _end(hb,k,htf)+TF_SEC[htf]
        choices=[]
        for r in rs:
            for br,close_t in breaks:
                if br.dir==r.dir and t0<close_t<=end_next:
                    choices.append((abs(hb[k].c-r.price),r,br,close_t)); break
        if choices:
            _,r,br,ct=min(choices,key=lambda x:(0 if x[3]<=_end(hb,k,htf) else 1,x[0]))
            out.append(Story(htf,ltf,r.dir,k,t0,max(ct,_end(hb,k,htf)),r.price,r.kind,br.level,ct,ct<=_end(hb,k,htf)))
    out.sort(key=lambda x:(x.eff,x.k))
    return out


def current_story(events: List[Story], now_ts: int) -> Optional[Story]:
    valid=[e for e in events if e.eff<=now_ts]
    if not valid: return None
    # Old storyline remains active until a confirmed opposite storyline starts.
    active=valid[0]
    for event in valid[1:]:
        if event.dir != active.dir: active=event
    return active


def find_confirmations(h4: List[Bar], h1: List[Bar], p: Params, now_ts: int, window_h: int=4) -> List[Confirm]:
    """H4 rejection -> H1 CHOCH close -> retest/rejection within 4h.

    The resulting opportunity is timestamped at the retest close; callers should
    permit entry only during the following H4 candle. Levels themselves persist.
    """
    if len(h4)<6 or len(h1)<12: return []
    rej,_,_=scan("H4",h4,p,start=max(0,len(h4)-6))
    brks=find_structure_breaks(h1,p.structure_w.get("H1",2))
    out=[]; seen=set()
    for r in rej:
        h4_close=_end(h4,r.k,"H4")
        if now_ts-h4_close>window_h*3600: continue
        for br in brks:
            bt=h1[br.i].t+TF_SEC["H1"]
            if br.dir!=r.dir or bt<=h4_close: continue
            # Retest the breakout level within 4h of the H1 close, then close back
            # in the breakout direction. The touch is measured on OHLC; close confirms.
            for j in range(br.i+1,len(h1)):
                rt=h1[j].t+TF_SEC["H1"]
                if rt-bt>4*3600: break
                b=h1[j]
                touched=(b.l<=br.level+p.tap_tol_pips*p.pip) if br.dir=="bull" else (b.h>=br.level-p.tap_tol_pips*p.pip)
                held=(b.c>br.level) if br.dir=="bull" else (b.c<br.level)
                if touched and held:
                    key=(r.k,br.i,j)
                    if key not in seen and now_ts-rt<=4*3600:
                        seen.add(key)
                        out.append(Confirm("buy" if br.dir=="bull" else "sell",h4[r.k].t,r.price,r.kind,"STRUCTURE_RETEST",br.level,rt,bt,rt))
                    break
            break
    return sorted(out,key=lambda x:x.retest_t)
