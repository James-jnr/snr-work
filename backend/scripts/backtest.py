import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import charts, config, db, service
from app.candle_store import load_candles
from app.state import SymbolState, TFS

TF_SECONDS = {
    "H1": 3600,
    "H4": 4 * 3600,
    "D1": 86400,
    "W1": 7 * 86400,
    "MN1": 30 * 86400,
}


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def evaluate_be_trade(
    future_bars: list,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    spread: float,
    max_wait_bars: int = 8,
):
    if entry is None or sl is None or tp is None:
        return "NO_SL_TP", 0.0

    filled_idx = None
    for idx, b in enumerate(future_bars[:max_wait_bars]):
        _, _, hi, lo, _ = b
        if direction == "buy":
            if lo <= entry:
                filled_idx = idx
                break
            if hi >= tp:
                return "MISSED", 0.0
        else:
            if (hi + spread) >= entry:
                filled_idx = idx
                break
            if (lo + spread) <= tp:
                return "MISSED", 0.0

    if filled_idx is None:
        return "MISSED", 0.0

    risk = abs(entry - sl)
    if risk <= 0:
        return "NO_SL_TP", 0.0

    rr = round(abs(tp - entry) / risk, 2)
    be_trigger = entry + risk if direction == "buy" else entry - risk
    active_sl = sl
    moved_be = False

    for b in future_bars[filled_idx:]:
        _, _, hi, lo, _ = b
        if direction == "buy":
            if lo <= active_sl:
                return ("BE", 0.0) if moved_be else ("LOSS", -1.0)
            if hi >= tp:
                return "WIN", rr
            if not moved_be and hi >= be_trigger:
                active_sl = entry
                moved_be = True
        else:
            if (hi + spread) >= active_sl:
                return ("BE", 0.0) if moved_be else ("LOSS", -1.0)
            if (lo + spread) <= tp:
                return "WIN", rr
            if not moved_be and (lo + spread) <= be_trigger:
                active_sl = entry
                moved_be = True

    return "OPEN", 0.0


def run_backtest(symbol: str = "XAUUSD", warmup_bars: int = 300, kz_only: bool = False):
    config.CHARTS_IN_ALERTS = False
    config.MIN_GRADE = "B"

    is_xau = symbol.upper().startswith(("XAU", "GOLD"))
    spread = 0.35 if is_xau else 0.00015

    history = {tf: load_candles(symbol, tf, limit=100_000) for tf in TFS}
    h1 = history.get("H1", [])

    if len(h1) <= warmup_bars:
        print(f"Not enough H1 candles for {symbol} ({len(h1)} stored; need > {warmup_bars}).")
        return

    st = SymbolState(symbol=symbol)
    st.meta = {
        "symbol": symbol,
        "digits": 2 if is_xau else 5,
        "point": 0.01 if is_xau else 0.00001,
        "gmt_offset": 0,
    }
    trades = []

    with patch.object(db, "save_thesis", lambda *a, **kw: None):
        for i in range(warmup_bars, len(h1)):
            cur_ts, _, _, _, _ = h1[i]
            cur_close_ts = cur_ts + 3600

            slice_tfs = {}
            for tf, bars in history.items():
                dur = TF_SECONDS.get(tf, 3600)
                closed = [b for b in bars if (b[0] + dur) <= cur_close_ts]
                if closed:
                    slice_tfs[tf] = closed[-700:]

            prev = {tf: st.last_time(tf) for tf in TFS}
            st.upsert(slice_tfs)
            new_tfs = [tf for tf in slice_tfs if tf in TFS and st.last_time(tf) > prev[tf]]

            if not st.ready():
                continue
            if not st.warmed:
                service.warmup(st)
                st.warmed = True
                continue
            if not new_tfs:
                continue

            for m in service.on_new_bars(st, new_tfs):
                meta = next((x for x in m if isinstance(x, dict)), {})
                if meta.get("kind") != "setup":
                    continue

                su = next((x for x in meta.get("setups", []) if x.get("key") == meta.get("key")), {})
                kz = su.get("in_killzone", False)
                if kz_only and not kz:
                    continue

                plan = charts.plan_from_setup(su) or {}
                entry = plan.get("entry") or meta.get("entry")
                sl = su.get("sl") or plan.get("invalid")
                tp = su.get("fixed_tp")
                if tp is None:
                    targets = plan.get("targets") or []
                    tp = targets[0][1] if targets else None

                direction = meta.get("direction")
                outcome, r_res = evaluate_be_trade(h1[i + 1:], direction, entry, sl, tp, spread=spread)

                trades.append({
                    "time": fmt_ts(cur_ts),
                    "symbol": symbol,
                    "dir": direction.upper(),
                    "grade": meta.get("grade"),
                    "kz": "KZ" if kz else "--",
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "r": r_res,
                    "outcome": outcome,
                })

    wins = sum(1 for t in trades if t["outcome"] == "WIN")
    losses = sum(1 for t in trades if t["outcome"] == "LOSS")
    bes = sum(1 for t in trades if t["outcome"] == "BE")
    missed = sum(1 for t in trades if t["outcome"] == "MISSED")
    filled = wins + losses + bes
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) else 0.0
    net_r = sum(t["r"] for t in trades)

    mode = "KILLZONE-ONLY" if kz_only else "ALL SESSIONS (MIN_GRADE=B)"
    print(f"\n=== BE + FIXED R:R BACKTEST: {symbol} [{mode}] ===")
    print(f"Period         : {fmt_ts(h1[warmup_bars][0])} -> {fmt_ts(h1[-1][0])} ({len(h1) - warmup_bars} H1 bars)")
    print(f"Total Setups   : {len(trades)}  (Filled: {filled} | Missed: {missed})")
    print(f"Filled Results : {wins}W | {bes} BE | {losses}L | Win Rate (excl BE): {win_rate:.1f}% | Net R: {net_r:+.2f}R\n")

    for t in trades:
        print(f"  {t['time']} | {t['dir']:4} (Gr {t['grade']} | {t['kz']}) | Entry: {t['entry']} | SL: {t['sl']} | TP: {t['tp']} | {t['r']:+.2f}R -> {t['outcome']}")

    out_csv = Path(__file__).resolve().parent.parent / "data" / f"backtest_be_{symbol}.csv"
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "symbol", "dir", "grade", "kz", "entry", "sl", "tp", "r", "outcome"])
        writer.writeheader()
        writer.writerows(trades)
    print(f"\nSaved CSV report -> {out_csv}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]; sym = args.pop(0) if args else "XAUUSD"
    kz_flag = "--kz" in sys.argv
    run_backtest(sym, kz_only=kz_flag)
