from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime

from ..database import DeliveryNote, DeliveryNoteItem, Client, get_next_id
from ..auth import get_current_user

router = APIRouter(prefix="/api/delivery-notes", tags=["delivery_notes"])


@router.get("/")
async def get_delivery_notes(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    client_id: Optional[int] = None,
    current_user=Depends(get_current_user),
):
    """Recuperer la liste des bons de livraison"""
    try:
        filters = {}

        if search:
            pattern = {"$regex": search, "$options": "i"}
            filters["$or"] = [
                {"delivery_note_number": pattern},
            ]

        if status:
            filters["status"] = status

        if client_id:
            filters["client_id"] = client_id

        total = await DeliveryNote.find(filters).count()
        notes = (
            await DeliveryNote.find(filters)
            .sort([("date", -1), ("delivery_note_id", -1)])
            .skip(skip)
            .limit(limit)
            .to_list()
        )

        # Enrich with items and client name
        result_notes = []
        for note in notes:
            items = await DeliveryNoteItem.find(
                DeliveryNoteItem.delivery_note_id == note.delivery_note_id
            ).to_list()

            client_name = ""
            if note.client_id:
                client = await Client.find_one(Client.client_id == note.client_id)
                client_name = client.name if client else ""

            note_dict = {
                "id": note.delivery_note_id,
                "number": note.delivery_note_number,
                "client_id": note.client_id,
                "client_name": client_name,
                "date": note.date.strftime("%Y-%m-%d") if note.date else None,
                "delivery_date": note.delivery_date.strftime("%Y-%m-%d") if note.delivery_date else None,
                "status": note.status,
                "items": [
                    {
                        "product_id": it.product_id,
                        "product_name": it.product_name,
                        "quantity": it.quantity,
                        "unit_price": float(it.price or 0),
                    }
                    for it in items
                ],
                "subtotal": float(note.subtotal or 0),
                "tax_rate": float(note.tax_rate or 18),
                "tax_amount": float(note.tax_amount or 0),
                "total": float(note.total or 0),
                "notes": note.notes,
                "created_at": note.created_at.isoformat() if note.created_at else None,
            }
            result_notes.append(note_dict)

        return {
            "delivery_notes": result_notes,
            "total": total,
            "page": (skip // limit) + 1,
            "pages": (total + limit - 1) // limit,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/summary")
async def get_delivery_notes_stats(
    current_user=Depends(get_current_user),
):
    """Recuperer les statistiques des bons de livraison"""
    try:
        total_notes = await DeliveryNote.count()
        pending_notes = await DeliveryNote.find(DeliveryNote.status == "en_preparation").count()
        delivered_notes = await DeliveryNote.find(DeliveryNote.status == "delivered").count()

        pipeline = [
            {"$group": {"_id": None, "total_value": {"$sum": "$total"}}},
        ]
        agg_result = await DeliveryNote.aggregate(pipeline).to_list()
        total_value = float(agg_result[0]["total_value"]) if agg_result else 0

        return {
            "total_notes": total_notes,
            "pending_notes": pending_notes,
            "delivered_notes": delivered_notes,
            "total_value": total_value,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
async def create_delivery_note(
    note_data: dict,
    current_user=Depends(get_current_user),
):
    """Creer un nouveau bon de livraison"""
    try:
        from decimal import Decimal

        new_id = await get_next_id("delivery_notes")
        new_number = f"BL-2024-{new_id:03d}"

        items_data = note_data.get("items", [])
        subtotal = sum(item["quantity"] * item["unit_price"] for item in items_data)
        tax_rate = note_data.get("tax_rate", 18)
        tax_amount = subtotal * tax_rate / 100
        total = subtotal + tax_amount

        new_note = DeliveryNote(
            delivery_note_id=new_id,
            delivery_note_number=new_number,
            client_id=note_data.get("client_id"),
            date=datetime.fromisoformat(note_data["date"]) if note_data.get("date") else datetime.now(),
            delivery_date=datetime.fromisoformat(note_data["delivery_date"]) if note_data.get("delivery_date") else None,
            status="en_preparation",
            subtotal=Decimal(str(subtotal)),
            tax_rate=Decimal(str(tax_rate)),
            tax_amount=Decimal(str(tax_amount)),
            total=Decimal(str(total)),
            notes=note_data.get("notes", ""),
        )
        await new_note.insert()

        # Create items
        for item in items_data:
            item_id = await get_next_id("delivery_note_items")
            dn_item = DeliveryNoteItem(
                item_id=item_id,
                delivery_note_id=new_id,
                product_id=item.get("product_id"),
                product_name=item.get("product_name", ""),
                quantity=item.get("quantity", 0),
                price=Decimal(str(item.get("unit_price", 0))),
            )
            await dn_item.insert()

        return {
            "id": new_id,
            "number": new_number,
            "client_id": note_data.get("client_id"),
            "client_name": note_data.get("client_name"),
            "date": note_data.get("date", datetime.now().strftime("%Y-%m-%d")),
            "delivery_date": note_data.get("delivery_date"),
            "status": "en_preparation",
            "items": items_data,
            "subtotal": subtotal,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "total": total,
            "notes": note_data.get("notes", ""),
            "created_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
