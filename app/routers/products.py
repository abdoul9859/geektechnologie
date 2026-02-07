from fastapi import APIRouter, Depends, HTTPException, status, Query, Body, UploadFile, File
from typing import List, Optional, Dict
from decimal import Decimal
import os
import shutil
from pathlib import Path
from ..database import (
    get_next_id, Product, ProductVariant, ProductVariantAttribute, StockMovement, Category,
    CategoryAttribute, CategoryAttributeValue, UserSettings, DailySale, Invoice, Client,
    InvoiceItem, QuotationItem, DeliveryNoteItem
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
import logging
import time
import re

router = APIRouter(prefix="/api/products", tags=["products"])

def is_product_used_in_transactions(product_id: int) -> bool:
    """Verifie si un produit est utilise dans des factures, devis ou bons de livraison
    NOTE: Cette fonction retourne toujours False maintenant car on permet la modification des produits.
    La protection se fait au niveau des variantes individuelles."""
    return False

# ---------------------------------------------------------------------------
# Helper: enrich a Product document with its variants + variant attributes
# so that ProductResponse (which expects a `variants` list) can serialise it.
# ---------------------------------------------------------------------------
async def _enrich_product_with_variants(product):
    """Attach `variants` list (each with `attributes`) to a Product document."""
    variants = await ProductVariant.find(
        ProductVariant.product_id == product.product_id
    ).sort(ProductVariant.variant_id).to_list()
    for v in variants:
        attrs = await ProductVariantAttribute.find(
            ProductVariantAttribute.variant_id == v.variant_id
        ).to_list()
        v.attributes = attrs
    product.variants = variants
    return product


@router.get("/id/{product_id}/can-modify")
async def can_modify_product(
    product_id: int,
    current_user = Depends(get_current_user)
):
    """Verifier si un produit peut etre modifie (toujours True maintenant)"""
    try:
        product = await Product.find_one(Product.product_id == product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        # Les produits peuvent toujours etre modifies maintenant
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
    current_user = Depends(get_current_user)
):
    """Recuperer les variantes disponibles d'un produit"""
    try:
        product = await Product.find_one(Product.product_id == product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        available_variants = await ProductVariant.find(
            ProductVariant.product_id == product_id,
            ProductVariant.is_sold == False
        ).to_list()

        variants_data = []
        for v in available_variants:
            # Recuperer les attributs de la variante
            attributes = await ProductVariantAttribute.find(
                ProductVariantAttribute.variant_id == v.variant_id
            ).to_list()

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
    current_user = Depends(get_current_user)
):
    """Recuperer les variantes vendues d'un produit"""
    try:
        product = await Product.find_one(Product.product_id == product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        sold_variants = await ProductVariant.find(
            ProductVariant.product_id == product_id,
            ProductVariant.is_sold == True
        ).to_list()

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
    imei: Optional[str] = Query(None, description="IMEI/numero de serie de la variante"),
    current_user = Depends(get_current_user),
):
    """Retourner les factures liees a un produit (et eventuellement a un IMEI precis).

    Strategie:
    - On s'appuie sur la table DailySale qui contient product_id, variant_imei et invoice_id.
    - On renvoie une liste d'objets facture legere (id, numero, client, date, total) ordonnee
      par date de vente decroissante.
    """
    try:
        # Verifier que le produit existe
        product = await Product.find_one(Product.product_id == product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        # Build query filter for DailySale
        sale_filter = {"product_id": product_id}
        if imei:
            imei_clean = imei.strip()
            if imei_clean:
                sale_filter["variant_imei"] = imei_clean

        sales = await DailySale.find(sale_filter).sort(
            -DailySale.sale_date, -DailySale.sale_id
        ).limit(50).to_list()

        # If no sales found for this IMEI, fall back to all sales for the product
        if not sales and imei:
            sales = await DailySale.find(
                DailySale.product_id == product_id
            ).sort(-DailySale.sale_date, -DailySale.sale_id).limit(50).to_list()

        # Deduplicate by invoice and enrich with invoice + client data
        seen_ids: set[int] = set()
        invoices_data: list[dict] = []
        for sale in sales:
            if not sale.invoice_id:
                continue
            if sale.invoice_id in seen_ids:
                continue
            seen_ids.add(sale.invoice_id)

            inv = await Invoice.find_one(Invoice.invoice_id == sale.invoice_id)
            if not inv:
                continue

            client_name = ""
            if inv.client_id:
                client_doc = await Client.find_one(Client.client_id == inv.client_id)
                if client_doc:
                    client_name = client_doc.name or ""

            invoices_data.append(
                {
                    "invoice_id": inv.invoice_id,
                    "invoice_number": inv.invoice_number,
                    "client_id": inv.client_id,
                    "client_name": client_name,
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

# Cache simple pour accelerer les endpoints produits (similaire au dashboard)
_cache = {}
_cache_duration = 300  # 5 minutes

from datetime import datetime


def _get_cache_key(*args):
    return "|".join(str(arg) for arg in args)


def _is_cache_valid(entry):
    return entry and (time.time() - entry.get('timestamp', 0)) < _cache_duration


async def _get_cached_or_compute(cache_key: str, compute_func):
    if cache_key in _cache and _is_cache_valid(_cache[cache_key]):
        return _cache[cache_key]['data']
    result = await compute_func()
    _cache[cache_key] = {"data": result, "timestamp": time.time()}
    return result

# Modeles Pydantic pour les categories
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
# Conditions (etat des produits)
# =====================

DEFAULT_CONDITIONS = ["neuf", "occasion", "venant"]
DEFAULT_CONDITION_KEY = "product_conditions"

async def _get_allowed_conditions() -> dict:
    """Retourne {options: [...], default: str}. Stocke dans UserSettings (global)."""
    setting = await UserSettings.find_one(
        UserSettings.user_id == None,
        UserSettings.setting_key == DEFAULT_CONDITION_KEY
    )
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

async def _set_allowed_conditions(options: list[str], default_value: str):
    import json
    payload = json.dumps({"options": options, "default": default_value}, ensure_ascii=False)
    setting = await UserSettings.find_one(
        UserSettings.user_id == None,
        UserSettings.setting_key == DEFAULT_CONDITION_KEY
    )
    if not setting:
        new_id = await get_next_id("user_settings")
        setting = UserSettings(
            setting_id=new_id,
            user_id=None,
            setting_key=DEFAULT_CONDITION_KEY,
            setting_value=payload
        )
        await setting.insert()
    else:
        setting.setting_value = payload
        await setting.save()

class ConditionsUpdate(BaseModel):
    options: List[str]
    default: Optional[str] = None

@router.get("/settings/conditions", tags=["settings"])
async def get_conditions_settings(current_user = Depends(get_current_user)):
    return await _get_allowed_conditions()

@router.put("/settings/conditions", tags=["settings"])
async def update_conditions_settings(payload: ConditionsUpdate, current_user = Depends(require_role("admin"))):
    options = [o.strip() for o in (payload.options or []) if o and o.strip()]
    if not options:
        raise HTTPException(status_code=400, detail="La liste des états ne peut pas être vide")
    default_value = (payload.default or options[0]).strip()
    if default_value not in options:
        options.insert(0, default_value)
    await _set_allowed_conditions(options, default_value)
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
    include_archived: Optional[bool] = False,
    current_user = Depends(get_current_user)
):
    """Lister les produits avec recherche et filtres"""
    query_filter: dict = {}
    and_conditions: list = []

    # Par defaut, exclure les produits archives
    if not include_archived:
        and_conditions.append({"$or": [{"is_archived": False}, {"is_archived": None}, {"is_archived": {"$exists": False}}]})

    if search:
        search_pattern = {"$regex": re.escape(search), "$options": "i"}
        # Search in variants too (barcode, imei_serial)
        variant_matches = await ProductVariant.find(
            {"$or": [
                {"barcode": search_pattern},
                {"imei_serial": search_pattern}
            ]}
        ).to_list()
        variant_product_ids = list({v.product_id for v in variant_matches})

        product_search_or = [
            {"name": search_pattern},
            {"description": search_pattern},
            {"brand": search_pattern},
            {"model": search_pattern},
            {"barcode": search_pattern},
        ]
        if variant_product_ids:
            product_search_or.append({"product_id": {"$in": variant_product_ids}})
        and_conditions.append({"$or": product_search_or})

    if category:
        and_conditions.append({"category": category})

    if condition:
        condition_lower = condition.strip().lower()
        # Products with matching condition OR variants with matching condition
        variant_cond_matches = await ProductVariant.find(
            {"condition": {"$regex": f"^\\s*{re.escape(condition_lower)}\\s*$", "$options": "i"}}
        ).to_list()
        variant_cond_pids = list({v.product_id for v in variant_cond_matches})
        cond_or = [{"condition": {"$regex": f"^\\s*{re.escape(condition_lower)}\\s*$", "$options": "i"}}]
        if variant_cond_pids:
            cond_or.append({"product_id": {"$in": variant_cond_pids}})
        and_conditions.append({"$or": cond_or})

    if min_price is not None:
        and_conditions.append({"price": {"$gte": Decimal(min_price)}})
    if max_price is not None:
        and_conditions.append({"price": {"$lte": Decimal(max_price)}})

    if brand:
        and_conditions.append({"brand": {"$regex": re.escape(brand), "$options": "i"}})
    if model:
        and_conditions.append({"model": {"$regex": re.escape(model), "$options": "i"}})

    if has_barcode is True:
        and_conditions.append({"barcode": {"$ne": None}})
        and_conditions.append({"barcode": {"$ne": ""}})
    elif has_barcode is False:
        and_conditions.append({"$or": [{"barcode": None}, {"barcode": ""}, {"barcode": {"$exists": False}}]})

    # For in_stock / has_variants filters we need variant info
    if in_stock is not None or has_variants is not None:
        # Get all product_ids that have at least one variant
        all_variant_pids_docs = await ProductVariant.find().to_list()
        pids_with_any_variant = set()
        pids_with_available_variant = set()
        for v in all_variant_pids_docs:
            pids_with_any_variant.add(v.product_id)
            if not v.is_sold:
                pids_with_available_variant.add(v.product_id)

        if in_stock is True:
            and_conditions.append({"$or": [
                {"quantity": {"$gt": 0}},
                {"product_id": {"$in": list(pids_with_available_variant)}}
            ]})
        elif in_stock is False:
            # Out of stock: quantity <= 0 AND no available variants
            pids_in_stock = pids_with_available_variant
            and_conditions.append({"quantity": {"$lte": 0}})
            if pids_in_stock:
                and_conditions.append({"product_id": {"$nin": list(pids_in_stock)}})

        if has_variants is True:
            and_conditions.append({"product_id": {"$in": list(pids_with_any_variant)}})
        elif has_variants is False:
            and_conditions.append({"product_id": {"$nin": list(pids_with_any_variant)}})

    if and_conditions:
        query_filter = {"$and": and_conditions} if len(and_conditions) > 1 else and_conditions[0]

    products = await Product.find(query_filter).sort(
        -Product.created_at
    ).skip(skip).limit(limit).to_list()

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

    # Enrich with variants
    for p in products:
        await _enrich_product_with_variants(p)

    # Si un filtre de condition est actif, ne retourner que les variantes correspondant a cette condition
    if condition:
        cond_lower = (condition or "").strip().lower()
        for p in products:
            try:
                p.variants = [v for v in (p.variants or []) if ((getattr(v, 'condition', None) or "").strip().lower() == cond_lower)]
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
    include_archived: Optional[bool] = False,
    sort_by: Optional[str] = Query("created_at"),  # name | category | price | stock | barcode | created_at
    sort_dir: Optional[str] = Query("desc"),  # asc | desc
    current_user = Depends(get_current_user)
):
    """Lister les produits avec pagination (retourne items + total)."""
    and_conditions: list = []

    # Par defaut, exclure les produits archives
    if not include_archived:
        and_conditions.append({"$or": [{"is_archived": False}, {"is_archived": None}, {"is_archived": {"$exists": False}}]})

    if search:
        search_pattern = {"$regex": re.escape(search), "$options": "i"}
        variant_matches = await ProductVariant.find(
            {"$or": [
                {"barcode": search_pattern},
                {"imei_serial": search_pattern}
            ]}
        ).to_list()
        variant_product_ids = list({v.product_id for v in variant_matches})

        product_search_or = [
            {"name": search_pattern},
            {"description": search_pattern},
            {"brand": search_pattern},
            {"model": search_pattern},
            {"barcode": search_pattern},
        ]
        if variant_product_ids:
            product_search_or.append({"product_id": {"$in": variant_product_ids}})
        and_conditions.append({"$or": product_search_or})

    if category:
        and_conditions.append({"category": category})

    if condition:
        condition_lower = condition.strip().lower()
        variant_cond_matches = await ProductVariant.find(
            {"condition": {"$regex": f"^\\s*{re.escape(condition_lower)}\\s*$", "$options": "i"}}
        ).to_list()
        variant_cond_pids = list({v.product_id for v in variant_cond_matches})
        cond_or = [{"condition": {"$regex": f"^\\s*{re.escape(condition_lower)}\\s*$", "$options": "i"}}]
        if variant_cond_pids:
            cond_or.append({"product_id": {"$in": variant_cond_pids}})
        and_conditions.append({"$or": cond_or})

    if min_price is not None:
        and_conditions.append({"price": {"$gte": Decimal(min_price)}})
    if max_price is not None:
        and_conditions.append({"price": {"$lte": Decimal(max_price)}})

    if brand:
        and_conditions.append({"brand": {"$regex": re.escape(brand), "$options": "i"}})
    if model:
        and_conditions.append({"model": {"$regex": re.escape(model), "$options": "i"}})

    if has_barcode is True:
        and_conditions.append({"barcode": {"$ne": None}})
        and_conditions.append({"barcode": {"$ne": ""}})
    elif has_barcode is False:
        and_conditions.append({"$or": [{"barcode": None}, {"barcode": ""}, {"barcode": {"$exists": False}}]})

    # For in_stock / has_variants filters
    # We pre-compute variant info only when these filters are active
    pids_with_any_variant = set()
    pids_with_available_variant = set()
    need_variant_scan = (in_stock is not None or has_variants is not None)
    if need_variant_scan:
        all_variant_docs = await ProductVariant.find().to_list()
        for v in all_variant_docs:
            pids_with_any_variant.add(v.product_id)
            if not v.is_sold:
                pids_with_available_variant.add(v.product_id)

        if in_stock is True:
            and_conditions.append({"$or": [
                {"quantity": {"$gt": 0}},
                {"product_id": {"$in": list(pids_with_available_variant)}}
            ]})
        elif in_stock is False:
            and_conditions.append({"quantity": {"$lte": 0}})
            if pids_with_available_variant:
                and_conditions.append({"product_id": {"$nin": list(pids_with_available_variant)}})

        if has_variants is True:
            and_conditions.append({"product_id": {"$in": list(pids_with_any_variant)}})
        elif has_variants is False:
            and_conditions.append({"product_id": {"$nin": list(pids_with_any_variant)}})

    query_filter = {}
    if and_conditions:
        query_filter = {"$and": and_conditions} if len(and_conditions) > 1 else and_conditions[0]

    # Sorting
    sort_key = (sort_by or "name").strip().lower()
    sort_dir_key = (sort_dir or "asc").strip().lower()
    dir_desc = sort_dir_key == "desc"

    start_time = time.time()

    # Total count
    total = await Product.find(query_filter).count()
    count_time = time.time()
    logging.info(f"Product count (filtered) took: {count_time - start_time:.4f} seconds")

    # For "stock" sorting, we need to compute available variant counts and sort in Python
    # For other sorts, we can use MongoDB sort directly
    skip_val = (page - 1) * page_size

    if sort_key == "stock":
        # Load all matching products, compute effective stock, sort in Python, then paginate
        all_products = await Product.find(query_filter).to_list()

        # Build available variants count per product
        product_ids_all = [p.product_id for p in all_products]
        avail_map: dict[int, int] = {}
        if product_ids_all:
            avail_variants = await ProductVariant.find(
                ProductVariant.product_id.is_in(product_ids_all) if hasattr(ProductVariant.product_id, 'is_in') else {"product_id": {"$in": product_ids_all}},
                ProductVariant.is_sold == False
            ).to_list()
            for v in avail_variants:
                avail_map[v.product_id] = avail_map.get(v.product_id, 0) + 1

        def stock_key(p):
            return avail_map.get(p.product_id, 0) if avail_map.get(p.product_id) is not None else (p.quantity or 0)

        all_products.sort(key=stock_key, reverse=dir_desc)
        items = all_products[skip_val:skip_val + page_size]
    else:
        # Map sort_key to Product field
        sort_field_map = {
            "price": Product.price,
            "category": Product.category,
            "barcode": Product.barcode,
            "created_at": Product.created_at,
            "name": Product.name,
        }
        sort_field = sort_field_map.get(sort_key, Product.name)
        mongo_sort = -sort_field if dir_desc else +sort_field

        items = await Product.find(query_filter).sort(
            mongo_sort, +Product.product_id
        ).skip(skip_val).limit(page_size).to_list()

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

    # Compute variant summary for displayed products (single DB round-trip)
    product_ids = [p.product_id for p in items]
    variant_summary_map: dict = {}
    if product_ids:
        all_variants_for_page = await ProductVariant.find(
            {"product_id": {"$in": product_ids}}
        ).to_list()

        has_variants_set = set()
        # Build per-product summary
        for pid in product_ids:
            variant_summary_map[pid] = {
                'has_variants': False,
                'available': 0,
                'by_condition': {}
            }

        for v in all_variants_for_page:
            has_variants_set.add(v.product_id)
            entry = variant_summary_map.get(v.product_id)
            if entry is None:
                entry = {'has_variants': True, 'available': 0, 'by_condition': {}}
                variant_summary_map[v.product_id] = entry
            entry['has_variants'] = True
            if not v.is_sold:
                cond_key = ((v.condition or "").strip().lower()) or "inconnu"
                entry['by_condition'][cond_key] = entry['by_condition'].get(cond_key, 0) + 1
                entry['available'] += 1

    # Inject lightweight fields into Product objects
    for p in items:
        try:
            sum_entry = variant_summary_map.get(p.product_id)
            p.has_variants = bool(sum_entry.get('has_variants')) if sum_entry else False
            p.variants_available = int(sum_entry.get('available', 0)) if sum_entry else 0
            p.variant_condition_counts = sum_entry.get('by_condition', {}) if sum_entry else {}
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
    current_user = Depends(get_current_user)
):
    """Obtenir un produit par ID"""
    product = await Product.find_one(Product.product_id == product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    await _enrich_product_with_variants(product)
    return product

@router.post("/", response_model=ProductResponse)
async def create_product(
    product_data: ProductCreate,
    current_user = Depends(require_any_role(["user", "manager"]))
):
    """Creer un nouveau produit avec variantes selon la regle metier"""
    try:
        print(f"Received product data: {product_data}")
        print(f"Product data dict: {product_data.dict()}")
        print(f"Variants: {product_data.variants}")
        cond_cfg = await _get_allowed_conditions()
        allowed = set([c.lower() for c in cond_cfg["options"]])
        default_cond = cond_cfg["default"]
        # Validation selon la regle metier des memoires
        has_variants = len(product_data.variants) > 0

        # Normaliser le code-barres produit (autorise meme si variantes, il sert de code partage)
        normalized_barcode = None
        if product_data.barcode is not None:
            bc = (product_data.barcode or "").strip()
            normalized_barcode = bc or None

        # Verifier l'unicite du code-barres produit (global: produits + variantes)
        if normalized_barcode:
            exists_prod = await Product.find_one(Product.barcode == normalized_barcode)
            exists_var = await ProductVariant.find_one(ProductVariant.barcode == normalized_barcode)
            if exists_prod or exists_var:
                raise HTTPException(status_code=400, detail="Ce code-barres existe déjà")

        # Normaliser et controler les variantes
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
        # Unicite globale pour variantes
        if variant_barcodes:
            exists_var_barcodes = await ProductVariant.find(
                {"barcode": {"$in": variant_barcodes}}
            ).to_list()
            exists_prod_barcodes = await Product.find(
                {"barcode": {"$in": variant_barcodes}}
            ).to_list()
            if exists_var_barcodes or exists_prod_barcodes:
                raise HTTPException(status_code=400, detail="Un ou plusieurs codes-barres de variantes existent déjà")
        if variant_serials:
            exists_serials = await ProductVariant.find(
                {"imei_serial": {"$in": variant_serials}}
            ).to_list()
            if exists_serials:
                raise HTTPException(status_code=400, detail="Un ou plusieurs IMEI/numéros de série existent déjà")

        # Creer le produit
        # Normaliser/valider condition produit
        prod_condition = (product_data.condition or default_cond)
        if prod_condition and prod_condition.lower() not in allowed:
            raise HTTPException(status_code=400, detail="Condition de produit invalide")

        product_id = await get_next_id("products")
        db_product = Product(
            product_id=product_id,
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

        await db_product.insert()

        # Creer les variantes si presentes
        created_variants = []
        for nv in normalized_variants:
            variant_id = await get_next_id("product_variants")
            db_variant = ProductVariant(
                variant_id=variant_id,
                product_id=db_product.product_id,
                imei_serial=nv['imei_serial'],
                barcode=nv['barcode'],
                condition=nv['condition']
            )
            await db_variant.insert()
            created_variants.append(db_variant)

        # Creer les attributs de la variante (si presents dans payload d'origine)
        for db_v, orig_v in zip(created_variants, (product_data.variants or [])):
            for attr_data in getattr(orig_v, 'attributes', []) or []:
                attr_id = await get_next_id("product_variant_attributes")
                db_attr = ProductVariantAttribute(
                    attribute_id=attr_id,
                    variant_id=db_v.variant_id,
                    attribute_name=attr_data.attribute_name,
                    attribute_value=attr_data.attribute_value
                )
                await db_attr.insert()

        # Creer un mouvement de stock d'entree
        if db_product.quantity > 0:
            movement_id = await get_next_id("stock_movements")
            stock_movement = StockMovement(
                movement_id=movement_id,
                product_id=db_product.product_id,
                quantity=db_product.quantity,
                movement_type="IN",
                reference_type="CREATION",
                notes="Création du produit"
            )
            await stock_movement.insert()

        # Enrich for response
        await _enrich_product_with_variants(db_product)
        return db_product

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if 'duplicate key' in error_msg.lower() or 'unique' in error_msg.lower() or 'E11000' in error_msg:
            raise HTTPException(status_code=400, detail="Violation d'unicité (code-barres ou IMEI déjà utilisé)")
        logging.error(f"Erreur lors de la création du produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/id/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    product_data: ProductUpdate,
    current_user = Depends(require_any_role(["user", "manager"]))
):
    """Mettre a jour un produit"""
    try:
        cond_cfg = await _get_allowed_conditions()
        allowed = set([c.lower() for c in cond_cfg["options"]])
        default_cond = cond_cfg["default"]
        product = await Product.find_one(Product.product_id == product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        # Verifier si le produit est utilise dans des transactions
        if is_product_used_in_transactions(product_id):
            raise HTTPException(
                status_code=400,
                detail="Ce produit ne peut pas être modifié car il est déjà utilisé dans des factures, devis ou bons de livraison"
            )

        # Validation selon la regle metier
        existing_variants = await ProductVariant.find(
            ProductVariant.product_id == product_id
        ).to_list()
        has_variants = len(existing_variants) > 0
        new_variants = product_data.variants if product_data.variants is not None else []
        will_have_variants = len(new_variants) > 0 or has_variants

        # Normaliser barcode produit recu: trim -> None si vide
        incoming_barcode = None
        if product_data.barcode is not None:
            bc = (product_data.barcode or "").strip()
            incoming_barcode = bc or None

        # Regle: si le produit a/va avoir des variantes, interdire le code-barres produit
        if will_have_variants and incoming_barcode:
            raise HTTPException(
                status_code=400,
                detail="Un produit avec variantes ne peut pas avoir de code-barres"
            )

        # Preparer les donnees a mettre a jour (sans variants)
        update_data = product_data.dict(exclude_unset=True, exclude={'variants'})

        # Normaliser et valider la condition si fournie
        if 'condition' in update_data and update_data['condition'] is not None:
            if update_data['condition'].lower() not in allowed:
                raise HTTPException(status_code=400, detail="Condition de produit invalide")

        # Normaliser barcode cote update_data
        if 'barcode' in update_data:
            update_data['barcode'] = None if will_have_variants else incoming_barcode

        # Verifier l'unicite du code-barres produit si fourni et modifie
        if update_data.get('barcode'):
            existing_product = await Product.find_one(
                Product.barcode == update_data['barcode'],
                Product.product_id != product_id
            )
            if existing_product:
                raise HTTPException(status_code=400, detail="Ce code-barres existe déjà")

        # Appliquer les mises a jour champ par champ
        for field, value in update_data.items():
            # Normaliser les chaines vides en None pour eviter les contraintes d'unicite sur ''
            if isinstance(value, str):
                value = value.strip()
                if value == "":
                    value = None
            setattr(product, field, value)

        # Gerer les variantes si fournies
        if product_data.variants is not None:
            # Normaliser les donnees variantes et preparer des listes pour validation
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

            # Detecter doublons dans le payload
            if len(set(variant_barcodes)) != len(variant_barcodes):
                raise HTTPException(status_code=400, detail="Codes-barres de variantes en double dans la demande")
            if len(set(variant_serials)) != len(variant_serials):
                raise HTTPException(status_code=400, detail="IMEI/numéros de série en double dans la demande")

            # Verifier unicite globale (hors variantes de ce produit)
            if variant_barcodes:
                existing_variant_barcodes = await ProductVariant.find(
                    {"barcode": {"$in": variant_barcodes}, "product_id": {"$ne": product_id}}
                ).to_list()
                if existing_variant_barcodes:
                    raise HTTPException(status_code=400, detail="Un ou plusieurs codes-barres de variantes existent déjà")
            existing_serial_matches = await ProductVariant.find(
                {"imei_serial": {"$in": variant_serials}, "product_id": {"$ne": product_id}}
            ).to_list()
            if existing_serial_matches:
                raise HTTPException(status_code=400, detail="Un ou plusieurs IMEI/numéros de série existent déjà")

            # Recuperer les variantes existantes avec leur statut de vente
            existing_variants_map = {v.imei_serial: v for v in existing_variants}

            # Identifier les variantes a supprimer (celles qui ne sont plus dans la nouvelle liste ET non vendues)
            new_imeis = {nv['imei_serial'] for nv in norm_variants}
            for imei, variant in existing_variants_map.items():
                if imei not in new_imeis and not variant.is_sold:
                    # Supprimer les attributs d'abord
                    await ProductVariantAttribute.find(
                        ProductVariantAttribute.variant_id == variant.variant_id
                    ).delete()
                    await variant.delete()

            # Creer ou mettre a jour les variantes
            imei_to_db_variant = {}
            for nv in norm_variants:
                imei = nv['imei_serial']
                if imei in existing_variants_map:
                    # Mettre a jour la variante existante (preserver is_sold)
                    db_variant = existing_variants_map[imei]
                    db_variant.barcode = nv['barcode']
                    db_variant.condition = nv['condition']
                    await db_variant.save()
                else:
                    # Creer une nouvelle variante
                    variant_id = await get_next_id("product_variants")
                    db_variant = ProductVariant(
                        variant_id=variant_id,
                        product_id=product_id,
                        imei_serial=nv['imei_serial'],
                        barcode=nv['barcode'],
                        condition=nv['condition'],
                        is_sold=False
                    )
                    await db_variant.insert()

                imei_to_db_variant[str(nv['imei_serial']).strip()] = db_variant

            # Supprimer tous les anciens attributs des variantes existantes
            for db_variant in imei_to_db_variant.values():
                await ProductVariantAttribute.find(
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
                        attr_id = await get_next_id("product_variant_attributes")
                        db_attr = ProductVariantAttribute(
                            attribute_id=attr_id,
                            variant_id=db_v.variant_id,
                            attribute_name=attr_data.attribute_name,
                            attribute_value=attr_data.attribute_value
                        )
                        await db_attr.insert()
                except Exception:
                    # Ne pas bloquer la mise a jour si un attribut est mal forme
                    pass

            # Mettre a jour la quantite basee sur les variantes non vendues uniquement
            available_count = await ProductVariant.find(
                ProductVariant.product_id == product_id,
                ProductVariant.is_sold == False
            ).count()
            product.quantity = available_count
            # S'assurer que le code-barres produit est None si variantes
            product.barcode = None

        await product.save()

        # Enrich for response
        await _enrich_product_with_variants(product)
        return product

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if 'duplicate key' in error_msg.lower() or 'unique' in error_msg.lower() or 'E11000' in error_msg:
            raise HTTPException(status_code=400, detail="Violation d'unicité (code-barres ou IMEI déjà utilisé)")
        logging.error(f"Erreur lors de la mise à jour du produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.delete("/id/{product_id}")
async def delete_product(
    product_id: int,
    current_user = Depends(require_any_role(["manager"]))
):
    """Supprimer un produit"""
    try:
        product = await Product.find_one(Product.product_id == product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        # Verifier si le produit est utilise dans des transactions
        if is_product_used_in_transactions(product_id):
            raise HTTPException(
                status_code=400,
                detail="Ce produit ne peut pas être supprimé car il est déjà utilisé dans des factures, devis ou bons de livraison"
            )

        # Creer un mouvement de stock de sortie pour tracabilite
        if product.quantity > 0:
            movement_id = await get_next_id("stock_movements")
            stock_movement = StockMovement(
                movement_id=movement_id,
                product_id=product_id,
                quantity=-product.quantity,
                movement_type="OUT",
                reference_type="DELETION",
                notes=f"Suppression du produit: {product.name}"
            )
            await stock_movement.insert()

        # Delete variant attributes, then variants, then product
        variants = await ProductVariant.find(
            ProductVariant.product_id == product_id
        ).to_list()
        for v in variants:
            await ProductVariantAttribute.find(
                ProductVariantAttribute.variant_id == v.variant_id
            ).delete()
        await ProductVariant.find(ProductVariant.product_id == product_id).delete()
        await product.delete()

        return {"message": "Produit supprimé avec succès"}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la suppression du produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/scan/{barcode}")
async def scan_barcode(
    barcode: str,
    current_user = Depends(get_current_user)
):
    """Scanner un code-barres (produit ou variante) et retourner un objet JSON simple.

    Recherche sur:
    - `products.barcode`
    - `product_variants.barcode`
    - `product_variants.imei_serial`
    Les espaces en trop sont ignores.
    """
    try:
        code = (barcode or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="Code-barres vide")

        # 1) Produit par code-barres exact (trim)
        # We search with regex to handle whitespace: ^\\s*code\\s*$
        product = await Product.find_one(
            {"barcode": {"$regex": f"^\\s*{re.escape(code)}\\s*$"}}
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

        # 2) Variante par code-barres ou IMEI/serie (exact match with trim)
        variant = await ProductVariant.find_one(
            {"$or": [
                {"barcode": {"$regex": f"^\\s*{re.escape(code)}\\s*$"}},
                {"imei_serial": {"$regex": f"^\\s*{re.escape(code)}\\s*$"}}
            ]}
        )
        if variant:
            parent_product = await Product.find_one(Product.product_id == variant.product_id)
            attributes = await ProductVariantAttribute.find(
                ProductVariantAttribute.variant_id == variant.variant_id
            ).to_list()
            attributes_text = ", ".join(
                [f"{a.attribute_name}: {a.attribute_value}" for a in (attributes or [])]
            )
            return {
                "type": "variant",
                "product_id": parent_product.product_id if parent_product else variant.product_id,
                "product_name": parent_product.name if parent_product else "",
                "price": float(parent_product.price or 0) if parent_product else 0,
                "category_name": parent_product.category if parent_product else None,
                "stock_quantity": 0 if variant.is_sold else 1,
                "variant": {
                    "variant_id": variant.variant_id,
                    "imei_serial": variant.imei_serial,
                    "barcode": variant.barcode,
                    "is_sold": bool(variant.is_sold),
                    "attributes": attributes_text
                }
            }

        # 3) Recherche partielle (fallback) sur variantes
        like_pattern = {"$regex": re.escape(code), "$options": "i"}
        variant_like = await ProductVariant.find_one(
            {"$or": [
                {"barcode": like_pattern},
                {"imei_serial": like_pattern}
            ]}
        )
        if variant_like:
            parent_product = await Product.find_one(Product.product_id == variant_like.product_id)
            attributes = await ProductVariantAttribute.find(
                ProductVariantAttribute.variant_id == variant_like.variant_id
            ).to_list()
            attributes_text = ", ".join(
                [f"{a.attribute_name}: {a.attribute_value}" for a in (attributes or [])]
            )
            return {
                "type": "variant",
                "product_id": parent_product.product_id if parent_product else variant_like.product_id,
                "product_name": parent_product.name if parent_product else "",
                "price": float(parent_product.price or 0) if parent_product else 0,
                "category_name": parent_product.category if parent_product else None,
                "stock_quantity": 0 if variant_like.is_sold else 1,
                "variant": {
                    "variant_id": variant_like.variant_id,
                    "imei_serial": variant_like.imei_serial,
                    "barcode": variant_like.barcode,
                    "is_sold": bool(variant_like.is_sold),
                    "attributes": attributes_text
                }
            }

        # 4) Recherche partielle sur produit barcode
        product_like = await Product.find_one(
            {"barcode": like_pattern}
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

# ==== GESTION DES CATEGORIES ====

@router.get("/categories", response_model=List[CategoryResponse])
async def get_categories(
    current_user = Depends(get_current_user)
):
    """Obtenir la liste des categories avec le nombre de produits associes (mis en cache 5 min)"""
    try:
        cache_key = _get_cache_key("product_categories")

        async def compute():
            categories = await Category.find().to_list()
            result = []
            for cat in categories:
                product_count = await Product.find(Product.category == cat.name).count()
                result.append({
                    "id": str(cat.category_id),
                    "name": str(cat.name),
                    "requires_variants": bool(getattr(cat, 'requires_variants', False)),
                    "product_count": int(product_count or 0),
                })
            return result

        result = await _get_cached_or_compute(cache_key, compute)
        return result
    except Exception as e:
        logging.error(f"Erreur /products/categories: {e}")
        return []

@router.get("/categories/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: str,
    current_user = Depends(get_current_user)
):
    """Obtenir une categorie specifique avec le nombre de produits associes"""
    category = await _category_find_by_identifier(category_id)

    if not category:
        raise HTTPException(status_code=404, detail="Catégorie non trouvée")

    # Compter les produits associes
    product_count = await Product.find(Product.category == category.name).count()

    return {
        "id": str(category.category_id),
        "name": category.name,
        "requires_variants": bool(category.requires_variants),
        "product_count": product_count
    }

@router.post("/categories", response_model=CategoryResponse)
async def create_category(
    category_data: CategoryCreate,
    current_user = Depends(get_current_user)
):
    """Creer une nouvelle categorie"""
    # Verifier si la categorie existe deja
    existing_category = await Category.find_one(
        {"name": {"$regex": f"^{re.escape(category_data.name)}$", "$options": "i"}}
    )

    if existing_category:
        raise HTTPException(
            status_code=400,
            detail="Une catégorie avec ce nom existe déjà"
        )

    # Creer la nouvelle categorie
    cat_id = await get_next_id("categories")
    new_category = Category(
        category_id=cat_id,
        name=category_data.name,
        description=getattr(category_data, 'description', None),
        requires_variants=bool(getattr(category_data, 'requires_variants', False))
    )

    await new_category.insert()

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
    current_user = Depends(get_current_user)
):
    """Mettre a jour une categorie existante"""
    category = await _category_find_by_identifier(category_id)

    if not category:
        raise HTTPException(status_code=404, detail="Catégorie non trouvée")

    # Verifier si le nouveau nom existe deja (sauf s'il s'agit du meme)
    if category.name.lower() != category_data.name.lower():
        existing_category = await Category.find_one(
            {"name": {"$regex": f"^{re.escape(category_data.name)}$", "$options": "i"}}
        )

        if existing_category:
            raise HTTPException(
                status_code=400,
                detail="Une catégorie avec ce nom existe déjà"
            )

    # Sauvegarder l'ancien nom pour mettre a jour les produits
    old_name = category.name

    # Mettre a jour la categorie
    category.name = category_data.name
    if hasattr(category_data, 'description'):
        category.description = category_data.description
    if hasattr(category_data, 'requires_variants'):
        category.requires_variants = bool(category_data.requires_variants)
    await category.save()

    # Mettre a jour tous les produits avec cette categorie
    if old_name != category_data.name:
        products_to_update = await Product.find(Product.category == old_name).to_list()
        for p in products_to_update:
            p.category = category_data.name
            await p.save()

    # Compter le nombre de produits dans la categorie mise a jour
    product_count = await Product.find(Product.category == category.name).count()

    return {
        "id": str(category.category_id),
        "name": category.name,
        "requires_variants": bool(category.requires_variants),
        "product_count": product_count
    }

@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: str,
    current_user = Depends(get_current_user)
):
    """Supprimer une categorie"""
    category = await _category_find_by_identifier(category_id)

    if not category:
        raise HTTPException(status_code=404, detail="Catégorie non trouvée")

    # Verifier si des produits utilisent cette categorie
    products_with_category = await Product.find(
        Product.category == category.name
    ).count()

    if products_with_category > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Impossible de supprimer la catégorie. {products_with_category} produit(s) l'utilisent encore."
        )

    # Supprimer la categorie
    await category.delete()

    return {"message": "Catégorie supprimée avec succès"}

# =====================
# Attributs de categorie
# =====================

def _slugify(text: str) -> str:
    return ''.join(c.lower() if c.isalnum() else '-' for c in text).strip('-')

async def _category_find_by_identifier(identifier: str):
    """Find a Category by numeric ID or name."""
    try:
        if str(identifier).isdigit():
            return await Category.find_one(Category.category_id == int(identifier))
    except Exception:
        pass
    return await Category.find_one(Category.name == identifier)

async def _category_or_404(category_id: str) -> Category:
    category = await _category_find_by_identifier(category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Catégorie non trouvée")
    return category

@router.get("/categories/{category_id}/attributes", response_model=List[CategoryAttributeResponse])
async def list_category_attributes(
    category_id: str,
    current_user = Depends(get_current_user)
):
    category = await _category_or_404(category_id)
    attrs = await CategoryAttribute.find(
        CategoryAttribute.category_id == category.category_id
    ).sort(CategoryAttribute.sort_order).to_list()
    # Load values for each attribute
    for a in attrs:
        values = await CategoryAttributeValue.find(
            CategoryAttributeValue.attribute_id == a.attribute_id
        ).sort(CategoryAttributeValue.sort_order).to_list()
        a.values = values
    return attrs

@router.post("/categories/{category_id}/attributes", response_model=CategoryAttributeResponse)
async def create_category_attribute(
    category_id: str,
    payload: CategoryAttributeCreate,
    current_user = Depends(require_role("admin"))
):
    category = await _category_or_404(category_id)
    code = payload.code or _slugify(payload.name)
    # unicite code par categorie
    existing = await CategoryAttribute.find_one(
        CategoryAttribute.category_id == category.category_id,
        {"code": {"$regex": f"^{re.escape(code)}$", "$options": "i"}}
    )
    if existing:
        raise HTTPException(status_code=400, detail="Code d'attribut déjà utilisé pour cette catégorie")
    attr_id = await get_next_id("category_attributes")
    attr = CategoryAttribute(
        attribute_id=attr_id,
        category_id=category.category_id,
        name=payload.name,
        code=code,
        type=payload.type,
        required=bool(payload.required),
        multi_select=bool(payload.multi_select),
        sort_order=payload.sort_order or 0
    )
    await attr.insert()
    # valeurs initiales
    for i, v in enumerate(payload.values or []):
        vcode = v.code or _slugify(v.value)
        val_id = await get_next_id("category_attribute_values")
        await CategoryAttributeValue(
            value_id=val_id,
            attribute_id=attr.attribute_id,
            value=v.value,
            code=vcode,
            sort_order=v.sort_order if v.sort_order is not None else i
        ).insert()
    # Reload with values for response
    attr.values = await CategoryAttributeValue.find(
        CategoryAttributeValue.attribute_id == attr.attribute_id
    ).sort(CategoryAttributeValue.sort_order).to_list()
    return attr

@router.put("/categories/{category_id}/attributes/{attribute_id}", response_model=CategoryAttributeResponse)
async def update_category_attribute(
    category_id: str,
    attribute_id: int,
    payload: CategoryAttributeUpdate,
    current_user = Depends(require_role("admin"))
):
    category = await _category_or_404(category_id)
    attr = await CategoryAttribute.find_one(
        CategoryAttribute.attribute_id == attribute_id,
        CategoryAttribute.category_id == category.category_id
    )
    if not attr:
        raise HTTPException(status_code=404, detail="Attribut non trouvé")
    if payload.name is not None:
        attr.name = payload.name
    if payload.code is not None:
        # verifier unicite
        existing = await CategoryAttribute.find_one(
            CategoryAttribute.category_id == category.category_id,
            {"code": {"$regex": f"^{re.escape(payload.code)}$", "$options": "i"}},
            CategoryAttribute.attribute_id != attr.attribute_id
        )
        if existing:
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
    await attr.save()
    # Reload values for response
    attr.values = await CategoryAttributeValue.find(
        CategoryAttributeValue.attribute_id == attr.attribute_id
    ).sort(CategoryAttributeValue.sort_order).to_list()
    return attr

@router.delete("/categories/{category_id}/attributes/{attribute_id}")
async def delete_category_attribute(
    category_id: str,
    attribute_id: int,
    current_user = Depends(require_role("admin"))
):
    category = await _category_or_404(category_id)
    attr = await CategoryAttribute.find_one(
        CategoryAttribute.attribute_id == attribute_id,
        CategoryAttribute.category_id == category.category_id
    )
    if not attr:
        raise HTTPException(status_code=404, detail="Attribut non trouvé")
    # empecher suppression si utilise dans des variantes
    in_use = await ProductVariantAttribute.find_one(
        {"attribute_name": {"$regex": f"^{re.escape(attr.name)}$", "$options": "i"}}
    )
    if in_use:
        raise HTTPException(status_code=400, detail="Attribut utilisé par des variantes, suppression interdite")
    # Delete associated values first
    await CategoryAttributeValue.find(
        CategoryAttributeValue.attribute_id == attr.attribute_id
    ).delete()
    await attr.delete()
    return {"message": "Attribut supprimé avec succès"}

@router.post("/categories/{category_id}/attributes/{attribute_id}/values", response_model=CategoryAttributeValueResponse)
async def create_attribute_value(
    category_id: str,
    attribute_id: int,
    payload: CategoryAttributeValueCreate,
    current_user = Depends(require_role("admin"))
):
    category = await _category_or_404(category_id)
    attr = await CategoryAttribute.find_one(
        CategoryAttribute.attribute_id == attribute_id,
        CategoryAttribute.category_id == category.category_id
    )
    if not attr:
        raise HTTPException(status_code=404, detail="Attribut non trouvé")
    code = payload.code or _slugify(payload.value)
    existing = await CategoryAttributeValue.find_one(
        CategoryAttributeValue.attribute_id == attribute_id,
        {"code": {"$regex": f"^{re.escape(code)}$", "$options": "i"}}
    )
    if existing:
        raise HTTPException(status_code=400, detail="Code de valeur déjà utilisé pour cet attribut")
    val_id = await get_next_id("category_attribute_values")
    val = CategoryAttributeValue(
        value_id=val_id,
        attribute_id=attribute_id,
        value=payload.value,
        code=code,
        sort_order=payload.sort_order or 0
    )
    await val.insert()
    return val

@router.put("/categories/{category_id}/attributes/{attribute_id}/values/{value_id}", response_model=CategoryAttributeValueResponse)
async def update_attribute_value(
    category_id: str,
    attribute_id: int,
    value_id: int,
    payload: CategoryAttributeValueUpdate,
    current_user = Depends(require_role("admin"))
):
    _ = await _category_or_404(category_id)
    val = await CategoryAttributeValue.find_one(
        CategoryAttributeValue.value_id == value_id,
        CategoryAttributeValue.attribute_id == attribute_id
    )
    if not val:
        raise HTTPException(status_code=404, detail="Valeur non trouvée")
    if payload.value is not None:
        val.value = payload.value
    if payload.code is not None:
        existing = await CategoryAttributeValue.find_one(
            CategoryAttributeValue.attribute_id == attribute_id,
            {"code": {"$regex": f"^{re.escape(payload.code)}$", "$options": "i"}},
            CategoryAttributeValue.value_id != value_id
        )
        if existing:
            raise HTTPException(status_code=400, detail="Code de valeur déjà utilisé pour cet attribut")
        val.code = payload.code
    if payload.sort_order is not None:
        val.sort_order = payload.sort_order
    await val.save()
    return val

@router.delete("/categories/{category_id}/attributes/{attribute_id}/values/{value_id}")
async def delete_attribute_value(
    category_id: str,
    attribute_id: int,
    value_id: int,
    current_user = Depends(require_role("admin"))
):
    category = await _category_or_404(category_id)
    attr = await CategoryAttribute.find_one(
        CategoryAttribute.attribute_id == attribute_id,
        CategoryAttribute.category_id == category.category_id
    )
    if not attr:
        raise HTTPException(status_code=404, detail="Attribut non trouvé")
    val = await CategoryAttributeValue.find_one(
        CategoryAttributeValue.value_id == value_id,
        CategoryAttributeValue.attribute_id == attribute_id
    )
    if not val:
        raise HTTPException(status_code=404, detail="Valeur non trouvée")
    # empecher suppression si valeur utilisee
    in_use = await ProductVariantAttribute.find_one(
        {"$and": [
            {"attribute_name": {"$regex": f"^{re.escape(attr.name)}$", "$options": "i"}},
            {"attribute_value": {"$regex": f"^{re.escape(val.value)}$", "$options": "i"}}
        ]}
    )
    if in_use:
        raise HTTPException(status_code=400, detail="Valeur utilisée par des variantes, suppression interdite")
    await val.delete()
    return {"message": "Valeur supprimée avec succès"}

# Pour la compatibilite avec l'ancien endpoint
@router.get("/categories/list")
async def get_categories_list(
    current_user = Depends(get_current_user)
):
    """Obtenir la liste des categories uniques (ancien format)"""
    products = await Product.find(
        {"category": {"$ne": None}}
    ).to_list()
    categories = list({p.category for p in products if p.category})
    return sorted(categories)


@router.get("/stats")
async def get_products_stats(
    current_user = Depends(get_current_user)
):
    """Endpoint agrege et mis en cache pour accelerer la page Produits."""
    try:
        cache_key = _get_cache_key("products_stats")

        async def compute():
            total_products = await Product.find().count()

            # Produits avec variantes (distinct product_id)
            all_variants = await ProductVariant.find().to_list()
            pids_with_variants = set()
            pids_with_available = set()
            for v in all_variants:
                pids_with_variants.add(v.product_id)
                if not v.is_sold:
                    pids_with_available.add(v.product_id)

            with_variants = len(pids_with_variants)
            without_variants = int(total_products) - int(with_variants)

            # In stock: quantity > 0 OR has available variants
            all_products = await Product.find().to_list()
            in_stock = 0
            out_of_stock = 0
            with_barcode = 0
            without_barcode = 0

            for p in all_products:
                has_stock = (p.quantity or 0) > 0 or p.product_id in pids_with_available
                if has_stock:
                    in_stock += 1
                else:
                    out_of_stock += 1

                bc = (p.barcode or "").strip()
                if bc:
                    with_barcode += 1
                else:
                    without_barcode += 1

            # Categories + count
            categories_docs = await Category.find().to_list()
            categories = []
            for cat in categories_docs:
                count = await Product.find(Product.category == cat.name).count()
                categories.append({
                    "id": str(cat.category_id),
                    "name": str(cat.name),
                    "requires_variants": bool(getattr(cat, 'requires_variants', False)),
                    "product_count": int(count or 0),
                })

            # Conditions autorisees
            conditions_cfg = await _get_allowed_conditions()

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

        result = await _get_cached_or_compute(cache_key, compute)
        return result
    except Exception as e:
        logging.error(f"Erreur /products/stats: {e}")
        # Fallback minimal
        try:
            conds = await _get_allowed_conditions()
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
    """Vider le cache lie aux endpoints produits (admin recommande)."""
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
    current_user = Depends(require_any_role(["manager"]))
):
    """Upload une image pour un produit"""
    try:
        # Verifier que le produit existe
        product = await Product.find_one(Product.product_id == product_id)
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

        # Creer le dossier uploads/products s'il n'existe pas
        upload_dir = Path("static/uploads/products")
        upload_dir.mkdir(parents=True, exist_ok=True)

        # Generer un nom de fichier unique
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

        # Mettre a jour le produit avec le chemin de l'image
        product.image_path = str(file_path)
        await product.save()

        return {
            "message": "Image uploadée avec succès",
            "image_path": str(file_path),
            "product_id": product_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de l'upload de l'image: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de l'upload de l'image")


@router.delete("/id/{product_id}/delete-image")
async def delete_product_image(
    product_id: int,
    current_user = Depends(require_any_role(["manager"]))
):
    """Supprimer l'image d'un produit"""
    try:
        product = await Product.find_one(Product.product_id == product_id)
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

        # Mettre a jour le produit
        product.image_path = None
        await product.save()

        return {"message": "Image supprimée avec succès"}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la suppression de l'image: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la suppression de l'image")

@router.put("/{product_id}/archive")
async def archive_product(
    product_id: int,
    current_user = Depends(get_current_user)
):
    """Archiver un produit (le masquer de la liste par defaut)"""
    try:
        product = await Product.find_one(Product.product_id == product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        product.is_archived = True
        await product.save()
        return {"message": "Produit archivé avec succès", "is_archived": True}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de l'archivage du produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de l'archivage")

@router.put("/{product_id}/unarchive")
async def unarchive_product(
    product_id: int,
    current_user = Depends(get_current_user)
):
    """Desarchiver un produit (le rendre visible a nouveau)"""
    try:
        product = await Product.find_one(Product.product_id == product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        product.is_archived = False
        await product.save()
        return {"message": "Produit désarchivé avec succès", "is_archived": False}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors du désarchivage du produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors du désarchivage")

@router.post("/archive-sold")
async def archive_sold_products(
    current_user = Depends(get_current_user)
):
    """Archiver tous les produits vendus (stock = 0 et toutes variantes vendues)"""
    try:
        # Trouver les produits non archives
        products = await Product.find(
            {"$or": [{"is_archived": False}, {"is_archived": None}, {"is_archived": {"$exists": False}}]}
        ).to_list()

        archived_count = 0
        for product in products:
            # Verifier si le produit a des variantes
            variants = await ProductVariant.find(
                ProductVariant.product_id == product.product_id
            ).to_list()

            if variants:
                # Si toutes les variantes sont vendues, archiver
                all_sold = all(v.is_sold for v in variants)
                if all_sold:
                    product.is_archived = True
                    await product.save()
                    archived_count += 1
            else:
                # Pas de variantes, verifier le stock
                if product.quantity == 0:
                    product.is_archived = True
                    await product.save()
                    archived_count += 1

        return {"message": f"{archived_count} produit(s) archivé(s)", "archived_count": archived_count}
    except Exception as e:
        logging.error(f"Erreur lors de l'archivage des produits vendus: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de l'archivage")

@router.post("/{product_id}/duplicate", response_model=ProductResponse)
async def duplicate_product(
    product_id: int,
    current_user = Depends(get_current_user)
):
    """Dupliquer un produit existant avec toutes ses proprietes (sans les variantes)"""
    try:
        original = await Product.find_one(Product.product_id == product_id)
        if not original:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        # Creer une copie du produit
        new_product_id = await get_next_id("products")
        new_product = Product(
            product_id=new_product_id,
            name=f"{original.name} (copie)",
            description=original.description,
            quantity=0,  # Stock a 0 pour la copie
            price=original.price,
            wholesale_price=original.wholesale_price,
            purchase_price=original.purchase_price,
            category=original.category,
            brand=original.brand,
            model=original.model,
            barcode=None,  # Pas de code-barres pour eviter les doublons
            condition=original.condition,
            has_unique_serial=original.has_unique_serial,
            notes=original.notes,
            image_path=original.image_path,
            is_archived=False,
        )

        await new_product.insert()

        # Enrich with empty variants for response
        new_product.variants = []
        return new_product
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la duplication du produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la duplication")
