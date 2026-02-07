from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from typing import Dict, Any, Optional, List
import json
from datetime import datetime

from ..database import UserSettings, ScanHistory, AppCache, get_next_id
from ..auth import get_current_user

router = APIRouter(prefix="/api/user-settings", tags=["user-settings"])

# ==================== USER SETTINGS ====================


@router.get("/")
async def get_user_settings(
    current_user=Depends(get_current_user),
):
    """Recuperer tous les parametres de l'utilisateur connecte"""
    settings = await UserSettings.find(
        UserSettings.user_id == current_user.user_id
    ).to_list()

    result = {}
    for setting in settings:
        try:
            result[setting.setting_key] = json.loads(setting.setting_value)
        except Exception:
            result[setting.setting_key] = setting.setting_value

    return {"data": result}


@router.get("/{setting_key}")
async def get_user_setting(
    setting_key: str,
    current_user=Depends(get_current_user),
):
    """Recuperer un parametre specifique de l'utilisateur"""
    setting = await UserSettings.find_one(
        {
            "user_id": current_user.user_id,
            "setting_key": setting_key,
        }
    )

    if not setting:
        return {"data": None}

    try:
        value = json.loads(setting.setting_value)
    except Exception:
        value = setting.setting_value

    return {"data": value}


@router.post("/{setting_key}")
async def save_user_setting(
    setting_key: str,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Sauvegarder un parametre utilisateur."""
    try:
        try:
            payload = await request.json()
        except Exception:
            payload = None

        if isinstance(payload, dict) and "value" in payload:
            value = payload.get("value")
        else:
            value = payload

        if isinstance(value, (dict, list)):
            setting_value = json.dumps(value, ensure_ascii=False)
        elif value is None:
            setting_value = "null"
        else:
            setting_value = str(value)

        existing_setting = await UserSettings.find_one(
            {
                "user_id": current_user.user_id,
                "setting_key": setting_key,
            }
        )

        if existing_setting:
            existing_setting.setting_value = setting_value
            existing_setting.updated_at = datetime.now()
            await existing_setting.save()
        else:
            new_id = await get_next_id("user_settings")
            new_setting = UserSettings(
                setting_id=new_id,
                user_id=current_user.user_id,
                setting_key=setting_key,
                setting_value=setting_value,
            )
            await new_setting.insert()

        return {"message": "Parametre sauvegarde avec succes"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{setting_key}")
async def delete_user_setting(
    setting_key: str,
    current_user=Depends(get_current_user),
):
    """Supprimer un parametre utilisateur"""
    setting = await UserSettings.find_one(
        {
            "user_id": current_user.user_id,
            "setting_key": setting_key,
        }
    )

    if setting:
        await setting.delete()
        return {"message": "Parametre supprime avec succes"}

    raise HTTPException(status_code=404, detail="Parametre non trouve")


# ==================== APP SETTINGS HELPERS ====================


@router.get("/invoice/payment-methods")
async def get_invoice_payment_methods(
    current_user=Depends(get_current_user),
):
    """Retourne la liste des methodes de paiement au niveau application."""
    try:
        methods: List[str] = []

        # 1) Clef dediee - chercher d'abord pour l'utilisateur, puis global (user_id None)
        setting_user = await UserSettings.find_one(
            {
                "setting_key": "INVOICE_PAYMENT_METHODS",
                "user_id": current_user.user_id,
            }
        )
        setting_global = await UserSettings.find_one(
            {
                "setting_key": "INVOICE_PAYMENT_METHODS",
                "user_id": None,
            }
        )
        setting = setting_user or setting_global
        if setting and setting.setting_value:
            try:
                data = json.loads(setting.setting_value)
                if isinstance(data, list):
                    methods = [str(x).strip() for x in data if str(x).strip()]
            except Exception:
                pass

        # 2) Fallback ancien stockage
        if not methods:
            legacy = await UserSettings.find_one(
                {
                    "setting_key": "appSettings",
                    "user_id": current_user.user_id,
                }
            )
            if legacy and legacy.setting_value:
                try:
                    data = json.loads(legacy.setting_value)
                except Exception:
                    data = {}
                raw = (data or {}).get("invoice", {}).get("invoicePaymentMethods")
                if isinstance(raw, list):
                    methods = [str(x).strip() for x in raw if str(x).strip()]
                elif isinstance(raw, str):
                    methods = [s.strip() for s in raw.splitlines() if s.strip()]

        if not methods:
            methods = [
                "Especes",
                "Virement bancaire",
                "Mobile Money",
                "Cheque",
                "Carte bancaire",
            ]
        return {"data": methods}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/invoice/payment-methods")
async def set_invoice_payment_methods(
    payload: Dict[str, Any],
    current_user=Depends(get_current_user),
):
    """Enregistre la liste des methodes au niveau application."""
    try:
        raw = payload.get("methods")
        if isinstance(raw, str):
            methods: List[str] = [s.strip() for s in raw.splitlines() if s.strip()]
        elif isinstance(raw, list):
            methods = [str(s).strip() for s in raw if str(s).strip()]
        else:
            methods = []

        setting = await UserSettings.find_one(
            {
                "setting_key": "INVOICE_PAYMENT_METHODS",
                "user_id": current_user.user_id,
            }
        )

        if setting:
            setting.setting_value = json.dumps(methods)
            setting.updated_at = datetime.now()
            await setting.save()
        else:
            new_id = await get_next_id("user_settings")
            setting = UserSettings(
                setting_id=new_id,
                user_id=current_user.user_id,
                setting_key="INVOICE_PAYMENT_METHODS",
                setting_value=json.dumps(methods),
            )
            await setting.insert()

        return {"message": "Methodes enregistrees", "data": methods}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== SCAN HISTORY ====================


@router.get("/scan-history")
async def get_scan_history(
    limit: int = 50,
    current_user=Depends(get_current_user),
):
    """Recuperer l'historique des scans de l'utilisateur"""
    scans = (
        await ScanHistory.find(ScanHistory.user_id == current_user.user_id)
        .sort(-ScanHistory.scanned_at)
        .limit(limit)
        .to_list()
    )

    result = []
    for scan in scans:
        scan_data = {
            "scan_id": scan.scan_id,
            "barcode": scan.barcode,
            "product_name": scan.product_name,
            "scan_type": scan.scan_type,
            "scanned_at": scan.scanned_at.isoformat(),
        }

        if scan.result_data:
            try:
                scan_data["result_data"] = json.loads(scan.result_data)
            except Exception:
                scan_data["result_data"] = scan.result_data

        result.append(scan_data)

    return {"data": result}


@router.post("/scan-history")
async def add_scan_history(
    scan_data: Dict[str, Any],
    current_user=Depends(get_current_user),
):
    """Ajouter un scan a l'historique"""
    result_data = scan_data.get("result_data")
    if isinstance(result_data, (dict, list)):
        result_data = json.dumps(result_data)

    new_id = await get_next_id("scan_history")
    new_scan = ScanHistory(
        scan_id=new_id,
        user_id=current_user.user_id,
        barcode=scan_data.get("barcode"),
        product_name=scan_data.get("product_name"),
        scan_type=scan_data.get("scan_type", "product"),
        result_data=result_data,
    )

    await new_scan.insert()
    return {"message": "Scan ajoute a l'historique"}


@router.delete("/scan-history")
async def clear_scan_history(
    current_user=Depends(get_current_user),
):
    """Vider l'historique des scans"""
    await ScanHistory.find(ScanHistory.user_id == current_user.user_id).delete()
    return {"message": "Historique des scans vide"}


# ==================== APP CACHE ====================


@router.get("/cache/{cache_key}")
async def get_cache_value(
    cache_key: str,
    current_user=Depends(get_current_user),
):
    """Recuperer une valeur du cache"""
    cache_item = await AppCache.find_one(AppCache.cache_key == cache_key)

    if not cache_item:
        return {"data": None}

    # Verifier l'expiration
    if cache_item.expires_at and cache_item.expires_at < datetime.now():
        await cache_item.delete()
        return {"data": None}

    try:
        value = json.loads(cache_item.cache_value)
    except Exception:
        value = cache_item.cache_value

    return {"data": value}


@router.post("/cache/{cache_key}")
async def set_cache_value(
    cache_key: str,
    cache_data: Dict[str, Any],
    current_user=Depends(get_current_user),
):
    """Definir une valeur dans le cache"""
    value = cache_data.get("value")
    expires_in_hours = cache_data.get("expires_in_hours", 24)

    if isinstance(value, (dict, list)):
        cache_value = json.dumps(value)
    else:
        cache_value = str(value)

    from datetime import timedelta

    expires_at = datetime.now()
    if expires_in_hours:
        expires_at += timedelta(hours=expires_in_hours)

    existing_cache = await AppCache.find_one(AppCache.cache_key == cache_key)

    if existing_cache:
        existing_cache.cache_value = cache_value
        existing_cache.expires_at = expires_at
        existing_cache.updated_at = datetime.now()
        await existing_cache.save()
    else:
        new_id = await get_next_id("app_cache")
        new_cache = AppCache(
            cache_id=new_id,
            cache_key=cache_key,
            cache_value=cache_value,
            expires_at=expires_at,
        )
        await new_cache.insert()

    return {"message": "Valeur mise en cache"}


@router.delete("/cache/{cache_key}")
async def delete_cache_value(
    cache_key: str,
    current_user=Depends(get_current_user),
):
    """Supprimer une valeur du cache"""
    cache_item = await AppCache.find_one(AppCache.cache_key == cache_key)

    if cache_item:
        await cache_item.delete()
        return {"message": "Valeur supprimee du cache"}

    raise HTTPException(status_code=404, detail="Cle de cache non trouvee")


# ==================== WALLPAPER UPLOAD ====================


@router.post("/upload/wallpaper")
async def upload_wallpaper(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """Upload a wallpaper image under static/uploads/wallpapers and return its public URL."""
    import os
    import re
    import time
    from pathlib import Path

    allowed = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/svg+xml"}
    if (file.content_type or "").lower() not in allowed:
        raise HTTPException(status_code=400, detail="Type de fichier non supporte")

    uploads_dir = Path("static/uploads/wallpapers")
    try:
        uploads_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    original = file.filename or "wallpaper"
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", original)
    ts = int(time.time())
    if "." in safe:
        name, ext = safe.rsplit(".", 1)
        out_name = f"wp_{current_user.user_id}_{ts}.{ext}"
    else:
        out_name = f"wp_{current_user.user_id}_{ts}"

    out_path = uploads_dir / out_name
    try:
        with out_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'envoi: {e}")

    public_url = f"/static/uploads/wallpapers/{out_name}"

    # Persist as the current user's app setting (theme.desktopBackground)
    try:
        setting = await UserSettings.find_one(
            {
                "user_id": current_user.user_id,
                "setting_key": "appSettings",
            }
        )
        payload = {}
        if setting and setting.setting_value:
            try:
                payload = json.loads(setting.setting_value) or {}
            except Exception:
                payload = {}
        theme = payload.get("theme") or {}
        theme["desktopBackground"] = public_url
        payload["theme"] = theme
        serialized = json.dumps(payload, ensure_ascii=False)
        if setting:
            setting.setting_value = serialized
            setting.updated_at = datetime.now()
            await setting.save()
        else:
            new_id = await get_next_id("user_settings")
            setting = UserSettings(
                setting_id=new_id,
                user_id=current_user.user_id,
                setting_key="appSettings",
                setting_value=serialized,
            )
            await setting.insert()
    except Exception:
        pass

    return {"url": public_url}


@router.post("/wallpaper/reset")
async def reset_wallpaper(
    current_user=Depends(get_current_user),
):
    """Reset the desktop background setting for the current user."""
    setting = await UserSettings.find_one(
        {
            "user_id": current_user.user_id,
            "setting_key": "appSettings",
        }
    )
    if setting and setting.setting_value:
        try:
            payload = json.loads(setting.setting_value) or {}
            if isinstance(payload.get("theme"), dict) and "desktopBackground" in payload["theme"]:
                payload["theme"].pop("desktopBackground", None)
                setting.setting_value = json.dumps(payload, ensure_ascii=False)
                setting.updated_at = datetime.now()
                await setting.save()
        except Exception:
            pass

    # Also remove legacy key if present
    legacy = await UserSettings.find_one(
        {
            "user_id": current_user.user_id,
            "setting_key": "DESKTOP_BACKGROUND",
        }
    )
    if legacy:
        await legacy.delete()

    return {"message": "Fond d'ecran reinitialise"}


# ==================== FAVICON UPLOAD ====================


@router.post("/upload/favicon")
async def upload_favicon(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """Upload a favicon image and return its public URL."""
    import os
    import re
    import time
    from pathlib import Path

    allowed = {"image/x-icon", "image/vnd.microsoft.icon", "image/png", "image/svg+xml"}
    if (file.content_type or "").lower() not in allowed:
        raise HTTPException(
            status_code=400,
            detail="Type de fichier non supporte. Utilisez ICO, PNG ou SVG",
        )

    uploads_dir = Path("static/uploads/favicons")
    try:
        uploads_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    original = file.filename or "favicon"
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", original)
    ts = int(time.time())
    if "." in safe:
        name, ext = safe.rsplit(".", 1)
        out_name = f"favicon_{current_user.user_id}_{ts}.{ext}"
    else:
        out_name = f"favicon_{current_user.user_id}_{ts}"

    out_path = uploads_dir / out_name
    try:
        with out_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'envoi: {e}")

    public_url = f"/static/uploads/favicons/{out_name}"

    # Persist as the current user's app setting (general.faviconUrl)
    try:
        setting = await UserSettings.find_one(
            {
                "user_id": current_user.user_id,
                "setting_key": "appSettings",
            }
        )
        payload = {}
        if setting and setting.setting_value:
            try:
                payload = json.loads(setting.setting_value) or {}
            except Exception:
                payload = {}
        general = payload.get("general") or {}
        general["faviconUrl"] = public_url
        payload["general"] = general
        serialized = json.dumps(payload, ensure_ascii=False)
        if setting:
            setting.setting_value = serialized
            setting.updated_at = datetime.now()
            await setting.save()
        else:
            new_id = await get_next_id("user_settings")
            setting = UserSettings(
                setting_id=new_id,
                user_id=current_user.user_id,
                setting_key="appSettings",
                setting_value=serialized,
            )
            await setting.insert()
    except Exception:
        pass

    return {"url": public_url}


@router.post("/favicon/reset")
async def reset_favicon(
    current_user=Depends(get_current_user),
):
    """Reset the favicon setting for the current user."""
    setting = await UserSettings.find_one(
        {
            "user_id": current_user.user_id,
            "setting_key": "appSettings",
        }
    )
    if setting and setting.setting_value:
        try:
            payload = json.loads(setting.setting_value) or {}
            if isinstance(payload.get("general"), dict) and "faviconUrl" in payload["general"]:
                payload["general"].pop("faviconUrl", None)
                setting.setting_value = json.dumps(payload, ensure_ascii=False)
                setting.updated_at = datetime.now()
                await setting.save()
        except Exception:
            pass

    return {"message": "Favicon reinitialise"}
