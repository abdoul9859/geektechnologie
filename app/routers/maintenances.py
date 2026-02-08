from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Optional, List
from datetime import datetime, date, timedelta
from pydantic import BaseModel
import logging
import os
import httpx

from ..database import Maintenance, Client, User, UserSettings, get_next_id
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


class SendMaintenanceWhatsAppRequest(BaseModel):
    maintenance_id: int
    phone: str
    kind: Optional[str] = "technician"


class SendMaintenanceEmailRequest(BaseModel):
    maintenance_id: int
    email: str
    kind: Optional[str] = "technician"


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

async def generate_maintenance_number() -> str:
    """Generer un numero de maintenance unique."""
    today = datetime.now()
    prefix = f"MAINT-{today.strftime('%y%m')}-"

    # Trouver le dernier numero du mois
    last = await Maintenance.find(
        {"maintenance_number": {"$regex": f"^{prefix}"}}
    ).sort(-Maintenance.maintenance_id).first_or_none()

    if last:
        try:
            last_num = int(last.maintenance_number.split("-")[-1])
            new_num = last_num + 1
        except Exception:
            new_num = 1
    else:
        new_num = 1

    return f"{prefix}{new_num:04d}"


async def maintenance_to_dict(m: Maintenance) -> dict:
    """Convertir une maintenance en dictionnaire."""
    # Look up technician name if technician_id is set
    technician_name = None
    if m.technician_id:
        tech = await User.find_one(User.user_id == m.technician_id)
        if tech:
            technician_name = tech.full_name

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
        "technician_name": technician_name,
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
    current_user = Depends(get_current_user)
):
    """Lister les maintenances avec filtres et pagination."""
    try:
        filters = []

        # Filtres
        if search:
            regex_filter = {"$regex": search, "$options": "i"}
            filters.append({"$or": [
                {"maintenance_number": regex_filter},
                {"client_name": regex_filter},
                {"client_phone": regex_filter},
                {"device_type": regex_filter},
                {"device_brand": regex_filter},
                {"device_model": regex_filter},
                {"device_serial": regex_filter},
            ]})

        if status:
            filters.append({"status": status})

        if priority:
            filters.append({"priority": priority})

        if overdue:
            today = date.today()
            filters.append({
                "pickup_deadline": {"$lt": datetime.combine(today, datetime.min.time())},
                "status": {"$in": ["completed", "ready"]},
                "pickup_date": None,
            })

        query_filter = {"$and": filters} if filters else {}

        # Comptage total
        total = await Maintenance.find(query_filter).count()

        # Pagination
        maintenances = await (
            Maintenance.find(query_filter)
            .sort(-Maintenance.created_at)
            .skip((page - 1) * per_page)
            .limit(per_page)
            .to_list()
        )

        return {
            "items": [await maintenance_to_dict(m) for m in maintenances],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page
        }
    except Exception as e:
        logging.error(f"Erreur liste maintenances: {e}")
        raise HTTPException(status_code=500, detail=str(e))


N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://n8n:5678")


def _normalize_maintenance_kind(kind: Optional[str]) -> str:
    k = (kind or "technician").strip().lower()
    allowed = {"technician", "tech", "fiche", "client", "recap", "receipt", "recu", "label", "sticker", "etiquette", "ticket"}
    if k not in allowed:
        return "technician"
    # Canonical values used by the /print endpoint
    if k in {"client", "recap", "receipt", "recu"}:
        return "client"
    if k in {"label", "sticker", "etiquette"}:
        return "label"
    if k in {"ticket"}:
        return "ticket"
    return "technician"


