"""
Helper pour la synchronisation automatique avec Google Sheets
"""
import os
from typing import Optional
from sqlalchemy.orm import Session
from app.database import Product
from app.services.google_sheets_service import GoogleSheetsService


def sync_product_stock_to_sheets(db: Session, product_id: int) -> bool:
    """
    Synchronise le stock d'un produit vers Google Sheets

    Args:
        db: Session SQLAlchemy
        product_id: ID du produit à synchroniser

    Returns:
        True si la synchronisation réussit, False sinon
    """
    try:
        # Vérifier si la synchronisation Google Sheets est activée
        spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
        worksheet_name = os.getenv('GOOGLE_SHEETS_WORKSHEET_NAME', 'Tableau1')
        auto_sync_enabled = os.getenv('GOOGLE_SHEETS_AUTO_SYNC', 'false').lower() == 'true'

        if not auto_sync_enabled:
            return False

        if not spreadsheet_id:
            print("⚠️ GOOGLE_SHEETS_SPREADSHEET_ID non configuré, synchronisation ignorée")
            return False

        # Récupérer le produit
        product = db.query(Product).filter(Product.product_id == product_id).first()
        if not product:
            print(f"⚠️ Produit {product_id} non trouvé")
            return False

        # Si le produit n'a pas de code-barres, impossible de le retrouver dans le Google Sheet
        if not product.barcode:
            print(f"⚠️ Produit {product.name} n'a pas de code-barres, synchronisation impossible")
            return False

        # Initialiser le service Google Sheets
        service = GoogleSheetsService()
        if not service.authenticate():
            print("❌ Échec d'authentification Google Sheets")
            return False

        # Mettre à jour le stock dans le Google Sheet
        success = service.update_product_stock_in_sheet(
            spreadsheet_id=spreadsheet_id,
            worksheet_name=worksheet_name,
            product_barcode=product.barcode,
            new_quantity=product.quantity or 0
        )

        return success

    except Exception as e:
        print(f"❌ Erreur lors de la synchronisation du stock vers Google Sheets: {str(e)}")
        return False


def sync_multiple_products_to_sheets(db: Session, product_ids: list) -> dict:
    """
    Synchronise plusieurs produits vers Google Sheets en une seule fois

    Args:
        db: Session SQLAlchemy
        product_ids: Liste des IDs de produits à synchroniser

    Returns:
        Statistiques de synchronisation
    """
    stats = {
        'total': len(product_ids),
        'updated': 0,
        'skipped': 0,
        'errors': 0
    }

    try:
        # Vérifier si la synchronisation est activée
        auto_sync_enabled = os.getenv('GOOGLE_SHEETS_AUTO_SYNC', 'false').lower() == 'true'
        if not auto_sync_enabled:
            stats['skipped'] = stats['total']
            return stats

        for product_id in product_ids:
            try:
                success = sync_product_stock_to_sheets(db, product_id)
                if success:
                    stats['updated'] += 1
                else:
                    stats['skipped'] += 1
            except Exception as e:
                print(f"❌ Erreur pour le produit {product_id}: {str(e)}")
                stats['errors'] += 1
                continue

        return stats

    except Exception as e:
        print(f"❌ Erreur globale de synchronisation: {str(e)}")
        stats['errors'] = stats['total']
        return stats
