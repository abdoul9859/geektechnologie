"""
Router WhatsApp -- QR code, statut, reconnexion.

Utilise Evolution API v2 directement.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import logging
import httpx

from ..services.evolution_api import (
    ensure_instance_exists,
    get_connection_state,
    get_qr_code,
    get_cached_qr,
    store_qr_from_webhook,
    clear_qr_cache,
    restart_instance,
    force_reset_instance,
    _base_url,
    _headers,
    EVOLUTION_INSTANCE_NAME,
    APP_PUBLIC_URL,
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
        # Verifier si deja connecte
        try:
            state_data = await get_connection_state()
            state = _extract_state(state_data)
            logger.info(f"[WhatsApp] QR - connection state: {state}")
            if state == "open":
                return {"status": "already_connected"}
        except Exception:
            # Instance n'existe peut-etre pas encore, get_qr_code() la creera
            pass

        # get_qr_code() gere la creation/recreation de l'instance si necessaire
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


@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    """Webhook appele par Evolution API pour les evenements QR et connexion."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": True}

    event = body.get("event", "").lower()
    data = body.get("data") or body

    logger.info(f"[WhatsApp] Webhook recu: event={event}, keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}")

    if "qrcode" in event or "qr" in event:
        # QRCODE_UPDATED event
        qr_obj = data.get("qrcode") or data
        code = qr_obj.get("code") if isinstance(qr_obj, dict) else None
        base64_img = qr_obj.get("base64") if isinstance(qr_obj, dict) else None
        if code or base64_img:
            store_qr_from_webhook(code=code, base64_img=base64_img)
            logger.info(f"[WhatsApp] QR code recu via webhook (code={bool(code)}, base64={bool(base64_img)})")

    elif "connection" in event:
        # CONNECTION_UPDATE event
        state = (data.get("state") or data.get("status") or "").lower()
        logger.info(f"[WhatsApp] Connection update: {state}")
        if state == "open":
            clear_qr_cache()

    return {"ok": True}


@router.get("/debug")
async def whatsapp_debug():
    """Debug - appels directs a Evolution API avec reponses brutes."""
    base = _base_url()
    hdrs = _headers()
    inst = EVOLUTION_INSTANCE_NAME
    result = {
        "config": {
            "evolution_url": base,
            "instance_name": inst,
            "webhook_url": f"{APP_PUBLIC_URL}/api/whatsapp/webhook",
            "api_key_set": bool(hdrs.get("apikey")),
        }
    }

    def _truncate(obj, max_len=150):
        if isinstance(obj, str) and len(obj) > max_len:
            return obj[:80] + f"... (len={len(obj)})"
        if isinstance(obj, dict):
            return {k: _truncate(v, max_len) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_truncate(v, max_len) for v in obj[:5]]
        return obj

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. fetchInstances
        try:
            r = await client.get(f"{base}/instance/fetchInstances?instanceName={inst}", headers=hdrs)
            result["1_fetchInstances"] = {"status": r.status_code, "body": _truncate(r.json())}
        except Exception as e:
            result["1_fetchInstances"] = {"error": str(e)}

        # 2. connectionState
        try:
            r = await client.get(f"{base}/instance/connectionState/{inst}", headers=hdrs)
            result["2_connectionState"] = {"status": r.status_code, "body": r.json()}
        except Exception as e:
            result["2_connectionState"] = {"error": str(e)}

        # 3. connect (GET)
        try:
            r = await client.get(f"{base}/instance/connect/{inst}", headers=hdrs)
            result["3_connect"] = {"status": r.status_code, "body": _truncate(r.json())}
        except Exception as e:
            result["3_connect"] = {"error": str(e)}

        # 4. webhook find
        try:
            r = await client.get(f"{base}/webhook/find/{inst}", headers=hdrs)
            result["4_webhook_find"] = {"status": r.status_code, "body": _truncate(r.json())}
        except Exception as e:
            result["4_webhook_find"] = {"error": str(e)}

        # 5. Set webhook (fix si null)
        webhook_url = f"{APP_PUBLIC_URL}/api/whatsapp/webhook"
        try:
            # Evolution API v2 attend le payload encapsule dans une cle "webhook"
            wb_payload = {
                "webhook": {
                    "enabled": True,
                    "url": webhook_url,
                    "webhookByEvents": False,
                    "webhookBase64": True,
                    "events": ["QRCODE_UPDATED", "CONNECTION_UPDATE"],
                }
            }
            r = await client.post(f"{base}/webhook/set/{inst}", json=wb_payload, headers=hdrs)
            result["5_webhook_set"] = {"status": r.status_code, "body": _truncate(r.json())}
        except Exception as e:
            result["5_webhook_set"] = {"error": str(e)}

    # 6. QR cache
    cached = get_cached_qr()
    result["6_qr_cache"] = {
        "has_cached_qr": cached is not None,
        "code_present": bool(cached.get("code")) if cached else False,
        "base64_present": bool(cached.get("base64")) if cached else False,
    }

    return result


@router.post("/force-reset")
async def whatsapp_force_reset():
    """Supprime et recree l'instance WhatsApp completement."""
    try:
        data = await force_reset_instance()
        return {"success": True, "message": "Instance recree", "data_keys": list(data.keys()) if isinstance(data, dict) else str(type(data))}
    except Exception as e:
        logger.error(f"[WhatsApp] Erreur force-reset: {e}")
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": str(e)},
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
