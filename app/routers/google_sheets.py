"""
API Router pour l'integration Google Sheets
"""
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional, List
from pydantic import BaseModel
from ..database import User
from ..auth import get_current_user
from ..services.google_sheets_service import GoogleSheetsService
from ..services.google_sheets_validator import GoogleSheetsValidator
from ..services.google_sheets_auto_sync import auto_sync_service
import os


router = APIRouter(prefix="/api/google-sheets", tags=["google-sheets"])


# Schemas Pydantic
class GoogleSheetsSyncRequest(BaseModel):
    spreadsheet_id: str
    worksheet_name: str = "Tableau1"
    update_existing: bool = False
    imei_columns: Optional[List[str]] = None


class GoogleSheetsTestRequest(BaseModel):
    spreadsheet_id: str


class GoogleSheetsSyncResponse(BaseModel):
    success: bool
    message: str
    stats: dict


class GoogleSheetsTestResponse(BaseModel):
    success: bool
    spreadsheet_title: Optional[str] = None
    worksheets: Optional[list] = None
    error: Optional[str] = None


class GoogleSheetsSettingsResponse(BaseModel):
    credentials_configured: bool
    spreadsheet_id: Optional[str] = None
    worksheet_name: Optional[str] = None
    last_sync: Optional[str] = None


# Apercu de feuille
class GoogleSheetsPreviewRequest(BaseModel):
    spreadsheet_id: str
    worksheet_name: str = "Tableau1"
    limit: int = 10


class GoogleSheetsPreviewResponse(BaseModel):
    success: bool
    headers: List[str] = []
    rows: list = []
    suggested_imei_headers: List[str] = []
    error: Optional[str] = None


# Configuration optionnelle pour demarrer l'auto-sync avec la meme feuille que le formulaire
class AutoSyncConfig(BaseModel):
    spreadsheet_id: Optional[str] = None
    worksheet_name: Optional[str] = None
    sync_interval_minutes: Optional[int] = None


@router.post("/sync", response_model=GoogleSheetsSyncResponse)
async def sync_products_from_sheets(
    request: GoogleSheetsSyncRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Synchronise les produits depuis Google Sheets
    """
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acces refuse. Seuls les administrateurs et managers peuvent synchroniser les produits.",
        )

    try:
        service = GoogleSheetsService()

        if not service.credentials_path or not os.path.exists(service.credentials_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Les credentials Google Sheets ne sont pas configures. "
                "Veuillez configurer GOOGLE_SHEETS_CREDENTIALS_PATH dans les variables d'environnement.",
            )

        if not service.authenticate():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Impossible de s'authentifier avec Google Sheets API",
            )

        stats = await service.sync_products(
            spreadsheet_id=request.spreadsheet_id,
            worksheet_name=request.worksheet_name,
            update_existing=False,
            imei_columns=request.imei_columns,
        )

        message = (
            f"Synchronisation terminee: {stats['created']} crees, "
            f"{stats['updated']} mis a jour, {stats['skipped']} ignores, "
            f"{stats['errors']} erreurs"
        )

        return GoogleSheetsSyncResponse(
            success=stats["errors"] == 0 or (stats["created"] + stats["updated"]) > 0,
            message=message,
            stats=stats,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la synchronisation: {str(e)}",
        )


@router.post("/preview", response_model=GoogleSheetsPreviewResponse)
async def preview_google_sheet(
    request: GoogleSheetsPreviewRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Recupere un apercu (en-tetes + premieres lignes) d'une feuille Google Sheets
    """
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acces refuse",
        )

    try:
        service = GoogleSheetsService()
        if not service.credentials_path or not os.path.exists(service.credentials_path):
            return GoogleSheetsPreviewResponse(
                success=False, error="Les credentials Google Sheets ne sont pas configures"
            )

        if not service.authenticate():
            return GoogleSheetsPreviewResponse(
                success=False, error="Impossible de s'authentifier avec Google Sheets API"
            )

        preview = service.get_sheet_preview(
            spreadsheet_id=request.spreadsheet_id,
            worksheet_name=request.worksheet_name,
            limit=request.limit,
        )

        return GoogleSheetsPreviewResponse(success=True, **preview)
    except Exception as e:
        return GoogleSheetsPreviewResponse(success=False, error=str(e))