@router.post("/send-whatsapp")
async def send_maintenance_whatsapp(
    data: SendMaintenanceWhatsAppRequest,
    current_user = Depends(get_current_user)
):
    """Envoyer un rapport/fiche de maintenance par WhatsApp via Evolution API."""
    from ..services.evolution_api import send_maintenance_whatsapp as _send_maintenance_wa
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == data.maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        kind = _normalize_maintenance_kind(data.kind)
        app_public_url = os.getenv("APP_PUBLIC_URL", "http://nitek_app:8000")
        pdf_url = f"{app_public_url}/api/maintenances/{data.maintenance_id}/print?kind={kind}"

        device = f"{maintenance.device_type} {maintenance.device_brand or ''} {maintenance.device_model or ''}".strip()

        await _send_maintenance_wa(
            phone=data.phone,
            maintenance_number=maintenance.maintenance_number,
            client_name=maintenance.client_name,
            device=device,
            pdf_url=pdf_url,
            kind=kind,
        )
        return {"success": True, "message": "Document de maintenance envoye par WhatsApp"}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        logging.error(f"Erreur Evolution API: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=502, detail="Erreur Evolution API")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur envoi maintenance WhatsApp: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send-email")
async def send_maintenance_email(
    data: SendMaintenanceEmailRequest,
    current_user = Depends(get_current_user)
):
    """Envoyer un rapport/fiche de maintenance par Email via n8n."""
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == data.maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        kind = _normalize_maintenance_kind(data.kind)
        app_public_url = os.getenv("APP_PUBLIC_URL", "http://nitek_app:8000")
        pdf_url = f"{app_public_url}/api/maintenances/{data.maintenance_id}/print?kind={kind}"

        webhook_url = f"{N8N_BASE_URL}/webhook/send-maintenance-email"
        payload = {
            "maintenance_id": data.maintenance_id,
            "maintenance_number": maintenance.maintenance_number,
            "email": data.email,
            "pdf_url": pdf_url,
            "client_name": maintenance.client_name,
            "device": f"{maintenance.device_type} {maintenance.device_brand or ''} {maintenance.device_model or ''}".strip(),
            "kind": kind,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(webhook_url, json=payload)

        if response.status_code == 200:
            return {"success": True, "message": "Document de maintenance envoye par email"}
        logging.error(f"Erreur n8n Email maintenance: {response.status_code} - {response.text}")
        return {"success": False, "message": f"Erreur n8n: {response.text}"}

    except httpx.RequestError as e:
        logging.error(f"Erreur connexion n8n (maintenance Email): {e}")
        raise HTTPException(status_code=503, detail="Service n8n indisponible")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur envoi maintenance Email: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_maintenance_stats(
    current_user = Depends(get_current_user)
):
    """Obtenir les statistiques des maintenances."""
    try:
        today = date.today()
        today_dt = datetime.combine(today, datetime.min.time())

        total = await Maintenance.find().count()
        received = await Maintenance.find(Maintenance.status == "received").count()
        in_progress = await Maintenance.find(Maintenance.status == "in_progress").count()
        completed = await Maintenance.find(Maintenance.status == "completed").count()
        ready = await Maintenance.find(Maintenance.status == "ready").count()
        picked_up = await Maintenance.find(Maintenance.status == "picked_up").count()
        abandoned = await Maintenance.find(Maintenance.status == "abandoned").count()

        # Maintenances en retard (deadline depassee, non recuperees)
        overdue = await Maintenance.find({
            "pickup_deadline": {"$lt": today_dt},
            "status": {"$in": ["completed", "ready"]},
            "pickup_date": None,
        }).count()

        # Maintenances urgentes
        urgent = await Maintenance.find({
            "priority": "urgent",
            "status": {"$nin": ["picked_up", "abandoned"]},
        }).count()

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
    current_user = Depends(get_current_user)
):
    """Obtenir le prochain numero de maintenance."""
    return {"maintenance_number": await generate_maintenance_number()}


@router.get("/overdue")
async def get_overdue_maintenances(
    current_user = Depends(get_current_user)
):
    """Obtenir les maintenances en retard de recuperation."""
    try:
        today = date.today()
        today_dt = datetime.combine(today, datetime.min.time())

        overdue = await (
            Maintenance.find({
                "pickup_deadline": {"$lt": today_dt},
                "status": {"$in": ["completed", "ready"]},
                "pickup_date": None,
            })
            .sort(+Maintenance.pickup_deadline)
            .to_list()
        )

        return {"items": [await maintenance_to_dict(m) for m in overdue]}
    except Exception as e:
        logging.error(f"Erreur maintenances en retard: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{maintenance_id}")
