"""Serialize reproducible results and render validation charts without a display."""

import json


def write_json(path, data):
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(data, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")


def plot_validation(output, report):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(10, 5.4), layout="constrained")
    FigureCanvasAgg(figure)
    error_axis, bias_axis = figure.subplots(1, 2)
    horizon = range(1, 8)
    scores = report["pooled_validation"]["by_horizon"]
    for key, title, color in (
        ("all", "All observations", "#264653"),
        ("in_stock_24h", "Fully in stock (24 h)", "#197457"),
        ("stockout_24h", "Stockout-affected (24 h)", "#b3551c"),
    ):
        error_axis.plot(horizon, [scores[str(i)][key]["mae"] for i in horizon],
                        "o-", color=color, label=title)
        bias_axis.plot(horizon, [scores[str(i)][key]["signed_bias_pct"] for i in horizon],
                       "o-", color=color, label=title)
    error_axis.set(ylabel="MAE (normalized sales units)", xlabel="Forecast horizon (days)", xticks=list(horizon))
    bias_axis.set(ylabel="Signed bias (%)", xlabel="Forecast horizon (days)", xticks=list(horizon))
    bias_axis.axhline(0, color="#7b8587", linewidth=1, linestyle="--")
    for axis in (error_axis, bias_axis):
        axis.grid(axis="y", alpha=.25)
    error_axis.legend(loc="best", frameon=False)
    figure.suptitle("FreshRetailNet-50K | Validation seasonal-naive observed sales")
    figure.savefig(output / "validation-by-horizon.png", dpi=140)


def plot_comparison(output, report, *, score_key="pooled_validation", scope="pooled validation"):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(11, 7), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2)
    horizons = list(range(1, 8))
    metrics = [
        ("mae", "MAE (normalized sales units)"), ("rmse", "RMSE (normalized sales units)"),
        ("wape_pct", "WAPE (%)"), ("signed_bias_pct", "Signed bias (%)"),
    ]
    for axis, (metric, label) in zip(axes.flat, metrics):
        for method, title, color in (("seasonal_naive", "Seasonal naive", "#b3551c"),
                                     ("lightgbm", "LightGBM", "#197457")):
            card = report[method][score_key] if score_key else report[method]
            axis.plot(horizons, [card["by_horizon"][str(i)]["all"][metric] for i in horizons],
                      "o-", color=color, label=title)
        axis.set(ylabel=label, xlabel="Forecast horizon (days)", xticks=horizons)
        axis.grid(axis="y", alpha=.25)
    axes[1, 1].axhline(0, color="#7b8587", linewidth=1, linestyle="--")
    axes[0, 0].legend(loc="best", frameon=False)
    figure.suptitle(f"FreshRetailNet-50K | Observed sales, {scope}")
    figure.savefig(output / "comparison-by-horizon.png", dpi=140)


def plot_actuals(output, totals):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import StrMethodFormatter

    figure = Figure(figsize=(12, 4.7), layout="constrained")
    FigureCanvasAgg(figure)
    days = sorted({row["target_date"] for row in totals})
    series = (
        ("actual", "Observed", "#264653"),
        ("seasonal_naive", "Seasonal naive", "#b3551c"),
        ("lightgbm", "LightGBM", "#197457"),
    )
    cohorts = (("All observations", None), ("Fully in stock (24 h)", False), ("Stockout-affected (24 h)", True))
    for axis, (title, flag) in zip(figure.subplots(1, 3), cohorts):
        rows = [row for row in totals if flag is None or row["stockout_24h"] == flag]
        for metric, label, color in series:
            values = [sum(row[metric] for row in rows if row["target_date"] == day) for day in days]
            axis.plot([day[5:] for day in days], values, "o-", color=color, label=label)
        axis.set(title=title, xlabel="Target date (2024)")
        axis.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
        axis.grid(axis="y", alpha=.25)
    figure.axes[0].set_ylabel("Daily total (normalized retail sales units)")
    figure.axes[0].legend(loc="best", frameon=False)
    figure.suptitle("FreshRetailNet-50K | Official holdout observed sales and sealed forecasts")
    figure.savefig(output / "actual-vs-predicted.png", dpi=140)
