import os
import sys
from pathlib import Path

# Ensure project root is on sys.path to import 'app'
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from decimal import Decimal
from datetime import datetime

from app.database import SessionLocal, create_tables
from app.database import Client, Product, StockMovement

def upsert_client(db, name, **kwargs):
    obj = db.query(Client).filter(Client.name == name).first()
    if not obj:
        obj = Client(name=name, **kwargs)
        db.add(obj)
    return obj

def upsert_product(db, name, **kwargs):
    obj = db.query(Product).filter(Product.name == name).first()
    if not obj:
        obj = Product(name=name, **kwargs)
        db.add(obj)
        db.flush()
    return obj

def ensure_stock_in(db, product: Product, qty: int, unit_price: Decimal):
    if qty > 0:
        db.add(StockMovement(
            product_id=product.product_id,
            quantity=qty,
            movement_type="IN",
            reference_type="SEED",
            unit_price=unit_price,
            notes="Seed demo"
        ))
        product.quantity = (product.quantity or 0) + qty


def run():
    # Ensure tables exist
    create_tables()
    db = SessionLocal()
    try:
        # Clients
        upsert_client(db, "Nitek Client A", contact="Alice", email="client.a@nitek.example", phone="+221 77 100 00 01", address="Dakar", city="Dakar", country="Sénégal")
        upsert_client(db, "Nitek Client B", contact="Brahim", email="client.b@nitek.example", phone="+221 77 100 00 02", address="Thies", city="Thiès", country="Sénégal")
        upsert_client(db, "Nitek Client C", contact="Carine", email="client.c@nitek.example", phone="+221 77 100 00 03", address="Saint-Louis", city="Saint-Louis", country="Sénégal")

        # Products
        p1 = upsert_product(db, "Nitek Phone A", description="Smartphone 128Go", quantity=0, price=Decimal("150000.00"), purchase_price=Decimal("120000.00"), category="Smartphones", brand="Nitek", model="A1", barcode="NITEK-A1-0001")
        p2 = upsert_product(db, "Nitek Phone B", description="Smartphone 256Go", quantity=0, price=Decimal("220000.00"), purchase_price=Decimal("180000.00"), category="Smartphones", brand="Nitek", model="B2", barcode="NITEK-B2-0001")
        p3 = upsert_product(db, "Nitek Accessory A", description="Chargeur rapide 30W", quantity=0, price=Decimal("15000.00"), purchase_price=Decimal("10000.00"), category="Accessoires", brand="Nitek", model="C30", barcode="NITEK-C30-0001")

        # Stock IN
        ensure_stock_in(db, p1, 5, Decimal("120000.00"))
        ensure_stock_in(db, p2, 3, Decimal("180000.00"))
        ensure_stock_in(db, p3, 25, Decimal("10000.00"))

        db.commit()
        print("✅ Seed demo completed.")
    except Exception as e:
        db.rollback()
        print(f"❌ Seed demo failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
