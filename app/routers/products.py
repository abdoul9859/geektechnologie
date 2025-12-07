from fastapi import APIRouter, Depends, HTTPException, status, Query, Body, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy.orm import selectinload, load_only
from sqlalchemy import or_, and_, func, text, exists, case
from typing import List, Optional, Dict
from decimal import Decimal
import os
import shutil
from pathlib import Path
from ..database import (
    get_db, Product, ProductVariant, ProductVariantAttribute, StockMovement, Category,
    CategoryAttribute, CategoryAttributeValue, UserSettings, DailySale, Invoice, Client
)
from ..schemas import (
    ProductCreate, ProductUpdate, ProductResponse, ProductVariantCreate, StockMovementCreate,
    CategoryAttributeCreate, CategoryAttributeUpdate, CategoryAttributeResponse,
    CategoryAttributeValueCreate, CategoryAttributeValueUpdate, CategoryAttributeValueResponse,
    ProductListItem, ProductVariantListItem
)
from ..auth import get_current_user, require_role, require_any_role
from decimal import Decimal
from pydantic import BaseModel
from ..database import InvoiceItem, QuotationItem, DeliveryNoteItem
from sqlalchemy.exc import IntegrityError
import logging
import time

router = APIRouter(prefix="/api/products", tags=["products"])

def is_product_used_in_transactions(db: Session, product_id: int) -> bool:
    """Vérifie si un produit est utilisé dans des factures, devis ou bons de livraison
    NOTE: Cette fonction retourne toujours False maintenant car on permet la modification des produits.
    La protection se fait au niveau des variantes individuelles."""
    return False

