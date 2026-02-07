from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from datetime import date, datetime

from ..database import DailyClientRequest, Client, get_next_id
from ..schemas import (
    DailyClientRequestCreate,
    DailyClientRequestUpdate,
    DailyClientRequestResponse,
)
from ..auth import get_current_user

router = APIRouter(prefix="/api/daily-requests", tags=["daily-requests"])


@router.get("/", response_model=List[DailyClientRequestResponse])
async def get_daily_requests(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user=Depends(get_current_user),
):
    """Recuperer la liste des demandes quotidiennes des clients"""
    filters: dict = {}

    if search:
        pattern = {"$regex": search, "$options": "i"}
        filters["$or"] = [
            {"client_name": pattern},
            {"product_description": pattern},
            {"notes": pattern},
        ]

    if status:
        filters["status"] = status

    if start_date:
        filters.setdefault("request_date", {})["$gte"] = start_date
    if end_date:
        filters.setdefault("request_date", {})["$lte"] = end_date

    requests = (
        await DailyClientRequest.find(filters)
        .sort([("request_date", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list()
    )
    return requests


@router.get("/{request_id}", response_model=DailyClientRequestResponse)
async def get_daily_request(
    request_id: int,
    current_user=Depends(get_current_user),
):
    """Recuperer une demande specifique"""
    request = await DailyClientRequest.find_one(DailyClientRequest.request_id == request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Demande non trouvee")
    return request


@router.post("/", response_model=DailyClientRequestResponse)
async def create_daily_request(
    request_data: DailyClientRequestCreate,
    current_user=Depends(get_current_user),
):
    """Creer une nouvelle demande quotidienne"""
    # Verifier si le client existe si client_id est fourni
    if request_data.client_id:
        client = await Client.find_one(Client.client_id == request_data.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouve")

    new_id = await get_next_id("daily_client_requests")
    db_request = DailyClientRequest(
        request_id=new_id,
        client_id=request_data.client_id,
        client_name=request_data.client_name,
        client_phone=request_data.client_phone,
        product_description=request_data.product_description,
        request_date=request_data.request_date,
        status=request_data.status,
        notes=request_data.notes,
    )

    await db_request.insert()

    return db_request


@router.put("/{request_id}", response_model=DailyClientRequestResponse)
async def update_daily_request(
    request_id: int,
    request_data: DailyClientRequestUpdate,
    current_user=Depends(get_current_user),
):
    """Mettre a jour une demande quotidienne"""
    db_request = await DailyClientRequest.find_one(DailyClientRequest.request_id == request_id)
    if not db_request:
        raise HTTPException(status_code=404, detail="Demande non trouvee")

    # Verifier si le client existe si client_id est fourni
    if request_data.client_id:
        client = await Client.find_one(Client.client_id == request_data.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouve")

    update_data = request_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_request, field, value)

    db_request.updated_at = datetime.now()

    await db_request.save()

    return db_request


@router.delete("/{request_id}")
async def delete_daily_request(
    request_id: int,
    current_user=Depends(get_current_user),
):
    """Supprimer une demande quotidienne"""
    db_request = await DailyClientRequest.find_one(DailyClientRequest.request_id == request_id)
    if not db_request:
        raise HTTPException(status_code=404, detail="Demande non trouvee")

    await db_request.delete()

    return {"message": "Demande supprimee avec succes"}


@router.post("/{request_id}/fulfill")
async def fulfill_request(
    request_id: int,
    current_user=Depends(get_current_user),
):
    """Marquer une demande comme satisfaite"""
    db_request = await DailyClientRequest.find_one(DailyClientRequest.request_id == request_id)
    if not db_request:
        raise HTTPException(status_code=404, detail="Demande non trouvee")

    db_request.status = "fulfilled"
    db_request.updated_at = datetime.now()

    await db_request.save()

    return {"message": "Demande marquee comme satisfaite"}


@router.post("/{request_id}/cancel")
async def cancel_request(
    request_id: int,
    current_user=Depends(get_current_user),
):
    """Annuler une demande"""
    db_request = await DailyClientRequest.find_one(DailyClientRequest.request_id == request_id)
    if not db_request:
        raise HTTPException(status_code=404, detail="Demande non trouvee")

    db_request.status = "cancelled"
    db_request.updated_at = datetime.now()

    await db_request.save()

    return {"message": "Demande annulee"}


@router.get("/stats/summary")
async def get_requests_summary(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user=Depends(get_current_user),
):
    """Obtenir un resume des demandes"""
    filters: dict = {}

    if start_date:
        filters.setdefault("request_date", {})["$gte"] = start_date
    if end_date:
        filters.setdefault("request_date", {})["$lte"] = end_date

    total_requests = await DailyClientRequest.find(filters).count()

    pending_filters = {**filters, "status": "pending"}
    pending_requests = await DailyClientRequest.find(pending_filters).count()

    fulfilled_filters = {**filters, "status": "fulfilled"}
    fulfilled_requests = await DailyClientRequest.find(fulfilled_filters).count()

    cancelled_filters = {**filters, "status": "cancelled"}
    cancelled_requests = await DailyClientRequest.find(cancelled_filters).count()

    return {
        "total_requests": total_requests,
        "pending_requests": pending_requests,
        "fulfilled_requests": fulfilled_requests,
        "cancelled_requests": cancelled_requests,
        "fulfillment_rate": round(
            (fulfilled_requests / total_requests * 100) if total_requests > 0 else 0, 2
        ),
    }
