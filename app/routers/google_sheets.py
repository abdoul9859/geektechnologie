"""
API Router pour l'intégration Google Sheets
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from app.database import get_db, User
from app.auth import get_current_user
from app.services.google_sheets_service import GoogleSheetsService
from app.services.google_sheets_validator import GoogleSheetsValidator
from app.services.google_sheets_auto_sync import auto_sync_service
import os


router = APIRouter(prefix="/api/google-sheets", tags=["google-sheets"])


# Schémas Pydantic
class GoogleSheetsSyncRequest(BaseModel):
    spreadsheet_id: str
    worksheet_name: str = 'Tableau1'
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

# Aperçu de feuille
class GoogleSheetsPreviewRequest(BaseModel):
    spreadsheet_id: str
    worksheet_name: str = 'Tableau1'
    limit: int = 10

class GoogleSheetsPreviewResponse(BaseModel):
    success: bool
    headers: List[str] = []
    rows: list = []
    suggested_imei_headers: List[str] = []
    error: Optional[str] = None

# Configuration optionnelle pour démarrer l'auto-sync avec la même feuille que le formulaire
class AutoSyncConfig(BaseModel):
    spreadsheet_id: Optional[str] = None
    worksheet_name: Optional[str] = None
    sync_interval_minutes: Optional[int] = None


@router.post("/sync", response_model=GoogleSheetsSyncResponse)
async def sync_products_from_sheets(
    request: GoogleSheetsSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Synchronise les produits depuis Google Sheets

    Nécessite:
    - Un fichier de credentials Google (service account JSON) configuré dans GOOGLE_SHEETS_CREDENTIALS_PATH
    - L'ID du Google Spreadsheet
    - Le nom de la feuille (optionnel, par défaut 'Tableau1')
    """
    # Vérification des permissions (admin ou manager uniquement)
    if current_user.role not in ['admin', 'manager']:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les administrateurs et managers peuvent synchroniser les produits."
        )

    try:
        service = GoogleSheetsService()

        if not service.credentials_path or not os.path.exists(service.credentials_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Les credentials Google Sheets ne sont pas configurés. "
                       "Veuillez configurer GOOGLE_SHEETS_CREDENTIALS_PATH dans les variables d'environnement."
            )

        if not service.authenticate():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Impossible de s'authentifier avec Google Sheets API"
            )

        # IMPORTANT: on force update_existing=False pour que Google Sheets
        # ne serve qu'à ajouter de nouveaux produits (pas de mise à jour ni
        # de modification de stock pour les produits déjà existants).
        stats = service.sync_products(
            db=db,
            spreadsheet_id=request.spreadsheet_id,
            worksheet_name=request.worksheet_name,
            update_existing=False,
            imei_columns=request.imei_columns
        )

        message = (
            f"Synchronisation terminée: {stats['created']} créés, "
            f"{stats['updated']} mis à jour, {stats['skipped']} ignorés, "
            f"{stats['errors']} erreurs"
        )

        return GoogleSheetsSyncResponse(
            success=stats['errors'] == 0 or (stats['created'] + stats['updated']) > 0,
            message=message,
            stats=stats
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la synchronisation: {str(e)}"
        )


@router.post("/preview", response_model=GoogleSheetsPreviewResponse)
async def preview_google_sheet(
    request: GoogleSheetsPreviewRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Récupère un aperçu (en-têtes + premières lignes) d'une feuille Google Sheets
    """
    if current_user.role not in ['admin', 'manager']:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )

    try:
        service = GoogleSheetsService()
        if not service.credentials_path or not os.path.exists(service.credentials_path):
            return GoogleSheetsPreviewResponse(success=False, error="Les credentials Google Sheets ne sont pas configurés")

        if not service.authenticate():
            return GoogleSheetsPreviewResponse(success=False, error="Impossible de s'authentifier avec Google Sheets API")

        preview = service.get_sheet_preview(
            spreadsheet_id=request.spreadsheet_id,
            worksheet_name=request.worksheet_name,
            limit=request.limit
        )

        return GoogleSheetsPreviewResponse(success=True, **preview)
    except Exception as e:
        return GoogleSheetsPreviewResponse(success=False, error=str(e))


@router.post("/test-connection", response_model=GoogleSheetsTestResponse)
async def test_google_sheets_connection(
    request: GoogleSheetsTestRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Test la connexion à un Google Spreadsheet
    """
    # Vérification des permissions
    if current_user.role not in ['admin', 'manager']:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )

    try:
        service = GoogleSheetsService()

        # Vérifie que les credentials sont configurés
        if not service.credentials_path or not os.path.exists(service.credentials_path):
            return GoogleSheetsTestResponse(
                success=False,
                error="Les credentials Google Sheets ne sont pas configurés"
            )

        # Test la connexion
        result = service.test_connection(request.spreadsheet_id)

        return GoogleSheetsTestResponse(**result)

    except Exception as e:
        return GoogleSheetsTestResponse(
            success=False,
            error=str(e)
        )


@router.get("/settings", response_model=GoogleSheetsSettingsResponse)
async def get_google_sheets_settings(
    current_user: User = Depends(get_current_user)
):
    """
    Récupère la configuration Google Sheets actuelle
    """
    try:
        credentials_path = os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH')
        credentials_configured = bool(credentials_path and os.path.exists(credentials_path))

        return GoogleSheetsSettingsResponse(
            credentials_configured=credentials_configured,
            spreadsheet_id=os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID'),
            worksheet_name=os.getenv('GOOGLE_SHEETS_WORKSHEET_NAME', 'Tableau1')
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la récupération des paramètres: {str(e)}"
        )


