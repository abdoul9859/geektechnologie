from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import datetime, date

from ..database import (
    get_db, User, Invoice, Client, InvoicePayment,
    Supplier, SupplierDebt, SupplierDebtPayment,
    SupplierInvoice, SupplierInvoicePayment,
    ClientDebt, ClientDebtPayment
)
from ..auth import get_current_user

router = APIRouter(prefix="/api/debts", tags=["debts"])

# Utilitaire local: convertir une valeur (str/date/datetime) en datetime
def _coerce_dt(v):
    try:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, date):
            return datetime.combine(v, datetime.min.time())
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            # ISO complet
            try:
                return datetime.fromisoformat(s)
            except Exception:
                pass
            # Format YYYY-MM-DD
            try:
                return datetime.strptime(s, "%Y-%m-%d")
            except Exception:
                pass
        return None
    except Exception:
        return None

@router.get("/")
async def get_debts(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    type: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer les dettes clients et fournisseurs."""
    try:
        debts_all = []
        today = date.today()
        
        # 1. Récupérer les créances clients (inclus: factures impayées + créances manuelles)
        if type is None or type == "client":
            # a) Factures client impayées (affichées pour visibilité globale)
            remaining_sql = func.coalesce(Invoice.remaining_amount, Invoice.total - func.coalesce(Invoice.paid_amount, 0))
            q = (
                db.query(Invoice, Client)
                .join(Client, Client.client_id == Invoice.client_id, isouter=True)
                .filter(remaining_sql > 0)
            )

            if search:
                s = f"%{(search or '').lower()}%"
                q = q.filter(
                    func.lower(Invoice.invoice_number).like(s) | func.lower(Client.name).like(s)
                )

            rows = q.all()

            for inv, cl in rows:
                amount = float(inv.total or 0)
                paid = float(inv.paid_amount or 0)
                remaining = float(inv.remaining_amount or (amount - paid))
                overdue = bool(inv.due_date and getattr(inv.due_date, 'date', lambda: inv.due_date)() < today and remaining > 0)
                st = "paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending"))
                if status and st != status:
                    continue
                debts_all.append({
                    "id": int(inv.invoice_id),
                    "type": "client",
                    "entity_id": int(inv.client_id) if inv.client_id is not None else None,
                    "entity_name": getattr(cl, 'name', None),
                    "reference": inv.invoice_number,
                    "invoice_number": inv.invoice_number,
                    "amount": amount,
                    "paid_amount": paid,
                    "remaining_amount": remaining,
                    "date": inv.date,
                    "due_date": inv.due_date,
                    "created_at": inv.created_at,
                    "status": st,
                    "days_overdue": ( (today - inv.due_date.date()).days if (inv.due_date and remaining > 0 and hasattr(inv.due_date, 'date')) else 0 ),
                    "description": None,
                    "has_invoice": True,
                })

            # b) Créances clients manuelles
            cd_q = db.query(ClientDebt, Client).join(Client, Client.client_id == ClientDebt.client_id, isouter=True)
            if search:
                s2 = f"%{(search or '').lower()}%"
                cd_q = cd_q.filter(func.lower(ClientDebt.reference).like(s2) | func.lower(Client.name).like(s2))
            client_debts = cd_q.all()
            for d, cl in client_debts:
                amount = float(d.amount or 0)
                paid = float(d.paid_amount or 0)
                remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)
                overdue = bool(d.due_date and getattr(d.due_date, 'date', lambda: d.due_date)() < today and remaining > 0)
                st = d.status or ("paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending")))
                if status and st != status:
                    continue
                debts_all.append({
                    "id": int(d.debt_id),
                    "type": "client",
                    "entity_id": int(d.client_id) if d.client_id is not None else None,
                    "entity_name": getattr(cl, 'name', None),
                    "reference": d.reference,
                    "amount": amount,
                    "paid_amount": paid,
                    "remaining_amount": remaining,
                    "date": d.date,
                    "due_date": d.due_date,
                    "created_at": d.created_at,
                    "status": st,
                    "days_overdue": ( (today - d.due_date.date()).days if (d.due_date and remaining > 0 and hasattr(d.due_date, 'date')) else 0 ),
                    "description": d.description,
                    "has_invoice": False,
                })
        
        # 2. Récupérer les dettes fournisseurs (factures fournisseur non payées)
        if type is None or type == "supplier":
            q_supplier = (
                db.query(SupplierInvoice, Supplier)
                .join(Supplier, Supplier.supplier_id == SupplierInvoice.supplier_id, isouter=True)
                .filter(SupplierInvoice.remaining_amount > 0)
            )
            
            if search:
                s = f"%{search.lower()}%"
                q_supplier = q_supplier.filter(
                    func.lower(SupplierInvoice.invoice_number).like(s) | func.lower(Supplier.name).like(s)
                )
            
            supplier_rows = q_supplier.all()
            
            for sup_inv, sup in supplier_rows:
                amount = float(sup_inv.amount or 0)
                paid = float(sup_inv.paid_amount or 0)
                remaining = float(sup_inv.remaining_amount or 0)
                # Statut
                overdue = bool(sup_inv.due_date and sup_inv.due_date.date() < today and remaining > 0)
                if remaining <= 0:
                    st = "paid"
                elif overdue:
                    st = "overdue"
                elif sup_inv.status:
                    st = sup_inv.status
                else:
                    st = ("partial" if paid > 0 else "pending")
                # Filtre statut si demandé
                if status and st != status:
                    continue
                debts_all.append({
                    "id": int(sup_inv.invoice_id),
                    "type": "supplier",
                    "entity_id": int(sup_inv.supplier_id) if sup_inv.supplier_id is not None else None,
                    "entity_name": getattr(sup, 'name', None),
                    "reference": sup_inv.invoice_number,
                    "invoice_number": sup_inv.invoice_number,
                    "amount": amount,
                    "paid_amount": paid,
                    "remaining_amount": remaining,
                    "date": sup_inv.invoice_date,
                    "due_date": sup_inv.due_date,
                    "created_at": sup_inv.created_at,
                    "status": st,
                    "days_overdue": ( (today - sup_inv.due_date.date()).days if (sup_inv.due_date and remaining > 0) else 0 ),
                    "description": sup_inv.description,
                })

        # Trier par date décroissante
        debts_all.sort(key=lambda x: x.get('date') or datetime.min, reverse=True)
        
        # Pagination côté Python sur la liste filtrée
        total = len(debts_all)
        debts = debts_all[skip: skip + limit]
        
        return {
            "debts": debts,
            "total": total,
            "page": (skip // limit) + 1,
            "pages": (total + limit - 1) // limit
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{debt_id}")
async def get_debt(
    debt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer une dette par ID"""
    # D'abord, chercher une facture client
    inv = db.query(Invoice).filter(Invoice.invoice_id == debt_id).first()
    if inv:
        cl = db.query(Client).filter(Client.client_id == inv.client_id).first() if inv.client_id else None
        amount = float(inv.total or 0)
        paid = float(inv.paid_amount or 0)
        remaining = float(inv.remaining_amount or (amount - paid))
        today = date.today()
        overdue = bool(inv.due_date and getattr(inv.due_date, 'date', lambda: inv.due_date)() < today and remaining > 0)
        st = "paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending"))
        return {
            "id": int(inv.invoice_id),
            "type": "client",
            "entity_id": int(inv.client_id) if inv.client_id is not None else None,
            "entity_name": getattr(cl, 'name', None),
            "reference": inv.invoice_number,
            "invoice_number": inv.invoice_number,
            "amount": amount,
            "paid_amount": paid,
            "remaining_amount": remaining,
            "date": inv.date,
            "due_date": inv.due_date,
            "created_at": inv.created_at,
            "status": st,
            "days_overdue": ( (today - inv.due_date.date()).days if (inv.due_date and remaining > 0 and hasattr(inv.due_date, 'date')) else 0 ),
            "description": None,
            "has_invoice": True,
        }

    # Sinon, chercher une créance client manuelle
    d = db.query(ClientDebt).filter(ClientDebt.debt_id == debt_id).first()
    if d:
        cl = db.query(Client).filter(Client.client_id == d.client_id).first() if d.client_id else None
        amount = float(d.amount or 0)
        paid = float(d.paid_amount or 0)
        remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)
        today = date.today()
        overdue = bool(d.due_date and getattr(d.due_date, 'date', lambda: d.due_date)() < today and remaining > 0)
        st = d.status or ("paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending")))
        return {
            "id": int(d.debt_id),
            "type": "client",
            "entity_id": int(d.client_id) if d.client_id is not None else None,
            "entity_name": getattr(cl, 'name', None),
            "reference": d.reference,
            "amount": amount,
            "paid_amount": paid,
            "remaining_amount": remaining,
            "date": d.date,
            "due_date": d.due_date,
            "created_at": d.created_at,
            "status": st,
            "days_overdue": ( (today - d.due_date.date()).days if (d.due_date and remaining > 0 and hasattr(d.due_date, 'date')) else 0 ),
            "description": d.description,
            "has_invoice": False,
        }

    raise HTTPException(status_code=404, detail="Dette non trouvée")
    cl = db.query(Client).filter(Client.client_id == inv.client_id).first() if inv.client_id else None
    amount = float(inv.total or 0)
    paid = float(inv.paid_amount or 0)
    remaining = float(inv.remaining_amount or (amount - paid))
    today = date.today()
    overdue = bool(inv.due_date and getattr(inv.due_date, 'date', lambda: inv.due_date)() < today and remaining > 0)
    st = "paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending"))
    return {
        "id": int(inv.invoice_id),
        "type": "client",
        "entity_id": int(inv.client_id) if inv.client_id is not None else None,
        "entity_name": getattr(cl, 'name', None),
        "reference": inv.invoice_number,
        "invoice_number": inv.invoice_number,
        "amount": amount,
        "paid_amount": paid,
        "remaining_amount": remaining,
        "date": inv.date,
        "due_date": inv.due_date,
        "created_at": inv.created_at,
        "status": st,
        "days_overdue": ( (today - inv.due_date.date()).days if (inv.due_date and remaining > 0 and hasattr(inv.due_date, 'date')) else 0 ),
        "description": None,
    }

