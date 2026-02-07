import os
import asyncio
import threading
import time
from datetime import datetime, date, timedelta
import smtplib
from email.mime.text import MIMEText
from typing import Optional
import base64
import json
from urllib import request as _urlrequest
from urllib import parse as _urlparse

from ..database import Invoice, Client, ClientDebt, AppCache, get_next_id


class DebtNotifier:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval_seconds = int(os.getenv("DEBT_REMINDER_INTERVAL_SECONDS", "21600"))
        self._period_days = int(os.getenv("DEBT_REMINDER_PERIOD_DAYS", "2"))
        self._dry_run = os.getenv("DEBT_REMINDER_DRY_RUN", "false").lower() == "true"
        self._default_cc = os.getenv("DEFAULT_COUNTRY_CODE", "+221")

    def start_background(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="DebtNotifier", daemon=True)
        self._thread.start()

    def stop_background(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self):
        while not self._stop.is_set():
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self._tick())
                loop.close()
            except Exception as e:
                print(f"[DebtNotifier] Error in tick: {e}")
            self._stop.wait(self._interval_seconds)

    async def _tick(self):
        today = date.today()
        # Collect clients with overdue invoices
        invoices = await Invoice.find(
            {"due_date": {"$ne": None}}
        ).to_list()

        client_overdue = {}
        for inv in invoices:
            dd = inv.due_date
            if hasattr(dd, 'date'):
                dd = dd.date()
            amount = float(inv.total or 0)
            paid = float(inv.paid_amount or 0)
            remaining = float(inv.remaining_amount if inv.remaining_amount is not None else max(0.0, amount - paid))
            if dd and remaining > 0 and dd < today:
                cid = inv.client_id
                if cid is None:
                    continue
                if cid not in client_overdue:
                    cl = await Client.find_one(Client.client_id == cid)
                    client_overdue[cid] = {"client": cl, "invoices": [], "manual": []}
                client_overdue[cid]["invoices"].append({
                    "invoice_number": inv.invoice_number,
                    "due_date": inv.due_date,
                    "remaining": remaining,
                })

        # Collect clients with overdue manual debts
        debts = await ClientDebt.find().to_list()
        for d in debts:
            dd = d.due_date
            if hasattr(dd, 'date'):
                dd = dd.date()
            amount = float(d.amount or 0)
            paid = float(d.paid_amount or 0)
            remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)
            if dd and remaining > 0 and dd < today and d.client_id is not None:
                cid = int(d.client_id)
                if cid not in client_overdue:
                    cl = await Client.find_one(Client.client_id == cid)
                    client_overdue[cid] = {"client": cl, "invoices": [], "manual": []}
                client_overdue[cid]["manual"].append({
                    "reference": d.reference,
                    "due_date": d.due_date,
                    "remaining": remaining,
                })

        for cid, data in client_overdue.items():
            if not data["invoices"] and not data["manual"]:
                continue
            cl = data.get("client")
            if cl and getattr(cl, 'disable_debt_reminder', False):
                print(f"[DebtNotifier] Skipping client {cl.name} (reminders disabled)")
                continue
            if not await self._should_notify(cid):
                continue
            await self._send_notification(cid, data)

    async def _should_notify(self, client_id: int) -> bool:
        key = f"DEBT_REMINDER_LAST_SENT_{client_id}"
        rec = await AppCache.find_one(AppCache.cache_key == key)
        if not rec:
            return True
        try:
            last = datetime.fromisoformat(rec.cache_value)
        except Exception:
            return True
        return (datetime.now() - last) >= timedelta(days=self._period_days)

    async def _mark_sent(self, client_id: int):
        key = f"DEBT_REMINDER_LAST_SENT_{client_id}"
        rec = await AppCache.find_one(AppCache.cache_key == key)
        now_s = datetime.now().isoformat()
        if not rec:
            new_id = await get_next_id("app_cache")
            rec = AppCache(cache_id=new_id, cache_key=key, cache_value=now_s)
            await rec.insert()
        else:
            rec.cache_value = now_s
            await rec.save()

    async def _send_notification(self, client_id: int, data: dict):
        cl = data.get("client")
        total_remaining = sum(x["remaining"] for x in data.get("invoices", [])) + sum(x["remaining"] for x in data.get("manual", []))
        subject = f"Rappel d'échéance - {cl.name}"
        app_name = os.getenv("APP_NAME", "GeekTechnologie")

        lines = [
            f"Bonjour {cl.name},",
            "",
            f"📌 {app_name} vous informe que vous avez des montants en retard de paiement.",
            f"Montant total restant : {total_remaining:.0f} XOF",
        ]
        if data.get("invoices"):
            lines.append("\n📄 Factures en retard :")
            for inv in data["invoices"]:
                dd = inv.get("due_date")
                dd_s = dd.strftime("%Y-%m-%d") if hasattr(dd, 'strftime') else str(dd)
                lines.append(f" - Facture {inv['invoice_number']} • Échéance {dd_s} • {inv['remaining']:.0f} XOF")
        if data.get("manual"):
            lines.append("\n🧾 Créances en retard :")
            for d in data["manual"]:
                dd = d.get("due_date")
                dd_s = dd.strftime("%Y-%m-%d") if hasattr(dd, 'strftime') else str(dd)
                lines.append(f" - Réf {d['reference']} • Échéance {dd_s} • {d['remaining']:.0f} XOF")
        lines.append("\nMerci de régulariser votre situation dans les meilleurs délais.")
        lines.append("Si vous avez déjà effectué le paiement, veuillez ignorer ce message.")
        lines.append("\nCordialement,")
        lines.append(app_name)
        body = "\n".join(lines)

        channel = (os.getenv("DEBT_REMINDER_CHANNEL", "log") or "log").strip().lower()
        if self._dry_run:
            print(f"[DebtNotifier] DRY-RUN would send to client_id={client_id} ({cl.email or cl.phone}):\n{body}")
            await self._mark_sent(client_id)
            return
        if channel == "email" and cl.email and os.getenv("SMTP_HOST"):
            self._send_email(cl.email, subject, body)
            await self._mark_sent(client_id)
        elif channel == "whatsapp":
            to_phone = self._normalize_phone((cl.phone or '').strip())
            if not to_phone:
                print(f"[DebtNotifier] No phone for client_id={client_id}")
                return
            ok = self._send_whatsapp_n8n(to_phone, body, client_id)
            if ok:
                await self._mark_sent(client_id)
        elif channel == "sms":
            to_phone = self._normalize_phone((cl.phone or '').strip())
            if not to_phone:
                return
            ok = self._send_sms_twilio(to_phone, body)
            if ok:
                await self._mark_sent(client_id)
        else:
            print(f"[DebtNotifier] notify client_id={client_id} ({cl.email or cl.phone}):\n{body}")
            await self._mark_sent(client_id)

    def _send_email(self, to_email: str, subject: str, body: str):
        host = os.getenv("SMTP_HOST")
        port = int(os.getenv("SMTP_PORT", "587"))
        user = os.getenv("SMTP_USER")
        password = os.getenv("SMTP_PASSWORD")
        sender = os.getenv("SMTP_SENDER", user or "no-reply@example.com")
        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to_email
        if not host:
            return
        try:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.starttls()
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(msg)
        except Exception as e:
            print(f"[DebtNotifier] Failed to send email to {to_email}: {e}")

    def _send_sms_twilio(self, to_phone: str, body: str) -> bool:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        from_phone = os.getenv("TWILIO_FROM")
        if not (account_sid and auth_token and from_phone):
            return False
        to_norm = to_phone if to_phone.startswith('+') else to_phone
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        payload = {'From': from_phone, 'To': to_norm, 'Body': body}
        data_bytes = _urlparse.urlencode(payload).encode('utf-8')
        req = _urlrequest.Request(url, data=data_bytes, method='POST')
        token = base64.b64encode(f"{account_sid}:{auth_token}".encode('utf-8')).decode('ascii')
        req.add_header('Authorization', f'Basic {token}')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        try:
            with _urlrequest.urlopen(req, timeout=15) as resp:
                return 200 <= resp.status < 300
        except Exception as e:
            print(f"[DebtNotifier] Twilio SMS error: {e}")
            return False

    def _send_whatsapp_n8n(self, to_phone: str, body: str, client_id: int = None) -> bool:
        n8n_base = os.getenv("N8N_BASE_URL", "http://n8n:5678")
        webhook_url = f"{n8n_base}/webhook/send-debt-reminder-whatsapp"
        to_norm = self._normalize_phone(to_phone)
        if not to_norm:
            return False
        payload = {
            'phone': to_norm, 'message': body, 'client_id': client_id,
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
                    print(f"[DebtNotifier] WhatsApp sent via n8n to {to_norm}")
                return ok
        except Exception as e:
            print(f"[DebtNotifier] n8n webhook error: {e}")
            return False

    def _normalize_phone(self, raw: str) -> Optional[str]:
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


debt_notifier = DebtNotifier()
