from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, date

from ..database import (
    User, Invoice, Client, InvoicePayment,
    Supplier, SupplierDebt, SupplierDebtPayment,
    SupplierInvoice, SupplierInvoicePayment,
    ClientDebt, ClientDebtPayment, BankTransaction,
    get_next_id,
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

def _safe_date(v):
    """Extract .date() from a datetime-like value, or return it as-is if already a date."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return v

@router.get("/")
async def get_debts(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    type: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """Recuperer les dettes clients et fournisseurs."""
    try:
        debts_all = []
        today = date.today()

        # 1. Recuperer les creances clients (inclus: factures impayees + creances manuelles)
        if type is None or type == "client":
            # a) Factures client impayees
            # Build a filter for invoices with remaining amount > 0
            all_invoices = await Invoice.find().to_list()
            # Filter in Python: remaining > 0
            open_invoices = []
            for inv in all_invoices:
                amount = float(inv.total or 0)
                paid = float(inv.paid_amount or 0)
                remaining = float(inv.remaining_amount if inv.remaining_amount is not None else (amount - paid))
                if remaining > 0:
                    open_invoices.append((inv, remaining))

            # Pre-fetch clients for these invoices
            client_ids = list(set(inv.client_id for inv, _ in open_invoices if inv.client_id is not None))
            clients_list = await Client.find({"client_id": {"$in": client_ids}}).to_list() if client_ids else []
            clients_map = {c.client_id: c for c in clients_list}

            for inv, remaining in open_invoices:
                # Search filter
                if search:
                    s = search.lower()
                    inv_num = (inv.invoice_number or "").lower()
                    cl = clients_map.get(inv.client_id)
                    cl_name = (cl.name if cl else "").lower()
                    if s not in inv_num and s not in cl_name:
                        continue

                cl = clients_map.get(inv.client_id)
                amount = float(inv.total or 0)
                paid = float(inv.paid_amount or 0)
                inv_date = _safe_date(inv.due_date)
                overdue = bool(inv_date and inv_date < today and remaining > 0)
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
                    "days_overdue": ((today - inv_date).days if (inv_date and remaining > 0) else 0),
                    "description": None,
                    "has_invoice": True,
                })

            # b) Creances clients manuelles
            client_debts_all = await ClientDebt.find().to_list()
            # Pre-fetch clients for debts
            cd_client_ids = list(set(d.client_id for d in client_debts_all if d.client_id is not None))
            cd_clients_list = await Client.find({"client_id": {"$in": cd_client_ids}}).to_list() if cd_client_ids else []
            cd_clients_map = {c.client_id: c for c in cd_clients_list}

            for d in client_debts_all:
                cl = cd_clients_map.get(d.client_id)
                if search:
                    s = search.lower()
                    ref = (d.reference or "").lower()
                    cl_name = (cl.name if cl else "").lower()
                    if s not in ref and s not in cl_name:
                        continue

                amount = float(d.amount or 0)
                paid = float(d.paid_amount or 0)
                remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)
                d_date = _safe_date(d.due_date)
                overdue = bool(d_date and d_date < today and remaining > 0)
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
                    "days_overdue": ((today - d_date).days if (d_date and remaining > 0) else 0),
                    "description": d.description,
                    "has_invoice": False,
                })

        # 2. Recuperer les dettes fournisseurs (factures fournisseur non payees)
        if type is None or type == "supplier":
            supplier_invs = await SupplierInvoice.find(
                {"remaining_amount": {"$gt": 0}}
            ).to_list()

            # Pre-fetch suppliers
            sup_ids = list(set(si.supplier_id for si in supplier_invs if si.supplier_id is not None))
            suppliers_list = await Supplier.find({"supplier_id": {"$in": sup_ids}}).to_list() if sup_ids else []
            suppliers_map = {s.supplier_id: s for s in suppliers_list}

            for sup_inv in supplier_invs:
                sup = suppliers_map.get(sup_inv.supplier_id)
                if search:
                    s = search.lower()
                    inv_num = (sup_inv.invoice_number or "").lower()
                    sup_name = (sup.name if sup else "").lower()
                    if s not in inv_num and s not in sup_name:
                        continue

                amount = float(sup_inv.amount or 0)
                paid = float(sup_inv.paid_amount or 0)
                remaining = float(sup_inv.remaining_amount or 0)
                si_date = _safe_date(sup_inv.due_date)
                overdue = bool(si_date and si_date < today and remaining > 0)
                if remaining <= 0:
                    st = "paid"
                elif overdue:
                    st = "overdue"
                elif sup_inv.status:
                    st = sup_inv.status
                else:
                    st = ("partial" if paid > 0 else "pending")
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
                    "days_overdue": ((today - si_date).days if (si_date and remaining > 0) else 0),
                    "description": sup_inv.description,
                })

        # Trier par date decroissante
        debts_all.sort(key=lambda x: x.get('date') or datetime.min, reverse=True)

        # Pagination cote Python sur la liste filtree
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

@router.get("/stats/summary")
async def get_debts_stats(
    current_user: User = Depends(get_current_user),
):
    """Recuperer les statistiques des dettes"""
    try:
        today = date.today()

        # 1. Statistiques des creances clients
        invs = await Invoice.find().to_list()
        def remaining_of(i):
            return float(i.remaining_amount if i.remaining_amount is not None else max(0.0, float(i.total or 0) - float(i.paid_amount or 0)))
        open_invoices = [i for i in invs if remaining_of(i) > 0]
        client_total_amount = sum(float(i.total or 0) for i in open_invoices)
        client_total_paid = sum(float(i.paid_amount or 0) for i in open_invoices)
        client_total_remaining = sum(remaining_of(i) for i in open_invoices)

        # 2. Statistiques des dettes fournisseurs
        supplier_invs = await SupplierInvoice.find({"remaining_amount": {"$gt": 0}}).to_list()
        supplier_total_amount = sum(float(i.amount or 0) for i in supplier_invs)
        supplier_total_paid = sum(float(i.paid_amount or 0) for i in supplier_invs)
        supplier_total_remaining = sum(float(i.remaining_amount or 0) for i in supplier_invs)

        # 3. Calcul des totaux combines
        total_client_debts = len(open_invoices)
        total_supplier_debts = len(supplier_invs)

        # Overdue clients
        client_overdue = [i for i in open_invoices if (_safe_date(i.due_date) and _safe_date(i.due_date) < today and remaining_of(i) > 0)]
        # Overdue fournisseurs
        supplier_overdue = [i for i in supplier_invs if (_safe_date(i.due_date) and _safe_date(i.due_date) < today)]

        # Pending (non paye du tout)
        client_overdue_set = set(id(i) for i in client_overdue)
        supplier_overdue_set = set(id(i) for i in supplier_overdue)
        client_pending = [i for i in open_invoices if float(i.paid_amount or 0) == 0 and id(i) not in client_overdue_set]
        supplier_pending = [i for i in supplier_invs if float(i.paid_amount or 0) == 0 and id(i) not in supplier_overdue_set]

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

@router.get("/{debt_id}")
async def get_debt(
    debt_id: int,
    current_user: User = Depends(get_current_user),
):
    """Recuperer une dette par ID"""
    # D'abord, chercher une facture client
    inv = await Invoice.find_one({"invoice_id": debt_id})
    if inv:
        cl = await Client.find_one({"client_id": inv.client_id}) if inv.client_id else None
        amount = float(inv.total or 0)
        paid = float(inv.paid_amount or 0)
        remaining = float(inv.remaining_amount or (amount - paid))
        today = date.today()
        inv_date = _safe_date(inv.due_date)
        overdue = bool(inv_date and inv_date < today and remaining > 0)
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
            "days_overdue": ((today - inv_date).days if (inv_date and remaining > 0) else 0),
            "description": None,
            "has_invoice": True,
        }

    # Sinon, chercher une creance client manuelle
    d = await ClientDebt.find_one({"debt_id": debt_id})
    if d:
        cl = await Client.find_one({"client_id": d.client_id}) if d.client_id else None
        amount = float(d.amount or 0)
        paid = float(d.paid_amount or 0)
        remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)
        today = date.today()
        d_date = _safe_date(d.due_date)
        overdue = bool(d_date and d_date < today and remaining > 0)
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
            "days_overdue": ((today - d_date).days if (d_date and remaining > 0) else 0),
            "description": d.description,
            "has_invoice": False,
        }

    raise HTTPException(status_code=404, detail="Dette non trouvee")

@router.post("/")
async def create_debt(
    debt_data: dict,
    current_user: User = Depends(get_current_user),
):
    """Creer une dette (client manuelle ou fournisseur)."""
    try:
        if debt_data.get("type") == "client":
            client_id = debt_data.get("entity_id") or debt_data.get("client_id")
            if not client_id:
                raise HTTPException(status_code=400, detail="Client requis")
            cl = await Client.find_one({"client_id": client_id})
            if not cl:
                raise HTTPException(status_code=404, detail="Client non trouve")

            amount = float(debt_data.get("amount") or 0)
            if amount <= 0:
                raise HTTPException(status_code=400, detail="Montant invalide")
            paid = float(debt_data.get("paid_amount") or 0)
            if paid < 0 or paid > amount:
                raise HTTPException(status_code=400, detail="Montants invalides")

            next_id = await get_next_id("client_debts")
            d = ClientDebt(
                debt_id=next_id,
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
            await d.insert()
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
            raise HTTPException(status_code=405, detail="Type de dette non supporte")

        supplier_id = debt_data.get("entity_id") or debt_data.get("supplier_id")
        if not supplier_id:
            raise HTTPException(status_code=400, detail="Fournisseur requis")
        sup = await Supplier.find_one({"supplier_id": supplier_id})
        if not sup:
            raise HTTPException(status_code=404, detail="Fournisseur non trouve")

        amount = float(debt_data.get("amount") or 0)
        paid = 0.0  # creation simplifiee: non paye au depart
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Montant invalide")

        next_id = await get_next_id("supplier_debts")
        debt = SupplierDebt(
            debt_id=next_id,
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
        await debt.insert()
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
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{debt_id}")
async def update_debt(
    debt_id: int,
    debt_data: dict,
    current_user: User = Depends(get_current_user),
):
    """Mettre a jour une dette (client manuelle ou fournisseur)"""
    try:
        # Tenter d'abord cote dettes fournisseurs
        d = await SupplierDebt.find_one({"debt_id": debt_id})
        if not d:
            # Sinon cote creances client
            cd = await ClientDebt.find_one({"debt_id": debt_id})
            if not cd:
                raise HTTPException(status_code=404, detail="Dette non trouvee")
            if debt_data.get("type") and debt_data.get("type") != "client":
                raise HTTPException(status_code=400, detail="Type invalide")
            # Mise a jour champs
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
            await cd.save()
            cl = await Client.find_one({"client_id": cd.client_id})
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
        await d.save()
        sup = await Supplier.find_one({"supplier_id": d.supplier_id})
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
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{debt_id}")
async def delete_debt(
    debt_id: int,
    current_user: User = Depends(get_current_user),
):
    """Supprimer une dette (client manuelle ou fournisseur)"""
    try:
        d = await SupplierDebt.find_one({"debt_id": debt_id})
        if d:
            await d.delete()
            return {"message": "Dette fournisseur supprimee"}
        cd = await ClientDebt.find_one({"debt_id": debt_id})
        if cd:
            await cd.delete()
            return {"message": "Creance client supprimee"}
        raise HTTPException(status_code=404, detail="Dette non trouvee")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{debt_id}/payments")
async def record_payment(
    debt_id: int,
    payment_data: dict,
    current_user: User = Depends(get_current_user),
):
    """Enregistrer un paiement pour une dette"""
    try:
        # Verifier d'abord si c'est une facture fournisseur
        sup_inv = await SupplierInvoice.find_one({"invoice_id": debt_id})
        if sup_inv:
            amount = round(float(payment_data.get("amount", 0)))
            if amount <= 0:
                raise HTTPException(status_code=400, detail="Le montant du paiement doit etre positif")
            if amount > float(sup_inv.remaining_amount):
                raise HTTPException(status_code=400, detail="Le montant depasse le solde restant")

            # Creer le paiement
            next_pay_id = await get_next_id("supplier_invoice_payments")
            pay = SupplierInvoicePayment(
                payment_id=next_pay_id,
                supplier_invoice_id=sup_inv.invoice_id,
                amount=amount,
                payment_date=payment_data.get("date") or datetime.now(),
                payment_method=payment_data.get("method"),
                reference=payment_data.get("reference"),
                notes=payment_data.get("notes")
            )
            await pay.insert()

            # Mettre a jour la facture
            sup_inv.paid_amount = (sup_inv.paid_amount or 0) + amount
            sup_inv.remaining_amount = (sup_inv.amount or 0) - (sup_inv.paid_amount or 0)
            if sup_inv.remaining_amount <= 0:
                sup_inv.remaining_amount = 0
                sup_inv.status = "paid"
            elif sup_inv.paid_amount > 0:
                sup_inv.status = "partial"
            await sup_inv.save()

            # Creer une transaction bancaire de sortie
            next_bt_id = await get_next_id("bank_transactions")
            pay_date = payment_data.get("date")
            if isinstance(pay_date, datetime):
                bt_date = pay_date.date()
            elif isinstance(pay_date, date):
                bt_date = pay_date
            else:
                bt_date = date.today()
            bank_transaction = BankTransaction(
                transaction_id=next_bt_id,
                type="exit",
                motif="Paiement fournisseur",
                description=f"Paiement facture {sup_inv.invoice_number}",
                amount=amount,
                date=bt_date,
                method="virement" if payment_data.get("method") in ["virement", "virement bancaire"] else "cheque",
                reference=payment_data.get("reference") or f"PAY-{sup_inv.invoice_number}"
            )
            await bank_transaction.insert()

            return {"message": "Paiement enregistre", "remaining": float(sup_inv.remaining_amount or 0)}

        # Sinon verifier si c'est une dette fournisseur manuelle
        d = await SupplierDebt.find_one({"debt_id": debt_id})
        if d:
            amount = round(float(payment_data.get("amount", 0)))
            if amount <= 0:
                raise HTTPException(status_code=400, detail="Le montant du paiement doit etre positif")
            if amount > float(d.remaining_amount or (d.amount or 0) - (d.paid_amount or 0)):
                raise HTTPException(status_code=400, detail="Le montant depasse le solde restant")
            next_pay_id = await get_next_id("supplier_debt_payments")
            pay = SupplierDebtPayment(
                payment_id=next_pay_id,
                debt_id=d.debt_id,
                amount=amount,
                payment_method=payment_data.get("method"),
                reference=payment_data.get("reference"),
                notes=payment_data.get("notes")
            )
            await pay.insert()
            d.paid_amount = (d.paid_amount or 0) + amount
            d.remaining_amount = (d.amount or 0) - (d.paid_amount or 0)
            if d.remaining_amount <= 0:
                d.remaining_amount = 0
                d.status = "paid"
            elif d.paid_amount > 0:
                d.status = "partial"
            await d.save()
            return {"message": "Paiement enregistre", "remaining": float(d.remaining_amount or 0)}

        # Sinon verifier si c'est une creance client manuelle
        cd = await ClientDebt.find_one({"debt_id": debt_id})
        if cd:
            amount = round(float(payment_data.get("amount", 0)))
            if amount <= 0:
                raise HTTPException(status_code=400, detail="Le montant du paiement doit etre positif")
            if amount > float(cd.remaining_amount or (cd.amount or 0) - (cd.paid_amount or 0)):
                raise HTTPException(status_code=400, detail="Le montant depasse le solde restant")
            next_pay_id = await get_next_id("client_debt_payments")
            pay = ClientDebtPayment(
                payment_id=next_pay_id,
                debt_id=cd.debt_id,
                amount=amount,
                payment_date=_coerce_dt(payment_data.get("date")) or datetime.now(),
                payment_method=payment_data.get("method"),
                reference=payment_data.get("reference"),
                notes=payment_data.get("notes")
            )
            await pay.insert()
            cd.paid_amount = (cd.paid_amount or 0) + amount
            cd.remaining_amount = (cd.amount or 0) - (cd.paid_amount or 0)
            if cd.remaining_amount <= 0:
                cd.remaining_amount = 0
                cd.status = "paid"
            elif cd.paid_amount > 0:
                cd.status = "partial"
            await cd.save()
            return {"message": "Paiement enregistre", "remaining": float(cd.remaining_amount or 0)}

        # Sinon: dette client via facture
        inv = await Invoice.find_one({"invoice_id": debt_id})
        if not inv:
            raise HTTPException(status_code=404, detail="Dette/Facture non trouvee")

        amount = round(float(payment_data.get("amount", 0)))
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Le montant du paiement doit etre positif")

        remaining = float(inv.remaining_amount or 0)
        if amount > remaining:
            raise HTTPException(status_code=400, detail="Le montant depasse le solde restant")

        next_pay_id = await get_next_id("invoice_payments")
        pay = InvoicePayment(
            payment_id=next_pay_id,
            invoice_id=inv.invoice_id,
            amount=amount,
            payment_method=payment_data.get("method"),
            reference=payment_data.get("reference"),
            notes=payment_data.get("notes")
        )
        await pay.insert()

        inv.paid_amount = (inv.paid_amount or 0) + amount
        inv.remaining_amount = (inv.total or 0) - (inv.paid_amount or 0)
        if inv.remaining_amount <= 0:
            inv.remaining_amount = 0
            inv.status = "payee"
        elif inv.paid_amount > 0:
            inv.status = "partiellement payee"

        await inv.save()
        return {"message": "Paiement enregistre", "remaining": float(inv.remaining_amount or 0)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
