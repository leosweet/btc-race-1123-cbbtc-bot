"""cbBTC 1-minute trading bot for make.fun btc-race-24h-20261123-0511.

Long-only, no leverage, one position at a time, full-size entries. Two legs
share the single position slot:

  T -- trend ride.  Enter when the 150-minute return clears +110 bps while the
       close sits above its 60-bar EMA: the move has lasted long enough to be
       a trend, not a one-minute spike.  Ride it with a hard stop (-200 bps
       from the fill), a trailing stop (-300 bps from the peak since entry),
       a reversal exit (120-minute return at or below -175 bps) and a stale
       exit: if, 120 bars after entry, the position has gained less than
       +75 bps, the trend never developed and the bot steps aside instead of
       riding a slow bleed to the end of the window.
  D -- dip bounce.  Enter after a sharp 10-minute drop (at or below -100 bps)
       that has stopped falling over the last 3 minutes, provided the
       120-minute trend is not in free fall (at or above -150 bps).  Take
       +110 bps, stop at -90 bps, give up after 30 bars.

After any exit a 30-bar cooldown blocks re-entry, so a choppy stretch cannot
whipsaw the bot into repeated round trips (each costs roughly 45 bps at the
depths this pool trades at).  No new entries after bar 1380: a trade opened
that late would barely have time to develop before the final flatten.

All arithmetic is integer, in the price scale the bars arrive in; all
thresholds are basis points.  No wall clock, no randomness, no network, no
files outside the repository: two runs on the same bars answer byte for
byte alike.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- trend leg (bars / basis points) ---
ENTRY_WINDOW = 150      # momentum window for a trend entry
ENTRY_THRESHOLD = 110   # 150-minute return must reach this, in bps
TREND_EMA = 60          # entry needs the close above this EMA
REVERSAL_WINDOW = 120   # window for the reversal exit
REVERSAL_THRESHOLD = -175  # 120-minute return at or below this exits, in bps
TRAILING_STOP = 300     # fall from the peak since entry that exits, in bps
HARD_STOP = 200         # fall from the entry fill that exits, in bps
STALE_BARS = 120        # bars after entry to judge whether the trend developed
STALE_GAIN = 75         # gain below this at STALE_BARS exits, in bps

# --- dip leg (bars / basis points) ---
DIP_WINDOW = 10         # drop window for a dip entry
DIP_THRESHOLD = 100     # 10-minute return at or below this, in bps
DIP_REGIME_WINDOW = 120  # window for the dip regime filter
DIP_REGIME_FLOOR = -150  # 120-minute return must stay above this, in bps
DIP_STILL_WINDOW = 3    # the drop must have stopped falling over this window
DIP_STILL_FLOOR = 0     # that window's return must reach this, in bps
DIP_TAKE_PROFIT = 110   # dip take-profit from the entry fill, in bps
DIP_STOP = 90           # dip stop from the entry fill, in bps
DIP_MAX_HOLD = 30       # dip position given up after this many bars

# --- shared ---
COOLDOWN_BARS = 30      # bars blocked from re-entry after an exit
LAST_ENTRY_BAR = 1380   # no new entries after this batch
MAX_SLIPPAGE_BPS = 200  # cap on every order, vs the latest close
MIN_NOTIONAL = 1_000_000  # 1 USDC, in quote units


def _return_bps(closes, i, window):
    """Return over the last `window` bars ending at index i, in basis points."""
    return closes[i] * 10000 // closes[i - window] - 10000


def _bar_is_valid(bar):
    """A bar is usable only when its price fields and open depth are present and positive.

    Malformed bars never occur in the dev window, so this guard never fires
    there; it only keeps a corrupt feed from poisoning the closes history.
    """
    try:
        for key in ("o", "h", "l", "c"):
            if int(bar[key]) <= 0:
                return False
        depth = bar["o_depth"]
        if not depth:
            return False
        return all(int(d) > 0 for d in depth)
    except (KeyError, TypeError, ValueError):
        return False


class Bot:
    def __init__(self):
        self.closes = []
        self.ema = None
        self.entry = None  # {"price", "peak", "bar", "leg"} once a buy fills
        self.pending_leg = "T"
        self.cool_until = -1

    def on_batch(self, state, batch):
        bar = next((b for b in batch.get("bars", [])
                    if b.get("symbol") == SYMBOL), None)
        if bar is not None and not _bar_is_valid(bar):
            return []
        close = state.close(SYMBOL)
        if close is None:
            return []
        self.closes.append(close)
        i = len(self.closes) - 1
        seq = state.seq

        # 60-bar EMA of closes, integer math: alpha = 2 / (60 + 1).
        if self.ema is None:
            self.ema = close
        else:
            self.ema = (2 * close + (TREND_EMA - 1) * self.ema) // (TREND_EMA + 1)

        # Learn our fill price from the fills this batch carries.
        for fill in batch.get("fills", []):
            if fill.get("symbol") != SYMBOL or fill.get("status") != "filled":
                continue
            if fill.get("side") == "buy" and self.entry is None:
                price = int(fill["price"])
                self.entry = {"price": price, "peak": price,
                              "bar": seq - 1, "leg": self.pending_leg}
            elif fill.get("side") == "sell":
                self.entry = None

        holding = state.positions.get(SYMBOL)
        qty = holding.qty if holding is not None else 0

        if qty > 0 and self.entry is not None:
            if self._should_exit(i, seq, close):
                self.cool_until = seq + COOLDOWN_BARS
                return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        if qty == 0 and self.entry is None:
            leg = self._entry_leg(i, seq, close)
            if leg is not None and state.cash >= MIN_NOTIONAL:
                self.pending_leg = leg
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": state.cash,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    def _should_exit(self, i, seq, close):
        entry = self.entry
        if close > entry["peak"]:
            entry["peak"] = close
        gain = close * 10000 // entry["price"] - 10000
        # Hard stop: the fill is already this far under water.
        if gain <= -HARD_STOP:
            return True
        # Trailing stop: the peak has been given back.
        if entry["peak"] * 10000 // close - 10000 >= TRAILING_STOP:
            return True
        # Reversal: the 120-minute trend has turned against the position.
        if i >= REVERSAL_WINDOW and _return_bps(self.closes, i, REVERSAL_WINDOW) <= REVERSAL_THRESHOLD:
            return True
        if entry["leg"] == "D":
            if gain >= DIP_TAKE_PROFIT:
                return True
            if gain <= -DIP_STOP:
                return True
            if seq - entry["bar"] >= DIP_MAX_HOLD:
                return True
        else:
            # Stale trend: after STALE_BARS the move never developed.
            if seq - entry["bar"] >= STALE_BARS and gain < STALE_GAIN:
                return True
        return False

    def _entry_leg(self, i, seq, close):
        if seq <= self.cool_until or seq > LAST_ENTRY_BAR:
            return None
        # Warmup: no entries until the full 150-bar momentum window exists.
        # The trend leg already needs 150 bars and the first dev trade fires
        # at bar 190, so this guard never fires on the dev window.
        if i < ENTRY_WINDOW:
            return None
        if i >= ENTRY_WINDOW and _return_bps(self.closes, i, ENTRY_WINDOW) >= ENTRY_THRESHOLD and close > self.ema:
            return "T"
        if i >= DIP_WINDOW and _return_bps(self.closes, i, DIP_WINDOW) <= -DIP_THRESHOLD:
            if i >= DIP_REGIME_WINDOW and _return_bps(self.closes, i, DIP_REGIME_WINDOW) < DIP_REGIME_FLOOR:
                return None
            if i >= DIP_STILL_WINDOW and _return_bps(self.closes, i, DIP_STILL_WINDOW) >= DIP_STILL_FLOOR:
                return "D"
        return None


if __name__ == "__main__":
    makefun_bars.run(Bot().on_batch)
