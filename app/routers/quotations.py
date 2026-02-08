from fastapi import APIRouter, HTTPException, status, Query, Request, Depends
from typing import List, Optional
from datetime import datetime, date as DateType
from pydantic import BaseModel
import httpx
import os
from ..database import (
    Quotation, QuotationItem, Client, Product, Invoice, InvoiceItem, InvoicePayment,
    get_next_id,
)
from ..schemas import QuotationCreate, QuotationResponse
from ..auth import get_current_user
import logging
import time

router = APIRouter(prefix="/api/quotations", tags=["quotations"])


# ── Helpers numérotation devis ───────────────────────────────────
import re as _re_mod
from datetime import datetime as _dt


async def _next_quotation_number(prefix: Optional[str] = None) -> str:
    """Retourne le prochain numéro de devis séquentiel sous la forme PREFIX-#### (par défaut DEV-####)."""
    pf = (prefix or 'DEV').strip('-')
    base_prefix = f"{pf}-"

    try:
        rows = await Quotation.find(
            {"quotation_number": {"$regex": f"^{_re_mod.escape(base_prefix)}", "$options": "i"}}
        ).to_list()
    except Exception:
        rows = []

    last_seq = 0
    # 1) format exact PREFIX-####
    for q in (rows or []):
        num = q.quotation_number
        if not isinstance(num, str):
            continue
        m = _re_mod.fullmatch(rf"{_re_mod.escape(pf)}-(\d+)", num.strip())
        if m:
            val = int(m.group(1))
            if val > last_seq:
                last_seq = val

    # 2) fallback sur le plus grand suffixe numérique si mix d'anciens formats
    if last_seq == 0:
        for q in (rows or []):
            num = q.quotation_number
            if not isinstance(num, str):
                continue
            matches = _re_mod.findall(r'(\d+)', num.strip())
            if matches:
                val = int(matches[-1])
                if val > last_seq:
                    last_seq = val

    next_seq = last_seq + 1
    while True:
        candidate = f"{base_prefix}{next_seq:04d}"
        exists = await Quotation.find_one(Quotation.quotation_number == candidate)
        if not exists:
            return candidate
        next_seq += 1


# ── Async stats recomputation (replaces SQLAlchemy stats_manager) ──
async def _recompute_quotations_stats():
    """Recompute quotation stats and store in AppCache."""
    try:
        from ..database import AppCache
        import json

        total = await Quotation.count()
        total_accepted = await Quotation.find(Quotation.status == "accepté").count()
        total_pending = await Quotation.find(Quotation.status == "en attente").count()

        pipeline = [{"$group": {"_id": None, "total_value": {"$sum": "$total"}}}]
        agg = await Quotation.aggregate(pipeline).to_list()
        total_value = float(str(agg[0]["total_value"])) if agg else 0

        result = {
            "total": int(total),
            "total_accepted": int(total_accepted),
            "total_pending": int(total_pending),
            "total_value": total_value,
        }

        payload = json.dumps(result, default=str)
        existing = await AppCache.find_one(AppCache.cache_key == "quotations_stats")
        if existing:
            existing.cache_value = payload
            await existing.save()
        else:
            await AppCache(cache_key="quotations_stats", cache_value=payload, expires_at=None).insert()

        return result
    except Exception:
        pass


# ── Async next invoice number (local helper for convert-to-invoice) ──
async def _next_invoice_number_async(prefix: Optional[str] = None) -> str:
    """Génère le prochain numéro de facture séquentiel (async/Beanie)."""
    pf = (prefix or 'FAC').strip('-')
    base_prefix = f"{pf}-"

    try:
        rows = await Invoice.find(
            {"invoice_number": {"$regex": f"^{_re_mod.escape(base_prefix)}", "$options": "i"}}
        ).to_list()
    except Exception:
        rows = []

    last_seq = 0
    for inv in (rows or []):
        num = inv.invoice_number
        if not isinstance(num, str):
            continue
        m = _re_mod.fullmatch(rf"{_re_mod.escape(pf)}-(\d+)", num.strip())
        if m:
            val = int(m.group(1))
            if val > last_seq:
                last_seq = val

    if last_seq == 0:
        for inv in (rows or []):
            num = inv.invoice_number
            if not isinstance(num, str):
                continue
            matches = _re_mod.findall(r'(\d+)', num.strip())
            if matches:
                val = int(matches[-1])
                if val > last_seq:
                    last_seq = val

    next_seq = last_seq + 1
    while True:
        candidate = f"{base_prefix}{next_seq:04d}"
        exists = await Invoice.find_one(Invoice.invoice_number == candidate)
        if not exists:
            return candidate
        next_seq += 1


