from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc
from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal

from app.database import get_db, DailyClientRequest, Client
from app.schemas import (
    DailyClientRequestCreate, 
    DailyClientRequestUpdate, 
    DailyClientRequestResponse,
    ClientCreate
)
from app.auth import get_current_user

router = APIRouter(prefix="/api/daily-requests", tags=["daily-requests"])

@router.get("/", response_model=List[DailyClientRequestResponse])
async def get_daily_requests(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer la liste des demandes quotidiennes des clients"""
    query = db.query(DailyClientRequest)
    
    # Filtres
    if search:
        query = query.filter(
            or_(
                DailyClientRequest.client_name.ilike(f"%{search}%"),
                DailyClientRequest.product_description.ilike(f"%{search}%"),
                DailyClientRequest.notes.ilike(f"%{search}%")
            )
        )
    
    if status:
        query = query.filter(DailyClientRequest.status == status)
    
    if start_date:
        query = query.filter(DailyClientRequest.request_date >= start_date)
    
    if end_date:
        query = query.filter(DailyClientRequest.request_date <= end_date)
    
    # Tri par date de demande décroissante
    query = query.order_by(desc(DailyClientRequest.request_date), desc(DailyClientRequest.created_at))
    
    # Pagination
    requests = query.offset(skip).limit(limit).all()
    return requests

@router.get("/{request_id}", response_model=DailyClientRequestResponse)
async def get_daily_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer une demande spécifique"""
    request = db.query(DailyClientRequest).filter(DailyClientRequest.request_id == request_id).first()
    if not request:
        raise HTTPException(status_code=404, detail="Demande non trouvée")
    return request

@router.post("/", response_model=DailyClientRequestResponse)
async def create_daily_request(
    request_data: DailyClientRequestCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Créer une nouvelle demande quotidienne"""
    # Vérifier si le client existe si client_id est fourni
    if request_data.client_id:
        client = db.query(Client).filter(Client.client_id == request_data.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
    
    # Créer la demande
    db_request = DailyClientRequest(
        client_id=request_data.client_id,
        client_name=request_data.client_name,
        client_phone=request_data.client_phone,
        product_description=request_data.product_description,
        request_date=request_data.request_date,
        status=request_data.status,
        notes=request_data.notes
    )
    
    db.add(db_request)
    db.commit()
    db.refresh(db_request)
    
    return db_request

@router.put("/{request_id}", response_model=DailyClientRequestResponse)
async def update_daily_request(
    request_id: int,
    request_data: DailyClientRequestUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Mettre à jour une demande quotidienne"""
    db_request = db.query(DailyClientRequest).filter(DailyClientRequest.request_id == request_id).first()
    if not db_request:
        raise HTTPException(status_code=404, detail="Demande non trouvée")
    
    # Vérifier si le client existe si client_id est fourni
    if request_data.client_id:
        client = db.query(Client).filter(Client.client_id == request_data.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
    
    # Mettre à jour les champs
    update_data = request_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_request, field, value)
    
    db_request.updated_at = datetime.now()
    
    db.commit()
    db.refresh(db_request)
    
    return db_request

@router.delete("/{request_id}")
async def delete_daily_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer une demande quotidienne"""
    db_request = db.query(DailyClientRequest).filter(DailyClientRequest.request_id == request_id).first()
    if not db_request:
        raise HTTPException(status_code=404, detail="Demande non trouvée")
    
    db.delete(db_request)
    db.commit()
    
    return {"message": "Demande supprimée avec succès"}

@router.post("/{request_id}/fulfill")
async def fulfill_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Marquer une demande comme satisfaite"""
    db_request = db.query(DailyClientRequest).filter(DailyClientRequest.request_id == request_id).first()
    if not db_request:
        raise HTTPException(status_code=404, detail="Demande non trouvée")
    
    db_request.status = "fulfilled"
    db_request.updated_at = datetime.now()
    
    db.commit()
    db.refresh(db_request)
    
    return {"message": "Demande marquée comme satisfaite"}

@router.post("/{request_id}/cancel")
async def cancel_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Annuler une demande"""
    db_request = db.query(DailyClientRequest).filter(DailyClientRequest.request_id == request_id).first()
    if not db_request:
        raise HTTPException(status_code=404, detail="Demande non trouvée")
    
    db_request.status = "cancelled"
    db_request.updated_at = datetime.now()
    
    db.commit()
    db.refresh(db_request)
    
    return {"message": "Demande annulée"}

@router.get("/stats/summary")
async def get_requests_summary(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir un résumé des demandes"""
    query = db.query(DailyClientRequest)
    
    if start_date:
        query = query.filter(DailyClientRequest.request_date >= start_date)
    
    if end_date:
        query = query.filter(DailyClientRequest.request_date <= end_date)
    
    total_requests = query.count()
    pending_requests = query.filter(DailyClientRequest.status == "pending").count()
    fulfilled_requests = query.filter(DailyClientRequest.status == "fulfilled").count()
    cancelled_requests = query.filter(DailyClientRequest.status == "cancelled").count()
    
    return {
        "total_requests": total_requests,
        "pending_requests": pending_requests,
        "fulfilled_requests": fulfilled_requests,
        "cancelled_requests": cancelled_requests,
        "fulfillment_rate": round((fulfilled_requests / total_requests * 100) if total_requests > 0 else 0, 2)
    }
