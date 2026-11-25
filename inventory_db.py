"""Inventory schema and database lifecycle."""

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text, URL, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class InventoryNotInitialized(Exception):
    """The configured database needs the inventory schema."""


class Base(DeclarativeBase):
    pass


class Ingredient(Base):
    __tablename__ = "ingredients"
    __table_args__ = (
        CheckConstraint("minimum_stock >= 0", name="ck_ingredients_minimum_stock"),
        CheckConstraint(
            "(archived_at IS NULL AND identity_key IS NOT NULL) OR "
            "(archived_at IS NOT NULL AND identity_key IS NULL)",
            name="ck_ingredients_active_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(40))
    minimum_stock: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    identity_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InventoryLot(Base):
    __tablename__ = "inventory_lots"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_inventory_lots_quantity"),
        CheckConstraint("cost_per_unit IS NULL OR cost_per_unit >= 0", name="ck_inventory_lots_cost"),
        CheckConstraint(
            "days_after_restock IS NULL OR days_after_restock >= 0",
            name="ck_inventory_lots_restock_days",
        ),
        CheckConstraint(
            "archived_at IS NULL OR quantity = 0",
            name="ck_inventory_lots_archived_quantity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), index=True)
    ingredient: Mapped[Ingredient] = relationship()
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    expiry_date: Mapped[date | None] = mapped_column(Date, index=True)
    source_expiry_text: Mapped[str | None] = mapped_column(Text)
    cost_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    days_after_restock: Mapped[int | None]
    import_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StockTransaction(Base):
    __tablename__ = "stock_transactions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('created', 'imported', 'updated', 'archived')",
            name="ck_stock_transactions_kind",
        ),
        CheckConstraint(
            "quantity_before >= 0 AND quantity_after >= 0",
            name="ck_stock_transactions_quantities",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("inventory_lots.id"), index=True)
    lot: Mapped[InventoryLot] = relationship()
    kind: Mapped[str] = mapped_column(String(12))
    quantity_before: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    quantity_after: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    expiry_before: Mapped[date | None] = mapped_column(Date)
    expiry_after: Mapped[date | None] = mapped_column(Date)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InventoryDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.engine = create_engine(URL.create("sqlite", database=str(path)))
        event.listen(self.engine, "connect", self._configure_sqlite)
        event.listen(self.engine, "begin", self._begin_sqlite)
        self.writer = self.engine.execution_options(inventory_write=True)

    @staticmethod
    def _configure_sqlite(connection, _):
        connection.isolation_level = None
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    @staticmethod
    def _begin_sqlite(connection):
        # Reserve the SQLite write lock before reading balances or matching import keys.
        statement = "BEGIN IMMEDIATE" if connection.get_execution_options().get("inventory_write") else "BEGIN"
        connection.exec_driver_sql(statement)

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(self.writer)

    @contextmanager
    def session(self, *, write=False):
        if not self.path.is_file():
            raise InventoryNotInitialized
        with Session(self.writer if write else self.engine) as session, session.begin():
            if not inspect(session.connection()).has_table("stock_transactions"):
                raise InventoryNotInitialized
            yield session