async def get_maintenance(
    maintenance_id: int,
    current_user = Depends(get_current_user)
):
    """Obtenir une maintenance par son ID."""
    maintenance = await Maintenance.find_one(Maintenance.maintenance_id == maintenance_id)
    if not maintenance:
        raise HTTPException(status_code=404, detail="Maintenance non trouvee")
    return await maintenance_to_dict(maintenance)


@router.post("")
async def create_maintenance(
    data: MaintenanceCreate,
    current_user = Depends(get_current_user)
):
    """Creer une nouvelle maintenance."""
    try:
        # Generer le numero
        maintenance_number = await generate_maintenance_number()

        # Obtenir le prochain ID auto-increment
        new_id = await get_next_id("maintenances")

        # Calculer la date limite de recuperation si non fournie (30 jours apres reception)
        reception = data.reception_date or datetime.now()
        pickup_deadline = data.pickup_deadline
        if not pickup_deadline:
            pickup_deadline = (reception + timedelta(days=30)).date()

        maintenance = Maintenance(
            maintenance_id=new_id,
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

        await maintenance.insert()

        return await maintenance_to_dict(maintenance)
    except Exception as e:
        logging.error(f"Erreur creation maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{maintenance_id}")
async def update_maintenance(
    maintenance_id: int,
    data: MaintenanceUpdate,
    current_user = Depends(get_current_user)
):
    """Mettre a jour une maintenance."""
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        # Mettre a jour les champs fournis
        update_data = data.dict(exclude_unset=True)
        for key, value in update_data.items():
            if hasattr(maintenance, key):
                setattr(maintenance, key, value)

        maintenance.updated_at = datetime.utcnow()
        await maintenance.save()

        return await maintenance_to_dict(maintenance)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur mise a jour maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{maintenance_id}/complete")
async def complete_maintenance(
    maintenance_id: int,
    current_user = Depends(get_current_user)
):
    """Marquer une maintenance comme terminee."""
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        maintenance.status = "completed"
        maintenance.actual_completion_date = date.today()
        maintenance.updated_at = datetime.utcnow()

        await maintenance.save()

        return await maintenance_to_dict(maintenance)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur completion maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{maintenance_id}/ready")
async def mark_ready_for_pickup(
    maintenance_id: int,
    current_user = Depends(get_current_user)
):
    """Marquer une maintenance comme prete a recuperer."""
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        maintenance.status = "ready"
        if not maintenance.actual_completion_date:
            maintenance.actual_completion_date = date.today()
        maintenance.updated_at = datetime.utcnow()

        await maintenance.save()

        return await maintenance_to_dict(maintenance)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur ready maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{maintenance_id}/pickup")
async def mark_picked_up(
    maintenance_id: int,
    current_user = Depends(get_current_user)
):
    """Marquer une maintenance comme recuperee."""
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        maintenance.status = "picked_up"
        maintenance.pickup_date = date.today()
        maintenance.updated_at = datetime.utcnow()

        await maintenance.save()

        return await maintenance_to_dict(maintenance)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur pickup maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{maintenance_id}/waive-liability")
async def waive_liability(
    maintenance_id: int,
    current_user = Depends(get_current_user)
):
    """Degager la responsabilite sur une maintenance en retard."""
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        maintenance.liability_waived = True
        maintenance.liability_waived_date = date.today()
        maintenance.status = "abandoned"
        maintenance.updated_at = datetime.utcnow()

        await maintenance.save()

        return await maintenance_to_dict(maintenance)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur waive liability: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{maintenance_id}")
async def delete_maintenance(
    maintenance_id: int,
    current_user = Depends(get_current_user)
):
    """Supprimer une maintenance."""
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        await maintenance.delete()

        return {"message": "Maintenance supprimee"}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur suppression maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{maintenance_id}/send-reminder")
async def send_pickup_reminder(
    maintenance_id: int,
    current_user = Depends(get_current_user)
):
    """Envoyer un rappel de recuperation au client."""
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        if not maintenance.client_phone:
            raise HTTPException(status_code=400, detail="Pas de numero de telephone pour ce client")

        # Marquer le rappel comme envoye
        maintenance.reminder_sent = True
        maintenance.reminder_sent_date = datetime.now()
        maintenance.updated_at = datetime.utcnow()

        await maintenance.save()

        # Retourner les infos pour l'envoi WhatsApp cote frontend
        return {
            "success": True,
            "maintenance": await maintenance_to_dict(maintenance),
            "message": f"Bonjour {maintenance.client_name},\n\nVotre appareil ({maintenance.device_type} {maintenance.device_brand or ''} {maintenance.device_model or ''}) est pret a etre recupere chez Geek Technologie.\n\nNumero de fiche: {maintenance.maintenance_number}\nDate limite: {maintenance.pickup_deadline.strftime('%d/%m/%Y') if maintenance.pickup_deadline else 'Non definie'}\n\nMerci de venir le recuperer dans les plus brefs delais.\n\nCordialement,\nGeek Technologie"
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur envoi rappel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SendReminderWhatsAppRequest(BaseModel):
    phone: str


@router.post("/{maintenance_id}/send-reminder-whatsapp")
async def send_reminder_whatsapp(
    maintenance_id: int,
    data: SendReminderWhatsAppRequest,
    current_user = Depends(get_current_user)
):
    """Envoyer un rappel de recuperation via Evolution API."""
    from ..services.evolution_api import send_reminder_text as _send_reminder
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        device = f"{maintenance.device_type} {maintenance.device_brand or ''} {maintenance.device_model or ''}".strip()
        pickup_date = maintenance.pickup_deadline.strftime('%d/%m/%Y') if maintenance.pickup_deadline else 'Non definie'

        message = (
            f"Bonjour {maintenance.client_name},\n\n"
            f"Votre appareil ({device}) est pret a etre recupere chez Geek Technologie.\n\n"
            f"Numero de fiche: {maintenance.maintenance_number}\n"
            f"Date limite: {pickup_date}\n\n"
            f"Merci de venir le recuperer dans les plus brefs delais.\n\n"
            f"Cordialement,\nGeek Technologie"
        )

        await _send_reminder(phone=data.phone, message=message)

        maintenance.reminder_sent = True
        maintenance.reminder_sent_date = datetime.now()
        maintenance.updated_at = datetime.utcnow()
        await maintenance.save()

        return {"success": True, "message": "Rappel de recuperation envoye par WhatsApp"}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        logging.error(f"Erreur Evolution API: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=502, detail="Erreur Evolution API")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur envoi rappel WhatsApp: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{maintenance_id}/print", response_class=HTMLResponse)
async def print_maintenance_sheet(
    request: Request,
    maintenance_id: int,
    kind: str = Query("technician"),
):
    """Generer la fiche de maintenance imprimable."""
    try:
        maintenance = await Maintenance.find_one(Maintenance.maintenance_id == maintenance_id)
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance non trouvee")

        # Charger les parametres de l'entreprise (key-value UserSettings)
        settings_dict = {}
        try:
            all_settings = await UserSettings.find().to_list()
            if all_settings:
                kv = {s.setting_key: s.setting_value for s in all_settings}
                settings_dict = {
                    "company_name": kv.get("company_name"),
                    "address": kv.get("address"),
                    "city": kv.get("city"),
                    "phone": kv.get("phone"),
                    "phone2": kv.get("phone2"),
                    "email": kv.get("email"),
                    "website": kv.get("website"),
                    "logo": kv.get("logo_path"),
                    "footer_text": kv.get("footer_text"),
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
