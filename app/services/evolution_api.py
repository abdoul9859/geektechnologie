"""
Service centralise WhatsApp via Evolution API v2.

Tous les appels WhatsApp (QR, status, envoi texte, envoi media) passent par ce module.
Appels directs a Evolution API -- pas d'intermediaire n8n.
"""

import asyncio
import os
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# -- Configuration ----------------------------------------------------------------
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://evolution_api:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME", "geektech")
DEFAULT_COUNTRY_CODE = os.getenv("DEFAULT_COUNTRY_CODE", "+221")

_TIMEOUT = 30.0
APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "http://app:8000")

# Cache QR code recu via webhook ou API
_qr_cache: dict = {"code": None, "base64": None, "updated_at": 0.0}
_last_qr_reset: float = 0
_QR_RESET_MIN_INTERVAL = 10  # secondes entre deux resets


def _headers() -> dict:
    return {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}


def _base_url() -> str:
    return EVOLUTION_API_URL.rstrip("/")


# -- Normalisation telephone (consolidee) ------------------------------------------
def normalize_phone(raw: str) -> Optional[str]:
    """Normalise un numero de telephone au format E.164 (+221XXXXXXXXX)."""
    if not raw:
        return None
    s = str(raw).strip()
    for ch in [" ", "-", "(", ")", "."]:
        s = s.replace(ch, "")
    if not s:
        return None
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("+") and s[1:].isdigit():
        return s
    cc_digits = DEFAULT_COUNTRY_CODE.lstrip("+")
    if s.isdigit() and s.startswith(cc_digits):
        return "+" + s
    if s.startswith("0") and s[1:].isdigit():
        local = s.lstrip("0")
        return f"{DEFAULT_COUNTRY_CODE}{local}"
    if s.isdigit() and len(s) == 9 and s[0] == "7":
        return f"{DEFAULT_COUNTRY_CODE}{s}"
    if s.isdigit():
        return f"{DEFAULT_COUNTRY_CODE}{s}"
    return None


def _phone_for_whatsapp(phone: str) -> Optional[str]:
    """Normalise et retire le '+' initial (format attendu par Evolution API)."""
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    return normalized.lstrip("+")


# -- Cache QR (alimente par webhook ou API) ----------------------------------------

def store_qr_from_webhook(code: str = None, base64_img: str = None):
    """Stocke le QR code recu via webhook Evolution API."""
    global _qr_cache
    _qr_cache = {
        "code": code,
        "base64": base64_img,
        "updated_at": time.time(),
    }
    logger.info(f"[EvolutionAPI] QR stocke via webhook (code={bool(code)}, base64={bool(base64_img)})")


def get_cached_qr() -> Optional[dict]:
    """Retourne le QR cache si frais (< 45 secondes)."""
    if not _qr_cache["code"] and not _qr_cache["base64"]:
        return None
    age = time.time() - _qr_cache["updated_at"]
    if age > 45:
        return None
    return _qr_cache


def clear_qr_cache():
    """Vide le cache QR."""
    global _qr_cache
    _qr_cache = {"code": None, "base64": None, "updated_at": 0.0}


# -- Gestion d'instance -----------------------------------------------------------

async def ensure_instance_exists() -> dict:
    """Cree l'instance si elle n'existe pas. Idempotent.

    Configure un webhook pour recevoir les QR codes et mises a jour de connexion.
    """
    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/instance/create"
    webhook_url = f"{APP_PUBLIC_URL}/api/whatsapp/webhook"
    payload = {
        "instanceName": instance,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": False,
            "webhookBase64": True,
            "events": [
                "QRCODE_UPDATED",
                "CONNECTION_UPDATE",
            ],
        },
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            r = await client.post(url, json=payload, headers=_headers())
            r.raise_for_status()
            data = r.json()
            logger.info(f"[EvolutionAPI] Instance '{instance}' creee (webhook: {webhook_url})")
            # Extraire le QR de la reponse de creation si present
            if _has_qr(data):
                qrcode = data.get("qrcode") or {}
                store_qr_from_webhook(
                    code=data.get("code") or qrcode.get("code"),
                    base64_img=data.get("base64") or qrcode.get("base64"),
                )
            return data
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (409, 403):
                logger.debug(f"[EvolutionAPI] Instance '{instance}' existe deja")
                # Configurer le webhook sur l'instance existante
                try:
                    await _set_webhook(webhook_url)
                except Exception:
                    pass
                return {"instanceName": instance, "status": "exists"}
            raise


