import logging

import pandas as pd

from runtime_config import DATA_DIR, InferenceError, parse_gemini_json, recipe_model


CSV_FILE = DATA_DIR / 'fictional-ingredient-catalog.csv'


def load_ingredients_by_category():
    try:
        df = pd.read_csv(CSV_FILE)
        df.columns = df.columns.str.strip()
        df = df.dropna(subset=['Catagory', 'Product_Name'])

        ingredients_by_category = {}

        for _, row in df.iterrows():
            cat = row['Catagory'].strip()
            item = row['Product_Name'].strip()

            if cat not in ingredients_by_category:
                ingredients_by_category[cat] = []
            if item not in ingredients_by_category[cat]:
                ingredients_by_category[cat].append(item)

        return ingredients_by_category

    except (OSError, ValueError, KeyError):
        logging.exception("Could not load the ingredient catalog")
        return {}

def generate_recipe(ingredients, *, structured=False, model=None):
    model = model or recipe_model()
    prompt = f"""Create a delicious and easy-to-make Indian recipe using some or all of these ingredients: {', '.join(ingredients)}.

    Please ensure the recipe is something that would be familiar and appealing to an Indian audience.

    Format the recipe as follows:

    1.  A creative and descriptive Indian-style recipe name.
    2.  A list of ingredients with measurements (using common Indian units where applicable, e.g., "cups," "tablespoons," "teaspoons," "grams," or "numbers of items").
    3.  Clear, step-by-step cooking instructions written in a way that's easy for an Indian cook to follow.
    4.  An estimated cooking time.
    5.  A recommended serving size.
    """

    if not structured:
        return model.generate_content(prompt).text

    prompt += """
    Use the provided inventory where possible. Identify additional ingredients explicitly if needed.
    Return only JSON with a name, ingredients (list of strings with quantities), steps (ordered list
    of strings), and waste_reduction_tips (list of practical suggestions without savings claims).
    Include estimated_cooking_time and servings as strings when appropriate.
    """
    result = parse_gemini_json(model.generate_content(prompt).text)
    if not isinstance(result.get("name"), str) or not result["name"].strip():
        raise InferenceError("Gemini did not return a usable recipe name.")
    for field in ("ingredients", "steps", "waste_reduction_tips"):
        values = result.get(field, [] if field == "waste_reduction_tips" else None)
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
            raise InferenceError("Gemini did not return usable recipe lists.")
        if field != "waste_reduction_tips" and not values:
            raise InferenceError("Gemini did not return ingredients and preparation steps.")
    fields = ("name", "ingredients", "steps", "waste_reduction_tips", "estimated_cooking_time", "servings")
    return {field: result[field] for field in fields if field in result and (
        field not in {"estimated_cooking_time", "servings"} or isinstance(result[field], str)
    )}
