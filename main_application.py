import json
import os
import tempfile
from datetime import datetime

from data_loader import (
    get_expiring_today_tomorrow, get_expiring_within_two_weeks, load_inventory_data, load_menu_data,
)
from recipe_generator import RecipeRecommender
from runtime_config import DATA_DIR, project_path, recommendations_path


def read_recommendations(path=None):
    path = recommendations_path() if path is None else project_path(path)
    if path == recommendations_path() and not os.getenv("RECOMMENDATIONS_FILE") and not path.exists():
        path = DATA_DIR / "fictional-menu.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_recommendations(path, recommendations):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary_path = project_path(file.name)
    try:
        temporary_path.write_text(
            json.dumps(recommendations, indent=4, allow_nan=False), encoding="utf-8"
        )
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


class RecipeRecommendationSystem:
    def __init__(self):
        self.inventory_df = None
        self.menu_df = None
        self.today = datetime.now().date()

    def load_data(self, inventory_df=None):
        self.inventory_df = load_inventory_data() if inventory_df is None else inventory_df

        self.menu_df = load_menu_data()

        return True

    def get_expiring_ingredients(self):
        """Get ingredients expiring today or tomorrow"""
        if self.inventory_df is None:
            raise ValueError("Data not loaded. Call load_data first.")

        expiring_soon = get_expiring_today_tomorrow(self.inventory_df)
        return expiring_soon

    def get_two_week_expiring(self):
        """Get ingredients expiring within two weeks"""
        if self.inventory_df is None:
            raise ValueError("Data not loaded. Call load_data first.")

        expiring_soon = get_expiring_within_two_weeks(self.inventory_df)
        return expiring_soon

    def get_recommendations(self, *, recommender=None, available=None):
        """Get all recommendations"""
        if self.inventory_df is None or self.menu_df is None:
            raise ValueError("Data not loaded. Call load_data first.")

        expiring_ingredients = self.get_expiring_ingredients()

        recommender = recommender or RecipeRecommender()
        options = {} if available is None else {"available": available}
        todays_special = recommender.generate_todays_special(self.inventory_df, self.menu_df, **options)
        two_week_menu = recommender.generate_two_week_menu(self.inventory_df, self.menu_df, **options)

        result = {
            "date": self.today.strftime("%Y-%m-%d"),
            "expiring_ingredients": json.loads(expiring_ingredients.to_json(orient="records", date_format="iso")),
            "todays_special": todays_special,
            "two_week_menu": two_week_menu
        }

        return result

    def save_recommendations(self, filename=None):
        """Generate once, then replace the saved snapshot after a successful write."""
        path = recommendations_path() if filename is None else project_path(filename)
        write_recommendations(path, self.get_recommendations())
        return path
