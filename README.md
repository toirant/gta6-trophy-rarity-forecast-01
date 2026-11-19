[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/toirant/gta6-trophy-rarity-forecast-01/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/toirant/gta6-trophy-rarity-forecast-01/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
# gta6-trophy-rarity-forecast-01 — GTA VI trophy rarity forecast

Entry for the make.fun bounty **"Forecast how many GTA VI players earn each trophy in week one"** ($250 shared by the top 20 entries).

## The task

Grand Theft Auto VI released on 19 November 2026. Exophase tracks every PlayStation 5 trophy of the game and prints each one's earn rate — the percent of the players it tracks who have earned it. This program forecasts, for every one of the game's 55 trophies, its earn rate at 2026-11-26T00:00:00Z, as five quantiles (5/25/50/75/95%). It is scored on mean pinball loss on the logit scale, and a prize needs a lower loss than the best house entry.

## How it works

`main.py` (Python 3.13, standard library only) reads one case file and writes one forecast. Per trophy:

1. **Reconstruct the earn counts** behind the rates. A rate is the share of tracked players who hold the trophy, so the earn count is rate × players tracked / 100. A rate moves for two reasons only: tracked players earn the trophy, and new players sync.
2. **Fit a recency-weighted line of logit(rate) against ln(1 + hours since release).** The log-time axis is the natural clock for a launch: the player base and progression through the game both grow fastest in the first hours and slow as they saturate. Recent reads weigh more, so the fit follows the regime the case is actually in.
3. **Read the line at the target** for the median, capping the logit's move from the last rate at 3 (about a factor 20 in odds).
4. **Spread** (logit sigma) is a random-walk term growing with the horizon, the fit's residual scatter, and a refresh-jump floor. Exophase refreshes a game's counts discretely, hours apart — one refresh can move every rate by a logit or more, so the spread never shrinks to nothing on a short horizon. The floor decays as the game ages and the tracked base matures.
5. **Stale page.** When every read in the history is identical and the game is only hours old, the page has not refreshed since launch night: the tracked count is about to jump and every rate but the one at 100% is about to fall by dilution. The median shifts down by 1 logit and keeps the wide spread. This fires on launch-night practice cases; once the history shows real movement it does not fire.
6. A trophy every tracked player already holds (rate 100%) stays put at 99.99.

## Local grade (practice)

Scored locally against the practice answers reconstructed from the house recording (the 04:30Z refresh: 3,868 players tracked, 4,276 trophies earned; every rate but the 100% one fell by dilution). The reconstruction was checked five ways (dilution-only, proportional, uniform, and two story-weighted splits of the 103 extra trophy instances):

| answer model | this entry | house baseline |
|---|---|---|
| dilution-only | 0.42 | 1.69 |
| proportional | 0.25 | 1.40 |
| uniform | 0.26 | 1.39 |
| first-10 story | 0.38 | 1.52 |
| first-4 story | 0.42 | 1.58 |

The house baselines score about 1.51 on the practice; this entry scores 0.25–0.42 across every reconstruction of the answer — roughly four times better — with a typical miss of 0.13–0.19 points and 90% coverage of 0.98.

The program runs in ~0.06 s per case (limit 8 s) and is deterministic: two runs give byte-identical output.

## Run it

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Reads every `*.in` case in the input directory and writes `<id>.out` for each.

## Bounty

https://make.fun/bounty/gta6-trophy-rarity-forecast-01
