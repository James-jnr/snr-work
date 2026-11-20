# Version 4: Thesis and two-sided scenarios

*Written 20 November 2026. This is layer 1 of a four-layer plan. Outcome tracking (layer 2) is not built yet.*

---

## 1. In one paragraph

The system reads closed candles from MT5 (Monthly, Weekly, Daily, 4-hour, 1-hour), applies the Malaysian SNR / story-line
mentorship rules, and tells you three things: **what the story lines are**, **what the key levels are**, and **what to
look for next**. Version 4 adds a memory of its own plan for each pair (the **thesis**) and replaces "wait for a setup"
with **two scenarios**: if price goes to the continuation point, what we would look for, and if it goes to the reversal
point, what we would look for. It also fixes the problem you raised: a lower-timeframe trade that goes against a
higher story line is now labelled a **retracement**, graded accordingly, and its targets stop at the higher story
line's entry zone.

## 2. How the pieces fit

```
 MT5 (your PC)                  Backend (local now, Render later)                  You
┌────────────────┐  /ingest   ┌────────────────────────────────────────┐   ┌─────────────────────────┐
│ SNR_Feeder EA  │ ─────────► │ engine: levels, story lines (4 TFs)    │──►│ Web pages  (/ui /alert  │
│ closed candles │            │ hierarchy: trend or retracement?       │   │  /thesis /charts)       │
│ every 15 s     │ ◄───────── │ zones: key levels, strong zones        │   │ Telegram + email        │
│ (v1.1)         │  "sync"    │ setups (3) + scenarios (2) + thesis    │   │ Phone link (QR code)    │
└────────────────┘            │ SQLite: alerts, notes, thesis, history │   └─────────────────────────┘
                              └────────────────────────────────────────┘
```
The EA is read-only. It never places or changes a trade. Every number in every report comes from the engine.

## 3. Current state: what the system does

### Data and engine
- **Levels** from closing prices on five timeframes. Types named in plain words: **Classic A/V** (peak or trough), **OCL**
  (open-close level), **QM** (a level broken by a structure break). RBS and SBR flips are tracked.
- **Strength score 0 to 100** per level: timeframe weight, touches, role changes, how recent.
- **Rejections**: a wick into a level and a close back on its original side.
- **Story lines on four timeframes**: Monthly, Weekly, Daily and 4-hour. Rejection of a level on that timeframe plus a
  structure break (QM) one timeframe lower. A new story line ends the previous one on that timeframe.
- **Status text** per story line: moving with it, approaching an obstacle, or "hit a level and closed beyond it but
  there is no breakout, so it remains bearish".
- **Key levels and obstacles** selected per story line (Weekly story uses Daily levels, Daily story uses 4-hour and
  1-hour levels, Monthly story uses Weekly levels, 4-hour story uses 1-hour levels). Only the most relevant, most recent,
  strongest are kept.
- **Strong zones** where adjacent timeframes overlap (Monthly+Weekly, Weekly+Daily, Daily+4-hour, Daily+1-hour).
- **Liquidity**: previous day and week high and low, and the Asian range. Asia is the **first two 4-hour candles** of the
  broker's day.

### Decisions (new in v4 marked **NEW**)
- **Hierarchy rule: trend or retracement? NEW.** See section 5.
- **Three setups** in every report: (1) Daily story line, 4-hour entry refined to 1-hour; (2) 4-hour story line,
  1-hour entry; (3) confirmation (4-hour rejection, then 1-hour break). Each has alignment, context, entry zone,
  invalidation level, enter-by time, how long it stays valid, targets, and, for retracements, a **ceiling** **NEW**.
- **Two scenarios NEW**: Scenario A is the continuation point (with the Daily or Weekly story line) and Scenario B is the
  reversal / retracement point (against it). Each has a checklist of conditions with live status
  (`[x]` met, `[ ]` not yet, `[~]` timing note), a cancel level, targets, and a status: *waiting*, *zone reached* or
  *triggered*. Scenario B also says what would turn a retracement into a full reversal.
