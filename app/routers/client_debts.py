from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import date

from ..database import Client, Invoice, InvoiceItem, ClientDebt
from ..auth import get_current_user

router = APIRouter(prefix="/api/clients", tags=["client_debts"])


@router.get("/{client_id}/debts")
async def get_client_debts(
    client_id: int,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    cl = await Client.find_one(Client.client_id == client_id)
    if not cl:
        raise HTTPException(status_code=404, detail="Client non trouve")

    # Fetch invoices with remaining > 0 for this client
    inv_filters: dict = {"client_id": client_id}
    if date_from:
        try:
            inv_filters.setdefault("date", {})["$gte"] = date_from
        except Exception:
            pass
    if date_to:
        try:
            inv_filters.setdefault("date", {})["$lte"] = date_to
        except Exception:
            pass

    all_invoices = await Invoice.find(inv_filters).sort([("date", -1)]).to_list()

    invoices = []
    today = date.today()
    for inv in all_invoices:
        amount = float(inv.total or 0)
        paid = float(inv.paid_amount or 0)
        remaining = float(
            inv.remaining_amount if inv.remaining_amount is not None else max(0.0, amount - paid)
        )

        if remaining <= 0:
            continue

        overdue = bool(
            inv.due_date
            and getattr(inv.due_date, "date", lambda: inv.due_date)() < today
            and remaining > 0
        )
        st = (
            "paid"
            if remaining <= 0
            else ("overdue" if overdue else ("partial" if paid > 0 else "pending"))
        )
        if status and st != status:
            continue

        # Fetch items for this invoice
        items_list = await InvoiceItem.find(
            InvoiceItem.invoice_id == inv.invoice_id
        ).to_list()

        items = [
            {
                "item_id": it.item_id,
                "product_id": it.product_id,
                "product_name": it.product_name,
                "quantity": int(it.quantity or 0),
                "price": float(it.price or 0),
                "total": float(it.total or 0),
            }
            for it in items_list
        ]
        invoices.append(
            {
                "id": int(inv.invoice_id),
                "invoice_number": inv.invoice_number,
                "date": inv.date,
                "due_date": inv.due_date,
                "amount": amount,
                "paid_amount": paid,
                "remaining_amount": remaining,
                "status": st,
                "items": items,
            }
        )

    # Manual debts (ClientDebt)
    cd_filters: dict = {"client_id": client_id}
    if date_from:
        try:
            cd_filters.setdefault("date", {})["$gte"] = date_from
        except Exception:
            pass
    if date_to:
        try:
            cd_filters.setdefault("date", {})["$lte"] = date_to
        except Exception:
            pass

    all_debts = await ClientDebt.find(cd_filters).sort([("date", -1)]).to_list()

    manual_debts = []
    for d in all_debts:
        amount = float(d.amount or 0)
        paid = float(d.paid_amount or 0)
        remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)

        if remaining <= 0:
            continue

        overdue = bool(
            d.due_date
            and getattr(d.due_date, "date", lambda: d.due_date)() < today
            and remaining > 0
        )
        st = d.status or (
            "paid"
            if remaining <= 0
            else ("overdue" if overdue else ("partial" if paid > 0 else "pending"))
        )
        if status and st != status:
            continue
        manual_debts.append(
            {
                "id": int(d.debt_id),
                "reference": d.reference,
                "date": d.date,
                "due_date": d.due_date,
                "amount": amount,
                "paid_amount": paid,
                "remaining_amount": remaining,
                "status": st,
                "description": d.description,
            }
        )

    total_amount = sum(x.get("amount", 0.0) for x in invoices) + sum(
        x.get("amount", 0.0) for x in manual_debts
    )
    total_paid = sum(x.get("paid_amount", 0.0) for x in invoices) + sum(
        x.get("paid_amount", 0.0) for x in manual_debts
    )
    total_remaining = sum(x.get("remaining_amount", 0.0) for x in invoices) + sum(
        x.get("remaining_amount", 0.0) for x in manual_debts
    )

    overdue_count = sum(1 for x in invoices if x.get("status") == "overdue") + sum(
        1 for x in manual_debts if x.get("status") == "overdue"
    )

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
