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
_instance_created_at: float = 0  # timestamp de derniere creation/reset
_QR_PATIENCE_SECONDS = 45  # patience apres creation avant de restart
_QR_RESET_MIN_INTERVAL = 30  # secondes minimum entre deux restarts


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
    global _instance_created_at
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
            _instance_created_at = time.time()
            # Log complet pour debug
            data_keys = list(data.keys()) if isinstance(data, dict) else str(type(data))
            logger.info(f"[EvolutionAPI] Instance '{instance}' creee, keys={data_keys}")
            logger.info(f"[EvolutionAPI] Create response (500 chars): {str(data)[:500]}")
            # Extraire le QR de la reponse de creation si present
            _store_qr_from_data(data)
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
    # Evolution API v2 attend le payload encapsule dans une cle "webhook"
    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": False,
            "webhookBase64": True,
            "events": [
                "QRCODE_UPDATED",
                "CONNECTION_UPDATE",
            ],
        }
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
    if data.get("pairingCode"):
        return True
    qrcode = data.get("qrcode")
    if isinstance(qrcode, dict) and (qrcode.get("code") or qrcode.get("base64")):
        return True
    return False


def _store_qr_from_data(data: dict) -> bool:
    """Extrait et stocke le QR code depuis une reponse API. Retourne True si QR trouve."""
    if not isinstance(data, dict):
        return False
    # Chercher QR a differents niveaux de la reponse
    code = data.get("code")
    base64_img = data.get("base64")
    qrcode = data.get("qrcode")
    if isinstance(qrcode, dict):
        code = code or qrcode.get("code")
        base64_img = base64_img or qrcode.get("base64")
    if code or base64_img:
        store_qr_from_webhook(code=code, base64_img=base64_img)
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
    2. Essayer /instance/connect/ avec retries (le QR met du temps a se generer)
    3. Si pas de QR apres patience, redemarrer l'instance (dernier recours)
    """
    global _last_qr_reset, _instance_created_at

    # 1. Verifier le cache webhook d'abord
    cached = get_cached_qr()
    if cached:
        logger.debug("[EvolutionAPI] QR depuis cache webhook")
        return {"code": cached["code"], "base64": cached["base64"]}

    # 2. Essayer /instance/connect/ avec retries
    for attempt in range(3):
        try:
            data = await _connect_instance()
            logger.info(
                f"[EvolutionAPI] connect attempt {attempt + 1}: "
                f"keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}, "
                f"count={data.get('count', 'N/A')}, "
                f"has_code={bool(data.get('code'))}, has_base64={bool(data.get('base64'))}"
            )

            if _has_qr(data):
                _store_qr_from_data(data)
                return data
        except Exception as e:
            logger.warning(f"[EvolutionAPI] connect attempt {attempt + 1} failed: {e}")

        # Verifier le cache (le webhook a peut-etre livre le QR pendant l'attente)
        cached = get_cached_qr()
        if cached:
            return {"code": cached["code"], "base64": cached["base64"]}

        # Attendre avant le prochain essai (sauf dernier)
        if attempt < 2:
            await asyncio.sleep(3)

    # 3. Toujours pas de QR -- evaluer si on doit redemarrer
    now = time.time()
    time_since_creation = now - _instance_created_at if _instance_created_at else 999
    time_since_reset = now - _last_qr_reset if _last_qr_reset else 999

    # Ne pas restart si l'instance est recente -- le QR va arriver
    if time_since_creation < _QR_PATIENCE_SECONDS:
        logger.info(
            f"[EvolutionAPI] Instance creee il y a {time_since_creation:.0f}s "
            f"(patience {_QR_PATIENCE_SECONDS}s), pas de restart"
        )
        return {}

    # Ne pas restart trop souvent
    if time_since_reset < _QR_RESET_MIN_INTERVAL:
        logger.info(
            f"[EvolutionAPI] Dernier restart il y a {time_since_reset:.0f}s, attente..."
        )
        return {}

    # 4. Restart de l'instance (dernier recours)
    _last_qr_reset = now
    clear_qr_cache()
    logger.info("[EvolutionAPI] Pas de QR apres retries, redemarrage de l'instance...")

    try:
        await _restart_instance_api()
    except Exception:
        try:
            await logout_instance()
        except Exception:
            pass

    _instance_created_at = time.time()
    await asyncio.sleep(5)

    # Un dernier essai apres restart
    try:
        data = await _connect_instance()
        if _has_qr(data):
            _store_qr_from_data(data)
            return data
    except Exception:
        pass

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
        # Evolution API v2 utilise POST pour logout (pas DELETE)
        r = await client.post(url, headers=_headers())
        r.raise_for_status()
        return r.json()


async def restart_instance() -> dict:
    """Redemarre l'instance pour generer un nouveau QR code."""
    global _last_qr_reset, _instance_created_at
    _last_qr_reset = 0
    _instance_created_at = time.time()
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
    Attend activement le QR apres recreation.
    """
    global _last_qr_reset, _instance_created_at
    _last_qr_reset = 0
    _instance_created_at = 0
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
    logger.info(
        f"[EvolutionAPI] Force reset: instance recreee, "
        f"keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}"
    )

    # 4. Attendre activement le QR (connect avec retries)
    for attempt in range(5):
        await asyncio.sleep(3)

        # Verifier le cache webhook d'abord
        cached = get_cached_qr()
        if cached:
            logger.info(f"[EvolutionAPI] Force reset: QR recu via webhook (attempt {attempt + 1})")
            return {"qr_source": "webhook", **data}

        # Essayer connect
        try:
            connect_data = await _connect_instance()
            logger.info(
                f"[EvolutionAPI] Force reset connect attempt {attempt + 1}: "
                f"count={connect_data.get('count', 'N/A')}, "
                f"has_code={bool(connect_data.get('code'))}, "
                f"has_base64={bool(connect_data.get('base64'))}, "
                f"full={str(connect_data)[:300]}"
            )
            if _has_qr(connect_data):
                _store_qr_from_data(connect_data)
                return {"qr_source": "connect", **connect_data}
        except Exception as e:
            logger.warning(f"[EvolutionAPI] Force reset connect {attempt + 1} failed: {e}")

    logger.warning("[EvolutionAPI] Force reset: QR non obtenu apres 5 tentatives")
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
