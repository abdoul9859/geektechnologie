from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, UniqueConstraint, Index, Numeric, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

# Source de vérité de la connexion DB
_RAW_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    # Valeur par défaut: SQLite local
    "sqlite:///./data/geektech.db",
)

# Normalisation pour SQLAlchemy
def _normalize_db_url(url: str) -> str:
    if not url:
        return url
    u = url.strip()
    # Alias postgres -> postgresql et forcer le driver psycopg (v3)
    if u.startswith("postgres://"):
        u = u.replace("postgres://", "postgresql+psycopg://", 1)
    elif u.startswith("postgresql://"):
        u = u.replace("postgresql://", "postgresql+psycopg://", 1)
    elif u.startswith("postgresql+psycopg2://"):
        u = u.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    # Gestion SSL: respecter URL explicite, sinon DB_SSLMODE, sinon heuristique locale vs distante
    if u.startswith("postgresql+") and "sslmode=" not in u:
        # 1) Variable d'env prioritaire si définie (ex: require, disable, prefer)
        sslmode_env = os.getenv("DB_SSLMODE")
        if sslmode_env:
            sep = "&" if "?" in u else "?"
            u = f"{u}{sep}sslmode={sslmode_env}"
        else:
            # 2) Heuristique: pour localhost/127.0.0.1 -> disable, sinon require
            lower_u = u.lower()
            is_local = ("@localhost" in lower_u) or ("@127.0.0.1" in lower_u)
            sep = "&" if "?" in u else "?"
            u = f"{u}{sep}sslmode={'disable' if is_local else 'require'}"
    return u

DATABASE_URL = _normalize_db_url(_RAW_DATABASE_URL)

# Pool configuration (tunable via env)
_is_sqlite = "sqlite" in DATABASE_URL
_pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
_max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
_pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
_pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "1800"))  # 30 min

engine_kwargs = {
    "pool_pre_ping": True,
}
if _is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update({
        "pool_size": _pool_size,
        "max_overflow": _max_overflow,
        "pool_timeout": _pool_timeout,
        "pool_recycle": _pool_recycle,
    })

engine = create_engine(
    DATABASE_URL,
    **engine_kwargs,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Modèles de base de données basés sur le schéma PostgreSQL original

class User(Base):
    __tablename__ = "users"
    
    user_id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100))
    role = Column(String(20), default="user")  # admin, user, manager
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    last_login = Column(DateTime)

class Client(Base):
    __tablename__ = "clients"
    
    client_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    contact = Column(String(100))
    email = Column(String(100))
    phone = Column(String(20))
    address = Column(Text)
    city = Column(String(50))
    postal_code = Column(String(10))
    country = Column(String(50), default="Sénégal")
    tax_number = Column(String(50))
    notes = Column(Text)
    disable_debt_reminder = Column(Boolean, default=False)

# Créances clients (dettes clients manuelles)
class ClientDebt(Base):
    __tablename__ = "client_debts"

    debt_id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="SET NULL"), index=True, nullable=True)
    reference = Column(String(100), nullable=False)
    date = Column(DateTime, default=func.now())
    due_date = Column(DateTime)
    amount = Column(Numeric(12, 2), nullable=False)
    paid_amount = Column(Numeric(12, 2), default=0)
    remaining_amount = Column(Numeric(12, 2), default=0)
    status = Column(String(20), default="pending")  # pending, partial, paid, overdue
    description = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

    # Relations
    client = relationship("Client")

class ClientDebtPayment(Base):
    __tablename__ = "client_debt_payments"

    payment_id = Column(Integer, primary_key=True, index=True)
    debt_id = Column(Integer, ForeignKey("client_debts.debt_id", ondelete="CASCADE"))
    amount = Column(Numeric(12, 2), nullable=False)
    payment_date = Column(DateTime, default=func.now())
    payment_method = Column(String(50))
    reference = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

