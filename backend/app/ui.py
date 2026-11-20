"""Web UI + JSON API for browsing, saving and annotating alerts (open /ui)."""
import hmac
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import charts, config, db, netinfo
from .state import STORE

router = APIRouter()
WEB = Path(__file__).parent / "web"


def ui_auth(header_key: Optional[str], query_key: Optional[str]):
    """Open when UI_PUBLIC=true (local use). Otherwise the same API_KEY as the rest of the backend."""
    if config.UI_PUBLIC:
        return
    if not config.API_KEY:
        raise HTTPException(503, "API_KEY is not configured on the server")
    if not hmac.compare_digest(header_key or query_key or "", config.API_KEY):
        raise HTTPException(401, "bad api key")


class SaveBody(BaseModel):
    saved: bool = True


class TakenBody(BaseModel):
    taken: bool = True


class StoryBody(BaseModel):
    symbol: str
    tf: str
    dir: str
    eff: int
    status: str            # verified | rejected | unverified


class NotesBody(BaseModel):
    notes: Optional[str] = None
    outcome: Optional[str] = None      # "", won, lost, breakeven, skipped


@router.get("/ui", include_in_schema=False)
def ui_page():
    return FileResponse(WEB / "index.html", media_type="text/html",
                        headers={"Cache-Control": "no-store"})


@router.get("/api/config")
def ui_config():
    return {"needs_key": not config.UI_PUBLIC}


