# SNR System Progress

## Recently implemented on `v3-upgrade`

- Setup cards now show their A/B/C grade when they have a direction. Grades use the existing rule: A = Weekly and Daily agree, B = Daily agrees, C = Daily does not agree.
- Monthly, Weekly, Daily, 4-hour, and 1-hour charts now use 30 candles each and default to candlesticks. The chart page still allows switching to a line of closes.

### Planned but missing or only partly done

| Item | State |
| --- | --- |
| **News-day and Friday warnings** | **Regression.** Version 2 had `NO_TRADE_DATES` and a “late Friday” warning. Both were dropped when the report was rewritten, so the setting remains in `.env` but does nothing. |
| **UNVERIFIED tag on setups** | The agreed plan said setups built on unverified story lines carry the tag. Only the story-line lines show `[UNVERIFIED]`. Rejecting a story line does end it in the engine. Ticking Verify does not change alerts already sent. |
| **Plain-English chart labels** | Charts still print `KEY` and `OBS`. They should read “Key level” and “Obstacle”. |
| **Entry zone choice** | It picks the newest strong key level near price, not specifically the level the 4-hour rejection created. The refine distance is 12 pips on forex (25 on gold), not exactly 20–30. |
| **Target order** | Targets are sorted by distance. Each is labelled roadblock, Daily key level, or liquidity sweep, but they are not strictly ordered by category. |
| **Missed-setup catch-up list** | Not built. Sync now gives a fresh snapshot with the current setups instead. |
| **Heart status text** | It shows live or offline and the next 4-hour close, but not “last data 12 s ago”. |
| **Asian high/low strengthening a zone** | The course mentions this. They are used only as targets and information. |
| **Risk tiers** | The medium- and high-risk entries (0.30% split across zones) are not in the system. The `RISK_PCT` settings remain in the config but do nothing. |
| **Trend lines** | Still manual. |
| **Decision points and dropping points** | Not built. The course notes never covered them fully. |

## Exit-rule requirements from mentorship notes

The rules below were supplied as mentorship guidance. The status column describes what the current v3 code does.

| Rule | Current v3 status |
| --- | --- |
| **Fixed intraday target: 100 pips** | Not enforced. `TP_PIPS` defaults to 100 in config, but v3 generates dynamic level-based targets instead. |
| **Pay at the closest obstacle first** | Partly supported. Targets include timeframe roadblocks, Daily key levels, and liquidity, then are sorted by distance together rather than by category. Up to four targets are shown. |
| **Previous day/week highs or lows** | Included as target candidates in the story-line direction, if beyond entry and the minimum target distance. |
| **Asian high/low liquidity** | Included as target candidates. Equal highs/lows and external range liquidity are not separately modeled. |
| **Break-even after about 50–100 pips or favorable structure break** | No trigger or partial-close logic. Roadblock advice mentions paying yourself or moving to break-even, but the rule is not automated. |
| **Friday close about two hours after NY open** | Not implemented. There is no scheduled position-close logic. |
| **Take intraday profit when direction is unclear; do not force a trade** | Manual guidance only; the system does not evaluate confusion or force trades. |
| **Hold higher-timeframe entries for higher-timeframe targets** | Risk tiers and explicit higher-timeframe holding/exit rules are not implemented. |
| **Entry timeframe determines exit timeframe** | Not enforced. Setups have timeframe-specific target candidates, but no exit-timeframe management. |
| **Max three trades per day; stop after three consecutive losses** | Tracked as daily limits for trades marked as taken. These limits do not close or manage open positions. |