@router.post("/test-connection", response_model=GoogleSheetsTestResponse)
async def test_google_sheets_connection(
    request: GoogleSheetsTestRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Test la connexion a un Google Spreadsheet
    """
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acces refuse",
        )

    try:
        service = GoogleSheetsService()

        if not service.credentials_path or not os.path.exists(service.credentials_path):
            return GoogleSheetsTestResponse(
                success=False,
                error="Les credentials Google Sheets ne sont pas configures",
            )

        result = service.test_connection(request.spreadsheet_id)

        return GoogleSheetsTestResponse(**result)

    except Exception as e:
        return GoogleSheetsTestResponse(success=False, error=str(e))


@router.get("/settings", response_model=GoogleSheetsSettingsResponse)
async def get_google_sheets_settings(
    current_user: User = Depends(get_current_user),
):
    """
    Recupere la configuration Google Sheets actuelle
    """
    try:
        credentials_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH")
        credentials_configured = bool(credentials_path and os.path.exists(credentials_path))

        return GoogleSheetsSettingsResponse(
            credentials_configured=credentials_configured,
            spreadsheet_id=os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID"),
            worksheet_name=os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME", "Tableau1"),
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la recuperation des parametres: {str(e)}",
        )


@router.post("/sync-stock-to-sheets")
async def sync_stock_to_sheets(
    current_user: User = Depends(get_current_user),
):
    """
    Synchronise tous les stocks de la base de donnees vers Google Sheets
    """
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acces refuse",
        )

    try:
        service = GoogleSheetsService()

        credentials_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH")
        if not credentials_path or not os.path.exists(credentials_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Les credentials Google Sheets ne sont pas configures",
            )

        if not service.authenticate():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Impossible de s'authentifier avec Google Sheets API",
            )

        spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
        worksheet_name = os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME", "Tableau1")

        if not spreadsheet_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GOOGLE_SHEETS_SPREADSHEET_ID n'est pas configure",
            )

        stats = await service.sync_stock_to_sheets(
            spreadsheet_id=spreadsheet_id,
            worksheet_name=worksheet_name,
        )

        message = (
            f"Synchronisation des stocks terminee: {stats['updated']} mis a jour, "
            f"{stats['not_found']} non trouves, {stats['errors']} erreurs"
        )

        return {
            "success": stats["errors"] == 0 or stats["updated"] > 0,
            "message": message,
            "stats": stats,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la synchronisation: {str(e)}",
        )


@router.post("/validate")
async def validate_google_sheet(
    request: GoogleSheetsTestRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Valide un Google Sheet et detecte les problemes de donnees
    """
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acces refuse",
        )

    try:
        validator = GoogleSheetsValidator()

        worksheet_name = os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME", "Tableau1")

        result = validator.validate_sheet(
            spreadsheet_id=request.spreadsheet_id,
            worksheet_name=worksheet_name,
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Erreur de validation"),
            )

        return {
            "success": True,
            "total_issues": result["total_issues"],
            "report": result["report"],
            "issues": result["issues"],
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la validation: {str(e)}",
        )


@router.post("/auto-sync/start")
async def start_auto_sync(
    config: AutoSyncConfig = None,
    current_user: User = Depends(get_current_user),
):
    """
    Demarre la synchronisation automatique depuis Google Sheets
    """
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acces refuse",
        )

    try:
        if config is not None:
            if config.spreadsheet_id:
                os.environ["GOOGLE_SHEETS_SPREADSHEET_ID"] = config.spreadsheet_id
            if config.worksheet_name:
                os.environ["GOOGLE_SHEETS_WORKSHEET_NAME"] = config.worksheet_name
            if config.sync_interval_minutes and config.sync_interval_minutes > 0:
                auto_sync_service.sync_interval_minutes = int(config.sync_interval_minutes)

        success = auto_sync_service.start()

        if success:
            return {
                "success": True,
                "message": f"Synchronisation automatique demarree (intervalle: {auto_sync_service.sync_interval_minutes} minutes)",
                "status": auto_sync_service.get_status(),
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de demarrer la synchronisation automatique. Verifiez la configuration.",
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors du demarrage: {str(e)}",
        )


@router.post("/auto-sync/stop")
async def stop_auto_sync(
    current_user: User = Depends(get_current_user),
):
    """
    Arrete la synchronisation automatique
    """
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acces refuse",
        )

    try:
        success = auto_sync_service.stop()

        if success:
            return {"success": True, "message": "Synchronisation automatique arretee"}
        else:
            return {
                "success": False,
                "message": "La synchronisation automatique n'etait pas en cours",
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de l'arret: {str(e)}",
        )


@router.get("/auto-sync/status")
async def get_auto_sync_status(
    current_user: User = Depends(get_current_user),
):
    """
    Recupere le statut de la synchronisation automatique
    """
    try:
        sync_status = auto_sync_service.get_status()
        return {"success": True, "status": sync_status}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la recuperation du statut: {str(e)}",
        )


@router.post("/auto-sync/trigger")
async def trigger_sync_now(
    current_user: User = Depends(get_current_user),
):
    """
    Declenche une synchronisation immediate (sans attendre l'intervalle)
    """
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acces refuse",
        )

    try:
        success = auto_sync_service.trigger_sync_now()

        if success:
            return {
                "success": True,
                "message": "Synchronisation declenchee avec succes",
                "stats": auto_sync_service.last_sync_stats,
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La synchronisation automatique n'est pas demarree",
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la synchronisation: {str(e)}",
        )