async def _set_webhook(webhook_url: str) -> None:
    """Configure le webhook sur une instance existante."""
    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/webhook/set/{instance}"
    payload = {
        "enabled": True,
        "url": webhook_url,
        "webhookByEvents": False,
        "webhookBase64": True,
        "events": [
            "QRCODE_UPDATED",
            "CONNECTION_UPDATE",
        ],
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, json=payload, headers=_headers())
        logger.info(f"[EvolutionAPI] Webhook set: {r.status_code} {r.text[:200]}")


async def get_connection_state() -> dict:
    """Retourne l'etat de connexion de l'instance."""
    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/instance/connectionState/{instance}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, headers=_headers())
        r.raise_for_status()
        return r.json()


def _has_qr(data: dict) -> bool:
    """Verifie si la reponse contient un QR code."""
    if not isinstance(data, dict):
        return False
    if data.get("code") or data.get("base64"):
        return True
    qrcode = data.get("qrcode")
    if isinstance(qrcode, dict) and (qrcode.get("code") or qrcode.get("base64")):
        return True
    return False


async def _connect_instance() -> dict:
    """Appelle /instance/connect/ et retourne la reponse brute."""
    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/instance/connect/{instance}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=_headers())
        r.raise_for_status()
        return r.json()


async def _restart_instance_api() -> None:
    """Redemarre l'instance via l'API Evolution (POST /instance/restart/)."""
    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/instance/restart/{instance}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Evolution API v2 utilise POST pour restart (pas PUT)
        r = await client.post(url, headers=_headers())
        logger.info(f"[EvolutionAPI] restart API response: {r.status_code} {r.text[:200]}")


async def get_qr_code() -> dict:
    """Recupere le QR code pour scanner.

    Strategie:
    1. Verifier le cache QR (alimente par webhook)
    2. Essayer /instance/connect/ (retourne QR si etat 'close')
    3. Si pas de QR, redemarrer l'instance et attendre le webhook
    """
    global _last_qr_reset

    # 1. Verifier le cache webhook d'abord
    cached = get_cached_qr()
    if cached:
        logger.debug("[EvolutionAPI] QR depuis cache webhook")
        return {"code": cached["code"], "base64": cached["base64"]}

    # 2. Essayer /instance/connect/
    try:
        data = await _connect_instance()
        keys = list(data.keys()) if isinstance(data, dict) else str(type(data))
        logger.info(f"[EvolutionAPI] connect response keys: {keys}")

        if _has_qr(data):
            # Stocker dans le cache
            qrcode = data.get("qrcode") or {}
            store_qr_from_webhook(
                code=data.get("code") or qrcode.get("code"),
                base64_img=data.get("base64") or qrcode.get("base64"),
            )
            return data
    except Exception as e:
        logger.warning(f"[EvolutionAPI] connect failed: {e}")

    # 3. Pas de QR -- redemarrer l'instance (le QR arrivera par webhook)
    now = time.time()
    if (now - _last_qr_reset) < _QR_RESET_MIN_INTERVAL:
        logger.debug("[EvolutionAPI] Trop tot pour redemarrer, attente webhook...")
        # Verifier une derniere fois le cache
        cached = get_cached_qr()
        if cached:
            return {"code": cached["code"], "base64": cached["base64"]}
        return {}

    _last_qr_reset = now
    clear_qr_cache()
    logger.info("[EvolutionAPI] Pas de QR, redemarrage de l'instance...")

    # Essayer restart (POST), puis logout en fallback
    try:
        await _restart_instance_api()
        logger.info("[EvolutionAPI] Instance redemarree via API")
    except Exception as e:
        logger.warning(f"[EvolutionAPI] restart failed: {e}, tentative logout...")
        try:
            await logout_instance()
            logger.info("[EvolutionAPI] Logout effectue")
        except Exception as e2:
            logger.warning(f"[EvolutionAPI] logout failed: {e2}")

    # Attendre et reconnecter (force la generation d'un nouveau QR)
    await asyncio.sleep(3)

    try:
        data = await _connect_instance()
        if _has_qr(data):
            qrcode = data.get("qrcode") or {}
            store_qr_from_webhook(
                code=data.get("code") or qrcode.get("code"),
                base64_img=data.get("base64") or qrcode.get("base64"),
            )
            return data
    except Exception as e:
        logger.error(f"[EvolutionAPI] connect after restart failed: {e}")

    # Le QR sera envoye par webhook -- attendre un peu
    await asyncio.sleep(2)
    cached = get_cached_qr()
    if cached:
        return {"code": cached["code"], "base64": cached["base64"]}

    return {}


