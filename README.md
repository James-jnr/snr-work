# SNR Story Line System

```
 MT5 (your PC)                Render                         You
┌───────────────┐  HTTPS   ┌────────────────────┐  push   ┌────────────┐
│ SNR_Feeder EA │ ───────► │ FastAPI + engine   │ ──────► │ Telegram   │
│ closed candles│  /ingest │ levels, story lines│         │ + Email    │
│ MN1 W1 D1 H4 H1│         │ confirmation setups│         └────────────┘
└───────────────┘          └────────────────────┘
```
The EA is read-only (never trades). The backend does all the analysis.

## What it alerts you about
| Alert | When |
|---|---|
| Story line (Monthly/Weekly/Daily) | A new story line starts in the **opposite** direction to the previous one (direction flips only) |
| Confirmation setup | H4 rejects an H4 level at a W1/D1 zone, then H1 QM or open-close break; graded A/B/C with entry zones, SL 20p, TP 100p, nearest obstacle, lot size |
| Daily / weekly briefing | New D1 / W1 candle: story lines, alignment, zones, obstacles, prev day/week high-low, Asia range |
| Feed offline / restored | No data from MT5 for 10 min during market hours |
| Startup snapshot | After a full data load, so you can check it matches your own chart |

## Run locally first (database + web page)
Every alert is saved automatically to `backend/snr.db` (SQLite) **and** as a readable text file in
`backend/setups/all/`. Browse, save (★), add notes and mark outcomes at **http://127.0.0.1:8000/ui**.
Telegram/email are optional: with no tokens set they are simply skipped.

```
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env             # Mac/Linux: cp .env.example .env   then set API_KEY
uvicorn app.main:app --port 8000 --env-file .env
```
- Check storage works (no MT5 needed): `python scripts/check_storage.py`
- Try the pipeline with fake data: `python scripts/simulate.py http://127.0.0.1:8000 <API_KEY>`
- MT5: add `http://127.0.0.1:8000` under *Tools > Options > Expert Advisors > Allow WebRequest*, then set the EA
  inputs `BackendUrl = http://127.0.0.1:8000/ingest` and `ApiKey` = your `API_KEY`.
- `UI_PUBLIC=true` (in `.env.example`) opens the web page without a key: local use only. On Render it is `false`
  and the page asks for the API key. Render also wipes files on restart unless you attach a persistent disk.

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

## 5. Validate before you trust it (important)
- `GET /storylines?symbol=XAUUSD&tf=D1&key=<API_KEY>` lists every detected Daily story line with level and date. Compare with your own chart/course examples (`tf=W1`, `tf=MN1` too).
- `GET /state?symbol=XAUUSD&key=...` shows current story lines, signals and why setups were skipped.
- `GET /brief?symbol=XAUUSD&send=true&key=...` pushes a briefing now.
- Tune with env vars if it is too noisy/quiet: `PIVOT_N_<TF>`, `QM_W_<TF>`, `ZONE_PIPS`, `SIGNAL_COOLDOWN_H`, `MIN_GRADE`.
- Run on a demo account first and keep a log of alerts vs outcomes.

## Pip size
Gold uses **1 pip = $0.10** (course convention, so 20 pips = $2, 100 pips = $10). Override with `PIP_OVERRIDES={"XAUUSD":0.1}`.

## Test without MT5
`python backend/scripts/simulate.py https://<service>.onrender.com <API_KEY>` sends synthetic data and triggers the startup snapshot.

## What is automated vs still manual
**Automated:** closing-price levels (A/V/open-close), RBS/SBR, rejections, story lines (W1/D1/MN1) with QM on the lower timeframe, story-line end by opposite story line, obstacles, H4-reject + H1-break confirmation, Asia skip, grading, SL/TP, lot size, Friday/news-date warnings, briefings.
**Manual (you decide):** trend-line confluence, high-impact news (use `NO_TRADE_DATES`), choosing between several zones, swing vs intraday, decision points (not covered in the course notes).

## Design choices that are mine, not the course's
- Open-close level = close of the first of two same-colour candles at the start of a run.
- QM is measured on **closing prices** (LH+LL, then close above the last LH).
- Grading A/B/C and reduced risk (0.5%) when the weekly story line disagrees.
- "At a zone" = H4 rejected level within `ZONE_PIPS` of a W1 or recent D1 level.
- Alert cooldown of 8 h per direction.
This is analysis tooling, not financial advice; the course's win-rate claims are unverified.

## Charts: see the levels, one timeframe at a time
Open **http://127.0.0.1:8000/charts** (link also in the Alerts page header).

- Tabs: **Monthly · Weekly · Daily · 4-Hour · 1-Hour**. Each tab draws that timeframe ALONE: only the levels
  the engine finds on that timeframe's own closing prices.
- Red = resistance, green = support, dashed = open-close level, `flip` = broken and reused (RBS/SBR),
  ▼/▲ on the line = the swing close that created a level, arrows = rejections, shaded band = story line
  (with its rejected level and the QM level). Table underneath lists every active level with pips from price.
- Options: number of candles, line-of-closes or candles, 8 / 12 / 20 / all levels, auto-refresh.
- Image only: `GET /api/chart.png?symbol=XAUUSD&tf=W1&key=<API_KEY>` (`tf` = MN1, W1, D1, H4, H1).

**In alerts:** every alert now carries charts. Setup = D1 + H4 + H1 with entry / SL / TP / zones drawn;
story line = that timeframe + the one below; briefings and the startup snapshot = Monthly, Weekly, Daily, H4.
They are sent to Telegram (album) and email (attachments), saved in `setups/charts/`, and shown inside each
alert card in `/ui`. Briefings also include a text **level map** (nearest levels above/below price per timeframe).
Turn off with `CHARTS_IN_ALERTS=false`.

Use the charts to **validate the rules**: if a level, rejection or story line on the chart is not what you would
mark by hand, that is exactly where to tune (`PIVOT_N_<TF>`, `QM_W_<TF>`, `ZONE_PIPS`) or change the logic.
On Render, `setups/` lives on an ephemeral disk (use a Render Disk if you want to keep saved charts across deploys).
