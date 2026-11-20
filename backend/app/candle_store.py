import os
import sqlite3
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("CANDLE_DB_PATH", str(BASE_DIR / "data" / "market_data.db")))


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT    NOT NULL,
            tf     TEXT    NOT NULL,
            ts     INTEGER NOT NULL,
            open   REAL    NOT NULL,
            high   REAL    NOT NULL,
            low    REAL    NOT NULL,
            close  REAL    NOT NULL,
            PRIMARY KEY (symbol, tf, ts)
        )
    """)
    return con


def persist_bars(symbol: str, tfs: Dict[str, List[List[float]]]) -> None:
    """Upserts incoming MT5 candles into SQLite without duplicates."""
    rows = []
    for tf, bars in tfs.items():
        for b in bars:
            if len(b) >= 5:
                rows.append(
                    (
                        symbol,
                        tf,
                        int(b[0]),
                        float(b[1]),
                        float(b[2]),
                        float(b[3]),
                        float(b[4]),
                    )
                )

    if not rows:
        return

    with _conn() as con:
        con.executemany(
            """
            INSERT INTO candles (symbol, tf, ts, open, high, low, close)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, tf, ts) DO UPDATE SET
                open  = excluded.open,
                high  = excluded.high,
                low   = excluded.low,
                close = excluded.close
        """,
            rows,
        )


def load_candles(symbol: str, tf: str, limit: int = 5000) -> List[List[float]]:
    """Reads stored candles oldest -> newest."""
    with _conn() as con:
        cur = con.execute(
            """
            SELECT ts, open, high, low, close
            FROM candles
            WHERE symbol = ? AND tf = ?
            ORDER BY ts DESC
            LIMIT ?
        """,
            (symbol, tf, limit),
        )
        rows = cur.fetchall()
    return [list(r) for r in reversed(rows)]


def hydrate_store(store, tfs) -> int:
    """Preloads saved candles from SQLite into in-memory STORE on server startup."""
    if not DB_PATH.exists():
        return 0
    with _conn() as con:
        symbols = [
            r[0] for r in con.execute("SELECT DISTINCT symbol FROM candles").fetchall()
        ]
    loaded = 0
    for sym in symbols:
        data = {tf: load_candles(sym, tf, limit=700) for tf in tfs}
        if any(data.values()):
            st = store.get(sym)
            st.upsert({k: v for k, v in data.items() if v})
            loaded += 1
    return loaded
