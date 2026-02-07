from fastapi import APIRouter, Depends, HTTPException, Query, Body
from typing import Optional, List
from datetime import date
from decimal import Decimal

from ..database import DailyPurchase, DailyPurchaseCategory, get_next_id
from ..auth import get_current_user, User
from ..schemas import (
    DailyPurchaseCreate,
    DailyPurchaseResponse,
    DailyPurchaseCategoryCreate,
    DailyPurchaseCategoryResponse,
)

router = APIRouter(
    prefix="/api/daily-purchases",
    tags=["daily-purchases"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/", response_model=List[DailyPurchaseResponse])
async def list_daily_purchases(
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
        filters: dict = {}

        if date_from:
            filters.setdefault("date", {})["$gte"] = date_from
        if date_to:
            filters.setdefault("date", {})["$lte"] = date_to
        if category:
            filters["category"] = {"$regex": f"^{category}$", "$options": "i"}
        if payment_method:
            filters["payment_method"] = {"$regex": f"^{payment_method}$", "$options": "i"}
        if search:
            pattern = {"$regex": search, "$options": "i"}
            filters["$or"] = [
                {"description": pattern},
                {"supplier": pattern},
                {"reference": pattern},
            ]

        items = (
            await DailyPurchase.find(filters)
            .sort([("date", -1), ("purchase_id", -1)])
            .skip(skip)
            .limit(limit)
            .to_list()
        )
        return items
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/summary")
async def get_summary(
    current_user: User = Depends(get_current_user),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    category: Optional[str] = Query(None),
):
    try:
        match_filter: dict = {}
        if date_from:
            match_filter.setdefault("date", {})["$gte"] = date_from
        if date_to:
            match_filter.setdefault("date", {})["$lte"] = date_to
        if category:
            match_filter["category"] = {"$regex": f"^{category}$", "$options": "i"}

        # Total
        pipeline_total = [
            {"$match": match_filter} if match_filter else {"$match": {}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        total_result = await DailyPurchase.aggregate(pipeline_total).to_list()
        total = float(total_result[0]["total"]) if total_result else 0

        # Par categorie
        pipeline_cat = [
            {"$match": match_filter} if match_filter else {"$match": {}},
            {"$group": {"_id": "$category", "amount": {"$sum": "$amount"}}},
        ]
        cat_result = await DailyPurchase.aggregate(pipeline_cat).to_list()
        by_category = [
            {"category": r["_id"] or "", "amount": float(r["amount"] or 0)}
            for r in cat_result
        ]

        return {"total": total, "by_category": by_category}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/", response_model=DailyPurchaseResponse)
async def create_daily_purchase(
    payload: DailyPurchaseCreate,
    current_user: User = Depends(get_current_user),
):
    try:
        amount_int = Decimal(str(payload.amount)).quantize(Decimal("1"))
        new_id = await get_next_id("daily_purchases")
        item = DailyPurchase(
            purchase_id=new_id,
            date=payload.date,
            category=payload.category,
            supplier=payload.supplier,
            description=payload.description,
            amount=amount_int,
            payment_method=payload.payment_method,
            reference=payload.reference,
            created_by=current_user.user_id,
        )
        await item.insert()
        return item
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ================= Categories management =================


@router.get("/categories", response_model=List[DailyPurchaseCategoryResponse])
async def list_categories(
    current_user: User = Depends(get_current_user),
):
    cats = await DailyPurchaseCategory.find().sort("+name").to_list()
    return cats


@router.post("/categories", response_model=DailyPurchaseCategoryResponse)
async def add_category(
    payload: DailyPurchaseCategoryCreate,
    current_user: User = Depends(get_current_user),
):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nom de categorie requis")
    existing = await DailyPurchaseCategory.find_one(
        {"name": {"$regex": f"^{name}$", "$options": "i"}}
    )
    if existing:
        return existing
    new_id = await get_next_id("daily_purchase_categories")
    cat = DailyPurchaseCategory(category_pk=new_id, name=name)
    await cat.insert()
    return cat


@router.delete("/categories/{cat_id}")
async def delete_category(
    cat_id: int,
    current_user: User = Depends(get_current_user),
):
    cat = await DailyPurchaseCategory.find_one(DailyPurchaseCategory.category_pk == cat_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Categorie introuvable")
    await cat.delete()
    return {"message": "Categorie supprimee"}


@router.put("/{item_id}", response_model=DailyPurchaseResponse)
async def update_daily_purchase(
    item_id: int,
    payload: dict = Body(default={}),
    current_user: User = Depends(get_current_user),
):
    from datetime import date as _date

    try:
        item = await DailyPurchase.find_one(DailyPurchase.purchase_id == item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Achat quotidien introuvable")

        data = payload or {}
        if "date" in data and data["date"]:
            try:
                if isinstance(data["date"], str):
                    data["date"] = _date.fromisoformat(data["date"][:10])
            except Exception:
                data.pop("date", None)
        if "amount" in data and data["amount"] is not None:
            try:
                data["amount"] = Decimal(str(data["amount"]).replace(",", ".")).quantize(
                    Decimal("1")
                )
            except Exception:
                data.pop("amount", None)

        for field in [
            "date",
            "category",
            "description",
            "amount",
            "payment_method",
            "reference",
            "supplier",
        ]:
            if field in data:
                setattr(item, field, data[field])
        await item.save()
        return item
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{item_id}")
async def delete_daily_purchase(
    item_id: int,
    current_user: User = Depends(get_current_user),
):
    try:
        item = await DailyPurchase.find_one(DailyPurchase.purchase_id == item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Achat quotidien introuvable")
        await item.delete()
        return {"message": "Supprime"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
