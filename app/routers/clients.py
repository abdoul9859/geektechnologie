from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from ..database import Client, Invoice, ClientDebt, get_next_id
from ..schemas import ClientCreate, ClientUpdate, ClientResponse
from ..auth import get_current_user, require_any_role
import logging
import re

router = APIRouter(prefix="/api/clients", tags=["clients"])

@router.get("/", response_model=List[ClientResponse])
async def list_clients(
    skip: int = 0, limit: int = 100,
    search: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    query = {}
    if search:
        pattern = re.compile(re.escape(search), re.IGNORECASE)
        query = {"$or": [
            {"name": {"$regex": pattern}},
            {"email": {"$regex": pattern}},
            {"phone": {"$regex": pattern}},
            {"contact": {"$regex": pattern}},
        ]}
    clients = (
        await Client.find(query)
        .sort(-Client.client_id)
        .skip(skip).limit(limit)
        .to_list()
    )
    return clients

@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(client_id: int, current_user=Depends(get_current_user)):
    client = await Client.find_one(Client.client_id == client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client non trouvé")
    return client

@router.get("/{client_id}/details")
async def get_client_details(client_id: int, current_user=Depends(get_current_user)):
    client = await Client.find_one(Client.client_id == client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client non trouvé")
    invoices = await Invoice.find(Invoice.client_id == client_id).sort(-Invoice.date).to_list()
    client_debts = await ClientDebt.find(ClientDebt.client_id == client_id).sort(-ClientDebt.date).to_list()
    debts = [
        {
            "debt_id": d.debt_id,
            "reference": d.reference,
            "date": d.date,
            "due_date": d.due_date,
            "amount": float(d.amount or 0),
            "paid_amount": float(d.paid_amount or 0),
            "remaining_amount": float(d.remaining_amount if d.remaining_amount is not None else (float(d.amount or 0) - float(d.paid_amount or 0))),
            "status": d.status or ("paid" if (float(d.remaining_amount or (float(d.amount or 0) - float(d.paid_amount or 0))) <= 0) else ("partial" if float(d.paid_amount or 0) > 0 else "pending")),
            "description": d.description,
        }
        for d in client_debts
    ]
    total_invoiced = float(sum(float(i.total or 0) for i in invoices))
    total_paid = float(sum(float(i.paid_amount or 0) for i in invoices))
    total_due = total_invoiced - total_paid
    total_debts = float(sum(float(d.get("remaining_amount", 0) or 0) for d in debts))
    return {
        "client": ClientResponse(**client.dict()),
        "stats": {
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "total_due": total_due,
            "total_debts": total_debts,
        },
        "invoices": [
            {
                "invoice_id": inv.invoice_id,
                "invoice_number": inv.invoice_number,
                "date": inv.date,
                "status": inv.status,
                "total": float(inv.total or 0),
                "paid": float(inv.paid_amount or 0),
                "remaining": float(inv.remaining_amount or 0),
            }
            for inv in invoices
        ],
        "debts": debts,
    }

@router.post("/", response_model=ClientResponse)
async def create_client(client_data: ClientCreate, current_user=Depends(get_current_user)):
    try:
        if client_data.phone:
            incoming_phone = client_data.phone.strip()
            if incoming_phone:
                existing = await Client.find_one(
                    {"phone": {"$regex": f"^{re.escape(incoming_phone)}$", "$options": "i"}}
                )
                if existing:
                    raise HTTPException(status_code=400, detail="Un client avec ce numéro de téléphone existe déjà")
        new_id = await get_next_id("clients")
        db_client = Client(
            client_id=new_id,
            name=client_data.name,
            contact=client_data.contact,
            email=client_data.email,
            phone=client_data.phone,
            address=client_data.address,
            city=client_data.city,
            postal_code=client_data.postal_code,
            country=client_data.country,
            tax_number=client_data.tax_number,
            notes=client_data.notes,
        )
        await db_client.insert()
        return db_client
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la création du client: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/{client_id}", response_model=ClientResponse)
async def update_client(
    client_id: int, client_data: ClientUpdate,
    current_user=Depends(require_any_role(["user", "manager"])),
):
    try:
        client = await Client.find_one(Client.client_id == client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
        update_data = client_data.dict(exclude_unset=True)
        new_phone = update_data.get("phone")
        if new_phone is not None:
            new_phone_stripped = new_phone.strip()
            if new_phone_stripped:
                conflict = await Client.find_one(
                    {"phone": {"$regex": f"^{re.escape(new_phone_stripped)}$", "$options": "i"},
                     "client_id": {"$ne": client_id}}
                )
                if conflict:
                    raise HTTPException(status_code=400, detail="Un autre client possède déjà ce numéro de téléphone")
            else:
                update_data["phone"] = None
        for field, value in update_data.items():
            setattr(client, field, value)
        await client.save()
        return client
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la mise à jour du client: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.delete("/{client_id}")
async def delete_client(client_id: int, current_user=Depends(get_current_user)):
    try:
        client = await Client.find_one(Client.client_id == client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
        await client.delete()
        return {"message": "Client supprimé avec succès"}
    except Exception as e:
        logging.error(f"Erreur lors de la suppression du client: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
