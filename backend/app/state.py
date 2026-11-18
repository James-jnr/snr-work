import threading
import time
from typing import Dict, List

from .engine import Bar

TFS = ("MN1", "W1", "D1", "H4", "H1", "M15")
MAXBARS = {"MN1": 60, "W1": 200, "D1": 400, "H4": 600, "H1": 800, "M15": 800}
MIN_READY = {"W1": 30, "D1": 60, "H4": 100, "H1": 150}  # MN1 and M15 are optional
M15_MIN = 60                                             # candles needed before Setup 4 can run


class SymbolState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.meta: dict = {}
        self.bars: Dict[str, Dict[int, Bar]] = {tf: {} for tf in TFS}
        self.last_seen = 0.0
        self.notified: set = set()
        self.warmed = False
        self.offline = False
        self.last_online_msg = 0.0
        self.story_dirs: dict = {}
        self.last_signal: dict = {}
        self.sync_pending = False       # the web UI asked for a fresh push from MT5
        self.force_snapshot = False

    def set_meta(self, p):
        self.meta = {k: getattr(p, k) for k in (
            "gmt_offset", "digits", "point", "tick_size", "tick_value",
            "vol_min", "vol_step", "vol_max", "balance", "currency", "server_time")}

    def upsert(self, tfs: Dict[str, List[List[float]]]):
        for tf, rows in tfs.items():
            if tf not in self.bars:
                continue
            d = self.bars[tf]
            for r in rows:
                if len(r) < 5:
                    continue
                t = int(r[0])
                d[t] = Bar(t, float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            if len(d) > MAXBARS[tf]:
                for t in sorted(d)[: len(d) - MAXBARS[tf]]:
                    del d[t]

    def series(self, tf: str) -> List[Bar]:
        d = self.bars[tf]
        return [d[t] for t in sorted(d)]

    def last_time(self, tf: str) -> int:
        d = self.bars[tf]
        return max(d) if d else -1

    def has_m15(self) -> bool:
        return len(self.bars["M15"]) >= M15_MIN

    def ready(self) -> bool:
        return all(len(self.bars[tf]) >= n for tf, n in MIN_READY.items())


class Store:
    def __init__(self):
        self._d: Dict[str, SymbolState] = {}
        self.lock = threading.RLock()

    def get(self, symbol: str) -> SymbolState:
        if symbol not in self._d:
            self._d[symbol] = SymbolState(symbol)
        return self._d[symbol]

    def request_sync(self) -> int:
        n = 0
        for st in self._d.values():
            st.sync_pending = True
            st.force_snapshot = True
            n += 1
        return n

    def peek(self, symbol: str):
        """Existing state or None (never creates one)."""
        return self._d.get(symbol)

    def all(self):
        return list(self._d.values())


STORE = Store()
