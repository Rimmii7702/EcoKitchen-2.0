"""Score observed sales, retaining distinct 24-hour and daytime stockout strata."""

from math import fsum, sqrt


COHORTS = {
    "all": lambda full_day, daytime: True,
    "in_stock_24h": lambda full_day, daytime: not full_day,
    "stockout_24h": lambda full_day, daytime: full_day,
    "in_stock_daytime": lambda full_day, daytime: not daytime,
    "stockout_daytime": lambda full_day, daytime: daytime,
}


def check_keys(connection, expected):
    counts = connection.execute("""
        WITH keys AS (
            SELECT store_id, product_id, target_date, 1 AS source FROM model_predictions WHERE prediction IS NOT NULL
            UNION ALL
            SELECT store_id, product_id, target_date, 2 FROM baseline_predictions WHERE prediction IS NOT NULL
            UNION ALL
            SELECT store_id, product_id, target_date, 3 FROM scoring_labels WHERE actual IS NOT NULL
        ), occurrences AS (
            SELECT count(*) AS n, count(DISTINCT source) AS sources FROM keys GROUP BY store_id, product_id, target_date
        )
        SELECT count(*), count(*) FILTER (WHERE n <> 3 OR sources <> 3) FROM occurrences
        """).fetchall()[0]
    if counts != (expected, 0):
        raise ValueError("The observed labels, baseline, and LightGBM must use identical, unique scoring keys.")


def score_totals(connection, origin, predictions="baseline_predictions"):
    if predictions not in {"baseline_predictions", "model_predictions"}:
        raise ValueError("Unknown forecast source.")
    return connection.execute(f"""
        WITH matched AS (
            SELECT date_diff('day', $origin, l.target_date) AS horizon,
                   l.stockout_24h, l.stockout_daytime, l.actual, p.prediction - l.actual AS error
            FROM scoring_labels l
            LEFT JOIN {predictions} p USING (store_id, product_id, target_date)
        )
        SELECT horizon, stockout_24h, stockout_daytime,
               count(*) AS observations, count(error) AS scored,
               sum(abs(error)), sum(error * error), sum(error),
               sum(actual) FILTER (WHERE error IS NOT NULL)
        FROM matched
        GROUP BY ALL ORDER BY horizon, stockout_24h, stockout_daytime
        """, {"origin": origin}).fetchall()


def summarize(totals):
    observations = sum(row[0] for row in totals)
    scored = sum(row[1] for row in totals)
    absolute, squared, signed, actual = (
        fsum(row[index] or 0 for row in totals) for index in range(2, 6)
    )

    return {
        "observations": observations,
        "scored": scored,
        "unscored": observations - scored,
        "mae": round(absolute / scored, 10) if scored else None,
        "rmse": round(sqrt(squared / scored), 10) if scored else None,
        "wape_pct": round(100 * absolute / actual, 10) if actual > 0 else None,
        "signed_bias_pct": round(100 * signed / actual, 10) if actual > 0 else None,
    }


def scorecard(rows):
    def cohorts(rows):
        return {name: summarize([row[3:] for row in rows if matches(row[1], row[2])])
                for name, matches in COHORTS.items()}

    return {
        "overall": cohorts(rows),
        "by_horizon": {str(horizon): cohorts([row for row in rows if row[0] == horizon])
                       for horizon in range(1, 8)},
    }
