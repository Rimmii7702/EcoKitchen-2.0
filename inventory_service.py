"""Inventory validation, stock operations, and the restaurant CSV import."""

import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import joinedload

from inventory_db import Ingredient, InventoryLot, StockTransaction


ZERO = Decimal("0.000")
SCALE = Decimal("0.001")
LIMIT = Decimal("1000000000")
ID_LIMIT = 2**63 - 1
TEXT_LIMITS = {"name": 150, "category": 100, "unit": 40}
INGREDIENT_FIELDS = {*TEXT_LIMITS, "minimum_stock"}
LOT_FIELDS = {"quantity", "expiry_date", "cost_per_unit", "days_after_restock"}
CSV_COLUMNS = {
    "Name", "Catagory", "Quantity", "Unit", "Cost per Unit", "Expiry date",
    "Minimum stock level", "No of days after restock.",
}


class InventoryError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def whole_number(value, field, minimum, maximum, *, allow_text=False):
    if allow_text and isinstance(value, str) and len(value) <= 19 and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        raise InventoryError(f"{field} must be an integer from {minimum} to {maximum}.")
    return value


def amount(value, field):
    error = f"{field} must be a nonnegative number below {LIMIT} with at most three decimal places."
    if type(value) not in (int, float, str) or len(str(value)) > 64:
        raise InventoryError(error)
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not ZERO <= number < LIMIT:
            raise InventoryError(error)
        rounded = number.quantize(SCALE)
        if rounded != number:
            raise InventoryError(error)
        return ZERO if number == ZERO else rounded
    except InvalidOperation:
        raise InventoryError(error) from None


def iso_date(value):
    try:
        result = date.fromisoformat(value)
        if result.isoformat() == value:
            return result
    except (ValueError, TypeError):
        pass
    raise InventoryError("expiry_date must be a valid YYYY-MM-DD date or null.")


