from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_
from typing import Optional, List
from datetime import datetime, date, timedelta
from pydantic import BaseModel
import logging

from ..database import get_db, Maintenance, Client, User, UserSettings
from ..auth import get_current_user

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/api/maintenances", tags=["maintenances"])

# ==================== SCHEMAS ====================

class MaintenanceCreate(BaseModel):
    client_id: Optional[int] = None
    client_name: str
    client_phone: Optional[str] = None
    client_email: Optional[str] = None
    
    device_type: str
    device_brand: Optional[str] = None
    device_model: Optional[str] = None
    device_serial: Optional[str] = None
    device_description: Optional[str] = None
    device_accessories: Optional[str] = None
    device_condition: Optional[str] = None
    
    problem_description: str
    diagnosis: Optional[str] = None
    
    reception_date: Optional[datetime] = None
    estimated_completion_date: Optional[date] = None
    pickup_deadline: Optional[date] = None
    
    status: str = "received"
    priority: str = "normal"
    
    estimated_cost: Optional[float] = None
    advance_paid: Optional[float] = 0
    
    warranty_days: int = 30
    
    technician_id: Optional[int] = None
    notes: Optional[str] = None
    internal_notes: Optional[str] = None


class MaintenanceUpdate(BaseModel):
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    client_email: Optional[str] = None
    
    device_type: Optional[str] = None
    device_brand: Optional[str] = None
    device_model: Optional[str] = None
    device_serial: Optional[str] = None
    device_description: Optional[str] = None
    device_accessories: Optional[str] = None
    device_condition: Optional[str] = None
    
    problem_description: Optional[str] = None
    diagnosis: Optional[str] = None
    work_done: Optional[str] = None
    
    estimated_completion_date: Optional[date] = None
    actual_completion_date: Optional[date] = None
    pickup_deadline: Optional[date] = None
    pickup_date: Optional[date] = None
    
    status: Optional[str] = None
    priority: Optional[str] = None
    
    estimated_cost: Optional[float] = None
    final_cost: Optional[float] = None
    advance_paid: Optional[float] = None
    
    warranty_days: Optional[int] = None
    liability_waived: Optional[bool] = None
    
    technician_id: Optional[int] = None
    notes: Optional[str] = None
    internal_notes: Optional[str] = None


# ==================== HELPERS ====================

def generate_maintenance_number(db: Session) -> str:
    """Générer un numéro de maintenance unique."""
    today = datetime.now()
    prefix = f"MAINT-{today.strftime('%y%m')}-"
    
    # Trouver le dernier numéro du mois
    last = db.query(Maintenance).filter(
        Maintenance.maintenance_number.like(f"{prefix}%")
    ).order_by(Maintenance.maintenance_id.desc()).first()
    
    if last:
        try:
            last_num = int(last.maintenance_number.split("-")[-1])
            new_num = last_num + 1
        except:
            new_num = 1
    else:
        new_num = 1
    
    return f"{prefix}{new_num:04d}"