# Achats quotidiens (petites dépenses)
class DailyPurchase(Base):
    __tablename__ = "daily_purchases"

    id = Column(Integer, primary_key=True, index=True)
    # Date de l'achat (jour civil)
    date = Column(Date, nullable=False, index=True)
    # Catégorie simple: café, eau, électricité, transport, fournitures, autres
    category = Column(String(50), nullable=False, index=True)
    # Fournisseur ou source libre (ex: "Boutique du coin")
    supplier = Column(String(100))
    # Description libre
    description = Column(Text)
    # Montant TTC
    amount = Column(Numeric(12, 2), nullable=False)
    # Méthode: espece | mobile | virement | cheque
    payment_method = Column(String(20), default="espece", index=True)
    # Référence/Justif optionnelle
    reference = Column(String(100))
    created_at = Column(DateTime, default=func.now())
    created_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    
    # Relations
    creator = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        Index('ix_daily_purchases_date_category', 'date', 'category'),
    )

# Paramétrage des catégories d'achats quotidiens
class DailyPurchaseCategory(Base):
    __tablename__ = "daily_purchase_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())

class Category(Base):
    __tablename__ = "categories"
    
    category_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(Text)
    requires_variants = Column(Boolean, default=False, nullable=False)

class CategoryAttribute(Base):
    __tablename__ = "category_attributes"
    
    attribute_id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.category_id", ondelete="CASCADE"), index=True, nullable=False)
    name = Column(String(50), nullable=False)
    code = Column(String(50), nullable=True)  # unique within category
    type = Column(String(20), default="select")  # select, multiselect, text, number, boolean
    required = Column(Boolean, default=False, nullable=False)
    multi_select = Column(Boolean, default=False, nullable=False)
    sort_order = Column(Integer, default=0)
    
    # Relations
    values = relationship("CategoryAttributeValue", back_populates="attribute", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint('category_id', 'code', name='uq_category_attribute_code_per_category'),
    )

class CategoryAttributeValue(Base):
    __tablename__ = "category_attribute_values"
    
    value_id = Column(Integer, primary_key=True, index=True)
    attribute_id = Column(Integer, ForeignKey("category_attributes.attribute_id", ondelete="CASCADE"), index=True, nullable=False)
    value = Column(String(100), nullable=False)
    code = Column(String(100), nullable=True)  # unique within attribute
    sort_order = Column(Integer, default=0)
    
    # Relations
    attribute = relationship("CategoryAttribute", back_populates="values")
    
    __table_args__ = (
        UniqueConstraint('attribute_id', 'code', name='uq_attribute_value_code_per_attribute'),
    )

class Product(Base):
    __tablename__ = "products"

    product_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(500), nullable=False)  # Augmenté selon les mémoires
    description = Column(Text)
    quantity = Column(Integer, nullable=False, default=0)
    price = Column(Numeric(10, 2), nullable=False)  # Prix de vente unitaire
    wholesale_price = Column(Numeric(10, 2), nullable=True)  # Prix de vente en gros
    purchase_price = Column(Numeric(10, 2), default=0.00)
    category = Column(String(50), index=True)
    brand = Column(String(100))
    model = Column(String(100))
    barcode = Column(String(255), unique=True)
    condition = Column(String(50), nullable=True, default="neuf")  # neuf | occasion | venant (configurable)
    has_unique_serial = Column(Boolean, default=False)
    entry_date = Column(DateTime)
    notes = Column(Text)
    image_path = Column(String(500), nullable=True)  # Chemin vers l'image du produit
    created_at = Column(DateTime, default=func.now())
    is_archived = Column(Boolean, default=False, index=True)  # Produit archivé (masqué par défaut)

    # Relations
    serial_numbers = relationship("ProductSerialNumber", back_populates="product", cascade="all, delete-orphan")
    stock_movements = relationship("StockMovement", back_populates="product")
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")