@router.post("/")
async def create_debt(
    debt_data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Créer une dette (client manuelle ou fournisseur)."""
    try:
        if debt_data.get("type") == "client":
            client_id = debt_data.get("entity_id") or debt_data.get("client_id")
            if not client_id:
                raise HTTPException(status_code=400, detail="Client requis")
            cl = db.query(Client).filter(Client.client_id == client_id).first()
            if not cl:
                raise HTTPException(status_code=404, detail="Client non trouvé")

            amount = float(debt_data.get("amount") or 0)
            if amount <= 0:
                raise HTTPException(status_code=400, detail="Montant invalide")
            paid = float(debt_data.get("paid_amount") or 0)
            if paid < 0 or paid > amount:
                raise HTTPException(status_code=400, detail="Montants invalides")

            d = ClientDebt(
                client_id=client_id,
                reference=str(debt_data.get("reference") or "CRE-" + datetime.now().strftime("%Y%m%d%H%M%S")),
                date=_coerce_dt(debt_data.get("date")),
                due_date=_coerce_dt(debt_data.get("due_date")),
                amount=amount,
                paid_amount=paid,
                remaining_amount=amount - paid,
                status=("paid" if amount - paid == 0 else ("partial" if paid > 0 else "pending")),
                description=debt_data.get("description"),
                notes=debt_data.get("notes"),
            )
            db.add(d)
            db.commit()
            db.refresh(d)
            return {
                "id": d.debt_id,
                "type": "client",
                "entity_id": d.client_id,
                "entity_name": cl.name,
                "reference": d.reference,
                "amount": float(d.amount or 0),
                "paid_amount": float(d.paid_amount or 0),
                "remaining_amount": float(d.remaining_amount or 0),
                "date": d.date,
                "due_date": d.due_date,
                "status": d.status,
                "description": d.description,
                "notes": d.notes,
            }
        if debt_data.get("type") != "supplier":
            raise HTTPException(status_code=405, detail="Type de dette non supporté")

        supplier_id = debt_data.get("entity_id") or debt_data.get("supplier_id")
        if not supplier_id:
            raise HTTPException(status_code=400, detail="Fournisseur requis")
        sup = db.query(Supplier).filter(Supplier.supplier_id == supplier_id).first()
        if not sup:
            raise HTTPException(status_code=404, detail="Fournisseur non trouvé")

        amount = float(debt_data.get("amount") or 0)
        paid = 0.0  # création simplifiée: non payé au départ
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Montant invalide")

        debt = SupplierDebt(
            supplier_id=supplier_id,
            reference=str(debt_data.get("reference") or "DEBT-" + datetime.now().strftime("%Y%m%d%H%M%S")),
            date=debt_data.get("date"),
            due_date=debt_data.get("due_date"),
            amount=amount,
            paid_amount=paid,
            remaining_amount=amount - paid,
            status=("paid" if amount - paid == 0 else ("partial" if paid > 0 else "pending")),
            description=debt_data.get("description"),
            notes=debt_data.get("notes"),
        )
        db.add(debt)
        db.commit()
        db.refresh(debt)
        return {
            "id": debt.debt_id,
            "type": "supplier",
            "entity_id": debt.supplier_id,
            "entity_name": sup.name,
            "reference": debt.reference,
            "amount": float(debt.amount or 0),
            "paid_amount": float(debt.paid_amount or 0),
            "remaining_amount": float(debt.remaining_amount or 0),
            "date": debt.date,
            "due_date": debt.due_date,
            "status": debt.status,
            "description": debt.description,
            "notes": debt.notes,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{debt_id}")
async def update_debt(
    debt_id: int,
    debt_data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mettre à jour une dette (client manuelle ou fournisseur)"""
    try:
        # Tenter d'abord côté dettes fournisseurs
        d = db.query(SupplierDebt).filter(SupplierDebt.debt_id == debt_id).first()
        is_supplier = bool(d)
        if not d:
            # Sinon côté créances client
            cd = db.query(ClientDebt).filter(ClientDebt.debt_id == debt_id).first()
            if not cd:
                raise HTTPException(status_code=404, detail="Dette non trouvée")
            if debt_data.get("type") and debt_data.get("type") != "client":
                raise HTTPException(status_code=400, detail="Type invalide")
            # Mise à jour champs
            for field in ["reference", "date", "due_date", "description", "notes"]:
                if field in debt_data:
                    if field in ("date", "due_date"):
                        setattr(cd, field, _coerce_dt(debt_data[field]))
                    else:
                        setattr(cd, field, debt_data[field])
            if "amount" in debt_data or "paid_amount" in debt_data:
                amount = float(debt_data.get("amount") if "amount" in debt_data else (cd.amount or 0))
                paid = float(debt_data.get("paid_amount") if "paid_amount" in debt_data else (cd.paid_amount or 0))
                if amount <= 0 or paid < 0 or paid > amount:
                    raise HTTPException(status_code=400, detail="Montants invalides")
                cd.amount = amount
                cd.paid_amount = paid
                cd.remaining_amount = amount - paid
                cd.status = "paid" if cd.remaining_amount == 0 else ("partial" if cd.paid_amount > 0 else "pending")
            db.commit()
            db.refresh(cd)
            cl = db.query(Client).filter(Client.client_id == cd.client_id).first()
            return {
                "id": cd.debt_id,
                "type": "client",
                "entity_id": cd.client_id,
                "entity_name": getattr(cl, 'name', None),
                "reference": cd.reference,
                "amount": float(cd.amount or 0),
                "paid_amount": float(cd.paid_amount or 0),
                "remaining_amount": float(cd.remaining_amount or 0),
                "date": cd.date,
                "due_date": cd.due_date,
                "status": cd.status,
                "description": cd.description,
                "notes": cd.notes,
            }
        # Branche fournisseur
        if debt_data.get("type") and debt_data.get("type") != "supplier":
            raise HTTPException(status_code=400, detail="Type invalide")
        # Champs modifiables
        for field in ["reference", "date", "due_date", "description", "notes"]:
            if field in debt_data:
                setattr(d, field, debt_data[field])
        if "amount" in debt_data or "paid_amount" in debt_data:
            amount = float(debt_data.get("amount") if "amount" in debt_data else (d.amount or 0))
            paid = float(debt_data.get("paid_amount") if "paid_amount" in debt_data else (d.paid_amount or 0))
            if amount <= 0 or paid < 0 or paid > amount:
                raise HTTPException(status_code=400, detail="Montants invalides")
            d.amount = amount
            d.paid_amount = paid
            d.remaining_amount = amount - paid
            d.status = "paid" if d.remaining_amount == 0 else ("partial" if d.paid_amount > 0 else "pending")
        db.commit()
        db.refresh(d)
        sup = db.query(Supplier).filter(Supplier.supplier_id == d.supplier_id).first()
        return {
            "id": d.debt_id,
            "type": "supplier",
            "entity_id": d.supplier_id,
            "entity_name": getattr(sup, 'name', None),
            "reference": d.reference,
            "amount": float(d.amount or 0),
            "paid_amount": float(d.paid_amount or 0),
            "remaining_amount": float(d.remaining_amount or 0),
            "date": d.date,
            "due_date": d.due_date,
            "status": d.status,
            "description": d.description,
            "notes": d.notes,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{debt_id}")
async def delete_debt(
    debt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Supprimer une dette (client manuelle ou fournisseur)"""
    try:
        d = db.query(SupplierDebt).filter(SupplierDebt.debt_id == debt_id).first()
        if d:
            db.delete(d)
            db.commit()
            return {"message": "Dette fournisseur supprimée"}
        cd = db.query(ClientDebt).filter(ClientDebt.debt_id == debt_id).first()
        if cd:
            db.delete(cd)
            db.commit()
            return {"message": "Créance client supprimée"}
        raise HTTPException(status_code=404, detail="Dette non trouvée")
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{debt_id}/payments")
async def record_payment(
    debt_id: int,
    payment_data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Enregistrer un paiement pour une dette"""
    try:
        # Vérifier d'abord si c'est une facture fournisseur
        sup_inv = db.query(SupplierInvoice).filter(SupplierInvoice.invoice_id == debt_id).first()
        if sup_inv:
            amount = round(float(payment_data.get("amount", 0)))
            if amount <= 0:
                raise HTTPException(status_code=400, detail="Le montant du paiement doit être positif")
            if amount > float(sup_inv.remaining_amount):
                raise HTTPException(status_code=400, detail="Le montant dépasse le solde restant")
            
            # Créer le paiement
            pay = SupplierInvoicePayment(
                supplier_invoice_id=sup_inv.invoice_id,
                amount=amount,
                payment_date=payment_data.get("date") or datetime.now(),
                payment_method=payment_data.get("method"),
                reference=payment_data.get("reference"),
                notes=payment_data.get("notes")
            )
            db.add(pay)
            
            # Mettre à jour la facture
            sup_inv.paid_amount = (sup_inv.paid_amount or 0) + amount
            sup_inv.remaining_amount = (sup_inv.amount or 0) - (sup_inv.paid_amount or 0)
            if sup_inv.remaining_amount <= 0:
                sup_inv.remaining_amount = 0
                sup_inv.status = "paid"
            elif sup_inv.paid_amount > 0:
                sup_inv.status = "partial"
            
            # Créer une transaction bancaire de sortie
            from ..database import BankTransaction
            bank_transaction = BankTransaction(
                type="exit",
                motif="Paiement fournisseur",
                description=f"Paiement facture {sup_inv.invoice_number}",
                amount=amount,
                date=payment_data.get("date", datetime.now()).date() if isinstance(payment_data.get("date"), datetime) else date.today(),
                method="virement" if payment_data.get("method") in ["virement", "virement bancaire"] else "cheque",
                reference=payment_data.get("reference") or f"PAY-{sup_inv.invoice_number}"
            )
            db.add(bank_transaction)
            
            db.commit()
            return {"message": "Paiement enregistré", "remaining": float(sup_inv.remaining_amount or 0)}
        
        # Sinon vérifier si c'est une dette fournisseur manuelle
        d = db.query(SupplierDebt).filter(SupplierDebt.debt_id == debt_id).first()
        if d:
            amount = round(float(payment_data.get("amount", 0)))
            if amount <= 0:
                raise HTTPException(status_code=400, detail="Le montant du paiement doit être positif")
            if amount > float(d.remaining_amount or (d.amount or 0) - (d.paid_amount or 0)):
                raise HTTPException(status_code=400, detail="Le montant dépasse le solde restant")
            pay = SupplierDebtPayment(
                debt_id=d.debt_id,
                amount=amount,
                payment_method=payment_data.get("method"),
                reference=payment_data.get("reference"),
                notes=payment_data.get("notes")
            )
            db.add(pay)
            d.paid_amount = (d.paid_amount or 0) + amount
            d.remaining_amount = (d.amount or 0) - (d.paid_amount or 0)
            if d.remaining_amount <= 0:
                d.remaining_amount = 0
                d.status = "paid"
            elif d.paid_amount > 0:
                d.status = "partial"
            db.commit()
            return {"message": "Paiement enregistré", "remaining": float(d.remaining_amount or 0)}

        # Sinon vérifier si c'est une créance client manuelle
        cd = db.query(ClientDebt).filter(ClientDebt.debt_id == debt_id).first()
        if cd:
            amount = round(float(payment_data.get("amount", 0)))
            if amount <= 0:
                raise HTTPException(status_code=400, detail="Le montant du paiement doit être positif")
            if amount > float(cd.remaining_amount or (cd.amount or 0) - (cd.paid_amount or 0)):
                raise HTTPException(status_code=400, detail="Le montant dépasse le solde restant")
            pay = ClientDebtPayment(
                debt_id=cd.debt_id,
                amount=amount,
                payment_date=_coerce_dt(payment_data.get("date")) or datetime.now(),
                payment_method=payment_data.get("method"),
                reference=payment_data.get("reference"),
                notes=payment_data.get("notes")
            )
            db.add(pay)
            cd.paid_amount = (cd.paid_amount or 0) + amount
            cd.remaining_amount = (cd.amount or 0) - (cd.paid_amount or 0)
            if cd.remaining_amount <= 0:
                cd.remaining_amount = 0
                cd.status = "paid"
            elif cd.paid_amount > 0:
                cd.status = "partial"
            db.commit()
            return {"message": "Paiement enregistré", "remaining": float(cd.remaining_amount or 0)}

        # Sinon: dette client via facture
        inv = db.query(Invoice).filter(Invoice.invoice_id == debt_id).first()
        if not inv:
            raise HTTPException(status_code=404, detail="Dette/Facture non trouvée")

        amount = round(float(payment_data.get("amount", 0)))
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Le montant du paiement doit être positif")
        
        remaining = float(inv.remaining_amount or 0)
        if amount > remaining:
            raise HTTPException(status_code=400, detail="Le montant dépasse le solde restant")

        pay = InvoicePayment(
            invoice_id=inv.invoice_id,
            amount=amount,
            payment_method=payment_data.get("method"),
            reference=payment_data.get("reference"),
            notes=payment_data.get("notes")
        )
        db.add(pay)

        inv.paid_amount = (inv.paid_amount or 0) + amount
        inv.remaining_amount = (inv.total or 0) - (inv.paid_amount or 0)
        if inv.remaining_amount <= 0:
            inv.remaining_amount = 0
            inv.status = "payée"
        elif inv.paid_amount > 0:
            inv.status = "partiellement payée"

        db.commit()
        return {"message": "Paiement enregistré", "remaining": float(inv.remaining_amount or 0)}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats/summary")
async def get_debts_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer les statistiques des dettes"""
    try:
        today = date.today()
        
        # 1. Statistiques des créances clients
        invs = db.query(Invoice).all()
        def remaining_of(i):
            return float(i.remaining_amount if i.remaining_amount is not None else max(0.0, float(i.total or 0) - float(i.paid_amount or 0)))
        open_invoices = [i for i in invs if remaining_of(i) > 0]
        client_total_amount = sum(float(i.total or 0) for i in open_invoices)
        client_total_paid = sum(float(i.paid_amount or 0) for i in open_invoices)
        client_total_remaining = sum(remaining_of(i) for i in open_invoices)
        
        # 2. Statistiques des dettes fournisseurs
        supplier_invs = db.query(SupplierInvoice).filter(SupplierInvoice.remaining_amount > 0).all()
        supplier_total_amount = sum(float(i.amount or 0) for i in supplier_invs)
        supplier_total_paid = sum(float(i.paid_amount or 0) for i in supplier_invs)
        supplier_total_remaining = sum(float(i.remaining_amount or 0) for i in supplier_invs)
        
        # 3. Calcul des totaux combinés
        total_client_debts = len(open_invoices)
        total_supplier_debts = len(supplier_invs)
        
        # Overdue clients
        client_overdue = [i for i in open_invoices if (i.due_date and getattr(i.due_date, 'date', lambda: i.due_date)() < today and remaining_of(i) > 0)]
        # Overdue fournisseurs
        supplier_overdue = [i for i in supplier_invs if (i.due_date and i.due_date.date() < today)]
        
        # Pending (non payé du tout)
        client_pending = [i for i in open_invoices if float(i.paid_amount or 0) == 0 and not (i in client_overdue)]
        supplier_pending = [i for i in supplier_invs if float(i.paid_amount or 0) == 0 and not (i in supplier_overdue)]
        
        return {
            "total_debts": total_client_debts + total_supplier_debts,
            "client_debts_count": total_client_debts,
            "supplier_debts_count": total_supplier_debts,
            "client_total_amount": client_total_amount,
            "client_total_paid": client_total_paid,
            "client_total_remaining": client_total_remaining,
            "supplier_total_amount": supplier_total_amount,
            "supplier_total_paid": supplier_total_paid,
            "supplier_total_remaining": supplier_total_remaining,
            "total_amount": client_total_amount + supplier_total_amount,
            "total_paid": client_total_paid + supplier_total_paid,
            "total_remaining": client_total_remaining + supplier_total_remaining,
            "overdue_count": len(client_overdue) + len(supplier_overdue),
            "overdue_amount": sum(remaining_of(i) for i in client_overdue) + sum(float(i.remaining_amount or 0) for i in supplier_overdue),
            "pending_count": len(client_pending) + len(supplier_pending),
            "pending_amount": sum(remaining_of(i) for i in client_pending) + sum(float(i.remaining_amount or 0) for i in supplier_pending)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
