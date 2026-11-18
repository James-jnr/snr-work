"""All tunables come from environment variables (see .env.example)."""
import json
import os
from pathlib import Path


def _f(name, default):
    return float(os.getenv(name, default))


def _b(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


API_KEY = os.getenv("API_KEY", "")

# --- storage / web UI ---
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "snr.db")))
SETUPS_DIR = Path(os.getenv("SETUPS_DIR", str(BASE_DIR / "setups")))   # text copies go in SETUPS_DIR/all
UI_PUBLIC = _b("UI_PUBLIC", False)   # True = web UI needs no key (local use only!)

# --- trade plan (course defaults) ---
SL_PIPS = _f("SL_PIPS", 20)            # course: 20 pips
SL_MAX_PIPS = _f("SL_MAX_PIPS", 25)    # course: 25 max
TP_PIPS = _f("TP_PIPS", 100)           # course: 100 pip intraday target
RISK_PCT = _f("RISK_PCT", 1.0)         # course: 1% on confirmation entries
RISK_PCT_REDUCED = _f("RISK_PCT_REDUCED", 0.5)  # my addition for weaker alignment
MIN_GRADE = os.getenv("MIN_GRADE", "B").upper()  # A best, B, C weakest

# --- filters ---
ASIA_HOURS = _f("ASIA_HOURS", 8)       # first two H4 candles of the server day
ENTRY_MAX_DIST_PIPS = _f("ENTRY_MAX_DIST_PIPS", 60)
CONFIRM_WINDOW_H = int(_f("CONFIRM_WINDOW_H", 4))  # confirmation must happen inside the next 4-hour candle
TAP_TOL_PIPS = _f("TAP_TOL_PIPS", 1.0)
ZONE_PIPS = _f("ZONE_PIPS", 20)                  # H4 rejection must sit within this of a W1/D1 level
HTF_LEVEL_AGE_D = int(_f("HTF_LEVEL_AGE_D", 120))  # ignore D1 levels older than this
SIGNAL_COOLDOWN_H = _f("SIGNAL_COOLDOWN_H", 8)     # min hours between alerts in the same direction
ENABLE_MN1 = _b("ENABLE_MN1", True)
NO_TRADE_DATES = {d.strip() for d in os.getenv("NO_TRADE_DATES", "").split(",") if d.strip()}

# --- level / structure detection strength (tunable) ---
PIVOT_N = {tf: int(_f(f"PIVOT_N_{tf}", d)) for tf, d in
           {"MN1": 1, "W1": 2, "D1": 2, "H4": 2, "H1": 1, "M15": 1}.items()}
QM_W = {tf: int(_f(f"QM_W_{tf}", d)) for tf, d in
        {"W1": 2, "D1": 2, "H4": 2, "H1": 2, "M15": 2}.items()}

PIP_OVERRIDES = json.loads(os.getenv("PIP_OVERRIDES", "{}"))  # {"XAUUSD": 0.1}

# --- v4: 15-minute setup (Setup 4) ---
ENABLE_M15 = _b("ENABLE_M15", True)
CONFIRM_WINDOW_M15_H = _f("CONFIRM_WINDOW_M15_H", 2)   # the 15-min break must come within this many hours of the 1-hour rejection
M15_ENTRY_MIN = int(_f("M15_ENTRY_MIN", 45))           # how long after the 15-min break the entry stays valid (minutes)
M15_COOLDOWN_MIN = int(_f("M15_COOLDOWN_MIN", 45))     # min minutes between 15-min alerts in the same direction
M15_ZONE_COOLDOWN_H = _f("M15_ZONE_COOLDOWN_H", 3)        # no second 15-min alert at the same entry level for this many hours
M15_MIN_CLASS = os.getenv("M15_MIN_CLASS", "B").upper()  # alert only when the 15-min class is this or better (A best); C = alert all
M15_ZONE_PIPS = _f("M15_ZONE_PIPS", 0)                 # the 1-hour rejection must sit this close to an H1/H4 key level (0 = 1.5 x zone overlap)

# --- delivery ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(_f("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.getenv("EMAIL_TO", "")
DISPLAY_TZ = os.getenv("DISPLAY_TZ", "UTC")

OFFLINE_AFTER_SEC = int(_f("OFFLINE_AFTER_SEC", 600))

# --- charts ---
CHARTS_IN_ALERTS = _b("CHARTS_IN_ALERTS", True)      # attach per-timeframe level charts to alerts
CHART_THEME = os.getenv("CHART_THEME", "light")      # light | dark
CHART_STYLE = os.getenv("CHART_STYLE", "line")       # line (closes, as in the course) | candles

# --- v3: levels, zones, limits, interface ---
ZONE_OVERLAP_PIPS = _f("ZONE_OVERLAP_PIPS", 0)      # 0 = automatic (gold 25, JPY 15, other forex 12)
MIN_GAP_PIPS = _f("MIN_GAP_PIPS", 3)                # key level may sit this far on the wrong side of price
MIN_TARGET_PIPS = _f("MIN_TARGET_PIPS", 8)          # ignore targets/obstacles closer than this
APPROACH_PIPS = _f("APPROACH_PIPS", 0)              # alert when price is this close to a Daily-setup zone (0 = auto)
MAX_TRADES_PER_DAY = int(_f("MAX_TRADES_PER_DAY", 3))      # mentorship rule
MAX_CONSEC_LOSSES = int(_f("MAX_CONSEC_LOSSES", 3))        # mentorship rule
SUPPRESS_WHEN_LIMIT = _b("SUPPRESS_WHEN_LIMIT", False)     # True = no setup alerts once a limit is hit
HEARTBEAT_SEC = int(_f("HEARTBEAT_SEC", 15))
LIVE_WINDOW_SEC = int(_f("LIVE_WINDOW_SEC", 45))
SHOW_INVALIDATION = _b("SHOW_INVALIDATION", True)
PORT = int(_f("PORT", 8000))
BASE_URL = os.getenv("BASE_URL", "")          # e.g. http://192.168.1.20:8000 ; empty = detect the local network address
