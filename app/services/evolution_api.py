"""
Service centralise WhatsApp via Evolution API v2.

Tous les appels WhatsApp (QR, status, envoi texte, envoi media) passent par ce module.
Appels directs a Evolution API -- pas d'intermediaire n8n.
"""

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

# Cache pour eviter de recreer l'instance trop souvent
_last_qr_recreate: float = 0
_QR_RECREATE_MIN_INTERVAL = 8  # secondes entre deux recreations


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


# -- Gestion d'instance -----------------------------------------------------------

async def ensure_instance_exists() -> dict:
    """Cree l'instance si elle n'existe pas. Idempotent."""
    instance = EVOLUTION_INSTANCE_NAME
    url = f"{_base_url()}/instance/create"
    payload = {
        "instanceName": instance,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            r = await client.post(url, json=payload, headers=_headers())
            r.raise_for_status()
            logger.info(f"[EvolutionAPI] Instance '{instance}' creee")
            return r.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (409, 403):
                logger.debug(f"[EvolutionAPI] Instance '{instance}' existe deja")
                return {"instanceName": instance, "status": "exists"}
            raise


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


async def get_qr_code() -> dict:
    """Recupere le QR code pour scanner.

    Evolution API v2 retourne le QR uniquement a la creation de l'instance.
    Les appels suivants a /instance/connect/ retournent {"count": N}.
    Si pas de QR, on supprime et recree l'instance pour forcer la generation.
    """
    global _last_qr_recreate
    instance = EVOLUTION_INSTANCE_NAME

    # 1. Essayer /instance/connect/ d'abord
    url = f"{_base_url()}/instance/connect/{instance}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=_headers())
        r.raise_for_status()
        data = r.json()

    logger.info(f"[EvolutionAPI] connect response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")

    if _has_qr(data):
        return data

    # 2. Pas de QR -- supprimer et recreer l'instance
    now = time.time()
    if (now - _last_qr_recreate) < _QR_RECREATE_MIN_INTERVAL:
        logger.debug("[EvolutionAPI] Trop tot pour recreer l'instance, retour cache vide")
        return data

    _last_qr_recreate = now
    logger.info("[EvolutionAPI] Pas de QR dans la reponse, suppression + recreation de l'instance...")

    try:
        await delete_instance()
    except Exception as e:
        logger.warning(f"[EvolutionAPI] Erreur suppression instance: {e}")

    # Recreer l'instance avec qrcode=true
    create_url = f"{_base_url()}/instance/create"
    payload = {
        "instanceName": instance,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(create_url, json=payload, headers=_headers())
        r.raise_for_status()
        create_data = r.json()

    logger.info(f"[EvolutionAPI] Instance recreee, reponse keys: {list(create_data.keys()) if isinstance(create_data, dict) else type(create_data)}")

    # Le QR peut etre dans la reponse de creation directement ou dans un sous-objet
    if _has_qr(create_data):
        return create_data

    # Parfois le QR est dans create_data["qrcode"] ou create_data nested
    if isinstance(create_data, dict):
        for key in ("qrcode", "qr"):
            nested = create_data.get(key)
            if isinstance(nested, dict) and _has_qr(nested):
                return nested

    return create_data


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
    """Supprime et recree l'instance pour generer un nouveau QR code."""
    global _last_qr_recreate
    _last_qr_recreate = 0  # Reset le timer pour forcer la recreation
    try:
        await delete_instance()
    except Exception:
        pass
    return await ensure_instance_exists()


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
