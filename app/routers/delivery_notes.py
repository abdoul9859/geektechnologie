from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import json

from ..database import get_db, User
from ..auth import get_current_user

router = APIRouter(prefix="/api/delivery-notes", tags=["delivery_notes"])

# Données simulées pour les bons de livraison
delivery_notes_data = [
    {
        "id": 1,
        "number": "BL-2024-001",
        "client_id": 1,
        "client_name": "Amadou Ba",
        "date": "2024-01-15",
        "delivery_date": "2024-01-16",
        "status": "delivered",
        "items": [
            {"product_id": 1, "product_name": "iPhone 15", "quantity": 2, "unit_price": 750000},
            {"product_id": 2, "product_name": "Samsung Galaxy S24", "quantity": 1, "unit_price": 650000}
        ],
        "subtotal": 2150000,
        "tax_rate": 18,
        "tax_amount": 387000,
        "total": 2537000,
        "notes": "Livraison à domicile",
        "created_at": "2024-01-15T10:00:00"
    },
    {
        "id": 2,
        "number": "BL-2024-002",
        "client_id": 2,
        "client_name": "Fatou Diop",
        "date": "2024-01-18",
        "delivery_date": "2024-01-19",
        "status": "pending",
        "items": [
            {"product_id": 3, "product_name": "MacBook Air", "quantity": 1, "unit_price": 1200000}
        ],
        "subtotal": 1200000,
        "tax_rate": 18,
        "tax_amount": 216000,
        "total": 1416000,
        "notes": "Livraison en magasin",
        "created_at": "2024-01-18T14:30:00"
    }
]

@router.get("/")
async def get_delivery_notes(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    client_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer la liste des bons de livraison"""
    try:
        filtered_notes = delivery_notes_data.copy()
        
        # Filtrer par recherche
        if search:
            search_lower = search.lower()
            filtered_notes = [
                n for n in filtered_notes 
                if search_lower in n["number"].lower() or 
                   search_lower in n["client_name"].lower()
            ]
        
        # Filtrer par statut
        if status:
            filtered_notes = [n for n in filtered_notes if n["status"] == status]
            
        # Filtrer par client
        if client_id:
            filtered_notes = [n for n in filtered_notes if n["client_id"] == client_id]
        
        # Pagination
        total = len(filtered_notes)
        notes = filtered_notes[skip:skip + limit]
        
        return {
            "delivery_notes": notes,
            "total": total,
            "page": (skip // limit) + 1,
            "pages": (total + limit - 1) // limit
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats/summary")
async def get_delivery_notes_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer les statistiques des bons de livraison"""
    try:
        total_notes = len(delivery_notes_data)
        pending_notes = len([n for n in delivery_notes_data if n["status"] == "pending"])
        delivered_notes = len([n for n in delivery_notes_data if n["status"] == "delivered"])
        total_value = sum(n["total"] for n in delivery_notes_data)
        
        return {
            "total_notes": total_notes,
            "pending_notes": pending_notes,
            "delivered_notes": delivered_notes,
            "total_value": total_value
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/")
async def create_delivery_note(
    note_data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Créer un nouveau bon de livraison"""
    try:
        new_id = max([n["id"] for n in delivery_notes_data], default=0) + 1
        new_number = f"BL-2024-{new_id:03d}"
        
        items = note_data.get("items", [])
        subtotal = sum(item["quantity"] * item["unit_price"] for item in items)
        tax_rate = note_data.get("tax_rate", 18)
        tax_amount = subtotal * tax_rate / 100
        total = subtotal + tax_amount
        
        new_note = {
            "id": new_id,
            "number": new_number,
            "client_id": note_data.get("client_id"),
            "client_name": note_data.get("client_name"),
            "date": note_data.get("date", datetime.now().strftime("%Y-%m-%d")),
            "delivery_date": note_data.get("delivery_date"),
            "status": "pending",
            "items": items,
            "subtotal": subtotal,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "total": total,
            "notes": note_data.get("notes", ""),
            "created_at": datetime.now().isoformat()
        }
        
        delivery_notes_data.append(new_note)
        return new_note
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