class ProductSerialNumber(Base):
    __tablename__ = "product_serial_numbers"
    
    serial_number_id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="CASCADE"))
    serial_number = Column(String(255), nullable=False)
    barcode = Column(String(255), unique=True)
    is_available = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    
    # Relations
    product = relationship("Product", back_populates="serial_numbers")
    
    __table_args__ = (UniqueConstraint('product_id', 'serial_number'),)

class ProductVariant(Base):
    __tablename__ = "product_variants"
    
    variant_id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="CASCADE"))
    imei_serial = Column(String(255), unique=True, nullable=False)
    barcode = Column(String(128), unique=True)  # Selon les mémoires
    condition = Column(String(50), nullable=True)  # hérite par défaut du produit
    is_sold = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    
    # Relations
    product = relationship("Product", back_populates="variants")
    attributes = relationship("ProductVariantAttribute", back_populates="variant", cascade="all, delete-orphan")

class ProductVariantAttribute(Base):
    __tablename__ = "product_variant_attributes"
    
    attribute_id = Column(Integer, primary_key=True, index=True)
    variant_id = Column(Integer, ForeignKey("product_variants.variant_id", ondelete="CASCADE"))
    attribute_name = Column(String(50), nullable=False)  # couleur, stockage, etc.
    attribute_value = Column(String(100), nullable=False)
    
    # Relations
    variant = relationship("ProductVariant", back_populates="attributes")

class StockMovement(Base):
    __tablename__ = "stock_movements"
    
    movement_id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="CASCADE"))
    quantity = Column(Integer, nullable=False)
    movement_type = Column(String(10), nullable=False)  # IN, OUT
    reference_type = Column(String(20))  # INVOICE, QUOTATION, etc.
    reference_id = Column(Integer)
    notes = Column(Text)
    unit_price = Column(Numeric(10, 2), default=0)
    created_at = Column(DateTime, default=func.now())
    
    # Relations
    product = relationship("Product", back_populates="stock_movements")

# --- Bank Transactions ---
class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True, index=True)
    # 'entry' or 'exit'
    type = Column(String(10), nullable=False, index=True)
    motif = Column(String(255), nullable=False)
    description = Column(Text)
    amount = Column(Numeric(12, 2), nullable=False)
    date = Column(Date, nullable=False, index=True)
    # 'virement' or 'cheque'
    method = Column(String(20), nullable=False, index=True)
    reference = Column(String(255))
    created_at = Column(DateTime, default=func.now())

class Supplier(Base):
    __tablename__ = "suppliers"
    
    supplier_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    contact_person = Column(String(100))
    email = Column(String(100))
    phone = Column(String(20))
    address = Column(Text)

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    
    order_id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.supplier_id"))
    order_date = Column(DateTime, default=func.now())
    status = Column(String(20), default="PENDING")  # PENDING, DELIVERED, CANCELLED
    total_amount = Column(Numeric(12, 2), default=0)
    
    # Relations
    supplier = relationship("Supplier")
    items = relationship("PurchaseOrderItem", back_populates="order", cascade="all, delete-orphan")

class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    
    item_id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("purchase_orders.order_id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.product_id"))
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    
    # Relations
    order = relationship("PurchaseOrder", back_populates="items")
    product = relationship("Product")

# Dettes fournisseurs
class SupplierDebt(Base):
    __tablename__ = "supplier_debts"

    debt_id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.supplier_id", ondelete="SET NULL"), index=True, nullable=True)
    reference = Column(String(100), nullable=False)
    date = Column(DateTime, default=func.now())
    due_date = Column(DateTime)
    amount = Column(Numeric(12, 2), nullable=False)
    paid_amount = Column(Numeric(12, 2), default=0)
    remaining_amount = Column(Numeric(12, 2), default=0)
    status = Column(String(20), default="pending")  # pending, partial, paid, overdue
    description = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

    # Relations
    supplier = relationship("Supplier")