async def delete_instance() -> dict:
    """Supprime completement l'instance."""
    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/instance/delete/{instance}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.delete(url, headers=_headers())
        r.raise_for_status()
        return r.json()


async def logout_instance() -> dict:
    """Deconnecte la session WhatsApp (garde la config d'instance)."""
    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/instance/logout/{instance}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.delete(url, headers=_headers())
        r.raise_for_status()
        return r.json()


async def restart_instance() -> dict:
    """Redemarre l'instance pour generer un nouveau QR code."""
    global _last_qr_reset
    _last_qr_reset = 0
    clear_qr_cache()
    try:
        await _restart_instance_api()
    except Exception:
        try:
            await logout_instance()
        except Exception:
            pass
    await asyncio.sleep(2)
    return await ensure_instance_exists()


async def force_reset_instance() -> dict:
    """Supprime completement l'instance et en recree une nouvelle.

    Utilise quand l'instance est bloquee (count=0, pas de QR).
    """
    global _last_qr_reset
    _last_qr_reset = 0
    clear_qr_cache()
    instance = EVOLUTION_INSTANCE_NAME

    # 1. Logout d'abord (necessaire avant delete dans certaines versions)
    try:
        await logout_instance()
        logger.info("[EvolutionAPI] Force reset: logout OK")
    except Exception as e:
        logger.info(f"[EvolutionAPI] Force reset: logout skipped ({e})")

    await asyncio.sleep(1)

    # 2. Supprimer l'instance
    try:
        await delete_instance()
        logger.info("[EvolutionAPI] Force reset: instance supprimee")
    except Exception as e:
        logger.warning(f"[EvolutionAPI] Force reset: delete failed ({e})")
        # Essayer avec un POST au lieu de DELETE (certaines versions)
        try:
            url = f"{_base_url()}/instance/delete/{instance}"
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(url, headers=_headers())
                logger.info(f"[EvolutionAPI] Force reset: POST delete: {r.status_code}")
        except Exception:
            pass

    await asyncio.sleep(2)

    # 3. Recreer l'instance avec webhook
    data = await ensure_instance_exists()
    logger.info(f"[EvolutionAPI] Force reset: instance recreee, keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}")
    return data


# -- Envoi de messages -------------------------------------------------------------

async def send_text(phone: str, text: str) -> dict:
    """Envoie un message texte WhatsApp."""
    number = _phone_for_whatsapp(phone)
    if not number:
        raise ValueError(f"Numero de telephone invalide: {phone}")

    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/message/sendText/{instance}"
    payload = {"number": number, "text": text}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(url, json=payload, headers=_headers())
        r.raise_for_status()
        return r.json()


