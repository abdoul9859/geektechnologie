#!/usr/bin/env python3
"""
SQLite → MongoDB migration script.

Reads all data from the SQLite backup and inserts into MongoDB
using Motor + Beanie. Also initialises the counters collection
so that get_next_id() continues from the correct sequence.

Usage:
    python migrate_sqlite_to_mongo.py [--sqlite PATH] [--mongo URL]

Defaults:
    --sqlite  /opt/backups/geektech_20260207.db
    --mongo   mongodb://localhost:27017/geektech
"""

import asyncio
import sqlite3
import sys
import os
import argparse
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from app.models.documents import (
    User, Client, ClientDebt, ClientDebtPayment,
    DailyPurchase, DailyPurchaseCategory,
    Category, CategoryAttribute, CategoryAttributeValue,
    Product, ProductSerialNumber, ProductVariant, ProductVariantAttribute,
    StockMovement, BankTransaction,
    Supplier, PurchaseOrder, PurchaseOrderItem,
    SupplierDebt, SupplierDebtPayment, SupplierInvoice, SupplierInvoicePayment,
    Quotation, QuotationItem, Invoice, InvoiceItem, InvoicePayment,
    DeliveryNote, DeliveryNoteItem,
    UserSettings, ScanHistory, AppCache,
    DailyClientRequest, DailySale,
    Migration, MigrationLog, Maintenance,
    ALL_DOCUMENT_MODELS,
)

# ─────────────────── helpers ───────────────────

def parse_datetime(val):
    """Parse a SQLite TEXT datetime into a Python datetime."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_date(val):
    """Parse a SQLite TEXT date into a Python date."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    dt = parse_datetime(s)
    if dt:
        return dt.date() if isinstance(dt, datetime) else dt
    return None


def to_decimal(val):
    """Convert a SQLite REAL/TEXT to Decimal."""
    if val is None:
        return Decimal("0")
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def to_bool(val):
    """Convert SQLite INTEGER 0/1 to bool."""
    if val is None:
        return False
    return bool(int(val))


