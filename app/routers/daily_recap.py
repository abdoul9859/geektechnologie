from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, date, timedelta
import logging

from ..database import (
    Invoice, InvoicePayment,
    Quotation, Product, StockMovement,
    BankTransaction, Client,
    DailyPurchase
)
from ..auth import get_current_user

router = APIRouter(prefix="/api/daily-recap", tags=["daily-recap"])


def _date_to_datetime_range(target: date):
    """Return (start, end) datetimes for a given date, covering the full day."""
    start = datetime(target.year, target.month, target.day, 0, 0, 0)
    end = datetime(target.year, target.month, target.day, 23, 59, 59, 999999)
    return start, end


def _match_date_field(field_value, target: date) -> bool:
    """Check if a datetime or date field matches a target date."""
    if field_value is None:
        return False
    if isinstance(field_value, datetime):
        return field_value.date() == target
    if isinstance(field_value, date):
        return field_value == target
    # If it's a string, try to parse it
    if isinstance(field_value, str):
        try:
            return datetime.fromisoformat(field_value).date() == target
        except (ValueError, TypeError):
            return False
    return False


@router.get("/stats")
async def get_daily_recap_stats(
    target_date: Optional[str] = None,
    current_user=Depends(get_current_user)
):
    """
    Recap quotidien des activites pour le gerant
    - Factures creees/payees
    - Devis crees/acceptes
    - Mouvements de stock
    - Resume financier (caisse)
    """
    try:
        # Date cible (aujourd'hui par defaut)
        if target_date:
            try:
                recap_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                try:
                    recap_date = datetime.strptime(target_date, "%d/%m/%Y").date()
                except ValueError:
                    recap_date = date.today()
        else:
            recap_date = date.today()

        day_start, day_end = _date_to_datetime_range(recap_date)

        # === FACTURES ===
        try:
            # Factures creees ce jour
            # Invoice.date is a datetime field; query by range
            invoices_created = await Invoice.find(
                Invoice.date >= day_start,
                Invoice.date <= day_end
            ).to_list()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des factures: {e}")
            invoices_created = []

        # Fetch related clients for invoices
        try:
            client_ids = list({inv.client_id for inv in invoices_created if inv.client_id is not None})
            if client_ids:
                clients_list = await Client.find({"client_id": {"$in": client_ids}}).to_list()
            else:
                clients_list = []
            clients_map = {c.client_id: c for c in clients_list}
        except Exception as e:
            logging.error(f"Erreur chargement clients pour factures: {e}")
            clients_map = {}

        # Fetch payments for all invoices created today
        try:
            invoice_ids_today = [inv.invoice_id for inv in invoices_created]
            if invoice_ids_today:
                payments_for_today_invoices = await InvoicePayment.find(
                    {"invoice_id": {"$in": invoice_ids_today}}
                ).to_list()
            else:
                payments_for_today_invoices = []
            # Group payments by invoice_id
            payments_by_invoice = {}
            for p in payments_for_today_invoices:
                payments_by_invoice.setdefault(p.invoice_id, []).append(p)
        except Exception as e:
            logging.error(f"Erreur chargement paiements factures du jour: {e}")
            payments_by_invoice = {}

        try:
            # Paiements recus ce jour
            payments_received = await InvoicePayment.find(
                InvoicePayment.payment_date >= day_start,
                InvoicePayment.payment_date <= day_end
            ).to_list()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des paiements: {e}")
            payments_received = []

        # Fetch invoices referenced by payments (for display)
        try:
            payment_invoice_ids = list({p.invoice_id for p in payments_received if p.invoice_id is not None})
            if payment_invoice_ids:
                payment_invoices_list = await Invoice.find(
                    {"invoice_id": {"$in": payment_invoice_ids}}
                ).to_list()
            else:
                payment_invoices_list = []
            payment_invoices_map = {inv.invoice_id: inv for inv in payment_invoices_list}
        except Exception as e:
            logging.error(f"Erreur chargement factures pour paiements: {e}")
            payment_invoices_map = {}

        # === DEVIS ===
        try:
            # Devis crees ce jour
            quotations_created = await Quotation.find(
                Quotation.date >= day_start,
                Quotation.date <= day_end
            ).to_list()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des devis crees: {e}")
            quotations_created = []

        try:
            # Devis acceptes ce jour
            quotations_accepted = await Quotation.find(
                Quotation.date >= day_start,
                Quotation.date <= day_end,
                Quotation.status == "accepte"
            ).to_list()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des devis acceptes: {e}")
            quotations_accepted = []

        # Fetch clients for quotations
        try:
            q_client_ids = list({q.client_id for q in quotations_created + quotations_accepted if q.client_id is not None})
            if q_client_ids:
                q_clients_list = await Client.find({"client_id": {"$in": q_client_ids}}).to_list()
                for c in q_clients_list:
                    if c.client_id not in clients_map:
                        clients_map[c.client_id] = c
        except Exception as e:
            logging.error(f"Erreur chargement clients pour devis: {e}")

        # === MOUVEMENTS DE STOCK ===
        try:
            # Entrees de stock ce jour
            stock_in = await StockMovement.find(
                StockMovement.created_at >= day_start,
                StockMovement.created_at <= day_end,
                StockMovement.movement_type == "IN"
            ).to_list()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des entrees de stock: {e}")
            stock_in = []

        try:
            # Sorties de stock ce jour
            stock_out = await StockMovement.find(
                StockMovement.created_at >= day_start,
                StockMovement.created_at <= day_end,
                StockMovement.movement_type == "OUT"
            ).to_list()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des sorties de stock: {e}")
            stock_out = []

        # Fetch products for stock movements
        try:
            stock_product_ids = list({
                s.product_id
                for s in (stock_in or []) + (stock_out or [])
                if s.product_id is not None
            })
            if stock_product_ids:
                stock_products = await Product.find(
                    {"product_id": {"$in": stock_product_ids}}
                ).to_list()
            else:
                stock_products = []
            products_map = {p.product_id: p for p in stock_products}
        except Exception as e:
            logging.error(f"Erreur chargement produits pour mouvements stock: {e}")
            products_map = {}

        # === TRANSACTIONS BANCAIRES ===
        try:
            # Entrees d'argent ce jour
            # BankTransaction.date is a date field, not datetime
            bank_entries = await BankTransaction.find(
                BankTransaction.date == recap_date,
                BankTransaction.type == "entry"
            ).to_list()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des entrees bancaires: {e}")
            bank_entries = []

        try:
            # Sorties d'argent ce jour
            bank_exits = await BankTransaction.find(
                BankTransaction.date == recap_date,
                BankTransaction.type == "exit"
            ).to_list()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des sorties bancaires: {e}")
            bank_exits = []

        # === ACHATS QUOTIDIENS ===
        try:
            # DailyPurchase.date is a date field, created_at is datetime
            daily_purchases = await DailyPurchase.find(
                {"$or": [
                    {"date": recap_date.isoformat()},
                    {"created_at": {"$gte": day_start, "$lte": day_end}}
                ]}
            ).to_list()
        except Exception as e:
            logging.error(f"Erreur chargement achats quotidiens: {e}")
            daily_purchases = []

        total_daily_purchases = sum(float(p.amount or 0) for p in daily_purchases)

        # === STATISTIQUES PAR UTILISATEUR ===
        # Factures creees par l'utilisateur connecte
        try:
            user_invoices = [inv for inv in invoices_created if inv.created_by == current_user.user_id]
            user_invoices_total = sum(float(inv.total or 0) for inv in user_invoices)
        except Exception as e:
            logging.error(f"Erreur calcul factures utilisateur: {e}")
            user_invoices = []
            user_invoices_total = 0

        # Devis crees par l'utilisateur connecte
        try:
            user_quotations = [q for q in quotations_created if q.created_by == current_user.user_id]
            user_quotations_total = sum(float(q.total or 0) for q in user_quotations)
        except Exception as e:
            logging.error(f"Erreur calcul devis utilisateur: {e}")
            user_quotations = []
            user_quotations_total = 0

        # Achats quotidiens crees par l'utilisateur connecte
        try:
            user_purchases = [dp for dp in daily_purchases if dp.created_by == current_user.user_id]
            user_purchases_total = sum(float(dp.amount or 0) for dp in user_purchases)
        except Exception as e:
            logging.error(f"Erreur calcul achats utilisateur: {e}")
            user_purchases = []
            user_purchases_total = 0

        # Paiements recus par l'utilisateur (via les factures qu'il a creees)
        try:
            user_payments = [
                p for p in payments_received
                if p.invoice_id and payment_invoices_map.get(p.invoice_id)
                and getattr(payment_invoices_map[p.invoice_id], 'created_by', None) == current_user.user_id
            ]
            user_payments_total = sum(float(p.amount or 0) for p in user_payments)
        except Exception as e:
            logging.error(f"Erreur calcul paiements utilisateur: {e}")
            user_payments = []
            user_payments_total = 0

        # Repartition par categorie (Python-level aggregation)
        try:
            cat_totals = {}
            for dp in daily_purchases:
                cat = dp.category or ""
                cat_totals[cat] = cat_totals.get(cat, 0) + float(dp.amount or 0)
            by_category = [
                {"category": c, "amount": a}
                for c, a in cat_totals.items()
            ]
        except Exception:
            by_category = []

        # === CALCULS FINANCIERS ===
        # Total des paiements recus
        total_payments = sum(float(p.amount or 0) for p in payments_received)

        # Total des entrees bancaires
        total_bank_entries = sum(float(t.amount or 0) for t in bank_entries)

        # Total des sorties bancaires
        total_bank_exits = sum(float(t.amount or 0) for t in bank_exits)

        # Solde du jour (deduction des Achats quotidiens)
        daily_balance = total_payments + total_bank_entries - total_bank_exits - total_daily_purchases

        # Chiffre d'affaires facture (pour info)
        potential_revenue = sum(float(inv.total or 0) for inv in invoices_created)
        # Chiffre d'affaires encaisse net (aligne avec la caisse): paiements recus - achats quotidiens
        net_revenue = float(total_payments) - float(total_daily_purchases)

        # Preparer un mapping facture_id -> numero pour les mouvements de stock lies a une facture
        try:
            invoice_ids_from_stock = {
                int(s.reference_id)
                for s in list(stock_in or []) + list(stock_out or [])
                if getattr(s, "reference_type", None) == "INVOICE" and getattr(s, "reference_id", None)
            }
            invoices_map = {}
            if invoice_ids_from_stock:
                inv_list = await Invoice.find(
                    {"invoice_id": {"$in": list(invoice_ids_from_stock)}}
                ).to_list()
                invoices_map = {inv.invoice_id: inv.invoice_number for inv in inv_list}
        except Exception as e:
            logging.error(f"Erreur lors de la preparation du mapping factures pour les mouvements de stock: {e}")
            invoices_map = {}

        # === STATS COMPLEMENTAIRES (DETTES, DASHBOARD) ===
        # These functions are imported from their respective routers.
        dashboard_stats = None
        debts_stats = None

        try:
            from .dashboard import get_dashboard_stats
            dashboard_stats = await get_dashboard_stats(
                force_refresh=False,
                current_user=current_user
            )
        except Exception as e:
            logging.error(f"Erreur lors du chargement des stats dashboard dans daily_recap: {e}")
            dashboard_stats = None

        try:
            from .debts import get_debts_stats
            debts_stats = await get_debts_stats(
                current_user=current_user
            )
        except Exception as e:
            logging.error(f"Erreur lors du chargement des stats dettes dans daily_recap: {e}")
            debts_stats = None

        # Helper to get client name from map
        def _client_name(client_id):
            c = clients_map.get(client_id)
            return c.name if c else "Client inconnu"

        # Helper to compute invoice status from payments
        def _invoice_status(inv, inv_payments):
            paid = sum(float(p.amount or 0) for p in (inv_payments or []))
            total = float(inv.total or 0)
            if paid >= total:
                return "payee"
            elif paid > 0:
                return "partiellement payee"
            return "en attente"

        # === PREPARATION DES DONNEES ===
        return {
            "date": recap_date.isoformat(),
            "date_formatted": recap_date.strftime("%d/%m/%Y"),

            # Factures
            "invoices": {
                "created_count": len(invoices_created),
                "created_total": potential_revenue,
                "created_list": [
                    {
                        "id": inv.invoice_id,
                        "number": inv.invoice_number,
                        "client_name": _client_name(inv.client_id),
                        "total": float(inv.total or 0),
                        "status": _invoice_status(inv, payments_by_invoice.get(inv.invoice_id, [])),
                        "time": inv.created_at.strftime("%H:%M") if inv.created_at else ""
                    }
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
                        "invoice_id": p.invoice_id,
                        "invoice_number": (
                            payment_invoices_map[p.invoice_id].invoice_number
                            if p.invoice_id and p.invoice_id in payment_invoices_map
                            else f"Paiement #{p.payment_id}"
                        ),
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
                        "client_name": _client_name(q.client_id),
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
                        "client_name": _client_name(q.client_id),
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
                        "product_name": products_map.get(s.product_id, None) and products_map[s.product_id].name or "Produit inconnu",
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
                        "product_name": products_map.get(s.product_id, None) and products_map[s.product_id].name or "Produit inconnu",
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
                        "id": dp.purchase_id,
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
            # Stats avancees / Dashboard global
            "dashboard": dashboard_stats or {},

            # Statistiques de l'utilisateur connecte
            "user_stats": {
                "user_id": current_user.user_id,
                "username": current_user.username,
                "invoices": {
                    "count": len(user_invoices),
                    "total": user_invoices_total,
                    "list": [
                        {
                            "id": inv.invoice_id,
                            "number": inv.invoice_number,
                            "client_name": _client_name(inv.client_id),
                            "total": float(inv.total or 0),
                            "status": _invoice_status(inv, payments_by_invoice.get(inv.invoice_id, [])),
                            "time": inv.created_at.strftime("%H:%M") if inv.created_at else ""
                        }
                        for inv in user_invoices
                    ]
                },
                "quotations": {
                    "count": len(user_quotations),
                    "total": user_quotations_total,
                    "list": [
                        {
                            "id": q.quotation_id,
                            "number": q.quotation_number,
                            "client_name": _client_name(q.client_id),
                            "total": float(q.total or 0),
                            "status": q.status,
                            "time": q.created_at.strftime("%H:%M") if q.created_at else ""
                        }
                        for q in user_quotations
                    ]
                },
                "payments": {
                    "count": len(user_payments),
                    "total": user_payments_total
                },
                "daily_purchases": {
                    "count": len(user_purchases),
                    "total": user_purchases_total,
                    "list": [
                        {
                            "id": dp.purchase_id,
                            "time": (dp.created_at.strftime("%H:%M") if getattr(dp, 'created_at', None) else ""),
                            "category": dp.category,
                            "description": dp.description,
                            "amount": float(dp.amount or 0),
                            "method": dp.payment_method,
                        }
                        for dp in user_purchases
                    ]
                },
                "net_balance": user_payments_total - user_purchases_total
            }
        }

    except Exception as e:
        logging.error(f"Erreur daily recap stats: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur lors du calcul du recap quotidien: {str(e)}")


@router.get("/period-summary")
async def get_period_summary(
    start_date: str,
    end_date: str,
    current_user=Depends(get_current_user)
):
    """
    Resume sur une periode donnee pour comparaison
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()

        start_dt = datetime(start.year, start.month, start.day, 0, 0, 0)
        end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, 999999)

        # Paiements recus sur la periode
        payments = await InvoicePayment.find(
            InvoicePayment.payment_date >= start_dt,
            InvoicePayment.payment_date <= end_dt
        ).to_list()
        total_payments = sum(float(p.amount or 0) for p in payments)

        # Factures creees sur la periode
        invoices = await Invoice.find(
            Invoice.created_at >= start_dt,
            Invoice.created_at <= end_dt
        ).to_list()
        invoices_count = len(invoices)

        # Devis crees sur la periode
        quotations = await Quotation.find(
            Quotation.created_at >= start_dt,
            Quotation.created_at <= end_dt
        ).to_list()
        quotations_count = len(quotations)

        days_count = (end - start).days + 1

        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days_count": days_count,
            "total_payments": float(total_payments),
            "invoices_created": invoices_count,
            "quotations_created": quotations_count,
            "average_daily_payment": float(total_payments) / days_count if days_count > 0 else 0
        }

    except Exception as e:
        logging.error(f"Erreur period summary: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur lors du calcul du resume de periode: {str(e)}")