def maintenance_to_dict(m: Maintenance) -> dict:
    """Convertir une maintenance en dictionnaire."""
    return {
        "maintenance_id": m.maintenance_id,
        "maintenance_number": m.maintenance_number,
        "client_id": m.client_id,
        "client_name": m.client_name,
        "client_phone": m.client_phone,
        "client_email": m.client_email,
        "device_type": m.device_type,
        "device_brand": m.device_brand,
        "device_model": m.device_model,
        "device_serial": m.device_serial,
        "device_description": m.device_description,
        "device_accessories": m.device_accessories,
        "device_condition": m.device_condition,
        "problem_description": m.problem_description,
        "diagnosis": m.diagnosis,
        "work_done": m.work_done,
        "reception_date": m.reception_date.isoformat() if m.reception_date else None,
        "estimated_completion_date": m.estimated_completion_date.isoformat() if m.estimated_completion_date else None,
        "actual_completion_date": m.actual_completion_date.isoformat() if m.actual_completion_date else None,
        "pickup_deadline": m.pickup_deadline.isoformat() if m.pickup_deadline else None,
        "pickup_date": m.pickup_date.isoformat() if m.pickup_date else None,
        "status": m.status,
        "priority": m.priority,
        "estimated_cost": float(m.estimated_cost) if m.estimated_cost else None,
        "final_cost": float(m.final_cost) if m.final_cost else None,
        "advance_paid": float(m.advance_paid) if m.advance_paid else 0,
        "warranty_days": m.warranty_days,
        "liability_waived": m.liability_waived,
        "liability_waived_date": m.liability_waived_date.isoformat() if m.liability_waived_date else None,
        "reminder_sent": m.reminder_sent,
        "reminder_sent_date": m.reminder_sent_date.isoformat() if m.reminder_sent_date else None,
        "technician_id": m.technician_id,
        "technician_name": m.technician.full_name if m.technician else None,
        "notes": m.notes,
        "internal_notes": m.internal_notes,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


# ==================== ENDPOINTS ====================

@router.get("")
async def list_maintenances(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    overdue: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les maintenances avec filtres et pagination."""
    try:
        query = db.query(Maintenance)
        
        # Filtres
        if search:
            search_term = f"%{search}%"
            query = query.filter(or_(
                Maintenance.maintenance_number.ilike(search_term),
                Maintenance.client_name.ilike(search_term),
                Maintenance.client_phone.ilike(search_term),
                Maintenance.device_type.ilike(search_term),
                Maintenance.device_brand.ilike(search_term),
                Maintenance.device_model.ilike(search_term),
                Maintenance.device_serial.ilike(search_term),
            ))
        
        if status:
            query = query.filter(Maintenance.status == status)
        
        if priority:
            query = query.filter(Maintenance.priority == priority)
        
        if overdue:
            today = date.today()
            query = query.filter(
                and_(
                    Maintenance.pickup_deadline < today,
                    Maintenance.status.in_(["completed", "ready"]),
                    Maintenance.pickup_date == None
                )
            )
        
        # Comptage total
        total = query.count()
        
        # Pagination
        maintenances = query.order_by(Maintenance.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
        
        return {
            "items": [maintenance_to_dict(m) for m in maintenances],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page
        }
    except Exception as e:
        logging.error(f"Erreur liste maintenances: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_maintenance_stats(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir les statistiques des maintenances."""
    try:
        today = date.today()
        
        total = db.query(Maintenance).count()
        received = db.query(Maintenance).filter(Maintenance.status == "received").count()
        in_progress = db.query(Maintenance).filter(Maintenance.status == "in_progress").count()
        completed = db.query(Maintenance).filter(Maintenance.status == "completed").count()
        ready = db.query(Maintenance).filter(Maintenance.status == "ready").count()
        picked_up = db.query(Maintenance).filter(Maintenance.status == "picked_up").count()
        abandoned = db.query(Maintenance).filter(Maintenance.status == "abandoned").count()
        
        # Maintenances en retard (deadline dépassée, non récupérées)
        overdue = db.query(Maintenance).filter(
            and_(
                Maintenance.pickup_deadline < today,
                Maintenance.status.in_(["completed", "ready"]),
                Maintenance.pickup_date == None
            )
        ).count()
        
        # Maintenances urgentes
        urgent = db.query(Maintenance).filter(
            Maintenance.priority == "urgent",
            Maintenance.status.notin_(["picked_up", "abandoned"])
        ).count()
        
        return {
            "total": total,
            "received": received,
            "in_progress": in_progress,
            "completed": completed,
            "ready": ready,
            "picked_up": picked_up,
            "abandoned": abandoned,
            "overdue": overdue,
            "urgent": urgent
        }
    except Exception as e:
        logging.error(f"Erreur stats maintenances: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/next-number")
async def get_next_maintenance_number(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir le prochain numéro de maintenance."""
    return {"maintenance_number": generate_maintenance_number(db)}


@router.get("/overdue")
async def get_overdue_maintenances(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir les maintenances en retard de récupération."""
    try:
        today = date.today()
        
        overdue = db.query(Maintenance).filter(
            and_(
                Maintenance.pickup_deadline < today,
                Maintenance.status.in_(["completed", "ready"]),
                Maintenance.pickup_date == None
            )
        ).order_by(Maintenance.pickup_deadline.asc()).all()
        
        return {"items": [maintenance_to_dict(m) for m in overdue]}
    except Exception as e:
        logging.error(f"Erreur maintenances en retard: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{maintenance_id}")
async def get_maintenance(
    maintenance_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir une maintenance par son ID."""
    maintenance = db.query(Maintenance).filter(Maintenance.maintenance_id == maintenance_id).first()
    if not maintenance:
        raise HTTPException(status_code=404, detail="Maintenance non trouvée")
    return maintenance_to_dict(maintenance)


@router.post("")
async def create_maintenance(
    data: MaintenanceCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Créer une nouvelle maintenance."""
    try:
        # Générer le numéro
        maintenance_number = generate_maintenance_number(db)
        
        # Calculer la date limite de récupération si non fournie (30 jours après réception)
        reception = data.reception_date or datetime.now()
        pickup_deadline = data.pickup_deadline
        if not pickup_deadline:
            pickup_deadline = (reception + timedelta(days=30)).date()
        
        maintenance = Maintenance(
            maintenance_number=maintenance_number,
            client_id=data.client_id,
            client_name=data.client_name,
            client_phone=data.client_phone,
            client_email=data.client_email,
            device_type=data.device_type,
            device_brand=data.device_brand,
            device_model=data.device_model,
            device_serial=data.device_serial,
            device_description=data.device_description,
            device_accessories=data.device_accessories,
            device_condition=data.device_condition,
            problem_description=data.problem_description,
            diagnosis=data.diagnosis,
            reception_date=reception,
            estimated_completion_date=data.estimated_completion_date,
            pickup_deadline=pickup_deadline,
            status=data.status,
            priority=data.priority,
            estimated_cost=data.estimated_cost,
            advance_paid=data.advance_paid or 0,
            warranty_days=data.warranty_days,
            technician_id=data.technician_id,
            notes=data.notes,
            internal_notes=data.internal_notes,
        )
        
        db.add(maintenance)
        db.commit()
        db.refresh(maintenance)
        
        return maintenance_to_dict(maintenance)
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur création maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{maintenance_id}")
async def update_maintenance(
    maintenance_id: int,
    data: MaintenanceUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Mettre à jour une maintenance."""
    try:
        maintenance = db.query(Maintenance).filter(Maintenance.maintenance_id == maintenance_id).first()
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvée")
        
        # Mettre à jour les champs fournis
        update_data = data.dict(exclude_unset=True)
        for key, value in update_data.items():
            if hasattr(maintenance, key):
                setattr(maintenance, key, value)
        
        db.commit()
        db.refresh(maintenance)
        
        return maintenance_to_dict(maintenance)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur mise à jour maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{maintenance_id}/complete")
async def complete_maintenance(
    maintenance_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Marquer une maintenance comme terminée."""
    try:
        maintenance = db.query(Maintenance).filter(Maintenance.maintenance_id == maintenance_id).first()
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvée")
        
        maintenance.status = "completed"
        maintenance.actual_completion_date = date.today()
        
        db.commit()
        db.refresh(maintenance)
        
        return maintenance_to_dict(maintenance)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur completion maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{maintenance_id}/ready")
async def mark_ready_for_pickup(
    maintenance_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Marquer une maintenance comme prête à récupérer."""
    try:
        maintenance = db.query(Maintenance).filter(Maintenance.maintenance_id == maintenance_id).first()
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvée")
        
        maintenance.status = "ready"
        if not maintenance.actual_completion_date:
            maintenance.actual_completion_date = date.today()
        
        db.commit()
        db.refresh(maintenance)
        
        return maintenance_to_dict(maintenance)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur ready maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{maintenance_id}/pickup")
async def mark_picked_up(
    maintenance_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Marquer une maintenance comme récupérée."""
    try:
        maintenance = db.query(Maintenance).filter(Maintenance.maintenance_id == maintenance_id).first()
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvée")
        
        maintenance.status = "picked_up"
        maintenance.pickup_date = date.today()
        
        db.commit()
        db.refresh(maintenance)
        
        return maintenance_to_dict(maintenance)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur pickup maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{maintenance_id}/waive-liability")
async def waive_liability(
    maintenance_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Dégager la responsabilité sur une maintenance en retard."""
    try:
        maintenance = db.query(Maintenance).filter(Maintenance.maintenance_id == maintenance_id).first()
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvée")
        
        maintenance.liability_waived = True
        maintenance.liability_waived_date = date.today()
        maintenance.status = "abandoned"
        
        db.commit()
        db.refresh(maintenance)
        
        return maintenance_to_dict(maintenance)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur waive liability: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{maintenance_id}")
async def delete_maintenance(
    maintenance_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer une maintenance."""
    try:
        maintenance = db.query(Maintenance).filter(Maintenance.maintenance_id == maintenance_id).first()
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvée")
        
        db.delete(maintenance)
        db.commit()
        
        return {"message": "Maintenance supprimée"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur suppression maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{maintenance_id}/send-reminder")
async def send_pickup_reminder(
    maintenance_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Envoyer un rappel de récupération au client."""
    try:
        maintenance = db.query(Maintenance).filter(Maintenance.maintenance_id == maintenance_id).first()
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvée")
        
        if not maintenance.client_phone:
            raise HTTPException(status_code=400, detail="Pas de numéro de téléphone pour ce client")
        
        # Marquer le rappel comme envoyé
        maintenance.reminder_sent = True
        maintenance.reminder_sent_date = datetime.now()
        
        db.commit()
        db.refresh(maintenance)
        
        # Retourner les infos pour l'envoi WhatsApp côté frontend
        return {
            "success": True,
            "maintenance": maintenance_to_dict(maintenance),
            "message": f"Bonjour {maintenance.client_name},\n\nVotre appareil ({maintenance.device_type} {maintenance.device_brand or ''} {maintenance.device_model or ''}) est prêt à être récupéré chez Geek Technologie.\n\nNuméro de fiche: {maintenance.maintenance_number}\nDate limite: {maintenance.pickup_deadline.strftime('%d/%m/%Y') if maintenance.pickup_deadline else 'Non définie'}\n\nMerci de venir le récupérer dans les plus brefs délais.\n\nCordialement,\nGeek Technologie"
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur envoi rappel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{maintenance_id}/print", response_class=HTMLResponse)
async def print_maintenance_sheet(
    request: Request,
    maintenance_id: int,
    kind: str = Query("technician"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Générer la fiche de maintenance imprimable."""
    try:
        maintenance = db.query(Maintenance).filter(Maintenance.maintenance_id == maintenance_id).first()
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvée")
        
        # Charger les paramètres de l'entreprise
        settings_dict = {}
        try:
            settings = db.query(UserSettings).first()
            if settings:
                settings_dict = {
                    "company_name": settings.company_name,
                    "address": settings.address,
                    "city": settings.city,
                    "phone": settings.phone,
                    "phone2": getattr(settings, 'phone2', None),
                    "email": settings.email,
                    "website": getattr(settings, 'website', None),
                    "logo": settings.logo_path,
                    "footer_text": getattr(settings, 'footer_text', None),
                }
        except Exception as e:
            logging.error(f"Erreur chargement UserSettings (impression maintenance): {e}")
            settings_dict = {}

        kind_norm = (kind or "").strip().lower()
        template_name = "print_maintenance.html"
        if kind_norm in {"technician", "tech", "fiche"}:
            template_name = "print_maintenance.html"
        elif kind_norm in {"client", "recap", "receipt", "recu"}:
            template_name = "print_maintenance_client.html"
        elif kind_norm in {"label", "sticker", "etiquette"}:
            template_name = "print_maintenance_label.html"
        elif kind_norm in {"ticket"}:
            template_name = "print_maintenance_ticket.html"

        return templates.TemplateResponse(
            template_name,
            {
                "request": request,
                "maintenance": maintenance,
                "settings": settings_dict,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur impression fiche maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))
