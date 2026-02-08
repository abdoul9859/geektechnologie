import os
import asyncio
import threading
import time
from datetime import datetime, date, timedelta
import json
from urllib import request as _urlrequest
from typing import Optional

from ..database import Invoice, Client, AppCache, get_next_id


class WarrantyNotifier:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval_seconds = int(os.getenv("WARRANTY_REMINDER_INTERVAL_SECONDS", "86400"))
        self._days_before_expiry = int(os.getenv("WARRANTY_REMINDER_DAYS_BEFORE", "7"))
        self._dry_run = os.getenv("WARRANTY_REMINDER_DRY_RUN", "false").lower() == "true"
        self._default_cc = os.getenv("DEFAULT_COUNTRY_CODE", "+221")

    def start_background(self):
        if not os.getenv("ENABLE_WARRANTY_REMINDERS", "false").lower() == "true":
            print("[WarrantyNotifier] Disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="WarrantyNotifier", daemon=True)
        self._thread.start()

    def stop_background(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self):
        time.sleep(30)
        while not self._stop.is_set():
            try:
                asyncio.run(self._tick())
            except Exception as e:
                print(f"[WarrantyNotifier] Error in tick: {e}")
            self._stop.wait(self._interval_seconds)

    async def _tick(self):
        today = date.today()
        reminder_date = today + timedelta(days=self._days_before_expiry)
        invoices_expiring = await Invoice.find(
            {"has_warranty": True, "warranty_end_date": {"$ne": None, "$gte": today, "$lte": reminder_date}}
        ).to_list()
        print(f"[WarrantyNotifier] Found {len(invoices_expiring)} invoices with warranty expiring soon")
        for invoice in invoices_expiring:
            client = await Client.find_one(Client.client_id == invoice.client_id)
            if not client:
                continue
            if not await self._should_notify(invoice.invoice_id):
                continue
            await self._send_notification(invoice, client)

    async def _should_notify(self, invoice_id: int) -> bool:
        key = f"WARRANTY_REMINDER_SENT_{invoice_id}"
        rec = await AppCache.find_one(AppCache.cache_key == key)
        return rec is None

    async def _mark_sent(self, invoice_id: int):
        key = f"WARRANTY_REMINDER_SENT_{invoice_id}"
        rec = await AppCache.find_one(AppCache.cache_key == key)
        now_s = datetime.now().isoformat()
        if not rec:
            new_id = await get_next_id("app_cache")
            rec = AppCache(cache_id=new_id, cache_key=key, cache_value=now_s)
            await rec.insert()
        else:
            rec.cache_value = now_s
            await rec.save()

    async def _send_notification(self, invoice, client):
        app_name = os.getenv("APP_NAME", "GeekTechnologie")
        today = date.today()
        end_date = invoice.warranty_end_date
        if hasattr(end_date, 'date'):
            end_date = end_date.date()
        days_remaining = (end_date - today).days
        end_date_str = end_date.strftime("%d/%m/%Y") if end_date else "N/A"
        lines = [
            f"Bonjour {client.name},", "",
            f"📋 {app_name} vous informe que la garantie de votre achat arrive bientôt à expiration.", "",
            f"📄 Facture : {invoice.invoice_number}",
            f"📅 Date de fin de garantie : {end_date_str}",
            f"⏳ Jours restants : {days_remaining} jour(s)", "",
            f"Durée de garantie : {invoice.warranty_duration} mois", "",
            "Si vous rencontrez un problème avec votre produit, nous vous invitons à nous contacter avant l'expiration de la garantie.", "",
            "Cordialement,", app_name
        ]
        body = "\n".join(lines)
        if self._dry_run:
            print(f"[WarrantyNotifier] DRY-RUN to {client.name} ({client.phone}):\n{body}")
            await self._mark_sent(invoice.invoice_id)
            return
        to_phone = self._normalize_phone((client.phone or '').strip())
        if not to_phone:
            return
        ok = self._send_whatsapp_n8n(to_phone, body, invoice.invoice_id)
        if ok:
            await self._mark_sent(invoice.invoice_id)

    def _send_whatsapp_n8n(self, to_phone, body, invoice_id=None):
        n8n_base = os.getenv("N8N_BASE_URL", "http://n8n:5678")
        webhook_url = f"{n8n_base}/webhook/send-warranty-reminder-whatsapp"
        to_norm = self._normalize_phone(to_phone)
        if not to_norm:
            return False
        payload = {'phone': to_norm, 'message': body, 'invoice_id': invoice_id, 'app': os.getenv('APP_NAME', 'GeekTechnologie'), 'timestamp': datetime.now().isoformat()}
        try:
            data_bytes = json.dumps(payload).encode('utf-8')
            req = _urlrequest.Request(webhook_url, data=data_bytes, method='POST')
            req.add_header('Content-Type', 'application/json')
            with _urlrequest.urlopen(req, timeout=30) as resp:
                return 200 <= resp.status < 300
        except Exception as e:
            print(f"[WarrantyNotifier] n8n error: {e}")
            return False

    def _normalize_phone(self, raw):
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
            return f"{self._default_cc}{s.lstrip('0')}"
        if s.isdigit() and len(s) == 9 and s[0] == '7':
            return f"{self._default_cc}{s}"
        if s.isdigit():
            return f"{self._default_cc}{s}"
        return None


warranty_notifier = WarrantyNotifier()
