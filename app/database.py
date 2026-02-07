"""
Database module — MongoDB via Beanie ODM.

Provides:
  - init_db()          : connect to MongoDB and initialise Beanie
  - get_next_id(col)   : auto-increment helper (replaces SQLite rowid)
  - All document models re-exported for backward-compatible imports
"""

import os
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from dotenv import load_dotenv

load_dotenv()

# ── Connection URL ──────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mongodb://localhost:27017/geektech",
)

# ── Motor client (lazy, created once) ──────────────────────────
_client: AsyncIOMotorClient = None
_db = None


def get_motor_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(DATABASE_URL)
    return _client


def get_database():
    """Return the Motor database object."""
    global _db
    if _db is None:
        client = get_motor_client()
        # Extract DB name from URL, fallback to 'geektech'
        db_name = DATABASE_URL.rsplit("/", 1)[-1].split("?")[0] or "geektech"
        _db = client[db_name]
    return _db


# ── Re-export all document models ──────────────────────────────
# This allows existing code to keep doing:
#   from app.database import Client, Invoice, ...
from app.models.documents import (
    User,
    Client,
    ClientDebt,
    ClientDebtPayment,
    DailyPurchase,
    DailyPurchaseCategory,
    Category,
    CategoryAttribute,
    CategoryAttributeValue,
    Product,
    ProductSerialNumber,
    ProductVariant,
    ProductVariantAttribute,
    StockMovement,
    BankTransaction,
    Supplier,
    PurchaseOrder,
    PurchaseOrderItem,
    SupplierDebt,
    SupplierDebtPayment,
    SupplierInvoice,
    SupplierInvoicePayment,
    Quotation,
    QuotationItem,
    Invoice,
    InvoiceItem,
    InvoicePayment,
    DeliveryNote,
    DeliveryNoteItem,
    UserSettings,
    ScanHistory,
    AppCache,
    DailyClientRequest,
    DailySale,
    Migration,
    MigrationLog,
    Maintenance,
    ALL_DOCUMENT_MODELS,
)


# ── Beanie init (called at FastAPI startup) ────────────────────
async def init_db():
    """Initialise Motor + Beanie. Call once at app startup."""
    database = get_database()
    await init_beanie(database=database, document_models=ALL_DOCUMENT_MODELS)
    print(f"✅ MongoDB connecté : {DATABASE_URL}")


# ── Auto-increment helper ──────────────────────────────────────
async def get_next_id(collection_name: str) -> int:
    """
    Atomically increment and return a sequence counter.
    Uses a 'counters' collection with documents like:
      { "_id": "clients", "seq": 721 }
    """
    db = get_database()
    result = await db.counters.find_one_and_update(
        {"_id": collection_name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,  # return *after* update
    )
    return result["seq"]


# ── Legacy compatibility stubs ─────────────────────────────────
# Some old code references get_db / SessionLocal. Provide no-ops
# so imports don't break during transition. These should NOT be used.
def get_db():
    """Legacy stub — not used with MongoDB. Raises if called."""
    raise RuntimeError(
        "get_db() is a SQLAlchemy legacy stub. "
        "Use Beanie document methods directly (await Model.find(...), etc.)."
    )

SessionLocal = None  # Legacy stub for services that import it
