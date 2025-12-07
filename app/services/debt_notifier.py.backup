import os
import threading
import time
from datetime import datetime, date, timedelta
import smtplib
from email.mime.text import MIMEText
from typing import Optional
import base64
from urllib import request as _urlrequest
from urllib import parse as _urlparse

from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db, Invoice, Client, ClientDebt, AppCache, SessionLocal

class DebtNotifier:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval_seconds = int(os.getenv("DEBT_REMINDER_INTERVAL_SECONDS", "21600"))  # 6h
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
                self._tick()
            except Exception as e:
                print(f"[DebtNotifier] Error in tick: {e}")
            self._stop.wait(self._interval_seconds)

    def _tick(self):
        db: Session = SessionLocal()
        try:
            today = date.today()
            # Collect clients with overdue invoices
            inv_rows = (
                db.query(Invoice, Client)
                .join(Client, Client.client_id == Invoice.client_id, isouter=True)
                .filter((func.coalesce(Invoice.remaining_amount, Invoice.total - func.coalesce(Invoice.paid_amount, 0)) > 0))
                .filter(Invoice.due_date.isnot(None))
                .all()
            )
            client_overdue = {}
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
                        "due_date": inv.due_date,
                        "remaining": remaining,
                    })
            # Collect clients with overdue manual debts
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
                        "due_date": d.due_date,
                        "remaining": remaining,
                    })

            # Respect reminder period per client
            for cid, data in client_overdue.items():
                if not data["invoices"] and not data["manual"]:
                    continue
                if not self._should_notify(db, cid):
                    continue
                self._send_notification(db, cid, data)
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _should_notify(self, db: Session, client_id: int) -> bool:
        key = f"DEBT_REMINDER_LAST_SENT_{client_id}"
        rec = db.query(AppCache).filter(AppCache.cache_key == key).first()
        if not rec:
            return True
        try:
            last = datetime.fromisoformat(rec.cache_value)
        except Exception:
            return True
        return (datetime.now() - last) >= timedelta(days=self._period_days)

    def _mark_sent(self, db: Session, client_id: int):
        key = f"DEBT_REMINDER_LAST_SENT_{client_id}"
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

    def _send_notification(self, db: Session, client_id: int, data: dict):
        cl: Client = data.get("client")
        total_remaining = sum(x["remaining"] for x in data.get("invoices", [])) + sum(x["remaining"] for x in data.get("manual", []))
        subject = f"Rappel d'échéance - {cl.name}"
        lines = [
            f"Bonjour {cl.name},",
            "",
            "Nous vous informons que certaines créances ont dépassé leur date d'échéance :",
        ]
        if data.get("invoices"):
            lines.append("\nFactures en retard:")
            for inv in data["invoices"]:
                dd = inv.get("due_date")
                dd_s = dd.strftime("%Y-%m-%d") if hasattr(dd, 'strftime') else str(dd)
                lines.append(f" - Facture {inv['invoice_number']} • Échéance: {dd_s} • Restant: {inv['remaining']:.0f} XOF")
        if data.get("manual"):
            lines.append("\nCréances manuelles en retard:")
            for d in data["manual"]:
                dd = d.get("due_date")
                dd_s = dd.strftime("%Y-%m-%d") if hasattr(dd, 'strftime') else str(dd)
                lines.append(f" - Réf {d['reference']} • Échéance: {dd_s} • Restant: {d['remaining']:.0f} XOF")
        lines.append("\nMerci de régulariser votre situation dans les meilleurs délais.")
        body = "\n".join(lines)

        channel = os.getenv("DEBT_REMINDER_CHANNEL", "log").lower()
        if self._dry_run:
            print(f"[DebtNotifier] DRY-RUN would send to client_id={client_id} ({cl.email or cl.phone}):\n{body}")
            self._mark_sent(db, client_id)
            return
        if channel == "email" and cl.email and os.getenv("SMTP_HOST"):
            self._send_email(cl.email, subject, body)
            self._mark_sent(db, client_id)
        elif channel == "sms":
            to_phone = (cl.phone or '').strip()
            to_phone = self._normalize_phone(to_phone)
            if not to_phone:
                print(f"[DebtNotifier] No phone for client_id={client_id}, cannot send SMS")
                return
            ok = self._send_sms_twilio(to_phone, body)
            if ok:
                self._mark_sent(db, client_id)
            else:
                print(f"[DebtNotifier] Failed to send SMS to {to_phone} (client_id={client_id})")
        else:
            # Fallback: log only
            print(f"[DebtNotifier] notify client_id={client_id} ({cl.email or cl.phone}):\n{body}")
            self._mark_sent(db, client_id)

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
            print(f"[DebtNotifier] SMTP_HOST not configured, cannot send to {to_email}")
            return
        try:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.starttls()
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(msg)
                print(f"[DebtNotifier] Email sent to {to_email}")
        except Exception as e:
            print(f"[DebtNotifier] Failed to send email to {to_email}: {e}")

    def _send_sms_twilio(self, to_phone: str, body: str) -> bool:
        """Send SMS via Twilio REST API using only stdlib. Returns True if 2xx."""
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        from_phone = os.getenv("TWILIO_FROM")
        if not (account_sid and auth_token and from_phone):
            print("[DebtNotifier] Twilio env vars missing (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM)")
            return False

        # Basic normalization: ensure it starts with '+'; otherwise send as-is
        to_norm = to_phone if to_phone.startswith('+') else to_phone

        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        payload = {
            'From': from_phone,
            'To': to_norm,
            'Body': body,
        }
        data_bytes = _urlparse.urlencode(payload).encode('utf-8')
        req = _urlrequest.Request(url, data=data_bytes, method='POST')
        # Twilio requires Basic auth: base64(ACxxxxxxxx:auth_token)
        token = base64.b64encode(f"{account_sid}:{auth_token}".encode('utf-8')).decode('ascii')
        req.add_header('Authorization', f'Basic {token}')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')

        try:
            with _urlrequest.urlopen(req, timeout=15) as resp:
                ok = 200 <= resp.status < 300
                if not ok:
                    print(f"[DebtNotifier] Twilio SMS non-2xx status: {resp.status}")
                return ok
        except Exception as e:
            print(f"[DebtNotifier] Twilio SMS error: {e}")
            return False

    def _normalize_phone(self, raw: str) -> Optional[str]:
        """Normalize phone to E.164. Defaults to +221 for local numbers like 77XXXXXXX.
        Returns None if cannot normalize."""
        if not raw:
            return None
        s = str(raw).strip()
        # Replace common separators
        for ch in [' ', '-', '(', ')', '.']:
            s = s.replace(ch, '')
        # 00 -> +
        if s.startswith('00'):
            s = '+' + s[2:]
        # If already in E.164
        if s.startswith('+') and s[1:].isdigit():
            return s
        # If starts with country code digits (e.g., 221...)
        cc_digits = self._default_cc.lstrip('+')
        if s.isdigit() and s.startswith(cc_digits):
            return '+' + s
        # Handle leading 0 national format (e.g., 077..., 77...)
        if s.startswith('0') and s[1:].isdigit():
            local = s.lstrip('0')
            return f"{self._default_cc}{local}"
        # Senegal common local mobile: 9 digits starting with 7
        if s.isdigit() and len(s) == 9 and s[0] == '7':
            return f"{self._default_cc}{s}"
        # As a last resort, if digits only, prefix country
        if s.isdigit():
            return f"{self._default_cc}{s}"
        return None


# Singleton

debt_notifier = DebtNotifier()
