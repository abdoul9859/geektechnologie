import os
import threading
import time
from datetime import datetime, date, timedelta
import json
from urllib import request as _urlrequest
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_

from ..database import get_db, Maintenance, Client, AppCache, SessionLocal


class MaintenanceNotifier:
    """Service de notification automatique pour les maintenances.
    
    Envoie 2 rappels :
    1. 2 jours avant la fin du délai de récupération (rappel préventif)
    2. Le jour même de la fin du délai (avec dégagement de responsabilité)
    """
    
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval_seconds = int(os.getenv("MAINTENANCE_REMINDER_INTERVAL_SECONDS", "86400"))  # 24h par défaut
        self._days_before_deadline = int(os.getenv("MAINTENANCE_REMINDER_DAYS_BEFORE", "2"))  # 2 jours avant
        self._dry_run = os.getenv("MAINTENANCE_REMINDER_DRY_RUN", "false").lower() == "true"
        self._default_cc = os.getenv("DEFAULT_COUNTRY_CODE", "+221")

    def start_background(self):
        if not os.getenv("ENABLE_MAINTENANCE_REMINDERS", "false").lower() == "true":
            print("[MaintenanceNotifier] Disabled (ENABLE_MAINTENANCE_REMINDERS != true)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="MaintenanceNotifier", daemon=True)
        self._thread.start()
        print("[MaintenanceNotifier] Started background thread")

    def stop_background(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self):
        # Attendre un peu au démarrage pour laisser l'app s'initialiser
        time.sleep(45)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[MaintenanceNotifier] Error in tick: {e}")
            self._stop.wait(self._interval_seconds)

    def _tick(self):
        db: Session = SessionLocal()
        try:
            today = date.today()
            reminder_date = today + timedelta(days=self._days_before_deadline)
            
            # 1. Rappel préventif : 2 jours avant la fin du délai
            # Maintenances dont le délai arrive dans X jours
            upcoming_maintenances = (
                db.query(Maintenance)
                .filter(Maintenance.pickup_deadline.isnot(None))
                .filter(Maintenance.pickup_deadline == reminder_date)  # Délai dans X jours
                .filter(Maintenance.pickup_date.is_(None))  # Pas encore récupéré
                .filter(Maintenance.status.in_(['completed', 'ready']))  # Réparation terminée
                .all()
            )
            
            print(f"[MaintenanceNotifier] Found {len(upcoming_maintenances)} maintenances with deadline in {self._days_before_deadline} days")
            
            for maintenance in upcoming_maintenances:
                if not self._should_notify_warning(db, maintenance.maintenance_id):
                    continue
                self._send_warning_notification(db, maintenance)
            
            # 2. Rappel final : le jour même de la fin du délai (avec dégagement de responsabilité)
            deadline_today_maintenances = (
                db.query(Maintenance)
                .filter(Maintenance.pickup_deadline.isnot(None))
                .filter(Maintenance.pickup_deadline == today)  # Délai aujourd'hui
                .filter(Maintenance.pickup_date.is_(None))  # Pas encore récupéré
                .filter(Maintenance.liability_waived == False)  # Responsabilité pas encore dégagée
                .filter(Maintenance.status.in_(['completed', 'ready']))  # Réparation terminée
                .all()
            )
            
            print(f"[MaintenanceNotifier] Found {len(deadline_today_maintenances)} maintenances with deadline today")
            
            for maintenance in deadline_today_maintenances:
                if not self._should_notify_final(db, maintenance.maintenance_id):
                    continue
                self._send_final_notification(db, maintenance)
                # Dégager la responsabilité automatiquement
                self._waive_liability(db, maintenance)
                
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _should_notify_warning(self, db: Session, maintenance_id: int) -> bool:
        """Vérifie si on doit envoyer le rappel préventif (2 jours avant)."""
        key = f"MAINTENANCE_WARNING_REMINDER_{maintenance_id}"
        rec = db.query(AppCache).filter(AppCache.cache_key == key).first()
        return rec is None  # Envoyer seulement si pas encore envoyé

    def _should_notify_final(self, db: Session, maintenance_id: int) -> bool:
        """Vérifie si on doit envoyer le rappel final (jour même)."""
        key = f"MAINTENANCE_FINAL_REMINDER_{maintenance_id}"
        rec = db.query(AppCache).filter(AppCache.cache_key == key).first()
        return rec is None  # Envoyer seulement si pas encore envoyé

    def _mark_warning_sent(self, db: Session, maintenance_id: int):
        """Marque le rappel préventif comme envoyé."""
        key = f"MAINTENANCE_WARNING_REMINDER_{maintenance_id}"
        rec = db.query(AppCache).filter(AppCache.cache_key == key).first()
        now_s = datetime.now().isoformat()
        if not rec:
            rec = AppCache(cache_key=key, cache_value=now_s)
            db.add(rec)
        else:
            rec.cache_value = now_s
        try:
            db.commit()
        except Exception:
            db.rollback()

    def _mark_final_sent(self, db: Session, maintenance_id: int):
        """Marque le rappel final comme envoyé."""
        key = f"MAINTENANCE_FINAL_REMINDER_{maintenance_id}"
        rec = db.query(AppCache).filter(AppCache.cache_key == key).first()
        now_s = datetime.now().isoformat()
        if not rec:
            rec = AppCache(cache_key=key, cache_value=now_s)
            db.add(rec)
        else:
            rec.cache_value = now_s
        try:
            db.commit()
        except Exception:
            db.rollback()

    def _waive_liability(self, db: Session, maintenance: Maintenance):
        """Dégage la responsabilité sur la machine."""
        try:
            maintenance.liability_waived = True
            maintenance.liability_waived_date = date.today()
            db.commit()
            print(f"[MaintenanceNotifier] Liability waived for maintenance {maintenance.maintenance_number}")
        except Exception as e:
            db.rollback()
            print(f"[MaintenanceNotifier] Error waiving liability: {e}")

    def _get_device_info(self, maintenance: Maintenance) -> str:
        """Retourne les informations sur l'appareil."""
        device_info = maintenance.device_type or "Appareil"
        if maintenance.device_brand:
            device_info += f" {maintenance.device_brand}"
        if maintenance.device_model:
            device_info += f" {maintenance.device_model}"
        return device_info

    def _send_warning_notification(self, db: Session, maintenance: Maintenance):
        """Envoie le rappel préventif (2 jours avant la fin du délai)."""
        app_name = os.getenv("APP_NAME", "GeekTechnologie")
        
        deadline = maintenance.pickup_deadline
        if hasattr(deadline, 'date'):
            deadline = deadline.date()
        deadline_str = deadline.strftime("%d/%m/%Y") if deadline else "N/A"
        device_info = self._get_device_info(maintenance)
        
        lines = [
            f"Bonjour {maintenance.client_name},",
            "",
            f"📢 {app_name} vous rappelle que votre appareil est prêt à être récupéré.",
            "",
            f"📋 Fiche de maintenance : {maintenance.maintenance_number}",
            f"🖥️ Appareil : {device_info}",
            f"📅 Date limite de récupération : {deadline_str}",
            f"⏳ Il vous reste {self._days_before_deadline} jour(s) pour récupérer votre appareil.",
            "",
            "⚠️ RAPPEL :",
            f"Passé ce délai, {app_name} dégagera toute responsabilité sur votre appareil conformément à nos conditions générales.",
            "",
            "Nous vous invitons à venir récupérer votre appareil dans les meilleurs délais.",
            "",
            "Pour toute question, n'hésitez pas à nous contacter.",
            "",
            "Cordialement,",
            app_name
        ]
        
        body = "\n".join(lines)
        
        if self._dry_run:
            print(f"[MaintenanceNotifier] DRY-RUN warning to {maintenance.client_name}:\n{body}")
            self._mark_warning_sent(db, maintenance.maintenance_id)
            return
        
        to_phone = (maintenance.client_phone or '').strip()
        to_phone = self._normalize_phone(to_phone)
        if not to_phone:
            print(f"[MaintenanceNotifier] No phone for maintenance {maintenance.maintenance_number}")
            return
        
        ok = self._send_whatsapp_n8n(to_phone, body, maintenance.maintenance_id)
        if ok:
            self._mark_warning_sent(db, maintenance.maintenance_id)
            print(f"[MaintenanceNotifier] Sent warning reminder for {maintenance.maintenance_number}")
        else:
            print(f"[MaintenanceNotifier] Failed to send warning to {to_phone}")

    def _send_final_notification(self, db: Session, maintenance: Maintenance):
        """Envoie le rappel final (jour même) avec dégagement de responsabilité."""
        app_name = os.getenv("APP_NAME", "GeekTechnologie")
        
        deadline = maintenance.pickup_deadline
        if hasattr(deadline, 'date'):
            deadline = deadline.date()
        deadline_str = deadline.strftime("%d/%m/%Y") if deadline else "N/A"
        device_info = self._get_device_info(maintenance)
        
        lines = [
            f"Bonjour {maintenance.client_name},",
            "",
            f"🔴 {app_name} vous informe que le délai de récupération de votre appareil expire AUJOURD'HUI.",
            "",
            f"📋 Fiche de maintenance : {maintenance.maintenance_number}",
            f"🖥️ Appareil : {device_info}",
            f"📅 Date limite de récupération : {deadline_str}",
            "",
            "⚠️ IMPORTANT :",
            f"Conformément à nos conditions générales, {app_name} dégage toute responsabilité sur votre appareil à compter de ce jour.",
            "",
            "Nous vous invitons à récupérer votre appareil dans les plus brefs délais.",
            "",
            "Pour toute question, n'hésitez pas à nous contacter.",
            "",
            "Cordialement,",
            app_name
        ]
        
        body = "\n".join(lines)
        
        if self._dry_run:
            print(f"[MaintenanceNotifier] DRY-RUN final to {maintenance.client_name}:\n{body}")
            self._mark_final_sent(db, maintenance.maintenance_id)
            return
        
        to_phone = (maintenance.client_phone or '').strip()
        to_phone = self._normalize_phone(to_phone)
        if not to_phone:
            print(f"[MaintenanceNotifier] No phone for maintenance {maintenance.maintenance_number}")
            return
        
        ok = self._send_whatsapp_n8n(to_phone, body, maintenance.maintenance_id)
        if ok:
            self._mark_final_sent(db, maintenance.maintenance_id)
            print(f"[MaintenanceNotifier] Sent final reminder for {maintenance.maintenance_number}")
        else:
            print(f"[MaintenanceNotifier] Failed to send final reminder to {to_phone}")

    def _send_whatsapp_n8n(self, to_phone: str, body: str, maintenance_id: int = None) -> bool:
        """Send WhatsApp message via n8n webhook. Returns True if successful."""
        n8n_base = os.getenv("N8N_BASE_URL", "http://n8n:5678")
        webhook_url = f"{n8n_base}/webhook/send-maintenance-reminder-whatsapp"
        
        to_norm = self._normalize_phone(to_phone)
        if not to_norm:
            print(f"[MaintenanceNotifier] Cannot normalize phone: {to_phone}")
            return False
        
        payload = {
            'phone': to_norm,
            'message': body,
            'maintenance_id': maintenance_id,
            'app': os.getenv('APP_NAME', 'GeekTechnologie'),
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            data_bytes = json.dumps(payload).encode('utf-8')
            req = _urlrequest.Request(webhook_url, data=data_bytes, method='POST')
            req.add_header('Content-Type', 'application/json')
            
            with _urlrequest.urlopen(req, timeout=30) as resp:
                ok = 200 <= resp.status < 300
                if ok:
                    print(f"[MaintenanceNotifier] WhatsApp sent via n8n to {to_norm}")
                else:
                    print(f"[MaintenanceNotifier] n8n webhook non-2xx status: {resp.status}")
                return ok
        except Exception as e:
            print(f"[MaintenanceNotifier] n8n webhook error: {e}")
            return False

    def _normalize_phone(self, raw: str) -> Optional[str]:
        """Normalize phone to E.164 format."""
        if not raw:
            return None
        s = str(raw).strip()
        for ch in [' ', '-', '(', ')', '.']:
            s = s.replace(ch, '')
        if s.startswith('00'):
            s = '+' + s[2:]
        if s.startswith('+') and s[1:].isdigit():
            return s
        cc_digits = self._default_cc.lstrip('+')
        if s.isdigit() and s.startswith(cc_digits):
            return '+' + s
        if s.startswith('0') and s[1:].isdigit():
            local = s.lstrip('0')
            return f"{self._default_cc}{local}"
        if s.isdigit() and len(s) == 9 and s[0] == '7':
            return f"{self._default_cc}{s}"
        if s.isdigit():
            return f"{self._default_cc}{s}"
        return None


# Singleton
maintenance_notifier = MaintenanceNotifier()