# ── Endpoints ────────────────────────────────────────────────────

@router.get("/", response_model=List[QuotationResponse])
async def list_quotations(
    skip: int = 0,
    limit: int = 100,
    status_filter: Optional[str] = None,
    client_id: Optional[int] = None,
    start_date: Optional[DateType] = None,
    end_date: Optional[DateType] = None,
    current_user=Depends(get_current_user),
):
    """Lister les devis avec filtres"""
    filters = {}

    if status_filter:
        filters["status"] = status_filter
    if client_id:
        filters["client_id"] = client_id
    if start_date or end_date:
        date_filter = {}
        if start_date:
            date_filter["$gte"] = datetime.combine(start_date, datetime.min.time())
        if end_date:
            date_filter["$lte"] = datetime.combine(end_date, datetime.max.time())
        if date_filter:
            filters["date"] = date_filter

    quotations = (
        await Quotation.find(filters)
        .sort(-Quotation.created_at)
        .skip(skip)
        .limit(limit)
        .to_list()
    )

    # Attacher l'ID de la facture liée (s'il existe) pour chaque devis
    try:
        qids = [int(q.quotation_id) for q in quotations]
        if qids:
            invoices = await Invoice.find({"quotation_id": {"$in": qids}}).to_list()
            qid_to_invoice = {}
            for inv in invoices:
                if inv.quotation_id is not None and inv.invoice_id is not None:
                    qid_to_invoice[int(inv.quotation_id)] = int(inv.invoice_id)
            for q in quotations:
                try:
                    q.invoice_id = qid_to_invoice.get(int(q.quotation_id))
                except Exception:
                    pass
    except Exception:
        pass

    return quotations


class QuotationListItem(BaseModel):
    quotation_id: int
    quotation_number: str
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    # Use strings to avoid Pydantic misinterpretation causing 'none_required'
    date: Optional[str] = None
    expiry_date: Optional[str] = None
    total: Optional[float] = 0
    status: Optional[str] = None
    is_sent: Optional[bool] = False
    invoice_id: Optional[int] = None


class PaginatedQuotationsResponse(BaseModel):
    items: List[QuotationListItem]
    total: int
    total_accepted: int
    total_pending: int
    total_value: float


# Simple in-process cache for quotations list
_quotations_cache = {}
_QUOTES_CACHE_TTL = 30  # seconds


