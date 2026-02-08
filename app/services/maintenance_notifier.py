import os
import asyncio
import threading
import time
from datetime import datetime, date, timedelta
import json
from urllib import request as _urlrequest
from typing import Optional

from ..database import Maintenance, Client, AppCache, get_next_id


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
                asyncio.run(self._tick())
            except Exception as e:
                print(f"[MaintenanceNotifier] Error in tick: {e}")
            self._stop.wait(self._interval_seconds)

    async def _tick(self):
        today = date.today()
        reminder_date = today + timedelta(days=self._days_before_deadline)

        # 1. Rappel préventif : 2 jours avant la fin du délai
        upcoming_maintenances = await Maintenance.find(
            {
                "pickup_deadline": {"$ne": None, "$eq": reminder_date},
                "pickup_date": None,
                "status": {"$in": ["completed", "ready"]},
            }
        ).to_list()

        print(f"[MaintenanceNotifier] Found {len(upcoming_maintenances)} maintenances with deadline in {self._days_before_deadline} days")

        for maintenance in upcoming_maintenances:
            if not await self._should_notify_warning(maintenance.maintenance_id):
                continue
            await self._send_warning_notification(maintenance)

        # 2. Rappel final : le jour même de la fin du délai (avec dégagement de responsabilité)
        deadline_today_maintenances = await Maintenance.find(
            {
                "pickup_deadline": {"$ne": None, "$eq": today},
                "pickup_date": None,
                "liability_waived": {"$ne": True},
                "status": {"$in": ["completed", "ready"]},
            }
        ).to_list()

        print(f"[MaintenanceNotifier] Found {len(deadline_today_maintenances)} maintenances with deadline today")

        for maintenance in deadline_today_maintenances:
            if not await self._should_notify_final(maintenance.maintenance_id):
                continue
            await self._send_final_notification(maintenance)
            # Dégager la responsabilité automatiquement
            await self._waive_liability(maintenance)

    async def _should_notify_warning(self, maintenance_id: int) -> bool:
        key = f"MAINTENANCE_WARNING_REMINDER_{maintenance_id}"
        rec = await AppCache.find_one(AppCache.cache_key == key)
        return rec is None

    async def _should_notify_final(self, maintenance_id: int) -> bool:
        key = f"MAINTENANCE_FINAL_REMINDER_{maintenance_id}"
        rec = await AppCache.find_one(AppCache.cache_key == key)
        return rec is None

    async def _mark_warning_sent(self, maintenance_id: int):
        key = f"MAINTENANCE_WARNING_REMINDER_{maintenance_id}"
        rec = await AppCache.find_one(AppCache.cache_key == key)
        now_s = datetime.now().isoformat()
        if not rec:
            new_id = await get_next_id("app_cache")
            rec = AppCache(cache_id=new_id, cache_key=key, cache_value=now_s)
            await rec.insert()
        else:
            rec.cache_value = now_s
            await rec.save()

    async def _mark_final_sent(self, maintenance_id: int):
        key = f"MAINTENANCE_FINAL_REMINDER_{maintenance_id}"
        rec = await AppCache.find_one(AppCache.cache_key == key)
        now_s = datetime.now().isoformat()
        if not rec:
            new_id = await get_next_id("app_cache")
            rec = AppCache(cache_id=new_id, cache_key=key, cache_value=now_s)
            await rec.insert()
        else:
            rec.cache_value = now_s
            await rec.save()

    async def _waive_liability(self, maintenance):
        try:
            maintenance.liability_waived = True
            maintenance.liability_waived_date = date.today()
            await maintenance.save()
            print(f"[MaintenanceNotifier] Liability waived for maintenance {maintenance.maintenance_number}")
        except Exception as e:
            print(f"[MaintenanceNotifier] Error waiving liability: {e}")

    def _get_device_info(self, maintenance) -> str:
        device_info = maintenance.device_type or "Appareil"
        if maintenance.device_brand:
            device_info += f" {maintenance.device_brand}"
        if maintenance.device_model:
            device_info += f" {maintenance.device_model}"
        return device_info

    async def _send_warning_notification(self, maintenance):
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
            await self._mark_warning_sent(maintenance.maintenance_id)
            return

        to_phone = (maintenance.client_phone or '').strip()
        to_phone = self._normalize_phone(to_phone)
        if not to_phone:
            print(f"[MaintenanceNotifier] No phone for maintenance {maintenance.maintenance_number}")
            return

        ok = self._send_whatsapp_n8n(to_phone, body, maintenance.maintenance_id)
        if ok:
            await self._mark_warning_sent(maintenance.maintenance_id)
            print(f"[MaintenanceNotifier] Sent warning reminder for {maintenance.maintenance_number}")
        else:
            print(f"[MaintenanceNotifier] Failed to send warning to {to_phone}")

    async def _send_final_notification(self, maintenance):
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
            await self._mark_final_sent(maintenance.maintenance_id)
            return

        to_phone = (maintenance.client_phone or '').strip()
        to_phone = self._normalize_phone(to_phone)
        if not to_phone:
            print(f"[MaintenanceNotifier] No phone for maintenance {maintenance.maintenance_number}")
            return

        ok = self._send_whatsapp_n8n(to_phone, body, maintenance.maintenance_id)
        if ok:
            await self._mark_final_sent(maintenance.maintenance_id)
            print(f"[MaintenanceNotifier] Sent final reminder for {maintenance.maintenance_number}")
        else:
            print(f"[MaintenanceNotifier] Failed to send final reminder to {to_phone}")

    def _send_whatsapp_n8n(self, to_phone: str, body: str, maintenance_id: int = None) -> bool:
        """Envoie un message WhatsApp via Evolution API (direct)."""
        from .evolution_api import send_text_sync
        return send_text_sync(to_phone, body)

    def _normalize_phone(self, raw: str) -> Optional[str]:
        from .evolution_api import normalize_phone
        return normalize_phone(raw)


# Singleton
maintenance_notifier = MaintenanceNotifier()
