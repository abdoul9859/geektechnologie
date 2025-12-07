from __future__ import annotations

from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date

from ..database import AppCache, Invoice, SupplierInvoice, Quotation


def _get_cache(db: Session, key: str) -> Optional[Dict[str, Any]]:
    try:
        row = db.query(AppCache).filter(AppCache.cache_key == key).first()
        if not row:
            return None
        import json
        return json.loads(row.cache_value or "{}")
    except Exception:
        return None


def _set_cache(db: Session, key: str, value: Dict[str, Any]) -> None:
    try:
        import json
        payload = json.dumps(value, default=str)
        existing = db.query(AppCache).filter(AppCache.cache_key == key).first()
        if existing:
            existing.cache_value = payload
        else:
            db.add(AppCache(cache_key=key, cache_value=payload, expires_at=None))
        db.commit()
    except Exception:
        db.rollback()


INVOICES_STATS_KEY = "invoices_stats"
QUOTATIONS_STATS_KEY = "quotations_stats"


def get_invoices_stats(db: Session) -> Dict[str, Any]:
    cached = _get_cache(db, INVOICES_STATS_KEY)
    if cached:
        return cached
    return recompute_invoices_stats(db)


def recompute_invoices_stats(db: Session) -> Dict[str, Any]:
    today = date.today()

    total_invoices = db.query(func.count(Invoice.invoice_id)).scalar() or 0
    paid_invoices = db.query(func.count(Invoice.invoice_id)).filter(Invoice.status.in_(["payée", "PAID"])) .scalar() or 0
    pending_invoices = db.query(func.count(Invoice.invoice_id)).filter(Invoice.status.in_(["en attente", "SENT", "DRAFT", "OVERDUE", "partiellement payée"])) .scalar() or 0

    # Revenus
    monthly_revenue_gross = db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(
        func.extract('month', Invoice.date) == today.month,
        func.extract('year', Invoice.date) == today.year,
        Invoice.status.in_(["payée", "PAID"])  # payées uniquement
    ).scalar() or 0

    monthly_supplier_payments = db.query(func.coalesce(func.sum(SupplierInvoice.paid_amount), 0)).filter(
        func.extract('month', SupplierInvoice.invoice_date) == today.month,
        func.extract('year', SupplierInvoice.invoice_date) == today.year
    ).scalar() or 0

    monthly_revenue = float(monthly_revenue_gross) - float(monthly_supplier_payments)

    total_revenue_gross = db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(Invoice.status.in_(["payée", "PAID"])) .scalar() or 0
    total_revenue = float(total_revenue_gross)

    unpaid_amount = db.query(func.coalesce(func.sum(Invoice.remaining_amount), 0)).filter(Invoice.status.in_(["en attente", "partiellement payée", "OVERDUE"])) .scalar() or 0

    result = {
        "total_invoices": int(total_invoices),
        "paid_invoices": int(paid_invoices),
        "pending_invoices": int(pending_invoices),
        "monthly_revenue": float(monthly_revenue),
        "total_revenue": float(total_revenue),
        "unpaid_amount": float(unpaid_amount),
    }
    _set_cache(db, INVOICES_STATS_KEY, result)
    return result


def get_quotations_stats(db: Session) -> Dict[str, Any]:
    cached = _get_cache(db, QUOTATIONS_STATS_KEY)
    if cached:
        return cached
    return recompute_quotations_stats(db)


def recompute_quotations_stats(db: Session) -> Dict[str, Any]:
    total = db.query(func.count(Quotation.quotation_id)).scalar() or 0
    total_accepted = db.query(func.count(Quotation.quotation_id)).filter(Quotation.status == 'accepté').scalar() or 0
    total_pending = db.query(func.count(Quotation.quotation_id)).filter(Quotation.status == 'en attente').scalar() or 0
    total_value = db.query(func.coalesce(func.sum(Quotation.total), 0)).scalar() or 0

    result = {
        "total": int(total),
        "total_accepted": int(total_accepted),
        "total_pending": int(total_pending),
        "total_value": float(total_value),
    }
    _set_cache(db, QUOTATIONS_STATS_KEY, result)
    return result