@router.get("/paginated", response_model=PaginatedQuotationsResponse)
async def list_quotations_paginated(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    status_filter: Optional[str] = None,
    client_search: Optional[str] = None,
    start_date: Optional[DateType] = None,
    end_date: Optional[DateType] = None,
    sort_by: Optional[str] = Query("date"),   # date | number | total | status | sent
    sort_dir: Optional[str] = Query("desc"),  # asc | desc
    current_user=Depends(get_current_user),
):
    """Lister les devis avec pagination et filtres légers pour la liste."""
    # Cache key
    try:
        import hashlib
        key_raw = f"p={page}|s={page_size}|sf={status_filter}|cs={client_search}|sd={start_date}|ed={end_date}|ob={sort_by}|od={sort_dir}"
        cache_key = hashlib.md5(key_raw.encode()).hexdigest()
        entry = _quotations_cache.get(cache_key)
        if entry and (time.time() - entry['ts']) < _QUOTES_CACHE_TTL:
            return entry['data']
    except Exception:
        cache_key = None

    # Build base filter
    filters = {}

    if status_filter:
        filters["status"] = status_filter

    # Client search: find matching client_ids first
    matching_client_ids = None
    if client_search:
        like = client_search.strip()
        clients = await Client.find(
            {"name": {"$regex": like, "$options": "i"}}
        ).to_list()
        matching_client_ids = [c.client_id for c in clients]
        filters["client_id"] = {"$in": matching_client_ids}

    if start_date:
        filters.setdefault("date", {})["$gte"] = datetime.combine(start_date, datetime.min.time())
    if end_date:
        filters.setdefault("date", {})["$lte"] = datetime.combine(end_date, datetime.max.time())

    start_ts = time.time()

    # Aggregate counts with same filters
    total = await Quotation.find(filters).count()

    accepted_filters = {**filters, "status": "accepté"}
    # If status_filter is already set and != 'accepté', count will be 0
    total_accepted = await Quotation.find(accepted_filters).count()

    pending_filters = {**filters, "status": "en attente"}
    total_pending = await Quotation.find(pending_filters).count()

    # Total value
    match_stage = {"$match": filters} if filters else {"$match": {}}
    pipeline = [match_stage, {"$group": {"_id": None, "total_value": {"$sum": "$total"}}}]
    agg = await Quotation.aggregate(pipeline).to_list()
    total_value = float(str(agg[0]["total_value"])) if agg else 0

    # Restreindre l'exposition de la valeur agrégée aux administrateurs uniquement
    try:
        role = getattr(current_user, "role", "user")
    except Exception:
        role = "user"
    if role != "admin":
        total_value = 0

    # Sorting
    sort_key_name = (sort_by or 'date').lower()
    desc_dir = (sort_dir or 'desc').lower() == 'desc'

    sort_field_map = {
        'number': 'quotation_number',
        'total': 'total',
        'status': 'status',
        'sent': 'is_sent',
        'date': 'date',
    }
    field_name = sort_field_map.get(sort_key_name, 'date')

    # Build sort list: primary field + quotation_id desc as tiebreaker
    if desc_dir:
        sort_spec = [("-" + field_name), ("-quotation_id",)]
    else:
        sort_spec = [(field_name,), ("-quotation_id",)]

    # Use raw sort tuples for Motor
    from pymongo import ASCENDING, DESCENDING
    sort_list = []
    if desc_dir:
        sort_list.append((field_name, DESCENDING))
    else:
        sort_list.append((field_name, ASCENDING))
    sort_list.append(("quotation_id", DESCENDING))

    # Pagination
    skip = (page - 1) * page_size
    quotations = await Quotation.find(filters).sort(sort_list).skip(skip).limit(page_size).to_list()

    # Fetch client names in bulk
    client_ids = list(set(q.client_id for q in quotations if q.client_id is not None))
    clients_map = {}
    if client_ids:
        clients = await Client.find({"client_id": {"$in": client_ids}}).to_list()
        clients_map = {c.client_id: c.name for c in clients}

    # Fetch linked invoice IDs in bulk
    q_ids = [q.quotation_id for q in quotations]
    invoice_map = {}
    if q_ids:
        invoices = await Invoice.find({"quotation_id": {"$in": q_ids}}).to_list()
        for inv in invoices:
            if inv.quotation_id is not None and inv.invoice_id is not None:
                invoice_map[int(inv.quotation_id)] = int(inv.invoice_id)

    items = []
    for q in quotations:
        d = q.date
        ed = q.expiry_date
        if isinstance(d, _dt):
            d = d.date()
        if isinstance(ed, _dt):
            ed = ed.date()
        items.append({
            'quotation_id': int(q.quotation_id),
            'quotation_number': q.quotation_number,
            'client_id': int(q.client_id) if q.client_id is not None else None,
            'date': (d.isoformat() if hasattr(d, 'isoformat') else (str(d) if d is not None else None)),
            'expiry_date': (ed.isoformat() if hasattr(ed, 'isoformat') else (str(ed) if ed is not None else None)),
            'total': float(q.total or 0),
            'status': q.status,
            'is_sent': bool(q.is_sent) if q.is_sent is not None else False,
            'client_name': clients_map.get(q.client_id),
            'invoice_id': invoice_map.get(int(q.quotation_id)),
        })

    logging.info(f"/quotations/paginated total={total} took {time.time()-start_ts:.3f}s")
    result = {
        'items': items,
        'total': int(total),
        'total_accepted': int(total_accepted),
        'total_pending': int(total_pending),
        'total_value': float(total_value or 0),
    }

    try:
        if cache_key:
            _quotations_cache[cache_key] = {'ts': time.time(), 'data': result}
    except Exception:
        pass

    return result


