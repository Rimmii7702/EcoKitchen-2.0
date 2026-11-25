"""Read-only inventory rules for recommendations and hypothetical consumption."""

from collections import defaultdict
from datetime import timedelta

from inventory_db import Ingredient, InventoryLot
from inventory_service import ZERO, active_ingredients, active_lots, ingredient_data, lot_data
from recipe_inventory import MAX_RECIPE_INGREDIENTS, NEAR_EXPIRY_DAYS


def stock_lots(ingredient_id=None):
    query = active_lots().where(InventoryLot.quantity > 0)
    if ingredient_id is not None:
        query = query.where(InventoryLot.ingredient_id == ingredient_id)
    return query.order_by(InventoryLot.expiry_date, InventoryLot.id)


def available_lots(lots, today):
    return [lot for lot in lots if lot.expiry_date is not None and lot.expiry_date >= today]


def stock_quantity(lots):
    return sum((lot.quantity for lot in lots), ZERO)


def recorded_price(eligible):
    manual = [lot for lot in eligible if lot.import_key is None]
    priced = [lot for lot in manual if lot.cost_per_unit is not None and lot.cost_per_unit > ZERO]
    prices = {lot.cost_per_unit for lot in priced}
    if not manual:
        reason = "no_eligible_manual_lots"
    elif len(prices) > 1:
        reason = "conflicting_unit_costs"
    elif len(priced) != len(manual):
        reason = "missing_unit_costs"
    else:
        return next(iter(prices)), "recorded_manual_lots", manual
    return None, reason, manual


def estimate_cost(shortfall, eligible, assumed_cost=None):
    unit_cost, basis, evidence = recorded_price(eligible)
    if assumed_cost is not None:
        unit_cost, basis, evidence = assumed_cost, "simulation_assumption", []
    return {
        "basis": basis,
        "unit_cost": str(unit_cost) if unit_cost is not None else None,
        "estimated_cost": str(shortfall * unit_cost) if unit_cost is not None else None,
        "evidence": [
            {"lot_id": lot.id, "unit_cost": str(lot.cost_per_unit) if lot.cost_per_unit is not None else None}
            for lot in evidence
        ],
    }


def recommendations(session, today):
    by_ingredient = defaultdict(list)
    near_expiry, expiry_review, expired = [], [], []
    cutoff = today + timedelta(days=NEAR_EXPIRY_DAYS)
    for lot in session.scalars(stock_lots()):
        by_ingredient[lot.ingredient_id].append(lot)
        if lot.expiry_date is None:
            expiry_review.append(lot_data(lot, today))
        elif lot.expiry_date < today:
            expired.append(lot_data(lot, today))
        elif lot.expiry_date <= cutoff:
            near_expiry.append(lot_data(lot, today))

    ingredients, low_stock = [], []
    for ingredient in session.scalars(active_ingredients().order_by(Ingredient.name, Ingredient.id)):
        eligible = available_lots(by_ingredient[ingredient.id], today)
        stock = stock_quantity(eligible)
        item = {**ingredient_data(ingredient), "eligible_quantity": str(stock)}
        ingredients.append(item)
        if stock < ingredient.minimum_stock:
            shortfall = ingredient.minimum_stock - stock
            low_stock.append({
                **item,
                "replenishment_quantity": str(shortfall),
                "eligible_lots": [lot_data(lot, today) for lot in eligible],
                "cost": estimate_cost(shortfall, eligible),
            })

    return {
        "as_of": today.isoformat(), "near_expiry_days": NEAR_EXPIRY_DAYS,
        "near_expiry": near_expiry, "expiry_review": expiry_review, "expired": expired,
        "ingredients": ingredients, "low_stock": low_stock,
        "recipe_ingredient_ids": list(dict.fromkeys(lot["ingredient_id"] for lot in near_expiry))[:MAX_RECIPE_INGREDIENTS],
    }


def simulate(ingredient, eligible, today, consume, assumed_cost):
    stock = stock_quantity(eligible)
    remaining = consume
    allocations = []
    for lot in eligible:
        if remaining == ZERO:
            break
        allocated = min(remaining, lot.quantity)
        remaining -= allocated
        allocations.append({**lot_data(lot, today), "allocated_quantity": str(allocated)})
    projected = stock - (consume - remaining)
    shortfall = max(ingredient.minimum_stock - projected, ZERO)
    return {
        "simulation": True, "as_of": today.isoformat(), "ingredient": ingredient_data(ingredient),
        "eligible_quantity": str(stock), "projected_quantity": str(projected),
        "requested_consumption": str(consume), "unfulfilled_quantity": str(remaining),
        "allocations": allocations, "replenishment_quantity": str(shortfall),
        "cost": estimate_cost(shortfall, eligible, assumed_cost),
    }
