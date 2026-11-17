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
CONFIRM_WINDOW_H = int(_f("CONFIRM_WINDOW_H", 8))  # how long a setup stays fresh
TAP_TOL_PIPS = _f("TAP_TOL_PIPS", 1.0)
ZONE_PIPS = _f("ZONE_PIPS", 20)                  # H4 rejection must sit within this of a W1/D1 level
HTF_LEVEL_AGE_D = int(_f("HTF_LEVEL_AGE_D", 120))  # ignore D1 levels older than this
SIGNAL_COOLDOWN_H = _f("SIGNAL_COOLDOWN_H", 8)     # min hours between alerts in the same direction
ENABLE_MN1 = _b("ENABLE_MN1", True)
NO_TRADE_DATES = {d.strip() for d in os.getenv("NO_TRADE_DATES", "").split(",") if d.strip()}

# --- level / structure detection strength (tunable) ---
PIVOT_N = {tf: int(_f(f"PIVOT_N_{tf}", d)) for tf, d in
           {"MN1": 1, "W1": 2, "D1": 2, "H4": 2, "H1": 1}.items()}
QM_W = {tf: int(_f(f"QM_W_{tf}", d)) for tf, d in
        {"W1": 2, "D1": 2, "H4": 2, "H1": 2}.items()}

PIP_OVERRIDES = json.loads(os.getenv("PIP_OVERRIDES", "{}"))  # {"XAUUSD": 0.1}

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
