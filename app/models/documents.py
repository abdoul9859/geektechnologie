"""
Beanie ODM Document models for MongoDB.
Converted from SQLAlchemy models in app/database.py.

Each class maps to a MongoDB collection. Sub-documents (items, payments)
are embedded where appropriate for performance; top-level entities
(clients, products, users) are referenced by ID.
"""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Annotated
from beanie import Document, Indexed
from bson.decimal128 import Decimal128
from pydantic import Field, BeforeValidator


def _dec(v):
    """Convert Decimal128 / any to Python Decimal."""
    if isinstance(v, Decimal128):
        return v.to_decimal()
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


Dec = Annotated[Decimal, BeforeValidator(_dec)]


# ──────────────────── User ────────────────────

class User(Document):
    user_id: Indexed(int, unique=True)  # legacy PK
    username: Indexed(str, unique=True)
    email: Indexed(str, unique=True)
    password_hash: str
    full_name: Optional[str] = None
    role: str = "user"
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None

    class Settings:
        name = "users"


# ──────────────────── Client ────────────────────

class Client(Document):
    client_id: Indexed(int, unique=True)
    name: str
    contact: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = "Sénégal"
    tax_number: Optional[str] = None
    notes: Optional[str] = None
    disable_debt_reminder: bool = False

    class Settings:
        name = "clients"


# ──────────────────── Client Debts ────────────────────

class ClientDebtPayment(Document):
    payment_id: Indexed(int, unique=True)
    debt_id: int
    amount: Dec
    payment_date: datetime = Field(default_factory=datetime.utcnow)
    payment_method: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "client_debt_payments"


class ClientDebt(Document):
    debt_id: Indexed(int, unique=True)
    client_id: Optional[int] = None
    reference: str
    date: datetime = Field(default_factory=datetime.utcnow)
    due_date: Optional[datetime] = None
    amount: Dec
    paid_amount: Dec = Decimal("0")
    remaining_amount: Dec = Decimal("0")
    status: str = "pending"
    description: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "client_debts"


# ──────────────────── Daily Purchases ────────────────────

