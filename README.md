# SNR Workstation — implementation in progress

This project retains the original local FastAPI + SQLite + MT5 Expert Advisor + HTML/JavaScript architecture. The MT5 Expert Advisor is **read-only**: it sends market/account/trade data to the backend and has no order placement, modification, or close logic.

## Run locally

```bash
cd backend
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
# Set a long random API_KEY in .env
uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file .env
```

Open `http://127.0.0.1:8000/ui` and `http://127.0.0.1:8000/charts`.

## Current revision

### SNR engine

- A level: close of a bullish candle followed by a bearish candle.
- V level: close of a bearish candle followed by a bullish candle.
- OCL: close of the first candle in a new run of two or more consecutive candles of the same colour.
- Rejection: candle range touches/crosses the level and closes back on its original side.
- RBS/SBR role change: requires a candle close beyond the level; a wick alone does not flip it.
- Storyline direction: monthly, weekly and daily only. A higher-timeframe rejection must be followed by a next-timeframe structural breakout/CHOCH.
- CHOCH is not QM. The engine now detects the close beyond the protected HL in an established HH/HL uptrend, or beyond the protected LH in an established LL/LH downtrend. QM remains a separate pattern and is not used as a synonym for structure break.
- A storyline direction remains active until a confirmed opposite storyline starts.

### Confirmation entry sequence

- H4 SNR rejection.
- H1 structural breakout/CHOCH.
- Price returns to the breakout level and rejects it within four hours.
- Entry is referenced at the breakout level and is valid in the following H4 window.
- The original SNR and breakout levels are not deleted when an entry opportunity expires.

### Session handling

- Session timezone: `Africa/Nairobi` (EAT, UTC+3).
- Asian session: midnight to approximately 9:00 AM EAT.
- The first two H4 candles, 00:00–08:00 EAT, are the restricted period for confirmation setups.
- Confirmation setups can be taken during the remaining Asian-session period.
- Broker timestamps are converted using the GMT offset supplied by MT5.

### Risk and MT5 sync

- SL is not hardcoded. The trader sets it manually in MT5.
- `/risk/size` calculates position size after the user supplies entry, SL and risk percentage. Volume is rounded down to the broker step and is not rounded up to the minimum if that would exceed the risk budget.
- The EA sends open positions, account balance/equity/margin fields and incremental deal history.
- SQLite stores MT5 position and deal tickets idempotently. Manual SL/TP edits are reflected on the next sync.
- `/trades/mt5` exposes the synchronized snapshot. `/trades/summary` aggregates deal tickets by MT5 position ID so partial closes count as one position after it is fully closed. Breakeven counts as a completed trade and is not treated as a loss.
- The EA remains read-only.

## API endpoints

- `GET /health`
- `POST /ingest` (MT5 EA; requires `X-API-Key`)
- `GET /state?symbol=XAUUSD`
- `GET /brief?symbol=XAUUSD`
- `GET /storylines?symbol=XAUUSD&tf=D1`
- `GET /trades/mt5` (optional `symbol` filter)
- `GET /trades/summary` (optional `symbol` filter; account-wide if omitted)
- `GET /risk/size?symbol=XAUUSD&entry=...&sl=...&risk_pct=1`
- `POST /test-alert`

Authenticated endpoints require `X-API-Key` (or `key` query parameter for browser convenience when enabled by the existing UI configuration).

## MT5 setup

1. Compile `mt5/SNR_Feeder.mq5` in MetaEditor.
2. In MT5, add the backend URL to **Tools → Options → Expert Advisors → Allow WebRequest**.
3. Attach one EA instance to a chart for each instrument you want the engine to analyse.
4. Set `BackendUrl` to `http://127.0.0.1:8000/ingest` and `ApiKey` to the backend key.
5. Check the Experts log and `/health` to confirm data is flowing.

The EA source has been updated in this revision but has **not** been compiled in MetaEditor in this environment. Compile and test on a demo account before relying on it.

## Tests

From `backend/`:

```bash
python -m unittest discover -s tests -v
python -m compileall -q app scripts tests
```

Current unit tests cover A/V creation, OCL creation, rejection, close-based role flips, position snapshot updates, and grouping partial closes.

## Not complete yet

This is the first implementation pass, not the finished trading workstation. The following work remains:

1. Validate the storyline protected-swing algorithm against the user's marked chart examples and replay data.
2. Complete persistent storyline/level/rejection history and multi-timeframe confluence/refinement evidence.
3. Separate the confirmation, existing medium qualification, and high setup engines; high setup split-risk allocation is not yet built.
4. Complete trade manager lifecycle, intraday 7 PM EAT reminders, swing trade classification/monitoring, and alert presentation.
5. Complete account-wide three-trade/day and three-consecutive-loss safeguards against all account deals; the current summary calculation is present, but the full alert/entry workflow and resets need replay testing.
6. Complete the dashboard, journal, phone-link access control, and local-network security.
7. Run end-to-end MT5 demo tests, weekend/restart tests, and historical replay tests. No real-money deployment should rely on this unvalidated revision.

## Safety boundary

No automated trade execution, SL/TP modification, or position closure is implemented or permitted by the MT5 feeder. Manual changes in MT5 are read and recorded; the system does not interfere with the trader's decisions.
