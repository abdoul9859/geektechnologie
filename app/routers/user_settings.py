from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import Dict, Any, Optional, List
import json
from datetime import datetime

from ..database import get_db, UserSettings, ScanHistory, AppCache
from ..auth import get_current_user
from ..schemas import UserResponse

router = APIRouter(prefix="/api/user-settings", tags=["user-settings"])

# ==================== USER SETTINGS ====================

@router.get("/")
async def get_user_settings(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer tous les paramètres de l'utilisateur connecté"""
    settings = db.query(UserSettings).filter(
        UserSettings.user_id == current_user.user_id
    ).all()
    
    result = {}
    for setting in settings:
        try:
            result[setting.setting_key] = json.loads(setting.setting_value)
        except:
            result[setting.setting_key] = setting.setting_value
    
    return {"data": result}

@router.get("/{setting_key}")
async def get_user_setting(
    setting_key: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer un paramètre spécifique de l'utilisateur"""
    setting = db.query(UserSettings).filter(
        and_(
            UserSettings.user_id == current_user.user_id,
            UserSettings.setting_key == setting_key
        )
    ).first()
    
    if not setting:
        return {"data": None}
    
    try:
        value = json.loads(setting.setting_value)
    except:
        value = setting.setting_value
    
    return {"data": value}

@router.post("/{setting_key}")
async def save_user_setting(
    setting_key: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Sauvegarder un paramètre utilisateur.
    Tolère deux formats de payload:
      1) { "value": ... } (format actuel côté frontend)
      2) valeur brute (objet JSON, tableau, chaîne, nombre)
    """
    try:
        try:
            payload = await request.json()
        except Exception:
            payload = None

        if isinstance(payload, dict) and "value" in payload:
            value = payload.get("value")
        else:
            value = payload

        # Convertir en JSON si nécessaire
        if isinstance(value, (dict, list)):
            setting_value = json.dumps(value, ensure_ascii=False)
        elif value is None:
            # Représenter explicitement l'absence de valeur
            setting_value = "null"
        else:
            setting_value = str(value)

        # Vérifier si le paramètre existe déjà
        existing_setting = db.query(UserSettings).filter(
            and_(
                UserSettings.user_id == current_user.user_id,
                UserSettings.setting_key == setting_key
            )
        ).first()

        if existing_setting:
            # Mettre à jour
            existing_setting.setting_value = setting_value
            existing_setting.updated_at = datetime.now()
        else:
            # Créer nouveau
            new_setting = UserSettings(
                user_id=current_user.user_id,
                setting_key=setting_key,
                setting_value=setting_value
            )
            db.add(new_setting)

        db.commit()
        return {"message": "Paramètre sauvegardé avec succès"}
    except Exception as e:
        # En cas d'erreur inattendue, rollback et retourner 400 plutôt que 422
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{setting_key}")
async def delete_user_setting(
    setting_key: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer un paramètre utilisateur"""
    setting = db.query(UserSettings).filter(
        and_(
            UserSettings.user_id == current_user.user_id,
            UserSettings.setting_key == setting_key
        )
    ).first()
    
    if setting:
        db.delete(setting)
        db.commit()
        return {"message": "Paramètre supprimé avec succès"}
    
    raise HTTPException(status_code=404, detail="Paramètre non trouvé")

# ==================== APP SETTINGS HELPERS ====================

@router.get("/invoice/payment-methods")
async def get_invoice_payment_methods(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Retourne la liste des méthodes de paiement au niveau application.
    Ordre de lecture:
      1) `INVOICE_PAYMENT_METHODS` (format JSON array)
      2) rétrocompatibilité: `appSettings.invoice.invoicePaymentMethods`
    """
    try:
        methods: List[str] = []

        # 1) Clef dédiée
        # Chercher d'abord pour l'utilisateur, puis global (user_id NULL)
        setting_user = (
            db.query(UserSettings)
            .filter(
                and_(
                    UserSettings.setting_key == 'INVOICE_PAYMENT_METHODS',
                    UserSettings.user_id == current_user.user_id
                )
            ).first()
        )
        setting_global = (
            db.query(UserSettings)
            .filter(
                and_(
                    UserSettings.setting_key == 'INVOICE_PAYMENT_METHODS',
                    UserSettings.user_id.is_(None)
                )
            ).first()
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
            legacy = (
                db.query(UserSettings)
                .filter(
                    and_(
                        UserSettings.setting_key == 'appSettings',
                        UserSettings.user_id == current_user.user_id
                    )
                )
                .order_by(UserSettings.updated_at.desc())
                .first()
            )
            if legacy and legacy.setting_value:
                try:
                    data = json.loads(legacy.setting_value)
                except Exception:
                    data = {}
                raw = (data or {}).get('invoice', {}).get('invoicePaymentMethods')
                if isinstance(raw, list):
                    methods = [str(x).strip() for x in raw if str(x).strip()]
                elif isinstance(raw, str):
                    methods = [s.strip() for s in raw.splitlines() if s.strip()]

        if not methods:
            methods = ["Espèces", "Virement bancaire", "Mobile Money", "Chèque", "Carte bancaire"]
        return {"data": methods}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/invoice/payment-methods")
async def set_invoice_payment_methods(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Enregistre la liste des méthodes au niveau application dans la clef `INVOICE_PAYMENT_METHODS`.
    Stockage canonique: JSON array (ordre conservé).
    """
    try:
        raw = payload.get("methods")
        if isinstance(raw, str):
            methods: List[str] = [s.strip() for s in raw.splitlines() if s.strip()]
        elif isinstance(raw, list):
            methods = [str(s).strip() for s in raw if str(s).strip()]
        else:
            methods = []

        # Upsert sur la clef dédiée (global, pas lié à un user spécifique)
        setting = (
            db.query(UserSettings)
            .filter(
                and_(
                    UserSettings.setting_key == 'INVOICE_PAYMENT_METHODS',
                    UserSettings.user_id == current_user.user_id
                )
            )
            .order_by(UserSettings.updated_at.desc())
            .first()
        )

        if setting:
            setting.setting_value = json.dumps(methods)
            setting.updated_at = datetime.now()
        else:
            setting = UserSettings(
                user_id=current_user.user_id,
                setting_key='INVOICE_PAYMENT_METHODS',
                setting_value=json.dumps(methods)
            )
            db.add(setting)

        db.commit()
        return {"message": "Méthodes enregistrées", "data": methods}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# ==================== SCAN HISTORY ====================

