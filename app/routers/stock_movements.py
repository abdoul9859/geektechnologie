from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional
from datetime import datetime, date, timedelta, time
from ..database import StockMovement, Product, ProductVariant, get_next_id
from ..schemas import StockMovementCreate, StockMovementResponse
from ..auth import get_current_user
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
    current_user=Depends(get_current_user),
):
    """Lister les mouvements de stock avec filtres"""
    try:
        # Exclure les lignes orphelines ou product_id est NULL
        filters: dict = {"product_id": {"$ne": None}}

        if movement_type:
            filters["movement_type"] = movement_type

        if product_id:
            filters["product_id"] = product_id

        if reference_type:
            filters["reference_type"] = reference_type

        # Utiliser des bornes temporelles
        if start_date or end_date:
            period_start = start_date or date.min
            period_end = end_date or start_date or date.max
            start_dt = datetime.combine(period_start, time.min)
            next_dt = datetime.combine(period_end + timedelta(days=1), time.min)
            filters["created_at"] = {"$gte": start_dt, "$lt": next_dt}

        movements = (
            await StockMovement.find(filters)
            .sort(-StockMovement.created_at)
            .skip(skip)
            .limit(limit)
            .to_list()
        )

        # Precharger les noms produits en une seule requete
        product_ids = list({m.product_id for m in movements if getattr(m, "product_id", None)})
        products_map = {}
        if product_ids:
            products = await Product.find(
                {"product_id": {"$in": product_ids}}
            ).to_list()
            products_map = {p.product_id: p.name for p in products}

        result = []
        for m in movements:
            result.append(
                {
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
                }
            )

        return result

    except Exception as e:
        logging.error(f"Erreur lors du listing des mouvements de stock: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur serveur: {str(e)}")