class SupplierDebtPayment(Base):
    __tablename__ = "supplier_debt_payments"

    payment_id = Column(Integer, primary_key=True, index=True)
    debt_id = Column(Integer, ForeignKey("supplier_debts.debt_id", ondelete="CASCADE"))
    amount = Column(Numeric(12, 2), nullable=False)
    payment_date = Column(DateTime, default=func.now())
    payment_method = Column(String(50))
    reference = Column(String(100))
    notes = Column(Text)

# Factures fournisseur (version simplifiée)
class SupplierInvoice(Base):
    __tablename__ = "supplier_invoices"

    invoice_id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.supplier_id", ondelete="SET NULL"), index=True)
    invoice_number = Column(String(100), unique=True, nullable=True)  # Optionnel - peut être extrait du PDF
    invoice_date = Column(DateTime, nullable=True)  # Optionnel - peut être extrait du PDF
    due_date = Column(DateTime)
    description = Column(Text, nullable=True)  # Optionnel - peut être extrait du PDF
    amount = Column(Numeric(12, 2), nullable=True)  # Optionnel - peut être extrait du PDF
    paid_amount = Column(Numeric(12, 2), default=0)
    remaining_amount = Column(Numeric(12, 2), default=0)
    status = Column(String(20), default="pending")  # pending, partial, paid, overdue
    payment_method = Column(String(50))
    notes = Column(Text)
    pdf_path = Column(String(500), nullable=False)  # Obligatoire - chemin vers le fichier PDF
    pdf_filename = Column(String(255), nullable=False)  # Obligatoire - nom original du fichier PDF
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relations
    supplier = relationship("Supplier")
    payments = relationship("SupplierInvoicePayment", back_populates="invoice", cascade="all, delete-orphan")

# Ancienne table SupplierInvoiceItem - supprimée dans la version simplifiée
# class SupplierInvoiceItem(Base):
#     __tablename__ = "supplier_invoice_items" - plus utilisée

class SupplierInvoicePayment(Base):
    __tablename__ = "supplier_invoice_payments"

    payment_id = Column(Integer, primary_key=True, index=True)
    supplier_invoice_id = Column(Integer, ForeignKey("supplier_invoices.invoice_id", ondelete="CASCADE"))
    amount = Column(Numeric(12, 2), nullable=False)
    payment_date = Column(DateTime, default=func.now())
    payment_method = Column(String(50))
    reference = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

    # Relations
    invoice = relationship("SupplierInvoice", back_populates="payments")