@router.get("/api/alerts")
def alerts(symbol: str = "", kind: str = "", grade: str = "", direction: str = "",
           saved: bool = False, q: str = "", since: int = 0, until: int = 0,
           after_id: int = 0, limit: int = 100, offset: int = 0,
           x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    ui_auth(x_api_key, key)
    return {"alerts": db.list_alerts(symbol, kind, grade, direction, saved, q, since, until,
                                     after_id, limit, offset),
            "symbols": db.symbols()}


@router.post("/api/alerts/{alert_id}/save")
def save_alert(alert_id: int, body: SaveBody = SaveBody(),
               x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    ui_auth(x_api_key, key)
    if not db.set_saved(alert_id, body.saved):
        raise HTTPException(404, "no such alert")
    return db.get_alert(alert_id)


@router.post("/api/alerts/{alert_id}/notes")
def alert_notes(alert_id: int, body: NotesBody,
                x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    ui_auth(x_api_key, key)
    try:
        ok = db.set_notes(alert_id, body.notes, body.outcome)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "no such alert")
    return db.get_alert(alert_id)


@router.get("/api/stats")
def stats(symbol: str = "", x_api_key: Optional[str] = Header(default=None),
          key: Optional[str] = None):
    ui_auth(x_api_key, key)
    return db.stats(symbol)


# ---------------------------------------------------------------- thesis (the stored plan per pair)
@router.get("/thesis", include_in_schema=False)
def thesis_page():
    return FileResponse(WEB / "thesis.html", media_type="text/html", headers={"Cache-Control": "no-store"})


@router.get("/api/thesis")
def thesis_api(symbol: str = "", limit: int = 30, x_api_key: Optional[str] = Header(default=None),
               key: Optional[str] = None):
    """The current plan for each pair, and how it changed over time."""
    ui_auth(x_api_key, key)
    syms = [symbol] if symbol else db.thesis_symbols()
    return {"symbols": db.thesis_symbols(),
            "items": {s: {"current": db.get_thesis(s), "history": db.thesis_history(s, max(1, min(limit, 200))),
                          "latest_alert_id": db.latest_alert_id(s)} for s in syms}}


# ---------------------------------------------------------------- pages and static files
@router.get("/alert/{alert_id}", include_in_schema=False)
def alert_page(alert_id: int):
    return FileResponse(WEB / "alert.html", media_type="text/html", headers={"Cache-Control": "no-store"})


@router.get("/static/{name}", include_in_schema=False)
def static_file(name: str):
    if name not in ("ui.css", "ui.js"):
        raise HTTPException(404, "not found")
    return FileResponse(WEB / name, headers={"Cache-Control": "no-store"})


@router.get("/api/alerts/{alert_id}")
def alert_one(alert_id: int, x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    ui_auth(x_api_key, key)
    a = db.get_alert(alert_id)
    if not a:
        raise HTTPException(404, "no such alert")
    return a


@router.post("/api/alerts/{alert_id}/taken")
def alert_taken(alert_id: int, body: TakenBody, x_api_key: Optional[str] = Header(default=None),
                key: Optional[str] = None):
    ui_auth(x_api_key, key)
    if not db.set_taken(alert_id, body.taken):
        raise HTTPException(404, "no such alert")
    return {"alert": db.get_alert(alert_id), "limits": db.daily_limits()}


@router.get("/api/story-status")
def story_status(symbol: str = "", x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    ui_auth(x_api_key, key)
    return db.story_statuses(symbol)


@router.post("/api/story-status")
def story_status_set(body: StoryBody, x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    """Verify or reject a story line. A rejected story line is treated as ended."""
    ui_auth(x_api_key, key)
    try:
        db.set_story_status(body.symbol, body.tf.upper(), body.dir, body.eff, body.status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return db.story_statuses(body.symbol)


# ---------------------------------------------------------------- live status: heart, next 4-hour close, sync
H4S = 4 * 3600


@router.get("/api/status")
def status(x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    ui_auth(x_api_key, key)
    now = time.time()
    syms, nxt_all = [], []
    with STORE.lock:
        for st in STORE.all():
            age = (now - st.last_seen) if st.last_seen else None
            nxt = None
            h4 = st.series("H4")
            if h4:
                off = int(st.meta.get("gmt_offset", 0) or 0)
                nxt = h4[-1].t + 2 * H4S - off          # UTC epoch when the forming 4-hour candle closes
                if nxt <= now:                          # data is stale: next boundary on the same 4-hour grid
                    nxt += -(-int(now - nxt) // H4S) * H4S
                    if nxt <= now:
                        nxt += H4S
                nxt_all.append(nxt)
            syms.append(dict(symbol=st.symbol, age=age, ready=st.ready(), next_h4_close_utc=nxt,
                             sync_pending=st.sync_pending))
    live = any(s["age"] is not None and s["age"] <= config.LIVE_WINDOW_SEC for s in syms)
    return {"live": live, "symbols": syms, "next_h4_close_utc": min(nxt_all) if nxt_all else None,
            "sync_pending": any(s["sync_pending"] for s in syms), "server_time": now,
            "limits": db.daily_limits(), "heartbeat_sec": config.HEARTBEAT_SEC}


@router.post("/api/sync")
def sync_now(x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    """Ask the MT5 feeder to push fresh data on its next heartbeat (every few seconds)."""
    ui_auth(x_api_key, key)
    with STORE.lock:
        n = STORE.request_sync()
    if n == 0:
        raise HTTPException(409, "no MT5 feed has connected yet")
    return {"requested": n, "expect_within_sec": config.HEARTBEAT_SEC + 5}


@router.get("/api/phone")
def phone_link(request: Request, x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    """Link (and QR code) to open this dashboard on a phone on the same Wi-Fi."""
    ui_auth(x_api_key, key)
    url = netinfo.base_url(request.url.port) + "/ui"
    if not config.UI_PUBLIC and config.API_KEY:
        url += "?key=" + config.API_KEY            # saves the key on the phone once; same Wi-Fi only
    svg = ""
    try:
        import segno
        svg = segno.make(url, error="m").svg_inline(scale=6, border=2, dark="#111111", light="#ffffff")
    except Exception:
        pass
    host = request.headers.get("host", "")
    return {"url": url, "qr_svg": svg, "local_only": host.startswith(("127.0.0.1", "localhost")),
            "note": "Your phone must be on the same Wi-Fi as this PC. Start the server with --host 0.0.0.0 and "
                    "allow port %d through the Windows firewall if the link does not open." % config.PORT}


# ---------------------------------------------------------------- charts
@router.get("/charts", include_in_schema=False)
def charts_page():
    return FileResponse(WEB / "charts.html", media_type="text/html", headers={"Cache-Control": "no-store"})


def _tf(tf: str) -> str:
    tf = tf.upper()
    if tf not in charts.TF_NAME:
        raise HTTPException(400, "tf must be one of " + ", ".join(charts.TF_NAME))
    return tf


@router.get("/api/chart.png")
def chart_png(symbol: str, tf: str = "D1", bars: int = 0, style: str = "line", theme: str = "light",
              all_levels: bool = False,
              x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    """Live chart of ONE timeframe (or the zone map, tf=ZM) with the key levels the engine selected."""
    ui_auth(x_api_key, key)
    try:
        spec = charts.spec_for(symbol, _tf(tf), max(0, min(bars, 400)), style, theme, all_levels)
    except LookupError as e:
        raise HTTPException(409, str(e))
    return Response(charts.render(spec), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/api/levels")
def levels_table(symbol: str, tf: str = "D1", limit: int = 60,
                 x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    ui_auth(x_api_key, key)
    tf = _tf(tf)
    if tf == "ZM":
        raise HTTPException(400, "choose a timeframe")
    try:
        return charts.levels_for(symbol, tf, max(1, min(limit, 300)))
    except LookupError as e:
        raise HTTPException(409, str(e))


@router.get("/api/chart-meta")
def chart_meta(x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    ui_auth(x_api_key, key)
    return charts.meta_for()


@router.get("/api/chart-files/{name}")
def chart_file(name: str, x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    """PNG saved with an alert (the chart exactly as it looked when the alert fired)."""
    ui_auth(x_api_key, key)
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.png", name):
        raise HTTPException(400, "bad file name")
    path = config.SETUPS_DIR / "charts" / name
    if not path.is_file():
        raise HTTPException(404, "no such chart")
    return FileResponse(path, media_type="image/png")
