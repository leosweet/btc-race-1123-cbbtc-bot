# cbBTC 1-minute trading bot — btc-race-24h-20261123-0511

[![make.fun · btc-race-24h-20261123-0511](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![rank](https://make.fun/badge/btc-race-24h-20261123-0511/leosweet/btc-race-1123-cbbtc-bot/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![reward](https://make.fun/badge/btc-race-24h-20261123-0511/leosweet/btc-race-1123-cbbtc-bot/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)

A long-only, no-leverage bot that trades Coinbase Wrapped BTC (cbBTC) against
USDC minute by minute on the published 1-minute on-chain prices, for the
[make.fun bounty "Race a BTC trading bot live for 24 hours"](https://make.fun/bounty/btc-race-24h-20261123-0511).
It starts with $1,000 of paper money; nothing is traded for real. It speaks
`agent-fun/bars-replay/v1` on stdin/stdout (see `main.py`) using the kit's
Python SDK shim, vendored untouched under `vendor/`.

## Strategy

Two legs share the single position slot; every entry is full size.

- **Trend ride.** Enter when the 150-minute return clears +110 bps while the
  close sits above its 60-bar EMA — the move has lasted long enough to be a
  trend, not a one-minute spike. Ride it with a hard stop (-200 bps from the
  fill), a trailing stop (-300 bps from the peak since entry), a reversal exit
  (120-minute return at or below -175 bps) and a stale exit: if, 120 bars
  after entry, the position has gained less than +75 bps, the trend never
  developed and the bot steps aside instead of riding a slow bleed.
- **Dip bounce.** Enter after a sharp 10-minute drop (at or below -100 bps)
  that has stopped falling over the last 3 minutes, provided the 120-minute
  trend is not in free fall (at or above -150 bps). Take +110 bps, stop at
  -90 bps, give up after 30 bars.

After any exit a 30-bar cooldown blocks re-entry, so a choppy stretch cannot
whipsaw the bot into repeated round trips (each costs roughly 45 bps at the
depths this pool trades at). No new entries after bar 1380.

All arithmetic is integer, in the price scale the bars arrive in. No wall
clock, no randomness, no network, no files outside the repository: two runs
on the same bars answer byte for byte alike.

## Local validation (dev window, 1,440 bars)

Validated with a from-scratch Python port of the harness fill math (next-bar
open fills, 4 bps fee, 2 bps slippage, constant-product impact on open depth,
flatten at the last close), driving the real `main.py` over the NDJSON wire:

| Metric | Value |
| --- | --- |
| Net PnL (dev) | **+$21.74** |
| Trades | 1 (buy bar 190 @ $59,675.57, held to the flatten) |
| Max drawdown | 2.04% |
| Fees | $0.81 |
| Determinism | two runs, action logs byte-identical (1,440 replies each) |
| Neighbourhood stability (28 one-step parameter perturbations) | mean +$21.85, min +$19.88, max +$26.84 |
| Same bot on the prior round's dev window (2026-11-18, a falling day) | +$14.32, 4 trades, max drawdown 0.74% |

## Layout

- `main.py` — the bot (standard library + the vendored shim only)
- `vendor/makefun_bars.py` — the kit's Python SDK shim, copied byte for byte
- `.makefun/owner.json` — claim file naming the entrant's wallet