def to_int(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def to_str(val):
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


# ─────────────────── table migration mappings ───────────────────

# Each entry: (sqlite_table, beanie_model, field_mapping)
# field_mapping is a dict: { mongo_field: (sqlite_column, converter) }
# converter is a callable: converter(raw_value) -> python_value

MIGRATIONS = [
    ("users", User, {
        "user_id": ("user_id", to_int),
        "username": ("username", to_str),
        "email": ("email", to_str),
        "password_hash": ("password_hash", to_str),
        "full_name": ("full_name", to_str),
        "role": ("role", lambda v: to_str(v) or "user"),
        "is_active": ("is_active", to_bool),
        "created_at": ("created_at", parse_datetime),
        "last_login": ("last_login", parse_datetime),
    }),
    ("clients", Client, {
        "client_id": ("client_id", to_int),
        "name": ("name", to_str),
        "contact": ("contact", to_str),
        "email": ("email", to_str),
        "phone": ("phone", to_str),
        "address": ("address", to_str),
        "city": ("city", to_str),
        "postal_code": ("postal_code", to_str),
        "country": ("country", lambda v: to_str(v) or "Sénégal"),
        "tax_number": ("tax_number", to_str),
        "notes": ("notes", to_str),
        "disable_debt_reminder": ("disable_debt_reminder", to_bool),
    }),
    ("client_debts", ClientDebt, {
        "debt_id": ("debt_id", to_int),
        "client_id": ("client_id", lambda v: to_int(v, None)),
        "reference": ("reference", lambda v: to_str(v) or ""),
        "date": ("date", parse_datetime),
        "due_date": ("due_date", parse_datetime),
        "amount": ("amount", to_decimal),
        "paid_amount": ("paid_amount", to_decimal),
        "remaining_amount": ("remaining_amount", to_decimal),
        "status": ("status", lambda v: to_str(v) or "pending"),
        "description": ("description", to_str),
        "notes": ("notes", to_str),
        "created_at": ("created_at", parse_datetime),
    }),
    ("client_debt_payments", ClientDebtPayment, {
        "payment_id": ("payment_id", to_int),
        "debt_id": ("debt_id", to_int),
        "amount": ("amount", to_decimal),
        "payment_date": ("payment_date", parse_datetime),
        "payment_method": ("payment_method", to_str),
        "reference": ("reference", to_str),
        "notes": ("notes", to_str),
        "created_at": ("created_at", parse_datetime),
    }),
    ("categories", Category, {
        "category_id": ("category_id", to_int),
        "name": ("name", to_str),
        "description": ("description", to_str),
        "requires_variants": ("requires_variants", to_bool),
    }),
    ("category_attributes", CategoryAttribute, {
        "attribute_id": ("attribute_id", to_int),
        "category_id": ("category_id", to_int),
        "name": ("name", to_str),
        "code": ("code", to_str),
        "type": ("type", lambda v: to_str(v) or "select"),
        "required": ("required", to_bool),
        "multi_select": ("multi_select", to_bool),
        "sort_order": ("sort_order", to_int),
    }),
    ("category_attribute_values", CategoryAttributeValue, {
        "value_id": ("value_id", to_int),
        "attribute_id": ("attribute_id", to_int),
        "value": ("value", to_str),
        "code": ("code", to_str),
        "sort_order": ("sort_order", to_int),
    }),
    ("products", Product, {
        "product_id": ("product_id", to_int),
        "name": ("name", to_str),
        "description": ("description", to_str),
        "quantity": ("quantity", to_int),
        "price": ("price", to_decimal),
        "wholesale_price": ("wholesale_price", lambda v: to_decimal(v) if v is not None else None),
        "purchase_price": ("purchase_price", to_decimal),
        "category": ("category", to_str),
        "brand": ("brand", to_str),
        "model": ("model", to_str),
        "barcode": ("barcode", to_str),
        "condition": ("condition", lambda v: to_str(v) or "neuf"),
        "has_unique_serial": ("has_unique_serial", to_bool),
        "entry_date": ("entry_date", parse_datetime),
        "notes": ("notes", to_str),
        "image_path": ("image_path", to_str),
        "created_at": ("created_at", parse_datetime),
        "is_archived": ("is_archived", to_bool),
    }),
    ("product_variants", ProductVariant, {
        "variant_id": ("variant_id", to_int),
        "product_id": ("product_id", to_int),
        "imei_serial": ("imei_serial", lambda v: to_str(v) or ""),
        "barcode": ("barcode", to_str),
        "condition": ("condition", to_str),
        "is_sold": ("is_sold", to_bool),
        "created_at": ("created_at", parse_datetime),
    }),
    ("product_variant_attributes", ProductVariantAttribute, {
        "attribute_id": ("attribute_id", to_int),
        "variant_id": ("variant_id", to_int),
        "attribute_name": ("attribute_name", to_str),
        "attribute_value": ("attribute_value", to_str),
    }),
    ("product_serial_numbers", ProductSerialNumber, {
        "serial_number_id": ("serial_number_id", to_int),
        "product_id": ("product_id", to_int),
        "serial_number": ("serial_number", to_str),
        "barcode": ("barcode", to_str),
        "is_available": ("is_available", to_bool),
        "created_at": ("created_at", parse_datetime),
    }),
    ("stock_movements", StockMovement, {
        "movement_id": ("movement_id", to_int),
        "product_id": ("product_id", to_int),
        "quantity": ("quantity", to_int),
        "movement_type": ("movement_type", to_str),
        "reference_type": ("reference_type", to_str),
        "reference_id": ("reference_id", lambda v: to_int(v, None)),
        "notes": ("notes", to_str),
        "unit_price": ("unit_price", to_decimal),
        "created_at": ("created_at", parse_datetime),
    }),
    ("bank_transactions", BankTransaction, {
        "transaction_id": ("id", to_int),  # id → transaction_id
        "type": ("type", to_str),
        "motif": ("motif", to_str),
        "description": ("description", to_str),
        "amount": ("amount", to_decimal),
        "date": ("date", parse_date),
        "method": ("method", to_str),
        "reference": ("reference", to_str),
        "created_at": ("created_at", parse_datetime),
    }),
    ("suppliers", Supplier, {
        "supplier_id": ("supplier_id", to_int),
        "name": ("name", to_str),
        "contact_person": ("contact_person", to_str),
        "email": ("email", to_str),
        "phone": ("phone", to_str),
        "address": ("address", to_str),
    }),
    ("supplier_invoices", SupplierInvoice, {
        "invoice_id": ("invoice_id", to_int),
        "supplier_id": ("supplier_id", lambda v: to_int(v, None)),
        "invoice_number": ("invoice_number", to_str),
        "invoice_date": ("invoice_date", parse_datetime),
        "due_date": ("due_date", parse_datetime),
        "description": ("description", to_str),
        "amount": ("amount", lambda v: to_decimal(v) if v is not None else None),
        "paid_amount": ("paid_amount", to_decimal),
        "remaining_amount": ("remaining_amount", to_decimal),
        "status": ("status", lambda v: to_str(v) or "pending"),
        "payment_method": ("payment_method", to_str),
        "notes": ("notes", to_str),
        "pdf_path": ("pdf_path", to_str),
        "pdf_filename": ("pdf_filename", to_str),
        "created_at": ("created_at", parse_datetime),
        "updated_at": ("updated_at", parse_datetime),
    }),
    ("supplier_invoice_payments", SupplierInvoicePayment, {
        "payment_id": ("payment_id", to_int),
        "supplier_invoice_id": ("supplier_invoice_id", to_int),
        "amount": ("amount", to_decimal),
        "payment_date": ("payment_date", parse_datetime),
        "payment_method": ("payment_method", to_str),
        "reference": ("reference", to_str),
        "notes": ("notes", to_str),
        "created_at": ("created_at", parse_datetime),
    }),
    ("supplier_debts", SupplierDebt, {
        "debt_id": ("debt_id", to_int),
        "supplier_id": ("supplier_id", lambda v: to_int(v, None)),
        "reference": ("reference", lambda v: to_str(v) or ""),
        "date": ("date", parse_datetime),
        "due_date": ("due_date", parse_datetime),
        "amount": ("amount", to_decimal),
        "paid_amount": ("paid_amount", to_decimal),
        "remaining_amount": ("remaining_amount", to_decimal),
        "status": ("status", lambda v: to_str(v) or "pending"),
        "description": ("description", to_str),
        "notes": ("notes", to_str),
        "created_at": ("created_at", parse_datetime),
    }),
    ("supplier_debt_payments", SupplierDebtPayment, {
        "payment_id": ("payment_id", to_int),
        "debt_id": ("debt_id", to_int),
        "amount": ("amount", to_decimal),
        "payment_date": ("payment_date", parse_datetime),
        "payment_method": ("payment_method", to_str),
        "reference": ("reference", to_str),
        "notes": ("notes", to_str),
    }),
    ("purchase_orders", PurchaseOrder, {
        "order_id": ("order_id", to_int),
        "supplier_id": ("supplier_id", to_int),
        "order_date": ("order_date", parse_datetime),
        "status": ("status", lambda v: to_str(v) or "PENDING"),
        "total_amount": ("total_amount", to_decimal),
    }),
    ("purchase_order_items", PurchaseOrderItem, {
        "item_id": ("item_id", to_int),
        "order_id": ("order_id", to_int),
        "product_id": ("product_id", to_int),
        "quantity": ("quantity", to_int),
        "unit_price": ("unit_price", to_decimal),
    }),
    ("invoices", Invoice, {
        "invoice_id": ("invoice_id", to_int),
        "invoice_number": ("invoice_number", to_str),
        "client_id": ("client_id", to_int),
        "quotation_id": ("quotation_id", lambda v: to_int(v, None)),
        "date": ("date", parse_datetime),
        "due_date": ("due_date", parse_datetime),
        "status": ("status", lambda v: to_str(v) or "en attente"),
        "payment_method": ("payment_method", to_str),
        "subtotal": ("subtotal", to_decimal),
        "tax_rate": ("tax_rate", to_decimal),
        "tax_amount": ("tax_amount", to_decimal),
        "total": ("total", to_decimal),
        "paid_amount": ("paid_amount", to_decimal),
        "remaining_amount": ("remaining_amount", to_decimal),
        "notes": ("notes", to_str),
        "show_tax": ("show_tax", to_bool),
        "price_display": ("price_display", lambda v: to_str(v) or "TTC"),
        "has_warranty": ("has_warranty", to_bool),
        "warranty_duration": ("warranty_duration", lambda v: to_int(v, None)),
        "warranty_start_date": ("warranty_start_date", parse_date),
        "warranty_end_date": ("warranty_end_date", parse_date),
        "show_item_prices": ("show_item_prices", lambda v: to_bool(v) if v is not None else True),
        "show_section_totals": ("show_section_totals", lambda v: to_bool(v) if v is not None else True),
        "created_at": ("created_at", parse_datetime),
        "created_by": ("created_by", lambda v: to_int(v, None)),
    }),
    ("invoice_items", InvoiceItem, {
        "item_id": ("item_id", to_int),
        "invoice_id": ("invoice_id", to_int),
        "product_id": ("product_id", lambda v: to_int(v, None)),
        "product_name": ("product_name", lambda v: to_str(v) or ""),
        "quantity": ("quantity", to_int),
        "price": ("price", to_decimal),
        "total": ("total", to_decimal),
    }),
    ("invoice_payments", InvoicePayment, {
        "payment_id": ("payment_id", to_int),
        "invoice_id": ("invoice_id", to_int),
        "amount": ("amount", to_decimal),
        "payment_date": ("payment_date", parse_datetime),
        "payment_method": ("payment_method", to_str),
        "reference": ("reference", to_str),
        "notes": ("notes", to_str),
    }),
    ("quotations", Quotation, {
        "quotation_id": ("quotation_id", to_int),
        "quotation_number": ("quotation_number", to_str),
        "client_id": ("client_id", to_int),
        "date": ("date", parse_datetime),
        "expiry_date": ("expiry_date", parse_datetime),
        "status": ("status", lambda v: to_str(v) or "en attente"),
        "is_sent": ("is_sent", to_bool),
        "subtotal": ("subtotal", to_decimal),
        "tax_rate": ("tax_rate", to_decimal),
        "tax_amount": ("tax_amount", to_decimal),
        "total": ("total", to_decimal),
        "notes": ("notes", to_str),
        "show_item_prices": ("show_item_prices", lambda v: to_bool(v) if v is not None else True),
        "show_section_totals": ("show_section_totals", lambda v: to_bool(v) if v is not None else True),
        "created_at": ("created_at", parse_datetime),
        "created_by": ("created_by", lambda v: to_int(v, None)),
    }),
    ("quotation_items", QuotationItem, {
        "item_id": ("item_id", to_int),
        "quotation_id": ("quotation_id", to_int),
        "product_id": ("product_id", lambda v: to_int(v, None)),
        "product_name": ("product_name", lambda v: to_str(v) or ""),
        "quantity": ("quantity", to_int),
        "price": ("price", to_decimal),
        "total": ("total", to_decimal),
    }),
    ("delivery_notes", DeliveryNote, {
        "delivery_note_id": ("delivery_note_id", to_int),
        "delivery_note_number": ("delivery_note_number", to_str),
        "invoice_id": ("invoice_id", lambda v: to_int(v, None)),
        "client_id": ("client_id", lambda v: to_int(v, None)),
        "date": ("date", parse_datetime),
        "delivery_date": ("delivery_date", parse_datetime),
        "status": ("status", lambda v: to_str(v) or "en_preparation"),
        "delivery_address": ("delivery_address", to_str),
        "delivery_contact": ("delivery_contact", to_str),
        "delivery_phone": ("delivery_phone", to_str),
        "subtotal": ("subtotal", to_decimal),
        "tax_rate": ("tax_rate", to_decimal),
        "tax_amount": ("tax_amount", to_decimal),
        "total": ("total", to_decimal),
        "transport_cost": ("transport_cost", to_decimal),
        "notes": ("notes", to_str),
        "delivered_by": ("delivered_by", to_str),
        "signature_received": ("signature_received", to_bool),
        "signature_data_url": ("signature_data_url", to_str),
        "created_at": ("created_at", parse_datetime),
        "delivered_at": ("delivered_at", parse_datetime),
    }),
    ("delivery_note_items", DeliveryNoteItem, {
        "item_id": ("item_id", to_int),
        "delivery_note_id": ("delivery_note_id", to_int),
        "product_id": ("product_id", lambda v: to_int(v, None)),
        "product_name": ("product_name", lambda v: to_str(v) or ""),
        "quantity": ("quantity", to_int),
        "price": ("price", to_decimal),
        "delivered_quantity": ("delivered_quantity", to_int),
        "serial_numbers": ("serial_numbers", to_str),
    }),
    ("user_settings", UserSettings, {
        "setting_id": ("setting_id", to_int),
        "user_id": ("user_id", lambda v: to_int(v, None)),
        "setting_key": ("setting_key", to_str),
        "setting_value": ("setting_value", to_str),
        "created_at": ("created_at", parse_datetime),
        "updated_at": ("updated_at", parse_datetime),
    }),
    ("scan_history", ScanHistory, {
        "scan_id": ("scan_id", to_int),
        "user_id": ("user_id", to_int),
        "barcode": ("barcode", lambda v: to_str(v) or ""),
        "product_name": ("product_name", to_str),
        "scan_type": ("scan_type", to_str),
        "result_data": ("result_data", to_str),
        "scanned_at": ("scanned_at", parse_datetime),
    }),
    ("app_cache", AppCache, {
        "cache_id": ("cache_id", to_int),
        "cache_key": ("cache_key", to_str),
        "cache_value": ("cache_value", to_str),
        "expires_at": ("expires_at", parse_datetime),
        "created_at": ("created_at", parse_datetime),
        "updated_at": ("updated_at", parse_datetime),
    }),
    ("daily_client_requests", DailyClientRequest, {
        "request_id": ("request_id", to_int),
        "client_id": ("client_id", lambda v: to_int(v, None)),
        "client_name": ("client_name", lambda v: to_str(v) or ""),
        "client_phone": ("client_phone", to_str),
        "product_description": ("product_description", lambda v: to_str(v) or ""),
        "request_date": ("request_date", parse_date),
        "status": ("status", lambda v: to_str(v) or "pending"),
        "notes": ("notes", to_str),
        "created_at": ("created_at", parse_datetime),
        "updated_at": ("updated_at", parse_datetime),
    }),
    ("daily_sales", DailySale, {
        "sale_id": ("sale_id", to_int),
        "client_id": ("client_id", lambda v: to_int(v, None)),
        "client_name": ("client_name", lambda v: to_str(v) or ""),
        "product_id": ("product_id", lambda v: to_int(v, None)),
        "product_name": ("product_name", lambda v: to_str(v) or ""),
        "variant_id": ("variant_id", lambda v: to_int(v, None)),
        "variant_imei": ("variant_imei", to_str),
        "variant_barcode": ("variant_barcode", to_str),
        "variant_condition": ("variant_condition", to_str),
        "quantity": ("quantity", to_int),
        "unit_price": ("unit_price", to_decimal),
        "total_amount": ("total_amount", to_decimal),
        "sale_date": ("sale_date", parse_date),
        "payment_method": ("payment_method", lambda v: to_str(v) or "espece"),
        "invoice_id": ("invoice_id", lambda v: to_int(v, None)),
        "notes": ("notes", to_str),
        "created_at": ("created_at", parse_datetime),
    }),
    ("daily_purchases", DailyPurchase, {
        "purchase_id": ("id", to_int),  # id → purchase_id
        "date": ("date", parse_date),
        "category": ("category", lambda v: to_str(v) or ""),
        "supplier": ("supplier", to_str),
        "description": ("description", to_str),
        "amount": ("amount", to_decimal),
        "payment_method": ("payment_method", lambda v: to_str(v) or "espece"),
        "reference": ("reference", to_str),
        "created_at": ("created_at", parse_datetime),
        "created_by": ("created_by", lambda v: to_int(v, None)),
    }),
    ("daily_purchase_categories", DailyPurchaseCategory, {
        "category_pk": ("id", to_int),  # id → category_pk
        "name": ("name", to_str),
        "created_at": ("created_at", parse_datetime),
    }),
    ("migrations", Migration, {
        "migration_id": ("migration_id", to_int),
        "name": ("name", lambda v: to_str(v) or ""),
        "type": ("type", lambda v: to_str(v) or ""),
        "status": ("status", lambda v: to_str(v) or "pending"),
        "created_at": ("created_at", parse_datetime),
        "completed_at": ("completed_at", parse_datetime),
        "total_records": ("total_records", to_int),
        "processed_records": ("processed_records", to_int),
        "success_records": ("success_records", to_int),
        "error_records": ("error_records", to_int),
        "file_name": ("file_name", to_str),
        "description": ("description", to_str),
        "error_message": ("error_message", to_str),
        "created_by": ("created_by", lambda v: to_int(v, None)),
    }),
    ("migration_logs", MigrationLog, {
        "log_id": ("log_id", to_int),
        "migration_id": ("migration_id", to_int),
        "timestamp": ("timestamp", parse_datetime),
        "level": ("level", lambda v: to_str(v) or "info"),
        "message": ("message", lambda v: to_str(v) or ""),
    }),
    ("maintenances", Maintenance, {
        "maintenance_id": ("maintenance_id", to_int),
        "maintenance_number": ("maintenance_number", to_str),
        "client_id": ("client_id", lambda v: to_int(v, None)),
        "client_name": ("client_name", lambda v: to_str(v) or ""),
        "client_phone": ("client_phone", to_str),
        "client_email": ("client_email", to_str),
        "device_type": ("device_type", lambda v: to_str(v) or ""),
        "device_brand": ("device_brand", to_str),
        "device_model": ("device_model", to_str),
        "device_serial": ("device_serial", to_str),
        "device_description": ("device_description", to_str),
        "device_accessories": ("device_accessories", to_str),
        "device_condition": ("device_condition", to_str),
        "problem_description": ("problem_description", lambda v: to_str(v) or ""),
        "diagnosis": ("diagnosis", to_str),
        "work_done": ("work_done", to_str),
        "reception_date": ("reception_date", parse_datetime),
        "estimated_completion_date": ("estimated_completion_date", parse_date),
        "actual_completion_date": ("actual_completion_date", parse_date),
        "pickup_deadline": ("pickup_deadline", parse_date),
        "pickup_date": ("pickup_date", parse_date),
        "status": ("status", lambda v: to_str(v) or "received"),
        "priority": ("priority", lambda v: to_str(v) or "normal"),
        "estimated_cost": ("estimated_cost", lambda v: to_decimal(v) if v is not None else None),
        "final_cost": ("final_cost", lambda v: to_decimal(v) if v is not None else None),
        "advance_paid": ("advance_paid", to_decimal),
        "warranty_days": ("warranty_days", to_int),
        "liability_waived": ("liability_waived", to_bool),
        "liability_waived_date": ("liability_waived_date", parse_date),
        "reminder_sent": ("reminder_sent", to_bool),
        "reminder_sent_date": ("reminder_sent_date", parse_datetime),
        "technician_id": ("technician_id", lambda v: to_int(v, None)),
        "notes": ("notes", to_str),
        "internal_notes": ("internal_notes", to_str),
        "created_at": ("created_at", parse_datetime),
        "updated_at": ("updated_at", parse_datetime),
    }),
]

# Counter name → (sqlite_table, pk_column) for initialising the counters collection
COUNTER_MAP = {
    "users": ("users", "user_id"),
    "clients": ("clients", "client_id"),
    "client_debts": ("client_debts", "debt_id"),
    "client_debt_payments": ("client_debt_payments", "payment_id"),
    "categories": ("categories", "category_id"),
    "category_attributes": ("category_attributes", "attribute_id"),
    "category_attribute_values": ("category_attribute_values", "value_id"),
    "products": ("products", "product_id"),
    "product_variants": ("product_variants", "variant_id"),
    "product_variant_attributes": ("product_variant_attributes", "attribute_id"),
    "product_serial_numbers": ("product_serial_numbers", "serial_number_id"),
    "stock_movements": ("stock_movements", "movement_id"),
    "bank_transactions": ("bank_transactions", "id"),
    "suppliers": ("suppliers", "supplier_id"),
    "supplier_invoices": ("supplier_invoices", "invoice_id"),
    "supplier_invoice_payments": ("supplier_invoice_payments", "payment_id"),
    "supplier_debts": ("supplier_debts", "debt_id"),
    "supplier_debt_payments": ("supplier_debt_payments", "payment_id"),
    "purchase_orders": ("purchase_orders", "order_id"),
    "purchase_order_items": ("purchase_order_items", "item_id"),
    "invoices": ("invoices", "invoice_id"),
    "invoice_items": ("invoice_items", "item_id"),
    "invoice_payments": ("invoice_payments", "payment_id"),
    "quotations": ("quotations", "quotation_id"),
    "quotation_items": ("quotation_items", "item_id"),
    "delivery_notes": ("delivery_notes", "delivery_note_id"),
    "delivery_note_items": ("delivery_note_items", "item_id"),
    "user_settings": ("user_settings", "setting_id"),
    "scan_history": ("scan_history", "scan_id"),
    "app_cache": ("app_cache", "cache_id"),
    "daily_client_requests": ("daily_client_requests", "request_id"),
    "daily_sales": ("daily_sales", "sale_id"),
    "daily_purchases": ("daily_purchases", "id"),
    "daily_purchase_categories": ("daily_purchase_categories", "id"),
    "migrations": ("migrations", "migration_id"),
    "migration_logs": ("migration_logs", "log_id"),
    "maintenances": ("maintenances", "maintenance_id"),
}


# ─────────────────── main migration logic ───────────────────

async def migrate(sqlite_path: str, mongo_url: str):
    # Connect to SQLite
    print(f"\n📂 Opening SQLite: {sqlite_path}")
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row

    # Connect to MongoDB
    print(f"🔌 Connecting to MongoDB: {mongo_url}")
    client = AsyncIOMotorClient(mongo_url)
    db_name = mongo_url.rsplit("/", 1)[-1].split("?")[0] or "geektech"
    database = client[db_name]

    # Initialize Beanie
    await init_beanie(database=database, document_models=ALL_DOCUMENT_MODELS)
    print("✅ Beanie initialized\n")

    total_migrated = 0
    total_errors = 0
    results = []

    for table_name, model_cls, field_map in MIGRATIONS:
        collection_name = model_cls.Settings.name

        # Check if collection already has data
        existing = await model_cls.find().count()
        if existing > 0:
            print(f"⚠️  {collection_name} already has {existing} documents — SKIPPING")
            results.append((table_name, collection_name, 0, 0, f"skipped ({existing} existing)"))
            continue

        # Read from SQLite
        try:
            cursor = conn.execute(f"SELECT * FROM [{table_name}]")
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
        except Exception as e:
            print(f"❌ Error reading {table_name}: {e}")
            results.append((table_name, collection_name, 0, 0, f"error: {e}"))
            total_errors += 1
            continue

        if not rows:
            print(f"  ○ {table_name} → {collection_name}: 0 rows (empty)")
            results.append((table_name, collection_name, 0, 0, "empty"))
            continue

        # Transform and insert
        docs = []
        row_errors = 0
        for row in rows:
            row_dict = dict(row)
            try:
                doc_data = {}
                for mongo_field, (sqlite_col, converter) in field_map.items():
                    raw = row_dict.get(sqlite_col)
                    doc_data[mongo_field] = converter(raw)

                # Provide defaults for required datetime fields
                now = datetime.utcnow()
                if "created_at" in field_map and doc_data.get("created_at") is None:
                    doc_data["created_at"] = now
                if "updated_at" in field_map and doc_data.get("updated_at") is None:
                    doc_data["updated_at"] = now
                if "date" in field_map and doc_data.get("date") is None:
                    doc_data["date"] = now

                doc = model_cls(**doc_data)
                docs.append(doc)
            except Exception as e:
                row_errors += 1
                if row_errors <= 3:
                    print(f"    ⚠ Row error in {table_name}: {e}")

        # Batch insert
        if docs:
            try:
                await model_cls.insert_many(docs)
            except Exception as e:
                # Fallback: insert one by one
                print(f"    ⚠ Batch insert failed for {collection_name}, trying one-by-one: {e}")
                for doc in docs:
                    try:
                        await doc.insert()
                    except Exception as e2:
                        row_errors += 1
                        if row_errors <= 5:
                            print(f"      ⚠ Insert error: {e2}")

        inserted = len(docs) - row_errors
        total_migrated += inserted
        total_errors += row_errors
        status = "✅" if row_errors == 0 else "⚠️"
        print(f"  {status} {table_name} → {collection_name}: {len(rows)} rows → {inserted} docs" +
              (f" ({row_errors} errors)" if row_errors else ""))
        results.append((table_name, collection_name, len(rows), inserted, f"{row_errors} errors" if row_errors else "ok"))

    # ── Initialise counters ──
    print("\n🔢 Initialising counters collection...")
    counters = database["counters"]
    for counter_name, (table_name, pk_col) in COUNTER_MAP.items():
        try:
            cursor = conn.execute(f"SELECT MAX([{pk_col}]) FROM [{table_name}]")
            max_val = cursor.fetchone()[0] or 0
            await counters.update_one(
                {"_id": counter_name},
                {"$set": {"seq": int(max_val)}},
                upsert=True,
            )
            if max_val > 0:
                print(f"  ✅ {counter_name}: seq = {max_val}")
        except Exception as e:
            print(f"  ⚠️  {counter_name}: {e}")

    conn.close()

    # ── Summary ──
    print(f"\n{'='*60}")
    print(f"Migration complete!")
    print(f"  Total migrated: {total_migrated}")
    print(f"  Total errors:   {total_errors}")
    print(f"{'='*60}")

    # Verification: compare counts
    print(f"\n📊 Verification — SQLite vs MongoDB counts:")
    conn2 = sqlite3.connect(sqlite_path)
    mismatch = False
    for table_name, model_cls, _ in MIGRATIONS:
        cursor = conn2.execute(f"SELECT COUNT(*) FROM [{table_name}]")
        sqlite_count = cursor.fetchone()[0]
        mongo_count = await model_cls.find().count()
        match = "✅" if sqlite_count == mongo_count else "❌"
        if sqlite_count != mongo_count:
            mismatch = True
        if sqlite_count > 0 or mongo_count > 0:
            print(f"  {match} {table_name}: SQLite={sqlite_count}, MongoDB={mongo_count}")
    conn2.close()

    if mismatch:
        print("\n⚠️  Some counts don't match. Check errors above.")
    else:
        print("\n✅ All counts match! Migration verified.")

    return total_migrated, total_errors


def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite to MongoDB")
    parser.add_argument("--sqlite", default="/opt/backups/geektech_20260207.db",
                        help="Path to SQLite database file")
    parser.add_argument("--mongo", default=os.getenv("DATABASE_URL", "mongodb://localhost:27017/geektech"),
                        help="MongoDB connection URL")
    args = parser.parse_args()

    if not os.path.exists(args.sqlite):
        print(f"❌ SQLite file not found: {args.sqlite}")
        sys.exit(1)

    asyncio.run(migrate(args.sqlite, args.mongo))


if __name__ == "__main__":
    main()