async def send_media(
    phone: str,
    media_url: str,
    caption: str = "",
    media_type: str = "document",
    file_name: str = "document.pdf",
) -> dict:
    """Envoie un fichier (PDF, image, etc.) via URL."""
    number = _phone_for_whatsapp(phone)
    if not number:
        raise ValueError(f"Numero de telephone invalide: {phone}")

    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/message/sendMedia/{instance}"
    payload = {
        "number": number,
        "mediatype": media_type,
        "media": media_url,
        "caption": caption,
        "fileName": file_name,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(url, json=payload, headers=_headers())
        r.raise_for_status()
        return r.json()


# -- Wrappers pratiques ------------------------------------------------------------

async def send_invoice_whatsapp(
    phone: str, invoice_number: str, client_name: str, total: float, pdf_url: str
) -> dict:
    """Envoie une facture par WhatsApp (PDF + message)."""
    app_name = os.getenv("APP_NAME", "GeekTechnologie")
    text = (
        f"Bonjour {client_name},\n\n"
        f"Veuillez trouver ci-joint votre facture {invoice_number} "
        f"d'un montant de {total:,.0f} F CFA.\n\n"
        f"Cordialement,\n{app_name}"
    )
    return await send_media(
        phone=phone,
        media_url=pdf_url,
        caption=text,
        media_type="document",
        file_name=f"Facture_{invoice_number}.pdf",
    )


async def send_quotation_whatsapp(
    phone: str, quotation_number: str, client_name: str, total: float, pdf_url: str
) -> dict:
    """Envoie un devis par WhatsApp (PDF + message)."""
    app_name = os.getenv("APP_NAME", "GeekTechnologie")
    text = (
        f"Bonjour {client_name},\n\n"
        f"Veuillez trouver ci-joint votre devis {quotation_number} "
        f"d'un montant de {total:,.0f} F CFA.\n\n"
        f"Cordialement,\n{app_name}"
    )
    return await send_media(
        phone=phone,
        media_url=pdf_url,
        caption=text,
        media_type="document",
        file_name=f"Devis_{quotation_number}.pdf",
    )


async def send_maintenance_whatsapp(
    phone: str,
    maintenance_number: str,
    client_name: str,
    device: str,
    pdf_url: str,
    kind: str = "technician",
) -> dict:
    """Envoie un document de maintenance par WhatsApp (PDF + message)."""
    app_name = os.getenv("APP_NAME", "GeekTechnologie")
    kind_labels = {
        "technician": "Fiche technique",
        "client": "Recu client",
        "label": "Etiquette",
        "ticket": "Ticket",
    }
    label = kind_labels.get(kind, "Document")
    text = (
        f"Bonjour {client_name},\n\n"
        f"Veuillez trouver ci-joint le document de maintenance ({label}) "
        f"pour votre appareil {device}.\n\n"
        f"Numero de fiche : {maintenance_number}\n\n"
        f"Cordialement,\n{app_name}"
    )
    return await send_media(
        phone=phone,
        media_url=pdf_url,
        caption=text,
        media_type="document",
        file_name=f"Maintenance_{maintenance_number}.pdf",
    )


async def send_reminder_text(phone: str, message: str) -> dict:
    """Envoie un rappel texte (dette, garantie, recuperation, etc.)."""
    return await send_text(phone=phone, text=message)


# -- Wrapper synchrone pour les notifiers (threads) --------------------------------

def send_text_sync(phone: str, text: str) -> bool:
    """Version synchrone pour les services en background thread.

    Retourne True en cas de succes, False sinon.
    """
    number = _phone_for_whatsapp(phone)
    if not number:
        logger.warning(f"[EvolutionAPI] Numero invalide: {phone}")
        return False

    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/message/sendText/{instance}"
    payload = {"number": number, "text": text}

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(url, json=payload, headers=_headers())
            r.raise_for_status()
            logger.info(f"[EvolutionAPI] Message envoye a {number}")
            return True
    except Exception as e:
        logger.error(f"[EvolutionAPI] Echec envoi a {number}: {e}")
        return False
