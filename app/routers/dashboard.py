from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timedelta, date
from collections import defaultdict
import time
import logging

from ..database import User
from ..database import (
    Invoice, InvoiceItem, InvoicePayment, Quotation, Product, ProductVariant,
    Client, StockMovement, SupplierInvoice, SupplierInvoicePayment,
    DailyPurchase,
)
from ..auth import get_current_user

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Cache simple pour les stats (30 secondes pour faciliter les tests)
_cache = {}
_cache_duration = 30  # 30 secondes pour faciliter les tests et debuggage

def _get_cache_key(*args):
    """Genere une cle de cache basee sur les arguments"""
    return "|".join(str(arg) for arg in args)

def _is_cache_valid(cache_entry):
    """Verifie si l'entree de cache est encore valide"""
    return cache_entry and (time.time() - cache_entry['timestamp']) < _cache_duration

async def _get_cached_or_compute(cache_key, compute_func):
    """Recupere depuis le cache ou calcule et met en cache (async version)"""
    if cache_key in _cache and _is_cache_valid(_cache[cache_key]):
        return _cache[cache_key]['data']

    # Calculer et mettre en cache
    result = await compute_func()
    _cache[cache_key] = {
        'data': result,
        'timestamp': time.time()
    }
    return result

def _safe_date(v):
    """Extract date from datetime-like or return as-is."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return v

@router.get("/stats")
async def get_dashboard_stats(
    force_refresh: bool = False,
    current_user = Depends(get_current_user)
):
    """
    Endpoint optimise pour le dashboard - retourne toutes les stats essentielles
    en une seule requete avec cache de 30 secondes
    """
    try:
        cache_key = _get_cache_key("dashboard_stats", date.today().isoformat())

        # Si force_refresh est demande, vider le cache
        if force_refresh:
            global _cache
            _cache.clear()

        async def compute_stats():
            today = date.today()
            now = datetime.now()
            month_start = datetime(today.year, today.month, 1)
            since_30 = now - timedelta(days=30)
            since_90 = now - timedelta(days=90)
            paid_statuses = ["payee", "PAID"]
            pending_statuses = ["en attente", "SENT", "DRAFT", "OVERDUE", "partiellement payee"]

            # ---- Stock counts (2 queries) ----
            products_with_stock = await Product.find({"quantity": {"$gt": 0}}).count()
            available_variant_pids = await ProductVariant.find(
                {"is_sold": False}
            ).to_list()
            pids_with_avail = set(v.product_id for v in available_variant_pids)
            # Products with zero qty but available variants
            zero_qty_with_variants = await Product.find(
                {"quantity": {"$lte": 0}, "product_id": {"$in": list(pids_with_avail)}}
            ).count() if pids_with_avail else 0
            total_stock = products_with_stock + zero_qty_with_variants

            out_of_stock = await Product.find(
                {"$and": [{"quantity": {"$lte": 0}}, {"product_id": {"$nin": list(pids_with_avail)}}]}
            ).count()
            low_stock = await Product.find(
                {"quantity": {"$gt": 0, "$lte": 3}}
            ).count()

            # ---- Invoice stats (aggregation) ----
            pending_invoices = await Invoice.find(
                {"status": {"$in": pending_statuses}}
            ).count()

            # Monthly revenue (paid invoices this month)
            rev_pipeline = [
                {"$match": {"status": {"$in": paid_statuses}, "date": {"$gte": month_start}}},
                {"$group": {"_id": None, "total": {"$sum": "$total"}}}
            ]
            rev_agg = await Invoice.aggregate(rev_pipeline).to_list()
            monthly_revenue_gross = float(str(rev_agg[0]["total"])) if rev_agg else 0

            # Monthly purchases
            purch_pipeline = [
                {"$match": {"$or": [
                    {"date": {"$gte": month_start}},
                    {"created_at": {"$gte": month_start}}
                ]}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]
            purch_agg = await DailyPurchase.aggregate(purch_pipeline).to_list()
            monthly_purchases = float(str(purch_agg[0]["total"])) if purch_agg else 0

            # Monthly supplier payments
            sup_pipeline = [
                {"$match": {"invoice_date": {"$gte": month_start}}},
                {"$group": {"_id": None, "total": {"$sum": "$paid_amount"}}}
            ]
            sup_agg = await SupplierInvoice.aggregate(sup_pipeline).to_list()
            monthly_supplier_payments = float(str(sup_agg[0]["total"])) if sup_agg else 0

            monthly_revenue = monthly_revenue_gross - monthly_supplier_payments - monthly_purchases

            # Unpaid amount
            unpaid_pipeline = [
                {"$match": {"remaining_amount": {"$gt": 0}}},
                {"$group": {"_id": None, "total": {"$sum": "$remaining_amount"}}}
            ]
            unpaid_agg = await Invoice.aggregate(unpaid_pipeline).to_list()
            unpaid_amount = float(str(unpaid_agg[0]["total"])) if unpaid_agg else 0

            # ---- 30-day KPIs (aggregation) ----
            rev30_pipeline = [
                {"$match": {"status": {"$in": paid_statuses}, "date": {"$gte": since_30}}},
                {"$group": {"_id": None, "total": {"$sum": "$total"}, "count": {"$sum": 1}}}
            ]
            rev30_agg = await Invoice.aggregate(rev30_pipeline).to_list()
            total_revenue_30d_gross = float(str(rev30_agg[0]["total"])) if rev30_agg else 0
            num_invoices_30d = int(rev30_agg[0]["count"]) if rev30_agg else 0

            purch30_pipeline = [
                {"$match": {"$or": [
                    {"date": {"$gte": since_30}},
                    {"created_at": {"$gte": since_30}}
                ]}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]
            purch30_agg = await DailyPurchase.aggregate(purch30_pipeline).to_list()
            purchases_30d = float(str(purch30_agg[0]["total"])) if purch30_agg else 0

            sup30_pipeline = [
                {"$match": {"payment_date": {"$gte": since_30}}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]
            sup30_agg = await SupplierInvoicePayment.aggregate(sup30_pipeline).to_list()
            supplier_payments_30d = float(str(sup30_agg[0]["total"])) if sup30_agg else 0

            total_revenue_30d = total_revenue_30d_gross - supplier_payments_30d - purchases_30d
            avg_ticket = float(total_revenue_30d / num_invoices_30d) if num_invoices_30d > 0 else 0.0

            # Conversion rate (30 days)
            quotes_30d = await Quotation.find({"date": {"$gte": since_30}}).count()
            converted_quotes_30d_list = await Invoice.find(
                {"quotation_id": {"$ne": None}, "date": {"$gte": since_30}}
            ).to_list()
            converted_quotes_30d = len(set(inv.quotation_id for inv in converted_quotes_30d_list))
            conversion_rate = float((converted_quotes_30d / quotes_30d) * 100) if quotes_30d > 0 else 0.0

            # Active customers (90 days)
            active_cust_list = await Invoice.find(
                {"client_id": {"$ne": None}, "date": {"$gte": since_90}}
            ).to_list()
            active_customers = len(set(inv.client_id for inv in active_cust_list))

            # Top 3 products by revenue (30 days) - need invoice_ids then items
            inv_ids_30d_list = await Invoice.find(
                {"date": {"$gte": since_30}}
            ).to_list()
            inv_ids_30d = [inv.invoice_id for inv in inv_ids_30d_list]

            top_pipeline = [
                {"$match": {"invoice_id": {"$in": inv_ids_30d}}},
                {"$group": {"_id": "$product_name", "revenue": {"$sum": "$total"}}},
                {"$sort": {"revenue": -1}},
                {"$limit": 3}
            ]
            top_agg = await InvoiceItem.aggregate(top_pipeline).to_list()
            top_products_list = [
                {"name": r["_id"] or "-", "revenue": float(str(r["revenue"]))}
                for r in top_agg
            ]

            # Payment methods breakdown (30 days)
            pay_pipeline = [
                {"$match": {"payment_date": {"$gte": since_30}}},
                {"$group": {"_id": "$payment_method", "amount": {"$sum": "$amount"}}},
                {"$sort": {"amount": -1}},
                {"$limit": 5}
            ]
            pay_agg = await InvoicePayment.aggregate(pay_pipeline).to_list()
            payments_breakdown = [
                {"method": r["_id"] or "Non specifie", "amount": float(str(r["amount"]))}
                for r in pay_agg
            ]

            return {
                # Stats de base
                "total_stock": int(total_stock),
                "pending_invoices": int(pending_invoices),
                "monthly_revenue": float(monthly_revenue),
                "monthly_revenue_gross": float(monthly_revenue_gross),
                "monthly_supplier_payments": float(monthly_supplier_payments),
                "monthly_daily_purchases": float(monthly_purchases),
                "unpaid_amount": float(unpaid_amount),

                # KPIs avances
                "avg_ticket": avg_ticket,
                "conversion_rate": conversion_rate,
                "critical_stock": int(low_stock + out_of_stock),
                "low_stock": int(low_stock),
                "out_of_stock": int(out_of_stock),
                "active_customers": int(active_customers),

                # Donnees detaillees
                "top_products": top_products_list,
                "payment_methods": payments_breakdown,

                # Meta
                "cached_at": datetime.now().isoformat(),
                "period_days": 30,
                "purchases_30d": float(purchases_30d),
                "revenue_30d_gross": float(total_revenue_30d_gross),
                "supplier_payments_30d": float(supplier_payments_30d)
            }

        result = await _get_cached_or_compute(cache_key, compute_stats)
        return result

    except Exception as e:
        logging.error(f"Erreur dashboard stats: {e}")
        # Retourner des donnees par defaut en cas d'erreur
        return {
            "total_stock": 0,
            "pending_invoices": 0,
            "monthly_revenue": 0.0,
            "unpaid_amount": 0.0,
            "avg_ticket": 0.0,
            "conversion_rate": 0.0,
            "critical_stock": 0,
            "low_stock": 0,
            "out_of_stock": 0,
            "active_customers": 0,
            "top_products": [],
            "payment_methods": [],
            "error": "Erreur lors du calcul des statistiques",
            "cached_at": datetime.now().isoformat(),
            "period_days": 30
        }

@router.get("/recent-movements")
async def get_recent_movements(
    limit: int = 5,
    current_user = Depends(get_current_user)
):
    """Mouvements de stock recents optimises"""
    try:
        cache_key = _get_cache_key("recent_movements", limit)

        async def compute_movements():
            movements = await StockMovement.find().sort(-StockMovement.created_at).limit(limit).to_list()

            return [
                {
                    "movement_id": m.movement_id,
                    "quantity": m.quantity,
                    "movement_type": m.movement_type,
                    "notes": m.notes,
                    "created_at": m.created_at.isoformat() if m.created_at else None
                }
                for m in movements
            ]

        result = await _get_cached_or_compute(cache_key, compute_movements)
        return result

    except Exception as e:
        logging.error(f"Erreur recent movements: {e}")
        return []

@router.get("/recent-invoices")
async def get_recent_invoices(
    limit: int = 5,
    current_user = Depends(get_current_user)
):
    """Factures recentes optimisees"""
    try:
        cache_key = _get_cache_key("recent_invoices", limit)

        async def compute_invoices():
            invoices = await Invoice.find().sort(-Invoice.created_at).limit(limit).to_list()

            return [
                {
                    "invoice_id": inv.invoice_id,
                    "invoice_number": inv.invoice_number,
                    "status": inv.status,
                    "total": float(inv.total or 0),
                    "date": inv.date.isoformat() if inv.date else None
                }
                for inv in invoices
            ]

        result = await _get_cached_or_compute(cache_key, compute_invoices)
        return result

    except Exception as e:
        logging.error(f"Erreur recent invoices: {e}")
        return []

@router.delete("/cache")
async def clear_dashboard_cache(
    current_user = Depends(get_current_user)
):
    """Vider le cache du dashboard (utile pour les admins)"""
    global _cache
    _cache.clear()
    return {"message": "Cache du dashboard vide avec succes"}

@router.get("/debug")
async def debug_dashboard_stats(
    current_user = Depends(get_current_user)
):
    """Debug des stats du dashboard pour diagnostiquer les problemes"""
    try:
        today = date.today()

        # Compter tous les produits
        all_products = await Product.find().to_list()
        total_products = len(all_products)

        # Compter les variantes disponibles par produit
        all_variants = await ProductVariant.find().to_list()
        variant_info_map = defaultdict(lambda: {"total": 0, "available": 0})
        for v in all_variants:
            variant_info_map[v.product_id]["total"] += 1
            if not v.is_sold:
                variant_info_map[v.product_id]["available"] += 1

        variants_info = [
            {
                "product_id": pid,
                "total_variants": info["total"],
                "available_variants": info["available"]
            }
            for pid, info in variant_info_map.items()
        ]

        # Factures du mois
        paid_statuses = ["payee", "PAID"]
        all_invoices = await Invoice.find().to_list()

        monthly_invoices = [
            inv for inv in all_invoices
            if inv.date and _safe_date(inv.date)
            and _safe_date(inv.date).month == today.month
            and _safe_date(inv.date).year == today.year
        ]
        monthly_paid_invoices = [
            inv for inv in monthly_invoices
            if inv.status in paid_statuses
        ]

        return {
            "date": today.isoformat(),
            "month": today.month,
            "year": today.year,
            "total_products": total_products,
            "variants_info": variants_info,
            "monthly_invoices_count": len(monthly_invoices),
            "monthly_paid_invoices_count": len(monthly_paid_invoices),
            "monthly_invoices": [
                {
                    "id": inv.invoice_id,
                    "number": inv.invoice_number,
                    "date": inv.date.isoformat() if inv.date else None,
                    "status": inv.status,
                    "total": float(inv.total or 0)
                }
                for inv in monthly_invoices
            ]
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/cache/info")
async def get_cache_info(
    current_user = Depends(get_current_user)
):
    """Informations sur le cache (debugging)"""
    cache_entries = []
    current_time = time.time()

    for key, entry in _cache.items():
        age_seconds = current_time - entry['timestamp']
        is_valid = age_seconds < _cache_duration

        cache_entries.append({
            "key": key,
            "age_seconds": int(age_seconds),
            "is_valid": is_valid,
            "expires_in": int(_cache_duration - age_seconds) if is_valid else 0
        })

    return {
        "cache_duration_seconds": _cache_duration,
        "total_entries": len(_cache),
        "entries": cache_entries
    }

@router.get("/sales-trend")
async def get_sales_trend(
    days: int = 7,
    current_user = Depends(get_current_user)
):
    """Tendance des ventes sur les N derniers jours"""
    try:
        cache_key = _get_cache_key("sales_trend", days)

        async def compute_trend():
            today = date.today()
            paid_statuses = ["payee", "PAID"]

            # Pre-fetch all invoices once
            all_invoices = await Invoice.find().to_list()

            # Build daily revenue map
            daily_map = defaultdict(float)
            for inv in all_invoices:
                if inv.status in paid_statuses and inv.date:
                    d = _safe_date(inv.date)
                    if d:
                        daily_map[d] += float(inv.total or 0)

            trend_data = []
            for i in range(days - 1, -1, -1):
                target_date = today - timedelta(days=i)
                trend_data.append({
                    "date": target_date.isoformat(),
                    "revenue": daily_map.get(target_date, 0.0)
                })

            return trend_data

        result = await _get_cached_or_compute(cache_key, compute_trend)
        return result

    except Exception as e:
        logging.error(f"Erreur sales trend: {e}")
        return []

@router.get("/sales-by-category")
async def get_sales_by_category(
    days: int = 30,
    current_user = Depends(get_current_user)
):
    """Repartition des ventes par categorie"""
    try:
        cache_key = _get_cache_key("sales_by_category", days)

        async def compute_category_sales():
            since_date = date.today() - timedelta(days=days)

            # Get invoices in the period
            all_invoices = await Invoice.find().to_list()
            inv_ids_in_period = set(
                inv.invoice_id for inv in all_invoices
                if inv.date and _safe_date(inv.date) and _safe_date(inv.date) >= since_date
            )

            # Get all invoice items for those invoices
            all_items = await InvoiceItem.find().to_list()
            # Get product_ids from items
            product_ids = list(set(item.product_id for item in all_items if item.product_id and item.invoice_id in inv_ids_in_period))
            all_products = await Product.find({"product_id": {"$in": product_ids}}).to_list() if product_ids else []
            product_category_map = {p.product_id: (p.category or "Non categorise") for p in all_products}

            category_revenue = defaultdict(float)
            for item in all_items:
                if item.invoice_id in inv_ids_in_period and item.product_id:
                    cat = product_category_map.get(item.product_id, "Non categorise")
                    category_revenue[cat] += float(item.total or 0)

            result = sorted(
                [{"category": cat, "revenue": rev} for cat, rev in category_revenue.items()],
                key=lambda x: x["revenue"],
                reverse=True
            )[:10]

            return result

        result = await _get_cached_or_compute(cache_key, compute_category_sales)
        return result

    except Exception as e:
        logging.error(f"Erreur sales by category: {e}")
        return []

@router.post("/optimize")
async def optimize_database(
    current_user = Depends(get_current_user),
):
    """Declencher l'optimisation de la base de donnees (admin seulement)"""
    # Verifier les permissions admin
    if not hasattr(current_user, 'role') or current_user.role != 'admin':
        raise HTTPException(status_code=403, detail="Acces restreint aux administrateurs")

    try:
        from ..database_optimization import optimize_database as run_optimization

        # Vider le cache avant optimisation
        global _cache
        _cache.clear()

        # Lancer l'optimisation
        run_optimization()

        return {
            "message": "Optimisation de la base de donnees terminee avec succes",
            "cache_cleared": True,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logging.error(f"Erreur optimisation database: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'optimisation: {str(e)}")
