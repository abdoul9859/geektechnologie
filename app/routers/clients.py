from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from sqlalchemy import func
from ..database import get_db, Client, Invoice, ClientDebt
from ..schemas import ClientCreate, ClientUpdate, ClientResponse
from ..auth import get_current_user, require_any_role
import logging

router = APIRouter(prefix="/api/clients", tags=["clients"])

@router.get("/", response_model=List[ClientResponse])
async def list_clients(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les clients avec recherche.
    - Trie par défaut: plus récents d'abord (client_id DESC).
    - Recherche sur name/email/phone (ilike).
    """
    query = db.query(Client)

    if search:
        like = f"%{search}%"
        query = query.filter(
            Client.name.ilike(like) |
            Client.email.ilike(like) |
            Client.phone.ilike(like) |
            Client.contact.ilike(like)
        )

    query = query.order_by(Client.client_id.desc())

    clients = query.offset(skip).limit(limit).all()
    return clients

@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir un client par ID"""
    client = db.query(Client).filter(Client.client_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client non trouvé")
    return client

@router.get("/{client_id}/details")
async def get_client_details(
    client_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Détails étendus d'un client: infos, factures, dettes et totaux."""
    client = db.query(Client).filter(Client.client_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client non trouvé")

    # Factures du client
    invoices = (
        db.query(Invoice)
        .filter(Invoice.client_id == client_id)
        .order_by(Invoice.date.desc())
        .all()
    )

    # Créances manuelles du client
    client_debts = db.query(ClientDebt).filter(ClientDebt.client_id == client_id).order_by(ClientDebt.date.desc()).all()
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

    # Agrégats
    total_invoiced = float(sum([float(i.total or 0) for i in invoices]))
    total_paid = float(sum([float(i.paid_amount or 0) for i in invoices]))
    total_due = total_invoiced - total_paid
    # Total des créances manuelles (reste à payer)
    total_debts = float(sum([float(getattr(d, 'remaining_amount', 0) or 0) for d in debts]))

    return {
        "client": ClientResponse.from_orm(client),
        "stats": {
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "total_due": total_due,
            "total_debts": total_debts
        },
        "invoices": [
            {
                "invoice_id": inv.invoice_id,
                "invoice_number": inv.invoice_number,
                "date": inv.date,
                "status": inv.status,
                "total": float(inv.total or 0),
                "paid": float(inv.paid_amount or 0),
                "remaining": float(inv.remaining_amount or 0)
            }
            for inv in invoices
        ],
        "debts": debts
    }

@router.post("/", response_model=ClientResponse)
async def create_client(
    client_data: ClientCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Créer un nouveau client"""
    try:
        # Vérifier l'unicité du numéro de téléphone s'il est fourni
        if client_data.phone:
            incoming_phone = client_data.phone.strip()
            if incoming_phone:
                existing = (
                    db.query(Client)
                    .filter(func.lower(Client.phone) == incoming_phone.lower())
                    .first()
                )
                if existing:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Un client avec ce numéro de téléphone existe déjà",
                    )

        # Créer le client en utilisant les champs explicitement
        db_client = Client(
            name=client_data.name,
            contact=client_data.contact,
            email=client_data.email,
            phone=client_data.phone,
            address=client_data.address,
            city=client_data.city,
            postal_code=client_data.postal_code,
            country=client_data.country,
            tax_number=client_data.tax_number,
            notes=client_data.notes
        )
        db.add(db_client)
        db.commit()
        db.refresh(db_client)
        return db_client
    except HTTPException:
        # Re-lever les HTTPException (comme les erreurs de validation) sans les transformer
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la création du client: {e}")
        logging.error(f"Données reçues: {client_data.dict()}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/{client_id}", response_model=ClientResponse)
async def update_client(
    client_id: int,
    client_data: ClientUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_any_role(["user", "manager"]))
):
    """Mettre à jour un client"""
    try:
        client = db.query(Client).filter(Client.client_id == client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
        
        update_data = client_data.dict(exclude_unset=True)

        # Vérifier l'unicité du numéro si modifié
        new_phone = update_data.get("phone")
        if new_phone is not None:
            new_phone_stripped = new_phone.strip()
            if new_phone_stripped:
                conflict = (
                    db.query(Client)
                    .filter(
                        func.lower(Client.phone) == new_phone_stripped.lower(),
                        Client.client_id != client_id,
                    )
                    .first()
                )
                if conflict:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Un autre client possède déjà ce numéro de téléphone",
                    )
            else:
                # Autoriser la mise à jour vers une valeur vide/null si souhaité
                update_data["phone"] = None
        for field, value in update_data.items():
            setattr(client, field, value)
        
        db.commit()
        db.refresh(client)
        return client
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la mise à jour du client: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.delete("/{client_id}")
async def delete_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer un client"""
    try:
        client = db.query(Client).filter(Client.client_id == client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
        
        db.delete(client)
        db.commit()
        return {"message": "Client supprimé avec succès"}
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la suppression du client: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