class Quotation(Base):
    __tablename__ = "quotations"
    
    quotation_id = Column(Integer, primary_key=True, index=True)
    quotation_number = Column(String(50), unique=True, nullable=False)
    client_id = Column(Integer, ForeignKey("clients.client_id"))
    date = Column(DateTime, nullable=False)
    expiry_date = Column(DateTime)
    status = Column(String(20), default="en attente")  # en attente, accepté, refusé, expiré
    is_sent = Column(Boolean, default=False)  # champ séparé pour marquer l'envoi
    subtotal = Column(Numeric(12, 2), nullable=False)
    tax_rate = Column(Numeric(5, 2), default=18.00)
    tax_amount = Column(Numeric(12, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    notes = Column(Text)
    show_item_prices = Column(Boolean, default=True)  # Afficher prix par article
    show_section_totals = Column(Boolean, default=True)  # Afficher total par section
    created_at = Column(DateTime, default=func.now())
    created_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    
    # Relations
    client = relationship("Client")
    items = relationship("QuotationItem", back_populates="quotation", cascade="all, delete-orphan")
    creator = relationship("User", foreign_keys=[created_by])

class QuotationItem(Base):
    __tablename__ = "quotation_items"
    
    item_id = Column(Integer, primary_key=True, index=True)
    quotation_id = Column(Integer, ForeignKey("quotations.quotation_id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.product_id"))
    product_name = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    
    # Relations
    quotation = relationship("Quotation", back_populates="items")
    product = relationship("Product")

class Invoice(Base):
    __tablename__ = "invoices"
    
    invoice_id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False)
    client_id = Column(Integer, ForeignKey("clients.client_id"))
    quotation_id = Column(Integer, ForeignKey("quotations.quotation_id"))
    date = Column(DateTime, nullable=False)
    due_date = Column(DateTime)
    status = Column(String(20), default="en attente")  # en attente, payée, partiellement payée, en retard, annulée
    payment_method = Column(String(50))
    subtotal = Column(Numeric(12, 2), nullable=False)
    tax_rate = Column(Numeric(5, 2), default=18.00)
    tax_amount = Column(Numeric(12, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    paid_amount = Column(Numeric(12, 2), default=0)
    remaining_amount = Column(Numeric(12, 2), nullable=False)
    notes = Column(Text)
    show_tax = Column(Boolean, default=True)
    show_item_prices = Column(Boolean, default=True)  # Afficher prix par article
    show_section_totals = Column(Boolean, default=True)  # Afficher total par section
    price_display = Column(String(10), default="TTC")  # HT, TTC
    # Champs de garantie
    has_warranty = Column(Boolean, default=False)
    warranty_duration = Column(Integer)  # en mois (6 ou 12)
    warranty_start_date = Column(Date)
    warranty_end_date = Column(Date)
    created_at = Column(DateTime, default=func.now())
    created_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    
    # Relations
    client = relationship("Client")
    quotation = relationship("Quotation")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    payments = relationship("InvoicePayment", back_populates="invoice", cascade="all, delete-orphan")
    creator = relationship("User", foreign_keys=[created_by])

class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    
    item_id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.product_id"))
    product_name = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    
    # Relations
    invoice = relationship("Invoice", back_populates="items")
    product = relationship("Product")

class InvoicePayment(Base):
    __tablename__ = "invoice_payments"
    
    payment_id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id", ondelete="CASCADE"))
    amount = Column(Numeric(12, 2), nullable=False)
    payment_date = Column(DateTime, default=func.now())
    payment_method = Column(String(50))
    reference = Column(String(100))
    notes = Column(Text)
    
    # Relations
    invoice = relationship("Invoice", back_populates="payments")

class DeliveryNote(Base):
    __tablename__ = "delivery_notes"
    
    delivery_note_id = Column(Integer, primary_key=True, index=True)
    delivery_note_number = Column(String(50), unique=True, nullable=False)
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id"))
    client_id = Column(Integer, ForeignKey("clients.client_id"))
    date = Column(DateTime, nullable=False)
    delivery_date = Column(DateTime)
    status = Column(String(20), default="en_preparation")  # en_preparation, en_cours, livré, annulé
    delivery_address = Column(Text)
    delivery_contact = Column(String(100))
    delivery_phone = Column(String(20))
    subtotal = Column(Numeric(12, 2), nullable=False)
    tax_rate = Column(Numeric(5, 2), default=18.00)
    tax_amount = Column(Numeric(12, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    transport_cost = Column(Numeric(10, 2), default=0)
    notes = Column(Text)
    delivered_by = Column(String(100))
    signature_received = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    delivered_at = Column(DateTime)
    
    # Relations
    invoice = relationship("Invoice")
    client = relationship("Client")
    items = relationship("DeliveryNoteItem", back_populates="delivery_note", cascade="all, delete-orphan")

class DeliveryNoteItem(Base):
    __tablename__ = "delivery_note_items"
    
    item_id = Column(Integer, primary_key=True, index=True)
    delivery_note_id = Column(Integer, ForeignKey("delivery_notes.delivery_note_id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.product_id"))
    product_name = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    delivered_quantity = Column(Integer, default=0)
    serial_numbers = Column(Text)  # JSON string pour les numéros de série
    
    # Relations
    delivery_note = relationship("DeliveryNote", back_populates="items")
    product = relationship("Product")

# Tables pour les paramètres et cache
class UserSettings(Base):
    __tablename__ = "user_settings"
    
    setting_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True)
    setting_key = Column(String(100), nullable=False)
    setting_value = Column(Text)  # JSON string
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relations
    user = relationship("User")
    
    __table_args__ = (UniqueConstraint('user_id', 'setting_key'),)

class ScanHistory(Base):
    __tablename__ = "scan_history"
    
    scan_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"))
    barcode = Column(String(255), nullable=False)
    product_name = Column(String(500))
    scan_type = Column(String(50))  # product, variant, etc.
    result_data = Column(Text)  # JSON string avec les détails
    scanned_at = Column(DateTime, default=func.now())
    
    # Relations
    user = relationship("User")

class AppCache(Base):
    __tablename__ = "app_cache"
    
    cache_id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String(255), unique=True, nullable=False)
    cache_value = Column(Text)  # JSON string
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

# Demandes quotidiennes des clients
class DailyClientRequest(Base):
    __tablename__ = "daily_client_requests"

    request_id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="SET NULL"), index=True, nullable=True)
    client_name = Column(String(100), nullable=False)  # Nom du client (peut être différent de la base)
    client_phone = Column(String(20))  # Téléphone du client
    product_description = Column(Text, nullable=False)  # Description textuelle du produit demandé
    request_date = Column(Date, nullable=False, index=True)
    status = Column(String(20), default="pending")  # pending, fulfilled, cancelled
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relations
    client = relationship("Client")

# Ventes quotidiennes
class DailySale(Base):
    __tablename__ = "daily_sales"

    sale_id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="SET NULL"), index=True, nullable=True)
    client_name = Column(String(100), nullable=False)  # Nom du client
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="SET NULL"), index=True, nullable=True)
    product_name = Column(String(500), nullable=False)  # Nom du produit vendu
    variant_id = Column(Integer, ForeignKey("product_variants.variant_id", ondelete="SET NULL"), index=True, nullable=True)
    variant_imei = Column(String(255), nullable=True)  # IMEI de la variante vendue
    variant_barcode = Column(String(128), nullable=True)  # Code-barres de la variante
    variant_condition = Column(String(50), nullable=True)  # Condition de la variante
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total_amount = Column(Numeric(12, 2), nullable=False)
    sale_date = Column(Date, nullable=False, index=True)
    payment_method = Column(String(50), default="espece")  # espece, mobile, virement, cheque
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id", ondelete="SET NULL"), nullable=True)  # Si lié à une facture
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

    # Relations
    client = relationship("Client")
    product = relationship("Product")
    variant = relationship("ProductVariant")
    invoice = relationship("Invoice")

    __table_args__ = (
        Index('ix_daily_sales_date_client', 'sale_date', 'client_id'),
    )

