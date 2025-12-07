from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional
from datetime import date

from ..database import (
    get_db, User, Client, Invoice, InvoiceItem, ClientDebt
)
from ..auth import get_current_user

router = APIRouter(prefix="/api/clients", tags=["client_debts"]) 

@router.get("/{client_id}/debts")
async def get_client_debts(
    client_id: int,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cl = db.query(Client).filter(Client.client_id == client_id).first()
    if not cl:
        raise HTTPException(status_code=404, detail="Client non trouvé")

    remaining_sql = func.coalesce(Invoice.remaining_amount, Invoice.total - func.coalesce(Invoice.paid_amount, 0))
    inv_q = (
        db.query(Invoice)
        .options(joinedload(Invoice.items))
        .filter(Invoice.client_id == client_id)
        .filter(remaining_sql > 0)
        .order_by(Invoice.date.desc())
    )
    if date_from:
        try:
            inv_q = inv_q.filter(Invoice.date >= date_from)
        except Exception:
            pass
    if date_to:
        try:
            inv_q = inv_q.filter(Invoice.date <= date_to)
        except Exception:
            pass

    invoices = []
    today = date.today()
    for inv in inv_q.all():
        amount = float(inv.total or 0)
        paid = float(inv.paid_amount or 0)
        remaining = float(inv.remaining_amount if inv.remaining_amount is not None else max(0.0, amount - paid))
        overdue = bool(inv.due_date and getattr(inv.due_date, 'date', lambda: inv.due_date)() < today and remaining > 0)
        st = "paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending"))
        if status and st != status:
            continue
        items = [
            {
                "item_id": it.item_id,
                "product_id": it.product_id,
                "product_name": it.product_name,
                "quantity": int(it.quantity or 0),
                "price": float(it.price or 0),
                "total": float(it.total or 0),
            }
            for it in (inv.items or [])
        ]
        invoices.append({
            "id": int(inv.invoice_id),
            "invoice_number": inv.invoice_number,
            "date": inv.date,
            "due_date": inv.due_date,
            "amount": amount,
            "paid_amount": paid,
            "remaining_amount": remaining,
            "status": st,
            "items": items,
        })

    remaining_cd = func.coalesce(ClientDebt.remaining_amount, ClientDebt.amount - func.coalesce(ClientDebt.paid_amount, 0))
    cd_q = (
        db.query(ClientDebt)
        .filter(ClientDebt.client_id == client_id)
        .filter(remaining_cd > 0)
        .order_by(ClientDebt.date.desc())
    )
    if date_from:
        try:
            cd_q = cd_q.filter(ClientDebt.date >= date_from)
        except Exception:
            pass
    if date_to:
        try:
            cd_q = cd_q.filter(ClientDebt.date <= date_to)
        except Exception:
            pass

    manual_debts = []
    for d in cd_q.all():
        amount = float(d.amount or 0)
        paid = float(d.paid_amount or 0)
        remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)
        overdue = bool(d.due_date and getattr(d.due_date, 'date', lambda: d.due_date)() < today and remaining > 0)
        st = d.status or ("paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending")))
        if status and st != status:
            continue
        manual_debts.append({
            "id": int(d.debt_id),
            "reference": d.reference,
            "date": d.date,
            "due_date": d.due_date,
            "amount": amount,
            "paid_amount": paid,
            "remaining_amount": remaining,
            "status": st,
            "description": d.description,
        })

    total_amount = sum(x.get("amount", 0.0) for x in invoices) + sum(x.get("amount", 0.0) for x in manual_debts)
    total_paid = sum(x.get("paid_amount", 0.0) for x in invoices) + sum(x.get("paid_amount", 0.0) for x in manual_debts)
    total_remaining = sum(x.get("remaining_amount", 0.0) for x in invoices) + sum(x.get("remaining_amount", 0.0) for x in manual_debts)

    overdue_count = sum(1 for x in invoices if x.get("status") == "overdue") + sum(1 for x in manual_debts if x.get("status") == "overdue")

    return {
        "client": {
            "client_id": cl.client_id,
            "name": cl.name,
            "email": cl.email,
            "phone": cl.phone,
        },
        "summary": {
            "total_amount": total_amount,
            "total_paid": total_paid,
            "total_remaining": total_remaining,
            "overdue_count": overdue_count,
        },
        "invoices": invoices,
        "manual_debts": manual_debts,
    }