def validate_fields(payload, fields, required=()):
    if not isinstance(payload, dict) or not payload:
        raise InventoryError("Send a nonempty JSON object.")
    unknown = payload.keys() - fields
    if unknown:
        raise InventoryError(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    missing = set(required) - payload.keys()
    if missing:
        raise InventoryError(f"Missing required field(s): {', '.join(sorted(missing))}.")

    cleaned = {}
    for field, value in payload.items():
        if field in TEXT_LIMITS:
            if not isinstance(value, str):
                raise InventoryError(f"{field} must be text.")
            value = " ".join(value.split())
            if not value or len(value) > TEXT_LIMITS[field] or not value.isprintable():
                raise InventoryError(f"{field} must contain 1 to {TEXT_LIMITS[field]} printable characters.")
        elif field == "expiry_date":
            value = None if value is None else iso_date(value)
        elif field == "ingredient_id":
            value = whole_number(value, field, 1, ID_LIMIT)
        elif field == "days_after_restock":
            value = None if value is None else whole_number(value, field, 0, 100000)
        elif value is not None or field != "cost_per_unit":
            value = amount(value, field)
        cleaned[field] = value
    return cleaned


def digest(values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode("utf-8")).hexdigest()


def identity(name, category, unit):
    return digest([name.casefold(), category.casefold(), unit.casefold()])


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()


def active_ingredients():
    return select(Ingredient).where(Ingredient.archived_at.is_(None))


def active_lots():
    return select(InventoryLot).where(InventoryLot.archived_at.is_(None)).options(joinedload(InventoryLot.ingredient))


def usable_expiry(today):
    return or_(and_(InventoryLot.expiry_date.is_(None), InventoryLot.source_expiry_text.is_(None)),
               InventoryLot.expiry_date >= today)


def require_active(session, model, item_id):
    item = session.get(model, item_id)
    if item is None or item.archived_at is not None:
        raise InventoryError("Inventory record not found.", 404)
    return item


def create_ingredient(session, values):
    key = identity(values["name"], values["category"], values["unit"])
    if session.scalar(select(Ingredient.id).where(Ingredient.identity_key == key)) is not None:
        raise InventoryError("An active ingredient already has this name, category, and unit.", 409)
    ingredient = Ingredient(minimum_stock=ZERO, identity_key=key)
    for field, value in values.items():
        setattr(ingredient, field, value)
    session.add(ingredient)
    session.flush()
    return ingredient


def update_ingredient(session, item, values):
    unit = values.get("unit", item.unit)
    if unit.casefold() != item.unit.casefold() and session.scalar(
        select(InventoryLot.id).where(InventoryLot.ingredient_id == item.id).limit(1)
    ) is not None:
        raise InventoryError("An ingredient with stock history must keep its unit. Create a new ingredient for the new unit.", 409)
    for field, value in values.items():
        setattr(item, field, value)
    item.identity_key = identity(item.name, item.category, item.unit)
    session.flush()
    return item


def archive_ingredient(session, item):
    if session.scalar(active_lots().where(InventoryLot.ingredient_id == item.id).limit(1)) is not None:
        raise InventoryError("Remove this ingredient's active lots before deleting it.", 409)
    item.archived_at = utc_now()
    item.identity_key = None


def record_stock_change(session, lot, kind, quantity_before, expiry_before):
    session.add(StockTransaction(
        lot=lot, kind=kind, quantity_before=quantity_before, quantity_after=lot.quantity,
        expiry_before=expiry_before, expiry_after=lot.expiry_date, occurred_at=utc_now(),
    ))


def create_lot(session, ingredient, values, *, import_key=None):
    lot = InventoryLot(ingredient=ingredient, import_key=import_key, **values)
    session.add(lot)
    record_stock_change(session, lot, "imported" if import_key else "created", ZERO, None)
    session.flush()
    return lot


def update_lot(session, lot, values):
    before = (lot.quantity, lot.expiry_date)
    for field, value in values.items():
        setattr(lot, field, value)
    if before != (lot.quantity, lot.expiry_date):
        record_stock_change(session, lot, "updated", *before)
    return lot


def archive_lot(session, lot):
    before = lot.quantity
    lot.quantity = ZERO
    lot.archived_at = utc_now()
    record_stock_change(session, lot, "archived", before, lot.expiry_date)


def ingredient_data(item):
    return {
        "id": item.id, "name": item.name, "category": item.category, "unit": item.unit,
        "minimum_stock": str(item.minimum_stock),
    }


def lot_data(lot, today):
    return {
        "id": lot.id, "ingredient": ingredient_data(lot.ingredient), "ingredient_id": lot.ingredient_id,
        "quantity": str(lot.quantity), "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
        "days_until_expiry": (lot.expiry_date - today).days if lot.expiry_date else None,
        "source_expiry_text": lot.source_expiry_text,
        "source": "csv_import" if lot.import_key is not None else "manual",
        "cost_per_unit": str(lot.cost_per_unit) if lot.cost_per_unit is not None else None,
        "days_after_restock": lot.days_after_restock,
    }


def transaction_data(transaction):
    return {
        "id": transaction.id, "lot_id": transaction.lot_id,
        "ingredient_id": transaction.lot.ingredient_id, "unit": transaction.lot.ingredient.unit,
        "ingredient_name": transaction.lot.ingredient.name,
        "kind": transaction.kind, "occurred_at": timestamp(transaction.occurred_at),
        "quantity_before": str(transaction.quantity_before), "quantity_after": str(transaction.quantity_after),
        "quantity_change": str(transaction.quantity_after - transaction.quantity_before),
        "expiry_before": transaction.expiry_before.isoformat() if transaction.expiry_before else None,
        "expiry_after": transaction.expiry_after.isoformat() if transaction.expiry_after else None,
        "source_expiry_text": transaction.lot.source_expiry_text,
    }


def alerts(session, today, days):
    near_expiry, expired = [], []
    dated = active_lots().where(
        InventoryLot.quantity > 0, InventoryLot.expiry_date <= today + timedelta(days=days)
    ).order_by(InventoryLot.expiry_date, InventoryLot.id)
    for lot in session.scalars(dated):
        target = expired if lot.expiry_date < today else near_expiry
        target.append(lot_data(lot, today))

    undated = active_lots().where(InventoryLot.quantity > 0, InventoryLot.expiry_date.is_(None))
    unknown_expiry = [lot_data(lot, today) for lot in session.scalars(undated.order_by(InventoryLot.id))]

    usable = func.coalesce(func.sum(InventoryLot.quantity), ZERO)
    low = select(Ingredient, usable).outerjoin(InventoryLot, and_(
        Ingredient.id == InventoryLot.ingredient_id, InventoryLot.archived_at.is_(None),
        usable_expiry(today),
    )).where(Ingredient.archived_at.is_(None)).group_by(Ingredient.id).order_by(Ingredient.id)
    low_stock = [
        {**ingredient_data(item), "available_quantity": str(available)}
        for item, available in session.execute(low)
        if available < item.minimum_stock
    ]
    return {
        "as_of": today.isoformat(), "days": days,
        "near_expiry": near_expiry, "expired": expired, "unknown_expiry": unknown_expiry, "low_stock": low_stock,
    }


def inventory_page_data(session, today):
    rows = []
    for lot in session.scalars(active_lots().order_by(InventoryLot.id)):
        rows.append({
            "Catagory": lot.ingredient.category, "Name": lot.ingredient.name,
            "Unit": lot.ingredient.unit, "Quantity": format(lot.quantity.normalize(), "f"),
            "Days until expiry": (lot.expiry_date - today).days if lot.expiry_date else None,
        })
    return rows


def read_import_rows(path):
    rows = {}
    invalid_expiries = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        headers = [header.strip() for header in (reader.fieldnames or [])]
        missing = CSV_COLUMNS - set(headers)
        if missing:
            raise InventoryError(f"Missing CSV column(s): {', '.join(sorted(missing))}.")
        if len(headers) != len(set(headers)):
            raise InventoryError("The CSV contains duplicate column names.")
        reader.fieldnames = headers
        duplicates = 0
        for raw in reader:
            try:
                if None in raw or any(raw[name] is None for name in CSV_COLUMNS):
                    raise InventoryError("Incorrect number of columns.")
                row = {name: raw[name].strip() for name in CSV_COLUMNS}
                ingredient = validate_fields({
                    "name": row["Name"], "category": row["Catagory"], "unit": row["Unit"],
                    "minimum_stock": row["Minimum stock level"],
                }, INGREDIENT_FIELDS)
                raw_expiry = row["Expiry date"] or None
                invalid_expiry = None
                try:
                    expiry = iso_date(raw_expiry) if raw_expiry else None
                except InventoryError:
                    expiry = None
                    invalid_expiry = raw["Expiry date"]
                    invalid_expiries.append((reader.line_num, invalid_expiry))
                lot = validate_fields({
                    "quantity": row["Quantity"],
                    "cost_per_unit": row["Cost per Unit"] or None,
                    "days_after_restock": whole_number(
                        row["No of days after restock."], "days_after_restock", 0, 100000, allow_text=True,
                    ) if row["No of days after restock."] else None,
                }, LOT_FIELDS)
                lot.update(expiry_date=expiry, source_expiry_text=invalid_expiry)
                key = identity(ingredient["name"], ingredient["category"], ingredient["unit"])
                import_key = digest([key, row["Expiry date"]])
                values = (key, ingredient, lot)
                if import_key in rows:
                    _, previous_ingredient, previous_lot = rows[import_key]
                    if lot != previous_lot or ingredient["minimum_stock"] != previous_ingredient["minimum_stock"]:
                        raise InventoryError("Conflicting stock rows for the same ingredient and expiry date.")
                    duplicates += 1
                else:
                    rows[import_key] = values
            except InventoryError as exc:
                raise InventoryError(f"CSV line {reader.line_num}: {exc}") from exc
    return rows, duplicates, invalid_expiries


def import_inventory(session, rows, duplicates):
    existing_keys = set(session.scalars(select(InventoryLot.import_key).where(InventoryLot.import_key.is_not(None))))
    ingredients = {item.identity_key: item for item in session.scalars(active_ingredients())}
    thresholds = {}
    for key, ingredient, _ in rows.values():
        thresholds[key] = max(thresholds.get(key, ZERO), ingredient["minimum_stock"])

    added = 0
    skipped = duplicates
    for import_key, (key, values, stock) in rows.items():
        if import_key in existing_keys:
            skipped += 1
            continue
        if key not in ingredients:
            ingredients[key] = create_ingredient(session, {**values, "minimum_stock": thresholds[key]})
        ingredient = ingredients[key]
        match_query = active_lots().where(
            InventoryLot.ingredient_id == ingredient.id, InventoryLot.expiry_date == stock["expiry_date"],
        )
        if stock["expiry_date"] is None:
            match_query = match_query.where(InventoryLot.source_expiry_text == stock["source_expiry_text"])
        matches = list(session.scalars(match_query))
        if matches:
            if len(matches) != 1 or matches[0].import_key is not None or any(
                getattr(matches[0], field) != value for field, value in stock.items()
            ):
                date_label = stock["expiry_date"] or "unknown expiry"
                raise InventoryError(f"Existing lots for {ingredient.name} ({date_label}) conflict with the CSV.", 409)
            matches[0].import_key = import_key
            skipped += 1
        else:
            create_lot(session, ingredient, stock, import_key=import_key)
            added += 1
    return added, skipped
