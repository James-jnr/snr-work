import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import charts, config, db, service
from app.candle_store import load_candles
from app.state import TFS, SymbolState

TF_SECONDS = {
    "H1": 3600,
    "H4": 4 * 3600,
    "D1": 86400,
    "W1": 7 * 86400,
    "MN1": 30 * 86400,
}


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def evaluate_pullback_trade(
    future_bars: list,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    spread: float = 0.35,
    max_wait_bars: int = 8,
) -> str:
    if entry is None or sl is None or tp is None:
        return "NO_SL_TP"

    filled_idx = None
    for idx, b in enumerate(future_bars[:max_wait_bars]):
        _, _, hi, lo, _ = b
        if direction == "buy":
            if direction == "buy":
                if hi >= tp or lo <= sl:
                    return "MISSED"
                if lo <= entry:
                    filled_idx = idx
                    break

        else:
            if lo <= tp or (hi + spread) >= sl:
                return "MISSED"
            if (hi + spread) >= entry:
                filled_idx = idx
                break

    if filled_idx is None:
        return "MISSED"

    for b in future_bars[filled_idx:]:
        _, _, hi, lo, _ = b
        if direction == "buy":
            if lo <= sl:
                return "LOSS"
            if hi >= tp:
                return "WIN"
        else:
            if (hi + spread) >= sl:
                return "LOSS"
            if (lo + spread) <= tp:
                return "WIN"
    return "OPEN"


def run_backtest(symbol: str = "XAUUSD", warmup_bars: int = 300, spread: float = 0.35):
    config.CHARTS_IN_ALERTS = False
    config.MIN_GRADE = "C"

    history = {tf: load_candles(symbol, tf, limit=100_000) for tf in TFS}
    h1 = history.get("H1", [])

    if len(h1) <= warmup_bars:
        print(
            f"Not enough H1 candles for {symbol} ({len(h1)} stored; need > {warmup_bars})."
        )
        return

    st = SymbolState(symbol=symbol)
    st.meta = {"symbol": symbol, "digits": 2, "point": 0.01, "gmt_offset": 0}
    trades = []

    with patch.object(db, "save_thesis", lambda *a, **kw: None):
        for i in range(warmup_bars, len(h1)):
            cur_bar = h1[i]
            cur_close_ts = cur_bar[0] + 3600

            slice_tfs = {}
            for tf, bars in history.items():
                dur = TF_SECONDS.get(tf, 3600)
                closed = [b for b in bars if (b[0] + dur) <= cur_close_ts]
                if closed:
                    slice_tfs[tf] = closed[-700:]

            prev = {tf: st.last_time(tf) for tf in TFS}
            st.upsert(slice_tfs)
            new_tfs = [
                tf for tf in slice_tfs if tf in TFS and st.last_time(tf) > prev[tf]
            ]

            if not st.ready():
                continue
            if not st.warmed:
                service.warmup(st)
                st.warmed = True
                continue
            if not new_tfs:
                continue

            for m in service.on_new_bars(st, new_tfs):
                meta = m[2] if len(m) > 2 and isinstance(m[2], dict) else {}
                if meta.get("kind") != "setup":
                    continue

                su = next(
                    (
                        x
                        for x in meta.get("setups", [])
                        if x.get("key") == meta.get("key")
                    ),
                    {},
                )
                plan = charts.plan_from_setup(su) or {}
                entry = plan.get("entry") or meta.get("entry")
                sl = plan.get("invalid")
                targets = plan.get("targets") or []
                tp1 = targets[0][1] if targets else None
                direction = meta.get("direction")
                kz = su.get("in_killzone", False)

                outcome = evaluate_pullback_trade(
                    h1[i + 1 :], direction, entry, sl, tp1, spread=spread
                )
                rr = (
                    abs(tp1 - entry) / abs(entry - sl)
                    if (entry and sl and tp1 and entry != sl)
                    else 0.0
                )

                trades.append(
                    {
                        "time": fmt_ts(cur_bar[0]),
                        "symbol": symbol,
                        "dir": direction.upper(),
                        "grade": meta.get("grade"),
                        "kz": "KZ" if kz else "--",
                        "entry": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "rr": round(rr, 2),
                        "outcome": outcome,
                    }
                )

    wins = sum(1 for t in trades if t["outcome"] == "WIN")
    losses = sum(1 for t in trades if t["outcome"] == "LOSS")
    missed = sum(1 for t in trades if t["outcome"] == "MISSED")
    filled = wins + losses
    win_rate = (wins / filled * 100) if filled else 0.0
    net_r = sum(t["rr"] for t in trades if t["outcome"] == "WIN") - losses

    print(f"\n=== V4 MENTOR-ALIGNED PULLBACK BACKTEST: {symbol} (Spread: {spread}) ===")
    print(
        f"Period         : {fmt_ts(h1[warmup_bars][0])} -> {fmt_ts(h1[-1][0])} ({len(h1) - warmup_bars} H1 bars)"
    )
    print(f"Total Setups   : {len(trades)}  (Filled: {filled} | Missed: {missed})")
    print(
        f"Filled Results : {wins} Wins | {losses} Losses | Win Rate: {win_rate:.1f}% | Net R: {net_r:+.2f}R"
    )

    for g in ("A", "B", "C"):
        gt = [t for t in trades if t["grade"] == g and t["outcome"] in ("WIN", "LOSS")]
        if gt:
            gw = sum(1 for t in gt if t["outcome"] == "WIN")
            gl = sum(1 for t in gt if t["outcome"] == "LOSS")
            gr = sum(t["rr"] for t in gt if t["outcome"] == "WIN") - gl
            print(
                f"  Grade {g}      : {gw}W / {gl}L ({gw / (gw + gl) * 100:.1f}% WR, {gr:+.2f}R)"
            )

    print()
    for t in trades:
        print(
            f"  {t['time']} | {t['dir']:4} (Gr {t['grade']} | {t['kz']}) | Entry: {t['entry']} | SL: {t['sl']} | TP1: {t['tp1']} | {t['rr']}R -> {t['outcome']}"
        )

    out_csv = (
        Path(__file__).resolve().parent.parent / "data" / f"backtest_v4_{symbol}.csv"
    )
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "time",
                "symbol",
                "dir",
                "grade",
                "kz",
                "entry",
                "sl",
                "tp1",
                "rr",
                "outcome",
            ],
        )
        writer.writeheader()
        writer.writerows(trades)
    print(f"\nSaved CSV report -> {out_csv}")


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"
    spr = (
        float(sys.argv[2])
        if len(sys.argv) > 2
        else (0.35 if "XAU" in sym.upper() else 0.00015)
    )
    run_backtest(sym, spread=spr)