# Migrations de données
class Migration(Base):
    __tablename__ = "migrations"

    migration_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False)  # products, clients, stock, etc.
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    created_at = Column(DateTime, default=func.now())
    completed_at = Column(DateTime)
    total_records = Column(Integer, default=0)
    processed_records = Column(Integer, default=0)
    success_records = Column(Integer, default=0)
    error_records = Column(Integer, default=0)
    file_name = Column(String(255))
    description = Column(Text)
    error_message = Column(Text)
    created_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))

    # Relations
    user = relationship("User")
    logs = relationship("MigrationLog", back_populates="migration", cascade="all, delete-orphan")

class MigrationLog(Base):
    __tablename__ = "migration_logs"

    log_id = Column(Integer, primary_key=True, index=True)
    migration_id = Column(Integer, ForeignKey("migrations.migration_id", ondelete="CASCADE"))
    timestamp = Column(DateTime, default=func.now())
    level = Column(String(20), default="info")  # info, success, error
    message = Column(Text, nullable=False)

    # Relations
    migration = relationship("Migration", back_populates="logs")

# Fonction pour créer les tables
def create_tables():
    Base.metadata.create_all(bind=engine)

# Fonction pour obtenir une session de base de données
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        # Defensive close: if the server terminated the connection (e.g.,
        # IdleInTransactionSessionTimeout), SQLAlchemy may raise during the
        # implicit rollback on close. Swallow those errors to avoid masking
        # the real response with a 500 at shutdown.
        try:
            db.close()
        except Exception:
            try:
                # Attempt explicit rollback then close, ignore any errors.
                db.rollback()
            except Exception:
                pass


