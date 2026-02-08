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
        # S'assurer que l'instance existe avant de demander le QR
        await ensure_instance_exists()

        state_data = await get_connection_state()
        state = _extract_state(state_data)
        logger.info(f"[WhatsApp] QR - connection state: {state} (raw: {state_data})")

        if state == "open":
            return {"status": "already_connected"}

        qr_data = await get_qr_code()
        logger.info(f"[WhatsApp] QR - raw response keys: {list(qr_data.keys()) if isinstance(qr_data, dict) else type(qr_data)}")
        logger.info(f"[WhatsApp] QR - raw response: {str(qr_data)[:500]}")

        # Evolution API v2 peut retourner differents formats
        qr_string = qr_data.get("code") or (qr_data.get("qrcode") or {}).get("code")
        qr_base64 = qr_data.get("base64") or (qr_data.get("qrcode") or {}).get("base64")

        if qr_string:
            logger.info(f"[WhatsApp] QR string found (len={len(qr_string)})")
            return {"qr": qr_string}
        elif qr_base64:
            logger.info(f"[WhatsApp] QR base64 found (len={len(qr_base64)})")
            return {"qr": qr_base64, "format": "base64"}
        else:
            logger.warning(f"[WhatsApp] No QR found in response: {qr_data}")
            return {"qr": None, "status": "waiting", "debug_keys": list(qr_data.keys()) if isinstance(qr_data, dict) else str(type(qr_data))}

    except Exception as e:
        logger.error(f"[WhatsApp] Erreur QR: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "qr": None, "error": str(e)},
        )


@router.get("/debug")
async def whatsapp_debug():
    """Debug endpoint - retourne les reponses brutes de Evolution API."""
    result = {}
    try:
        instance_data = await ensure_instance_exists()
        result["instance"] = instance_data
    except Exception as e:
        result["instance_error"] = str(e)

    try:
        state_data = await get_connection_state()
        result["connection_state"] = state_data
    except Exception as e:
        result["connection_state_error"] = str(e)

    try:
        qr_data = await get_qr_code()
        # Tronquer base64 pour lisibilite
        if isinstance(qr_data, dict):
            debug_qr = {}
            for k, v in qr_data.items():
                if isinstance(v, str) and len(v) > 200:
                    debug_qr[k] = v[:100] + f"... (len={len(v)})"
                elif isinstance(v, dict):
                    debug_qr[k] = {}
                    for k2, v2 in v.items():
                        if isinstance(v2, str) and len(v2) > 200:
                            debug_qr[k][k2] = v2[:100] + f"... (len={len(v2)})"
                        else:
                            debug_qr[k][k2] = v2
                else:
                    debug_qr[k] = v
            result["qr_data"] = debug_qr
        else:
            result["qr_data"] = str(qr_data)[:500]
    except Exception as e:
        result["qr_error"] = str(e)

    return result


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
