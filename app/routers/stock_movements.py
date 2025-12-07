from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, and_
from typing import List, Optional
from datetime import datetime, date, timedelta, time
from ..database import get_db, StockMovement, Product, ProductVariant
from ..schemas import StockMovementCreate, StockMovementResponse
from ..auth import get_current_user
from ..services.google_sheets_sync_helper import sync_product_stock_to_sheets
import logging

router = APIRouter(prefix="/api/stock-movements", tags=["stock-movements"])

@router.get("/", response_model=List[StockMovementResponse])
async def list_stock_movements(
    skip: int = 0,
    limit: int = 100,
    movement_type: Optional[str] = None,
    product_id: Optional[int] = None,
    reference_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les mouvements de stock avec filtres"""
    try:
        # Exclure les lignes orphelines où product_id est NULL (héritage de données)
        query = (
            db.query(StockMovement)
            .filter(StockMovement.product_id.isnot(None))
            .order_by(desc(StockMovement.created_at))
        )
        
        if movement_type:
            query = query.filter(StockMovement.movement_type == movement_type)
        
        if product_id:
            query = query.filter(StockMovement.product_id == product_id)
        
        if reference_type:
            query = query.filter(StockMovement.reference_type == reference_type)
        
        # Utiliser des bornes temporelles pour bénéficier des index (évite func.date)
        if start_date or end_date:
            # Si une seule borne est fournie, l'autre est déduite/faible impact
            period_start = start_date or date.min
            period_end = end_date or start_date or date.max
            start_dt = datetime.combine(period_start, time.min)
            next_dt = datetime.combine(period_end + timedelta(days=1), time.min)
            query = query.filter(StockMovement.created_at >= start_dt, StockMovement.created_at < next_dt)
        
        movements = query.offset(skip).limit(limit).all()

        # Précharger les noms produits en une seule requête (évite N+1 côté client)
        product_ids = list({m.product_id for m in movements if getattr(m, 'product_id', None)})
        products_map = {}
        if product_ids:
            products = (
                db.query(Product.product_id, Product.name)
                .filter(Product.product_id.in_(product_ids))
                .all()
            )
            products_map = {pid: name for pid, name in products}

        # Construire une réponse sérialisable enrichie avec product_name
        result = []
        for m in movements:
            result.append({
                "movement_id": m.movement_id,
                "product_id": m.product_id,
                "product_name": products_map.get(m.product_id),
                "quantity": m.quantity,
                "movement_type": m.movement_type,
                "reference_type": m.reference_type,
                "reference_id": m.reference_id,
                "notes": m.notes,
                "unit_price": (m.unit_price or 0),
                "created_at": m.created_at,
            })

        return result
        
    except Exception as e:
        logging.error(f"Erreur lors du listing des mouvements de stock: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur serveur: {str(e)}")
    finally:
        # Fin explicite de la transaction en lecture pour éviter l'inactivité
        try:
            db.rollback()
        except Exception:
            pass

@router.post("/", response_model=StockMovementResponse)
async def create_stock_movement(
    movement_data: StockMovementCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Créer un mouvement de stock"""
    try:
        # Vérifier que le produit existe
        product = db.query(Product).filter(Product.product_id == movement_data.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Créer le mouvement
        db_movement = StockMovement(**movement_data.dict())
        db.add(db_movement)
        
        # Mettre à jour la quantité du produit
        if movement_data.movement_type == "IN":
            product.quantity += movement_data.quantity
        elif movement_data.movement_type == "OUT":
            if product.quantity < movement_data.quantity:
                raise HTTPException(
                    status_code=400, 
                    detail="Stock insuffisant pour ce mouvement"
                )
            product.quantity -= movement_data.quantity
        
        db.commit()
        db.refresh(db_movement)

        # Synchroniser le stock avec Google Sheets (si activé)
        try:
            sync_product_stock_to_sheets(db, movement_data.product_id)
        except Exception as e:
            logging.warning(f"Échec de synchronisation Google Sheets pour le produit {movement_data.product_id}: {e}")
            pass

        return db_movement

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la création du mouvement: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/stats")
async def get_stock_stats(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir les statistiques des mouvements de stock.

    - Par défaut, calcule pour la journée en cours (heure serveur)
    - `today_entries` est la somme des quantités en entrée (toujours positive)
    - `today_exits` est la somme absolue des quantités en sortie (positive même si stock_movement.quantity est négatif)
    - Aligne le périmètre avec la liste en excluant les lignes orphelines (`product_id` NULL)
    """
    try:
        period_start = start_date or date.today()
        period_end = end_date or period_start
        # Fenêtre [start 00:00:00, next_day 00:00:00) robuste tous SGBD
        start_dt = datetime.combine(period_start, time.min)
        next_dt = datetime.combine(period_end + timedelta(days=1), time.min)

        # Adapter le filtre selon le SGBD pour éviter les décalages de fuseau sur SQLite
        dialect = getattr(db.bind, 'dialect', None)
        is_sqlite = bool(dialect and getattr(dialect, 'name', '') == 'sqlite')

        if is_sqlite:
            # Utiliser strftime avec 'localtime' pour matcher l'horloge locale
            start_str = period_start.strftime('%Y-%m-%d')
            end_str = period_end.strftime('%Y-%m-%d')
            period_filter = and_(
                func.strftime('%Y-%m-%d', StockMovement.created_at, 'localtime') >= start_str,
                func.strftime('%Y-%m-%d', StockMovement.created_at, 'localtime') <= end_str,
                StockMovement.product_id.isnot(None)
            )
        else:
            period_filter = and_(
                StockMovement.created_at >= start_dt,
                StockMovement.created_at < next_dt,
                StockMovement.product_id.isnot(None)
            )

        # Totaux globaux (toutes dates) pour la tuile « Total Mouvements »
        total_movements = db.query(StockMovement).filter(StockMovement.product_id.isnot(None)).count()

        # Mouvements sur la période
        period_movements = db.query(StockMovement).filter(period_filter).count()

        # Entrées (somme des quantités positives pour type IN)
        today_entries = (
            db.query(func.coalesce(func.sum(StockMovement.quantity), 0))
            .filter(and_(period_filter, StockMovement.movement_type == "IN"))
            .scalar()
            or 0
        )

        # Sorties (somme des quantités en valeur absolue pour type OUT)
        today_exits = (
            db.query(func.coalesce(func.sum(func.abs(StockMovement.quantity)), 0))
            .filter(and_(period_filter, StockMovement.movement_type == "OUT"))
            .scalar()
            or 0
        )

        return {
            "total_movements": total_movements,
            "today_movements": period_movements,
            "today_entries": int(today_entries),
            "today_exits": int(today_exits),
        }

    except Exception as e:
        logging.error(f"Erreur lors du calcul des stats: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
    finally:
        # Terminer rapidement la transaction de lecture
        try:
            db.rollback()
        except Exception:
            pass

@router.get("/search-variants")
async def search_variants(
    q: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Rechercher des variantes par IMEI/série"""
    try:
        variants = db.query(ProductVariant).join(Product).filter(
            ProductVariant.imei_serial.ilike(f"%{q}%")
        ).limit(10).all()
        
        results = []
        for variant in variants:
            results.append({
                "variant_id": variant.variant_id,
                "imei_serial": variant.imei_serial,
                "product_name": variant.product.name,
                "product_id": variant.product_id,
                "is_sold": variant.is_sold
            })
        
        return results
        
    except Exception as e:
        logging.error(f"Erreur lors de la recherche de variantes: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
    finally:
        # Terminer la transaction de lecture
        try:
            db.rollback()
        except Exception:
            pass

# Fonction utilitaire pour créer automatiquement des mouvements de stock
def create_stock_movement(db: Session, product_id: int, quantity: int, movement_type: str, 
                         reference_type: str = None, reference_id: int = None, 
                         notes: str = None, unit_price: float = 0):
    """Fonction utilitaire pour créer des mouvements de stock automatiquement"""
    try:
        movement = StockMovement(
            product_id=product_id,
            quantity=quantity,
            movement_type=movement_type,
            reference_type=reference_type,
            reference_id=reference_id,
            notes=notes,
            unit_price=unit_price
        )
        db.add(movement)
        return movement
    except Exception as e:
        logging.error(f"Erreur lors de la création automatique du mouvement: {e}")
        raise


# ====================== Maintenance / Nettoyage ======================

@router.delete("/cleanup")
async def cleanup_stock_movements(
    product_id: Optional[int] = None,
    reference_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer des mouvements de stock selon des filtres.

    - Par défaut ne supprime rien sans filtre explicite;
    - Si aucun filtre n'est fourni, ajoutez `reference_type=ANY` pour tout supprimer;
    - Retourne le nombre de lignes supprimées;
    - Ne modifie pas les quantités des produits (source de vérité) sauf si vous utilisez l'endpoint de recalcul.
    """
    try:
        # Restreindre aux admins
        try:
            if getattr(current_user, "role", None) not in ["admin"]:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permissions insuffisantes")
        except Exception:
            pass

        q = db.query(StockMovement)
        # Filtrer (éviter de tout supprimer par accident si aucun filtre)
        has_filter = False
        if product_id is not None:
            q = q.filter(StockMovement.product_id == product_id)
            has_filter = True
        if reference_type and reference_type != "ANY":
            q = q.filter(StockMovement.reference_type == reference_type)
            has_filter = True
        if start_date:
            q = q.filter(func.date(StockMovement.created_at) >= start_date)
            has_filter = True
        if end_date:
            q = q.filter(func.date(StockMovement.created_at) <= end_date)
            has_filter = True

        # Si aucun filtre, exiger reference_type=ANY pour autoriser effacement total
        if not has_filter and (reference_type or "") != "ANY":
            raise HTTPException(status_code=400, detail="Aucun filtre fourni. Spécifiez un filtre ou reference_type=ANY pour tout supprimer.")

        # Compter avant suppression
        to_delete = q.count()
        if to_delete <= 0:
            return {"deleted": 0}
        q.delete(synchronize_session=False)
        db.commit()
        return {"deleted": to_delete}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors du nettoyage des mouvements: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.post("/recompute-quantities")
async def recompute_product_quantities(
    product_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Recalculer les quantités produits à partir des mouvements restants.

    ATTENTION: Ne reflète que l'historique présent dans la table des mouvements.
    """
    try:
        # Admin seulement
        try:
            if getattr(current_user, "role", None) not in ["admin"]:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permissions insuffisantes")
        except Exception:
            pass

        products_q = db.query(Product)
        if product_id is not None:
            products_q = products_q.filter(Product.product_id == product_id)
        products_list = products_q.all()

        updated = 0
        for p in products_list:
            ins = db.query(func.coalesce(func.sum(StockMovement.quantity), 0)).filter(StockMovement.product_id == p.product_id, StockMovement.movement_type == "IN").scalar() or 0
            outs = db.query(func.coalesce(func.sum(StockMovement.quantity), 0)).filter(StockMovement.product_id == p.product_id, StockMovement.movement_type == "OUT").scalar() or 0
            try:
                # outs est positif dans le modèle actuel, on fait ins - outs
                new_qty = int(ins) - int(outs)
            except Exception:
                new_qty = (p.quantity or 0)
            if new_qty != (p.quantity or 0):
                p.quantity = new_qty
                updated += 1
        db.commit()
        return {"updated_products": updated}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors du recalcul des quantités: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
