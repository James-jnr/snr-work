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
KINDS = ("setup", "scenario", "storyline", "brief", "snapshot", "feed", "test", "other")

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
CREATE TABLE IF NOT EXISTS thesis (            -- the current plan for each pair
    symbol TEXT PRIMARY KEY, updated_at INTEGER NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS thesis_log (         -- one row each time the plan materially changes
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, created_at INTEGER NOT NULL,
    summary TEXT NOT NULL, changes TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_thesis_log_symbol ON thesis_log(symbol, id DESC);
CREATE TABLE IF NOT EXISTS story_status (
    symbol TEXT NOT NULL, tf TEXT NOT NULL, dir TEXT NOT NULL, eff INTEGER NOT NULL,
    status TEXT NOT NULL,                   -- verified | rejected  (no row = unverified)
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (symbol, tf, dir, eff)
);
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
            c.execute(
                "PRAGMA journal_mode=WAL"
            )  # reader and writer don't block each other
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(alerts)")}
            if "taken" not in cols:  # added in v3: "I took this trade"
                c.execute(
                    "ALTER TABLE alerts ADD COLUMN taken INTEGER NOT NULL DEFAULT 0"
                )
            c.commit()
        finally:
            c.close()
        _ready = True


def _ensure():
    if not _ready:
        init()


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
    elif m := _SETUP.search(subject):
        out.update(kind="setup", direction=m.group(1).lower(), grade=m.group(2))
    elif "story line:" in subject:
        out["kind"] = "storyline"
        out["direction"] = (
            "buy" if "BULLISH" in subject else "sell" if "BEARISH" in subject else ""
        )
    elif "price reached" in subject.lower():
        out["kind"] = "scenario"
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
def insert_alert(
    subject: str, body: str, meta: Optional[dict] = None
) -> Dict[str, Any]:
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
            (
                now,
                f["symbol"],
                f["kind"],
                f["direction"],
                f["grade"],
                subject,
                body,
                _num(meta.get("entry")),
                _num(meta.get("sl")),
                _num(meta.get("tp")),
                _num(meta.get("sl_pips")),
                _num(meta.get("tp_pips")),
                _num(meta.get("lots")),
                _num(meta.get("risk_pct")),
                json.dumps(meta, default=str),
            ),
        )
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
        c.execute(
            "UPDATE alerts SET meta=? WHERE id=?",
            (json.dumps(m, default=str), alert_id),
        )
        return True


def set_saved(alert_id: int, saved: bool) -> bool:
    _ensure()
    with _conn() as c:
        cur = c.execute(
            "UPDATE alerts SET saved=? WHERE id=?", (1 if saved else 0, alert_id)
        )
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


# ---------------------------------------------------------------- thesis memory (layer 1)
def get_thesis(symbol: str) -> Optional[Dict[str, Any]]:
    _ensure()
    with _conn() as c:
        r = c.execute(
            "SELECT data, updated_at FROM thesis WHERE symbol=?", (symbol,)
        ).fetchone()
    if not r:
        return None
    d = json.loads(r["data"])
    d["updated_at"] = r["updated_at"]
    return d


def save_thesis(symbol: str, data: Dict[str, Any], changes: List[str]) -> bool:
    """Store the current plan. A history row is added only when it changed materially (data['sig'])."""
    _ensure()
    now = int(time.time())
    with _conn() as c:
        row = c.execute("SELECT data FROM thesis WHERE symbol=?", (symbol,)).fetchone()
        old = json.loads(row["data"]) if row else None
        changed = old is None or old.get("sig") != data.get("sig")
        data = dict(
            data,
            last_changes=changes if changed else (old or {}).get("last_changes", []),
        )
        c.execute(
            "INSERT OR REPLACE INTO thesis (symbol, updated_at, data) VALUES (?,?,?)",
            (symbol, now, json.dumps(data, default=str)),
        )
        if changed:
            c.execute(
                "INSERT INTO thesis_log (symbol, created_at, summary, changes, data) VALUES (?,?,?,?,?)",
                (
                    symbol,
                    now,
                    data.get("headline", ""),
                    json.dumps(changes),
                    json.dumps(data, default=str),
                ),
            )
    return changed


def thesis_history(symbol: str, limit: int = 30) -> List[Dict[str, Any]]:
    _ensure()
    with _conn() as c:
        rows = c.execute(
            "SELECT id, created_at, summary, changes FROM thesis_log WHERE symbol=? "
            "ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    return [
        dict(
            id=r["id"],
            created_at=r["created_at"],
            summary=r["summary"],
            changes=json.loads(r["changes"]),
        )
        for r in rows
    ]


def latest_alert_id(symbol: str) -> Optional[int]:
    _ensure()
    with _conn() as c:
        r = c.execute(
            "SELECT id FROM alerts WHERE symbol=? ORDER BY id DESC LIMIT 1", (symbol,)
        ).fetchone()
    return r["id"] if r else None


def thesis_symbols() -> List[str]:
    _ensure()
    with _conn() as c:
        return [
            r["symbol"] for r in c.execute("SELECT symbol FROM thesis ORDER BY symbol")
        ]


def set_taken(alert_id: int, taken: bool) -> bool:
    _ensure()
    with _conn() as c:
        cur = c.execute(
            "UPDATE alerts SET taken=? WHERE id=?", (1 if taken else 0, alert_id)
        )
        return cur.rowcount > 0


STORY_STATUSES = ("unverified", "verified", "rejected")


def set_story_status(
    symbol: str, tf: str, direction: str, eff: int, status: str
) -> None:
    """Your decision on a story line. 'unverified' removes your mark."""
    _ensure()
    if status not in STORY_STATUSES:
        raise ValueError(f"status must be one of {STORY_STATUSES}")
    with _conn() as c:
        if status == "unverified":
            c.execute(
                "DELETE FROM story_status WHERE symbol=? AND tf=? AND dir=? AND eff=?",
                (symbol, tf, direction, int(eff)),
            )
        else:
            c.execute(
                "INSERT OR REPLACE INTO story_status (symbol, tf, dir, eff, status, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (symbol, tf, direction, int(eff), status, int(time.time())),
            )


def story_statuses(symbol: str = "") -> Dict[str, str]:
    """{'SYMBOL|tf|dir|eff': 'verified'|'rejected'}; story lines not listed are unverified."""
    _ensure()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM story_status" + (" WHERE symbol=?" if symbol else ""),
            [symbol] if symbol else [],
        ).fetchall()
    return {f"{r['symbol']}|{r['tf']}|{r['dir']}|{r['eff']}": r["status"] for r in rows}


