import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query

from . import config, db, service
from .analysis import analyze, pip_for
from .engine import LTF_OF, TF_SEC, Params, storyline_events
from .messages import fmt_brief, fmt_t
from .models import IngestPayload
from .notify import notify
from .state import STORE, TFS
from .ui import router as ui_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


def market_open(now: float) -> bool:
    d = datetime.fromtimestamp(now, timezone.utc)
    wd, h = d.weekday(), d.hour
    if wd == 5 or (wd == 4 and h >= 21) or (wd == 6 and h < 21):
        return False
    return True


async def watchdog():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        if not market_open(now):
            continue
        alerts = []
        with STORE.lock:
            for st in STORE.all():
                if st.last_seen and not st.offline and now - st.last_seen > config.OFFLINE_AFTER_SEC:
                    st.offline = True
                    alerts.append((f"⚠️ {st.symbol}: MT5 feed offline",
                                   f"No data from the MT5 bot for {int((now - st.last_seen) / 60)} min. "
                                   f"Check that MT5 is open, connected, and the EA is running."))
        for subj, body in alerts:
            await asyncio.to_thread(notify, subj, body)


@asynccontextmanager
async def lifespan(_):
    db.init()
    task = asyncio.create_task(watchdog())
    yield
    task.cancel()


app = FastAPI(title="SNR Story Line Backend", lifespan=lifespan)
app.include_router(ui_router)


def auth(key: Optional[str]):
    if not config.API_KEY:
        raise HTTPException(503, "API_KEY is not configured on the server")
    if not hmac.compare_digest(key or "", config.API_KEY):
        raise HTTPException(401, "bad api key")


@app.get("/")
def root():
    return {"service": "snr-storyline", "status": "ok"}


@app.get("/health")
def health():
    return {"ok": True, "symbols": {s.symbol: {"last_seen_s_ago": int(time.time() - s.last_seen) if s.last_seen else None,
                                              "ready": s.ready()} for s in STORE.all()}}


@app.post("/ingest")
def ingest(p: IngestPayload, bg: BackgroundTasks, x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    with STORE.lock:
        st = STORE.get(p.symbol)
        was_offline = st.offline
        prev = {tf: st.last_time(tf) for tf in TFS}
        st.set_meta(p)
        st.upsert(p.tfs)
        sync_result = db.sync_mt5(p.symbol, p.positions, p.deals, p.sync_time)
        st.last_seen, st.offline = time.time(), False
        new_tfs = [tf for tf in p.tfs if st.last_time(tf) > prev[tf]]
        ready = st.ready()
        msgs = []
        try:
            if ready and (p.full or not st.warmed):
                msgs = service.warmup(st)
                st.warmed = True
            elif ready and new_tfs:
                msgs = service.on_new_bars(st, new_tfs)
        except Exception:
            log.exception("analysis failed")
        if was_offline:
            msgs.insert(0, (f"✅ {p.symbol}: MT5 feed restored", "Data is flowing again."))
    for m in msgs:                      # m = (subject, body) or (subject, body, meta)
        bg.add_task(notify, *m)
    return {"ok": True, "need_full": not ready, "alerts": len(msgs), "mt5_sync": sync_result}


@app.get("/state")
def state(symbol: str, x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    auth(x_api_key or key)
    with STORE.lock:
        a = analyze(STORE.get(symbol))
    if a is None:
        raise HTTPException(409, "not enough data yet")
    return {"price": a.price, "pip": a.pip,
            "stories": {tf: (s.__dict__ if s else None) for tf, s in a.stories.items()},
            "signals": [{**g.__dict__, "confirm": g.confirm.__dict__} for g in a.signals],
            "skipped": a.skipped}


@app.get("/trades/mt5")
def mt5_trades(symbol: str = "", x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    auth(x_api_key or key)
    return db.mt5_snapshot(symbol)


@app.get("/trades/summary")
def trade_summary(symbol: str = "", x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    auth(x_api_key or key)
    return db.mt5_trade_summary(symbol)


@app.get("/brief")
def brief(symbol: str, send: bool = False, x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    auth(x_api_key or key)
    with STORE.lock:
        a = analyze(STORE.get(symbol))
    if a is None:
        raise HTTPException(409, "not enough data yet")
    text = fmt_brief(a, "On-demand brief")
    if send:
        notify(f"📋 {symbol} brief", text)
    return {"text": text}


@app.get("/storylines")
def storylines(symbol: str, tf: str = "D1", last: int = 15,
               x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    """History of detected story lines: compare with your own chart to validate/tune the rules."""
    auth(x_api_key or key)
    if tf not in LTF_OF:
        raise HTTPException(400, "tf must be MN1, W1 or D1")
    with STORE.lock:
        st = STORE.get(symbol)
        if not st.ready():
            raise HTTPException(409, "not enough data yet")
        p = Params(pip_for(symbol, st.meta), config.PIVOT_N, config.STRUCTURE_W, config.TAP_TOL_PIPS)
        evs = storyline_events(tf, LTF_OF[tf], st.series(tf), st.series(LTF_OF[tf]), p)
        meta = st.meta
    return [{"started": fmt_t(e.eff, meta), "dir": e.dir, "rejected_level": e.level,
             "kind": e.level_kind, "breakout_level": e.breakout_level, "same_candle": e.same_bar}
            for e in evs[-last:]]


@app.get("/risk/size")
def risk_size(symbol: str, entry: float, sl: float, risk_pct: float = 1.0,
              x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    """Calculate volume only after the user supplies the actual SL price."""
    auth(x_api_key or key)
    if risk_pct <= 0 or risk_pct > 100 or entry <= 0 or sl <= 0 or entry == sl:
        raise HTTPException(400, "Provide valid entry, SL and risk_pct values")
    with STORE.lock:
        st = STORE.peek(symbol)
        if st is None or not st.meta:
            raise HTTPException(404, "No MT5 account/symbol metadata received yet")
        meta = dict(st.meta)
        pip = pip_for(symbol, meta)
    distance = abs(entry - sl)
    lots = __import__("app.analysis", fromlist=["lots_for"]).lots_for(meta, risk_pct, distance)
    loss_per_lot = distance / float(meta.get("tick_size", 0) or 1) * float(meta.get("tick_value", 0) or 0)
    risk_money = float(meta.get("balance", 0) or 0) * risk_pct / 100
    return {"symbol": symbol, "entry": entry, "sl": sl, "sl_distance": distance,
            "sl_pips": distance / pip, "risk_pct": risk_pct, "risk_amount": risk_money,
            "estimated_loss_per_lot": loss_per_lot, "lots": lots,
            "warning": None if lots is not None else "Minimum volume would exceed the selected risk budget or MT5 tick data is incomplete."}


@app.post("/test-alert")
def test_alert(x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    notify("✅ SNR backend test", "If you can read this, delivery works.")
    return {"sent": True}
