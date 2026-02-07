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

            # 1. Nombre de produits en stock
            all_products = await Product.find().to_list()

            # Get available variants per product
            all_variants = await ProductVariant.find().to_list()
            available_by_product = defaultdict(int)
            for v in all_variants:
                if not v.is_sold:
                    available_by_product[v.product_id] += 1

            total_stock = sum(
                1 for p in all_products
                if (p.quantity or 0) > 0 or available_by_product.get(p.product_id, 0) > 0
            )

            # 2. Statistiques factures
            all_invoices = await Invoice.find().to_list()

            pending_statuses = ["en attente", "SENT", "DRAFT", "OVERDUE", "partiellement payee"]
            pending_invoices = sum(1 for inv in all_invoices if inv.status in pending_statuses)

            # Chiffre d'affaires mensuel (factures payees)
            paid_statuses = ["payee", "PAID"]
            monthly_revenue_gross = sum(
                float(inv.total or 0) for inv in all_invoices
                if inv.status in paid_statuses and inv.date
                and _safe_date(inv.date) and _safe_date(inv.date).month == today.month
                and _safe_date(inv.date).year == today.year
            )

            # Achats quotidiens du mois
            all_purchases = await DailyPurchase.find().to_list()
            monthly_purchases = sum(
                float(p.amount or 0) for p in all_purchases
                if (p.date and _safe_date(p.date) and _safe_date(p.date).month == today.month and _safe_date(p.date).year == today.year)
                or (p.created_at and _safe_date(p.created_at) and _safe_date(p.created_at).month == today.month and _safe_date(p.created_at).year == today.year)
            )

            # Paiements aux fournisseurs du mois
            all_supplier_invs = await SupplierInvoice.find().to_list()
            monthly_supplier_payments = sum(
                float(si.paid_amount or 0) for si in all_supplier_invs
                if si.invoice_date and _safe_date(si.invoice_date)
                and _safe_date(si.invoice_date).month == today.month
                and _safe_date(si.invoice_date).year == today.year
            )

            # Chiffre d'affaires net
            monthly_revenue = float(monthly_revenue_gross) - float(monthly_supplier_payments) - float(monthly_purchases)

            # Montant impaye
            unpaid_statuses = [
                "en attente", "En attente", "EN ATTENTE",
                "partiellement payee", "partiellement payee", "PARTIELLEMENT PAYEE",
                "OVERDUE", "en retard", "En retard"
            ]
            unpaid_amount = sum(
                float(inv.remaining_amount or 0) for inv in all_invoices
                if inv.status in unpaid_statuses or (inv.remaining_amount and float(inv.remaining_amount) > 0)
            )
            # Fallback
            if unpaid_amount <= 0:
                unpaid_amount = sum(
                    max(float(inv.total or 0) - float(inv.paid_amount or 0), 0)
                    for inv in all_invoices
                )

            # 3. KPIs avances (periode 30 jours)
            since_30 = now - timedelta(days=30)
            since_90 = now - timedelta(days=90)

            # Factures payees sur 30 jours
            paid_invoices_30d = [
                inv for inv in all_invoices
                if inv.status in paid_statuses and inv.date
                and _safe_date(inv.date) and _safe_date(inv.date) >= since_30.date()
            ]
            num_invoices_30d = len(paid_invoices_30d)
            total_revenue_30d_gross = sum(float(inv.total or 0) for inv in paid_invoices_30d)

            purchases_30d = sum(
                float(p.amount or 0) for p in all_purchases
                if (p.date and _safe_date(p.date) and _safe_date(p.date) >= since_30.date())
                or (p.created_at and _safe_date(p.created_at) and _safe_date(p.created_at) >= since_30.date())
            )

            # Paiements fournisseurs 30 jours
            all_sup_payments = await SupplierInvoicePayment.find().to_list()
            supplier_payments_30d = sum(
                float(sp.amount or 0) for sp in all_sup_payments
                if sp.payment_date and _safe_date(sp.payment_date) and _safe_date(sp.payment_date) >= since_30.date()
            )

            total_revenue_30d = float(total_revenue_30d_gross) - float(supplier_payments_30d) - float(purchases_30d)
            avg_ticket = float(total_revenue_30d / num_invoices_30d) if num_invoices_30d > 0 else 0.0

            # Taux de conversion devis->factures (30 jours)
            all_quotations = await Quotation.find().to_list()
            quotes_30d = sum(
                1 for q in all_quotations
                if q.date and _safe_date(q.date) and _safe_date(q.date) >= since_30.date()
            )
            converted_quotes_30d = len(set(
                inv.quotation_id for inv in all_invoices
                if inv.quotation_id is not None and inv.date
                and _safe_date(inv.date) and _safe_date(inv.date) >= since_30.date()
            ))
            conversion_rate = float((converted_quotes_30d / quotes_30d) * 100) if quotes_30d > 0 else 0.0

            # Stock critique
            out_of_stock = sum(1 for p in all_products if (p.quantity or 0) == 0)
            low_stock = sum(1 for p in all_products if (p.quantity or 0) > 0 and (p.quantity or 0) <= 3)

            # Clients actifs (90 jours)
            active_customers = len(set(
                inv.client_id for inv in all_invoices
                if inv.client_id is not None and inv.date
                and _safe_date(inv.date) and _safe_date(inv.date) >= since_90.date()
            ))

            # Top 3 produits par CA (30 jours)
            all_items = await InvoiceItem.find().to_list()
            # Build a set of invoice_ids from the last 30 days
            inv_ids_30d = set(
                inv.invoice_id for inv in all_invoices
                if inv.date and _safe_date(inv.date) and _safe_date(inv.date) >= since_30.date()
            )
            product_revenue = defaultdict(float)
            for item in all_items:
                if item.invoice_id in inv_ids_30d:
                    product_revenue[item.product_name or "-"] += float(item.total or 0)

            top_products_list = sorted(
                [{"name": name, "revenue": rev} for name, rev in product_revenue.items()],
                key=lambda x: x["revenue"],
                reverse=True
            )[:3]

            # Repartition paiements (30 jours)
            all_payments = await InvoicePayment.find().to_list()
            method_amounts = defaultdict(float)
            for pay in all_payments:
                if pay.payment_date and _safe_date(pay.payment_date) and _safe_date(pay.payment_date) >= since_30.date():
                    method_amounts[pay.payment_method or "Non specifie"] += float(pay.amount or 0)

            payments_breakdown = sorted(
                [{"method": method, "amount": amt} for method, amt in method_amounts.items()],
                key=lambda x: x["amount"],
                reverse=True
            )[:5]

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
