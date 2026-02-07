"""
Service de synchronisation automatique bidirectionnelle avec Google Sheets
"""
import os
import asyncio
import logging
from datetime import datetime
from typing import Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.database import Product
from app.services.google_sheets_service import GoogleSheetsService

# Configuration du logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GoogleSheetsAutoSync:
    """Service de synchronisation automatique avec Google Sheets"""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.is_running = False
        self.last_sync_time: Optional[datetime] = None
        self.last_sync_stats: Optional[dict] = None
        self.sync_interval_minutes = int(os.getenv('GOOGLE_SHEETS_SYNC_INTERVAL', '10'))

    def start(self):
        """Démarre la synchronisation automatique"""
        # Synchronisation automatique désactivée : on ne programme plus de job périodique.
        logger.info("Synchronisation automatique Google Sheets desactivee - utiliser l'endpoint /api/google-sheets/sync pour un import manuel uniquement.")
        return False

    def stop(self):
        """Arrête la synchronisation automatique"""
        if not self.is_running:
            logger.warning("La synchronisation automatique n'est pas en cours")
            return False

        try:
            self.scheduler.shutdown()
            self.is_running = False
            logger.info("Synchronisation automatique arretee")
            return True

        except Exception as e:
            logger.error(f"Erreur lors de l'arret de la synchronisation automatique: {str(e)}")
            return False

    def _check_configuration(self) -> bool:
        """Vérifie que la configuration Google Sheets est complète"""
        credentials_path = os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH')
        spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')

        if not credentials_path or not os.path.exists(credentials_path):
            logger.error("GOOGLE_SHEETS_CREDENTIALS_PATH non configure ou fichier inexistant")
            return False

        if not spreadsheet_id:
            logger.error("GOOGLE_SHEETS_SPREADSHEET_ID non configure")
            return False

        return True

    async def _async_sync_from_sheets(self):
        """Async method: synchronise les produits depuis Google Sheets vers l'application"""
        logger.info("Debut de la synchronisation depuis Google Sheets...")

        try:
            # Récupérer la configuration
            spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
            worksheet_name = os.getenv('GOOGLE_SHEETS_WORKSHEET_NAME', 'Tableau1')

            # Initialiser le service
            service = GoogleSheetsService()
            if not service.authenticate():
                logger.error("Echec d'authentification Google Sheets")
                return

            # Récupérer les données du sheet
            rows = service.get_sheet_data(spreadsheet_id, worksheet_name)

            stats = {
                'total': len(rows),
                'updated': 0,
                'created': 0,
                'skipped': 0,
                'errors': 0
            }

            for idx, row in enumerate(rows, start=1):
                try:
                    # Mapper la ligne vers un dict de produit
                    product_data = service.map_sheet_row_to_product(row)

                    # Ignorer les lignes sans nom de produit
                    if not product_data.get('name'):
                        stats['skipped'] += 1
                        continue

                    # Chercher le produit existant par code-barres
                    existing_product = None
                    if product_data.get('barcode'):
                        existing_product = await Product.find_one(
                            Product.barcode == product_data['barcode']
                        )

                    if existing_product:
                        # Mettre à jour uniquement si la quantité a changé
                        sheet_quantity = product_data.get('quantity', 0)
                        if existing_product.quantity != sheet_quantity:
                            old_quantity = existing_product.quantity
                            existing_product.quantity = sheet_quantity

                            # Mettre à jour les autres champs si nécessaire
                            for key in ['price', 'wholesale_price', 'purchase_price', 'name', 'description']:
                                if key in product_data and product_data[key] is not None:
                                    setattr(existing_product, key, product_data[key])

                            await existing_product.save()
                            stats['updated'] += 1
                            logger.info(f"Produit mis a jour: {existing_product.name} - Quantite: {old_quantity} -> {sheet_quantity}")
                        else:
                            stats['skipped'] += 1
                    else:
                        # Créer un nouveau produit
                        from app.database import get_next_id
                        product_id = await get_next_id("products")
                        # Remove keys not in the Product model
                        clean_data = {k: v for k, v in product_data.items() if k not in ('imei_serial', 'imei_serials')}
                        new_product = Product(product_id=product_id, **clean_data)
                        await new_product.insert()
                        stats['created'] += 1
                        logger.info(f"Nouveau produit cree: {new_product.name}")

                except Exception as e:
                    stats['errors'] += 1
                    logger.error(f"Erreur ligne {idx}: {str(e)}")
                    continue

            # Enregistrer les statistiques
            self.last_sync_time = datetime.now()
            self.last_sync_stats = stats

            logger.info(
                f"Synchronisation terminee: "
                f"{stats['updated']} mis a jour, "
                f"{stats['created']} crees, "
                f"{stats['skipped']} ignores, "
                f"{stats['errors']} erreurs"
            )

        except Exception as e:
            logger.error(f"Erreur lors de la synchronisation: {str(e)}")

    def _sync_from_sheets(self):
        """
        Synchronise les produits depuis Google Sheets vers l'application.
        Called from BackgroundScheduler thread -- creates a new event loop
        to run the async implementation.
        """
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._async_sync_from_sheets())
        finally:
            loop.close()

    def get_status(self) -> dict:
        """Retourne le statut de la synchronisation automatique"""
        return {
            'is_running': self.is_running,
            'sync_interval_minutes': self.sync_interval_minutes,
            'last_sync_time': self.last_sync_time.isoformat() if self.last_sync_time else None,
            'last_sync_stats': self.last_sync_stats,
            'next_sync_time': self._get_next_sync_time()
        }

    def _get_next_sync_time(self) -> Optional[str]:
        """Retourne l'heure de la prochaine synchronisation"""
        if not self.is_running or not self.scheduler.get_jobs():
            return None

        job = self.scheduler.get_job('google_sheets_sync')
        if job and job.next_run_time:
            return job.next_run_time.isoformat()

        return None

    def trigger_sync_now(self):
        """Déclenche une synchronisation immédiate"""
        # Désactivé: l'import se fait uniquement via l'endpoint manuel /api/google-sheets/sync
        logger.info("trigger_sync_now ignore: la synchronisation automatique est desactivee")
        return False


# Instance globale du service
auto_sync_service = GoogleSheetsAutoSync()