- **Thesis NEW**: one stored plan per pair (dominant trend, anchor story line, scenario zones, what we are waiting for)
  and a history of how it changed.
- **Daily limits**: at most 3 trades a day and stop after 3 losses in a row, driven by "I took this trade" and your
  outcomes.
- **No stop loss, lot size or risk percentage anywhere.** You set those.

### Alerts (Telegram, email, and the web pages)
| Alert | When |
|---|---|
| Story line | A story line starts in a new direction. Same direction is a quiet renewal. |
| Scenario **NEW** | Price **enters** the continuation or reversal zone. It fires once, then re-arms only after price has moved well away. |
| Setup | A confirmation setup (Setup 3) is ready and is not a retracement against its parent (grade B or better; `MIN_GRADE=C` allows more). |
| Briefing | New Daily or Weekly candle. |
| Snapshot | First data load, and every time you press Sync now. |
| Feed offline / restored | No data from MT5 for 10 minutes during market hours. |

Every alert carries charts (zone map plus one timeframe per image) and a link to its own page.

### Web pages (all local, full width)
- **/ui**: alert list with filters, saved, stats.
- **/alert/&lt;id&gt;**: the report split into cards, a Verify / Reject box per story line, your log (took it, outcome,
  notes), scenario and setup cards, charts with a click-to-enlarge popup (arrow keys, Esc).
- **/thesis NEW**: the stored plan for each pair, its two scenarios, and "how the plan changed".
- **/charts**: Zone map, Monthly, Weekly, Daily, 4-hour, 1-hour, each timeframe alone, with a table of levels.
- **Header on every page**: a heart that glows red while MT5 is sending data, the next 4-hour close with a countdown,
  **Sync now**, and **Phone link** (link plus QR code).

## 4. What changed in version 4

**New**
1. **Hierarchy rule** that labels every trade *with the trend*, *with the parent but against a higher story line*, or a
   *retracement*, and uses it for grading, validity text and target caps.
2. **Retracement ceiling and target capping**: a retracement trade's targets stop at the nearest key level of the story
   line it goes against, and that level is shown as the last target.
3. **Two-sided scenarios** (A continuation, B reversal) with condition checklists, cancel levels and targets.
4. **Thesis memory**: tables `thesis` (current plan) and `thesis_log` (one row each time it materially changes), a
   "what changed since the previous thesis" block in every report, the `/thesis` page and `/api/thesis`.
5. **Scenario alerts** (`📍 ... price reached the ... zone`), with hysteresis so they do not repeat while price hovers.
6. **Context line** under every story line, and a THESIS block and an IF PRICE GOES THERE block in every report.
7. Scenario charts: entry, targets and cancel level drawn on the 4-hour and 1-hour images.

**Changed**
- **Grade A/B/C now follows the hierarchy.** A = with every higher story line. B = with the parent but a higher story line is
  against it, or there is no higher story line. C = a retracement against its parent. Before, it only asked whether
  Weekly and Daily agreed. Old stored alerts keep their old grade, so the stats page mixes both meanings until you
  start fresh.
- **Setup 1 no longer sends its own approach alert.** The scenario alert replaces it.
- The alert page understands the new sections (Thesis, two scenario cards).
- The EA is unchanged from v1.1.

**Unchanged from v3** (see section 3): everything not listed above.

## 5. The hierarchy rule

Each tier of levels belongs to one story line: **Weekly levels belong to the Monthly story line, Daily levels to the
Weekly story line, 4-hour and 1-hour levels to the Daily story line.**

1. The **governing story line** of a trade is the one that owns the tier of its entry zone. A zone that is only a
   4-hour level is governed by the Daily story line. A 4-hour level that is **strong** because it overlaps a Daily level
   is Weekly territory, so the Weekly story line governs it.
2. Walk up from the governing story line, nearest first:
   - the trade is against the governing story line, or the governing story line is itself against its parent:
     **retracement of that story line** (grade C);
   - the parent agrees but a higher story line is against: **with the parent, but a higher story line is against it**
     (grade B);
   - everything agrees: **with the trend** (grade A);
   - nothing above to compare with: **no higher story line** (grade B).
