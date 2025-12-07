from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_, case
from typing import Optional
from datetime import datetime, date, timedelta
import logging

from ..database import (
    get_db, User, Invoice, InvoiceItem, InvoicePayment, 
    Quotation, Product, StockMovement, SupplierInvoice,
    SupplierInvoicePayment, BankTransaction, Client,
    DailyPurchase
)
from ..auth import get_current_user
from .dashboard import get_dashboard_stats
from .debts import get_debts_stats

router = APIRouter(prefix="/api/daily-recap", tags=["daily-recap"])

@router.get("/stats")
async def get_daily_recap_stats(
    target_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Récap quotidien des activités pour le gérant
    - Factures créées/payées
    - Devis créés/acceptés  
    - Mouvements de stock
    - Résumé financier (caisse)
    """
    try:
        # Date cible (aujourd'hui par défaut)
        if target_date:
            try:
                recap_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                recap_date = date.today()
        else:
            recap_date = date.today()
        
        # === FACTURES ===
        try:
            # Factures créées ce jour avec relations client et paiements
            from sqlalchemy.orm import joinedload
            invoices_created = db.query(Invoice).options(
                joinedload(Invoice.client),
                joinedload(Invoice.payments)
            ).filter(
                func.date(Invoice.created_at) == recap_date
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des factures: {e}")
            invoices_created = []
        
        try:
            # Paiements reçus ce jour avec relation facture
            payments_received = db.query(InvoicePayment).options(
                joinedload(InvoicePayment.invoice)
            ).filter(
                func.date(InvoicePayment.payment_date) == recap_date
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des paiements: {e}")
            payments_received = []
        
        # === DEVIS ===
        try:
            # Devis créés ce jour avec relation client
            quotations_created = db.query(Quotation).options(
                joinedload(Quotation.client)
            ).filter(
                func.date(Quotation.created_at) == recap_date
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des devis créés: {e}")
            quotations_created = []
        
        try:
            # Devis acceptés ce jour avec relation client
            quotations_accepted = db.query(Quotation).options(
                joinedload(Quotation.client)
            ).filter(
                func.date(Quotation.created_at) == recap_date,
                Quotation.status == "accepté"
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des devis acceptés: {e}")
            quotations_accepted = []
        
        # === MOUVEMENTS DE STOCK ===
        try:
            # Entrées de stock ce jour avec relation produit
            stock_in = db.query(StockMovement).options(
                joinedload(StockMovement.product)
            ).filter(
                func.date(StockMovement.created_at) == recap_date,
                StockMovement.movement_type == "IN"
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des entrées de stock: {e}")
            stock_in = []
        
        try:
            # Sorties de stock ce jour avec relation produit
            stock_out = db.query(StockMovement).options(
                joinedload(StockMovement.product)
            ).filter(
                func.date(StockMovement.created_at) == recap_date,
                StockMovement.movement_type == "OUT"
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des sorties de stock: {e}")
            stock_out = []
        
        # === TRANSACTIONS BANCAIRES ===
        try:
            # Entrées d'argent ce jour
            bank_entries = db.query(BankTransaction).filter(
                func.date(BankTransaction.date) == recap_date,
                BankTransaction.type == "entry"
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des entrées bancaires: {e}")
            bank_entries = []
        
        try:
            # Sorties d'argent ce jour
            bank_exits = db.query(BankTransaction).filter(
                func.date(BankTransaction.date) == recap_date,
                BankTransaction.type == "exit"
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des sorties bancaires: {e}")
            bank_exits = []
        
        # === ACHATS QUOTIDIENS ===
        try:
            daily_purchases = db.query(DailyPurchase).filter(
                (DailyPurchase.date == recap_date) | (func.date(DailyPurchase.created_at) == recap_date)
            ).all()
        except Exception as e:
            logging.error(f"Erreur chargement achats quotidiens: {e}")
            daily_purchases = []

        total_daily_purchases = sum(float(p.amount or 0) for p in daily_purchases)
        # Répartition par catégorie
        try:
            by_cat_rows = (
                db.query(DailyPurchase.category, func.coalesce(func.sum(DailyPurchase.amount), 0))
                .filter((DailyPurchase.date == recap_date) | (func.date(DailyPurchase.created_at) == recap_date))
                .group_by(DailyPurchase.category)
                .all()
            )
        except Exception:
            by_cat_rows = []
        by_category = [
            {"category": (c or ""), "amount": float(a or 0)}
            for c, a in by_cat_rows
        ]

        # === CALCULS FINANCIERS ===
        # Total des paiements reçus
        total_payments = sum(float(p.amount or 0) for p in payments_received)
        
        # Total des entrées bancaires
        total_bank_entries = sum(float(t.amount or 0) for t in bank_entries)
        
        # Total des sorties bancaires  
        total_bank_exits = sum(float(t.amount or 0) for t in bank_exits)
        
        # Solde du jour (déduction des Achats quotidiens)
        daily_balance = total_payments + total_bank_entries - total_bank_exits - total_daily_purchases
        
        # Chiffre d'affaires facturé (pour info)
        potential_revenue = sum(float(inv.total or 0) for inv in invoices_created)
        # Chiffre d'affaires encaissé net (aligné avec la caisse): paiements reçus - achats quotidiens
        net_revenue = float(total_payments) - float(total_daily_purchases)
        
        # Préparer un mapping facture_id -> numéro pour les mouvements de stock liés à une facture
        try:
            invoice_ids_from_stock = {
                int(s.reference_id)
                for s in list(stock_in or []) + list(stock_out or [])
                if getattr(s, "reference_type", None) == "INVOICE" and getattr(s, "reference_id", None)
            }
            invoices_map = {}
            if invoice_ids_from_stock:
                rows = db.query(Invoice.invoice_id, Invoice.invoice_number).filter(
                    Invoice.invoice_id.in_(invoice_ids_from_stock)
                ).all()
                invoices_map = {int(i): num for i, num in rows}
        except Exception as e:
            logging.error(f"Erreur lors de la préparation du mapping factures pour les mouvements de stock: {e}")
            invoices_map = {}

        # === STATS COMPLÉMENTAIRES (DETTES, DASHBOARD) ===
        # On réutilise les endpoints internes pour ne pas dupliquer la logique métier.
        try:
            dashboard_stats = await get_dashboard_stats(
                force_refresh=False,
                db=db,
                current_user=current_user
            )
        except Exception as e:
            logging.error(f"Erreur lors du chargement des stats dashboard dans daily_recap: {e}")
            dashboard_stats = None

        try:
            debts_stats = await get_debts_stats(
                current_user=current_user,
                db=db
            )
        except Exception as e:
            logging.error(f"Erreur lors du chargement des stats dettes dans daily_recap: {e}")
            debts_stats = None

        # === PRÉPARATION DES DONNÉES ===
        return {
            "date": recap_date.isoformat(),
            "date_formatted": recap_date.strftime("%d/%m/%Y"),
            
            # Factures
            "invoices": {
                "created_count": len(invoices_created),
                "created_total": potential_revenue,
                "created_list": [
                    (lambda inv: {
                        "id": inv.invoice_id,
                        "number": inv.invoice_number,
                        "client_name": inv.client.name if inv.client else "Client inconnu",
                        "total": float(inv.total or 0),
                        # Statut recalculé selon paiements cumulés (évite états obsolètes)
                        "status": (
                            "payée" if sum(float(p.amount or 0) for p in (inv.payments or [])) >= float(inv.total or 0) else
                            ("partiellement payée" if sum(float(p.amount or 0) for p in (inv.payments or [])) > 0 else "en attente")
                        ),
                        "time": inv.created_at.strftime("%H:%M") if inv.created_at else ""
                    })(inv)
                    for inv in invoices_created
                ]
            },
            
            # Paiements
            "payments": {
                "count": len(payments_received),
                "total": total_payments,
                "list": [
                    {
                        "id": p.payment_id,
                        "invoice_id": (p.invoice.invoice_id if p.invoice else None),
                        "invoice_number": p.invoice.invoice_number if p.invoice else f"Paiement #{p.payment_id}",
                        "amount": float(p.amount or 0),
                        "method": p.payment_method,
                        "time": p.payment_date.strftime("%H:%M") if p.payment_date else ""
                    }
                    for p in payments_received
                ]
            },
            
            # Devis
            "quotations": {
                "created_count": len(quotations_created),
                "accepted_count": len(quotations_accepted),
                "created_total": sum(float(q.total or 0) for q in quotations_created),
                "accepted_total": sum(float(q.total or 0) for q in quotations_accepted),
                "created_list": [
                    {
                        "id": q.quotation_id,
                        "number": q.quotation_number,
                        "client_name": q.client.name if q.client else "Client inconnu",
                        "total": float(q.total or 0),
                        "status": q.status,
                        "time": q.created_at.strftime("%H:%M") if q.created_at else ""
                    }
                    for q in quotations_created
                ],
                "accepted_list": [
                    {
                        "id": q.quotation_id,
                        "number": q.quotation_number,
                        "client_name": q.client.name if q.client else "Client inconnu",
                        "total": float(q.total or 0),
                        "time": q.created_at.strftime("%H:%M") if q.created_at else ""
                    }
                    for q in quotations_accepted
                ]
            },
            
            # Stock
            "stock": {
                "entries_count": len(stock_in),
                "exits_count": len(stock_out),
                "entries_quantity": sum(s.quantity for s in stock_in),
                "exits_quantity": sum(s.quantity for s in stock_out),
                "entries_list": [
                    {
                        "id": s.movement_id,
                        "product_name": s.product.name if s.product else "Produit inconnu",
                        "quantity": s.quantity,
                        "reference": s.reference_type,
                        "reference_id": s.reference_id,
                        "invoice_id": int(s.reference_id) if s.reference_type == "INVOICE" and s.reference_id else None,
                        "invoice_number": invoices_map.get(int(s.reference_id)) if s.reference_type == "INVOICE" and s.reference_id else None,
                        "notes": s.notes,
                        "time": s.created_at.strftime("%H:%M") if s.created_at else ""
                    }
                    for s in stock_in
                ],
                "exits_list": [
                    {
                        "id": s.movement_id,
                        "product_name": s.product.name if s.product else "Produit inconnu",
                        "quantity": s.quantity,
                        "reference": s.reference_type,
                        "reference_id": s.reference_id,
                        "invoice_id": int(s.reference_id) if s.reference_type == "INVOICE" and s.reference_id else None,
                        "invoice_number": invoices_map.get(int(s.reference_id)) if s.reference_type == "INVOICE" and s.reference_id else None,
                        "notes": s.notes,
                        "time": s.created_at.strftime("%H:%M") if s.created_at else ""
                    }
                    for s in stock_out
                ]
            },
            
            # Finances (Caisse)
            "finances": {
                "payments_received": total_payments,
                "bank_entries": total_bank_entries,
                "bank_exits": total_bank_exits,
                "daily_purchases_total": float(total_daily_purchases),
                "daily_balance": daily_balance,
                "potential_revenue": potential_revenue,
                "net_revenue": net_revenue,
                "bank_entries_list": [
                    {
                        "id": t.transaction_id,
                        "motif": t.motif,
                        "description": t.description,
                        "amount": float(t.amount or 0),
                        "method": t.method,
                        "reference": t.reference
                    }
                    for t in bank_entries
                ],
                "bank_exits_list": [
                    {
                        "id": t.transaction_id,
                        "motif": t.motif,
                        "description": t.description,
                        "amount": float(t.amount or 0),
                        "method": t.method,
                        "reference": t.reference
                    }
                    for t in bank_exits
                ]
            },
            # Achats quotidiens
            "daily_purchases": {
                "count": len(daily_purchases),
                "total": float(total_daily_purchases),
                "by_category": by_category,
                "list": [
                    {
                        "id": dp.id,
                        "time": (dp.created_at.strftime("%H:%M") if getattr(dp, 'created_at', None) else ""),
                        "category": dp.category,
                        "description": dp.description,
                        "amount": float(dp.amount or 0),
                        "method": dp.payment_method,
                        "reference": dp.reference,
                    }
                    for dp in daily_purchases
                ]
            },
            # Dettes (clients et fournisseurs)
            "debts": debts_stats or {},
            # Stats avancées / Dashboard global
            "dashboard": dashboard_stats or {}
        }
        
    except Exception as e:
        logging.error(f"Erreur daily recap stats: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur lors du calcul du récap quotidien: {str(e)}")

@router.get("/period-summary")
async def get_period_summary(
    start_date: str,
    end_date: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Résumé sur une période donnée pour comparaison
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        
        # Paiements reçus sur la période
        total_payments = db.query(func.coalesce(func.sum(InvoicePayment.amount), 0)).filter(
            func.date(InvoicePayment.payment_date) >= start,
            func.date(InvoicePayment.payment_date) <= end
        ).scalar() or 0
        
        # Factures créées sur la période
        invoices_count = db.query(func.count(Invoice.invoice_id)).filter(
            func.date(Invoice.created_at) >= start,
            func.date(Invoice.created_at) <= end
        ).scalar() or 0
        
        # Devis créés sur la période
        quotations_count = db.query(func.count(Quotation.quotation_id)).filter(
            func.date(Quotation.created_at) >= start,
            func.date(Quotation.created_at) <= end
        ).scalar() or 0
        
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days_count": (end - start).days + 1,
            "total_payments": float(total_payments),
            "invoices_created": invoices_count,
            "quotations_created": quotations_count,
            "average_daily_payment": float(total_payments) / ((end - start).days + 1) if (end - start).days >= 0 else 0
        }
        
    except Exception as e:
        logging.error(f"Erreur period summary: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur lors du calcul du résumé de période: {str(e)}")
