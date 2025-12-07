from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc, func
from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal

from app.database import get_db, DailySale, Client, Product, ProductVariant, Invoice, StockMovement
from app.schemas import (
    DailySaleCreate, 
    DailySaleUpdate, 
    DailySaleResponse
)
from app.auth import get_current_user

router = APIRouter(prefix="/api/daily-sales", tags=["daily-sales"])

@router.get("/", response_model=List[DailySaleResponse])
async def get_daily_sales(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    payment_method: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer la liste des ventes quotidiennes"""
    query = db.query(DailySale)
    
    # Filtres
    if search:
        query = query.filter(
            or_(
                DailySale.client_name.ilike(f"%{search}%"),
                DailySale.product_name.ilike(f"%{search}%"),
                DailySale.notes.ilike(f"%{search}%")
            )
        )
    
    if start_date and end_date and start_date == end_date:
        # Filtrage exact sur une seule date
        query = query.filter(DailySale.sale_date == start_date)
    else:
        if start_date:
            query = query.filter(DailySale.sale_date >= start_date)
        
        if end_date:
            query = query.filter(DailySale.sale_date <= end_date)
    
    if payment_method:
        query = query.filter(DailySale.payment_method == payment_method)
    
    # Restreindre les ventes liées à des factures impayées pour les non-admins
    try:
        role = getattr(current_user, "role", "user")
    except Exception:
        role = "user"
    if role != "admin":
        # Joindre les factures et ne garder que les ventes directes ou factures payées
        query = query.outerjoin(Invoice, DailySale.invoice_id == Invoice.invoice_id)
        query = query.filter(
            or_(
                DailySale.invoice_id.is_(None),
                Invoice.status.in_(["payée", "PAID", "partiellement payée"])  # inclure partiellement payée
            )
        )

    # Tri par date de vente décroissante
    query = query.order_by(desc(DailySale.sale_date), desc(DailySale.created_at))
    
    # Pagination
    sales = query.offset(skip).limit(limit).all()

    # Attacher le statut de paiement des factures liées
    try:
        inv_ids = [int(s.invoice_id) for s in sales if getattr(s, 'invoice_id', None)]
        if inv_ids:
            rows = db.query(Invoice.invoice_id, Invoice.status).filter(Invoice.invoice_id.in_(inv_ids)).all()
            st = {int(r[0]): r[1] for r in rows}
            for s in sales:
                try:
                    if s.invoice_id:
                        setattr(s, 'invoice_status', st.get(int(s.invoice_id)))
                except Exception:
                    pass
    except Exception:
        pass

    return sales

@router.get("/{sale_id}", response_model=DailySaleResponse)
async def get_daily_sale(
    sale_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer une vente spécifique"""
    sale = db.query(DailySale).filter(DailySale.sale_id == sale_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Vente non trouvée")
    return sale

@router.post("/", response_model=DailySaleResponse)
async def create_daily_sale(
    sale_data: DailySaleCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Créer une nouvelle vente quotidienne"""
    # Vérifier si le client existe si client_id est fourni
    if sale_data.client_id:
        client = db.query(Client).filter(Client.client_id == sale_data.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
    
    # Vérifier si le produit existe si product_id est fourni
    if sale_data.product_id:
        product = db.query(Product).filter(Product.product_id == sale_data.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Vérifier si une variante est spécifiée
        if sale_data.variant_id:
            variant = db.query(ProductVariant).filter(
                ProductVariant.variant_id == sale_data.variant_id,
                ProductVariant.product_id == sale_data.product_id,
                ProductVariant.is_sold == False
            ).first()
            if not variant:
                raise HTTPException(status_code=404, detail="Variante non trouvée ou déjà vendue")
        else:
            # Vérifier le stock disponible pour les produits sans variantes
            if product.quantity < sale_data.quantity:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Stock insuffisant. Disponible: {product.quantity}, Demandé: {sale_data.quantity}"
                )
    
    # Créer la vente
    db_sale = DailySale(
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
        notes=sale_data.notes
    )
    
    db.add(db_sale)
    
    # Mettre à jour le stock si un produit est spécifié
    if sale_data.product_id:
        if sale_data.variant_id:
            # Marquer la variante comme vendue (pas de décrément du stock principal)
            variant.is_sold = True
            # Créer un mouvement de stock pour la variante (quantité = 1 car c'est une variante unique)
            stock_movement = StockMovement(
                product_id=sale_data.product_id,
                quantity=-1,  # Une variante = 1 unité
                movement_type="OUT",
                reference_type="DAILY_SALE",
                reference_id=db_sale.sale_id,
                notes=f"Vente quotidienne - {sale_data.client_name} (Variante: {sale_data.variant_imei})",
                unit_price=sale_data.unit_price
            )
            db.add(stock_movement)
        else:
            # Mettre à jour la quantité en stock pour les produits sans variantes
            product.quantity -= sale_data.quantity
            
            # Créer un mouvement de stock
            stock_movement = StockMovement(
                product_id=sale_data.product_id,
                quantity=-sale_data.quantity,  # Négatif pour sortie
                movement_type="OUT",
                reference_type="DAILY_SALE",
                reference_id=db_sale.sale_id,
                notes=f"Vente quotidienne - {sale_data.client_name}",
                unit_price=sale_data.unit_price
            )
            db.add(stock_movement)
    
    db.commit()
    db.refresh(db_sale)
    
    return db_sale

@router.put("/{sale_id}", response_model=DailySaleResponse)
async def update_daily_sale(
    sale_id: int,
    sale_data: DailySaleUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Mettre à jour une vente quotidienne"""
    db_sale = db.query(DailySale).filter(DailySale.sale_id == sale_id).first()
    if not db_sale:
        raise HTTPException(status_code=404, detail="Vente non trouvée")
    
    # Vérifier si le client existe si client_id est fourni
    if sale_data.client_id:
        client = db.query(Client).filter(Client.client_id == sale_data.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client non trouvé")
    
    # Vérifier si le produit existe si product_id est fourni
    if sale_data.product_id:
        product = db.query(Product).filter(Product.product_id == sale_data.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
    
    # Sauvegarder les anciennes valeurs pour ajuster le stock
    old_quantity = db_sale.quantity
    old_product_id = db_sale.product_id
    
    # Mettre à jour les champs
    update_data = sale_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_sale, field, value)
    
    # Ajuster le stock si nécessaire
    if old_product_id and db_sale.product_id:
        if old_product_id == db_sale.product_id:
            # Même produit, ajuster la différence
            quantity_diff = db_sale.quantity - old_quantity
            if quantity_diff != 0:
                product = db.query(Product).filter(Product.product_id == db_sale.product_id).first()
                if product:
                    product.quantity -= quantity_diff
        else:
            # Produit différent, remettre l'ancien stock et déduire le nouveau
            old_product = db.query(Product).filter(Product.product_id == old_product_id).first()
            if old_product:
                old_product.quantity += old_quantity
            
            new_product = db.query(Product).filter(Product.product_id == db_sale.product_id).first()
            if new_product:
                new_product.quantity -= db_sale.quantity
    
    db.commit()
    db.refresh(db_sale)
    
    return db_sale

@router.delete("/{sale_id}")
async def delete_daily_sale(
    sale_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer une vente quotidienne"""
    db_sale = db.query(DailySale).filter(DailySale.sale_id == sale_id).first()
    if not db_sale:
        raise HTTPException(status_code=404, detail="Vente non trouvée")
    
    # Remettre le stock si un produit était associé
    if db_sale.product_id:
        if db_sale.variant_id:
            # Remettre la variante en stock (marquer comme non vendue)
            variant = db.query(ProductVariant).filter(ProductVariant.variant_id == db_sale.variant_id).first()
            if variant:
                variant.is_sold = False
        else:
            # Remettre le stock du produit principal
            product = db.query(Product).filter(Product.product_id == db_sale.product_id).first()
            if product:
                product.quantity += db_sale.quantity
    
    # Supprimer les mouvements de stock associés
    db.query(StockMovement).filter(
        and_(
            StockMovement.reference_type == "DAILY_SALE",
            StockMovement.reference_id == sale_id
        )
    ).delete()
    
    db.delete(db_sale)
    db.commit()
    
    return {"message": "Vente supprimée avec succès"}

@router.get("/stats/summary")
async def get_sales_summary(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir un résumé des ventes"""
    query = db.query(DailySale)
    
    if start_date:
        query = query.filter(DailySale.sale_date >= start_date)
    
    if end_date:
        query = query.filter(DailySale.sale_date <= end_date)
    
    # Exclure les ventes liées à des factures impayées pour les non-admins
    try:
        role = getattr(current_user, "role", "user")
    except Exception:
        role = "user"
    if role != "admin":
        query = query.outerjoin(Invoice, DailySale.invoice_id == Invoice.invoice_id)
        query = query.filter(
            or_(
                DailySale.invoice_id.is_(None),
                Invoice.status.in_(["payée", "PAID", "partiellement payée"])  # inclure partiellement payée
            )
        )

    # Statistiques générales
    # Compter chaque facture une seule fois (distinct invoice_id) + ventes directes
    direct_sales = query.filter(DailySale.invoice_id.is_(None)).count()
    invoice_sales_distinct = (
        db.query(func.count(func.distinct(DailySale.invoice_id)))
        .select_from(DailySale)
        .filter(DailySale.sale_id.in_([s.sale_id for s in query.all()]))
        .filter(DailySale.invoice_id.isnot(None))
        .scalar()
        or 0
    )
    total_sales = int(direct_sales) + int(invoice_sales_distinct)
    total_amount = db.query(func.sum(DailySale.total_amount)).filter(
        DailySale.sale_id.in_([s.sale_id for s in query.all()])
    ).scalar() or 0
    
    # Ventes par méthode de paiement
    payment_methods = db.query(
        DailySale.payment_method,
        func.count(DailySale.sale_id).label('count'),
        func.sum(DailySale.total_amount).label('total')
    ).filter(
        DailySale.sale_id.in_([s.sale_id for s in query.all()])
    ).group_by(DailySale.payment_method).all()
    
    # Ventes liées à des factures vs ventes directes
    invoice_sales = int(invoice_sales_distinct)
    
    # Restreindre 'vente moyenne' pour les non-admins
    try:
        role = getattr(current_user, "role", "user")
    except Exception:
        role = "user"
    avg_sale = float(total_amount / total_sales) if total_sales > 0 else 0
    if role != "admin":
        avg_sale = 0.0

    return {
        "total_sales": total_sales,
        "total_amount": float(total_amount),
        "average_sale": avg_sale,
        "payment_methods": [
            {
                "method": pm.payment_method,
                "count": pm.count,
                "total": float(pm.total or 0)
            }
            for pm in payment_methods
        ],
        "invoice_sales": invoice_sales,
        "direct_sales": direct_sales
    }

@router.get("/by-date/{sale_date}")
async def get_sales_by_date(
    sale_date: date,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Récupérer les ventes d'une date spécifique"""
    sales = db.query(DailySale).filter(DailySale.sale_date == sale_date).all()
    return sales
