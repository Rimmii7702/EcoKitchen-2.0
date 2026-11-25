import pandas as pd

from data_loader import (
    get_expiring_today_tomorrow, get_expiring_within_two_weeks, get_fresh_fruits_vegetables,
    load_inventory_data, load_menu_data,
)
from runtime_config import InferenceError, parse_gemini_json, recipe_model


def parse_menu(text):
    menu = parse_gemini_json(text)
    if not menu or any(
        not isinstance(dish, dict)
        or not isinstance(dish.get("name"), str)
        or not isinstance(dish.get("description"), str)
        or not isinstance(dish.get("ingredients"), list)
        or not all(isinstance(ingredient, str) for ingredient in dish["ingredients"])
        for dish in menu.values()
    ):
        raise InferenceError("Gemini did not return a usable menu.")
    return menu


class RecipeRecommender:
    def __init__(self, api_key=None):
        self.model = recipe_model(api_key)

    def generate_todays_special(self, inventory_df, menu_df, *, available=None):
        """Generate today's special menu (3 dishes) using Gemini API"""
        expiring_ingredients = get_expiring_today_tomorrow(inventory_df)

        fresh_produce = get_fresh_fruits_vegetables(inventory_df)

        priority_ingredients = pd.concat([expiring_ingredients, fresh_produce]).drop_duplicates()

        menu_items = menu_df['Name'].tolist()

        ingredients_list = priority_ingredients['Name'].tolist()

        prompt = f"""
        You're an AI chef assistant. Suggest 3 Indian dishes for today's special menu based on the following ingredients:

        Ingredients that should be used: {', '.join(ingredients_list)}
        Current menu items: {', '.join(menu_items[:30])}

        Please:
        1. Pick 2 dishes from the existing menu using many of the listed ingredients
        2. Create 1 new innovative Indian dish using those ingredients

        ⚠️ Output ONLY JSON. No explanation or comments. Format:
        {{
          "menu_item_1": {{
            "name": "...",
            "ingredients": [...],
            "description": "...",
            "recipe" : "..."
          }},
          "menu_item_2": {{
            "name": "...",
            "ingredients": [...],
            "description": "...",
            "recipe" : "..."
          }},
          "new_dish": {{
            "name": "...",
            "ingredients": [...],
            "description": "...",
            "recipe" : "..."
          }}
        }}

        The recipe should be a simple, step-by-step cooking guide. Give a proper recipe step-by-step with the quantity of the ingredients used.
        """
        if available is not None:
            prompt += f"\nAvailable SQLite inventory: {available}. Prioritize expiring lots when present. Do not describe unlisted ingredients as on hand."

        return parse_menu(self.model.generate_content(prompt).text)


    def generate_two_week_menu(self, inventory_df, menu_df, *, available=None):
        """Generate menu for next 2 weeks (5-7 dishes) using Gemini API"""
        expiring_soon = get_expiring_within_two_weeks(inventory_df)

        menu_items = menu_df['Name'].tolist()

        ingredients_list = expiring_soon['Name'].tolist()

        prompt = f"""
        I need recommendations for 5-7 Indian dishes to include in our menu for the next two weeks.
        These dishes should use ingredients that will expire within the next 15 days.

        Available ingredients that will expire soon: {', '.join(ingredients_list[:30])}... and more

        Restaurant's existing menu items: {', '.join(menu_items[:30])}... and more

        Please recommend 5-7 dishes that would make good use of these ingredients.
        For each dish, provide:
        - Name of the dish
        - Key ingredients from the list that would be used
        - A brief description (1-2 sentences)

        Format the response as a JSON with keys: "dish_1", "dish_2", etc., where each has "name", "ingredients", "description" as sub-keys.
        """
        if available is not None:
            prompt += f"\nAvailable SQLite inventory: {available}. When there are no near-expiry ingredients, use the available stock. Do not describe unlisted ingredients as on hand."

        return parse_menu(self.model.generate_content(prompt).text)


def main():
    recommender = RecipeRecommender()

    inventory_df = load_inventory_data()
    menu_df = load_menu_data()

    todays_special = recommender.generate_todays_special(inventory_df, menu_df)
    two_week_menu = recommender.generate_two_week_menu(inventory_df, menu_df)

    print("Today's Special:")
    for key, dish in todays_special.items():
        print(f"- {dish['name']}")
        print(f"  Ingredients: {', '.join(dish['ingredients'])}")
        print(f"  Description: {dish['description']}")

    print("\nTwo Week Menu:")
    for key, dish in two_week_menu.items():
        print(f"- {dish['name']}")
        print(f"  Ingredients: {', '.join(dish['ingredients'])}")
        print(f"  Description: {dish['description']}")

if __name__ == "__main__":
    main()
