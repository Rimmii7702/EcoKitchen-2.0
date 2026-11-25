import pandas as pd
from runtime_config import DATA_DIR

def load_inventory_data():
    """Load inventory data and preprocess it"""
    df = pd.read_csv(DATA_DIR / "fictional-ingredients.csv", sep=',')

    df.columns = df.columns.str.strip()

    if 'Expiry date' not in df.columns:
        raise KeyError("'Expiry date' column is missing or has a different name.")

    df['Expiry date'] = pd.to_datetime(df['Expiry date'], errors='coerce')

    today = pd.Timestamp.today().normalize()
    df['Days until expiry'] = (df['Expiry date'] - today).dt.days

    return df

def load_menu_data():
    """Load menu data"""
    df = pd.read_csv(DATA_DIR / "fictional-recipes.csv", sep=',')
    df.columns = df.columns.str.strip()
    return df

def get_expiring_today_tomorrow(inventory_df):
    """Get ingredients with verified expiry within the existing two-day window."""
    return inventory_df[inventory_df['Days until expiry'].between(0, 2)].sort_values('Days until expiry')

def get_expiring_within_two_weeks(inventory_df):
    """Get ingredients expiring within 3-15 days"""
    return inventory_df[
        (inventory_df['Days until expiry'] >= 3) &
        (inventory_df['Days until expiry'] <= 15)
    ].sort_values('Days until expiry')

def get_fresh_fruits_vegetables(inventory_df):
    """Get fruits and vegetables that should be used soon (within 8-10 days after restock)"""
    return inventory_df[
        (inventory_df['Catagory'] == 'Fruits & Vegetables') &
        (inventory_df['Days until expiry'] >= 0) &
        (inventory_df['No of days after restock.'] >= 8) &
        (inventory_df['No of days after restock.'] <= 10)
    ]

def main():
    inventory_df = load_inventory_data()
    expiring_soon = get_expiring_today_tomorrow(inventory_df)
    expiring_two_weeks = get_expiring_within_two_weeks(inventory_df)
    fresh_produce = get_fresh_fruits_vegetables(inventory_df)

    print(f"Total ingredients: {len(inventory_df)}")
    print(f"Ingredients expiring today/tomorrow: {len(expiring_soon)}")
    print(f"Ingredients expiring within 2 weeks: {len(expiring_two_weeks)}")
    print(f"Fresh produce to use: {len(fresh_produce)}")

if __name__ == "__main__":
    main()