@router.get("/{quotation_id}")
async def get_quotation(
    quotation_id: int,
    current_user=Depends(get_current_user),
):
    """Obtenir un devis par ID"""
    quotation = await Quotation.find_one(Quotation.quotation_id == quotation_id)
    if not quotation:
        raise HTTPException(status_code=404, detail="Devis non trouvé")

    # Récupérer le nom du client
    client = await Client.find_one(Client.client_id == quotation.client_id)
    client_name = client.name if client else None

    # Récupérer les items
    items = await QuotationItem.find(QuotationItem.quotation_id == quotation.quotation_id).to_list()

    # Attacher l'ID de facture liée si présent
    invoice_id = None
    try:
        inv = await Invoice.find_one(Invoice.quotation_id == quotation.quotation_id)
        if inv:
            invoice_id = inv.invoice_id
    except Exception:
        pass

    # Construire la réponse avec le nom du client
    return {
        "quotation_id": quotation.quotation_id,
        "quotation_number": quotation.quotation_number,
        "client_id": quotation.client_id,
        "client_name": client_name,
        "client": {"name": client_name, "phone": client.phone if client else None, "email": client.email if client else None} if client else None,
        "date": quotation.date,
        "expiry_date": quotation.expiry_date,
        "status": quotation.status,
        "is_sent": bool(quotation.is_sent) if hasattr(quotation, 'is_sent') else False,
        "subtotal": float(quotation.subtotal or 0),
        "tax_rate": float(quotation.tax_rate or 0),
        "tax_amount": float(quotation.tax_amount or 0),
        "total": float(quotation.total or 0),
        "notes": quotation.notes,
        "show_item_prices": bool(getattr(quotation, 'show_item_prices', True)),
        "show_section_totals": bool(getattr(quotation, 'show_section_totals', True)),
        "created_at": quotation.created_at,
        "invoice_id": invoice_id,
        "items": [{
            "item_id": item.item_id,
            "product_id": item.product_id,
            "product_name": item.product_name,
            "quantity": item.quantity,
            "price": float(item.price or 0),
            "total": float(item.total or 0)
        } for item in items]
    }


