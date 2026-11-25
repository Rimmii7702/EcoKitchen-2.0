"""Render the portfolio figure from the sealed aggregate scorecard."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "forecasting" / "results" / "freshretailnet-holdout-v1.json"
OUTPUT = ROOT / "docs" / "figures" / "freshretailnet-holdout-wape.png"
INK, MUTED, BACKGROUND = "#183329", "#52635b", "#f6f8f4"
MODELS = (
    ("seasonal_naive", "Seasonal naive", "#79552d"),
    ("lightgbm", "LightGBM · 96 trees", "#176447"),
)


def main():
    scorecard = json.loads(SCORECARD.read_text(encoding="utf-8"))
    advantage = scorecard["wape_improvement_percentage_points"]
    days = list(range(1, 8))
    fig, (overall, daily) = plt.subplots(
        1, 2, figsize=(13, 6.1), gridspec_kw={"width_ratios": [1, 1.33]}, facecolor=BACKGROUND,
    )
    fig.subplots_adjust(left=.095, right=.945, bottom=.22, top=.68, wspace=.38)
    fig.text(.075, .91, "FreshRetailNet-50K holdout", fontsize=24, weight="bold", color=INK)
    fig.text(.075, .842, "350,000 forecasts per model  •  50,000 store–product pairs  •  June 26–July 2, 2024",
             fontsize=12, color=MUTED)
    fig.text(.075, .073, "WAPE is measured on normalized observed grocery sales. Lower is better.",
             fontsize=10.5, color=INK, weight="bold")
    fig.text(.075, .038, "Source: Dingdong-Inc, FreshRetailNet-50K v1.0 (CC BY 4.0). EcoKitchen aggregate evaluation.",
             fontsize=9.5, color=MUTED)

    for axis in (overall, daily):
        axis.set_facecolor(BACKGROUND)
        axis.spines[["top", "right", "left", "bottom"]].set_visible(False)
        axis.tick_params(length=0, colors=MUTED, labelsize=10)
        axis.set_axisbelow(True)
    overall.set_title("Overall WAPE", loc="left", color=INK, weight="bold", pad=16)
    overall.set_xlim(0, 53)
    overall.set_xticks([0, 10, 20, 30, 40, 50], labels=[f"{x}%" for x in (0, 10, 20, 30, 40, 50)])
    overall.set_ylim(-.9, 1.65)
    overall.set_yticks([])
    overall.xaxis.grid(color="#dce4dc")
    overall.text(0, -.67, f"{advantage:.4f} percentage points lower", color=MODELS[1][2],
                 fontsize=11, weight="bold")

    daily.set_title("WAPE by forecast horizon", loc="left", color=INK, weight="bold", pad=16)
    daily.set_xticks(days, labels=[f"Day {day}" for day in days])
    daily.set_ylim(27, 47)
    daily.set_yticks([30, 35, 40, 45], labels=["30%", "35%", "40%", "45%"])
    daily.yaxis.grid(color="#dce4dc")
    for position, (key, label, color) in zip((1, 0), MODELS, strict=True):
        model = scorecard[key]
        metric = model["overall"]["all"]["wape_pct"]
        overall.text(0, position + .28, label, color=INK, fontsize=10, weight="bold")
        overall.barh(position, metric, color=color, height=.27, zorder=3)
        overall.text(metric + 1, position, f"{metric:.4f}%", color=color, va="center", fontsize=10, weight="bold")
        daily.plot(days, [model["by_horizon"][str(day)]["all"]["wape_pct"] for day in days],
                   label=label, color=color, marker="o" if position else "D", linewidth=2.6, markersize=5)
    daily.legend(loc="upper left", bbox_to_anchor=(0, -.19), ncol=2, frameon=False,
                 fontsize=9.5, labelcolor=INK, borderaxespad=0)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, dpi=180, facecolor=BACKGROUND)
    plt.close(fig)


if __name__ == "__main__":
    main()
