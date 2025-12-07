from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from typing import List, Optional
from datetime import datetime, timedelta, date
import json

from ..database import get_db, User
from ..database import Invoice, InvoiceItem, InvoicePayment, Quotation, Product, Client
from ..auth import get_current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])

@router.get("/overview")
async def get_overview_report(
    period: str = "month",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Rapport de vue d'ensemble"""
    try:
        # Données simulées pour le rapport d'ensemble
        return {
            "sales": {
                "total": 2500000,
                "count": 15,
                "average": 166667,
                "growth": 12.5
            },
            "purchases": {
                "total": 800000,
                "count": 8,
                "average": 100000,
                "growth": -5.2
            },
            "profit": {
                "total": 1700000,
                "margin": 68.0,
                "growth": 18.3
            },
            "customers": {
                "total": 45,
                "new": 8,
                "active": 32,
                "growth": 15.6
            },
            "products": {
                "total": 120,
                "low_stock": 12,
                "out_of_stock": 3,
                "top_selling": [
                    {"name": "iPhone 15", "quantity": 25, "revenue": 18750000},
                    {"name": "Samsung Galaxy S24", "quantity": 18, "revenue": 11700000},
                    {"name": "MacBook Air", "quantity": 8, "revenue": 9600000}
                ]
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard")
async def get_dashboard_metrics(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """KPI réels pour le tableau de bord, calculés depuis SQLite.
    - Panier moyen (factures payées sur N derniers jours)
    - Taux de conversion devis -> factures (N jours)
    - Stock critique (<=3) + en rupture (=0)
    - Clients actifs (90 jours)
    - Répartition des paiements (N jours)
    - Top produits par CA (N jours)
    """
    try:
        now = datetime.now()
        since = now - timedelta(days=max(1, days))

        # Panier moyen sur factures payées (FR/EN)
        paid_statuses = ["payée", "PAID"]
        invoices_q = (
            db.query(Invoice)
            .filter(func.date(Invoice.date) >= since.date())
            .filter(Invoice.status.in_(paid_statuses))
        )
        num_invoices = invoices_q.count()
        total_revenue = float(
            db.query(func.coalesce(func.sum(Invoice.total), 0))
            .filter(func.date(Invoice.date) >= since.date())
            .filter(Invoice.status.in_(paid_statuses))
            .scalar()
            or 0
        )
        avg_ticket = float(total_revenue / num_invoices) if num_invoices else 0.0

        # Conversion devis -> factures (N jours)
        quotes_total = db.query(func.count(Quotation.quotation_id)).filter(func.date(Quotation.date) >= since.date()).scalar() or 0
        converted_quotes = (
            db.query(func.count(func.distinct(Invoice.quotation_id)))
            .filter(Invoice.quotation_id.isnot(None))
            .filter(func.date(Invoice.date) >= since.date())
            .scalar()
            or 0
        )
        conversion_rate = float((converted_quotes / quotes_total) * 100) if quotes_total else 0.0

        # Stock critique
        out_of_stock = db.query(func.count(Product.product_id)).filter((Product.quantity == 0) | (Product.quantity.is_(None))).scalar() or 0
        low_stock = db.query(func.count(Product.product_id)).filter(Product.quantity > 0, Product.quantity <= 3).scalar() or 0

        # Clients actifs (90 jours)
        since_90 = now - timedelta(days=90)
        active_customers = (
            db.query(func.count(func.distinct(Invoice.client_id)))
            .filter(Invoice.client_id.isnot(None))
            .filter(func.date(Invoice.date) >= since_90.date())
            .scalar()
            or 0
        )

        # Répartition des paiements (N jours)
        payments = (
            db.query(InvoicePayment.payment_method, func.coalesce(func.sum(InvoicePayment.amount), 0).label("amount"))
            .filter(func.date(InvoicePayment.payment_date) >= since.date())
            .group_by(InvoicePayment.payment_method)
            .order_by(desc("amount"))
            .all()
        )
        payments_breakdown = [
            {"method": (pm or "Non spécifié"), "amount": float(am or 0)} for pm, am in payments
        ]

        # Top produits par CA (N jours)
        top_products_rows = (
            db.query(
                InvoiceItem.product_name,
                func.coalesce(func.sum(InvoiceItem.total), 0).label("revenue"),
            )
            .join(Invoice, InvoiceItem.invoice_id == Invoice.invoice_id)
            .filter(func.date(Invoice.date) >= since.date())
            .group_by(InvoiceItem.product_name)
            .order_by(desc("revenue"))
            .limit(5)
            .all()
        )
        top_products = [
            {"name": (name or "-"), "revenue": float(rev or 0)} for name, rev in top_products_rows
        ]

        return {
            "avg_ticket": avg_ticket,
            "conversion_rate": conversion_rate,
            "stock": {
                "low_stock": int(low_stock),
                "out_of_stock": int(out_of_stock),
                "critical_total": int(low_stock + out_of_stock),
            },
            "active_customers": int(active_customers),
            "payments": payments_breakdown,
            "top_products": top_products,
            "period_days": days,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sales")
async def get_sales_report(
    period: str = "month",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Rapport des ventes"""
    try:
        # Générer des données de ventes simulées
        sales_data = []
        chart_data = []
        
        # Données par jour pour les 30 derniers jours
        for i in range(30):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            amount = 50000 + (i * 10000) + (i % 7 * 25000)  # Variation simulée
            sales_data.append({
                "date": date,
                "amount": amount,
                "transactions": 2 + (i % 5),
                "average_ticket": amount / (2 + (i % 5))
            })
            chart_data.append({"date": date, "value": amount})
        
        return {
            "summary": {
                "total_sales": sum(s["amount"] for s in sales_data),
                "total_transactions": sum(s["transactions"] for s in sales_data),
                "average_ticket": sum(s["amount"] for s in sales_data) / sum(s["transactions"] for s in sales_data),
                "best_day": max(sales_data, key=lambda x: x["amount"]),
                "growth_rate": 15.2
            },
            "daily_data": sales_data[:7],  # 7 derniers jours
            "chart_data": chart_data,
            "top_products": [
                {"name": "iPhone 15", "quantity": 25, "revenue": 18750000},
                {"name": "Samsung Galaxy S24", "quantity": 18, "revenue": 11700000},
                {"name": "MacBook Air", "quantity": 8, "revenue": 9600000},
                {"name": "iPad Pro", "quantity": 12, "revenue": 8400000},
                {"name": "AirPods Pro", "quantity": 30, "revenue": 6000000}
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stock")
async def get_stock_report(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Rapport de stock"""
    try:
        return {
            "summary": {
                "total_products": 120,
                "total_value": 15000000,
                "low_stock_items": 12,
                "out_of_stock_items": 3,
                "overstocked_items": 5
            },
            "stock_levels": [
                {"category": "Smartphones", "total": 45, "low_stock": 5, "out_of_stock": 1, "value": 8500000},
                {"category": "Ordinateurs", "total": 25, "low_stock": 3, "out_of_stock": 1, "value": 4200000},
                {"category": "Accessoires", "total": 50, "low_stock": 4, "out_of_stock": 1, "value": 2300000}
            ],
            "low_stock_products": [
                {"name": "iPhone 15 Pro", "current_stock": 3, "min_stock": 10, "value": 2250000},
                {"name": "MacBook Pro", "current_stock": 2, "min_stock": 5, "value": 3200000},
                {"name": "AirPods Pro", "current_stock": 4, "min_stock": 15, "value": 800000}
            ],
            "stock_movements": [
                {"date": "2024-01-20", "type": "sale", "product": "iPhone 15", "quantity": -2, "stock_after": 23},
                {"date": "2024-01-19", "type": "purchase", "product": "Samsung Galaxy", "quantity": 10, "stock_after": 28},
                {"date": "2024-01-18", "type": "sale", "product": "MacBook Air", "quantity": -1, "stock_after": 7}
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/financial")
async def get_financial_report(
    period: str = "month",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Rapport financier"""
    try:
        return {
            "summary": {
                "revenue": 2500000,
                "expenses": 800000,
                "profit": 1700000,
                "profit_margin": 68.0,
                "tax_amount": 306000
            },
            "revenue_breakdown": [
                {"category": "Ventes produits", "amount": 2200000, "percentage": 88},
                {"category": "Services", "amount": 200000, "percentage": 8},
                {"category": "Autres", "amount": 100000, "percentage": 4}
            ],
            "expense_breakdown": [
                {"category": "Achats stock", "amount": 500000, "percentage": 62.5},
                {"category": "Salaires", "amount": 150000, "percentage": 18.75},
                {"category": "Loyer", "amount": 80000, "percentage": 10},
                {"category": "Autres charges", "amount": 70000, "percentage": 8.75}
            ],
            "monthly_trend": [
                {"month": "Jan", "revenue": 2500000, "expenses": 800000, "profit": 1700000},
                {"month": "Déc", "revenue": 2200000, "expenses": 750000, "profit": 1450000},
                {"month": "Nov", "revenue": 2100000, "expenses": 700000, "profit": 1400000},
                {"month": "Oct", "revenue": 1900000, "expenses": 650000, "profit": 1250000}
            ],
            "cash_flow": {
                "opening_balance": 1500000,
                "total_inflows": 2500000,
                "total_outflows": 800000,
                "closing_balance": 3200000
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/customers")
async def get_customers_report(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Rapport clients"""
    try:
        return {
            "summary": {
                "total_customers": 45,
                "new_customers": 8,
                "active_customers": 32,
                "inactive_customers": 13,
                "average_order_value": 166667
            },
            "top_customers": [
                {"name": "Amadou Ba", "orders": 12, "total_spent": 1800000, "last_order": "2024-01-20"},
                {"name": "Fatou Diop", "orders": 8, "total_spent": 1200000, "last_order": "2024-01-18"},
                {"name": "Moussa Ndiaye", "orders": 6, "total_spent": 950000, "last_order": "2024-01-15"},
                {"name": "Aïcha Sow", "orders": 5, "total_spent": 750000, "last_order": "2024-01-12"},
                {"name": "Omar Fall", "orders": 4, "total_spent": 600000, "last_order": "2024-01-10"}
            ],
            "customer_segments": [
                {"segment": "VIP (>1M)", "count": 5, "percentage": 11.1, "revenue": 6500000},
                {"segment": "Réguliers (500K-1M)", "count": 12, "percentage": 26.7, "revenue": 8400000},
                {"segment": "Occasionnels (<500K)", "count": 28, "percentage": 62.2, "revenue": 4100000}
            ],
            "acquisition_trend": [
                {"month": "Jan", "new_customers": 8, "retained": 24},
                {"month": "Déc", "new_customers": 6, "retained": 22},
                {"month": "Nov", "new_customers": 5, "retained": 20},
                {"month": "Oct", "new_customers": 7, "retained": 18}
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
