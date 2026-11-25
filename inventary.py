import logging

import pandas as pd

from runtime_config import DATA_DIR


def load_inventory_data():
    try:
        df = pd.read_csv(DATA_DIR / "fictional-ingredients.csv")
        df['Expiry date'] = pd.to_datetime(df['Expiry date'], errors='coerce')
        df['Days until expiry'] = (df['Expiry date'] - pd.Timestamp.now()).dt.days

        return df.to_dict(orient='records')
    except (OSError, ValueError, KeyError):
        logging.exception("Could not load inventory data")
        return []
