from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, and_, or_
from typing import List, Optional
from datetime import datetime, date, timedelta
import httpx
from ..database import (
    get_db,
    Invoice,
    InvoiceItem,
    InvoicePayment,
    Client,
    Product,
    ProductVariant,
    DeliveryNote,
    DeliveryNoteItem,
    SupplierInvoice,
    SupplierInvoicePayment,
    DailySale,
)
from ..database import DailyPurchase
from ..schemas import InvoiceCreate, InvoiceResponse, InvoiceItemResponse
from ..auth import get_current_user
from ..routers.stock_movements import create_stock_movement
from ..services.stats_manager import recompute_invoices_stats
from ..services.google_sheets_sync_helper import sync_product_stock_to_sheets
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import logging
import os

router = APIRouter(prefix="/api/invoices", tags=["invoices"]) 

# Helpers de numérotation
from datetime import datetime as _dt

def _next_invoice_number(db: Session, prefix: Optional[str] = None) -> str:
    """Génère le prochain numéro de facture séquentiel sous la forme PREFIX-####.
    Par défaut, PREFIX = 'FAC'. L'algorithme recherche d'abord les numéros
    existants au format exact PREFIX-<digits> et incrémente le plus grand.
    S'il n'en trouve pas, il tente un fallback sur le plus grand suffixe
    numérique présent et repart ensuite proprement.
    """
    import re
    pf = (prefix or 'FAC').strip('-')
    base_prefix = f"{pf}-"

    # Récupérer tous les numéros existants qui commencent par PREFIX-
    try:
        rows = db.query(Invoice.invoice_number).filter(Invoice.invoice_number.ilike(f"{base_prefix}%")).all()
    except Exception:
        rows = []

    last_seq = 0
    # 1) Chercher le max parmi les numéros au format exact PREFIX-####
    for (num,) in (rows or []):
        if not isinstance(num, str):
            continue
        m = re.fullmatch(rf"{re.escape(pf)}-(\\d+)", num.strip())
        if m:
            val = int(m.group(1))
            if val > last_seq:
                last_seq = val

    # 2) Fallback: si aucun au format exact, prendre le plus grand suffixe numérique
    if last_seq == 0:
        for (num,) in (rows or []):
            if not isinstance(num, str):
                continue
            matches = re.findall(r'(\\d+)', num.strip())
            if matches:
                val = int(matches[-1])  # dernier groupe de chiffres
                if val > last_seq:
                    last_seq = val

    next_seq = last_seq + 1

    # Garantir l'unicité (en cas de race, trous, etc.)
    while True:
        candidate = f"{base_prefix}{next_seq:04d}"
        exists = db.query(Invoice).filter(Invoice.invoice_number == candidate).first()
        if not exists:
            return candidate
        next_seq += 1

