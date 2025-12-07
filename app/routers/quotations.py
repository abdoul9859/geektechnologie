from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, text, and_, or_
from typing import List, Optional
from datetime import datetime, date as DateType
from pydantic import BaseModel
from ..database import get_db, Quotation, QuotationItem, Client, Product, Invoice
from ..schemas import QuotationCreate, QuotationResponse
from ..services.stats_manager import recompute_quotations_stats
from ..auth import get_current_user
import logging
import time

router = APIRouter(prefix="/api/quotations", tags=["quotations"]) 

# Helpers numérotation devis
from datetime import datetime as _dt
from sqlalchemy.orm import Session as _Session

def _next_quotation_number(db: _Session, prefix: Optional[str] = None) -> str:
    """Retourne le prochain numéro de devis séquentiel sous la forme PREFIX-#### (par défaut DEV-####)."""
    import re
    pf = (prefix or 'DEV').strip('-')
    base_prefix = f"{pf}-"

    try:
        rows = db.query(Quotation.quotation_number).filter(Quotation.quotation_number.ilike(f"{base_prefix}%")).all()
    except Exception:
        rows = []

    last_seq = 0
    # 1) format exact PREFIX-####
    for (num,) in (rows or []):
        if not isinstance(num, str):
            continue
        m = re.fullmatch(rf"{re.escape(pf)}-(\\d+)", num.strip())
        if m:
            val = int(m.group(1))
            if val > last_seq:
                last_seq = val

    # 2) fallback sur le plus grand suffixe numérique si mix d'anciens formats
    if last_seq == 0:
        for (num,) in (rows or []):
            if not isinstance(num, str):
                continue
            matches = re.findall(r'(\\d+)', num.strip())
            if matches:
                val = int(matches[-1])
                if val > last_seq:
                    last_seq = val

    next_seq = last_seq + 1
    while True:
        candidate = f"{base_prefix}{next_seq:04d}"
        exists = db.query(Quotation).filter(Quotation.quotation_number == candidate).first()
        if not exists:
            return candidate
        next_seq += 1

# Assurer la présence de la colonne is_sent (ajout sans Alembic)

def _ensure_quotation_sent_column(db: Session):
    try:
        bind = db.get_bind()
        dialect = bind.dialect.name
        if dialect == 'sqlite':
            res = db.execute(text("PRAGMA table_info(quotations)"))
            cols = [row[1] for row in res]
            if 'is_sent' not in cols:
                db.execute(text("ALTER TABLE quotations ADD COLUMN is_sent BOOLEAN DEFAULT 0"))
                db.commit()
        else:
            result = db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'quotations' AND column_name = 'is_sent'"))
            if not result.fetchone():
                db.execute(text("ALTER TABLE quotations ADD COLUMN is_sent BOOLEAN DEFAULT FALSE"))
                db.commit()
    except Exception:
        # silencieux: ne bloque pas l'app si la migration ad-hoc échoue
        pass

