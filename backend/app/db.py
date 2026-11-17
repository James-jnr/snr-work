"""SQLite storage for every alert (one file, no server). Source of truth for the web UI."""
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from . import config

OUTCOMES = ("", "won", "lost", "breakeven", "skipped")
KINDS = ("setup", "storyline", "brief", "snapshot", "feed", "test", "other")

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  INTEGER NOT NULL,           -- unix seconds, UTC
    symbol      TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'other',
    direction   TEXT NOT NULL DEFAULT '',   -- buy | sell (story lines: bull = buy bias)
    grade       TEXT NOT NULL DEFAULT '',   -- A | B | C (setups only)
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    entry       REAL, sl REAL, tp REAL,
    sl_pips     REAL, tp_pips REAL,
    lots        REAL, risk_pct REAL,
    meta        TEXT NOT NULL DEFAULT '{}', -- full structured data as JSON
    saved       INTEGER NOT NULL DEFAULT 0,
    notes       TEXT NOT NULL DEFAULT '',
    outcome     TEXT NOT NULL DEFAULT '',
    txt_file    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_alerts_created ON alerts(created_at);
CREATE INDEX IF NOT EXISTS ix_alerts_kind    ON alerts(kind, symbol);
CREATE INDEX IF NOT EXISTS ix_alerts_saved   ON alerts(saved);
CREATE TABLE IF NOT EXISTS mt5_positions (
    ticket INTEGER PRIMARY KEY, position_id INTEGER NOT NULL DEFAULT 0,
    symbol TEXT NOT NULL, side TEXT NOT NULL DEFAULT '', volume REAL NOT NULL DEFAULT 0,
    price_open REAL, price_current REAL, sl REAL, tp REAL, profit REAL DEFAULT 0,
    swap REAL DEFAULT 0, opened_at INTEGER, magic INTEGER DEFAULT 0,
    comment TEXT DEFAULT '', updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mt5_positions_symbol ON mt5_positions(symbol);
CREATE TABLE IF NOT EXISTS mt5_deals (
    ticket INTEGER PRIMARY KEY, position_id INTEGER NOT NULL DEFAULT 0,
    symbol TEXT NOT NULL, side TEXT NOT NULL DEFAULT '', entry_type TEXT NOT NULL DEFAULT '',
    volume REAL NOT NULL DEFAULT 0, price REAL, profit REAL DEFAULT 0,
    commission REAL DEFAULT 0, swap REAL DEFAULT 0, deal_time INTEGER,
    magic INTEGER DEFAULT 0, comment TEXT DEFAULT '', updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mt5_deals_position ON mt5_deals(position_id);
CREATE INDEX IF NOT EXISTS ix_mt5_deals_time ON mt5_deals(deal_time);
"""

_init_lock = threading.Lock()
_ready = False


@contextmanager
def _conn():
    c = sqlite3.connect(str(config.DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA busy_timeout=5000")
        yield c
        c.commit()
    finally:
        c.close()


def init():
    """Create the database file and tables if missing (safe to call many times)."""
    global _ready
    with _init_lock:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(config.DB_PATH), timeout=10)
        try:
            c.execute("PRAGMA journal_mode=WAL")   # reader and writer don't block each other
            c.executescript(SCHEMA)
            c.commit()
        finally:
            c.close()
        _ready = True


def _ensure():
    if not _ready:
        init()


# ---------------------------------------------------------------- MT5 read-only synchronization
def sync_mt5(symbol: str, positions: List[dict], deals: List[dict], synced_at: int = 0) -> dict:
    """Upsert the current position snapshot and append/update immutable deal tickets."""
    _ensure()
    now = int(synced_at or time.time())
    with _conn() as c:
        for p in positions:
            ticket = int(p.get("ticket", 0) or 0)
            if not ticket: continue
            c.execute("""INSERT INTO mt5_positions
                (ticket,position_id,symbol,side,volume,price_open,price_current,sl,tp,profit,swap,opened_at,magic,comment,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ticket) DO UPDATE SET position_id=excluded.position_id,symbol=excluded.symbol,
                side=excluded.side,volume=excluded.volume,price_open=excluded.price_open,price_current=excluded.price_current,
                sl=excluded.sl,tp=excluded.tp,profit=excluded.profit,swap=excluded.swap,opened_at=excluded.opened_at,
                magic=excluded.magic,comment=excluded.comment,updated_at=excluded.updated_at""",
                (ticket,int(p.get("position_id",ticket) or ticket),str(p.get("symbol") or symbol),str(p.get("side","")),float(p.get("volume",0) or 0),
                 _num(p.get("price_open")),_num(p.get("price_current")),_num(p.get("sl")),_num(p.get("tp")),
                 _num(p.get("profit")) or 0,_num(p.get("swap")) or 0,int(p.get("opened_at",0) or 0),
                 int(p.get("magic",0) or 0),str(p.get("comment", ""))[:512],now))
        # The EA sends a complete open-position snapshot for this symbol.
        active = [int(p.get("ticket",0) or 0) for p in positions if int(p.get("ticket",0) or 0)]
        if active:
            marks=",".join("?" for _ in active)
            c.execute(f"DELETE FROM mt5_positions WHERE symbol=? AND ticket NOT IN ({marks})",[symbol,*active])
        else:
            c.execute("DELETE FROM mt5_positions WHERE symbol=?",(symbol,))
        for d in deals:
            ticket=int(d.get("ticket",0) or 0)
            if not ticket: continue
            c.execute("""INSERT INTO mt5_deals
                (ticket,position_id,symbol,side,entry_type,volume,price,profit,commission,swap,deal_time,magic,comment,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ticket) DO UPDATE SET position_id=excluded.position_id,symbol=excluded.symbol,
                side=excluded.side,entry_type=excluded.entry_type,volume=excluded.volume,price=excluded.price,
                profit=excluded.profit,commission=excluded.commission,swap=excluded.swap,deal_time=excluded.deal_time,
                magic=excluded.magic,comment=excluded.comment,updated_at=excluded.updated_at""",
                (ticket,int(d.get("position_id",0) or 0),str(d.get("symbol") or symbol),str(d.get("side","")),str(d.get("entry_type","")),
                 float(d.get("volume",0) or 0),_num(d.get("price")),_num(d.get("profit")) or 0,
                 _num(d.get("commission")) or 0,_num(d.get("swap")) or 0,int(d.get("deal_time",0) or 0),
                 int(d.get("magic",0) or 0),str(d.get("comment", ""))[:512],now))
        pos_count=c.execute("SELECT COUNT(*) FROM mt5_positions WHERE symbol=?",(symbol,)).fetchone()[0]
        deal_count=c.execute("SELECT COUNT(*) FROM mt5_deals WHERE symbol=?",(symbol,)).fetchone()[0]
    return {"positions":pos_count,"deals":deal_count,"synced_at":now}


def mt5_snapshot(symbol: str = "") -> dict:
    _ensure()
    with _conn() as c:
        if symbol:
            positions=[dict(r) for r in c.execute("SELECT * FROM mt5_positions WHERE symbol=? ORDER BY opened_at",(symbol,))]
            deals=[dict(r) for r in c.execute("SELECT * FROM mt5_deals WHERE symbol=? ORDER BY deal_time DESC LIMIT 500",(symbol,))]
        else:
            positions=[dict(r) for r in c.execute("SELECT * FROM mt5_positions ORDER BY opened_at")]
            deals=[dict(r) for r in c.execute("SELECT * FROM mt5_deals ORDER BY deal_time DESC LIMIT 2000")]
    return {"positions":positions,"deals":deals}


def mt5_trade_summary(symbol: str = "", timezone_name: str = "Africa/Nairobi") -> dict:
    """Aggregate deal tickets by MT5 position identifier; partial exits count once."""
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone
    snap=mt5_snapshot(symbol)
    open_ids={int(p.get("position_id") or p["ticket"]) for p in snap["positions"]}
    grouped={}
    for d in snap["deals"]:
        pid=int(d.get("position_id") or 0)
        if not pid: continue
        g=grouped.setdefault(pid,{"position_id":pid,"symbol":d.get("symbol",symbol),"net":0.0,"last_close":0,"first_open":0,"has_exit":False,"deals":0})
        g["net"] += float(d.get("profit") or 0)+float(d.get("commission") or 0)+float(d.get("swap") or 0)
        g["deals"] += 1
        if d.get("entry_type") == "in":
            opened=int(d.get("deal_time") or 0)
            g["first_open"] = opened if not g["first_open"] else min(g["first_open"],opened)
        if d.get("entry_type") in ("out","out_by","inout"):
            g["has_exit"]=True
            g["last_close"]=max(g["last_close"],int(d.get("deal_time") or 0))
    closed=[]
    for g in grouped.values():
        if g["has_exit"] and g["position_id"] not in open_ids:
            g["outcome"]="won" if g["net"]>0 else "lost" if g["net"]<0 else "breakeven"
            closed.append(g)
    closed.sort(key=lambda x:x["last_close"])
    today=datetime.now(ZoneInfo(timezone_name)).date()
    todays=[g for g in closed if g["last_close"] and datetime.fromtimestamp(g["last_close"],timezone.utc).astimezone(ZoneInfo(timezone_name)).date()==today]
    started_today=[g for g in grouped.values() if g["first_open"] and datetime.fromtimestamp(g["first_open"],timezone.utc).astimezone(ZoneInfo(timezone_name)).date()==today]
    streak=0
    for g in reversed(closed):
        if g["outcome"]=="lost": streak+=1
        elif g["outcome"]=="breakeven": continue
        else: break
    return {"trades_started_today":len(started_today),"completed_trades_today":len(todays),"today":today.isoformat(),
            "consecutive_losses":streak,"closed_trades":closed,
            "open_positions":snap["positions"]}


# ---------------------------------------------------------------- classify
_SETUP = re.compile(r"\b(BUY|SELL) setup \(grade ([ABC])\)")
_SYMBOL = re.compile(r"^\S+\s+([A-Za-z0-9._#\-]+)")


def classify(subject: str) -> Dict[str, str]:
    """Work out kind/symbol/direction/grade from an alert subject (used when no meta is given)."""
    out = {"kind": "other", "symbol": "", "direction": "", "grade": ""}
    m = _SYMBOL.match(subject)
    if m:
        out["symbol"] = m.group(1)
    if "backend test" in subject:
        out.update(kind="test", symbol="")
    elif (m := _SETUP.search(subject)):
        out.update(kind="setup", direction=m.group(1).lower(), grade=m.group(2))
    elif "story line:" in subject:
        out["kind"] = "storyline"
        out["direction"] = "buy" if "BULLISH" in subject else "sell" if "BEARISH" in subject else ""
    elif "feed online" in subject:
        out["kind"] = "snapshot"
    elif "MT5 feed" in subject:
        out["kind"] = "feed"
    elif "brief" in subject.lower():
        out["kind"] = "brief"
    return out


def _num(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- write
def insert_alert(subject: str, body: str, meta: Optional[dict] = None) -> Dict[str, Any]:
    """Store one alert. Returns {'id', 'created_at', 'symbol', 'kind', 'grade', 'direction'}."""
    _ensure()
    meta = dict(meta or {})
    f = classify(subject)
    for k in ("kind", "symbol", "direction", "grade"):
        if meta.get(k):
            f[k] = str(meta[k])
    now = int(time.time())
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO alerts (created_at, symbol, kind, direction, grade, subject, body,"
            " entry, sl, tp, sl_pips, tp_pips, lots, risk_pct, meta)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, f["symbol"], f["kind"], f["direction"], f["grade"], subject, body,
             _num(meta.get("entry")), _num(meta.get("sl")), _num(meta.get("tp")),
             _num(meta.get("sl_pips")), _num(meta.get("tp_pips")),
             _num(meta.get("lots")), _num(meta.get("risk_pct")),
             json.dumps(meta, default=str)))
        rid = cur.lastrowid
    return {"id": rid, "created_at": now, **f}


def set_txt_file(alert_id: int, name: str):
    _ensure()
    with _conn() as c:
        c.execute("UPDATE alerts SET txt_file=? WHERE id=?", (name, alert_id))


def add_meta(alert_id: int, extra: Dict[str, Any]) -> bool:
    """Merge extra keys into an alert's meta JSON (used to record its chart files)."""
    _ensure()
    with _conn() as c:
        r = c.execute("SELECT meta FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if not r:
            return False
        try:
            m = json.loads(r["meta"] or "{}")
        except Exception:
            m = {}
        m.update(extra)
        c.execute("UPDATE alerts SET meta=? WHERE id=?", (json.dumps(m, default=str), alert_id))
        return True


def set_saved(alert_id: int, saved: bool) -> bool:
    _ensure()
    with _conn() as c:
        cur = c.execute("UPDATE alerts SET saved=? WHERE id=?", (1 if saved else 0, alert_id))
        return cur.rowcount > 0


def set_notes(alert_id: int, notes: Optional[str], outcome: Optional[str]) -> bool:
    """Update notes and/or outcome (None = leave unchanged)."""
    _ensure()
    if outcome is not None and outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    sets, args = [], []
    if notes is not None:
        sets.append("notes=?")
        args.append(notes[:5000])
    if outcome is not None:
        sets.append("outcome=?")
        args.append(outcome)
    if not sets:
        return True
    args.append(alert_id)
    with _conn() as c:
        cur = c.execute(f"UPDATE alerts SET {', '.join(sets)} WHERE id=?", args)
        return cur.rowcount > 0


# ---------------------------------------------------------------- read
def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["saved"] = bool(d["saved"])
    try:
        d["meta"] = json.loads(d["meta"])
    except Exception:
        d["meta"] = {}
    return d


def list_alerts(symbol: str = "", kind: str = "", grade: str = "", direction: str = "",
                saved_only: bool = False, q: str = "", since: int = 0, until: int = 0,
                after_id: int = 0, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    _ensure()
    where, args = [], []
    if symbol:
        where.append("symbol=?"); args.append(symbol)
    if kind:
        where.append("kind=?"); args.append(kind)
    if grade:
        where.append("grade=?"); args.append(grade.upper())
    if direction:
        where.append("direction=?"); args.append(direction.lower())
    if saved_only:
        where.append("saved=1")
    if q:
        where.append("(subject LIKE ? OR body LIKE ? OR notes LIKE ?)")
        args += [f"%{q}%"] * 3
    if since:
        where.append("created_at>=?"); args.append(int(since))
    if until:
        where.append("created_at<=?"); args.append(int(until))
    if after_id:
        where.append("id>?"); args.append(int(after_id))
    sql = "SELECT * FROM alerts" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [max(1, min(int(limit), 500)), max(0, int(offset))]
    with _conn() as c:
        return [_row(r) for r in c.execute(sql, args).fetchall()]


def get_alert(alert_id: int) -> Optional[Dict[str, Any]]:
    _ensure()
    with _conn() as c:
        r = c.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        return _row(r) if r else None


def symbols() -> List[str]:
    _ensure()
    with _conn() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT symbol FROM alerts WHERE symbol<>'' ORDER BY symbol")]


def stats(symbol: str = "") -> Dict[str, Any]:
    """Counts by kind, and results of setups you have marked with an outcome, by grade."""
    _ensure()
    sym, args = ("WHERE symbol=?", [symbol]) if symbol else ("", [])
    with _conn() as c:
        by_kind = {r["kind"]: r["n"] for r in c.execute(
            f"SELECT kind, COUNT(*) n FROM alerts {sym} GROUP BY kind", args)}
        cond = "kind='setup'" + (" AND symbol=?" if symbol else "")
        rows = c.execute(
            f"SELECT grade, outcome, COUNT(*) n FROM alerts WHERE {cond} GROUP BY grade, outcome",
            args).fetchall()
    grades: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        g = grades.setdefault(r["grade"] or "?", {"total": 0, "won": 0, "lost": 0,
                                                  "breakeven": 0, "skipped": 0, "unmarked": 0})
        g["total"] += r["n"]
        g[r["outcome"] if r["outcome"] else "unmarked"] += r["n"]
    tot = {"total": 0, "won": 0, "lost": 0, "breakeven": 0, "skipped": 0, "unmarked": 0}
    for g in grades.values():
        decided = g["won"] + g["lost"]
        g["win_rate"] = round(100.0 * g["won"] / decided, 1) if decided else None
        for k in tot:
            tot[k] += g[k]
    decided = tot["won"] + tot["lost"]
    tot["win_rate"] = round(100.0 * tot["won"] / decided, 1) if decided else None
    return {"by_kind": by_kind, "grades": dict(sorted(grades.items())), "overall": tot}
