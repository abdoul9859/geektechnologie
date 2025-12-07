from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from datetime import date, datetime, timedelta
from sqlalchemy import func

from ..database import get_db, Invoice, Client, ClientDebt, AppCache
from ..services.debt_notifier import debt_notifier
from ..auth import get_current_user

router = APIRouter(prefix="/api/debug/debt-notifier", tags=["debug"])

@router.get("/status")
async def get_debt_notifier_status(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Affiche le statut du DebtNotifier et sa configuration"""
    import os
    
    config = {
        "ENABLE_DEBT_REMINDERS": os.getenv("ENABLE_DEBT_REMINDERS"),
        "DEBT_REMINDER_CHANNEL": os.getenv("DEBT_REMINDER_CHANNEL"),
        "DEBT_REMINDER_DRY_RUN": os.getenv("DEBT_REMINDER_DRY_RUN"),
        "DEBT_REMINDER_INTERVAL_SECONDS": os.getenv("DEBT_REMINDER_INTERVAL_SECONDS"),
        "DEBT_REMINDER_PERIOD_DAYS": os.getenv("DEBT_REMINDER_PERIOD_DAYS"),
        "N8N_WEBHOOK_URL": os.getenv("N8N_WEBHOOK_URL"),
        "DEFAULT_COUNTRY_CODE": os.getenv("DEFAULT_COUNTRY_CODE"),
        "APP_NAME": os.getenv("APP_NAME"),
    }
    
    # Vérifier si le thread du notifier tourne
    thread_status = "running" if debt_notifier._thread and debt_notifier._thread.is_alive() else "stopped"
    
    return {
        "config": config,
        "thread_status": thread_status,
        "notifier_interval": debt_notifier._interval_seconds,
        "notifier_period_days": debt_notifier._period_days,
        "notifier_dry_run": debt_notifier._dry_run,
        "default_country_code": debt_notifier._default_cc
    }

@router.get("/overdue-clients")
async def get_overdue_clients(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Liste tous les clients avec des dettes en retard selon la logique du DebtNotifier"""
    today = date.today()
    client_overdue = {}

    # Factures en retard (logique exacte du _tick())
    inv_rows = (
        db.query(Invoice, Client)
        .join(Client, Client.client_id == Invoice.client_id, isouter=True)
        .filter((func.coalesce(Invoice.remaining_amount, Invoice.total - func.coalesce(Invoice.paid_amount, 0)) > 0))
        .filter(Invoice.due_date.isnot(None))
        .all()
    )
    
    for inv, cl in inv_rows:
        dd = getattr(inv.due_date, 'date', lambda: inv.due_date)()
        amount = float(inv.total or 0)
        paid = float(inv.paid_amount or 0)
        remaining = float(inv.remaining_amount if inv.remaining_amount is not None else max(0.0, amount - paid))
        if dd and remaining > 0 and dd < today:
            cid = int(inv.client_id) if inv.client_id is not None else None
            if cid is None:
                continue
            client_overdue.setdefault(cid, {"client": cl, "invoices": [], "manual": []})
            client_overdue[cid]["invoices"].append({
                "invoice_number": inv.invoice_number,
                "due_date": dd.isoformat(),
                "remaining": remaining,
            })

    # Créances manuelles en retard
    cd_rows = db.query(ClientDebt, Client).join(Client, Client.client_id == ClientDebt.client_id, isouter=True).all()
    for d, cl in cd_rows:
        dd = getattr(d.due_date, 'date', lambda: d.due_date)()
        amount = float(d.amount or 0)
        paid = float(d.paid_amount or 0)
        remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)
        if dd and remaining > 0 and dd < today and d.client_id is not None:
            cid = int(d.client_id)
            client_overdue.setdefault(cid, {"client": cl, "invoices": [], "manual": []})
            client_overdue[cid]["manual"].append({
                "reference": d.reference,
                "due_date": dd.isoformat(),
                "remaining": remaining,
            })

    # Formater la réponse
    result = []
    for cid, data in client_overdue.items():
        cl = data["client"]
        
        # Vérifier si le client devrait être notifié (période de rappel)
        should_notify = debt_notifier._should_notify(db, cid)
        
        result.append({
            "client_id": cid,
            "client_name": cl.name if cl else "Unknown",
            "client_phone": cl.phone if cl else None,
            "client_email": cl.email if cl else None,
            "should_notify": should_notify,
            "overdue_invoices": data["invoices"],
            "overdue_manual_debts": data["manual"],
            "total_overdue": sum(x["remaining"] for x in data["invoices"]) + sum(x["remaining"] for x in data["manual"])
        })
    
    return {
        "today": today.isoformat(),
        "total_overdue_clients": len(result),
        "clients": result
    }