@router.get("/", response_model=List[QuotationResponse])
async def list_quotations(
    skip: int = 0,
    limit: int = 100,
    status_filter: Optional[str] = None,
    client_id: Optional[int] = None,
    start_date: Optional[DateType] = None,
    end_date: Optional[DateType] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les devis avec filtres"""
    _ensure_quotation_sent_column(db)
    query = db.query(Quotation).order_by(desc(Quotation.created_at))
    
    if status_filter:
        query = query.filter(Quotation.status == status_filter)
    
    if client_id:
        query = query.filter(Quotation.client_id == client_id)
    
    if start_date:
        query = query.filter(func.date(Quotation.date) >= start_date)
    
    if end_date:
        query = query.filter(func.date(Quotation.date) <= end_date)
    
    quotations = query.offset(skip).limit(limit).all()
    # Attacher l'ID de la facture liée (s'il existe) pour chaque devis
    try:
        qids = [int(q.quotation_id) for q in quotations]
        if qids:
            rows = (
                db.query(Invoice.quotation_id, Invoice.invoice_id)
                .filter(Invoice.quotation_id.in_(qids))
                .all()
            )
            qid_to_invoice = {int(r[0]): int(r[1]) for r in rows if r[0] is not None and r[1] is not None}
            for q in quotations:
                try:
                    setattr(q, "invoice_id", qid_to_invoice.get(int(q.quotation_id)))
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
    sort_by: Optional[str] = Query("date"),  # date | number | total | status | sent
    sort_dir: Optional[str] = Query("desc"), # asc | desc
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les devis avec pagination et filtres légers pour la liste."""
    # Cache key
    try:
        import time, hashlib
        key_raw = f"p={page}|s={page_size}|sf={status_filter}|cs={client_search}|sd={start_date}|ed={end_date}|ob={sort_by}|od={sort_dir}"
        key = hashlib.md5(key_raw.encode()).hexdigest()
        entry = _quotations_cache.get(key)
        if entry and (time.time() - entry['ts']) < _QUOTES_CACHE_TTL:
            return entry['data']
    except Exception:
        key = None
    _ensure_quotation_sent_column(db)

    # Sous-requête facture par devis (une ligne par devis)
    inv_sub = (
        db.query(Invoice.quotation_id.label('quotation_id'), func.max(Invoice.invoice_id).label('invoice_id'))
        .group_by(Invoice.quotation_id)
        .subquery()
    )

    base = db.query(
        Quotation.quotation_id,
        Quotation.quotation_number,
        Quotation.client_id,
        Quotation.date,
        Quotation.expiry_date,
        Quotation.total,
        Quotation.status,
        Quotation.is_sent,
        Client.name.label('client_name'),
        inv_sub.c.invoice_id
    ).outerjoin(Client, Client.client_id == Quotation.client_id)
    base = base.outerjoin(inv_sub, inv_sub.c.quotation_id == Quotation.quotation_id)

    # Filtres
    if status_filter:
        base = base.filter(Quotation.status == status_filter)
    if client_search:
        like = f"%{client_search.strip()}%"
        base = base.filter(Client.name.ilike(like))
    if start_date:
        base = base.filter(func.date(Quotation.date) >= start_date)
    if end_date:
        base = base.filter(func.date(Quotation.date) <= end_date)

    # Compteurs agrégés (basés sur mêmes filtres)
    agg_base = db.query(Quotation)
    if status_filter:
        agg_base = agg_base.filter(Quotation.status == status_filter)
    if client_search:
        agg_base = agg_base.join(Client).filter(Client.name.ilike(f"%{client_search.strip()}%"))
    if start_date:
        agg_base = agg_base.filter(func.date(Quotation.date) >= start_date)
    if end_date:
        agg_base = agg_base.filter(func.date(Quotation.date) <= end_date)

    start_ts = time.time()
    total = agg_base.count()
    total_accepted = agg_base.filter(Quotation.status == 'accepté').count()
    total_pending = agg_base.filter(Quotation.status == 'en attente').count()
    # Calcul du total via la même base filtrée, sans produit cartésien
    total_value = agg_base.with_entities(func.coalesce(func.sum(Quotation.total), 0)).scalar() or 0

    # Restreindre l'exposition de la valeur agrégée aux administrateurs uniquement
    try:
        role = getattr(current_user, "role", "user")
    except Exception:
        role = "user"
    if role != "admin":
        total_value = 0

    # Tri
    key = (sort_by or 'date').lower()
    desc_dir = (sort_dir or 'desc').lower() == 'desc'
    if key == 'number':
        order = Quotation.quotation_number.desc() if desc_dir else Quotation.quotation_number.asc()
    elif key == 'total':
        order = Quotation.total.desc() if desc_dir else Quotation.total.asc()
    elif key == 'status':
        order = Quotation.status.desc() if desc_dir else Quotation.status.asc()
    elif key == 'sent':
        order = Quotation.is_sent.desc() if desc_dir else Quotation.is_sent.asc()
    else:
        order = Quotation.date.desc() if desc_dir else Quotation.date.asc()
    base = base.order_by(order, Quotation.quotation_id.desc())

    # Pagination
    skip = (page - 1) * page_size
    rows = base.offset(skip).limit(page_size).all()

    items = []
    from datetime import datetime as _dt
    for r in rows:
        d = r[3]
        ed = r[4]
        if isinstance(d, _dt):
            d = d.date()
        if isinstance(ed, _dt):
            ed = ed.date()
        items.append({
            'quotation_id': int(r[0]),
            'quotation_number': r[1],
            'client_id': int(r[2]) if r[2] is not None else None,
            'date': (d.isoformat() if hasattr(d, 'isoformat') else (str(d) if d is not None else None)),
            'expiry_date': (ed.isoformat() if hasattr(ed, 'isoformat') else (str(ed) if ed is not None else None)),
            'total': float(r[5] or 0),
            'status': r[6],
            'is_sent': bool(r[7]) if r[7] is not None else False,
            'client_name': r[8],
            'invoice_id': int(r[9]) if r[9] is not None else None,
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
        if key:
            _quotations_cache[key] = { 'ts': time.time(), 'data': result }
    except Exception:
        pass

    return result

@router.get("/{quotation_id}", response_model=QuotationResponse)
async def get_quotation(
    quotation_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir un devis par ID"""
    _ensure_quotation_sent_column(db)
    quotation = db.query(Quotation).filter(Quotation.quotation_id == quotation_id).first()
    if not quotation:
        raise HTTPException(status_code=404, detail="Devis non trouvé")
    # Attacher l'ID de facture liée si présent
    try:
        inv = db.query(Invoice).filter(Invoice.quotation_id == quotation.quotation_id).first()
        if inv:
            setattr(quotation, "invoice_id", inv.invoice_id)
    except Exception:
        pass
    return quotation

@router.post("/", response_model=QuotationResponse)
async def create_quotation(
    quotation_data: QuotationCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Créer un nouveau devis.
    - Si le numéro est vide/auto ou déjà utilisé, génère automatiquement DEV-####.
    """
    try:
        _ensure_quotation_sent_column(db)
        # Vérifier que le client existe
        client = db.query(Client).filter(Client.client_id == quotation_data.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
        
        # Déterminer le numéro final (Tolère 'AUTO')
        requested = (str(quotation_data.quotation_number or '').strip())
        if not requested or requested.upper() in {"AUTO", "AUTOMATIC"}:
            final_qnum = _next_quotation_number(db)
        else:
            exists = db.query(Quotation).filter(Quotation.quotation_number == requested).first()
            final_qnum = requested if not exists else _next_quotation_number(db)
        
        # Créer le devis
        db_quotation = Quotation(
            quotation_number=final_qnum,
            client_id=quotation_data.client_id,
            date=quotation_data.date,
            expiry_date=quotation_data.expiry_date,
            subtotal=quotation_data.subtotal,
            tax_rate=quotation_data.tax_rate,
            tax_amount=quotation_data.tax_amount,
            total=quotation_data.total,
            notes=quotation_data.notes
        )
        
        db.add(db_quotation)
        db.flush()  # Pour obtenir l'ID du devis
        
        # Créer les éléments du devis (supporte lignes personnalisées sans produit)
        for item_data in quotation_data.items:
            pid = getattr(item_data, 'product_id', None)
            if pid is not None:
                # Vérifier l'existence uniquement si un product_id est fourni
                product = db.query(Product).filter(Product.product_id == pid).first()
                if not product:
                    raise HTTPException(status_code=404, detail=f"Produit {pid} non trouvé")
            db_item = QuotationItem(
                quotation_id=db_quotation.quotation_id,
                product_id=pid,
                product_name=item_data.product_name,
                quantity=item_data.quantity,
                price=item_data.price,
                total=item_data.total
            )
            db.add(db_item)
        
        db.commit()
        db.refresh(db_quotation)
        try:
            recompute_quotations_stats(db)
        except Exception:
            pass

        return db_quotation
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la création du devis: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/{quotation_id}", response_model=QuotationResponse)
async def update_quotation(
    quotation_id: int,
    quotation_data: QuotationCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Mettre à jour un devis existant et ses lignes."""
    try:
        _ensure_quotation_sent_column(db)
        quotation = db.query(Quotation).filter(Quotation.quotation_id == quotation_id).first()
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")

        # Normaliser et garantir l'unicité du numéro (même comportement que la création)
        requested_num = str(quotation_data.quotation_number or '').strip()
        current_num = str(quotation.quotation_number or '').strip()

        # Autoriser 'AUTO' / vide pour régénérer un numéro
        if not requested_num or requested_num.upper() in {"AUTO", "AUTOMATIC"}:
            requested_num = _next_quotation_number(db)
        elif requested_num != current_num:
            existing = db.query(Quotation).filter(Quotation.quotation_number == requested_num).first()
            if existing and int(existing.quotation_id) != int(quotation_id):
                # Conflit: attribuer automatiquement le prochain numéro disponible plutôt que d'erreur
                requested_num = _next_quotation_number(db)

        # Vérifier client
        client = db.query(Client).filter(Client.client_id == quotation_data.client_id).first()
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

        # Remplacer les lignes (delete-orphan actif)
        for old in list(quotation.items or []):
            try:
                db.delete(old)
            except Exception:
                pass
        db.flush()

        for item_data in (quotation_data.items or []):
            pid = getattr(item_data, 'product_id', None)
            if pid is not None:
                product = db.query(Product).filter(Product.product_id == pid).first()
                if not product:
                    raise HTTPException(status_code=404, detail=f"Produit {pid} non trouvé")
            db_item = QuotationItem(
                quotation_id=quotation.quotation_id,
                product_id=pid,
                product_name=item_data.product_name,
                quantity=item_data.quantity,
                price=item_data.price,
                total=item_data.total
            )
            db.add(db_item)

        db.commit()
        db.refresh(quotation)
        return quotation

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la mise à jour du devis: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/{quotation_id}/status")
async def update_quotation_status(
    quotation_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Mettre à jour le statut d'un devis"""
    try:
        _ensure_quotation_sent_column(db)
        quotation = db.query(Quotation).filter(Quotation.quotation_id == quotation_id).first()
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")
        
        new_status = str(payload.get("status", "")).lower()
        valid_statuses = ["en attente", "accepté", "refusé", "expiré"]
        if new_status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Statut invalide")
        
        quotation.status = new_status
        db.commit()
        try:
            recompute_quotations_stats(db)
        except Exception:
            pass
        
        return {"message": "Statut mis à jour avec succès"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la mise à jour du statut: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/next-number")
async def get_next_quotation_number(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    try:
        return {"quotation_number": _next_quotation_number(db)}
    except Exception as e:
        logging.error(f"Erreur get_next_quotation_number: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.delete("/{quotation_id}")
async def delete_quotation(
    quotation_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer un devis"""
    try:
        _ensure_quotation_sent_column(db)
        quotation = db.query(Quotation).filter(Quotation.quotation_id == quotation_id).first()
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")
        
        db.delete(quotation)
        db.commit()
        try:
            recompute_quotations_stats(db)
        except Exception:
            pass

        return {"message": "Devis supprimé avec succès"}
        
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la suppression du devis: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.post("/{quotation_id}/convert-to-invoice")
async def convert_to_invoice(
    quotation_id: int,
    payload: dict = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Convertir un devis en facture"""
    try:
        _ensure_quotation_sent_column(db)
        from ..database import Invoice, InvoiceItem, InvoicePayment
        
        quotation = db.query(Quotation).filter(Quotation.quotation_id == quotation_id).first()
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")
        
        if quotation.status != "accepté":
            raise HTTPException(status_code=400, detail="Seuls les devis acceptés peuvent être convertis")
        
        # Éviter la double conversion
        existing_invoice_for_quote = db.query(Invoice).filter(Invoice.quotation_id == quotation.quotation_id).first()
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

        # Utiliser le helper commun d'invoices pour calculer le prochain numéro si nécessaire
        from . import invoices as _inv_router
        if req_number:
            exists = db.query(Invoice).filter(Invoice.invoice_number == req_number).first()
            invoice_number_final = req_number if not exists else _inv_router._next_invoice_number(db)
        else:
            invoice_number_final = _inv_router._next_invoice_number(db)
        
        # Due date + paiement initial éventuel
        from datetime import timedelta
        payment_payload = (payload or {}).get('payment') if isinstance(payload, dict) else None
        term_days = 30
        try:
            term_days = int((payload or {}).get('payment_terms') or 30)
        except Exception:
            term_days = 30
        due_date = datetime.now().date() + timedelta(days=term_days)

        # Créer la facture
        db_invoice = Invoice(
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
        
        db.add(db_invoice)
        db.flush()
        
        # Copier les éléments
        # Conserver la quantité d'origine par produit dans des métadonnées pour affichage ultérieur
        quote_qty_map = {}
        for item in quotation.items:
            try:
                pid = int(item.product_id) if item.product_id is not None else None
                if pid is not None:
                    quote_qty_map[pid] = (quote_qty_map.get(pid, 0) + int(item.quantity or 0))
            except Exception:
                pass
            db_item = InvoiceItem(
                invoice_id=db_invoice.invoice_id,
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                price=item.price,
                total=item.total
            )
            db.add(db_item)
        
        # Paiement initial optionnel
        if payment_payload and isinstance(payment_payload, dict):
            try:
                amt = float(payment_payload.get('amount') or 0)
                method = (payment_payload.get('method') or '').strip() or None
                if amt and amt > 0:
                    pay = InvoicePayment(
                        invoice_id=db_invoice.invoice_id,
                        amount=amt,
                        payment_method=method,
                    )
                    db.add(pay)
                    # MAJ montants payés/restants
                    db_invoice.paid_amount = (db_invoice.paid_amount or 0) + amt
                    db_invoice.remaining_amount = max(0, (db_invoice.total or 0) - (db_invoice.paid_amount or 0))
                    # statut
                    if db_invoice.remaining_amount == 0:
                        db_invoice.status = 'payée'
                    elif db_invoice.paid_amount > 0:
                        db_invoice.status = 'partiellement payée'
            except Exception:
                pass

        # Stocker les quantités du devis dans les notes de la facture sous forme de méta balise
        try:
            import json as _json
            if quote_qty_map:
                serialized = _json.dumps([{"product_id": pid, "qty": qty} for pid, qty in quote_qty_map.items()])
                base_notes = (db_invoice.notes or "").strip()
                # Nettoyer une éventuelle ancienne balise
                import re as _re
                if base_notes and "__QUOTE_QTYS__=" in base_notes:
                    base_notes = _re.sub(r"\n?\n?__QUOTE_QTYS__=.*$", "", base_notes, flags=_re.S)
                meta = f"__QUOTE_QTYS__={serialized}"
                db_invoice.notes = (base_notes + ("\n\n" if base_notes else "") + meta).strip()
        except Exception:
            pass

        db.commit()
        
        # Mettre à jour côté devis: optionnel, mais nous laissons la relation se faire via la clé étrangère sur Invoice
        return {"message": "Devis converti en facture avec succès", "invoice_id": db_invoice.invoice_id, "invoice_number": db_invoice.invoice_number}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la conversion: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/{quotation_id}/sent")
async def set_quotation_sent(
    quotation_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Basculer le champ 'is_sent' d'un devis (Oui/Non)."""
    try:
        _ensure_quotation_sent_column(db)
        quotation = db.query(Quotation).filter(Quotation.quotation_id == quotation_id).first()
        if not quotation:
            raise HTTPException(status_code=404, detail="Devis non trouvé")
        is_sent = bool(payload.get("is_sent", False))
        quotation.is_sent = is_sent
        db.commit()
        return {"message": "Statut d'envoi mis à jour", "is_sent": is_sent}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la MAJ is_sent: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