class DailyPurchaseCategory(Document):
    category_pk: Indexed(int, unique=True)
    name: Indexed(str, unique=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "daily_purchase_categories"


class DailyPurchase(Document):
    purchase_id: Indexed(int, unique=True)
    date: date
    category: Indexed(str)
    supplier: Optional[str] = None
    description: Optional[str] = None
    amount: Dec
    payment_method: str = "espece"
    reference: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[int] = None

    class Settings:
        name = "daily_purchases"


# ──────────────────── Categories ────────────────────

class CategoryAttributeValue(Document):
    value_id: Indexed(int, unique=True)
    attribute_id: int
    value: str
    code: Optional[str] = None
    sort_order: int = 0

    class Settings:
        name = "category_attribute_values"


class CategoryAttribute(Document):
    attribute_id: Indexed(int, unique=True)
    category_id: int
    name: str
    code: Optional[str] = None
    type: str = "select"
    required: bool = False
    multi_select: bool = False
    sort_order: int = 0

    class Settings:
        name = "category_attributes"


class Category(Document):
    category_id: Indexed(int, unique=True)
    name: Indexed(str, unique=True)
    description: Optional[str] = None
    requires_variants: bool = False

    class Settings:
        name = "categories"


# ──────────────────── Products ────────────────────

class ProductVariantAttribute(Document):
    attribute_id: Indexed(int, unique=True)
    variant_id: int
    attribute_name: str
    attribute_value: str

    class Settings:
        name = "product_variant_attributes"


class ProductVariant(Document):
    variant_id: Indexed(int, unique=True)
    product_id: int
    imei_serial: Indexed(str, unique=True)
    barcode: Optional[str] = None
    condition: Optional[str] = None
    is_sold: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    attributes: Optional[list] = []

    class Settings:
        name = "product_variants"


class ProductSerialNumber(Document):
    serial_number_id: Indexed(int, unique=True)
    product_id: int
    serial_number: str
    barcode: Optional[str] = None
    is_available: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "product_serial_numbers"


class Product(Document):
    product_id: Indexed(int, unique=True)
    name: str
    description: Optional[str] = None
    quantity: int = 0
    price: Dec
    wholesale_price: Optional[Dec] = None
    purchase_price: Dec = Decimal("0")
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    barcode: Optional[str] = None
    condition: Optional[str] = "neuf"
    has_unique_serial: bool = False
    entry_date: Optional[datetime] = None
    notes: Optional[str] = None
    image_path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_archived: bool = False
    variants: Optional[list] = []

    class Settings:
        name = "products"


# ──────────────────── Stock Movements ────────────────────

class StockMovement(Document):
    movement_id: Indexed(int, unique=True)
    product_id: int
    quantity: int
    movement_type: str  # IN, OUT
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
    notes: Optional[str] = None
    unit_price: Dec = Decimal("0")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "stock_movements"


# ──────────────────── Bank Transactions ────────────────────

class BankTransaction(Document):
    transaction_id: Indexed(int, unique=True)
    type: str  # entry, exit
    motif: str
    description: Optional[str] = None
    amount: Dec
    date: date
    method: str  # virement, cheque
    reference: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "bank_transactions"


# ──────────────────── Suppliers ────────────────────

class Supplier(Document):
    supplier_id: Indexed(int, unique=True)
    name: str
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

    class Settings:
        name = "suppliers"


class PurchaseOrderItem(Document):
    item_id: Indexed(int, unique=True)
    order_id: int
    product_id: int
    quantity: int
    unit_price: Dec

    class Settings:
        name = "purchase_order_items"


class PurchaseOrder(Document):
    order_id: Indexed(int, unique=True)
    supplier_id: int
    order_date: datetime = Field(default_factory=datetime.utcnow)
    status: str = "PENDING"
    total_amount: Dec = Decimal("0")

    class Settings:
        name = "purchase_orders"


class SupplierDebtPayment(Document):
    payment_id: Indexed(int, unique=True)
    debt_id: int
    amount: Dec
    payment_date: datetime = Field(default_factory=datetime.utcnow)
    payment_method: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None

    class Settings:
        name = "supplier_debt_payments"


class SupplierDebt(Document):
    debt_id: Indexed(int, unique=True)
    supplier_id: Optional[int] = None
    reference: str
    date: datetime = Field(default_factory=datetime.utcnow)
    due_date: Optional[datetime] = None
    amount: Dec
    paid_amount: Dec = Decimal("0")
    remaining_amount: Dec = Decimal("0")
    status: str = "pending"
    description: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "supplier_debts"


class SupplierInvoicePayment(Document):
    payment_id: Indexed(int, unique=True)
    supplier_invoice_id: int
    amount: Dec
    payment_date: datetime = Field(default_factory=datetime.utcnow)
    payment_method: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "supplier_invoice_payments"


class SupplierInvoice(Document):
    invoice_id: Indexed(int, unique=True)
    supplier_id: Optional[int] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    description: Optional[str] = None
    amount: Optional[Dec] = None
    paid_amount: Dec = Decimal("0")
    remaining_amount: Dec = Decimal("0")
    status: str = "pending"
    payment_method: Optional[str] = None
    notes: Optional[str] = None
    pdf_path: Optional[str] = None
    pdf_filename: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "supplier_invoices"


# ──────────────────── Quotations ────────────────────

class QuotationItem(Document):
    item_id: Indexed(int, unique=True)
    quotation_id: int
    product_id: Optional[int] = None
    product_name: str
    quantity: int
    price: Dec
    total: Dec

    class Settings:
        name = "quotation_items"


class Quotation(Document):
    quotation_id: Indexed(int, unique=True)
    quotation_number: Indexed(str, unique=True)
    client_id: int
    date: datetime
    expiry_date: Optional[datetime] = None
    status: str = "en attente"
    is_sent: bool = False
    subtotal: Dec
    tax_rate: Dec = Decimal("18.00")
    tax_amount: Dec
    total: Dec
    notes: Optional[str] = None
    show_item_prices: bool = True
    show_section_totals: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[int] = None

    class Settings:
        name = "quotations"


# ──────────────────── Invoices ────────────────────

class InvoiceItem(Document):
    item_id: Indexed(int, unique=True)
    invoice_id: int
    product_id: Optional[int] = None
    product_name: str
    quantity: int
    price: Dec
    total: Dec

    class Settings:
        name = "invoice_items"


class InvoicePayment(Document):
    payment_id: Indexed(int, unique=True)
    invoice_id: int
    amount: Dec
    payment_date: datetime = Field(default_factory=datetime.utcnow)
    payment_method: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None

    class Settings:
        name = "invoice_payments"


class Invoice(Document):
    invoice_id: Indexed(int, unique=True)
    invoice_number: Indexed(str, unique=True)
    client_id: int
    quotation_id: Optional[int] = None
    date: datetime
    due_date: Optional[datetime] = None
    status: str = "en attente"
    payment_method: Optional[str] = None
    subtotal: Dec
    tax_rate: Dec = Decimal("18.00")
    tax_amount: Dec
    total: Dec
    paid_amount: Dec = Decimal("0")
    remaining_amount: Dec
    notes: Optional[str] = None
    show_tax: bool = True
    show_item_prices: bool = True
    show_section_totals: bool = True
    price_display: str = "TTC"
    has_warranty: bool = False
    warranty_duration: Optional[int] = None
    warranty_start_date: Optional[date] = None
    warranty_end_date: Optional[date] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[int] = None

    class Settings:
        name = "invoices"


# ──────────────────── Delivery Notes ────────────────────

class DeliveryNoteItem(Document):
    item_id: Indexed(int, unique=True)
    delivery_note_id: int
    product_id: Optional[int] = None
    product_name: str
    quantity: int
    price: Dec
    delivered_quantity: int = 0
    serial_numbers: Optional[str] = None

    class Settings:
        name = "delivery_note_items"


class DeliveryNote(Document):
    delivery_note_id: Indexed(int, unique=True)
    delivery_note_number: Indexed(str, unique=True)
    invoice_id: Optional[int] = None
    client_id: Optional[int] = None
    date: datetime
    delivery_date: Optional[datetime] = None
    status: str = "en_preparation"
    delivery_address: Optional[str] = None
    delivery_contact: Optional[str] = None
    delivery_phone: Optional[str] = None
    subtotal: Dec
    tax_rate: Dec = Decimal("18.00")
    tax_amount: Dec
    total: Dec
    transport_cost: Dec = Decimal("0")
    notes: Optional[str] = None
    delivered_by: Optional[str] = None
    signature_received: bool = False
    signature_data_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    delivered_at: Optional[datetime] = None

    class Settings:
        name = "delivery_notes"


# ──────────────────── Settings / Cache ────────────────────

class UserSettings(Document):
    setting_id: Indexed(int, unique=True)
    user_id: Optional[int] = None
    setting_key: str
    setting_value: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "user_settings"


class ScanHistory(Document):
    scan_id: Indexed(int, unique=True)
    user_id: int
    barcode: str
    product_name: Optional[str] = None
    scan_type: Optional[str] = None
    result_data: Optional[str] = None
    scanned_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "scan_history"


class AppCache(Document):
    cache_id: Indexed(int, unique=True)
    cache_key: Indexed(str, unique=True)
    cache_value: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "app_cache"


# ──────────────────── Daily Client Requests ────────────────────

class DailyClientRequest(Document):
    request_id: Indexed(int, unique=True)
    client_id: Optional[int] = None
    client_name: str
    client_phone: Optional[str] = None
    product_description: str
    request_date: date
    status: str = "pending"
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "daily_client_requests"


# ──────────────────── Daily Sales ────────────────────

class DailySale(Document):
    sale_id: Indexed(int, unique=True)
    client_id: Optional[int] = None
    client_name: str
    product_id: Optional[int] = None
    product_name: str
    variant_id: Optional[int] = None
    variant_imei: Optional[str] = None
    variant_barcode: Optional[str] = None
    variant_condition: Optional[str] = None
    quantity: int = 1
    unit_price: Dec
    total_amount: Dec
    sale_date: date
    payment_method: str = "espece"
    invoice_id: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "daily_sales"


# ──────────────────── Migrations ────────────────────

class MigrationLog(Document):
    log_id: Indexed(int, unique=True)
    migration_id: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    level: str = "info"
    message: str

    class Settings:
        name = "migration_logs"


class Migration(Document):
    migration_id: Indexed(int, unique=True)
    name: str
    type: str
    status: str = "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    total_records: int = 0
    processed_records: int = 0
    success_records: int = 0
    error_records: int = 0
    file_name: Optional[str] = None
    description: Optional[str] = None
    error_message: Optional[str] = None
    created_by: Optional[int] = None

    class Settings:
        name = "migrations"


# ──────────────────── Maintenance ────────────────────

class Maintenance(Document):
    maintenance_id: Indexed(int, unique=True)
    maintenance_number: Indexed(str, unique=True)
    client_id: Optional[int] = None
    client_name: str
    client_phone: Optional[str] = None
    client_email: Optional[str] = None
    device_type: str
    device_brand: Optional[str] = None
    device_model: Optional[str] = None
    device_serial: Optional[str] = None
    device_description: Optional[str] = None
    device_accessories: Optional[str] = None
    device_condition: Optional[str] = None
    problem_description: str
    diagnosis: Optional[str] = None
    work_done: Optional[str] = None
    reception_date: datetime = Field(default_factory=datetime.utcnow)
    estimated_completion_date: Optional[date] = None
    actual_completion_date: Optional[date] = None
    pickup_deadline: Optional[date] = None
    pickup_date: Optional[date] = None
    status: str = "received"
    priority: str = "normal"
    estimated_cost: Optional[Dec] = None
    final_cost: Optional[Dec] = None
    advance_paid: Dec = Decimal("0")
    warranty_days: int = 30
    liability_waived: bool = False
    liability_waived_date: Optional[date] = None
    reminder_sent: bool = False
    reminder_sent_date: Optional[datetime] = None
    technician_id: Optional[int] = None
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "maintenances"


# ──────────────────── All document models list ────────────────────

ALL_DOCUMENT_MODELS = [
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
]
