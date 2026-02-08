"""
Router WhatsApp -- QR code, statut, reconnexion.

Utilise Evolution API v2 directement.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
import logging

from ..services.evolution_api import (
    ensure_instance_exists,
    get_connection_state,
    get_qr_code,
    restart_instance,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


def _extract_state(data: dict) -> str:
    """Extrait l'etat de connexion depuis la reponse Evolution API."""
    state = (data.get("state") or "").lower()
    if not state:
        state = (data.get("instance", {}).get("state") or "").lower()
    return state


@router.get("/status")
async def whatsapp_status():
    """Statut de la connexion WhatsApp.

    Retourne {"status": "ready"} si connecte,
    {"status": "not_ready", "state": "..."} sinon.
    """
    try:
        await ensure_instance_exists()
        data = await get_connection_state()
        state = _extract_state(data)

        if state == "open":
            return {"status": "ready", "state": "open"}
        return {"status": "not_ready", "state": state}

    except Exception as e:
        logger.error(f"[WhatsApp] Erreur status: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "qrCode": False, "error": str(e)},
        )


@router.get("/qr")
async def whatsapp_qr():
    """QR code pour scanner avec WhatsApp.

    Retourne {"qr": "<string>"} ou {"qr": "<base64>", "format": "base64"},
    ou {"status": "already_connected"} si deja connecte.
    """
    try:
        state_data = await get_connection_state()
        state = _extract_state(state_data)

        if state == "open":
            return {"status": "already_connected"}

        qr_data = await get_qr_code()

        # Evolution API v2 peut retourner differents formats
        qr_string = qr_data.get("code") or (qr_data.get("qrcode") or {}).get("code")
        qr_base64 = qr_data.get("base64") or (qr_data.get("qrcode") or {}).get("base64")

        if qr_string:
            return {"qr": qr_string}
        elif qr_base64:
            return {"qr": qr_base64, "format": "base64"}
        else:
            return {"qr": None, "status": "waiting"}

    except Exception as e:
        logger.error(f"[WhatsApp] Erreur QR: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "qr": None, "error": str(e)},
        )


@router.post("/restart")
async def whatsapp_restart():
    """Deconnecte WhatsApp et prepare un nouveau QR code."""
    try:
        await restart_instance()
        return {"success": True, "message": "WhatsApp deconnecte, nouveau QR en cours"}
    except Exception as e:
        logger.error(f"[WhatsApp] Erreur restart: {e}")
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": str(e)},
        )