def rejected_storylines(symbol: str):
    _ensure()
    with _conn() as c:
        return {
            (r["tf"], r["dir"], int(r["eff"]))
            for r in c.execute(
                "SELECT tf, dir, eff FROM story_status WHERE symbol=? AND status='rejected'",
                (symbol,),
            )
        }


def daily_limits(now: Optional[float] = None) -> Dict[str, Any]:
    """Mentorship rules: max trades per day, and stop after N losses in a row (trades you marked as taken)."""
    _ensure()
    import datetime as _dt

    t = now if now is not None else time.time()
    midnight = int(
        _dt.datetime.fromtimestamp(t)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    with _conn() as c:
        rows = c.execute(
            "SELECT outcome FROM alerts WHERE taken=1 AND created_at>=? ORDER BY id DESC",
            (midnight,),
        ).fetchall()
    outs = [r["outcome"] for r in rows]
    consec = 0
    for o in outs:
        if o == "lost":
            consec += 1
        elif o:  # won / breakeven / skipped ends the streak; unmarked is ignored
            break
    blocked = ""
    if consec >= config.MAX_CONSEC_LOSSES:
        blocked = f"STOP FOR TODAY: {consec} losses in a row (rule: stop after {config.MAX_CONSEC_LOSSES})"
    elif len(outs) >= config.MAX_TRADES_PER_DAY:
        blocked = f"DAILY LIMIT: {len(outs)} trades taken today (rule: max {config.MAX_TRADES_PER_DAY})"
    return {
        "trades_today": len(outs),
        "consecutive_losses": consec,
        "blocked": blocked,
        "max_trades": config.MAX_TRADES_PER_DAY,
        "max_losses": config.MAX_CONSEC_LOSSES,
    }


# ---------------------------------------------------------------- read
def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["saved"] = bool(d["saved"])
    d["taken"] = bool(d.get("taken", 0))
    try:
        d["meta"] = json.loads(d["meta"])
    except Exception:
        d["meta"] = {}
    return d


def list_alerts(
    symbol: str = "",
    kind: str = "",
    grade: str = "",
    direction: str = "",
    saved_only: bool = False,
    q: str = "",
    since: int = 0,
    until: int = 0,
    after_id: int = 0,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    _ensure()
    where, args = [], []
    if symbol:
        where.append("symbol=?")
        args.append(symbol)
    if kind:
        where.append("kind=?")
        args.append(kind)
    if grade:
        where.append("grade=?")
        args.append(grade.upper())
    if direction:
        where.append("direction=?")
        args.append(direction.lower())
    if saved_only:
        where.append("saved=1")
    if q:
        where.append("(subject LIKE ? OR body LIKE ? OR notes LIKE ?)")
        args += [f"%{q}%"] * 3
    if since:
        where.append("created_at>=?")
        args.append(int(since))
    if until:
        where.append("created_at<=?")
        args.append(int(until))
    if after_id:
        where.append("id>?")
        args.append(int(after_id))
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
        return [
            r[0]
            for r in c.execute(
                "SELECT DISTINCT symbol FROM alerts WHERE symbol<>'' ORDER BY symbol"
            )
        ]


def stats(symbol: str = "") -> Dict[str, Any]:
    """Counts by kind, and results of setups you have marked with an outcome, by grade."""
    _ensure()
    sym, args = ("WHERE symbol=?", [symbol]) if symbol else ("", [])
    with _conn() as c:
        by_kind = {
            r["kind"]: r["n"]
            for r in c.execute(
                f"SELECT kind, COUNT(*) n FROM alerts {sym} GROUP BY kind", args
            )
        }
        cond = "kind='setup'" + (" AND symbol=?" if symbol else "")
        rows = c.execute(
            f"SELECT grade, outcome, COUNT(*) n FROM alerts WHERE {cond} GROUP BY grade, outcome",
            args,
        ).fetchall()
    grades: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        g = grades.setdefault(
            r["grade"] or "?",
            {
                "total": 0,
                "won": 0,
                "lost": 0,
                "breakeven": 0,
                "skipped": 0,
                "unmarked": 0,
            },
        )
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


# Add to SCHEMA in app/db.py:
"""
CREATE TABLE IF NOT EXISTS story_override (
    symbol     TEXT NOT NULL,
    tf         TEXT NOT NULL,
    dir        TEXT NOT NULL,   -- 'bear' | 'bull' | 'none'
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (symbol, tf)
);
"""


# Add these functions to app/db.py:
def set_story_override(symbol: str, tf: str, direction: str) -> None:
    """Manually force a storyline ('bear', 'bull', 'none') or clear it ('auto')."""
    _ensure()
    d = direction.strip().lower()
    if d not in ("bear", "bull", "none", "auto"):
        raise ValueError("direction must be 'bear', 'bull', 'none', or 'auto'")
    with _conn() as c:
        if d == "auto":
            c.execute(
                "DELETE FROM story_override WHERE symbol=? AND tf=?", (symbol, tf)
            )
        else:
            c.execute(
                "INSERT OR REPLACE INTO story_override (symbol, tf, dir, updated_at) VALUES (?,?,?,?)",
                (symbol, tf, d, int(time.time())),
            )


def story_overrides(symbol: str) -> Dict[str, str]:
    """Returns {'MN1': 'bear', ...} for manually overridden storylines."""
    _ensure()
    with _conn() as c:
        rows = c.execute(
            "SELECT tf, dir FROM story_override WHERE symbol=?", (symbol,)
        ).fetchall()
    return {r["tf"]: r["dir"] for r in rows}
