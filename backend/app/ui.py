"""Web UI + JSON API for browsing, saving and annotating alerts (open /ui)."""
import hmac
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import charts, config, db

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
              levels: int = 12, all_levels: bool = False,
              x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    """Live chart of ONE timeframe with the levels the engine uses on it."""
    ui_auth(x_api_key, key)
    try:
        spec = charts.spec_for(symbol, _tf(tf), max(0, min(bars, 400)), style, theme,
                               max(2, min(levels, 40)), all_levels)
    except LookupError as e:
        raise HTTPException(409, str(e))
    return Response(charts.render(spec), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/api/levels")
def levels_table(symbol: str, tf: str = "D1", limit: int = 60,
                 x_api_key: Optional[str] = Header(default=None), key: Optional[str] = None):
    ui_auth(x_api_key, key)
    try:
        return charts.levels_for(symbol, _tf(tf), max(1, min(limit, 300)))
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