@router.get("/id/{product_id}/can-modify")
async def can_modify_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Vérifier si un produit peut être modifié (toujours True maintenant)"""
    try:
        product = db.query(Product).filter(Product.product_id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Les produits peuvent toujours être modifiés maintenant
        return {
            "product_id": product_id,
            "can_modify": True,
            "is_used_in_transactions": False
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la vérification de modification du produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/id/{product_id}/variants/available")
async def get_available_variants(
    product_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer les variantes disponibles d'un produit"""
    try:
        product = db.query(Product).filter(Product.product_id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        available_variants = db.query(ProductVariant).filter(
            ProductVariant.product_id == product_id,
            ProductVariant.is_sold == False
        ).all()
        
        variants_data = []
        for v in available_variants:
            # Récupérer les attributs de la variante
            attributes = db.query(ProductVariantAttribute).filter(
                ProductVariantAttribute.variant_id == v.variant_id
            ).all()
            
            variant_data = {
                "variant_id": v.variant_id,
                "imei_serial": v.imei_serial,
                "barcode": v.barcode,
                "condition": v.condition,
                "is_sold": v.is_sold,
                "attributes": [
                    {
                        "attribute_name": attr.attribute_name,
                        "attribute_value": attr.attribute_value
                    }
                    for attr in attributes
                ]
            }
            variants_data.append(variant_data)
        
        return {
            "product_id": product_id,
            "product_name": product.name,
            "available_variants": variants_data
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la récupération des variantes disponibles: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/id/{product_id}/variants/sold")
async def get_sold_variants(
    product_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer les variantes vendues d'un produit"""
    try:
        product = db.query(Product).filter(Product.product_id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        sold_variants = db.query(ProductVariant).filter(
            ProductVariant.product_id == product_id,
            ProductVariant.is_sold == True
        ).all()
        
        return {
            "product_id": product_id,
            "sold_variants": [
                {
                    "variant_id": v.variant_id,
                    "imei_serial": v.imei_serial,
                    "barcode": v.barcode,
                    "condition": v.condition,
                    "is_sold": v.is_sold
                }
                for v in sold_variants
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la récupération des variantes vendues: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/id/{product_id}/sales/invoices-by-serial")
async def get_product_invoices_by_serial(
    product_id: int,
    imei: Optional[str] = Query(None, description="IMEI/numéro de série de la variante"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Retourner les factures liées à un produit (et éventuellement à un IMEI précis).

    Stratégie:
    - On s'appuie sur la table DailySale qui contient product_id, variant_imei et invoice_id.
    - On renvoie une liste d'objets facture légère (id, numéro, client, date, total) ordonnée
      par date de vente décroissante.
    """
    try:
        # Vérifier que le produit existe
        product = db.query(Product).filter(Product.product_id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        base_q = (
            db.query(DailySale, Invoice, Client.name.label("client_name"))
            .join(Invoice, DailySale.invoice_id == Invoice.invoice_id, isouter=True)
            .join(Client, Client.client_id == Invoice.client_id, isouter=True)
            .filter(DailySale.product_id == product_id)
        )

        q = base_q
        if imei:
            imei_clean = imei.strip()
            if imei_clean:
                q = q.filter(func.trim(DailySale.variant_imei) == imei_clean)

        rows = (
            q.order_by(DailySale.sale_date.desc(), DailySale.sale_id.desc())
            .limit(50)
            .all()
        )

        # Si aucune vente trouvée pour cet IMEI (ex: anciennes factures sans variant_imei enregistré),
        # retomber sur toutes les ventes du produit pour ne pas renvoyer une liste vide.
        if not rows:
            rows = (
                base_q.order_by(DailySale.sale_date.desc(), DailySale.sale_id.desc())
                .limit(50)
                .all()
            )

        # Dédoublonner par facture
        seen_ids: set[int] = set()
        invoices_data: list[dict] = []
        for sale, inv, client_name in rows:
            if not inv:
                continue
            if inv.invoice_id in seen_ids:
                continue
            seen_ids.add(inv.invoice_id)
            invoices_data.append(
                {
                    "invoice_id": inv.invoice_id,
                    "invoice_number": inv.invoice_number,
                    "client_id": inv.client_id,
                    "client_name": client_name or "",
                    "date": inv.date,
                    "total": float(inv.total or 0),
                    "remaining_amount": float(inv.remaining_amount or 0),
                    "status": inv.status,
                    "sale_date": sale.sale_date,
                }
            )

        return {
            "product_id": product_id,
            "imei": imei,
            "invoices": invoices_data,
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la récupération des factures liées au produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# Cache simple pour accélérer les endpoints produits (similaire au dashboard)
_cache = {}
_cache_duration = 300  # 5 minutes

from datetime import datetime


def _get_cache_key(*args):
    return "|".join(str(arg) for arg in args)


def _is_cache_valid(entry):
    return entry and (time.time() - entry.get('timestamp', 0)) < _cache_duration


def _get_cached_or_compute(cache_key: str, compute_func):
    if cache_key in _cache and _is_cache_valid(_cache[cache_key]):
        return _cache[cache_key]['data']
    result = compute_func()
    _cache[cache_key] = {"data": result, "timestamp": time.time()}
    return result

# Modèles Pydantic pour les catégories
class CategoryBase(BaseModel):
    name: str
    requires_variants: bool = False

class CategoryCreate(CategoryBase):
    pass

class CategoryUpdate(CategoryBase):
    pass

class CategoryResponse(CategoryBase):
    id: str
    product_count: int
    
    class Config:
        from_attributes = True

# =====================
# Conditions (état des produits)
# =====================

DEFAULT_CONDITIONS = ["neuf", "occasion", "venant"]
DEFAULT_CONDITION_KEY = "product_conditions"

def _ensure_condition_columns(db: Session):
    """Ajoute les colonnes condition aux tables si absentes (sans Alembic)."""
    try:
        bind = db.get_bind()
        dialect = bind.dialect.name
        if dialect == 'sqlite':
            # products
            res = db.execute(text("PRAGMA table_info(products)"))
            prod_cols = [row[1] for row in res]
            if 'condition' not in prod_cols:
                db.execute(text("ALTER TABLE products ADD COLUMN condition VARCHAR(50)"))
                db.commit()
            # product_variants
            res2 = db.execute(text("PRAGMA table_info(product_variants)"))
            var_cols = [row[1] for row in res2]
            if 'condition' not in var_cols:
                db.execute(text("ALTER TABLE product_variants ADD COLUMN condition VARCHAR(50)"))
                db.commit()
        else:
            # PostgreSQL: vérifier si les colonnes existent avant de les ajouter
            try:
                # Vérifier si la colonne condition existe dans products
                result = db.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'products' AND column_name = 'condition'"
                ))
                if not result.fetchone():
                    db.execute(text("ALTER TABLE products ADD COLUMN condition VARCHAR(50)"))
                
                # Vérifier si la colonne condition existe dans product_variants  
                result2 = db.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'product_variants' AND column_name = 'condition'"
                ))
                if not result2.fetchone():
                    db.execute(text("ALTER TABLE product_variants ADD COLUMN condition VARCHAR(50)"))
                
                db.commit()
            except Exception as e:
                db.rollback()
                logging.error(f"Erreur lors de l'ajout des colonnes condition: {e}")
    except Exception as e:
        logging.error(f"Erreur dans _ensure_condition_columns: {e}")

def _get_allowed_conditions(db: Session) -> dict:
    """Retourne {options: [...], default: str}. Stocké dans UserSettings (global)."""
    setting = db.query(UserSettings).filter(
        UserSettings.user_id.is_(None), UserSettings.setting_key == DEFAULT_CONDITION_KEY
    ).first()
    import json
    if setting and setting.setting_value:
        try:
            data = json.loads(setting.setting_value)
            options = data.get("options") or DEFAULT_CONDITIONS
            default = data.get("default") or options[0]
            return {"options": options, "default": default}
        except Exception:
            pass
    return {"options": DEFAULT_CONDITIONS, "default": DEFAULT_CONDITIONS[0]}

def _set_allowed_conditions(db: Session, options: list[str], default_value: str):
    import json
    payload = json.dumps({"options": options, "default": default_value}, ensure_ascii=False)
    setting = db.query(UserSettings).filter(
        UserSettings.user_id.is_(None), UserSettings.setting_key == DEFAULT_CONDITION_KEY
    ).first()
    if not setting:
        setting = UserSettings(user_id=None, setting_key=DEFAULT_CONDITION_KEY, setting_value=payload)
    else:
        setting.setting_value = payload
    db.add(setting)
    db.commit()

class ConditionsUpdate(BaseModel):
    options: List[str]
    default: Optional[str] = None

@router.get("/settings/conditions", tags=["settings"])
async def get_conditions_settings(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    _ensure_condition_columns(db)
    return _get_allowed_conditions(db)

@router.put("/settings/conditions", tags=["settings"])
async def update_conditions_settings(payload: ConditionsUpdate, db: Session = Depends(get_db), current_user = Depends(require_role("admin"))):
    _ensure_condition_columns(db)
    options = [o.strip() for o in (payload.options or []) if o and o.strip()]
    if not options:
        raise HTTPException(status_code=400, detail="La liste des états ne peut pas être vide")
    default_value = (payload.default or options[0]).strip()
    if default_value not in options:
        options.insert(0, default_value)
    _set_allowed_conditions(db, options, default_value)
    return {"options": options, "default": default_value}

@router.get("/", response_model=List[ProductResponse])
async def list_products(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    category: Optional[str] = None,
    condition: Optional[str] = None,
    in_stock: Optional[bool] = None,
    has_variants: Optional[bool] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    brand: Optional[str] = None,
    model: Optional[str] = None,
    has_barcode: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les produits avec recherche et filtres"""
    _ensure_condition_columns(db)
    query = db.query(Product)
    
    if search:
        # Recherche dans nom, description, marque, modèle et codes-barres (produit et variantes)
        search_filter = or_(
            Product.name.ilike(f"%{search}%"),
            Product.description.ilike(f"%{search}%"),
            Product.brand.ilike(f"%{search}%"),
            Product.model.ilike(f"%{search}%"),
            Product.barcode.ilike(f"%{search}%")
        )
        
        # Recherche aussi dans les codes-barres ou IMEI/séries des variantes
        variant_search = db.query(ProductVariant.product_id).filter(
            or_(
                ProductVariant.barcode.ilike(f"%{search}%"),
                ProductVariant.imei_serial.ilike(f"%{search}%")
            )
        ).subquery()
        
        query = query.filter(
            or_(
                search_filter,
                Product.product_id.in_(variant_search)
            )
        )
    
    if category:
        query = query.filter(Product.category == category)

    if condition:
        # Comparaison insensible à la casse et aux espaces pour produit ET variantes
        condition_lower = condition.strip().lower()
        
        # Sous-requête pour les variantes ayant cette condition
        variant_condition_subquery = db.query(ProductVariant.product_id).filter(
            func.lower(func.trim(ProductVariant.condition)) == condition_lower
        )
        
        # Filtrer les produits qui ont soit la condition au niveau produit, soit des variantes avec cette condition
        query = query.filter(
            or_(
                func.lower(func.trim(Product.condition)) == condition_lower,
                Product.product_id.in_(variant_condition_subquery)
            )
        )

    if min_price is not None:
        query = query.filter(Product.price >= Decimal(min_price))
    if max_price is not None:
        query = query.filter(Product.price <= Decimal(max_price))

    if brand:
        query = query.filter(Product.brand.ilike(f"%{brand}%"))
    if model:
        query = query.filter(Product.model.ilike(f"%{model}%"))

    if has_barcode is True:
        query = query.filter(Product.barcode.isnot(None), func.length(func.trim(Product.barcode)) > 0)
    elif has_barcode is False:
        query = query.filter(or_(Product.barcode.is_(None), func.length(func.trim(Product.barcode)) == 0))

    # Existence-based filters
    pv_exists_available = exists().where(and_(ProductVariant.product_id == Product.product_id, ProductVariant.is_sold == False))
    pv_exists_any = exists().where(ProductVariant.product_id == Product.product_id)
    if in_stock is True:
        query = query.filter(or_(Product.quantity > 0, pv_exists_available))
    elif in_stock is False:
        query = query.filter(and_(Product.quantity <= 0, ~pv_exists_available))

    if has_variants is True:
        query = query.filter(pv_exists_any)
    elif has_variants is False:
        query = query.filter(~pv_exists_any)
    
    # Tri par défaut: dernier produit ajouté en haut
    query = query.order_by(Product.created_at.desc())
    
    products = query.offset(skip).limit(limit).all()
    # Mask purchase_price for non-manager/admin
    try:
        role = getattr(current_user, "role", "user")
        if role not in ("admin", "manager"):
            for p in (products or []):
                try:
                    p.purchase_price = Decimal(0)
                except Exception:
                    pass
    except Exception:
        pass
    # Si un filtre de condition est actif, ne retourner que les variantes correspondant à cette condition
    if condition:
        cond_lower = (condition or "").strip().lower()
        for p in products:
            try:
                _ = p.variants  # force load
                p.variants = [v for v in (p.variants or []) if ((v.condition or "").strip().lower() == cond_lower)]
            except Exception:
                pass
    return products

class PaginatedProductsResponse(BaseModel):
    items: List[ProductListItem]
    total: int

@router.get("/paginated", response_model=PaginatedProductsResponse)
async def list_products_paginated(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: Optional[str] = None,
    category: Optional[str] = None,
    condition: Optional[str] = None,
    in_stock: Optional[bool] = None,
    has_variants: Optional[bool] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    brand: Optional[str] = None,
    model: Optional[str] = None,
    has_barcode: Optional[bool] = None,
    sort_by: Optional[str] = Query("created_at"),  # name | category | price | stock | barcode | created_at
    sort_dir: Optional[str] = Query("desc"),  # asc | desc
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les produits avec pagination (retourne items + total)."""
    _ensure_condition_columns(db)
    # Eager-load only the necessary columns to speed up list view
    # Note: nous n'incluons plus le selectinload des variantes pour la liste; un résumé sera calculé séparément
    base_query = (
        db.query(Product)
        .options(
            load_only(
                Product.product_id,
                Product.name,
                Product.description,
                Product.quantity,
                Product.price,
                Product.purchase_price,
                Product.category,
                Product.brand,
                Product.model,
                Product.barcode,
                Product.condition,
                Product.has_unique_serial,
                Product.entry_date,
                Product.notes,
                Product.image_path,
                Product.created_at,
            )
        )
    )

    if search:
        search_filter = or_(
            Product.name.ilike(f"%{search}%"),
            Product.description.ilike(f"%{search}%"),
            Product.brand.ilike(f"%{search}%"),
            Product.model.ilike(f"%{search}%"),
            Product.barcode.ilike(f"%{search}%")
        )
        variant_search = db.query(ProductVariant.product_id).filter(
            or_(
                ProductVariant.barcode.ilike(f"%{search}%"),
                ProductVariant.imei_serial.ilike(f"%{search}%")
            )
        ).subquery()
        base_query = base_query.filter(or_(search_filter, Product.product_id.in_(variant_search)))

    if category:
        base_query = base_query.filter(Product.category == category)

    if condition:
        # Comparaison insensible à la casse et aux espaces pour produit ET variantes
        condition_lower = condition.strip().lower()
        
        # Sous-requête pour les variantes ayant cette condition
        variant_condition_subquery = db.query(ProductVariant.product_id).filter(
            func.lower(func.trim(ProductVariant.condition)) == condition_lower
        )
        
        # Filtrer les produits qui ont soit la condition au niveau produit, soit des variantes avec cette condition
        base_query = base_query.filter(
            or_(
                func.lower(func.trim(Product.condition)) == condition_lower,
                Product.product_id.in_(variant_condition_subquery)
            )
        )

    if min_price is not None:
        base_query = base_query.filter(Product.price >= Decimal(min_price))
    if max_price is not None:
        base_query = base_query.filter(Product.price <= Decimal(max_price))

    if brand:
        base_query = base_query.filter(Product.brand.ilike(f"%{brand}%"))
    if model:
        base_query = base_query.filter(Product.model.ilike(f"%{model}%"))

    if has_barcode is True:
        base_query = base_query.filter(Product.barcode.isnot(None), func.length(func.trim(Product.barcode)) > 0)
    elif has_barcode is False:
        base_query = base_query.filter(or_(Product.barcode.is_(None), func.length(func.trim(Product.barcode)) == 0))

    pv_exists_available = exists().where(and_(ProductVariant.product_id == Product.product_id, ProductVariant.is_sold == False))
    pv_exists_any = exists().where(ProductVariant.product_id == Product.product_id)
    if in_stock is True:
        base_query = base_query.filter(or_(Product.quantity > 0, pv_exists_available))
    elif in_stock is False:
        base_query = base_query.filter(and_(Product.quantity <= 0, ~pv_exists_available))

    if has_variants is True:
        base_query = base_query.filter(pv_exists_any)
    elif has_variants is False:
        base_query = base_query.filter(~pv_exists_any)

    # Prepare join for stock sorting: available variants per product (non sold)
    available_variants_sub = (
        db.query(
            ProductVariant.product_id.label('product_id'),
            func.sum(case((ProductVariant.is_sold == False, 1), else_=0)).label('available')
        )
        .group_by(ProductVariant.product_id)
        .subquery()
    )
    base_query = base_query.outerjoin(available_variants_sub, available_variants_sub.c.product_id == Product.product_id)

    # Apply ordering
    sort_key = (sort_by or "name").strip().lower()
    sort_dir_key = (sort_dir or "asc").strip().lower()
    dir_desc = sort_dir_key == 'desc'
    stock_expr = func.coalesce(available_variants_sub.c.available, Product.quantity)

    if sort_key == 'price':
        order_expr = Product.price.desc() if dir_desc else Product.price.asc()
    elif sort_key == 'category':
        order_expr = Product.category.desc() if dir_desc else Product.category.asc()
    elif sort_key == 'barcode':
        order_expr = Product.barcode.desc() if dir_desc else Product.barcode.asc()
    elif sort_key == 'stock':
        order_expr = stock_expr.desc() if dir_desc else stock_expr.asc()
    elif sort_key == 'created_at':
        order_expr = Product.created_at.desc() if dir_desc else Product.created_at.asc()
    else:  # name (default)
        order_expr = Product.name.desc() if dir_desc else Product.name.asc()

    base_query = base_query.order_by(order_expr, Product.product_id.asc())

    start_time = time.time()
    # Calculer le total AVANT les jointures/tri pour de meilleures perfs
    filtered_query = base_query
    total = filtered_query.count()
    count_time = time.time()
    logging.info(f"Product count (filtered) took: {count_time - start_time:.4f} seconds")

    skip = (page - 1) * page_size
    items = base_query.offset(skip).limit(page_size).all()
    # Mask purchase_price for non-manager/admin
    try:
        role = getattr(current_user, "role", "user")
        if role not in ("admin", "manager"):
            for p in (items or []):
                try:
                    p.purchase_price = Decimal(0)
                except Exception:
                    pass
    except Exception:
        pass

    # Calcul d'un résumé variantes pour les produits affichés (un seul aller-retour DB)
    product_ids = [p.product_id for p in items]
    variant_summary_map = {}
    if product_ids:
        # Ensemble des produits qui ont au moins une variante (vendue ou non)
        variant_any_rows = (
            db.query(ProductVariant.product_id)
            .filter(ProductVariant.product_id.in_(product_ids))
            .group_by(ProductVariant.product_id)
            .all()
        )
        has_variants_set = {r[0] for r in variant_any_rows}

        # Compte des variantes disponibles et répartition par condition (uniquement non vendues)
        rows = (
            db.query(
                ProductVariant.product_id,
                func.lower(func.coalesce(func.trim(ProductVariant.condition), '')).label('cond_key'),
                func.count(ProductVariant.variant_id).label('available_by_condition')
            )
            .filter(
                ProductVariant.product_id.in_(product_ids),
                ProductVariant.is_sold == False
            )
            .group_by(
                ProductVariant.product_id,
                ProductVariant.condition
            )
            .all()
        )
        # Agréger par produit
        for pid in product_ids:
            variant_summary_map[pid] = {
                'has_variants': pid in has_variants_set,
                'available': 0,
                'by_condition': {}
            }
        for pid, cond_key, available_by_cond in rows:
            entry = variant_summary_map.get(pid)
            if entry is None:
                entry = {'has_variants': pid in has_variants_set, 'available': 0, 'by_condition': {}}
                variant_summary_map[pid] = entry
            key = (cond_key or '').strip() or 'inconnu'
            count_val = int(available_by_cond or 0)
            entry['by_condition'][key] = int((entry['by_condition'].get(key, 0)) + count_val)
            entry['available'] = entry['available'] + count_val

    # Injecter les champs légers dans les objets Product renvoyés (les pydantic ProductListItem les acceptera)
    for p in items:
        try:
            sum_entry = variant_summary_map.get(p.product_id)
            p.has_variants = bool(sum_entry.get('has_variants')) if sum_entry else False
            p.variants_available = int(sum_entry.get('available', 0)) if sum_entry else 0
            p.variant_condition_counts = sum_entry.get('by_condition', {}) if sum_entry else {}
            # éviter de renvoyer toutes les variantes pour la liste
            if hasattr(p, 'variants'):
                p.variants = []
        except Exception:
            pass

    fetch_time = time.time()
    logging.info(f"Product query fetch took: {fetch_time - count_time:.4f} seconds")
    logging.info(f"Total paginated request took: {fetch_time - start_time:.4f} seconds")

    return {"items": items, "total": total}

@router.get("/id/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir un produit par ID"""
    _ensure_condition_columns(db)
    product = db.query(Product).filter(Product.product_id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    # Mask purchase_price for non-manager/admin
    try:
        role = getattr(current_user, "role", "user")
        if role not in ("admin", "manager") and product is not None:
            product.purchase_price = Decimal(0)
    except Exception:
        pass
    return product

@router.post("/", response_model=ProductResponse)
async def create_product(
    product_data: ProductCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_any_role(["user", "manager"]))
):
    """Créer un nouveau produit avec variantes selon la règle métier"""
    try:
        print(f"🔍 Received product data: {product_data}")
        print(f"🔍 Product data dict: {product_data.dict()}")
        print(f"🔍 Variants: {product_data.variants}")
        _ensure_condition_columns(db)
        cond_cfg = _get_allowed_conditions(db)
        allowed = set([c.lower() for c in cond_cfg["options"]])
        default_cond = cond_cfg["default"]
        # Validation selon la règle métier des mémoires
        has_variants = len(product_data.variants) > 0
        
        # Normaliser le code-barres produit (autorisé même si variantes, il sert de code partagé)
        normalized_barcode = None
        if product_data.barcode is not None:
            bc = (product_data.barcode or "").strip()
            normalized_barcode = bc or None
        
        # Vérifier l'unicité du code-barres produit (global: produits + variantes)
        if normalized_barcode:
            exists_prod = db.query(Product).filter(Product.barcode == normalized_barcode).first()
            exists_var = db.query(ProductVariant).filter(ProductVariant.barcode == normalized_barcode).first()
            if exists_prod or exists_var:
                raise HTTPException(status_code=400, detail="Ce code-barres existe déjà")
        
        # Normaliser et contrôler les variantes
        variant_barcodes = []
        variant_serials = []
        normalized_variants = []
        for v in (product_data.variants or []):
            v_barcode = (v.barcode or "").strip() if getattr(v, 'barcode', None) is not None else None
            v_barcode = v_barcode or None
            v_serial = (v.imei_serial or "").strip()
            if not v_serial:
                raise HTTPException(status_code=400, detail="Chaque variante doit avoir un IMEI/numéro de série")
            normalized_variants.append({
                'imei_serial': v_serial,
                'barcode': v_barcode,
                'condition': (getattr(v, 'condition', None) or (product_data.condition or default_cond))
            })
            if v_barcode:
                variant_barcodes.append(v_barcode)
            variant_serials.append(v_serial)
        # Duplicates dans payload
        if len(set(variant_barcodes)) != len(variant_barcodes):
            raise HTTPException(status_code=400, detail="Codes-barres de variantes en double dans la demande")
        if len(set(variant_serials)) != len(variant_serials):
            raise HTTPException(status_code=400, detail="IMEI/numéros de série en double dans la demande")
        # Unicité globale pour variantes
        if variant_barcodes:
            exists_var_barcodes = db.query(ProductVariant).filter(ProductVariant.barcode.in_(variant_barcodes)).all()
            exists_prod_barcodes = db.query(Product).filter(Product.barcode.in_(variant_barcodes)).all()
            if exists_var_barcodes or exists_prod_barcodes:
                raise HTTPException(status_code=400, detail="Un ou plusieurs codes-barres de variantes existent déjà")
        if variant_serials:
            exists_serials = db.query(ProductVariant).filter(ProductVariant.imei_serial.in_(variant_serials)).all()
            if exists_serials:
                raise HTTPException(status_code=400, detail="Un ou plusieurs IMEI/numéros de série existent déjà")
        
        # Créer le produit
        # Normaliser/valider condition produit
        prod_condition = (product_data.condition or default_cond)
        if prod_condition and prod_condition.lower() not in allowed:
            raise HTTPException(status_code=400, detail="Condition de produit invalide")

        db_product = Product(
            name=product_data.name,
            description=product_data.description,
            quantity=len(normalized_variants) if has_variants else product_data.quantity,
            price=product_data.price,
            wholesale_price=product_data.wholesale_price,
            purchase_price=product_data.purchase_price,
            category=product_data.category,
            brand=product_data.brand,
            model=product_data.model,
            barcode=normalized_barcode,
            condition=prod_condition,
            has_unique_serial=product_data.has_unique_serial,
            entry_date=product_data.entry_date,
            notes=product_data.notes
        )
        
        db.add(db_product)
        db.flush()  # Pour obtenir l'ID du produit
        
        # Créer les variantes si présentes
        for nv in normalized_variants:
            db_variant = ProductVariant(
                product_id=db_product.product_id,
                imei_serial=nv['imei_serial'],
                barcode=nv['barcode'],
                condition=nv['condition']
            )
            db.add(db_variant)
            db.flush()
            
        # Créer les attributs de la variante (si présents dans payload d'origine)
        for db_v, orig_v in zip(db_product.variants, (product_data.variants or [])):
            for attr_data in getattr(orig_v, 'attributes', []) or []:
                db_attr = ProductVariantAttribute(
                    variant_id=db_v.variant_id,
                    attribute_name=attr_data.attribute_name,
                    attribute_value=attr_data.attribute_value
                )
                db.add(db_attr)
        
        # Créer un mouvement de stock d'entrée
        if db_product.quantity > 0:
            stock_movement = StockMovement(
                product_id=db_product.product_id,
                quantity=db_product.quantity,
                movement_type="IN",
                reference_type="CREATION",
                notes="Création du produit"
            )
            db.add(stock_movement)
        
        db.commit()
        db.refresh(db_product)
        
        return db_product
        
    except HTTPException:
        raise
    except IntegrityError as ie:
        db.rollback()
        # Essayer de mapper les erreurs d'unicité en 400
        msg = str(getattr(ie, 'orig', ie))
        if 'unique' in msg.lower() or 'duplicate key value' in msg.lower():
            raise HTTPException(status_code=400, detail="Violation d'unicité (code-barres ou IMEI déjà utilisé)")
        logging.error(f"Erreur d'intégrité lors de la création du produit: {ie}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la création du produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/id/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    product_data: ProductUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_any_role(["manager"]))
):
    """Mettre à jour un produit"""
    try:
        _ensure_condition_columns(db)
        cond_cfg = _get_allowed_conditions(db)
        allowed = set([c.lower() for c in cond_cfg["options"]])
        default_cond = cond_cfg["default"]
        product = db.query(Product).filter(Product.product_id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Vérifier si le produit est utilisé dans des transactions
        if is_product_used_in_transactions(db, product_id):
            raise HTTPException(
                status_code=400, 
                detail="Ce produit ne peut pas être modifié car il est déjà utilisé dans des factures, devis ou bons de livraison"
            )
        
        # Validation selon la règle métier
        has_variants = len(product.variants) > 0
        new_variants = product_data.variants if product_data.variants is not None else []
        will_have_variants = len(new_variants) > 0 or has_variants
        
        # Normaliser barcode produit reçu: trim -> None si vide
        incoming_barcode = None
        if product_data.barcode is not None:
            bc = (product_data.barcode or "").strip()
            incoming_barcode = bc or None
        
        # Règle: si le produit a/va avoir des variantes, interdire le code-barres produit
        if will_have_variants and incoming_barcode:
            raise HTTPException(
                status_code=400,
                detail="Un produit avec variantes ne peut pas avoir de code-barres"
            )
        
        # Préparer les données à mettre à jour (sans variants)
        update_data = product_data.dict(exclude_unset=True, exclude={'variants'})
        
        # Normaliser et valider la condition si fournie
        if 'condition' in update_data and update_data['condition'] is not None:
            if update_data['condition'].lower() not in allowed:
                raise HTTPException(status_code=400, detail="Condition de produit invalide")
        
        # Normaliser barcode côté update_data
        if 'barcode' in update_data:
            update_data['barcode'] = None if will_have_variants else incoming_barcode
        
        # Vérifier l'unicité du code-barres produit si fourni et modifié
        if update_data.get('barcode'):
            existing_product = (
                db.query(Product)
                .filter(Product.barcode == update_data['barcode'], Product.product_id != product_id)
                .first()
            )
            if existing_product:
                raise HTTPException(status_code=400, detail="Ce code-barres existe déjà")
        
        # Appliquer les mises à jour champ par champ
        for field, value in update_data.items():
            # Normaliser les chaînes vides en None pour éviter les contraintes d'unicité sur ''
            if isinstance(value, str):
                value = value.strip()
                if value == "":
                    value = None
            setattr(product, field, value)
        
        # Gérer les variantes si fournies
        if product_data.variants is not None:
            # Normaliser les données variantes et préparer des listes pour validation
            norm_variants = []
            variant_barcodes = []
            variant_serials = []
            for v in (product_data.variants or []):
                v_barcode = (v.barcode or "").strip() if getattr(v, 'barcode', None) is not None else None
                v_barcode = v_barcode or None
                v_serial = (v.imei_serial or "").strip()
                if not v_serial:
                    raise HTTPException(status_code=400, detail="Chaque variante doit avoir un IMEI/numéro de série")
                norm_variants.append({
                    'imei_serial': v_serial,
                    'barcode': v_barcode,
                    'condition': (getattr(v, 'condition', None) or product.condition or default_cond)
                })
                if v_barcode:
                    variant_barcodes.append(v_barcode)
                variant_serials.append(v_serial)
            
            # Détecter doublons dans le payload
            if len(set(variant_barcodes)) != len(variant_barcodes):
                raise HTTPException(status_code=400, detail="Codes-barres de variantes en double dans la demande")
            if len(set(variant_serials)) != len(variant_serials):
                raise HTTPException(status_code=400, detail="IMEI/numéros de série en double dans la demande")
            
            # Vérifier unicité globale (hors variantes de ce produit)
            if variant_barcodes:
                existing_variants = db.query(ProductVariant).filter(
                    ProductVariant.barcode.in_(variant_barcodes),
                    ProductVariant.product_id != product_id
                ).all()
                if existing_variants:
                    raise HTTPException(status_code=400, detail="Un ou plusieurs codes-barres de variantes existent déjà")
            existing_serials = db.query(ProductVariant).filter(
                ProductVariant.imei_serial.in_(variant_serials),
                ProductVariant.product_id != product_id
            ).all()
            if existing_serials:
                raise HTTPException(status_code=400, detail="Un ou plusieurs IMEI/numéros de série existent déjà")
            
            # Récupérer les variantes existantes avec leur statut de vente
            existing_variants = {v.imei_serial: v for v in product.variants}
            
            # Identifier les variantes à supprimer (celles qui ne sont plus dans la nouvelle liste ET non vendues)
            new_imeis = {nv['imei_serial'] for nv in norm_variants}
            variants_to_delete = []
            for imei, variant in existing_variants.items():
                if imei not in new_imeis and not variant.is_sold:
                    # Seulement supprimer les variantes non vendues
                    variants_to_delete.append(variant)
                elif imei not in new_imeis and variant.is_sold:
                    # Les variantes vendues sont préservées même si elles ne sont pas dans la nouvelle liste
                    pass
            
            # Supprimer uniquement les variantes non vendues
            for variant in variants_to_delete:
                try:
                    db.delete(variant)
                except Exception:
                    pass
            db.flush()

            # Créer ou mettre à jour les variantes
            imei_to_db_variant = {}
            for nv in norm_variants:
                imei = nv['imei_serial']
                if imei in existing_variants:
                    # Mettre à jour la variante existante (préserver is_sold)
                    db_variant = existing_variants[imei]
                    db_variant.barcode = nv['barcode']
                    db_variant.condition = nv['condition']
                    # Ne pas modifier is_sold - il est préservé
                else:
                    # Créer une nouvelle variante
                    db_variant = ProductVariant(
                        product_id=product_id,
                        imei_serial=nv['imei_serial'],
                        barcode=nv['barcode'],
                        condition=nv['condition'],
                        is_sold=False  # Nouvelle variante = non vendue
                    )
                    db.add(db_variant)
                    db.flush()  # pour obtenir variant_id immédiatement
                
                imei_to_db_variant[str(nv['imei_serial']).strip()] = db_variant

            # Supprimer tous les anciens attributs des variantes existantes
            for db_variant in imei_to_db_variant.values():
                db.query(ProductVariantAttribute).filter(
                    ProductVariantAttribute.variant_id == db_variant.variant_id
                ).delete()
            
            # Attacher les nouveaux attributs aux bonnes variantes en se basant sur l'IMEI
            for orig_v in (product_data.variants or []):
                try:
                    orig_imei = str(getattr(orig_v, 'imei_serial', '')).strip()
                    if not orig_imei:
                        continue
                    db_v = imei_to_db_variant.get(orig_imei)
                    if not db_v:
                        continue
                    for attr_data in (getattr(orig_v, 'attributes', []) or []):
                        db_attr = ProductVariantAttribute(
                            variant_id=db_v.variant_id,
                            attribute_name=attr_data.attribute_name,
                            attribute_value=attr_data.attribute_value
                        )
                        db.add(db_attr)
                except Exception:
                    # Ne pas bloquer la mise à jour si un attribut est mal formé
                    pass
            
            # Mettre à jour la quantité basée sur les variantes non vendues uniquement
            available_variants = db.query(ProductVariant).filter(
                ProductVariant.product_id == product_id,
                ProductVariant.is_sold == False
            ).count()
            product.quantity = available_variants
            # S'assurer que le code-barres produit est None si variantes
            product.barcode = None
        
        db.commit()
        db.refresh(product)
        
        return product
        
    except HTTPException:
        raise
    except IntegrityError as ie:
        db.rollback()
        msg = str(getattr(ie, 'orig', ie))
        if 'unique' in msg.lower() or 'duplicate key value' in msg.lower():
            raise HTTPException(status_code=400, detail="Violation d'unicité (code-barres ou IMEI déjà utilisé)")
        logging.error(f"Erreur d'intégrité lors de la mise à jour du produit: {ie}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la mise à jour du produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.delete("/id/{product_id}")
async def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_any_role(["manager"]))
):
    """Supprimer un produit"""
    try:
        product = db.query(Product).filter(Product.product_id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Vérifier si le produit est utilisé dans des transactions
        if is_product_used_in_transactions(db, product_id):
            raise HTTPException(
                status_code=400, 
                detail="Ce produit ne peut pas être supprimé car il est déjà utilisé dans des factures, devis ou bons de livraison"
            )
        
        # Créer un mouvement de stock de sortie pour traçabilité
        if product.quantity > 0:
            stock_movement = StockMovement(
                product_id=product_id,
                quantity=-product.quantity,
                movement_type="OUT",
                reference_type="DELETION",
                notes=f"Suppression du produit: {product.name}"
            )
            db.add(stock_movement)
        
        db.delete(product)
        db.commit()
        
        return {"message": "Produit supprimé avec succès"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la suppression du produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/scan/{barcode}")
async def scan_barcode(
    barcode: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Scanner un code-barres (produit ou variante) et retourner un objet JSON simple.

    Recherche sur:
    - `products.barcode`
    - `product_variants.barcode`
    - `product_variants.imei_serial`
    Les espaces en trop sont ignorés.
    """
    try:
        code = (barcode or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="Code-barres vide")

        # 1) Produit par code-barres exact (trim)
        product = (
            db.query(Product)
            .filter(func.trim(Product.barcode) == code)
            .first()
        )
        if product:
            return {
                "type": "product",
                "product_id": product.product_id,
                "product_name": product.name,
                "price": float(product.price or 0),
                "category_name": product.category,
                "stock_quantity": int(product.quantity or 0),
                "barcode": product.barcode
            }

        # 2) Variante par code-barres ou IMEI/série
        variant = (
            db.query(ProductVariant)
            .join(Product)
            .filter(
                or_(
                    func.trim(ProductVariant.barcode) == code,
                    func.trim(ProductVariant.imei_serial) == code
                )
            )
            .first()
        )
        if variant:
            # Charger les attributs
            _ = variant.attributes  # force load
            attributes_text = ", ".join(
                [f"{a.attribute_name}: {a.attribute_value}" for a in (variant.attributes or [])]
            )
            return {
                "type": "variant",
                "product_id": variant.product.product_id,
                "product_name": variant.product.name,
                "price": float(variant.product.price or 0),
                "category_name": variant.product.category,
                "stock_quantity": 0 if variant.is_sold else 1,
                "variant": {
                    "variant_id": variant.variant_id,
                    "imei_serial": variant.imei_serial,
                    "barcode": variant.barcode,
                    "is_sold": bool(variant.is_sold),
                    "attributes": attributes_text
                }
            }

        # 3) Recherche partielle (fallback) sur produits et variantes
        # Utile quand le code scanné a des préfixes/suffixes ou quand on veut matcher IMEI partiel
        like_code = f"%{code}%"
        variant_like = (
            db.query(ProductVariant)
            .join(Product)
            .filter(
                or_(
                    ProductVariant.barcode.ilike(like_code),
                    ProductVariant.imei_serial.ilike(like_code)
                )
            )
            .first()
        )
        if variant_like:
            _ = variant_like.attributes
            attributes_text = ", ".join(
                [f"{a.attribute_name}: {a.attribute_value}" for a in (variant_like.attributes or [])]
            )
            return {
                "type": "variant",
                "product_id": variant_like.product.product_id,
                "product_name": variant_like.product.name,
                "price": float(variant_like.product.price or 0),
                "category_name": variant_like.product.category,
                "stock_quantity": 0 if variant_like.is_sold else 1,
                "variant": {
                    "variant_id": variant_like.variant_id,
                    "imei_serial": variant_like.imei_serial,
                    "barcode": variant_like.barcode,
                    "is_sold": bool(variant_like.is_sold),
                    "attributes": attributes_text
                }
            }

        product_like = (
            db.query(Product)
            .filter(Product.barcode.ilike(like_code))
            .first()
        )
        if product_like:
            return {
                "type": "product",
                "product_id": product_like.product_id,
                "product_name": product_like.name,
                "price": float(product_like.price or 0),
                "category_name": product_like.category,
                "stock_quantity": int(product_like.quantity or 0),
                "barcode": product_like.barcode
            }

        raise HTTPException(status_code=404, detail="Code-barres non trouvé")

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors du scan: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# ==== GESTION DES CATÉGORIES ====

@router.get("/categories", response_model=List[CategoryResponse])
async def get_categories(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir la liste des catégories avec le nombre de produits associés (mis en cache 5 min)"""
    try:
        cache_key = _get_cache_key("product_categories")

        def compute():
            rows = db.query(
                Category.category_id.label('id'),
                Category.name.label('name'),
                Category.requires_variants.label('requires_variants'),
                func.count(Product.product_id).label('product_count')
            ).outerjoin(
                Product, Category.name == Product.category
            ).group_by(
                Category.category_id, Category.name, Category.requires_variants
            ).all()

            return [
                {
                    "id": str(r.id),
                    "name": str(r.name),
                    "requires_variants": bool(getattr(r, 'requires_variants', False)),
                    "product_count": int(r.product_count or 0),
                }
                for r in rows
            ]

        result = _get_cached_or_compute(cache_key, compute)
        return result
    except Exception as e:
        logging.error(f"Erreur /products/categories: {e}")
        return []

@router.get("/categories/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir une catégorie spécifique avec le nombre de produits associés"""
    # Chercher d'abord la catégorie par ID (numérique) ou par nom (texte)
    category = _category_query_by_identifier(db, category_id).first()
    
    if not category:
        raise HTTPException(status_code=404, detail="Catégorie non trouvée")
    
    # Compter les produits associés
    product_count = db.query(Product).filter(Product.category == category.name).count()
    
    return {
        "id": str(category.category_id),
        "name": category.name,
        "requires_variants": bool(category.requires_variants),
        "product_count": product_count
    }

@router.post("/categories", response_model=CategoryResponse)
async def create_category(
    category_data: CategoryCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Créer une nouvelle catégorie"""
    # Vérifier si la catégorie existe déjà
    existing_category = db.query(Category).filter(
        func.lower(Category.name) == func.lower(category_data.name)
    ).first()
    
    if existing_category:
        raise HTTPException(
            status_code=400,
            detail="Une catégorie avec ce nom existe déjà"
        )
    
    # Créer la nouvelle catégorie
    new_category = Category(
        name=category_data.name,
        description=getattr(category_data, 'description', None),
        requires_variants=bool(getattr(category_data, 'requires_variants', False))
    )
    
    db.add(new_category)
    db.commit()
    db.refresh(new_category)
    
    return {
        "id": str(new_category.category_id),
        "name": new_category.name,
        "requires_variants": bool(new_category.requires_variants),
        "product_count": 0
    }

@router.put("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: str,
    category_data: CategoryUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Mettre à jour une catégorie existante"""
    # Chercher la catégorie par ID (numérique) ou nom (texte)
    category = _category_query_by_identifier(db, category_id).first()
    
    if not category:
        raise HTTPException(status_code=404, detail="Catégorie non trouvée")
    
    # Vérifier si le nouveau nom existe déjà (sauf s'il s'agit du même)
    if category.name.lower() != category_data.name.lower():
        existing_category = db.query(Category).filter(
            func.lower(Category.name) == func.lower(category_data.name)
        ).first()
        
        if existing_category:
            raise HTTPException(
                status_code=400,
                detail="Une catégorie avec ce nom existe déjà"
            )
    
    # Sauvegarder l'ancien nom pour mettre à jour les produits
    old_name = category.name
    
    # Mettre à jour la catégorie
    category.name = category_data.name
    if hasattr(category_data, 'description'):
        category.description = category_data.description
    if hasattr(category_data, 'requires_variants'):
        category.requires_variants = bool(category_data.requires_variants)
    
    # Mettre à jour tous les produits avec cette catégorie
    db.query(Product).filter(Product.category == old_name).update(
        {"category": category_data.name}
    )
    
    db.commit()
    db.refresh(category)
    
    # Compter le nombre de produits dans la catégorie mise à jour
    product_count = db.query(Product).filter(Product.category == category.name).count()
    
    return {
        "id": str(category.category_id),
        "name": category.name,
        "requires_variants": bool(category.requires_variants),
        "product_count": product_count
    }

@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer une catégorie"""
    # Chercher la catégorie par ID (numérique) ou nom (texte)
    category = _category_query_by_identifier(db, category_id).first()
    
    if not category:
        raise HTTPException(status_code=404, detail="Catégorie non trouvée")
    
    # Vérifier si des produits utilisent cette catégorie
    products_with_category = db.query(Product).filter(
        Product.category == category.name
    ).count()
    
    if products_with_category > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Impossible de supprimer la catégorie. {products_with_category} produit(s) l'utilisent encore."
        )
    
    # Supprimer la catégorie
    db.delete(category)
    db.commit()
    
    return {"message": "Catégorie supprimée avec succès"}

# =====================
# Attributs de catégorie
# =====================

def _slugify(text: str) -> str:
    return ''.join(c.lower() if c.isalnum() else '-' for c in text).strip('-')

def _category_query_by_identifier(db: Session, identifier: str):
    """Return a query for `Category` matching either numeric ID or name.

    Avoids Postgres type mismatch (integer vs varchar) by casting in Python,
    not in SQL.
    """
    try:
        # Accept strings like "001" -> 1 as id
        if str(identifier).isdigit():
            return db.query(Category).filter(Category.category_id == int(identifier))
    except Exception:
        pass
    return db.query(Category).filter(Category.name == identifier)

def _category_or_404(db: Session, category_id: str) -> Category:
    category = _category_query_by_identifier(db, category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Catégorie non trouvée")
    return category

@router.get("/categories/{category_id}/attributes", response_model=List[CategoryAttributeResponse])
async def list_category_attributes(
    category_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    category = _category_or_404(db, category_id)
    attrs = db.query(CategoryAttribute).filter(CategoryAttribute.category_id == category.category_id).order_by(CategoryAttribute.sort_order).all()
    # charger les valeurs
    for a in attrs:
        _ = a.values  # load relationship
    return attrs

@router.post("/categories/{category_id}/attributes", response_model=CategoryAttributeResponse)
async def create_category_attribute(
    category_id: str,
    payload: CategoryAttributeCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_role("admin"))
):
    category = _category_or_404(db, category_id)
    code = payload.code or _slugify(payload.name)
    # unicité code par catégorie
    exists = db.query(CategoryAttribute).filter(
        CategoryAttribute.category_id == category.category_id,
        func.lower(CategoryAttribute.code) == func.lower(code)
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="Code d'attribut déjà utilisé pour cette catégorie")
    attr = CategoryAttribute(
        category_id=category.category_id,
        name=payload.name,
        code=code,
        type=payload.type,
        required=bool(payload.required),
        multi_select=bool(payload.multi_select),
        sort_order=payload.sort_order or 0
    )
    db.add(attr)
    db.flush()
    # valeurs initiales
    for i, v in enumerate(payload.values or []):
        vcode = v.code or _slugify(v.value)
        db.add(CategoryAttributeValue(
            attribute_id=attr.attribute_id,
            value=v.value,
            code=vcode,
            sort_order=v.sort_order if v.sort_order is not None else i
        ))
    db.commit()
    db.refresh(attr)
    return attr

@router.put("/categories/{category_id}/attributes/{attribute_id}", response_model=CategoryAttributeResponse)
async def update_category_attribute(
    category_id: str,
    attribute_id: int,
    payload: CategoryAttributeUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_role("admin"))
):
    category = _category_or_404(db, category_id)
    attr = db.query(CategoryAttribute).filter(
        CategoryAttribute.attribute_id == attribute_id,
        CategoryAttribute.category_id == category.category_id
    ).first()
    if not attr:
        raise HTTPException(status_code=404, detail="Attribut non trouvé")
    if payload.name is not None:
        attr.name = payload.name
    if payload.code is not None:
        # vérifier unicité
        exists = db.query(CategoryAttribute).filter(
            CategoryAttribute.category_id == category.category_id,
            func.lower(CategoryAttribute.code) == func.lower(payload.code),
            CategoryAttribute.attribute_id != attr.attribute_id
        ).first()
        if exists:
            raise HTTPException(status_code=400, detail="Code d'attribut déjà utilisé pour cette catégorie")
        attr.code = payload.code
    if payload.type is not None:
        attr.type = payload.type
    if payload.required is not None:
        attr.required = bool(payload.required)
    if payload.multi_select is not None:
        attr.multi_select = bool(payload.multi_select)
    if payload.sort_order is not None:
        attr.sort_order = payload.sort_order
    db.commit()
    db.refresh(attr)
    return attr

@router.delete("/categories/{category_id}/attributes/{attribute_id}")
async def delete_category_attribute(
    category_id: str,
    attribute_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_role("admin"))
):
    category = _category_or_404(db, category_id)
    attr = db.query(CategoryAttribute).filter(
        CategoryAttribute.attribute_id == attribute_id,
        CategoryAttribute.category_id == category.category_id
    ).first()
    if not attr:
        raise HTTPException(status_code=404, detail="Attribut non trouvé")
    # empêcher suppression si utilisé dans des variantes
    in_use = db.query(ProductVariantAttribute).filter(
        func.lower(ProductVariantAttribute.attribute_name) == func.lower(attr.name)
    ).first()
    if in_use:
        raise HTTPException(status_code=400, detail="Attribut utilisé par des variantes, suppression interdite")
    db.delete(attr)
    db.commit()
    return {"message": "Attribut supprimé avec succès"}

@router.post("/categories/{category_id}/attributes/{attribute_id}/values", response_model=CategoryAttributeValueResponse)
async def create_attribute_value(
    category_id: str,
    attribute_id: int,
    payload: CategoryAttributeValueCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_role("admin"))
):
    category = _category_or_404(db, category_id)
    attr = db.query(CategoryAttribute).filter(
        CategoryAttribute.attribute_id == attribute_id,
        CategoryAttribute.category_id == category.category_id
    ).first()
    if not attr:
        raise HTTPException(status_code=404, detail="Attribut non trouvé")
    code = payload.code or _slugify(payload.value)
    exists = db.query(CategoryAttributeValue).filter(
        CategoryAttributeValue.attribute_id == attribute_id,
        func.lower(CategoryAttributeValue.code) == func.lower(code)
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="Code de valeur déjà utilisé pour cet attribut")
    val = CategoryAttributeValue(
        attribute_id=attribute_id,
        value=payload.value,
        code=code,
        sort_order=payload.sort_order or 0
    )
    db.add(val)
    db.commit()
    db.refresh(val)
    return val

@router.put("/categories/{category_id}/attributes/{attribute_id}/values/{value_id}", response_model=CategoryAttributeValueResponse)
async def update_attribute_value(
    category_id: str,
    attribute_id: int,
    value_id: int,
    payload: CategoryAttributeValueUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_role("admin"))
):
    _ = _category_or_404(db, category_id)
    val = db.query(CategoryAttributeValue).filter(
        CategoryAttributeValue.value_id == value_id,
        CategoryAttributeValue.attribute_id == attribute_id
    ).first()
    if not val:
        raise HTTPException(status_code=404, detail="Valeur non trouvée")
    if payload.value is not None:
        val.value = payload.value
    if payload.code is not None:
        exists = db.query(CategoryAttributeValue).filter(
            CategoryAttributeValue.attribute_id == attribute_id,
            func.lower(CategoryAttributeValue.code) == func.lower(payload.code),
            CategoryAttributeValue.value_id != value_id
        ).first()
        if exists:
            raise HTTPException(status_code=400, detail="Code de valeur déjà utilisé pour cet attribut")
        val.code = payload.code
    if payload.sort_order is not None:
        val.sort_order = payload.sort_order
    db.commit()
    db.refresh(val)
    return val

@router.delete("/categories/{category_id}/attributes/{attribute_id}/values/{value_id}")
async def delete_attribute_value(
    category_id: str,
    attribute_id: int,
    value_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_role("admin"))
):
    category = _category_or_404(db, category_id)
    attr = db.query(CategoryAttribute).filter(
        CategoryAttribute.attribute_id == attribute_id,
        CategoryAttribute.category_id == category.category_id
    ).first()
    if not attr:
        raise HTTPException(status_code=404, detail="Attribut non trouvé")
    val = db.query(CategoryAttributeValue).filter(
        CategoryAttributeValue.value_id == value_id,
        CategoryAttributeValue.attribute_id == attribute_id
    ).first()
    if not val:
        raise HTTPException(status_code=404, detail="Valeur non trouvée")
    # empêcher suppression si valeur utilisée
    in_use = db.query(ProductVariantAttribute).filter(
        and_(
            func.lower(ProductVariantAttribute.attribute_name) == func.lower(attr.name),
            func.lower(ProductVariantAttribute.attribute_value) == func.lower(val.value)
        )
    ).first()
    if in_use:
        raise HTTPException(status_code=400, detail="Valeur utilisée par des variantes, suppression interdite")
    db.delete(val)
    db.commit()
    return {"message": "Valeur supprimée avec succès"}

# Pour la compatibilité avec l'ancien endpoint
@router.get("/categories/list")
async def get_categories_list(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir la liste des catégories uniques (ancien format)"""
    categories = db.query(Product.category).distinct().filter(Product.category.isnot(None)).all()
    return [cat[0] for cat in categories if cat[0]]


@router.get("/stats")
async def get_products_stats(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Endpoint agrégé et mis en cache pour accélérer la page Produits."""
    try:
        cache_key = _get_cache_key("products_stats")

        def compute():
            total_products = db.query(func.count(Product.product_id)).scalar() or 0

            # Produits avec variantes (distinct product_id)
            with_variants = db.query(func.count(func.distinct(ProductVariant.product_id))).scalar() or 0
            without_variants = int(total_products) - int(with_variants)

            # Sous-requête: variantes disponibles (non vendues) par produit
            available_variants_sub = (
                db.query(
                    ProductVariant.product_id.label('product_id'),
func.sum(case((ProductVariant.is_sold == False, 1), else_=0)).label('available')
                )
                .group_by(ProductVariant.product_id)
                .subquery()
            )

            # En stock: quantité > 0 OU variantes disponibles > 0
            in_stock = (
                db.query(func.count(Product.product_id))
                .outerjoin(available_variants_sub, available_variants_sub.c.product_id == Product.product_id)
                .filter(or_(Product.quantity > 0, available_variants_sub.c.available > 0))
                .scalar()
                or 0
            )

            # Rupture de stock: (quantité <= 0 ou NULL) ET (aucune variante disponible)
            out_of_stock = (
                db.query(func.count(Product.product_id))
                .outerjoin(available_variants_sub, available_variants_sub.c.product_id == Product.product_id)
                .filter(
                    and_(
                        or_(Product.quantity <= 0, Product.quantity.is_(None)),
                        or_(available_variants_sub.c.available == None, available_variants_sub.c.available <= 0)
                    )
                )
                .scalar()
                or 0
            )

            # Codes-barres
            with_barcode = (
                db.query(func.count(Product.product_id))
                .filter(Product.barcode.isnot(None), func.length(func.trim(Product.barcode)) > 0)
                .scalar()
                or 0
            )
            without_barcode = int(total_products) - int(with_barcode)

            # Catégories + compte produits
            categories_with_count = db.query(
                Category.category_id.label('id'),
                Category.name.label('name'),
                Category.requires_variants.label('requires_variants'),
                func.count(Product.product_id).label('product_count')
            ).outerjoin(
                Product, Category.name == Product.category
            ).group_by(
                Category.category_id, Category.name, Category.requires_variants
            ).all()
            categories = [
                {
                    "id": str(cat.id),
                    "name": str(cat.name),
                    "requires_variants": bool(getattr(cat, 'requires_variants', False)),
                    "product_count": int(cat.product_count or 0),
                }
                for cat in categories_with_count
            ]

            # État/conditions autorisées
            conditions_cfg = _get_allowed_conditions(db)

            return {
                "total_products": int(total_products),
                "with_variants": int(with_variants),
                "without_variants": int(without_variants),
                "in_stock": int(in_stock),
                "out_of_stock": int(out_of_stock),
                "with_barcode": int(with_barcode),
                "without_barcode": int(without_barcode),
                "categories": categories,
                "allowed_conditions": conditions_cfg,
                "cached_at": datetime.now().isoformat(),
            }

        result = _get_cached_or_compute(cache_key, compute)
        return result
    except Exception as e:
        logging.error(f"Erreur /products/stats: {e}")
        # Fallback minimal
        try:
            conds = _get_allowed_conditions(db)
        except Exception:
            conds = {"options": DEFAULT_CONDITIONS, "default": DEFAULT_CONDITIONS[0]}
        return {
            "total_products": 0,
            "with_variants": 0,
            "without_variants": 0,
            "in_stock": 0,
            "out_of_stock": 0,
            "with_barcode": 0,
            "without_barcode": 0,
            "categories": [],
            "allowed_conditions": conds,
            "cached_at": datetime.now().isoformat(),
        }


@router.delete("/cache")
async def clear_products_cache(current_user = Depends(get_current_user)):
    """Vider le cache lié aux endpoints produits (admin recommandé)."""
    try:
        global _cache
        _cache.clear()
        return {"message": "Cache produits vidé", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        logging.error(f"Erreur clear products cache: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors du vidage du cache")


@router.get("/cache/info")
async def products_cache_info(current_user = Depends(get_current_user)):
    """Informations de debug sur le cache produits."""
    entries = []
    now_ts = time.time()
    for k, v in _cache.items():
        age = now_ts - v.get('timestamp', 0)
        valid = age < _cache_duration
        entries.append({
            "key": k,
            "age_seconds": int(age),
            "is_valid": valid,
            "expires_in": int(_cache_duration - age) if valid else 0,
        })
    return {"cache_duration_seconds": _cache_duration, "total_entries": len(entries), "entries": entries}


# ==== GESTION DES IMAGES PRODUITS ====

@router.post("/id/{product_id}/upload-image")
async def upload_product_image(
    product_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(require_any_role(["manager"]))
):
    """Upload une image pour un produit"""
    try:
        # Vérifier que le produit existe
        product = db.query(Product).filter(Product.product_id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        # Valider le type de fichier
        allowed_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Type de fichier non autorisé. Types acceptés : {', '.join(allowed_extensions)}"
            )

        # Créer le dossier uploads/products s'il n'existe pas
        upload_dir = Path("static/uploads/products")
        upload_dir.mkdir(parents=True, exist_ok=True)

        # Générer un nom de fichier unique
        import uuid
        unique_filename = f"product_{product_id}_{uuid.uuid4().hex}{file_ext}"
        file_path = upload_dir / unique_filename

        # Supprimer l'ancienne image si elle existe
        if product.image_path:
            old_image_path = Path(product.image_path)
            if old_image_path.exists():
                try:
                    old_image_path.unlink()
                except Exception as e:
                    logging.warning(f"Impossible de supprimer l'ancienne image: {e}")

        # Sauvegarder le fichier
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Mettre à jour le produit avec le chemin de l'image
        product.image_path = str(file_path)
        db.commit()
        db.refresh(product)

        return {
            "message": "Image uploadée avec succès",
            "image_path": str(file_path),
            "product_id": product_id
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de l'upload de l'image: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de l'upload de l'image")


@router.delete("/id/{product_id}/delete-image")
async def delete_product_image(
    product_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_any_role(["manager"]))
):
    """Supprimer l'image d'un produit"""
    try:
        product = db.query(Product).filter(Product.product_id == product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        if not product.image_path:
            raise HTTPException(status_code=404, detail="Ce produit n'a pas d'image")

        # Supprimer le fichier physique
        image_path = Path(product.image_path)
        if image_path.exists():
            try:
                image_path.unlink()
            except Exception as e:
                logging.warning(f"Impossible de supprimer le fichier image: {e}")

        # Mettre à jour le produit
        product.image_path = None
        db.commit()

        return {"message": "Image supprimée avec succès"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la suppression de l'image: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la suppression de l'image")