3. For the two retracement cases, the **ceiling** is the nearest key level of the opposing story line on the trade's
   target side. Targets beyond it are removed and the ceiling is added as the last target ("going further would assume a
   reversal that has not happened").
4. A story line stays active until a new one starts on that timeframe, or you reject it on the alert page.

Worked example from the simulated data (Monthly bullish, Weekly bearish, Daily bullish, 4-hour bearish):

| Trade | Governing story line | Result |
|---|---|---|
| BUY at a plain 4-hour support | Daily (bullish) | Retracement of the Weekly (bearish). Ceiling: the Daily resistance that is the Weekly story's key level. |
| SELL at a strong 4-hour zone overlapping a Daily resistance | Weekly (bearish) | Follows the Weekly, but the Weekly is itself a retracement of the Monthly. Ceiling: the Weekly support that is the Monthly story's key level. |

The same trade now gets the same label and ceiling in Setup 2, Setup 3 and Scenario B. In an earlier build these used two
different rules; that was found and fixed during testing.

## 6. Report layout (every alert)
1. Header and any headline for this alert
2. **STORY LINES** (why each started, status of the story line, context, what it replaced)
3. **WHERE WE ARE NOW**
4. **KEY LEVELS** and **STRONG ZONES**
5. **LIQUIDITY**
6. **THESIS (the plan for this pair)**, with what changed since the previous thesis
7. **IF PRICE GOES THERE: TWO SCENARIOS**
8. The London / New York kill-zone reminder
9. Heavy divider, then **SETUPS**, always at the bottom

## 7. Database (SQLite, created and migrated automatically)
| Table | What it holds |
|---|---|
| `alerts` | Every alert: text, structured data, charts, your notes, outcome, saved, **taken**. |
| `story_status` | Your Verify / Reject decision per story line. |
| `thesis` | The current plan for each pair. |
| `thesis_log` | One row each time the plan changed materially, with the changes in words. |

## 8. Interfaces
- **EA to backend**: `POST /ingest` with the `X-API-Key` header. The reply can tell the EA to resend everything (Sync now).
- **Pages**: `/ui`, `/alert/{id}`, `/thesis`, `/charts`, `/static/*`.
- **JSON**: `/api/alerts` (list, one, save, notes, taken), `/api/stats`, `/api/story-status`, `/api/thesis`, `/api/status`,
  `/api/sync`, `/api/phone`, `/api/chart.png`, `/api/levels`, `/api/chart-meta`, `/api/chart-files/{name}`, `/state`, `/brief`,
  `/storylines`, `/health`, `/test-alert`.

## 9. Settings that matter (in `.env`)
| Setting | Default | Meaning |
|---|---|---|
| `API_KEY` | none | Must match the EA's `ApiKey`. |
| `DISPLAY_TZ` | UTC | Time zone for report times, for example `Africa/Nairobi`. |
| `MIN_GRADE` | B | Lowest grade that can trigger a confirmation-setup alert. |
| `SIGNAL_COOLDOWN_H` | 8 | One setup or scenario alert per direction per this many hours. |
| `APPROACH_PIPS` | auto | How close counts as "reached" a scenario zone (auto = 1.5 times the zone overlap size). |
| `ZONE_OVERLAP_PIPS` | auto | Strong-zone overlap size (gold 25, JPY 15, other forex 12). |
| `MIN_TARGET_PIPS` | 8 | Ignore targets closer than this. |
| `MAX_TRADES_PER_DAY`, `MAX_CONSEC_LOSSES` | 3, 3 | The daily limits. |
| `SHOW_INVALIDATION` | true | Show the invalidation / cancel level. |
| `CHARTS_IN_ALERTS` | true | Attach charts to alerts. |

Start the server with `uvicorn app.main:app --host 0.0.0.0 --port 8000 --env-file .env` from the `backend` folder.

## 10. Testing status

**Passed on simulated data**
- `scripts/check_logic.py`: unit tests of the hierarchy rule (all, retracement, parent-agrees, strong-zone override) and
  the thesis change text.
- `scripts/check_v3.py`: end to end (ingest, reports, wording, charts, verification, daily limits, sync handshake,
  phone link, thesis, scenarios, one context rule for the same trade, no target beyond a ceiling).
- `scripts/check_storage.py`: your original storage checks (`REPLAY_HOURS=120`).
- `scripts/ui_smoke.js`: every page driven in a simulated browser (jsdom): list, alert page, popup, verify and reject,
  thesis page, charts page, phone link, sync button. Needs `npm install jsdom`.

**Not verified yet**
- **Real data.** The engine has not been checked against your real gold, EURUSD or GBPUSD charts since v3. Weights and
  thresholds are my choices, not tuned.
- **A real browser**, including a phone screen. jsdom does not check layout.
- **Live Telegram image albums and email attachments.**
- **EA v1.1** has not been compiled or run in MT5.
- **Render.** Not deployed. See the notes in the README.

## 11. Known gaps and limits
- **News and Friday warnings** from v2 (`NO_TRADE_DATES`, late-Friday note) are not in the new reports. The setting does
  nothing right now.
- **UNVERIFIED tag on setups and scenarios** is not added. Story lines show it, and rejecting a story line does end it.
- **Short chart labels**: charts still print `KEY`, `OBS` and `T1`. They should read in full.
- **Entry zone choice** picks the newest strong key level near price, not specifically the level the 4-hour rejection created.
- **Targets are ordered by distance**, labelled by type, not strictly roadblocks then key levels then liquidity.
- **Scenario conditions** are fully tracked only when the anchor is the Daily story line. With a Weekly anchor the
  rejection and breakout conditions show `[~]`.
- **Scenarios** exist only around the Daily story line (or the Weekly if there is no Daily one).
- **The zone map** does not mark the scenario zones.
- **The thesis review is light**: it lists what changed in the plan. It does not yet say how the last setups turned out.
- **Not built**: risk tiers (medium and high), trend-line detection, decision points, catch-up list for missed setups,
  Asian high / low strengthening a zone, "last data N seconds ago" in the heart.
- **Render move**: alerts and charts live on disk that Render wipes on redeploy (use a Render Disk), `BASE_URL` must be set
  so Telegram links work, `UI_PUBLIC` must stay off, and the free plan sleeps.

## 12. Roadmap (not started)
2. **Outcome tracking.** Score every issued setup and scenario against later candles: entered or not, which targets were
   reached, how far price went for and against, cancelled or not. Cases where the target and the cancel level fall in
   the same hourly candle are marked *unclear*. Kept separate from your own trade log.
3. **Review block** at the top of each report: what the last scenarios did, which story lines changed, where you
   disagreed.
4. **Statistics by condition**, always with sample counts: with-trend or retracement, strong zone or not, setup type,
   session, plus your own mistake tags. The system shows the numbers and never changes rules itself.
5. **History replay** (alternative or addition): replay stored candles through the engine to score thousands of past
   scenarios. This needs the EA to send more history and the database to store candles.

## 13. Version history
| Version | Added |
|---|---|
| 1 | MT5 feeder, backend, levels and story lines on Monthly / Weekly / Daily, Telegram and email alerts |
| 2 | Database and saved alerts, web page, per-timeframe charts |
| 3 | 4-hour story line, level types and strength, key levels vs obstacles, strong zones, three setups, mentor wording, per-alert pages, verification, popup charts, heart, Sync now, phone link, daily limits |
| **4** | **Hierarchy rule and retracement ceilings, two-sided scenarios, thesis memory and page, scenario alerts** |

## 14. Upgrading from version 3
1. Stop the server, replace the project folder with version 4 (keep your `.env` and your `snr.db` / `setups` folders).
2. `pip install -r requirements.txt`, then start the server as in section 9. The database adds the new tables by itself.
3. Reload the EA only if you have not yet installed v1.1.
4. Press **Sync now**. The first report stores the first thesis for each pair, and it will say "First thesis stored".
