# SNR Story Line System (v4: thesis and two-sided scenarios)

> **Read `VERSION.md` first.** It describes the current state, what changed in version 4, how the hierarchy rule works,
> what is tested, and what is still missing.


```
 MT5 (your PC)               Backend (local or Render)               You
┌───────────────┐  HTTPS   ┌─────────────────────────────┐  ┌──────────────────────────┐
│ SNR_Feeder EA │ ───────► │ levels, story lines,        │─►│ Web pages (full width)   │
│ closed candles│  /ingest │ key levels, 3 setups        │  │ Telegram + email         │
│ MN1 W1 D1 H4 H1│ ◄─────── │ charts, alert database      │  │ Phone link (QR code)     │
└───────────────┘  "sync"  └─────────────────────────────┘  └──────────────────────────┘
```
The EA is read-only (it never trades). The backend does all the analysis.

## Run it locally (start here)
```
cd backend
python -m venv .venv && .venv\Scripts\activate          (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                                    (then set API_KEY and DISPLAY_TZ=Africa/Nairobi)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --env-file .env
```
`--host 0.0.0.0` lets your phone reach it. Open **http://127.0.0.1:8000/ui**. In MT5 allow WebRequest for
`http://127.0.0.1:8000` and attach `mt5/SNR_Feeder.mq5` (v1.1) to an XAUUSD / EURUSD / GBPUSD chart with
`BackendUrl=http://127.0.0.1:8000/ingest` and your `ApiKey`. No MT5 yet? `python scripts/simulate.py http://127.0.0.1:8000 <API_KEY>`.
Telegram and email are optional: with neither set, everything still lands on the web pages.

