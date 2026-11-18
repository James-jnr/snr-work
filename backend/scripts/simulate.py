"""Send synthetic gold candles to your backend to test the pipeline without MT5.

Usage:
  python scripts/simulate.py https://YOUR-SERVICE.onrender.com YOUR_API_KEY
"""
import random
import sys

H = 3600


def make_h1(n=24 * 365 * 2, seed=7, start=1_640_995_200 - (1_640_995_200 % 604800) + 3 * 86400):
    random.seed(seed)
    price, t, out, drift = 1800.0, start, [], 0.0
    for i in range(n):
        if i % 120 == 0:
            drift = random.uniform(-0.25, 0.25)
        o = price
        c = o + drift + random.gauss(0, 2.2)
        hi = max(o, c) + abs(random.gauss(0, 0.9))
        lo = min(o, c) - abs(random.gauss(0, 0.9))
        out.append([t, o, hi, lo, c])
        price, t = c, t + H
    return out


def agg(rows, seconds, align_offset=0):
    buckets = {}
    for r in rows:
        b = (r[0] - align_offset) // seconds * seconds + align_offset
        buckets.setdefault(b, []).append(r)
    res = []
    for b in sorted(buckets):
        g = buckets[b]
        res.append([b, g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4]])
    return res[:-1]  # drop the still-forming last bucket


def make_m15(h1_rows, seed=11):
    """Split each H1 candle into four 15-min candles (same open, close, high and low as the hour)."""
    rnd = random.Random(seed)
    out = []
    for t, o, hi, lo, c in h1_rows:
        span = max(hi - lo, 1e-9)
        closes = [o + (c - o) * (k + 1) / 4 + rnd.gauss(0, span * 0.12) for k in range(3)] + [c]
        closes = [min(hi, max(lo, x)) for x in closes[:3]] + [c]
        prev = o
        for k in range(4):
            cl = closes[k]
            h = max(prev, cl) + abs(rnd.gauss(0, span * 0.05))
            l = min(prev, cl) - abs(rnd.gauss(0, span * 0.05))
            out.append([t + k * 900, prev, min(h, hi) if k != 1 else hi, max(l, lo) if k != 2 else lo, cl])
            prev = cl
        out[-4][3] = min(out[-4][3], out[-4][1], out[-4][4])
    return out


def build_payload(h1=None, full=True, symbol="XAUUSD"):
    h1 = h1 or make_h1()
    tfs = {"M15": make_m15(h1[-200:])[-600:], "H1": h1[-700:], "H4": agg(h1, 4 * H)[-500:], "D1": agg(h1, 86400)[-300:],
           "W1": agg(h1, 604800, 3 * 86400)[-120:], "MN1": agg(h1, 30 * 86400)[-30:]}
    return {"symbol": symbol, "server_time": h1[-1][0] + H, "gmt_offset": 7200, "digits": 2, "point": 0.01,
            "tick_size": 0.01, "tick_value": 1.0, "vol_min": 0.01, "vol_step": 0.01, "vol_max": 100,
            "balance": 10000, "currency": "USD", "full": full, "tfs": tfs}


if __name__ == "__main__":
    import httpx
    url, key = sys.argv[1].rstrip("/"), sys.argv[2]
    r = httpx.post(f"{url}/ingest", json=build_payload(), headers={"X-API-Key": key}, timeout=60)
    print(r.status_code, r.text)