@router.post("/sync-stock-to-sheets")
async def sync_stock_to_sheets(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Synchronise tous les stocks de la base de données vers Google Sheets
    """
    # Vérification des permissions
    if current_user.role not in ['admin', 'manager']:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )

    try:
        service = GoogleSheetsService()

        # Vérifie que les credentials sont configurés
        credentials_path = os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH')
        if not credentials_path or not os.path.exists(credentials_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Les credentials Google Sheets ne sont pas configurés"
            )

        # Authentification
        if not service.authenticate():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Impossible de s'authentifier avec Google Sheets API"
            )

        # Récupération des paramètres
        spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
        worksheet_name = os.getenv('GOOGLE_SHEETS_WORKSHEET_NAME', 'Tableau1')

        if not spreadsheet_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GOOGLE_SHEETS_SPREADSHEET_ID n'est pas configuré"
            )

        # Synchronise tous les stocks
        stats = service.sync_stock_to_sheets(
            db=db,
            spreadsheet_id=spreadsheet_id,
            worksheet_name=worksheet_name
        )

        message = (
            f"Synchronisation des stocks terminée: {stats['updated']} mis à jour, "
            f"{stats['not_found']} non trouvés, {stats['errors']} erreurs"
        )

        return {
            'success': stats['errors'] == 0 or stats['updated'] > 0,
            'message': message,
            'stats': stats
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la synchronisation: {str(e)}"
        )


@router.post("/validate")
async def validate_google_sheet(
    request: GoogleSheetsTestRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Valide un Google Sheet et détecte les problèmes de données

    Vérifie:
    - Lignes vides
    - Noms de produits manquants
    - Code-barres manquants
    - Code-barres dupliqués
    - Prix invalides
    - Quantités invalides
    """
    # Vérification des permissions
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )

    try:
        validator = GoogleSheetsValidator()

        # Récupération des paramètres
        worksheet_name = os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME", "Tableau1")

        # Validation
        result = validator.validate_sheet(
            spreadsheet_id=request.spreadsheet_id,
            worksheet_name=worksheet_name
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Erreur de validation")
            )

        return {
            "success": True,
            "total_issues": result["total_issues"],
            "report": result["report"],
            "issues": result["issues"]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la validation: {str(e)}"
        )


@router.post("/auto-sync/start")
async def start_auto_sync(
    config: AutoSyncConfig = None,
    current_user: User = Depends(get_current_user)
):
    """
    Démarre la synchronisation automatique depuis Google Sheets
    
    La synchronisation s'effectuera automatiquement toutes les X minutes
    (défini par GOOGLE_SHEETS_SYNC_INTERVAL, par défaut 10 minutes)
    """
    # Vérification des permissions
    if current_user.role not in ['admin', 'manager']:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    try:
        # Optionally override configuration from request
        if config is not None:
            if config.spreadsheet_id:
                os.environ['GOOGLE_SHEETS_SPREADSHEET_ID'] = config.spreadsheet_id
            if config.worksheet_name:
                os.environ['GOOGLE_SHEETS_WORKSHEET_NAME'] = config.worksheet_name
            if config.sync_interval_minutes and config.sync_interval_minutes > 0:
                auto_sync_service.sync_interval_minutes = int(config.sync_interval_minutes)

        success = auto_sync_service.start()
        
        if success:
            return {
                'success': True,
                'message': f'Synchronisation automatique démarrée (intervalle: {auto_sync_service.sync_interval_minutes} minutes)',
                'status': auto_sync_service.get_status()
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de démarrer la synchronisation automatique. Vérifiez la configuration."
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors du démarrage: {str(e)}"
        )


@router.post("/auto-sync/stop")
async def stop_auto_sync(
    current_user: User = Depends(get_current_user)
):
    """
    Arrête la synchronisation automatique
    """
    # Vérification des permissions
    if current_user.role not in ['admin', 'manager']:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    try:
        success = auto_sync_service.stop()
        
        if success:
            return {
                'success': True,
                'message': 'Synchronisation automatique arrêtée'
            }
        else:
            return {
                'success': False,
                'message': 'La synchronisation automatique n\'était pas en cours'
            }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de l'arrêt: {str(e)}"
        )


@router.get("/auto-sync/status")
async def get_auto_sync_status(
    current_user: User = Depends(get_current_user)
):
    """
    Récupère le statut de la synchronisation automatique
    """
    try:
        status = auto_sync_service.get_status()
        return {
            'success': True,
            'status': status
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la récupération du statut: {str(e)}"
        )


@router.post("/auto-sync/trigger")
async def trigger_sync_now(
    current_user: User = Depends(get_current_user)
):
    """
    Déclenche une synchronisation immédiate (sans attendre l'intervalle)
    """
    # Vérification des permissions
    if current_user.role not in ['admin', 'manager']:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    try:
        success = auto_sync_service.trigger_sync_now()
        
        if success:
            return {
                'success': True,
                'message': 'Synchronisation déclenchée avec succès',
                'stats': auto_sync_service.last_sync_stats
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La synchronisation automatique n'est pas démarrée"
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la synchronisation: {str(e)}"
        )