@router.get("/next-number")
async def get_next_invoice_number(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Retourne le prochain numéro de facture disponible (FAC-####).
    Placé avant la route dynamique '/{invoice_id}' pour éviter un 422 dû à la résolution de chemin.
    """
    try:
        return {"invoice_number": _next_invoice_number(db)}
    except Exception as e:
        logging.error(f"Erreur get_next_invoice_number: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/", response_model=List[InvoiceResponse])
async def list_invoices(
    skip: int = 0,
    limit: int = 100,
    status_filter: Optional[str] = None,
    client_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les factures avec filtres"""
    # Utiliser un JOIN avec la table des clients pour récupérer le nom du client
    query = db.query(Invoice, Client.name.label('client_name')).join(Client, Invoice.client_id == Client.client_id).order_by(desc(Invoice.created_at))
    
    if status_filter:
        query = query.filter(Invoice.status == status_filter)
    
    if client_id:
        query = query.filter(Invoice.client_id == client_id)
    
    if start_date:
        query = query.filter(func.date(Invoice.date) >= start_date)
    
    if end_date:
        query = query.filter(func.date(Invoice.date) <= end_date)
    
    results = query.offset(skip).limit(limit).all()
    
    # Construire la réponse avec le nom du client
    invoices = []
    for invoice, client_name in results:
        invoice_dict = {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "client_id": invoice.client_id,
            "client_name": client_name,  # Ajouter le nom du client
            "quotation_id": invoice.quotation_id,
            "date": invoice.date,
            "due_date": invoice.due_date,
            "status": invoice.status,
            "payment_method": invoice.payment_method,
            "subtotal": float(invoice.subtotal or 0),
            "tax_rate": float(invoice.tax_rate or 0),
            "tax_amount": float(invoice.tax_amount or 0),
            "total": float(invoice.total or 0),
            "paid_amount": float(invoice.paid_amount or 0),
            "remaining_amount": float(invoice.remaining_amount or 0),
            "notes": invoice.notes,
            "show_tax": bool(invoice.show_tax),
            "show_item_prices": bool(getattr(invoice, 'show_item_prices', True)),
            "show_section_totals": bool(getattr(invoice, 'show_section_totals', True)),
            "price_display": invoice.price_display or "FCFA",
            # Champs de garantie
            "has_warranty": bool(getattr(invoice, "has_warranty", False)),
            "warranty_duration": getattr(invoice, "warranty_duration", None),
            "warranty_start_date": getattr(invoice, "warranty_start_date", None),
            "warranty_end_date": getattr(invoice, "warranty_end_date", None),
            "created_at": invoice.created_at,
            "items": []
        }
        invoices.append(invoice_dict)
    
    return invoices

# Simple in-process cache for list responses
_invoices_cache = {}
_CACHE_TTL_SECONDS = 30

@router.get("/paginated")
async def list_invoices_paginated(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    status_filter: Optional[str] = None,
    client_search: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    sort_by: Optional[str] = Query("created_at"),  # created_at | date | number | total | status | client
    sort_dir: Optional[str] = Query("desc"),       # asc | desc
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les factures avec pagination, filtres et tri pour la liste principale."""
    # Cache key
    try:
        import time, hashlib
        key_raw = f"p={page}|s={page_size}|sf={status_filter}|cs={client_search}|q={search}|sd={start_date}|ed={end_date}|ob={sort_by}|od={sort_dir}"
        key = hashlib.md5(key_raw.encode()).hexdigest()
        entry = _invoices_cache.get(key)
        if entry and (time.time() - entry['ts']) < _CACHE_TTL_SECONDS:
            return entry['data']
    except Exception:
        key = None
    # Base avec JOIN client pour récupérer le nom
    base = db.query(
        Invoice,
        Client.name.label('client_name')
    ).join(Client, Client.client_id == Invoice.client_id, isouter=True)

    # Filtres
    if status_filter:
        base = base.filter(Invoice.status == status_filter)
    if client_search:
        like = f"%{client_search.strip()}%"
        base = base.filter(Client.name.ilike(like))
    if start_date:
        base = base.filter(func.date(Invoice.date) >= start_date)
    if end_date:
        base = base.filter(func.date(Invoice.date) <= end_date)
    if search:
        s = search.strip()

        # Sous-requête: factures contenant un produit/une variante correspondant au code (barcode/IMEI)
        try:
            from sqlalchemy.sql import exists as _exists
        except Exception:
            _exists = None

        product_match_invoice_ids = None
        try:
            # Construire une sous-requête sur InvoiceItem -> Product -> ProductVariant
            items_q = (
                db.query(InvoiceItem.invoice_id)
                .join(Product, InvoiceItem.product_id == Product.product_id, isouter=True)
                .join(ProductVariant, ProductVariant.product_id == Product.product_id, isouter=True)
            )
            like = f"%{s}%"
            items_q = items_q.filter(
                or_(
                    func.trim(Product.barcode).ilike(like),
                    func.trim(ProductVariant.barcode).ilike(like),
                    func.trim(ProductVariant.imei_serial).ilike(like),
                )
            ).distinct()
            product_match_invoice_ids = [row[0] for row in items_q.all()]
        except Exception:
            product_match_invoice_ids = None

        if s.isdigit():
            # Recherche numérique: matcher l'ID de facture, le numéro, ET les produits/IMEI éventuels
            try:
                id_val = int(s)
            except Exception:
                id_val = None

            conditions = []
            if id_val is not None:
                conditions.append(Invoice.invoice_id == id_val)
            conditions.append(Invoice.invoice_number.ilike(f"%{s}%"))
            if product_match_invoice_ids:
                conditions.append(Invoice.invoice_id.in_(product_match_invoice_ids))

            base = base.filter(or_(*conditions))
        else:
            # Recherche texte: numéro de facture ou IMEI/barcode produit/variante
            conditions = [Invoice.invoice_number.ilike(f"%{s}%")]
            if product_match_invoice_ids:
                conditions.append(Invoice.invoice_id.in_(product_match_invoice_ids))
            base = base.filter(or_(*conditions))

    # Total avant pagination
    total = base.count()

    # Tri
    sort_col = Invoice.created_at
    if sort_by == "date":
        sort_col = Invoice.date
    elif sort_by == "number":
        sort_col = Invoice.invoice_number
    elif sort_by == "total":
        sort_col = Invoice.total
    elif sort_by == "status":
        sort_col = Invoice.status
    elif sort_by == "client":
        sort_col = Client.name

    if (sort_dir or "").lower() == "asc":
        base = base.order_by(sort_col.asc())
    else:
        base = base.order_by(sort_col.desc())

    # Pagination
    skip = (page - 1) * page_size
    rows = base.offset(skip).limit(page_size).all()

    # Façonner la réponse légère (pas d'items/payments pour la liste,
    # et surtout pas le champ "notes" qui peut contenir des signatures base64 très lourdes)
    result_invoices = []
    for inv, client_name in rows:
        result_invoices.append({
            "invoice_id": inv.invoice_id,
            "invoice_number": inv.invoice_number,
            "client_id": inv.client_id,
            "client_name": client_name or "",
            "quotation_id": inv.quotation_id,
            "date": inv.date,
            "due_date": inv.due_date,
            "status": inv.status,
            "payment_method": inv.payment_method,
            "subtotal": float(inv.subtotal or 0),
            "tax_rate": float(inv.tax_rate or 0),
            "tax_amount": float(inv.tax_amount or 0),
            "total": float(inv.total or 0),
            "paid_amount": float(inv.paid_amount or 0),
            "remaining_amount": float(inv.remaining_amount or 0),
            "show_tax": bool(inv.show_tax),
            "price_display": inv.price_display or "FCFA",
            "created_at": inv.created_at,
        })

    result = {
        "invoices": result_invoices,
        "total": total,
        "page": page,
        "pages": (total + page_size - 1) // page_size if total > 0 else 1,
    }

    # Store in cache
    try:
        if key:
            import time
            _invoices_cache[key] = { 'ts': time.time(), 'data': result }
    except Exception:
        pass

    return result

@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir une facture par ID avec items, paiements et nom du client"""
    invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Facture non trouvée")

    # Forcer chargement relations
    _ = invoice.items
    _ = invoice.payments

    client_name = None
    client_phone = None
    try:
        client_data = db.query(Client.name, Client.phone).filter(Client.client_id == invoice.client_id).first()
        if client_data:
            client_name = client_data.name
            client_phone = client_data.phone
    except Exception:
        client_name = None
        client_phone = None

    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "client_id": invoice.client_id,
        "client_name": client_name,
        "client": {"name": client_name, "phone": client_phone} if client_name else None,
        "date": invoice.date,
        "due_date": invoice.due_date,
        "status": invoice.status,
        "payment_method": invoice.payment_method,
        "subtotal": float(invoice.subtotal or 0),
        "tax_rate": float(invoice.tax_rate or 0),
        "tax_amount": float(invoice.tax_amount or 0),
        "total": float(invoice.total or 0),
        "paid_amount": float(invoice.paid_amount or 0),
        "remaining_amount": float(invoice.remaining_amount or 0),
        "show_tax": bool(invoice.show_tax),
        "show_item_prices": bool(getattr(invoice, 'show_item_prices', True)),
        "show_section_totals": bool(getattr(invoice, 'show_section_totals', True)),
        "notes": invoice.notes,
        # Champs de garantie
        "has_warranty": bool(getattr(invoice, "has_warranty", False)),
        "warranty_duration": getattr(invoice, "warranty_duration", None),
        "warranty_start_date": getattr(invoice, "warranty_start_date", None),
        "warranty_end_date": getattr(invoice, "warranty_end_date", None),
        "items": [
            {
                "item_id": it.item_id,
                "product_id": it.product_id,
                "product_name": it.product_name,
                "quantity": it.quantity,
                "price": float(it.price or 0),
                "total": float(it.total or 0)
            } for it in (invoice.items or [])
        ],
        "payments": [
            {
                "payment_id": p.payment_id,
                "amount": float(p.amount or 0),
                "payment_date": p.payment_date,
                "payment_method": p.payment_method,
                "reference": p.reference
            } for p in (invoice.payments or [])
        ]
    }

@router.post("/", response_model=InvoiceResponse)
async def create_invoice(
    invoice_data: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Créer une nouvelle facture.
    - Si le numéro est vide ou déjà utilisé, génère automatiquement le prochain numéro disponible (FAC-####).
    """
    try:
        # Vérifier que le client existe
        client = db.query(Client).filter(Client.client_id == invoice_data.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
        
        # Déterminer le numéro final (tolère vide/auto/duplicate)
        requested_number = (str(invoice_data.invoice_number or '').strip())
        final_number = None
        if not requested_number or requested_number.upper() in {"AUTO", "AUTOMATIC"}:
            final_number = _next_invoice_number(db)
        else:
            # Si déjà existant, basculer sur le prochain disponible
            exists = db.query(Invoice).filter(Invoice.invoice_number == requested_number).first()
            final_number = requested_number if not exists else _next_invoice_number(db)
        
        # Calculer le montant restant
        remaining_amount = invoice_data.total
        
        # Déterminer la date d'échéance par défaut à J+4 si non fournie
        try:
            final_due_date = invoice_data.due_date or (invoice_data.date + timedelta(days=4))
        except Exception:
            final_due_date = invoice_data.due_date or (datetime.utcnow() + timedelta(days=4))

        # Créer la facture
        db_invoice = Invoice(
            invoice_number=final_number,
            client_id=invoice_data.client_id,
            quotation_id=invoice_data.quotation_id,
            date=invoice_data.date,
            due_date=final_due_date,
            payment_method=invoice_data.payment_method,
            subtotal=invoice_data.subtotal,
            tax_rate=invoice_data.tax_rate,
            tax_amount=invoice_data.tax_amount,
            total=invoice_data.total,
            remaining_amount=remaining_amount,
            notes=invoice_data.notes,
            show_tax=invoice_data.show_tax,
            show_item_prices=getattr(invoice_data, 'show_item_prices', True),
            show_section_totals=getattr(invoice_data, 'show_section_totals', True),
            price_display=invoice_data.price_display,
            # Champs de garantie
            has_warranty=bool(getattr(invoice_data, "has_warranty", False)),
            warranty_duration=getattr(invoice_data, "warranty_duration", None),
            warranty_start_date=getattr(invoice_data, "warranty_start_date", None),
            warranty_end_date=getattr(invoice_data, "warranty_end_date", None),
            status="en attente",
            created_by=current_user.user_id
        )
        
        db.add(db_invoice)
        db.flush()  # Pour obtenir l'ID de la facture
        
        # Créer les éléments de facture et gérer le stock
        for item_data in invoice_data.items:
            # Lignes personnalisées sans produit: pas d'impact stock
            if not getattr(item_data, 'product_id', None):
                # Ensure custom line name respects DB length
                safe_custom_name = (item_data.product_name or 'Service')[:100]
                db_item = InvoiceItem(
                    invoice_id=db_invoice.invoice_id,
                    product_id=None,
                    product_name=safe_custom_name,
                    quantity=item_data.quantity,
                    price=item_data.price,
                    total=item_data.total
                )
                db.add(db_item)
                continue

            # Vérifier que le produit existe
            product = db.query(Product).filter(Product.product_id == item_data.product_id).first()
            if not product:
                raise HTTPException(status_code=404, detail=f"Produit {item_data.product_id} non trouvé")
            
            # Déterminer si le produit possède des variantes
            has_variants = db.query(ProductVariant.variant_id).filter(ProductVariant.product_id == product.product_id).first() is not None

            if has_variants:
                # Les produits à variantes ne peuvent pas utiliser une quantité agrégée
                # Exiger une variante explicite (ID ou IMEI) et forcer quantity=1 par ligne
                resolved_variant = None
                if getattr(item_data, 'variant_id', None):
                    resolved_variant = db.query(ProductVariant).filter(ProductVariant.variant_id == item_data.variant_id).first()
                    if not resolved_variant:
                        raise HTTPException(status_code=404, detail=f"Variante {item_data.variant_id} introuvable")
                elif getattr(item_data, 'variant_imei', None):
                    imei_code = str(item_data.variant_imei).strip()
                    resolved_variant = db.query(ProductVariant).filter(
                        ProductVariant.product_id == product.product_id,
                        func.trim(ProductVariant.imei_serial) == imei_code
                    ).first()
                    if not resolved_variant:
                        raise HTTPException(status_code=404, detail=f"Variante avec IMEI {imei_code} introuvable")
                else:
                    raise HTTPException(status_code=400, detail="Produit avec variantes: vous devez sélectionner des variantes (IMEI) au lieu de définir une quantité")

                # Valider l'appartenance et la disponibilité de la variante
                if resolved_variant.product_id != product.product_id:
                    raise HTTPException(status_code=400, detail="Variante n'appartient pas au produit")
                if bool(resolved_variant.is_sold):
                    raise HTTPException(status_code=400, detail=f"La variante {resolved_variant.imei_serial} est déjà vendue")

                # Forcer quantité = 1 pour une ligne de variante
                if int(item_data.quantity or 0) != 1:
                    raise HTTPException(status_code=400, detail="Pour un produit avec variantes, la quantité doit être 1 par ligne de variante")

                # Marquer la variante comme vendue
                resolved_variant.is_sold = True
            else:
                # Produits sans variantes: vérifier stock disponible agrégé
                if (product.quantity or 0) < item_data.quantity:
                    raise HTTPException(status_code=400, detail=f"Stock insuffisant pour le produit {product.name}")
            
            # Créer l'élément de facture
            # Ensure product_name respects DB length (String(100))
            safe_name = (item_data.product_name or product.name)[:100]
            db_item = InvoiceItem(
                invoice_id=db_invoice.invoice_id,
                product_id=item_data.product_id,
                product_name=safe_name,
                quantity=item_data.quantity,
                price=item_data.price,
                total=item_data.total
            )
            db.add(db_item)
            
            # Mettre à jour le stock et créer un mouvement
            product.quantity = (product.quantity or 0) - item_data.quantity
            try:
                create_stock_movement(
                    db=db,
                    product_id=item_data.product_id,
                    quantity=item_data.quantity,
                    movement_type="OUT",
                    reference_type="INVOICE",
                    reference_id=db_invoice.invoice_id,
                    notes=f"Vente - Facture {final_number}",
                    unit_price=float(item_data.price)
                )
            except Exception:
                # Ne pas bloquer la création de facture si l'enregistrement du mouvement échoue
                pass

            # Synchroniser le stock avec Google Sheets (si activé)
            try:
                sync_product_stock_to_sheets(db, item_data.product_id)
            except Exception as e:
                # Ne pas bloquer la création de facture si la sync Google Sheets échoue
                logging.warning(f"Échec de synchronisation Google Sheets pour le produit {item_data.product_id}: {e}")
                pass
        
        db.commit()
        db.refresh(db_invoice)
        
        # Créer automatiquement les ventes quotidiennes pour chaque produit de la facture
        try:
            for item_data in invoice_data.items:
                if getattr(item_data, 'product_id', None):  # Seulement pour les produits réels
                    product = db.query(Product).filter(Product.product_id == item_data.product_id).first()
                    if not product:
                        continue

                    # Préparer les infos de variante si applicable
                    variant_id_val = None
                    variant_imei_val = None
                    variant_barcode_val = None
                    variant_condition_val = None
                    try:
                        has_variants = (
                            db.query(ProductVariant.variant_id)
                            .filter(ProductVariant.product_id == product.product_id)
                            .first()
                            is not None
                        )
                        if has_variants:
                            resolved_variant = None
                            if getattr(item_data, 'variant_id', None):
                                resolved_variant = (
                                    db.query(ProductVariant)
                                    .filter(ProductVariant.variant_id == item_data.variant_id)
                                    .first()
                                )
                            elif getattr(item_data, 'variant_imei', None):
                                imei_code = str(item_data.variant_imei).strip()
                                if imei_code:
                                    resolved_variant = (
                                        db.query(ProductVariant)
                                        .filter(
                                            ProductVariant.product_id == product.product_id,
                                            func.trim(ProductVariant.imei_serial) == imei_code,
                                        )
                                        .first()
                                    )
                            if resolved_variant is not None:
                                variant_id_val = resolved_variant.variant_id
                                variant_imei_val = resolved_variant.imei_serial
                                variant_barcode_val = resolved_variant.barcode
                                variant_condition_val = resolved_variant.condition
                    except Exception:
                        pass

                    daily_sale = DailySale(
                        client_id=invoice_data.client_id,
                        client_name=client.name,
                        product_id=item_data.product_id,
                        product_name=item_data.product_name or product.name,
                        variant_id=variant_id_val,
                        variant_imei=variant_imei_val,
                        variant_barcode=variant_barcode_val,
                        variant_condition=variant_condition_val,
                        quantity=item_data.quantity,
                        unit_price=item_data.price,
                        total_amount=item_data.total,
                        sale_date=invoice_data.date.date(),
                        payment_method=invoice_data.payment_method or "espece",
                        invoice_id=db_invoice.invoice_id,
                        notes=f"Vente automatique depuis facture {final_number}",
                    )
                    db.add(daily_sale)

            db.commit()
        except Exception as e:
            # Ne pas bloquer la création de facture si l'enregistrement des ventes quotidiennes échoue
            logging.warning(f"Erreur lors de la création des ventes quotidiennes: {e}")
            pass
        
        # Clear invoices cache after creation to ensure fresh data on next load
        _invoices_cache.clear()
        
        try:
            # Mettre à jour les stats persistées
            recompute_invoices_stats(db)
        except Exception:
            pass

        # Façonner et retourner la réponse complète avec client_name
        try:
            client_name = db.query(Client.name).filter(Client.client_id == db_invoice.client_id).scalar() or ""
        except Exception:
            client_name = ""
        try:
            _ = db_invoice.items
        except Exception:
            pass
        return {
            "invoice_id": db_invoice.invoice_id,
            "invoice_number": db_invoice.invoice_number,
            "client_id": db_invoice.client_id,
            "client_name": client_name,
            "quotation_id": db_invoice.quotation_id,
            "date": db_invoice.date,
            "due_date": db_invoice.due_date,
            "status": db_invoice.status,
            "payment_method": db_invoice.payment_method,
            "subtotal": float(db_invoice.subtotal or 0),
            "tax_rate": float(db_invoice.tax_rate or 0),
            "tax_amount": float(db_invoice.tax_amount or 0),
            "total": float(db_invoice.total or 0),
            "paid_amount": float(db_invoice.paid_amount or 0),
            "remaining_amount": float(db_invoice.remaining_amount or 0),
            "notes": db_invoice.notes,
            "show_tax": bool(db_invoice.show_tax),
            "price_display": db_invoice.price_display or "FCFA",
            # Champs de garantie
            "has_warranty": bool(getattr(db_invoice, "has_warranty", False)),
            "warranty_duration": getattr(db_invoice, "warranty_duration", None),
            "warranty_start_date": getattr(db_invoice, "warranty_start_date", None),
            "warranty_end_date": getattr(db_invoice, "warranty_end_date", None),
            "created_at": db_invoice.created_at,
            "items": [
                {
                    "item_id": it.item_id,
                    "product_id": it.product_id,
                    "product_name": it.product_name,
                    "quantity": it.quantity,
                    "price": float(it.price or 0),
                    "total": float(it.total or 0),
                }
                for it in (db_invoice.items or [])
            ],
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception(f"Erreur lors de la création de la facture")
        if str(os.getenv("DEBUG_ERRORS", "")).lower() == "true":
            raise HTTPException(status_code=500, detail=f"Erreur serveur: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: int,
    invoice_data: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Mettre à jour une facture existante avec réconciliation du stock et des variantes.

    Stratégie:
    - Restaurer le stock des anciens items (IN) et tenter de réactiver les variantes vendues
      en se basant sur les métadonnées de notes (__SERIALS__) ou, à défaut, sur le libellé (IMEI: ...).
      En dernier recours, désactiver l'état vendu de n variantes correspondant à la quantité.
    - Remplacer les items par ceux du payload et appliquer le nouveau stock (OUT) + variantes vendues.
    - Mettre à jour les montants et le statut en cohérence avec le montant payé actuel.
    """
    try:
        # Charger la facture existante
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")


        # Vérifier que le client existe
        client = db.query(Client).filter(Client.client_id == invoice_data.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")

        # 1) REVERT: restaurer le stock des anciens items et réactiver variantes
        #   a) Restaurer le stock pour chaque item produit
        old_items = list(invoice.items or [])
        for it in old_items:
            if it.product_id is None:
                continue
            product = db.query(Product).filter(Product.product_id == it.product_id).first()
            if product:
                try:
                    product.quantity = (product.quantity or 0) + int(it.quantity or 0)
                except Exception:
                    product.quantity = (product.quantity or 0)
                # Mouvement IN pour revert
                try:
                    create_stock_movement(
                        db=db,
                        product_id=it.product_id,
                        quantity=int(it.quantity or 0),
                        movement_type="IN",
                        reference_type="INV_UPDATE_REVERT",  # Shortened to fit VARCHAR(20)
                        reference_id=invoice_id,
                        notes=f"Revert mise à jour facture {invoice.invoice_number}",
                        unit_price=float(it.price or 0),
                    )
                except Exception:
                    pass

        #   b) Tenter de réactiver les variantes vendues pour les anciens items
        try:
            serials_meta = []
            if invoice.notes:
                import re, json
                txt = str(invoice.notes)
                if "__SERIALS__=" in txt:
                    sub = txt.split("__SERIALS__=", 1)[1]
                    cut_idx = sub.find("\n__")
                    if cut_idx != -1:
                        sub = sub[:cut_idx].strip()
                    sub = sub.strip()
                    try:
                        serials_meta = json.loads(sub)
                    except Exception:
                        m = re.search(r"__SERIALS__=(\[.*?\])", txt, flags=re.S)
                        if m:
                            serials_meta = json.loads(m.group(1))

            processed_products = set()
            # 1) Depuis meta notes
            for entry in (serials_meta or []):
                pid = entry.get("product_id")
                if pid is not None:
                    processed_products.add(int(pid))
                for imei in (entry.get("imeis") or []):
                    variant = db.query(ProductVariant).filter(func.trim(ProductVariant.imei_serial) == str(imei).strip()).first()
                    if variant and bool(variant.is_sold):
                        variant.is_sold = False

            # 2) Fallback: IMEI dans le libellé de ligne
            import re as _re
            for it in (old_items or []):
                if it.product_id is None:
                    continue
                name = it.product_name or ""
                m2 = _re.search(r"\(IMEI:\s*([^)]+)\)", name, flags=_re.I)
                if not m2:
                    continue
                imei = (m2.group(1) or '').strip()
                if not imei:
                    continue
                try:
                    processed_products.add(int(it.product_id))
                except Exception:
                    pass
                variant = db.query(ProductVariant).filter(func.trim(ProductVariant.imei_serial) == imei).first()
                if variant and bool(variant.is_sold):
                    variant.is_sold = False

            # 3) Ultime fallback: désactiver l'état vendu pour autant de variantes que la quantité (par produit)
            for it in (old_items or []):
                pid = int(it.product_id) if it.product_id is not None else None
                if pid is None:
                    continue
                if pid in processed_products:
                    continue
                try:
                    qty = int(it.quantity or 0)
                except Exception:
                    qty = 0
                if qty <= 0:
                    continue
                sold_variants = (
                    db.query(ProductVariant)
                    .filter(ProductVariant.product_id == pid, ProductVariant.is_sold == True)
                    .limit(qty)
                    .all()
                )
                for v in sold_variants:
                    v.is_sold = False
        except Exception:
            # Ne pas bloquer la mise à jour si la réactivation des variantes échoue
            pass

        # Supprimer les anciens items
        for it in old_items:
            try:
                db.delete(it)
            except Exception:
                pass
        db.flush()

        # 2) APPLY: mettre à jour la facture et recréer les items avec nouveaux impacts stock/variants
        invoice.invoice_number = invoice.invoice_number
        invoice.client_id = invoice_data.client_id
        invoice.quotation_id = invoice_data.quotation_id
        invoice.date = invoice_data.date
        invoice.due_date = invoice_data.due_date
        invoice.payment_method = invoice_data.payment_method
        invoice.subtotal = invoice_data.subtotal
        invoice.tax_rate = invoice_data.tax_rate
        invoice.tax_amount = invoice_data.tax_amount
        invoice.total = invoice_data.total
        invoice.notes = invoice_data.notes
        invoice.show_tax = bool(invoice_data.show_tax)
        invoice.show_item_prices = bool(getattr(invoice_data, 'show_item_prices', True))
        invoice.show_section_totals = bool(getattr(invoice_data, 'show_section_totals', True))
        invoice.price_display = invoice_data.price_display
        # Champs de garantie
        invoice.has_warranty = bool(getattr(invoice_data, "has_warranty", False))
        invoice.warranty_duration = getattr(invoice_data, "warranty_duration", None)
        invoice.warranty_start_date = getattr(invoice_data, "warranty_start_date", None)
        invoice.warranty_end_date = getattr(invoice_data, "warranty_end_date", None)

        # Recalculer remaining_amount en fonction du payé existant
        try:
            paid = float(invoice.paid_amount or 0)
            total_val = float(invoice.total or 0)
            invoice.remaining_amount = max(0, total_val - paid)
            # Ajuster le statut si nécessaire
            if invoice.remaining_amount == 0:
                invoice.status = "payée"
            elif paid > 0:
                invoice.status = "partiellement payée"
            else:
                invoice.status = "en attente"
        except Exception:
            pass

        # Créer les nouveaux items et appliquer le stock
        for item_data in (invoice_data.items or []):
            # Lignes personnalisées sans produit: pas d'impact stock
            if not getattr(item_data, 'product_id', None):
                # Ensure custom line name respects DB length
                safe_custom_name = (item_data.product_name or 'Service')[:100]
                db_item = InvoiceItem(
                    invoice_id=invoice.invoice_id,
                    product_id=None,
                    product_name=safe_custom_name,
                    quantity=item_data.quantity,
                    price=item_data.price,
                    total=item_data.total,
                )
                db.add(db_item)
                continue

            # Vérifier produit
            product = db.query(Product).filter(Product.product_id == item_data.product_id).first()
            if not product:
                raise HTTPException(status_code=404, detail=f"Produit {item_data.product_id} non trouvé")

            # Déterminer si le produit possède des variantes
            has_variants = db.query(ProductVariant.variant_id).filter(ProductVariant.product_id == product.product_id).first() is not None

            if has_variants:
                # Pour la mise à jour, on est plus permissif: si aucune variante n'est spécifiée,
                # on permet quand même la mise à jour (les variantes ont été restaurées dans REVERT)
                resolved_variant = None
                if getattr(item_data, 'variant_id', None):
                    resolved_variant = db.query(ProductVariant).filter(ProductVariant.variant_id == item_data.variant_id).first()
                    if not resolved_variant:
                        raise HTTPException(status_code=404, detail=f"Variante {item_data.variant_id} introuvable")
                elif getattr(item_data, 'variant_imei', None):
                    imei_code = str(item_data.variant_imei).strip()
                    resolved_variant = db.query(ProductVariant).filter(
                        ProductVariant.product_id == product.product_id,
                        func.trim(ProductVariant.imei_serial) == imei_code
                    ).first()
                    if not resolved_variant:
                        raise HTTPException(status_code=404, detail=f"Variante avec IMEI {imei_code} introuvable")
                
                # Si une variante est spécifiée, valider et marquer comme vendue
                if resolved_variant:
                    if resolved_variant.product_id != product.product_id:
                        raise HTTPException(status_code=400, detail="Variante n'appartient pas au produit")
                    if bool(resolved_variant.is_sold):
                        raise HTTPException(status_code=400, detail=f"La variante {resolved_variant.imei_serial} est déjà vendue")
                    # Forcer quantité = 1 par ligne de variante
                    if int(item_data.quantity or 0) != 1:
                        raise HTTPException(status_code=400, detail="Pour un produit avec variantes, la quantité doit être 1 par ligne de variante")
                    resolved_variant.is_sold = True
                # Si aucune variante n'est spécifiée lors d'une mise à jour, on permet quand même
                # (c'est une modification de facture existante, les variantes ont été restaurées)
            else:
                # Produits sans variantes: vérifier stock disponible agrégé
                if (product.quantity or 0) < int(item_data.quantity or 0):
                    raise HTTPException(status_code=400, detail=f"Stock insuffisant pour le produit {product.name}")

            # Créer l'item
            # Ensure product_name respects DB length (String(100))
            safe_name = (item_data.product_name or product.name)[:100]
            db_item = InvoiceItem(
                invoice_id=invoice.invoice_id,
                product_id=item_data.product_id,
                product_name=safe_name,
                quantity=item_data.quantity,
                price=item_data.price,
                total=item_data.total,
            )
            db.add(db_item)

            # Appliquer le stock et enregistrer le mouvement OUT
            product.quantity = (product.quantity or 0) - int(item_data.quantity or 0)
            try:
                create_stock_movement(
                    db=db,
                    product_id=item_data.product_id,
                    quantity=int(item_data.quantity or 0),
                    movement_type="OUT",
                    reference_type="INVOICE_UPDATE",
                    reference_id=invoice.invoice_id,
                    notes=f"Mise à jour - Facture {invoice.invoice_number}",
                    unit_price=float(item_data.price or 0),
                )
            except Exception:
                pass

            # Synchroniser le stock avec Google Sheets (si activé)
            try:
                sync_product_stock_to_sheets(db, item_data.product_id)
            except Exception as e:
                logging.warning(f"Échec de synchronisation Google Sheets pour le produit {item_data.product_id}: {e}")
                pass

        # Mettre à jour les ventes quotidiennes associées à cette facture
        try:
            # Supprimer les ventes quotidiennes existantes pour cette facture
            existing_sales = db.query(DailySale).filter(DailySale.invoice_id == invoice.invoice_id).all()
            for s in existing_sales:
                db.delete(s)
            db.flush()

            # Recréer les ventes quotidiennes à partir des nouveaux items produits
            for item_data in (invoice_data.items or []):
                if not getattr(item_data, "product_id", None):
                    continue

                product = db.query(Product).filter(Product.product_id == item_data.product_id).first()
                if not product:
                    continue

                # Préparer les infos de variante si applicable
                variant_id_val = None
                variant_imei_val = None
                variant_barcode_val = None
                variant_condition_val = None
                try:
                    has_variants = (
                        db.query(ProductVariant.variant_id)
                        .filter(ProductVariant.product_id == product.product_id)
                        .first()
                        is not None
                    )
                    if has_variants:
                        resolved_variant = None
                        if getattr(item_data, "variant_id", None):
                            resolved_variant = (
                                db.query(ProductVariant)
                                .filter(ProductVariant.variant_id == item_data.variant_id)
                                .first()
                            )
                        elif getattr(item_data, "variant_imei", None):
                            imei_code = str(item_data.variant_imei).strip()
                            if imei_code:
                                resolved_variant = (
                                    db.query(ProductVariant)
                                    .filter(
                                        ProductVariant.product_id == product.product_id,
                                        func.trim(ProductVariant.imei_serial) == imei_code,
                                    )
                                    .first()
                                )
                        if resolved_variant is not None:
                            variant_id_val = resolved_variant.variant_id
                            variant_imei_val = resolved_variant.imei_serial
                            variant_barcode_val = resolved_variant.barcode
                            variant_condition_val = resolved_variant.condition
                except Exception:
                    pass

                daily_sale = DailySale(
                    client_id=invoice.client_id,
                    client_name=client.name,
                    product_id=item_data.product_id,
                    product_name=item_data.product_name or product.name,
                    variant_id=variant_id_val,
                    variant_imei=variant_imei_val,
                    variant_barcode=variant_barcode_val,
                    variant_condition=variant_condition_val,
                    quantity=item_data.quantity,
                    unit_price=item_data.price,
                    total_amount=item_data.total,
                    sale_date=invoice.date.date(),
                    payment_method=invoice.payment_method or "espece",
                    invoice_id=invoice.invoice_id,
                    notes=f"Mise à jour automatique depuis facture {invoice.invoice_number}",
                )
                db.add(daily_sale)
        except Exception as e:
            # Ne pas bloquer la mise à jour de facture si la mise à jour des ventes quotidiennes échoue
            logging.warning(f"Erreur lors de la mise à jour des ventes quotidiennes pour la facture {invoice.invoice_id}: {e}")

        db.commit()
        db.refresh(invoice)

        # Clear invoices cache after update to ensure fresh data on next load
        _invoices_cache.clear()

        try:
            recompute_invoices_stats(db)
        except Exception:
            pass

        # Façonner la réponse complète avec client_name pour respecter InvoiceResponse
        try:
            client_name = db.query(Client.name).filter(Client.client_id == invoice.client_id).scalar() or ""
        except Exception:
            client_name = ""
        try:
            _ = invoice.items
        except Exception:
            pass
        return {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "client_id": invoice.client_id,
            "client_name": client_name,
            "quotation_id": invoice.quotation_id,
            "date": invoice.date,
            "due_date": invoice.due_date,
            "status": invoice.status,
            "payment_method": invoice.payment_method,
            "subtotal": float(invoice.subtotal or 0),
            "tax_rate": float(invoice.tax_rate or 0),
            "tax_amount": float(invoice.tax_amount or 0),
            "total": float(invoice.total or 0),
            "paid_amount": float(invoice.paid_amount or 0),
            "remaining_amount": float(invoice.remaining_amount or 0),
            "notes": invoice.notes,
            "show_tax": bool(invoice.show_tax),
            "show_item_prices": bool(getattr(invoice, 'show_item_prices', True)),
            "show_section_totals": bool(getattr(invoice, 'show_section_totals', True)),
            "price_display": invoice.price_display or "FCFA",
            "created_at": getattr(invoice, "created_at", None),
            "items": [
                {
                    "item_id": it.item_id,
                    "product_id": it.product_id,
                    "product_name": it.product_name,
                    "quantity": it.quantity,
                    "price": float(it.price or 0),
                    "total": float(it.total or 0),
                }
                for it in (invoice.items or [])
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception(f"Erreur lors de la mise à jour de la facture")
        if str(os.getenv("DEBUG_ERRORS", "")).lower() == "true":
            raise HTTPException(status_code=500, detail=f"Erreur serveur: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/{invoice_id}/status")
async def update_invoice_status(
    invoice_id: int,
    status: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Mettre à jour le statut d'une facture"""
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")

        if getattr(current_user, "role", "user") != "admin":
            raise HTTPException(status_code=403, detail="Permissions insuffisantes")
        
        valid_statuses = ["en attente", "payée", "partiellement payée", "en retard", "annulée"]
        if status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Statut invalide")
        
        invoice.status = status
        db.commit()
        
        # Clear invoices cache after status update to ensure fresh data on next load
        _invoices_cache.clear()
        
        return {"message": "Statut mis à jour avec succès"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la mise à jour du statut: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime

class PaymentCreate(BaseModel):
    amount: float
    payment_method: str
    payment_date: Optional[datetime] = None
    reference: Optional[str] = None
    notes: Optional[str] = None

def _recompute_invoice_payment_status(invoice: Invoice, db: Session) -> None:
    try:
        total_dec = Decimal(str(invoice.total or 0)).quantize(Decimal('1'))
    except Exception:
        total_dec = Decimal('0')

    try:
        payments = db.query(InvoicePayment).filter(InvoicePayment.invoice_id == invoice.invoice_id).all()
    except Exception:
        payments = []

    paid_dec = Decimal('0')
    for p in (payments or []):
        try:
            paid_dec += Decimal(str(p.amount or 0)).quantize(Decimal('1'))
        except Exception:
            continue

    remaining_dec = total_dec - paid_dec
    if remaining_dec < Decimal('0'):
        remaining_dec = Decimal('0')

    invoice.paid_amount = paid_dec
    invoice.remaining_amount = remaining_dec

    if total_dec > Decimal('0') and remaining_dec == Decimal('0'):
        invoice.status = "payée"
    elif paid_dec > Decimal('0'):
        invoice.status = "partiellement payée"
    else:
        invoice.status = "en attente"

# REMOVED duplicate get_next_invoice_number defined earlier to prevent conflicts

@router.post("/{invoice_id}/payments")
async def add_payment(
    invoice_id: int,
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Ajouter un paiement à une facture (JSON body)"""
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        if payload.amount <= 0:
            raise HTTPException(status_code=400, detail="Le montant doit être positif")
        
        # Convertir en Decimal et forcer un montant entier
        amount_dec = Decimal(str(payload.amount)).quantize(Decimal('1'))
        remaining = Decimal(str(invoice.remaining_amount or 0)).quantize(Decimal('1'))
        if amount_dec > remaining:
            raise HTTPException(status_code=400, detail="Le montant dépasse le solde restant")
        
        # Créer le paiement
        payment = InvoicePayment(
            invoice_id=invoice_id,
            amount=amount_dec,
            payment_method=payload.payment_method,
            payment_date=(payload.payment_date or datetime.now()),
            reference=payload.reference,
            notes=payload.notes
        )
        db.add(payment)
        
        # Mettre à jour les montants de la facture
        invoice.paid_amount = Decimal(str(invoice.paid_amount or 0)) + amount_dec
        invoice.remaining_amount = remaining - amount_dec

        # Mettre à jour le statut de façon cohérente avec tous les paiements
        # IMPORTANT: flush avant le recalcul pour que la requête voie le nouveau paiement
        db.flush()
        _recompute_invoice_payment_status(invoice, db)
        
        db.commit()
        db.refresh(payment)
        
        # Clear invoices cache after payment to ensure fresh data on next load
        _invoices_cache.clear()
        
        return {"message": "Paiement ajouté avec succès", "payment_id": payment.payment_id}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de l'ajout du paiement: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.delete("/{invoice_id}/payments/{payment_id}")
async def delete_payment(
    invoice_id: int,
    payment_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer un paiement d'une facture et recalculer le statut/montants.

    Permet de corriger une facture marquée "payée" par erreur en retirant
    un ou plusieurs paiements, puis en remettant la facture au bon état
    (en attente / partiellement payée) selon les paiements restants.
    """
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")

        payment = (
            db.query(InvoicePayment)
            .filter(InvoicePayment.payment_id == payment_id, InvoicePayment.invoice_id == invoice_id)
            .first()
        )
        if not payment:
            raise HTTPException(status_code=404, detail="Paiement non trouvé")

        db.delete(payment)
        db.flush()

        _recompute_invoice_payment_status(invoice, db)

        db.commit()
        db.refresh(invoice)

        _invoices_cache.clear()

        return {
            "message": "Paiement supprimé avec succès",
            "invoice_id": invoice.invoice_id,
            "status": invoice.status,
            "paid_amount": float(invoice.paid_amount or 0),
            "remaining_amount": float(invoice.remaining_amount or 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la suppression du paiement: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.post("/{invoice_id}/payments/reset")
async def reset_payments(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer tous les paiements d'une facture et remettre le statut/montants à zéro.

    Utile lorsqu'une facture a été marquée payée par erreur :
    on vide les paiements puis on repasse automatiquement la facture à
    "en attente" (ou partiellement payée si d'autres paiements sont recréés ensuite).
    """
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")

        payments = db.query(InvoicePayment).filter(InvoicePayment.invoice_id == invoice_id).all()
        for p in (payments or []):
            db.delete(p)

        db.flush()

        _recompute_invoice_payment_status(invoice, db)

        db.commit()
        db.refresh(invoice)

        _invoices_cache.clear()

        return {
            "message": "Paiements réinitialisés avec succès",
            "invoice_id": invoice.invoice_id,
            "status": invoice.status,
            "paid_amount": float(invoice.paid_amount or 0),
            "remaining_amount": float(invoice.remaining_amount or 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la réinitialisation des paiements: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.delete("/{invoice_id}")
async def delete_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer une facture (admin seulement)"""
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        if current_user.role not in ["admin"]:
            raise HTTPException(status_code=403, detail="Permissions insuffisantes")
        
        # Restaurer le stock des produits
        for item in invoice.items:
            product = db.query(Product).filter(Product.product_id == item.product_id).first()
            if product:
                product.quantity += item.quantity
                create_stock_movement(
                    db=db,
                    product_id=item.product_id,
                    quantity=item.quantity,
                    movement_type="IN",
                    reference_type="INVOICE_CANCELLATION",
                    reference_id=invoice_id,
                    notes=f"Annulation facture {invoice.invoice_number}",
                    unit_price=float(item.price)
                )

                # Synchroniser le stock avec Google Sheets (si activé)
                try:
                    sync_product_stock_to_sheets(db, item.product_id)
                except Exception as e:
                    logging.warning(f"Échec de synchronisation Google Sheets pour le produit {item.product_id}: {e}")
        
        # Réactiver les variantes vendues
        try:
            serials_meta = []
            if invoice.notes:
                import re, json
                txt = str(invoice.notes)
                if "__SERIALS__=" in txt:
                    sub = txt.split("__SERIALS__=", 1)[1]
                    # Couper avant une autre balise meta commençant par __ ou fin de texte
                    cut_idx = sub.find("\n__")
                    if cut_idx != -1:
                        sub = sub[:cut_idx].strip()
                    # Nettoyer d'éventuels sauts de lignes/trailing
                    sub = sub.strip()
                    try:
                        serials_meta = json.loads(sub)
                    except Exception:
                        # Ultime tentative: regex non-gourmande entre crochets
                        m = re.search(r"__SERIALS__=(\[.*?\])", txt, flags=re.S)
                        if m:
                            serials_meta = json.loads(m.group(1))
            # 1) Depuis meta notes (le plus fiable)
            processed_products = set()
            if serials_meta:
                for entry in (serials_meta or []):
                    pid = entry.get('product_id')
                    if pid is not None:
                        processed_products.add(int(pid))
                    for imei in (entry.get('imeis') or []):
                        variant = db.query(ProductVariant).filter(func.trim(ProductVariant.imei_serial) == str(imei).strip()).first()
                        if variant and bool(variant.is_sold):
                            variant.is_sold = False
            else:
                # 2) Fallback: extraire IMEI depuis le libellé de chaque ligne: "(IMEI: XXXXX)"
                import re
                for it in (invoice.items or []):
                    name = it.product_name or ""
                    m2 = re.search(r"\(IMEI:\s*([^)]+)\)", name, flags=re.I)
                    if not m2:
                        continue
                    imei = (m2.group(1) or '').strip()
                    if not imei:
                        continue
                    if it.product_id is not None:
                        processed_products.add(int(it.product_id))
                    variant = db.query(ProductVariant).filter(func.trim(ProductVariant.imei_serial) == imei).first()
                    if variant and bool(variant.is_sold):
                        variant.is_sold = False

            # 3) Ultime fallback: pour les produits concernés mais sans IMEI détecté,
            # désactiver l'état "vendu" pour autant de variantes que la quantité des lignes
            # (utile pour anciennes factures sans meta ni IMEI dans le libellé)
            for it in (invoice.items or []):
                pid = int(it.product_id) if it.product_id is not None else None
                if pid is None:
                    continue
                # Si déjà traité via IMEI, sauter
                if pid in processed_products:
                    continue
                try:
                    qty = int(it.quantity or 0)
                except Exception:
                    qty = 0
                if qty <= 0:
                    continue
                sold_variants = (
                    db.query(ProductVariant)
                    .filter(ProductVariant.product_id == pid, ProductVariant.is_sold == True)
                    .limit(qty)
                    .all()
                )
                for v in sold_variants:
                    v.is_sold = False
                # Mettre à jour la quantité disponible du produit si incohérente
                try:
                    product = db.query(Product).filter(Product.product_id == pid).first()
                    if product:
                        product.quantity = (product.quantity or 0) + len(sold_variants)
                except Exception:
                    pass
        except Exception:
            # ne pas bloquer la suppression de la facture si parsing échoue
            pass
        
        # Supprimer également tous les bons de livraison associés à cette facture
        try:
            related_dns = db.query(DeliveryNote).filter(DeliveryNote.invoice_id == invoice_id).all()
            for dn in (related_dns or []):
                try:
                    # Les items seront supprimés grâce au cascade="all, delete-orphan"
                    db.delete(dn)
                except Exception:
                    pass
        except Exception:
            # Ne pas bloquer la suppression de la facture si la recherche/itération échoue
            pass

        db.delete(invoice)
        db.commit()
        
        # Clear invoices cache after deletion to ensure fresh data on next load
        _invoices_cache.clear()
        
        try:
            recompute_invoices_stats(db)
        except Exception:
            pass
        
        return {"message": "Facture supprimée avec succès"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la suppression de la facture: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/stats/dashboard")
async def get_invoice_stats(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir les statistiques des factures pour le tableau de bord"""
    try:
        today = date.today()
        
        # Total des factures
        total_invoices = db.query(Invoice).count()
        
        # Comptages par statut (support FR/EN)
        pending_invoices = db.query(Invoice).filter(Invoice.status.in_(["en attente", "SENT", "DRAFT", "OVERDUE", "partiellement payée"]) ).count()
        paid_invoices = db.query(Invoice).filter(Invoice.status.in_(["payée", "PAID"]) ).count()

        # Si l'utilisateur n'est pas admin, ne pas exposer les chiffres d'affaires
        try:
            role = getattr(current_user, "role", "user")
        except Exception:
            role = "user"
        if role != "admin":
            return {
                "total_invoices": total_invoices,
                "pending_invoices": pending_invoices,
                "paid_invoices": paid_invoices,
            }
        
        # Chiffre d'affaires brut du mois
        monthly_revenue_gross = db.query(func.sum(Invoice.total)).filter(
            func.extract('month', Invoice.date) == today.month,
            func.extract('year', Invoice.date) == today.year,
            Invoice.status.in_(["payée", "PAID"])
        ).scalar() or 0

        # Achats quotidiens du mois (par date ou created_at)
        monthly_daily_purchases = db.query(func.coalesce(func.sum(DailyPurchase.amount), 0)).filter(
            or_(
                and_(func.extract('month', DailyPurchase.date) == today.month, func.extract('year', DailyPurchase.date) == today.year),
                and_(func.extract('month', DailyPurchase.created_at) == today.month, func.extract('year', DailyPurchase.created_at) == today.year),
            )
        ).scalar() or 0
        
        # Paiements aux fournisseurs du mois
        monthly_supplier_payments = db.query(func.sum(SupplierInvoice.paid_amount)).filter(
            func.extract('month', SupplierInvoice.invoice_date) == today.month,
            func.extract('year', SupplierInvoice.invoice_date) == today.year
        ).scalar() or 0
        
        # Chiffre d'affaires net du mois (déduction achats quotidiens)
        monthly_revenue = float(monthly_revenue_gross or 0) - float(monthly_supplier_payments or 0) - float(monthly_daily_purchases or 0)
        
        # Chiffre d'affaires total brut (toutes factures payées)
        total_revenue_gross = db.query(func.sum(Invoice.total)).filter(Invoice.status.in_(["payée", "PAID"])).scalar() or 0
        
        # Total des paiements aux fournisseurs
        total_supplier_payments = db.query(func.sum(SupplierInvoice.paid_amount)).scalar() or 0
        
        # Total des achats quotidiens (toute période)
        total_daily_purchases = db.query(func.coalesce(func.sum(DailyPurchase.amount), 0)).scalar() or 0
        
        # Chiffre d'affaires total net (déduction achats quotidiens)
        total_revenue = float(total_revenue_gross or 0) - float(total_supplier_payments or 0) - float(total_daily_purchases or 0)
        
        # Montant impayé (restant)
        unpaid_amount = db.query(func.sum(Invoice.remaining_amount)).filter(Invoice.status.in_(["en attente", "partiellement payée", "OVERDUE"])) .scalar() or 0
        
        # Toujours recalculer à la demande pour refléter immédiatement les derniers changements (admin uniquement)
        try:
            from ..services.stats_manager import recompute_invoices_stats
            return recompute_invoices_stats(db)
        except Exception:
            return {
                "total_invoices": total_invoices,
                "pending_invoices": pending_invoices,
                "paid_invoices": paid_invoices,
                "monthly_revenue": float(monthly_revenue),
                "monthly_revenue_gross": float(monthly_revenue_gross),
                "monthly_supplier_payments": float(monthly_supplier_payments),
                "monthly_daily_purchases": float(monthly_daily_purchases),
                "total_revenue": float(total_revenue),
                "total_revenue_gross": float(total_revenue_gross),
                "total_supplier_payments": float(total_supplier_payments),
                "total_daily_purchases": float(total_daily_purchases),
                "unpaid_amount": float(unpaid_amount)
            }
        
    except Exception as e:
        logging.error(f"Erreur lors du calcul des stats factures: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.post("/{invoice_id}/delivery-note")
async def create_delivery_note_from_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Générer un bon de livraison à partir d'une facture existante.

    - Copie les lignes produits (ignore les lignes personnalisées sans produit)
    - Calque les montants (HT/TVA/Total) de la facture
    - Tente d'attacher les numéros de série/IMEI depuis les notes de la facture (__SERIALS__=...)
    """
    try:
        # Charger la facture et ses éléments
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")

        _ = invoice.items  # force load
        _ = invoice.client  # force load

        # Générer un numéro de BL: BL-YYYYMMDD-XXXX
        from datetime import datetime as _dt
        today_prefix = _dt.now().strftime("BL-%Y%m%d-")
        last_note = (
            db.query(DeliveryNote)
            .filter(DeliveryNote.delivery_note_number.ilike(f"{today_prefix}%"))
            .order_by(DeliveryNote.delivery_note_id.desc())
            .first()
        )
        if last_note and last_note.delivery_note_number.startswith(today_prefix):
            try:
                last_seq = int(last_note.delivery_note_number.split("-")[-1])
            except Exception:
                last_seq = 0
            next_seq = last_seq + 1
        else:
            next_seq = 1
        delivery_number = f"{today_prefix}{next_seq:04d}"

        # Parser les IMEIs/séries depuis les notes de facture si présents
        serials_meta = []
        try:
            txt = str(invoice.notes or "")
            if "__SERIALS__=" in txt:
                import re, json
                sub = txt.split("__SERIALS__=", 1)[1]
                cut_idx = sub.find("\n__")
                if cut_idx != -1:
                    sub = sub[:cut_idx].strip()
                sub = sub.strip()
                try:
                    serials_meta = json.loads(sub)
                except Exception:
                    m = re.search(r"__SERIALS__=(\[.*?\])", txt, flags=re.S)
                    if m:
                        serials_meta = json.loads(m.group(1))
        except Exception:
            serials_meta = []

        # Index des séries par produit
        product_id_to_imeis = {}
        try:
            for entry in (serials_meta or []):
                pid = entry.get("product_id")
                if pid is None:
                    continue
                product_id_to_imeis[int(pid)] = list(entry.get("imeis") or [])
        except Exception:
            product_id_to_imeis = {}

        # Créer le BL
        dn = DeliveryNote(
            delivery_note_number=delivery_number,
            invoice_id=invoice.invoice_id,
            client_id=invoice.client_id,
            date=invoice.date or _dt.now(),
            delivery_date=_dt.now(),
            status="en_preparation",
            delivery_address=getattr(invoice.client, "address", None) if invoice.client else None,
            delivery_contact=getattr(invoice.client, "name", None) if invoice.client else None,
            delivery_phone=getattr(invoice.client, "phone", None) if invoice.client else None,
            subtotal=invoice.subtotal,
            tax_rate=invoice.tax_rate,
            tax_amount=invoice.tax_amount,
            total=invoice.total,
            notes=f"Créé depuis facture {invoice.invoice_number}"
        )
        db.add(dn)
        db.flush()  # obtenir l'ID

        # Lignes du BL à partir des lignes facture (produits uniquement)
        for it in (invoice.items or []):
            if it.product_id is None:
                # ignorer lignes personnalisées
                continue
            imeis = product_id_to_imeis.get(int(it.product_id), [])
            dn_item = DeliveryNoteItem(
                delivery_note_id=dn.delivery_note_id,
                product_id=it.product_id,
                product_name=it.product_name,
                quantity=it.quantity,
                price=it.price,
                delivered_quantity=0,
                serial_numbers=(None if not imeis else __import__("json").dumps(imeis))
            )
            db.add(dn_item)

        db.commit()
        db.refresh(dn)

        return {
            "message": "Bon de livraison créé",
            "delivery_note_id": dn.delivery_note_id,
            "delivery_note_number": dn.delivery_note_number,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la génération du BL depuis facture: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# Créer l'instance de templates
templates = Jinja2Templates(directory="templates")

@router.get("/{invoice_id}/warranty-certificate", response_class=HTMLResponse)
async def get_warranty_certificate(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Générer et afficher le certificat de garantie pour une facture"""
    try:
        # Charger la facture avec le client
        from sqlalchemy.orm import joinedload
        invoice = db.query(Invoice).options(
            joinedload(Invoice.client)
        ).filter(Invoice.invoice_id == invoice_id).first()
        
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        # Vérifier si la facture a une garantie
        if not getattr(invoice, 'has_warranty', False):
            raise HTTPException(status_code=400, detail="Cette facture n'a pas de garantie associée")
        
        # Forcer le chargement du client
        _ = invoice.client
        
        # Préparer les données pour le template
        warranty_duration = getattr(invoice, 'warranty_duration', 12)
        
        # Utiliser une fausse requête pour le template
        class FakeRequest:
            def __init__(self):
                pass
            
            def get(self, key, default=None):
                return default
        
        return templates.TemplateResponse("warranty_certificate.html", {
            "request": FakeRequest(),
            "invoice": invoice,
            "warranty_duration": warranty_duration
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la génération du certificat de garantie: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# Configuration n8n
# N8N_BASE_URL: URL de base de n8n (sans path) pour les webhooks de factures/devis
N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://n8n:5678")

from pydantic import BaseModel

class SendWhatsAppRequest(BaseModel):
    invoice_id: int
    phone: str

class SendEmailRequest(BaseModel):
    invoice_id: int
    email: str

@router.post("/send-whatsapp")
async def send_invoice_whatsapp(
    request: Request,
    data: SendWhatsAppRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Envoyer une facture par WhatsApp via n8n"""
    try:
        # Vérifier que la facture existe
        invoice = db.query(Invoice).filter(Invoice.invoice_id == data.invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        # Construire l'URL du PDF de la facture (accessible depuis n8n via réseau Docker)
        app_public_url = os.getenv("APP_PUBLIC_URL", "http://nitek_app:8000")
        pdf_url = f"{app_public_url}/invoices/print/{data.invoice_id}"
        
        # Appeler le webhook n8n pour envoyer via WhatsApp
        webhook_url = f"{N8N_BASE_URL}/webhook/send-invoice-whatsapp"
        
        payload = {
            "invoice_id": data.invoice_id,
            "invoice_number": invoice.invoice_number,
            "phone": data.phone,
            "pdf_url": pdf_url,
            "client_name": invoice.client.name if invoice.client else "Client",
            "total": float(invoice.total or 0)
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(webhook_url, json=payload)
            
        if response.status_code == 200:
            return {"success": True, "message": "Facture envoyée par WhatsApp"}
        else:
            logging.error(f"Erreur n8n WhatsApp: {response.status_code} - {response.text}")
            return {"success": False, "message": f"Erreur n8n: {response.text}"}
            
    except httpx.RequestError as e:
        logging.error(f"Erreur connexion n8n: {e}")
        raise HTTPException(status_code=503, detail="Service n8n indisponible")
    except Exception as e:
        logging.error(f"Erreur envoi WhatsApp: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/send-email")
async def send_invoice_email(
    request: Request,
    data: SendEmailRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Envoyer une facture par Email via n8n"""
    try:
        # Vérifier que la facture existe
        invoice = db.query(Invoice).filter(Invoice.invoice_id == data.invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        # Construire l'URL HTML de la facture (même URL que WhatsApp)
        app_public_url = os.getenv("APP_PUBLIC_URL", "http://nitek_app:8000")
        pdf_url = f"{app_public_url}/invoices/print/{data.invoice_id}"
        
        # Appeler le webhook n8n pour envoyer par email
        webhook_url = f"{N8N_BASE_URL}/webhook/send-invoice-email"
        
        payload = {
            "invoice_id": data.invoice_id,
            "invoice_number": invoice.invoice_number,
            "email": data.email,
            "pdf_url": pdf_url,
            "client_name": invoice.client.name if invoice.client else "Client",
            "total": float(invoice.total or 0)
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(webhook_url, json=payload)
            
        if response.status_code == 200:
            return {"success": True, "message": "Facture envoyée par email"}
        else:
            logging.error(f"Erreur n8n Email: {response.status_code} - {response.text}")
            return {"success": False, "message": f"Erreur n8n: {response.text}"}
            
    except httpx.RequestError as e:
        logging.error(f"Erreur connexion n8n: {e}")
        raise HTTPException(status_code=503, detail="Service n8n indisponible")
    except Exception as e:
        logging.error(f"Erreur envoi email: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{invoice_id}/duplicate", response_model=InvoiceResponse)
async def duplicate_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Dupliquer une facture existante avec tous ses articles (sans les paiements)"""
    try:
        original = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not original:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        # Générer un nouveau numéro de facture
        new_number = _next_invoice_number(db)
        
        # Créer une copie de la facture
        new_date = datetime.now()
        new_invoice = Invoice(
            invoice_number=new_number,
            client_id=original.client_id,
            date=new_date,
            due_date=new_date + timedelta(days=30),
            status="en attente",
            payment_method=original.payment_method,
            subtotal=original.subtotal,
            tax_rate=original.tax_rate,
            tax_amount=original.tax_amount,
            total=original.total,
            paid_amount=0,
            remaining_amount=original.total,
            notes=original.notes,
            show_tax=original.show_tax,
            show_item_prices=original.show_item_prices,
            show_section_totals=original.show_section_totals,
            price_display=original.price_display,
            has_warranty=original.has_warranty,
            warranty_duration=original.warranty_duration,
        )
        
        db.add(new_invoice)
        db.flush()  # Pour obtenir l'ID de la nouvelle facture
        
        # Copier les articles (sans décrémenter le stock)
        original_items = db.query(InvoiceItem).filter(InvoiceItem.invoice_id == invoice_id).all()
        for item in original_items:
            new_item = InvoiceItem(
                invoice_id=new_invoice.invoice_id,
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                price=item.price,
                total=item.total,
            )
            db.add(new_item)
        
        db.commit()
        db.refresh(new_invoice)
        
        # Récupérer le nom du client pour la réponse
        client = db.query(Client).filter(Client.client_id == new_invoice.client_id).first()
        client_name = client.name if client else ""
        
        # Construire la réponse avec client_name
        return {
            "invoice_id": new_invoice.invoice_id,
            "invoice_number": new_invoice.invoice_number,
            "client_id": new_invoice.client_id,
            "client_name": client_name,
            "quotation_id": new_invoice.quotation_id,
            "date": new_invoice.date,
            "due_date": new_invoice.due_date,
            "status": new_invoice.status,
            "payment_method": new_invoice.payment_method,
            "subtotal": new_invoice.subtotal,
            "tax_rate": new_invoice.tax_rate,
            "tax_amount": new_invoice.tax_amount,
            "total": new_invoice.total,
            "paid_amount": new_invoice.paid_amount,
            "remaining_amount": new_invoice.remaining_amount,
            "notes": new_invoice.notes,
            "show_tax": new_invoice.show_tax,
            "show_item_prices": new_invoice.show_item_prices,
            "show_section_totals": new_invoice.show_section_totals,
            "price_display": new_invoice.price_display,
            "has_warranty": new_invoice.has_warranty,
            "warranty_duration": new_invoice.warranty_duration,
            "warranty_start_date": new_invoice.warranty_start_date,
            "warranty_end_date": new_invoice.warranty_end_date,
            "created_at": new_invoice.created_at,
            "items": [],
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la duplication de la facture: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la duplication")