# ==================== MODÈLE MAINTENANCE ====================

class Maintenance(Base):
    __tablename__ = "maintenances"
    
    maintenance_id = Column(Integer, primary_key=True, index=True)
    maintenance_number = Column(String(50), unique=True, nullable=False, index=True)  # Ex: MAINT-0001
    
    # Client
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="SET NULL"), nullable=True)
    client_name = Column(String(100), nullable=False)
    client_phone = Column(String(20))
    client_email = Column(String(100))
    
    # Machine/Appareil
    device_type = Column(String(100), nullable=False)  # Ordinateur portable, PC fixe, Imprimante, etc.
    device_brand = Column(String(100))  # Marque
    device_model = Column(String(100))  # Modèle
    device_serial = Column(String(100))  # Numéro de série
    device_description = Column(Text)  # Description détaillée de l'appareil
    device_accessories = Column(Text)  # Accessoires laissés (chargeur, souris, etc.)
    device_condition = Column(Text)  # État à la réception (rayures, dommages, etc.)
    
    # Problème et diagnostic
    problem_description = Column(Text, nullable=False)  # Description du problème par le client
    diagnosis = Column(Text)  # Diagnostic du technicien
    work_done = Column(Text)  # Travaux effectués
    
    # Dates
    reception_date = Column(DateTime, nullable=False, default=func.now())  # Date de réception
    estimated_completion_date = Column(Date)  # Date estimée de fin
    actual_completion_date = Column(Date)  # Date réelle de fin
    pickup_deadline = Column(Date)  # Date limite de récupération
    pickup_date = Column(Date)  # Date de récupération effective
    
    # Statut
    status = Column(String(30), default="received")  # received, in_progress, completed, ready, picked_up, abandoned
    priority = Column(String(20), default="normal")  # low, normal, high, urgent
    
    # Coûts
    estimated_cost = Column(Numeric(12, 2))  # Coût estimé
    final_cost = Column(Numeric(12, 2))  # Coût final
    advance_paid = Column(Numeric(12, 2), default=0)  # Avance payée
    
    # Garantie et responsabilité
    warranty_days = Column(Integer, default=30)  # Jours de garantie sur la réparation
    liability_waived = Column(Boolean, default=False)  # Responsabilité dégagée après délai
    liability_waived_date = Column(Date)  # Date de dégagement de responsabilité
    
    # Rappels
    reminder_sent = Column(Boolean, default=False)  # Rappel envoyé
    reminder_sent_date = Column(DateTime)  # Date d'envoi du rappel
    
    # Technicien
    technician_id = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    
    # Notes
    notes = Column(Text)
    internal_notes = Column(Text)  # Notes internes (non visibles sur la fiche)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relations
    client = relationship("Client")
    technician = relationship("User")
    
    __table_args__ = (
        Index('ix_maintenances_status', 'status'),
        Index('ix_maintenances_pickup_deadline', 'pickup_deadline'),
    )


# ==================== MODÈLES BOUTIQUE EN LIGNE ====================
# Importer les modèles de la boutique pour créer les tables
try:
    from boutique.backend.models import (
        StoreCustomer,
        StoreOrder,
        StoreOrderItem,
        StorePayment
    )
except ImportError:
    # Si les modèles de la boutique ne sont pas encore disponibles
    pass
