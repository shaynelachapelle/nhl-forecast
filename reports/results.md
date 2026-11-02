# Results log

Key decisions and results, recorded when they happen. Later changes get a dated note; logged results are not rewritten.

## Data (2026-10-26)

- 2015-16 to 2026-27: 14,856 regular-season and 996 playoff games. Every season matches its official game count.
- Home-win rate by season ranges from 0.522 to 0.563 (training average 0.541).
- OT/SO games are close to a coin flip for the home team: home-ice advantage shows up mostly in regulation.

## Elo baseline

### Decision: test goal margin as an Elo variant (2026-11-01)

Added `ln(margin + 1)` as an optional multiplier on Elo rating updates. It changes how far ratings move after a game, not what is predicted. Tested before the 2025-26 test run so it can still be compared fairly.

Training seasons only (2016-17 to 2023-24, 9,658 games), each version tuned separately:

| Version | K | HFA | Log loss | Brier | Accuracy |
|---|---|---|---|---|---|
| Constant home-win rate | | | 0.6897 | 0.2483 | 54.1% |
| Plain Elo | 9.5 | 30 | 0.6679 | 0.2376 | 58.9% |
| Margin | 8.0 | 30 | 0.6661 | 0.2368 | 59.3% |
| Margin + favorite adjustment | 8.5 | 30 | 0.6664 | 0.2370 | 59.2% |

- Margin vs plain: -0.0018 log loss (95% CI -0.0025 to -0.0010), better in all 8 seasons.
- The favorite adjustment added nothing, so it was dropped for simplicity.
- Adopted only if it also wins on 2024-25 validation. Plain Elo stays as the reference.

### Validation: 2024-25 (2026-11-01)

1,312 games, each variant tuned on training seasons only. Difference is validation log loss vs plain.

| Variant | K | HFA | Train LL | Val LL | Brier | Accuracy | Difference (95% CI) |
|---|---|---|---|---|---|---|---|
| Constant home-win rate | | | 0.6897 | 0.6862 | 0.2465 | 56.2% | |
| Plain | 9.5 | 30 | 0.6679 | 0.6683 | 0.2379 | 59.0% | reference |
| Margin | 8.0 | 30 | 0.6661 | 0.6681 | 0.2378 | 58.8% | -0.0002 (-0.0023 to +0.0018) |
| No playoffs | 9.5 | 30 | 0.6678 | 0.6680 | 0.2378 | 58.8% | -0.0003 (-0.0013 to +0.0006) |
| Regress 1/4 | 9.0 | 30 | 0.6677 | 0.6684 | 0.2380 | 58.8% | +0.0000 (-0.0009 to +0.0010) |
| Regress 1/2 | 11.0 | 30 | 0.6686 | 0.6692 | 0.2383 | 59.3% | +0.0009 (-0.0007 to +0.0025) |
| 2020-21 own HFA (25) | 9.5 | 30 | 0.6679 | 0.6683 | 0.2379 | 59.0% | +0.0000 |
| Tune without 2020-21 | 9.5 | 30 | 0.6679 | 0.6683 | 0.2379 | 59.0% | +0.0000 |

- No variant clearly beats plain on validation. Margin's training gain was not confirmed, though one season cannot rule it out.
- The no-crowds season needs no special handling: its best HFA (25) is close to the normal 30.

### Decision: freeze plain Elo as elo-v1 (2026-11-01)

K=9.5, HFA=30, regress 1/3, playoffs included, one shared HFA. No variant had a validation interval clearly below zero, which was the agreed rule for adopting one. Margin runs as a shadow model from step 3 and is promoted only if it beats Elo on live log loss.
