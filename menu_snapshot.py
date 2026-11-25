"""Project a saved menu for dashboard display."""

from datetime import date, datetime


def snapshot_date(value, parse):
    if isinstance(value, str):
        try:
            return parse(value).isoformat()
        except ValueError:
            return None
    return None


def public_snapshot(snapshot):
    if not snapshot:
        return None
    result = {"date": snapshot_date(snapshot.get("date"), date.fromisoformat)}
    if snapshot.get("fictional_demo") is True:
        result["fictional_demo"] = True
    generated = snapshot_date(snapshot.get("generated_at"), datetime.fromisoformat)
    if generated:
        result["generated_at"] = generated
    for group in ("todays_special", "two_week_menu"):
        dishes = snapshot.get(group)
        result[group] = {}
        if isinstance(dishes, dict):
            for key, dish in dishes.items():
                if not isinstance(dish, dict) or not isinstance(dish.get("name"), str):
                    continue
                result[group][key] = {
                    field: dish[field] for field in ("name", "description", "recipe") if isinstance(dish.get(field), str)
                }
                ingredients = dish.get("ingredients")
                if isinstance(ingredients, list):
                    result[group][key]["ingredients"] = [item for item in ingredients if isinstance(item, str)]
    return result if result["todays_special"] or result["two_week_menu"] else None
