from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from nhl_forecast.db import connect
from nhl_forecast.eval.backtest import HFA_GRID, K_GRID, load_dataset, predict
from nhl_forecast.eval.freeze import ELO_V1, ELO_V1_PARAMS, TEST_SEASON
from nhl_forecast.eval.metrics import calibration, log_loss
from nhl_forecast.models.elo import EloParams

OUT = Path("reports/figures")
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e6e5e0"
BLUE, ORANGE = "#2a78d6", "#eb6834"
MIN_BIN_GAMES = 30

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": MUTED,
        "axes.titlecolor": TEXT,
        "axes.titlesize": 12,
        "axes.titlelocation": "left",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "text.color": TEXT,
        "font.size": 10,
        "legend.frameon": False,
        "savefig.dpi": 160,
    }
)


def season_label(season: int) -> str:
    return f"{season // 10000}-{season % 100:02d}"


def grid_figure(conn) -> None:
    dataset = load_dataset(conn)
    mask = dataset.train_mask()
    y = dataset.outcomes[mask]
    losses = np.array(
        [
            [log_loss(predict(dataset, EloParams(k=k, hfa=h))[mask], y) for k in K_GRID]
            for h in HFA_GRID
        ]
    )
    fig, ax = plt.subplots(figsize=(8, 4.2))
    image = ax.imshow(
        losses,
        origin="lower",
        aspect="auto",
        cmap="Blues_r",
        extent=(K_GRID[0] - 0.25, K_GRID[-1] + 0.25, HFA_GRID[0] - 1.25, HFA_GRID[-1] + 1.25),
    )
    ax.grid(False)
    ax.plot(ELO_V1_PARAMS.k, ELO_V1_PARAMS.hfa, "o", ms=9, mfc=SURFACE, mec=TEXT, mew=1.5)
    ax.annotate(
        f"elo-v1: K={ELO_V1_PARAMS.k}, HFA={ELO_V1_PARAMS.hfa:.0f}\nlog loss {losses.min():.4f}",
        (ELO_V1_PARAMS.k, ELO_V1_PARAMS.hfa),
        xytext=(10, 12),
        textcoords="offset points",
        bbox={"boxstyle": "round,pad=0.3", "fc": SURFACE, "ec": GRID},
    )
    ax.set(xlabel="K", ylabel="Home-ice advantage (Elo points)")
    ax.set_title("Many settings score almost the same (training log loss, lower is better)")
    fig.colorbar(image, ax=ax, label="Log loss").outline.set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "elo_grid.png")
    plt.close(fig)


def stored_predictions(conn) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = conn.execute(
        """
        select g.season, b.p_home_win, g.home_win::int as y
        from backtest_predictions b join games g using (game_id)
        where b.model_version = %s
        """,
        (ELO_V1,),
    ).fetchall()
    return (
        np.array([r["season"] for r in rows]),
        np.array([r["p_home_win"] for r in rows]),
        np.array([r["y"] for r in rows], dtype=float),
    )


def calibration_figure(seasons, p, y) -> None:
    fig, ax = plt.subplots(figsize=(6.4, 5.4))
    ax.plot([0.25, 0.8], [0.25, 0.8], color=MUTED, lw=1, ls="--")
    ax.annotate(
        "perfect calibration",
        (0.72, 0.72),
        xytext=(-8, 6),
        rotation=38,
        textcoords="offset points",
        ha="right",
        color=MUTED,
    )
    groups = (
        ("2016-17 to 2024-25", seasons != TEST_SEASON, BLUE),
        ("2025-26 (test)", seasons == TEST_SEASON, ORANGE),
    )
    for name, mask, color in groups:
        bins = [b for b in calibration(p[mask], y[mask]).bins if b.games >= MIN_BIN_GAMES]
        ax.plot(
            [b.mean_predicted for b in bins],
            [b.observed for b in bins],
            "-o",
            color=color,
            lw=2,
            ms=6,
            mec=SURFACE,
            mew=1.5,
            label=name,
        )
    ax.set(xlabel="Predicted home-win probability", ylabel="Share of those games won at home")
    ax.set_title("Calibrated through 2024-25, less reliable in 2025-26")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "elo_calibration.png")
    plt.close(fig)


def points_spread(conn) -> dict[int, float]:
    rows = conn.execute(
        """
        select season, home_team_id, away_team_id, home_win, last_period_type
        from games where game_type = 2 and home_win is not null and season <= %s
        """,
        (TEST_SEASON,),
    ).fetchall()
    points: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    played: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        home, away = r["home_team_id"], r["away_team_id"]
        winner, loser = (home, away) if r["home_win"] else (away, home)
        points[r["season"]][winner] += 2
        if r["last_period_type"] in ("OT", "SO"):
            points[r["season"]][loser] += 1
        played[r["season"]][home] += 1
        played[r["season"]][away] += 1

    return {
        season: float(np.std([points[season][t] / (2 * played[season][t]) for t in played[season]]))
        for season in played
    }


def seasons_figure(conn, seasons, p, y) -> None:
    rate = float(y[seasons <= 20232024].mean())
    labels = sorted(set(seasons))
    elo = [log_loss(p[seasons == s], y[seasons == s]) for s in labels]
    constant = [log_loss(np.full((seasons == s).sum(), rate), y[seasons == s]) for s in labels]
    spread = points_spread(conn)
    x = np.arange(len(labels))

    fig, (top, bottom) = plt.subplots(2, 1, figsize=(9, 6), sharex=True, height_ratios=[3, 2])
    top.plot(x, constant, "-o", color=ORANGE, lw=2, ms=6, mec=SURFACE, mew=1.5, label="Constant")
    top.plot(x, elo, "-o", color=BLUE, lw=2, ms=6, mec=SURFACE, mew=1.5, label="elo-v1")
    top.annotate("elo-v1", (x[0], elo[0]), xytext=(0, -16), textcoords="offset points")
    top.annotate("Constant", (x[0], constant[0]), xytext=(0, 8), textcoords="offset points")
    top.set(ylabel="Log loss")
    top.set_title("Elo scores worse when teams finish closer together")
    top.legend(loc="lower left", ncols=2)

    bottom.bar(x, [spread[s] for s in labels], color=BLUE, width=0.55)
    bottom.set(ylabel="SD of team\npoints %")
    bottom.set_title("How far apart teams finished")
    bottom.set_xticks(x, [season_label(s) for s in labels])
    bottom.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(OUT / "elo_by_season.png")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        seasons, p, y = stored_predictions(conn)
        grid_figure(conn)
        calibration_figure(seasons, p, y)
        seasons_figure(conn, seasons, p, y)


if __name__ == "__main__":
    main()