@router.post("/", response_model=QuotationResponse)
async def create_quotation(
    quotation_data: QuotationCreate,
    current_user=Depends(get_current_user),
):
    """Créer un nouveau devis.
    - Si le numéro est vide/auto ou déjà utilisé, génère automatiquement DEV-####.
    """
    try:
        # Vérifier que le client existe
        client = await Client.find_one(Client.client_id == quotation_data.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")

        # Déterminer le numéro final (Tolère 'AUTO')
        requested = (str(quotation_data.quotation_number or '').strip())
        if not requested or requested.upper() in {"AUTO", "AUTOMATIC"}:
            final_qnum = await _next_quotation_number()
        else:
            exists = await Quotation.find_one(Quotation.quotation_number == requested)
            final_qnum = requested if not exists else await _next_quotation_number()

        # Créer le devis
        new_id = await get_next_id("quotations")
        db_quotation = Quotation(
            quotation_id=new_id,
            quotation_number=final_qnum,
            client_id=quotation_data.client_id,
            date=quotation_data.date,
            expiry_date=quotation_data.expiry_date,
            subtotal=quotation_data.subtotal,
            tax_rate=quotation_data.tax_rate,
            tax_amount=quotation_data.tax_amount,
            total=quotation_data.total,
            notes=quotation_data.notes,
            show_item_prices=getattr(quotation_data, 'show_item_prices', True),
            show_section_totals=getattr(quotation_data, 'show_section_totals', True),
            created_by=current_user.user_id,
        )

        await db_quotation.insert()

        # Créer les éléments du devis (supporte lignes personnalisées sans produit)
        for item_data in quotation_data.items:
            pid = getattr(item_data, 'product_id', None)
            if pid is not None:
                # Vérifier l'existence uniquement si un product_id est fourni
                product = await Product.find_one(Product.product_id == pid)
                if not product:
                    raise HTTPException(status_code=404, detail=f"Produit {pid} non trouvé")
            item_id = await get_next_id("quotation_items")
            db_item = QuotationItem(
                item_id=item_id,
                quotation_id=db_quotation.quotation_id,
                product_id=pid,
                product_name=item_data.product_name,
                quantity=item_data.quantity,
                price=item_data.price,
                total=item_data.total,
            )
            await db_item.insert()

        try:
            await _recompute_quotations_stats()
        except Exception:
            pass

        return db_quotation

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la création du devis: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.put("/{quotation_id}", response_model=QuotationResponse)
async def update_quotation(
    quotation_id: int,
    quotation_data: QuotationCreate,
    current_user=Depends(get_current_user),
):
    """Mettre à jour un devis existant et ses lignes."""
    try:
        quotation = await Quotation.find_one(Quotation.quotation_id == quotation_id)
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")

        # Normaliser et garantir l'unicité du numéro (même comportement que la création)
        requested_num = str(quotation_data.quotation_number or '').strip()
        current_num = str(quotation.quotation_number or '').strip()

        # Autoriser 'AUTO' / vide pour régénérer un numéro
        if not requested_num or requested_num.upper() in {"AUTO", "AUTOMATIC"}:
            requested_num = await _next_quotation_number()
        elif requested_num != current_num:
            existing = await Quotation.find_one(Quotation.quotation_number == requested_num)
            if existing and int(existing.quotation_id) != int(quotation_id):
                # Conflit: attribuer automatiquement le prochain numéro disponible plutôt que d'erreur
                requested_num = await _next_quotation_number()

        # Vérifier client
        client = await Client.find_one(Client.client_id == quotation_data.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")

        # Mettre à jour les champs principaux
        quotation.quotation_number = requested_num
        quotation.client_id = quotation_data.client_id
        quotation.date = quotation_data.date
        quotation.expiry_date = quotation_data.expiry_date
        quotation.subtotal = quotation_data.subtotal
        quotation.tax_rate = quotation_data.tax_rate
        quotation.tax_amount = quotation_data.tax_amount
        quotation.total = quotation_data.total
        quotation.notes = quotation_data.notes
        quotation.show_item_prices = getattr(quotation_data, 'show_item_prices', True)
        quotation.show_section_totals = getattr(quotation_data, 'show_section_totals', True)

        # Normaliser un statut éventuel reçu
        try:
            raw_status = getattr(quotation_data, 'status', None)
            if raw_status:
                s = str(raw_status).strip().lower()
                if s in ["draft", "sent", "en attente", "en_attente", "brouillon", "envoyé", "envoye"]:
                    quotation.status = "en attente"
                elif s in ["accepté", "accepte", "accepted"]:
                    quotation.status = "accepté"
                elif s in ["refusé", "refuse", "rejected"]:
                    quotation.status = "refusé"
                elif s in ["expiré", "expire", "expired"]:
                    quotation.status = "expiré"
        except Exception:
            pass

        await quotation.save()

        # Remplacer les lignes (supprimer les anciennes, insérer les nouvelles)
        old_items = await QuotationItem.find(QuotationItem.quotation_id == quotation.quotation_id).to_list()
        for old in old_items:
            try:
                await old.delete()
            except Exception:
                pass

        for item_data in (quotation_data.items or []):
            pid = getattr(item_data, 'product_id', None)
            if pid is not None:
                product = await Product.find_one(Product.product_id == pid)
                if not product:
                    raise HTTPException(status_code=404, detail=f"Produit {pid} non trouvé")
            item_id = await get_next_id("quotation_items")
            db_item = QuotationItem(
                item_id=item_id,
                quotation_id=quotation.quotation_id,
                product_id=pid,
                product_name=item_data.product_name,
                quantity=item_data.quantity,
                price=item_data.price,
                total=item_data.total,
            )
            await db_item.insert()

        return quotation

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la mise à jour du devis: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.put("/{quotation_id}/status")
async def update_quotation_status(
    quotation_id: int,
    payload: dict,
    current_user=Depends(get_current_user),
):
    """Mettre à jour le statut d'un devis"""
    try:
        quotation = await Quotation.find_one(Quotation.quotation_id == quotation_id)
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")

        new_status = str(payload.get("status", "")).lower()
        valid_statuses = ["en attente", "accepté", "refusé", "expiré"]
        if new_status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Statut invalide")

        quotation.status = new_status
        await quotation.save()

        try:
            await _recompute_quotations_stats()
        except Exception:
            pass

        return {"message": "Statut mis à jour avec succès"}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la mise à jour du statut: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.get("/next-number")
async def get_next_quotation_number(
    current_user=Depends(get_current_user),
):
    try:
        return {"quotation_number": await _next_quotation_number()}
    except Exception as e:
        logging.error(f"Erreur get_next_quotation_number: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.delete("/{quotation_id}")
async def delete_quotation(
    quotation_id: int,
    current_user=Depends(get_current_user),
):
    """Supprimer un devis"""
    try:
        quotation = await Quotation.find_one(Quotation.quotation_id == quotation_id)
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")

        # Also delete associated items
        items = await QuotationItem.find(QuotationItem.quotation_id == quotation_id).to_list()
        for item in items:
            await item.delete()

        await quotation.delete()

        try:
            await _recompute_quotations_stats()
        except Exception:
            pass

        return {"message": "Devis supprimé avec succès"}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la suppression du devis: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.post("/{quotation_id}/convert-to-invoice")
async def convert_to_invoice(
    quotation_id: int,
    payload: dict = None,
    current_user=Depends(get_current_user),
):
    """Convertir un devis en facture"""
    try:
        quotation = await Quotation.find_one(Quotation.quotation_id == quotation_id)
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")

        if quotation.status != "accepté":
            raise HTTPException(status_code=400, detail="Seuls les devis acceptés peuvent être convertis")

        # Éviter la double conversion
        existing_invoice_for_quote = await Invoice.find_one(Invoice.quotation_id == quotation.quotation_id)
        if existing_invoice_for_quote:
            return {"message": "Déjà converti", "invoice_id": existing_invoice_for_quote.invoice_id, "invoice_number": existing_invoice_for_quote.invoice_number}

        # Numéro de facture: à partir du payload ou auto-généré
        req_number = None
        try:
            if payload and isinstance(payload, dict):
                tmp = (payload.get("invoice_number") or "").strip()
                req_number = tmp or None
        except Exception:
            req_number = None

        if req_number:
            exists = await Invoice.find_one(Invoice.invoice_number == req_number)
            invoice_number_final = req_number if not exists else await _next_invoice_number_async()
        else:
            invoice_number_final = await _next_invoice_number_async()

        # Due date + paiement initial éventuel
        from datetime import timedelta
        payment_payload = (payload or {}).get('payment') if isinstance(payload, dict) else None
        term_days = 30
        try:
            term_days = int((payload or {}).get('payment_terms') or 30)
        except Exception:
            term_days = 30
        due_date = datetime.now().date() + timedelta(days=term_days)

        # Fetch quotation items
        q_items = await QuotationItem.find(QuotationItem.quotation_id == quotation.quotation_id).to_list()

        # Créer la facture
        invoice_id = await get_next_id("invoices")
        db_invoice = Invoice(
            invoice_id=invoice_id,
            invoice_number=invoice_number_final,
            client_id=quotation.client_id,
            quotation_id=quotation.quotation_id,
            date=datetime.now().date(),
            due_date=due_date,
            subtotal=quotation.subtotal,
            tax_rate=quotation.tax_rate,
            tax_amount=quotation.tax_amount,
            total=quotation.total,
            remaining_amount=quotation.total,
            notes=f"Convertie du devis {quotation.quotation_number}",
            show_tax=bool(float(quotation.tax_rate or 0) > 0),
            price_display="TTC",
        )

        await db_invoice.insert()

        # Copier les éléments
        # Conserver la quantité d'origine par produit dans des métadonnées pour affichage ultérieur
        quote_qty_map = {}
        for item in q_items:
            try:
                pid = int(item.product_id) if item.product_id is not None else None
                if pid is not None:
                    quote_qty_map[pid] = (quote_qty_map.get(pid, 0) + int(item.quantity or 0))
            except Exception:
                pass
            inv_item_id = await get_next_id("invoice_items")
            db_item = InvoiceItem(
                item_id=inv_item_id,
                invoice_id=db_invoice.invoice_id,
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                price=item.price,
                total=item.total,
            )
            await db_item.insert()

        # Paiement initial optionnel
        if payment_payload and isinstance(payment_payload, dict):
            try:
                amt = float(payment_payload.get('amount') or 0)
                method = (payment_payload.get('method') or '').strip() or None
                if amt and amt > 0:
                    pay_id = await get_next_id("invoice_payments")
                    pay = InvoicePayment(
                        payment_id=pay_id,
                        invoice_id=db_invoice.invoice_id,
                        amount=amt,
                        payment_method=method,
                    )
                    await pay.insert()
                    # MAJ montants payés/restants
                    db_invoice.paid_amount = (db_invoice.paid_amount or 0) + amt
                    db_invoice.remaining_amount = max(0, float(db_invoice.total or 0) - float(db_invoice.paid_amount or 0))
                    # statut
                    if db_invoice.remaining_amount == 0:
                        db_invoice.status = 'payée'
                    elif db_invoice.paid_amount > 0:
                        db_invoice.status = 'partiellement payée'
                    await db_invoice.save()
            except Exception:
                pass

        # Stocker les quantités du devis dans les notes de la facture sous forme de méta balise
        try:
            import json as _json
            import re as _re
            if quote_qty_map:
                serialized = _json.dumps([{"product_id": pid, "qty": qty} for pid, qty in quote_qty_map.items()])
                base_notes = (db_invoice.notes or "").strip()
                # Nettoyer une éventuelle ancienne balise
                if base_notes and "__QUOTE_QTYS__=" in base_notes:
                    base_notes = _re.sub(r"\n?\n?__QUOTE_QTYS__=.*$", "", base_notes, flags=_re.S)
                meta = f"__QUOTE_QTYS__={serialized}"
                db_invoice.notes = (base_notes + ("\n\n" if base_notes else "") + meta).strip()
                await db_invoice.save()
        except Exception:
            pass

        return {"message": "Devis converti en facture avec succès", "invoice_id": db_invoice.invoice_id, "invoice_number": db_invoice.invoice_number}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la conversion: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.put("/{quotation_id}/sent")
async def set_quotation_sent(
    quotation_id: int,
    payload: dict,
    current_user=Depends(get_current_user),
):
    """Basculer le champ 'is_sent' d'un devis (Oui/Non)."""
    try:
        quotation = await Quotation.find_one(Quotation.quotation_id == quotation_id)
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")
        is_sent = bool(payload.get("is_sent", False))
        quotation.is_sent = is_sent
        await quotation.save()
        return {"message": "Statut d'envoi mis à jour", "is_sent": is_sent}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la MAJ is_sent: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


# Configuration n8n
N8N_BASE_URL = os.getenv("N8N_WEBHOOK_URL", os.getenv("N8N_BASE_URL", "http://n8n:5678"))


class SendQuotationWhatsAppRequest(BaseModel):
    quotation_id: int
    phone: str


class SendQuotationEmailRequest(BaseModel):
    quotation_id: int
    email: str


@router.post("/send-whatsapp")
async def send_quotation_whatsapp(
    request: Request,
    data: SendQuotationWhatsAppRequest,
    current_user=Depends(get_current_user),
):
    """Envoyer un devis par WhatsApp via n8n"""
    try:
        # Vérifier que le devis existe
        quotation = await Quotation.find_one(Quotation.quotation_id == data.quotation_id)
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")

        # Construire l'URL du PDF du devis
        app_public_url = os.getenv("APP_PUBLIC_URL", str(request.base_url).rstrip('/'))
        pdf_url = f"{app_public_url}/quotations/print/{data.quotation_id}"

        # Appeler le webhook n8n pour envoyer via WhatsApp
        webhook_url = f"{N8N_BASE_URL}/webhook/send-quotation-whatsapp"

        client_obj = await Client.find_one(Client.client_id == quotation.client_id)

        payload = {
            "quotation_id": data.quotation_id,
            "quotation_number": quotation.quotation_number,
            "phone": data.phone,
            "pdf_url": pdf_url,
            "client_name": client_obj.name if client_obj else "Client",
            "total": float(quotation.total or 0)
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(webhook_url, json=payload)

        if response.status_code == 200:
            # Marquer le devis comme envoyé
            quotation.is_sent = True
            await quotation.save()
            return {"success": True, "message": "Devis envoyé par WhatsApp"}
        else:
            logging.error(f"Erreur n8n WhatsApp: {response.status_code} - {response.text}")
            return {"success": False, "message": f"Erreur n8n: {response.text}"}

    except httpx.RequestError as e:
        logging.error(f"Erreur connexion n8n: {e}")
        raise HTTPException(status_code=503, detail="Service n8n indisponible")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur envoi WhatsApp: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send-email")
async def send_quotation_email(
    request: Request,
    data: SendQuotationEmailRequest,
    current_user=Depends(get_current_user),
):
    """Envoyer un devis par Email via n8n"""
    try:
        # Vérifier que le devis existe
        quotation = await Quotation.find_one(Quotation.quotation_id == data.quotation_id)
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")

        # Construire l'URL HTML du devis
        app_public_url = os.getenv("APP_PUBLIC_URL", "http://nitek_app:8000")
        pdf_url = f"{app_public_url}/quotations/print/{data.quotation_id}"

        # Appeler le webhook n8n pour envoyer par email
        webhook_url = f"{N8N_BASE_URL}/webhook/send-quotation-email"

        client_obj = await Client.find_one(Client.client_id == quotation.client_id)

        payload = {
            "quotation_id": data.quotation_id,
            "quotation_number": quotation.quotation_number,
            "email": data.email,
            "pdf_url": pdf_url,
            "client_name": client_obj.name if client_obj else "Client",
            "total": float(quotation.total or 0)
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(webhook_url, json=payload)

        if response.status_code == 200:
            # Marquer le devis comme envoyé
            quotation.is_sent = True
            await quotation.save()
            return {"success": True, "message": "Devis envoyé par email"}
        else:
            logging.error(f"Erreur n8n Email: {response.status_code} - {response.text}")
            return {"success": False, "message": f"Erreur n8n: {response.text}"}

    except httpx.RequestError as e:
        logging.error(f"Erreur connexion n8n: {e}")
        raise HTTPException(status_code=503, detail="Service n8n indisponible")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur envoi email: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{quotation_id}/duplicate", response_model=QuotationResponse)
async def duplicate_quotation(
    quotation_id: int,
    current_user=Depends(get_current_user),
):
    """Dupliquer un devis existant avec tous ses articles"""
    try:
        original = await Quotation.find_one(Quotation.quotation_id == quotation_id)
        if not original:
            raise HTTPException(status_code=404, detail="Devis non trouvé")

        # Générer un nouveau numéro de devis
        new_number = await _next_quotation_number()

        # Calculer la date d'expiration (30 jours par défaut)
        from datetime import timedelta
        new_date = datetime.now()
        new_expiry = new_date + timedelta(days=30)

        # Créer une copie du devis
        new_id = await get_next_id("quotations")
        new_quotation = Quotation(
            quotation_id=new_id,
            quotation_number=new_number,
            client_id=original.client_id,
            date=new_date,
            expiry_date=new_expiry,
            status="en attente",
            notes=original.notes,
            subtotal=original.subtotal,
            tax_rate=original.tax_rate,
            tax_amount=original.tax_amount,
            total=original.total,
            show_item_prices=original.show_item_prices,
            show_section_totals=original.show_section_totals,
            is_sent=False,
        )

        await new_quotation.insert()

        # Copier les articles
        original_items = await QuotationItem.find(QuotationItem.quotation_id == quotation_id).to_list()
        for item in original_items:
            new_item_id = await get_next_id("quotation_items")
            new_item = QuotationItem(
                item_id=new_item_id,
                quotation_id=new_quotation.quotation_id,
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                price=item.price,
                total=item.total,
            )
            await new_item.insert()

        # Construire la réponse avec tous les champs requis
        return {
            "quotation_id": new_quotation.quotation_id,
            "quotation_number": new_quotation.quotation_number,
            "client_id": new_quotation.client_id,
            "date": new_quotation.date,
            "expiry_date": new_quotation.expiry_date,
            "status": new_quotation.status,
            "is_sent": new_quotation.is_sent,
            "subtotal": new_quotation.subtotal,
            "tax_rate": new_quotation.tax_rate,
            "tax_amount": new_quotation.tax_amount,
            "total": new_quotation.total,
            "notes": new_quotation.notes,
            "show_item_prices": new_quotation.show_item_prices,
            "show_section_totals": new_quotation.show_section_totals,
            "created_at": new_quotation.created_at,
            "invoice_id": None,
            "items": [],
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la duplication du devis: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la duplication")
