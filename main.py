#!/usr/bin/env python3
"""Forecast GTA VI PS5 trophy earn rates on Exophase.

Reads one case file (schema makefun/gta6-trophies-case/v1) from
$MAKEFUN_INPUT_DIR (or /input) and writes one forecast file to
$MAKEFUN_OUTPUT_DIR (or /output): for every trophy, five quantiles
(5/25/50/75/95%) of its earn rate at the case's target.

Method, per trophy
------------------
1. Reconstruct the earn counts behind the rates: a trophy's rate is the
   share of the players Exophase tracks who hold it, so its earn count
   is rate * players_tracked / 100. The rate moves for two reasons only:
   tracked players earn the trophy (count up) and new players sync
   (denominator up). Both are visible in the history.
2. Fit a recency-weighted least-squares line of logit(rate) against
   ln(1 + hours since release). The log-time axis is the natural clock
   for a launch: the player base and the progression through the game
   both grow fastest in the first hours and slow as they saturate, so a
   line in log-time captures the trajectory where a line in clock time
   would not. Recent reads weigh more (24 h half-life in weight), so
   the fit follows the regime the case is actually in.
3. Read the line at the target. That is the median. The logit move from
   the last rate is capped at 3 (about a factor 20 in odds) so a steep
   early slope cannot run away over a long horizon.
4. The spread (logit sigma) is three parts added in quadrature:
   a random-walk term that grows with the horizon, 0.5 * sqrt(days);
   the residual scatter of the fit, how noisy the trophy's own history
   is; and a refresh-jump floor. Exophase refreshes a game's counts
   discretely, hours apart, not continuously: one refresh can move
   every rate by a logit or more, so the spread never shrinks to
   nothing on a short horizon the way a pure random walk would. The
   floor is largest at launch, when one refresh can multiply the
   tracked base, and decays as the game ages and the base matures.
5. Stale page. When every read in the history is identical and the game
   is only hours old, the page has not refreshed since launch night:
   the tracked count is about to jump and every rate but the one at
   100% is about to fall by dilution. Shift the median down by 1 logit
   (expecting the tracked count to roughly triple before the target)
   and keep the wide spread. This fires on launch-night practice
   cases; once the history shows real movement it does not fire.
6. A trophy every tracked player already holds (rate 100%) stays put:
   it is forecast at 99.99 with a narrow spread.

Output is one JSON object, {"trophies": [{"id": n, "q": [...]}]}, one
row per trophy, quantiles non-decreasing, each in [0, 100]. No
randomness anywhere: the same case always gives the same forecast.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

# Standard normal quantiles at the five levels.
Z = [-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722]

# Random-walk spread per sqrt(day) on the logit scale.
RW_SIGMA = 0.5
# Refresh-jump floor, at launch (age 0): one Exophase refresh can move
# a rate by this much on the logit scale, whatever the horizon.
REFRESH_SIGMA_LAUNCH = 0.55
# Refresh-jump floor once the player base is mature: a single refresh
# moves a large tracked base far less, so the floor decays to this.
REFRESH_SIGMA_MATURE = 0.25
# The refresh floor decays with the game's age (days) at this rate.
REFRESH_DECAY_DAYS = 2.0
# How much the median falls when the page is stale (launch night).
STALE_SHIFT = -1.0
# A stale page only counts while the game is this young (hours).
STALE_MAX_AGE_HOURS = 24.0
# Recency weight half-life for the trend fit (hours).
WEIGHT_TAU_HOURS = 24.0
# Cap on the logit's move from the last rate.
MAX_LOGIT_MOVE = 3.0
# Cap on the spread, so a noisy fit cannot blow the quantiles out.
MAX_SIGMA = 2.5


def parse_time(text):
    """Parse an ISO 8601 timestamp to an aware datetime (UTC)."""
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def logit(pct):
    """logit of a percent, clamped to [0.01, 99.99] first."""
    p = min(max(pct, 0.01), 99.99) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit(lo):
    """Inverse logit, as a percent in (0, 100)."""
    if lo >= 0:
        return 100.0 / (1.0 + math.exp(-lo))
    e = math.exp(lo)
    return 100.0 * e / (1.0 + e)


def clamp(value, low, high):
    return low if value < low else high if value > high else value


def fit_line(xs, ys, ws):
    """Weighted least squares y = a + b*x. Returns (a, b, resid_sigma)."""
    sw = sum(ws)
    if sw <= 0:
        return (ys[0] if ys else 0.0, 0.0, 0.0)
    xbar = sum(w * x for w, x in zip(ws, xs)) / sw
    ybar = sum(w * y for w, y in zip(ws, ys)) / sw
    sxx = sum(w * (x - xbar) ** 2 for w, x in zip(ws, xs))
    if sxx <= 1e-12:
        b = 0.0
    else:
        sxy = sum(w * (x - xbar) * (y - ybar)
                  for w, x, y in zip(ws, xs, ys))
        b = sxy / sxx
    a = ybar - b * xbar
    resid = math.sqrt(
        sum(w * (y - (a + b * x)) ** 2 for w, x, y in zip(ws, xs, ys)) / sw
    )
    return (a, b, resid)


def forecast_case(case):
    """Return the forecast rows for one case dict."""
    trophies = case["trophies"]
    history = case.get("history", [])
    horizon_days = case["horizon_minutes"] / 1440.0

    release = parse_time(case["game"]["release_date"] + "T00:00:00Z")
    checkpoint = parse_time(case["at"])
    target = parse_time(case["target"])
    age_hours = (checkpoint - release).total_seconds() / 3600.0

    # Is the whole history one unchanging read? (launch night: the
    # page sat still while the game was hours old.)
    stale = False
    if history and age_hours <= STALE_MAX_AGE_HOURS:
        first_players = history[0]["players_tracked"]
        first_rates = history[0]["rates"]
        stale = all(
            h["players_tracked"] == first_players
            and h["rates"] == first_rates
            for h in history
        )

    # Read times in hours since release, as log-time coordinates.
    read_hours = []
    for h in history:
        t = parse_time(h["at"])
        hrs = (t - release).total_seconds() / 3600.0
        read_hours.append(max(hrs, 0.0))
    target_hours = max((target - release).total_seconds() / 3600.0, 0.0)
    x_target = math.log1p(target_hours)

    # Recency weights: a read's weight decays with its age at the
    # checkpoint, 24 h half-life in weight.
    weights = []
    for h in history:
        t = parse_time(h["at"])
        age = (checkpoint - t).total_seconds() / 3600.0
        weights.append(math.exp(-max(age, 0.0) / WEIGHT_TAU_HOURS))

    rw_sigma = RW_SIGMA * math.sqrt(max(horizon_days, 0.0))
    # Refresh-jump floor, decaying with the game's age at the
    # checkpoint: a refresh moves a young, fast-growing tracked base
    # far more than a mature one.
    refresh_sigma = REFRESH_SIGMA_MATURE + REFRESH_SIGMA_LAUNCH * math.exp(
        -(age_hours / 24.0) / REFRESH_DECAY_DAYS
    )

    rows = []
    for idx, trophy in enumerate(trophies):
        xs, ys, ws = [], [], []
        for j, h in enumerate(history):
            rate = h["rates"][idx]
            if rate is None:
                continue
            xs.append(math.log1p(read_hours[j]))
            ys.append(logit(rate))
            ws.append(weights[j])

        last_rate = None
        for h in reversed(history):
            if h["rates"][idx] is not None:
                last_rate = h["rates"][idx]
                break
        if last_rate is None:
            last_rate = 1.0
        y_last = logit(last_rate)

        # A trophy everyone holds stays held.
        if last_rate >= 99.99:
            rows.append({"id": trophy["id"], "q": [99.99] * 5})
            continue

        if len(xs) >= 2:
            a, b, resid = fit_line(xs, ys, ws)
            median_logit = a + b * x_target
        else:
            resid = 0.0
            median_logit = y_last

        # Cap the move from the last rate, then the stale-page shift.
        move = clamp(median_logit - y_last, -MAX_LOGIT_MOVE, MAX_LOGIT_MOVE)
        median_logit = y_last + move
        if stale:
            median_logit += STALE_SHIFT

        sigma = math.sqrt(
            rw_sigma * rw_sigma
            + min(resid, 1.0) ** 2
            + refresh_sigma * refresh_sigma
        )
        sigma = min(sigma, MAX_SIGMA)

        quantiles = []
        for z in Z:
            q = inv_logit(median_logit + z * sigma)
            quantiles.append(round(clamp(q, 0.01, 99.99), 4))
        # Rounding can only tie neighbours, never reverse them.
        rows.append({"id": trophy["id"], "q": quantiles})

    return {"trophies": rows}


def main():
    in_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)

    names = sorted(
        n for n in os.listdir(in_dir) if n.endswith(".in")
    )
    if not names:
        print("no .in case files in %s" % in_dir, file=sys.stderr)
        return 1
    for name in names:
        case_id = name[:-3]
        with open(os.path.join(in_dir, name), "r", encoding="utf-8") as f:
            case = json.load(f)
        forecast = forecast_case(case)
        out_path = os.path.join(out_dir, case_id + ".out")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(forecast, f, separators=(",", ":"))
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
