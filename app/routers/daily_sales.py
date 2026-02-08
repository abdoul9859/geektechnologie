from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal

from ..database import DailySale, Client, Product, ProductVariant, Invoice, StockMovement, get_next_id
from ..schemas import (
    DailySaleCreate,
    DailySaleUpdate,
    DailySaleResponse,
)
from ..auth import get_current_user

router = APIRouter(prefix="/api/daily-sales", tags=["daily-sales"])


@router.get("/", response_model=List[DailySaleResponse])
async def get_daily_sales(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    payment_method: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    """Recuperer la liste des ventes quotidiennes"""
    filters: dict = {}

    if search:
        pattern = {"$regex": search, "$options": "i"}
        filters["$or"] = [
            {"client_name": pattern},
            {"product_name": pattern},
            {"notes": pattern},
        ]

    if start_date and end_date and start_date == end_date:
        filters["sale_date"] = start_date
    else:
        if start_date:
            filters.setdefault("sale_date", {})["$gte"] = start_date
        if end_date:
            filters.setdefault("sale_date", {})["$lte"] = end_date

    if payment_method:
        filters["payment_method"] = payment_method

    # Restreindre les ventes liees a des factures impayees pour les non-admins
    try:
        role = getattr(current_user, "role", "user")
    except Exception:
        role = "user"

    sales = (
        await DailySale.find(filters)
        .sort([("sale_date", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list()
    )

    # Batch-load invoices for filtering and status attachment
    inv_ids_all = list({int(s.invoice_id) for s in sales if getattr(s, "invoice_id", None)})
    invoices_map = {}
    if inv_ids_all:
        invoices_list = await Invoice.find({"invoice_id": {"$in": inv_ids_all}}).to_list()
        invoices_map = {int(inv.invoice_id): inv for inv in invoices_list}

    if role != "admin":
        allowed_statuses = {"payee", "PAID", "partiellement payee"}
        filtered_sales = []
        for s in sales:
            if s.invoice_id is None:
                filtered_sales.append(s)
            else:
                inv = invoices_map.get(int(s.invoice_id))
                if inv and inv.status in allowed_statuses:
                    filtered_sales.append(s)
        sales = filtered_sales

    # Attach invoice payment status
    for s in sales:
        try:
            if s.invoice_id:
                inv = invoices_map.get(int(s.invoice_id))
                if inv:
                    setattr(s, "invoice_status", inv.status)
        except Exception:
            pass

    return sales


@router.get("/{sale_id}", response_model=DailySaleResponse)
async def get_daily_sale(
    sale_id: int,
    current_user=Depends(get_current_user),
):
    """Recuperer une vente specifique"""
    sale = await DailySale.find_one(DailySale.sale_id == sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Vente non trouvee")
    return sale


@router.post("/", response_model=DailySaleResponse)
async def create_daily_sale(
    sale_data: DailySaleCreate,
    current_user=Depends(get_current_user),
):
    """Creer une nouvelle vente quotidienne"""
    variant = None

    # Verifier si le client existe si client_id est fourni
    if sale_data.client_id:
        client = await Client.find_one(Client.client_id == sale_data.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouve")

    # Verifier si le produit existe si product_id est fourni
    product = None
    if sale_data.product_id:
        product = await Product.find_one(Product.product_id == sale_data.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouve")

        if sale_data.variant_id:
            variant = await ProductVariant.find_one(
                {
                    "variant_id": sale_data.variant_id,
                    "product_id": sale_data.product_id,
                    "is_sold": False,
                }
            )
            if not variant:
                raise HTTPException(status_code=404, detail="Variante non trouvee ou deja vendue")
        else:
            if product.quantity < sale_data.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Stock insuffisant. Disponible: {product.quantity}, Demande: {sale_data.quantity}",
                )

    # Creer la vente
    new_id = await get_next_id("daily_sales")
    db_sale = DailySale(
        sale_id=new_id,
        client_id=sale_data.client_id,
        client_name=sale_data.client_name,
        product_id=sale_data.product_id,
        product_name=sale_data.product_name,
        variant_id=sale_data.variant_id,
        variant_imei=sale_data.variant_imei,
        variant_barcode=sale_data.variant_barcode,
        variant_condition=sale_data.variant_condition,
        quantity=sale_data.quantity,
        unit_price=sale_data.unit_price,
        total_amount=sale_data.total_amount,
        sale_date=sale_data.sale_date,
        payment_method=sale_data.payment_method,
        invoice_id=sale_data.invoice_id,
        notes=sale_data.notes,
    )

    await db_sale.insert()

    # Mettre a jour le stock si un produit est specifie
    if sale_data.product_id:
        if sale_data.variant_id and variant:
            variant.is_sold = True
            await variant.save()

            sm_id = await get_next_id("stock_movements")
            stock_movement = StockMovement(
                movement_id=sm_id,
                product_id=sale_data.product_id,
                quantity=-1,
                movement_type="OUT",
                reference_type="DAILY_SALE",
                reference_id=db_sale.sale_id,
                notes=f"Vente quotidienne - {sale_data.client_name} (Variante: {sale_data.variant_imei})",
                unit_price=sale_data.unit_price,
            )
            await stock_movement.insert()
        else:
            if product:
                product.quantity -= sale_data.quantity
                await product.save()

            sm_id = await get_next_id("stock_movements")
            stock_movement = StockMovement(
                movement_id=sm_id,
                product_id=sale_data.product_id,
                quantity=-sale_data.quantity,
                movement_type="OUT",
                reference_type="DAILY_SALE",
                reference_id=db_sale.sale_id,
                notes=f"Vente quotidienne - {sale_data.client_name}",
                unit_price=sale_data.unit_price,
            )
            await stock_movement.insert()

    return db_sale


@router.put("/{sale_id}", response_model=DailySaleResponse)
async def update_daily_sale(
    sale_id: int,
    sale_data: DailySaleUpdate,
    current_user=Depends(get_current_user),
):
    """Mettre a jour une vente quotidienne"""
    db_sale = await DailySale.find_one(DailySale.sale_id == sale_id)
    if not db_sale:
        raise HTTPException(status_code=404, detail="Vente non trouvee")

    # Verifier si le client existe si client_id est fourni
    if sale_data.client_id:
        client = await Client.find_one(Client.client_id == sale_data.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouve")

    # Verifier si le produit existe si product_id est fourni
    if sale_data.product_id:
        product = await Product.find_one(Product.product_id == sale_data.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouve")

    # Sauvegarder les anciennes valeurs pour ajuster le stock
    old_quantity = db_sale.quantity
    old_product_id = db_sale.product_id

    # Mettre a jour les champs
    update_data = sale_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_sale, field, value)

    # Ajuster le stock si necessaire
    if old_product_id and db_sale.product_id:
        if old_product_id == db_sale.product_id:
            quantity_diff = db_sale.quantity - old_quantity
            if quantity_diff != 0:
                product = await Product.find_one(Product.product_id == db_sale.product_id)
                if product:
                    product.quantity -= quantity_diff
                    await product.save()
        else:
            old_product = await Product.find_one(Product.product_id == old_product_id)
            if old_product:
                old_product.quantity += old_quantity
                await old_product.save()

            new_product = await Product.find_one(Product.product_id == db_sale.product_id)
            if new_product:
                new_product.quantity -= db_sale.quantity
                await new_product.save()

    await db_sale.save()

    return db_sale


@router.delete("/{sale_id}")
async def delete_daily_sale(
    sale_id: int,
    current_user=Depends(get_current_user),
):
    """Supprimer une vente quotidienne"""
    db_sale = await DailySale.find_one(DailySale.sale_id == sale_id)
    if not db_sale:
        raise HTTPException(status_code=404, detail="Vente non trouvee")

    # Remettre le stock si un produit etait associe
    if db_sale.product_id:
        if db_sale.variant_id:
            variant = await ProductVariant.find_one(ProductVariant.variant_id == db_sale.variant_id)
            if variant:
                variant.is_sold = False
                await variant.save()
        else:
            product = await Product.find_one(Product.product_id == db_sale.product_id)
            if product:
                product.quantity += db_sale.quantity
                await product.save()

    # Supprimer les mouvements de stock associes
    await StockMovement.find(
        {
            "reference_type": "DAILY_SALE",
            "reference_id": sale_id,
        }
    ).delete()

    await db_sale.delete()

    return {"message": "Vente supprimee avec succes"}


@router.get("/stats/summary")
async def get_sales_summary(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user=Depends(get_current_user),
):
    """Obtenir un resume des ventes"""
    filters: dict = {}

    if start_date:
        filters.setdefault("sale_date", {})["$gte"] = start_date
    if end_date:
        filters.setdefault("sale_date", {})["$lte"] = end_date

    try:
        role = getattr(current_user, "role", "user")
    except Exception:
        role = "user"

    # Get all matching sales
    all_sales = await DailySale.find(filters).to_list()

    # Filter for non-admins — batch load invoices
    if role != "admin":
        inv_ids_check = list({int(s.invoice_id) for s in all_sales if s.invoice_id is not None})
        inv_map = {}
        if inv_ids_check:
            inv_list = await Invoice.find({"invoice_id": {"$in": inv_ids_check}}).to_list()
            inv_map = {int(inv.invoice_id): inv for inv in inv_list}
        allowed_statuses = {"payee", "PAID", "partiellement payee"}
        filtered_sales = []
        for s in all_sales:
            if s.invoice_id is None:
                filtered_sales.append(s)
            else:
                inv = inv_map.get(int(s.invoice_id))
                if inv and inv.status in allowed_statuses:
                    filtered_sales.append(s)
        all_sales = filtered_sales

    sale_ids = [s.sale_id for s in all_sales]

    # Direct sales (no invoice)
    direct_sales = sum(1 for s in all_sales if s.invoice_id is None)

    # Distinct invoice IDs
    invoice_ids_set = {s.invoice_id for s in all_sales if s.invoice_id is not None}
    invoice_sales_distinct = len(invoice_ids_set)

    total_sales = direct_sales + invoice_sales_distinct
    total_amount = sum(float(s.total_amount or 0) for s in all_sales)

    # Ventes par methode de paiement
    payment_method_stats: dict = {}
    for s in all_sales:
        pm = s.payment_method or "autre"
        if pm not in payment_method_stats:
            payment_method_stats[pm] = {"count": 0, "total": 0}
        payment_method_stats[pm]["count"] += 1
        payment_method_stats[pm]["total"] += float(s.total_amount or 0)

    invoice_sales = invoice_sales_distinct

    avg_sale = float(total_amount / total_sales) if total_sales > 0 else 0
    if role != "admin":
        avg_sale = 0.0

    return {
        "total_sales": total_sales,
        "total_amount": float(total_amount),
        "average_sale": avg_sale,
        "payment_methods": [
            {"method": method, "count": stats["count"], "total": stats["total"]}
            for method, stats in payment_method_stats.items()
        ],
        "invoice_sales": invoice_sales,
        "direct_sales": direct_sales,
    }


@router.get("/by-date/{sale_date}")
async def get_sales_by_date(
    sale_date: date,
    current_user=Depends(get_current_user),
):
    """Recuperer les ventes d'une date specifique"""
    sales = await DailySale.find(DailySale.sale_date == sale_date).to_list()
    return sales
