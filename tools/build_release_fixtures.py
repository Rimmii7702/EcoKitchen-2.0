"""Generate the public candidate's fictional CSV and saved-menu fixtures."""
import argparse
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"

FILES = {
    "fictional-ingredients.csv": (
        "Name,Catagory,Quantity,Unit,Cost per Unit,Expiry date,Minimum stock level,No of days after restock.\n"
        "Demo lentils,Pantry,2.500,kg,2.40,2028-01-02,3,2\n"
        "Demo lentils,Pantry,3.000,kg,2.40,2028-01-05,7,5\n"
        "Demo mint,Produce,1.250,kg,,2028-02-30,2,1\n"
    ),
    "fictional-recipes.csv": (
        "Name,Category,Preparation Time,Cooking Time,Selling Price\n"
        "Demo lentil soup,Main,10,20,8.50\nDemo herb rice,Side,5,15,4.50\n"
    ),
    "fictional-ingredient-catalog.csv": (
        "Catagory,Product_Name\nPantry,Demo lentils\nProduce,Demo mint\nPantry,Demo rice\n"
    ),
    "fictional-weekly-sales.csv": (
        "Week,Dish Name,Quantity Sold\n"
        "01-Jan-2024 to 03-Jan-2024,Demo herb rice,9\n"
        "08-Jan-2024 to 14-Jan-2024,Demo lentil soup,16\n"
        "08-Jan-2024 to 14-Jan-2024,Demo herb rice,11\n"
    ),
}
FILES["fictional-menu.json"] = json.dumps({
    "date": "2024-01-08",
    "fictional_demo": True,
    "expiring_ingredients": [],
    "todays_special": {
        "demo_soup": {
            "name": "Fictional demo lentil soup",
            "description": "Independently written example for the saved menu view.",
            "ingredients": ["Demo lentils", "Water"],
            "recipe": "Rinse, then simmer the lentils in water until tender.",
        },
    },
    "two_week_menu": {
        "demo_rice": {
            "name": "Fictional demo herb rice",
            "description": "Independently written example for the two-week menu.",
            "ingredients": ["Demo rice", "Demo mint"],
            "recipe": "Cook the rice, then stir in chopped mint.",
        },
    },
}, ensure_ascii=False, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify bytes without changing files")
    args = parser.parse_args()
    if not args.check:
        DATA.mkdir(exist_ok=True)
    for name, content in FILES.items():
        path = DATA / name
        expected = content.encode("utf-8")
        if args.check:
            if path.read_bytes() != expected:
                raise SystemExit(f"Generated fixture differs: {name}")
        else:
            path.write_bytes(expected)


if __name__ == "__main__":
    main()
