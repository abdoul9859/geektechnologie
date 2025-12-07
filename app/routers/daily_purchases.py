from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List
from datetime import date

from ..database import get_db, DailyPurchase, DailyPurchaseCategory
from ..auth import get_current_user, User
from ..schemas import (
    DailyPurchaseCreate, DailyPurchaseResponse,
    DailyPurchaseCategoryCreate, DailyPurchaseCategoryResponse,
)

router = APIRouter(prefix="/api/daily-purchases", tags=["daily-purchases"], dependencies=[Depends(get_current_user)])


@router.get("/", response_model=List[DailyPurchaseResponse])
async def list_daily_purchases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = 0,
    limit: int = Query(100, le=1000),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
):
    try:
        q = db.query(DailyPurchase)
        if date_from:
            q = q.filter(DailyPurchase.date >= date_from)
        if date_to:
            q = q.filter(DailyPurchase.date <= date_to)
        if category:
            q = q.filter(func.lower(DailyPurchase.category) == func.lower(category))
        if payment_method:
            q = q.filter(func.lower(DailyPurchase.payment_method) == func.lower(payment_method))
        if search:
            s = f"%{search.lower()}%"
            q = q.filter(
                func.lower(func.coalesce(DailyPurchase.description, "")) .like(s) |
                func.lower(func.coalesce(DailyPurchase.supplier, "")) .like(s) |
                func.lower(func.coalesce(DailyPurchase.reference, "")) .like(s)
            )
        q = q.order_by(DailyPurchase.date.desc(), DailyPurchase.id.desc())
        return q.offset(skip).limit(limit).all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/summary")
async def get_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    category: Optional[str] = Query(None),
):
    try:
        q = db.query(func.coalesce(func.sum(DailyPurchase.amount), 0))
        if date_from:
            q = q.filter(DailyPurchase.date >= date_from)
        if date_to:
            q = q.filter(DailyPurchase.date <= date_to)
        if category:
            q = q.filter(func.lower(DailyPurchase.category) == func.lower(category))
        total = float(q.scalar() or 0)

        # Par catégorie
        q2 = db.query(DailyPurchase.category, func.coalesce(func.sum(DailyPurchase.amount), 0))
        if date_from:
            q2 = q2.filter(DailyPurchase.date >= date_from)
        if date_to:
            q2 = q2.filter(DailyPurchase.date <= date_to)
        q2 = q2.group_by(DailyPurchase.category)
        by_category = [{"category": c or "", "amount": float(a or 0)} for c, a in q2.all()]

        return {"total": total, "by_category": by_category}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/", response_model=DailyPurchaseResponse)
async def create_daily_purchase(
    payload: DailyPurchaseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        # Forcer montant entier
        from decimal import Decimal
        amount_int = Decimal(str(payload.amount)).quantize(Decimal('1'))
        item = DailyPurchase(
            date=payload.date,
            category=payload.category,
            supplier=payload.supplier,
            description=payload.description,
            amount=amount_int,
            payment_method=payload.payment_method,
            reference=payload.reference,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ================= Categories management =================

@router.get("/categories", response_model=List[DailyPurchaseCategoryResponse])
async def list_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # If empty, return empty; UI can propose defaults
    cats = db.query(DailyPurchaseCategory).order_by(DailyPurchaseCategory.name.asc()).all()
    return cats


@router.post("/categories", response_model=DailyPurchaseCategoryResponse)
async def add_category(
    payload: DailyPurchaseCategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    name = (payload.name or '').strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nom de catégorie requis")
    existing = db.query(DailyPurchaseCategory).filter(func.lower(DailyPurchaseCategory.name) == name.lower()).first()
    if existing:
        return existing
    cat = DailyPurchaseCategory(name=name)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


@router.delete("/categories/{cat_id}")
async def delete_category(
    cat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cat = db.query(DailyPurchaseCategory).filter(DailyPurchaseCategory.id == cat_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Catégorie introuvable")
    db.delete(cat)
    db.commit()
    return {"message": "Catégorie supprimée"}

@router.put("/{item_id}", response_model=DailyPurchaseResponse)
async def update_daily_purchase(
    item_id: int,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from datetime import date as _date
    from decimal import Decimal
    try:
        item = db.query(DailyPurchase).filter(DailyPurchase.id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Achat quotidien introuvable")

        data = payload or {}
        if "date" in data and data["date"]:
            try:
                if isinstance(data["date"], str):
                    data["date"] = _date.fromisoformat(data["date"][:10])
            except Exception:
                # Ignorer date invalide au lieu d'erreur
                data.pop("date", None)
        if "amount" in data and data["amount"] is not None:
            try:
                data["amount"] = Decimal(str(data["amount"]).replace(',', '.')).quantize(Decimal('1'))
            except Exception:
                # Ignorer montant invalide au lieu d'erreur
                data.pop("amount", None)

        for field in ["date", "category", "description", "amount", "payment_method", "reference", "supplier"]:
            if field in data:
                setattr(item, field, data[field])
        db.commit()
        db.refresh(item)
        return item
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{item_id}")
async def delete_daily_purchase(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        item = db.query(DailyPurchase).filter(DailyPurchase.id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Achat quotidien introuvable")
        db.delete(item)
        db.commit()
        return {"message": "Supprimé"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
