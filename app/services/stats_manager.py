from __future__ import annotations

from typing import Any, Dict, Optional
from datetime import date
import json

from ..database import AppCache, Invoice, SupplierInvoice, Quotation, get_next_id


async def _get_cache(key: str) -> Optional[Dict[str, Any]]:
    try:
        row = await AppCache.find_one(AppCache.cache_key == key)
        if not row:
            return None
        return json.loads(row.cache_value or "{}")
    except Exception:
        return None


async def _set_cache(key: str, value: Dict[str, Any]) -> None:
    try:
        payload = json.dumps(value, default=str)
        existing = await AppCache.find_one(AppCache.cache_key == key)
        if existing:
            existing.cache_value = payload
            await existing.save()
        else:
            new_id = await get_next_id("app_cache")
            rec = AppCache(cache_id=new_id, cache_key=key, cache_value=payload, expires_at=None)
            await rec.insert()
    except Exception:
        pass


INVOICES_STATS_KEY = "invoices_stats"
QUOTATIONS_STATS_KEY = "quotations_stats"


async def get_invoices_stats() -> Dict[str, Any]:
    cached = await _get_cache(INVOICES_STATS_KEY)
    if cached:
        return cached
    return await recompute_invoices_stats()


async def recompute_invoices_stats() -> Dict[str, Any]:
    today = date.today()

    total_invoices = await Invoice.find().count()
    paid_invoices = await Invoice.find({"status": {"$in": ["payée", "PAID"]}}).count()
    pending_invoices = await Invoice.find({"status": {"$in": ["en attente", "SENT", "DRAFT", "OVERDUE", "partiellement payée"]}}).count()

    # Monthly revenue (paid invoices this month)
    pipeline_monthly = [
        {"$match": {
            "status": {"$in": ["payée", "PAID"]},
            "$expr": {
                "$and": [
                    {"$eq": [{"$month": "$date"}, today.month]},
                    {"$eq": [{"$year": "$date"}, today.year]},
                ]
            }
        }},
        {"$group": {"_id": None, "total": {"$sum": "$total"}}}
    ]
    monthly_result = await Invoice.aggregate(pipeline_monthly).to_list()
    monthly_revenue_gross = float(monthly_result[0]["total"]) if monthly_result else 0.0

    # Monthly supplier payments
    pipeline_supplier = [
        {"$match": {
            "$expr": {
                "$and": [
                    {"$eq": [{"$month": "$invoice_date"}, today.month]},
                    {"$eq": [{"$year": "$invoice_date"}, today.year]},
                ]
            }
        }},
        {"$group": {"_id": None, "total": {"$sum": "$paid_amount"}}}
    ]
    supplier_result = await SupplierInvoice.aggregate(pipeline_supplier).to_list()
    monthly_supplier_payments = float(supplier_result[0]["total"]) if supplier_result else 0.0

    monthly_revenue = monthly_revenue_gross - monthly_supplier_payments

    # Total revenue (all paid invoices)
    pipeline_total_rev = [
        {"$match": {"status": {"$in": ["payée", "PAID"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$total"}}}
    ]
    total_rev_result = await Invoice.aggregate(pipeline_total_rev).to_list()
    total_revenue = float(total_rev_result[0]["total"]) if total_rev_result else 0.0

    # Unpaid amount
    pipeline_unpaid = [
        {"$match": {"status": {"$in": ["en attente", "partiellement payée", "OVERDUE"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$remaining_amount"}}}
    ]
    unpaid_result = await Invoice.aggregate(pipeline_unpaid).to_list()
    unpaid_amount = float(unpaid_result[0]["total"]) if unpaid_result else 0.0

    result = {
        "total_invoices": int(total_invoices),
        "paid_invoices": int(paid_invoices),
        "pending_invoices": int(pending_invoices),
        "monthly_revenue": float(monthly_revenue),
        "total_revenue": float(total_revenue),
        "unpaid_amount": float(unpaid_amount),
    }
    await _set_cache(INVOICES_STATS_KEY, result)
    return result


async def get_quotations_stats() -> Dict[str, Any]:
    cached = await _get_cache(QUOTATIONS_STATS_KEY)
    if cached:
        return cached
    return await recompute_quotations_stats()


async def recompute_quotations_stats() -> Dict[str, Any]:
    total = await Quotation.find().count()
    total_accepted = await Quotation.find(Quotation.status == 'accepté').count()
    total_pending = await Quotation.find(Quotation.status == 'en attente').count()

    pipeline_value = [
        {"$group": {"_id": None, "total": {"$sum": "$total"}}}
    ]
    value_result = await Quotation.aggregate(pipeline_value).to_list()
    total_value = float(value_result[0]["total"]) if value_result else 0.0

    result = {
        "total": int(total),
        "total_accepted": int(total_accepted),
        "total_pending": int(total_pending),
        "total_value": float(total_value),
    }
    await _set_cache(QUOTATIONS_STATS_KEY, result)
    return result