@router.post("/", response_model=StockMovementResponse)
async def create_stock_movement_endpoint(
    movement_data: StockMovementCreate,
    current_user=Depends(get_current_user),
):
    """Creer un mouvement de stock"""
    try:
        # Verifier que le produit existe
        product = await Product.find_one(Product.product_id == movement_data.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouve")

        # Creer le mouvement
        new_id = await get_next_id("stock_movements")
        db_movement = StockMovement(movement_id=new_id, **movement_data.dict())
        await db_movement.insert()

        # Mettre a jour la quantite du produit
        if movement_data.movement_type == "IN":
            product.quantity += movement_data.quantity
        elif movement_data.movement_type == "OUT":
            if product.quantity < movement_data.quantity:
                raise HTTPException(
                    status_code=400,
                    detail="Stock insuffisant pour ce mouvement",
                )
            product.quantity -= movement_data.quantity

        await product.save()

        return db_movement

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la creation du mouvement: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.get("/stats")
async def get_stock_stats(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user=Depends(get_current_user),
):
    """Obtenir les statistiques des mouvements de stock."""
    try:
        period_start = start_date or date.today()
        period_end = end_date or period_start
        start_dt = datetime.combine(period_start, time.min)
        next_dt = datetime.combine(period_end + timedelta(days=1), time.min)

        period_filter = {
            "created_at": {"$gte": start_dt, "$lt": next_dt},
            "product_id": {"$ne": None},
        }

        # Totaux globaux
        total_movements = await StockMovement.find({"product_id": {"$ne": None}}).count()

        # Mouvements sur la periode
        period_movements = await StockMovement.find(period_filter).count()

        # Entrees
        pipeline_in = [
            {"$match": {**period_filter, "movement_type": "IN"}},
            {"$group": {"_id": None, "total": {"$sum": "$quantity"}}},
        ]
        in_result = await StockMovement.aggregate(pipeline_in).to_list()
        today_entries = int(in_result[0]["total"]) if in_result else 0

        # Sorties (valeur absolue)
        pipeline_out = [
            {"$match": {**period_filter, "movement_type": "OUT"}},
            {"$group": {"_id": None, "total": {"$sum": {"$abs": "$quantity"}}}},
        ]
        out_result = await StockMovement.aggregate(pipeline_out).to_list()
        today_exits = int(out_result[0]["total"]) if out_result else 0

        return {
            "total_movements": total_movements,
            "today_movements": period_movements,
            "today_entries": today_entries,
            "today_exits": today_exits,
        }

    except Exception as e:
        logging.error(f"Erreur lors du calcul des stats: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.get("/search-variants")
async def search_variants(
    q: str = Query(..., min_length=1),
    current_user=Depends(get_current_user),
):
    """Rechercher des variantes par IMEI/serie"""
    try:
        variants = (
            await ProductVariant.find(
                {"imei_serial": {"$regex": q, "$options": "i"}}
            )
            .limit(10)
            .to_list()
        )

        results = []
        for variant in variants:
            product = await Product.find_one(Product.product_id == variant.product_id)
            results.append(
                {
                    "variant_id": variant.variant_id,
                    "imei_serial": variant.imei_serial,
                    "product_name": product.name if product else None,
                    "product_id": variant.product_id,
                    "is_sold": variant.is_sold,
                }
            )

        return results

    except Exception as e:
        logging.error(f"Erreur lors de la recherche de variantes: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


# Fonction utilitaire pour creer automatiquement des mouvements de stock
async def create_stock_movement_util(
    product_id: int,
    quantity: int,
    movement_type: str,
    reference_type: str = None,
    reference_id: int = None,
    notes: str = None,
    unit_price: float = 0,
):
    """Fonction utilitaire pour creer des mouvements de stock automatiquement"""
    try:
        new_id = await get_next_id("stock_movements")
        movement = StockMovement(
            movement_id=new_id,
            product_id=product_id,
            quantity=quantity,
            movement_type=movement_type,
            reference_type=reference_type,
            reference_id=reference_id,
            notes=notes,
            unit_price=unit_price,
        )
        await movement.insert()
        return movement
    except Exception as e:
        logging.error(f"Erreur lors de la creation automatique du mouvement: {e}")
        raise


# ====================== Maintenance / Nettoyage ======================


@router.delete("/cleanup")
async def cleanup_stock_movements(
    product_id: Optional[int] = None,
    reference_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user=Depends(get_current_user),
):
    """Supprimer des mouvements de stock selon des filtres."""
    try:
        # Restreindre aux admins
        try:
            if getattr(current_user, "role", None) not in ["admin"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Permissions insuffisantes",
                )
        except Exception:
            pass

        filters: dict = {}
        has_filter = False

        if product_id is not None:
            filters["product_id"] = product_id
            has_filter = True
        if reference_type and reference_type != "ANY":
            filters["reference_type"] = reference_type
            has_filter = True
        if start_date:
            filters.setdefault("created_at", {})["$gte"] = datetime.combine(start_date, time.min)
            has_filter = True
        if end_date:
            filters.setdefault("created_at", {})["$lte"] = datetime.combine(end_date + timedelta(days=1), time.min)
            has_filter = True

        if not has_filter and (reference_type or "") != "ANY":
            raise HTTPException(
                status_code=400,
                detail="Aucun filtre fourni. Specifiez un filtre ou reference_type=ANY pour tout supprimer.",
            )

        to_delete = await StockMovement.find(filters).count()
        if to_delete <= 0:
            return {"deleted": 0}

        await StockMovement.find(filters).delete()
        return {"deleted": to_delete}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors du nettoyage des mouvements: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.post("/recompute-quantities")
async def recompute_product_quantities(
    product_id: Optional[int] = None,
    current_user=Depends(get_current_user),
):
    """Recalculer les quantites produits a partir des mouvements restants."""
    try:
        # Admin seulement
        try:
            if getattr(current_user, "role", None) not in ["admin"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Permissions insuffisantes",
                )
        except Exception:
            pass

        product_filters = {}
        if product_id is not None:
            product_filters["product_id"] = product_id

        products_list = await Product.find(product_filters).to_list()

        updated = 0
        for p in products_list:
            pipeline_in = [
                {"$match": {"product_id": p.product_id, "movement_type": "IN"}},
                {"$group": {"_id": None, "total": {"$sum": "$quantity"}}},
            ]
            pipeline_out = [
                {"$match": {"product_id": p.product_id, "movement_type": "OUT"}},
                {"$group": {"_id": None, "total": {"$sum": "$quantity"}}},
            ]

            in_result = await StockMovement.aggregate(pipeline_in).to_list()
            out_result = await StockMovement.aggregate(pipeline_out).to_list()

            ins = int(in_result[0]["total"]) if in_result else 0
            outs = int(out_result[0]["total"]) if out_result else 0

            try:
                new_qty = ins - outs
            except Exception:
                new_qty = p.quantity or 0

            if new_qty != (p.quantity or 0):
                p.quantity = new_qty
                await p.save()
                updated += 1

        return {"updated_products": updated}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors du recalcul des quantites: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