@router.post("/send-notification/{client_id}")
async def send_notification_to_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Force l'envoi d'une notification pour un client spécifique"""
    today = date.today()
    client_overdue = {}

    # Récupérer les dettes en retard pour ce client spécifique
    inv_rows = (
        db.query(Invoice, Client)
        .join(Client, Client.client_id == Invoice.client_id, isouter=True)
        .filter(Invoice.client_id == client_id)
        .filter((func.coalesce(Invoice.remaining_amount, Invoice.total - func.coalesce(Invoice.paid_amount, 0)) > 0))
        .filter(Invoice.due_date.isnot(None))
        .all()
    )
    
    for inv, cl in inv_rows:
        dd = getattr(inv.due_date, 'date', lambda: inv.due_date)()
        amount = float(inv.total or 0)
        paid = float(inv.paid_amount or 0)
        remaining = float(inv.remaining_amount if inv.remaining_amount is not None else max(0.0, amount - paid))
        if dd and remaining > 0 and dd < today:
            client_overdue.setdefault(client_id, {"client": cl, "invoices": [], "manual": []})
            client_overdue[client_id]["invoices"].append({
                "invoice_number": inv.invoice_number,
                "due_date": dd,
                "remaining": remaining,
            })

    # Créances manuelles en retard pour ce client
    cd_rows = db.query(ClientDebt, Client).join(Client, Client.client_id == ClientDebt.client_id, isouter=True).filter(ClientDebt.client_id == client_id).all()
    for d, cl in cd_rows:
        dd = getattr(d.due_date, 'date', lambda: d.due_date)()
        amount = float(d.amount or 0)
        paid = float(d.paid_amount or 0)
        remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)
        if dd and remaining > 0 and dd < today:
            client_overdue.setdefault(client_id, {"client": cl, "invoices": [], "manual": []})
            client_overdue[client_id]["manual"].append({
                "reference": d.reference,
                "due_date": dd,
                "remaining": remaining,
            })

    if not client_overdue.get(client_id):
        raise HTTPException(status_code=404, detail=f"No overdue debts found for client_id={client_id}")

    data = client_overdue[client_id]
    cl = data["client"]
    
    try:
        # Appeler directement _send_notification
        debt_notifier._send_notification(db, client_id, data)
        
        return {
            "success": True,
            "client_id": client_id,
            "client_name": cl.name if cl else "Unknown",
            "client_phone": cl.phone if cl else None,
            "message": "Notification sent successfully",
            "overdue_invoices_count": len(data["invoices"]),
            "overdue_manual_debts_count": len(data["manual"])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send notification: {str(e)}")

@router.post("/test-webhook")
async def test_webhook_direct(
    phone: str,
    message: str = "Test message from debt notifier debug",
    current_user = Depends(get_current_user)
):
    """Test direct du webhook n8n sans passer par le DebtNotifier"""
    try:
        # Appeler directement _send_whatsapp_n8n
        success = debt_notifier._send_whatsapp_n8n(phone, message, client_id=999)
        
        return {
            "success": success,
            "phone": phone,
            "message": message,
            "webhook_url": debt_notifier._default_cc  # Juste pour info
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Webhook test failed: {str(e)}")
