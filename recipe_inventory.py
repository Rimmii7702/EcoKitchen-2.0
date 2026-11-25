"""SQLite inventory inputs for the dashboard's Gemini workflows."""

from datetime import date, timedelta

from inventory_context import database
from inventory_db import InventoryLot
from inventory_service import active_lots, ingredient_data


NEAR_EXPIRY_DAYS = 7
MAX_RECIPE_INGREDIENTS = 20


def inventory_lots(today):
    with database().session() as session:
        query = active_lots().where(InventoryLot.quantity > 0, InventoryLot.expiry_date >= today)
        lots = list(session.scalars(query.order_by(InventoryLot.id)))
        session.expunge_all()  # Retain the eager-loaded values after the read-only session closes.
        return lots


def recipe_inputs(lots, today):
    groups = {}
    for lot in lots:
        item = groups.setdefault(lot.ingredient_id, {
            **ingredient_data(lot.ingredient), "available_quantity": 0,
            "earliest_expiry": None, "sources": set(),
        })
        item["available_quantity"] += lot.quantity
        item["sources"].add("csv_import" if lot.import_key is not None else "manual")
        if lot.expiry_date and (
            item["earliest_expiry"] is None or lot.expiry_date < item["earliest_expiry"]
        ):
            item["earliest_expiry"] = lot.expiry_date

    items = sorted(groups.values(), key=lambda row: (row["earliest_expiry"] or date.max, row["name"].casefold()))
    for item in items:
        expiry = item["earliest_expiry"]
        item["near_expiry"] = expiry is not None and expiry <= today + timedelta(days=NEAR_EXPIRY_DAYS)
        item["earliest_expiry"] = expiry.isoformat() if expiry else None
        item["available_quantity"] = str(item["available_quantity"])
        item["sources"] = sorted(item["sources"])
    return items


def describe_inputs(items):
    return [
        f"{item['name']} ({item['available_quantity']} {item['unit']} available"
        + (f"; earliest expiry {item['earliest_expiry']}" if item["earliest_expiry"] else "")
        + ("; prioritize near-expiry stock" if item["near_expiry"] else "")
        + ")"
        for item in items
    ]


def menu_inventory_frame(lots, today):
    import pandas as pd

    frame = pd.DataFrame([{
        "Name": lot.ingredient.name,
        "Catagory": lot.ingredient.category,
        "Quantity": str(lot.quantity),
        "Unit": lot.ingredient.unit,
        "Expiry date": lot.expiry_date.isoformat() if lot.expiry_date else None,
        "Days until expiry": (lot.expiry_date - today).days if lot.expiry_date else None,
        "No of days after restock.": lot.days_after_restock,
    } for lot in lots])
    for field in ("Days until expiry", "No of days after restock."):
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    return frame