## What changed in v4
- **Hierarchy rule**: every trade is labelled *with the trend*, *retracement*, etc. Retracement targets stop at a **ceiling** (the parent story line's entry zone).
- **Two scenarios** at the continuation point and the reversal point, each with a checklist of what to look for, a cancel level and targets.
- **Thesis**: the plan for each pair is stored in the database and shown on `/thesis`, with how it changed over time.
- **Scenario alerts** (`📍 price reached the ... zone`) fire once when price enters a zone.
- Grade A/B/C now follows the hierarchy (A = with every higher story line, C = a retracement against its parent).

## What changed in v3
**Pages**
- **Alerts** (`/ui`): full-width list. Click a row to open that alert on **its own page** (`/alert/<id>`).
- **Alert page:** report split into cards (story lines, where we are now, key levels, liquidity) and a **Setups** section
  below a heavy divider; a **Verify** checkbox for every story line; your log (took it / outcome / notes); charts in a grid.
  **Click any chart to enlarge it** (arrow keys switch charts, Esc closes).
- **Heart** (top bar): glows red while MT5 is sending data, with the **next 4-hour candle close** and a countdown ("check again at ...").
- **Sync now**: asks MT5 to push everything again (the EA checks every 15 s), then refreshes the pages and creates a fresh snapshot.
- **Phone link**: a popup with the link and a QR code (same Wi-Fi). Windows may ask to allow port 8000 through the firewall.
- **Charts** (`/charts`): Zone map, Monthly, Weekly, Daily, 4-hour, 1-hour, each timeframe alone.

**Logic (mentorship wording)**
- **Story lines on four timeframes**: Monthly, Weekly, Daily and **4-hour** (a 4-hour level is rejected, then a 1-hour breakout).
  A new story line ends the previous one on that timeframe; same direction = renewed (quiet). Reports say why it started:
  *"Weekly rejected the Weekly resistance (OCL) at 1.33945 with a wick and closed below it, and Daily gave a bearish breakout (QM) at 1.33040 in the following candle."*
  and where it stands: *"Weekly is still bearish. It hit a Weekly support (Classic A/V) at ... (a Monthly story-line obstacle) and closed above it, but there is no Daily breakout, so it remains bearish."*
- **Level names**: `Daily support (QM)`, `Monthly resistance (OCL)`, `Weekly support (Classic A/V)`. Each has a **strength score /100**
  (timeframe weight, touches, role changes RBS/SBR, how recent).
- **Key levels vs obstacles**: levels that support the story line are KEY LEVELS, the opposing ones are OBSTACLES.
  Weekly story uses Daily levels, Daily story uses 4-hour and 1-hour levels, Monthly story uses Weekly levels, 4-hour story uses 1-hour levels.
  Only the most relevant, most recent, strongest are kept.
- **Strong zones**: overlap of Monthly+Weekly, Weekly+Daily, Daily+4-hour, Daily+1-hour. The newest 4-hour level near a Daily zone ranks highest.
- **Three setups** in every report: (1) Daily story line, 4-hour entry refined to 1-hour; (2) 4-hour story line, 1-hour entry;
  (3) confirmation (4-hour rejection, then 1-hour break). Each shows alignment with Weekly/Daily/Monthly, entry zone, invalidation,
  **enter by**, **how long it stays valid** (depends on alignment) and **targets** (4-hour/1-hour roadblocks, Daily key levels,
  previous day/week and Asian high/low expected to be swept). Setup 3 upgrades to Setup 2 when the 4-hour story line starts from the same rejection.
- **Asia = the first two 4-hour candles** of the broker's day (counted as candles, not hours): no entries, and its high/low are liquidity targets.
- **Verify / Reject**: untick = *unverified* (still alerts, tagged). **Reject** ends that story line in the engine.
- **Daily limits** (mentorship): max 3 trades per day and stop after 3 losses in a row. Tick *I took this trade* and mark outcomes; a red banner appears when a limit is hit.
- **No stop-loss or lot size** anywhere: you set those. A reference "invalid if" level is shown (`SHOW_INVALIDATION=false` hides it).

## Tuning (all optional, in .env)
`PIVOT_N_<TF>`, `QM_W_<TF>` (how big a swing counts), `ZONE_OVERLAP_PIPS` (0 = auto: gold 25, JPY 15, forex 12),
`MIN_TARGET_PIPS` (ignore targets closer than this), `MIN_GAP_PIPS`, `MIN_GRADE` (A/B/C for confirmation alerts),
`SIGNAL_COOLDOWN_H`, `APPROACH_PIPS`, `MAX_TRADES_PER_DAY`, `MAX_CONSEC_LOSSES`, `SUPPRESS_WHEN_LIMIT`, `CHARTS_IN_ALERTS`,
`CHART_THEME`, `CHART_STYLE`, `BASE_URL`, `PIP_OVERRIDES`.

## Tests
```
cd backend
python scripts/check_logic.py                           (hierarchy rules, instant)
API_KEY=test python scripts/check_v3.py                 (end to end on simulated data, ~3 min)
REPLAY_HOURS=200 API_KEY=test python scripts/check_storage.py
```

## 1. Telegram
1. Message **@BotFather** → `/newbot` → copy the token.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.

## 2. Email (Gmail)
Turn on 2-step verification, create an **App Password**, use it as `SMTP_PASS` (`SMTP_USER`/`EMAIL_TO` = your address).

## 3. Deploy on Render
1. Push the `backend/` folder to a GitHub repo.
2. Render → **New → Blueprint** → select the repo (uses `render.yaml`).
3. Fill the secret env vars (`TELEGRAM_*`, `SMTP_*`, `EMAIL_TO`). `API_KEY` is generated; copy it.
4. The **free plan sleeps** after 15 min idle and cold starts take ~1 min; the EA retries automatically, but use **Starter** for reliable alerts.
5. Test: `curl -X POST https://<service>.onrender.com/test-alert -H "X-API-Key: <API_KEY>"`.

## 4. MT5 side
1. Copy `SNR_Feeder.mq5` to `MQL5/Experts/`, compile in MetaEditor (F7).
2. **Tools → Options → Expert Advisors → Allow WebRequest for listed URL** → add `https://<service>.onrender.com`.
3. Tools → Options → Charts → **Max bars in chart: Unlimited** (or ≥ 2000).
4. Open an **XAUUSD** chart (any timeframe), drag the EA on, set `BackendUrl` (`.../ingest`) and `ApiKey`, enable Algo Trading.
5. One chart per symbol you want analysed. Check the Experts tab for errors.
6. MT5 and the PC must stay on. For 24/5 coverage run MT5 on a VPS.

## Pip size
Gold uses **1 pip = $0.10** (course convention). Override with `PIP_OVERRIDES={"XAUUSD":0.1}`.

## What is automated vs still manual
**Automated:** closing-price levels, QM/OCL/Classic A/V naming, strength, rejections, story lines on four timeframes, status text,
key levels vs obstacles, strong zones, three setups with validity and targets, Asia window, daily limits, charts, alerts.
**Manual (you decide):** trend-line confluence, high-impact news, stop-loss and position size, final judgement on each story line (Verify box).

## Design choices that are mine, not the course's
- Strength score formula and the "recent window" per timeframe; "strong zone" overlap tolerance.
- OCL = close of the first of two same-colour candles at the start of a run; QM measured on closing prices.
- Validity and target ordering rules per setup; grade A/B/C is only an alignment label (A = Weekly+Daily agree, B = Daily agrees, C = against Daily).
- Alert cooldown: one setup alert per direction per `SIGNAL_COOLDOWN_H` hours.
This is analysis tooling, not financial advice; the course's win-rate claims are unverified.