@router.get("/scan-history")
async def get_scan_history(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer l'historique des scans de l'utilisateur"""
    scans = db.query(ScanHistory).filter(
        ScanHistory.user_id == current_user.user_id
    ).order_by(ScanHistory.scanned_at.desc()).limit(limit).all()
    
    result = []
    for scan in scans:
        scan_data = {
            "scan_id": scan.scan_id,
            "barcode": scan.barcode,
            "product_name": scan.product_name,
            "scan_type": scan.scan_type,
            "scanned_at": scan.scanned_at.isoformat()
        }
        
        if scan.result_data:
            try:
                scan_data["result_data"] = json.loads(scan.result_data)
            except:
                scan_data["result_data"] = scan.result_data
        
        result.append(scan_data)
    
    return {"data": result}

@router.post("/scan-history")
async def add_scan_history(
    scan_data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Ajouter un scan à l'historique"""
    result_data = scan_data.get("result_data")
    if isinstance(result_data, (dict, list)):
        result_data = json.dumps(result_data)
    
    new_scan = ScanHistory(
        user_id=current_user.user_id,
        barcode=scan_data.get("barcode"),
        product_name=scan_data.get("product_name"),
        scan_type=scan_data.get("scan_type", "product"),
        result_data=result_data
    )
    
    db.add(new_scan)
    db.commit()
    return {"message": "Scan ajouté à l'historique"}

@router.delete("/scan-history")
async def clear_scan_history(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Vider l'historique des scans"""
    db.query(ScanHistory).filter(
        ScanHistory.user_id == current_user.user_id
    ).delete()
    db.commit()
    return {"message": "Historique des scans vidé"}

# ==================== APP CACHE ====================

@router.get("/cache/{cache_key}")
async def get_cache_value(
    cache_key: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer une valeur du cache"""
    cache_item = db.query(AppCache).filter(
        AppCache.cache_key == cache_key
    ).first()
    
    if not cache_item:
        return {"data": None}
    
    # Vérifier l'expiration
    if cache_item.expires_at and cache_item.expires_at < datetime.now():
        db.delete(cache_item)
        db.commit()
        return {"data": None}
    
    try:
        value = json.loads(cache_item.cache_value)
    except:
        value = cache_item.cache_value
    
    return {"data": value}

@router.post("/cache/{cache_key}")
async def set_cache_value(
    cache_key: str,
    cache_data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Définir une valeur dans le cache"""
    value = cache_data.get("value")
    expires_in_hours = cache_data.get("expires_in_hours", 24)
    
    # Convertir en JSON si nécessaire
    if isinstance(value, (dict, list)):
        cache_value = json.dumps(value)
    else:
        cache_value = str(value)
    
    # Calculer la date d'expiration
    expires_at = datetime.now()
    if expires_in_hours:
        from datetime import timedelta
        expires_at += timedelta(hours=expires_in_hours)
    
    # Vérifier si la clé existe déjà
    existing_cache = db.query(AppCache).filter(
        AppCache.cache_key == cache_key
    ).first()
    
    if existing_cache:
        # Mettre à jour
        existing_cache.cache_value = cache_value
        existing_cache.expires_at = expires_at
        existing_cache.updated_at = datetime.now()
    else:
        # Créer nouveau
        new_cache = AppCache(
            cache_key=cache_key,
            cache_value=cache_value,
            expires_at=expires_at
        )
        db.add(new_cache)
    
    db.commit()
    return {"message": "Valeur mise en cache"}

@router.delete("/cache/{cache_key}")
async def delete_cache_value(
    cache_key: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer une valeur du cache"""
    cache_item = db.query(AppCache).filter(
        AppCache.cache_key == cache_key
    ).first()
    
    if cache_item:
        db.delete(cache_item)
        db.commit()
        return {"message": "Valeur supprimée du cache"}
    
    raise HTTPException(status_code=404, detail="Clé de cache non trouvée")

# ==================== WALLPAPER UPLOAD ====================

@router.post("/upload/wallpaper")
async def upload_wallpaper(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Upload a wallpaper image under static/uploads/wallpapers and return its public URL.
    Accepted types: image/jpeg, image/png, image/webp, image/gif, image/svg+xml
    """
    import os, re, time
    from pathlib import Path

    allowed = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/svg+xml"}
    if (file.content_type or "").lower() not in allowed:
        raise HTTPException(status_code=400, detail="Type de fichier non supporté")

    uploads_dir = Path("static/uploads/wallpapers")
    try:
        uploads_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Sanitize filename
    original = file.filename or "wallpaper"
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", original)
    ts = int(time.time())
    # keep extension if present
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
    # Store in UserSettings with key 'appSettings' merged
    try:
        # Load existing appSettings
        setting = db.query(UserSettings).filter(
            and_(UserSettings.user_id == current_user.user_id, UserSettings.setting_key == 'appSettings')
        ).first()
        payload = {}
        if setting and setting.setting_value:
            try:
                payload = json.loads(setting.setting_value) or {}
            except Exception:
                payload = {}
        theme = payload.get('theme') or {}
        theme['desktopBackground'] = public_url
        payload['theme'] = theme
        serialized = json.dumps(payload, ensure_ascii=False)
        if setting:
            setting.setting_value = serialized
            setting.updated_at = datetime.now()
        else:
            setting = UserSettings(user_id=current_user.user_id, setting_key='appSettings', setting_value=serialized)
            db.add(setting)
        db.commit()
    except Exception:
        # Non bloquant: on retourne quand même l'URL
        db.rollback()

    return {"url": public_url}

@router.post("/wallpaper/reset")
async def reset_wallpaper(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Reset the desktop background setting for the current user."""
    # Clear from appSettings.theme.desktopBackground
    setting = db.query(UserSettings).filter(
        and_(UserSettings.user_id == current_user.user_id, UserSettings.setting_key == 'appSettings')
    ).first()
    if setting and setting.setting_value:
        try:
            payload = json.loads(setting.setting_value) or {}
            if isinstance(payload.get('theme'), dict) and 'desktopBackground' in payload['theme']:
                payload['theme'].pop('desktopBackground', None)
                setting.setting_value = json.dumps(payload, ensure_ascii=False)
                setting.updated_at = datetime.now()
                db.commit()
        except Exception:
            db.rollback()
    # Also remove legacy key if present
    legacy = db.query(UserSettings).filter(
        and_(UserSettings.user_id == current_user.user_id, UserSettings.setting_key == 'DESKTOP_BACKGROUND')
    ).first()
    if legacy:
        db.delete(legacy)
        db.commit()
    return {"message": "Fond d'écran réinitialisé"}

# ==================== FAVICON UPLOAD ====================

@router.post("/upload/favicon")
async def upload_favicon(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Upload a favicon image and return its public URL.
    Accepted types: image/x-icon, image/vnd.microsoft.icon, image/png, image/svg+xml
    """
    import os, re, time
    from pathlib import Path

    allowed = {"image/x-icon", "image/vnd.microsoft.icon", "image/png", "image/svg+xml"}
    if (file.content_type or "").lower() not in allowed:
        raise HTTPException(status_code=400, detail="Type de fichier non supporté. Utilisez ICO, PNG ou SVG")

    uploads_dir = Path("static/uploads/favicons")
    try:
        uploads_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Sanitize filename
    original = file.filename or "favicon"
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", original)
    ts = int(time.time())
    # keep extension if present
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
        # Load existing appSettings
        setting = db.query(UserSettings).filter(
            and_(UserSettings.user_id == current_user.user_id, UserSettings.setting_key == 'appSettings')
        ).first()
        payload = {}
        if setting and setting.setting_value:
            try:
                payload = json.loads(setting.setting_value) or {}
            except Exception:
                payload = {}
        general = payload.get('general') or {}
        general['faviconUrl'] = public_url
        payload['general'] = general
        serialized = json.dumps(payload, ensure_ascii=False)
        if setting:
            setting.setting_value = serialized
            setting.updated_at = datetime.now()
        else:
            setting = UserSettings(user_id=current_user.user_id, setting_key='appSettings', setting_value=serialized)
            db.add(setting)
        db.commit()
    except Exception:
        # Non bloquant: on retourne quand même l'URL
        db.rollback()

    return {"url": public_url}

@router.post("/favicon/reset")
async def reset_favicon(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Reset the favicon setting for the current user."""
    # Clear from appSettings.general.faviconUrl
    setting = db.query(UserSettings).filter(
        and_(UserSettings.user_id == current_user.user_id, UserSettings.setting_key == 'appSettings')
    ).first()
    if setting and setting.setting_value:
        try:
            payload = json.loads(setting.setting_value) or {}
            if isinstance(payload.get('general'), dict) and 'faviconUrl' in payload['general']:
                payload['general'].pop('faviconUrl', None)
                setting.setting_value = json.dumps(payload, ensure_ascii=False)
                setting.updated_at = datetime.now()
                db.commit()
        except Exception:
            db.rollback()
    return {"message": "Favicon réinitialisé"}
