from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc, func
from typing import Optional, List
from decimal import Decimal

from ..database import get_db, Supplier
from ..schemas import SupplierQuickCreate, SupplierResponse
from ..auth import get_current_user, User

router = APIRouter(
    prefix="/api/suppliers",
    tags=["suppliers"],
    dependencies=[Depends(get_current_user)]
)

@router.get("/", response_model=List[SupplierResponse])
async def get_suppliers(
    skip: int = Query(0, ge=0, description="Nombre d'éléments à ignorer"),
    limit: int = Query(100, ge=1, le=1000, description="Nombre d'éléments à récupérer"),
    search: Optional[str] = Query(None, description="Recherche par nom, téléphone ou email"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Récupérer la liste des fournisseurs avec pagination et recherche"""
    
    try:
        query = db.query(Supplier)
        
        # Appliquer les filtres de recherche
        if search and search.strip():
            search_term = f"%{search.strip().lower()}%"
            query = query.filter(
                or_(
                    func.lower(Supplier.name).like(search_term),
                    func.lower(Supplier.phone).like(search_term),
                    func.lower(Supplier.email).like(search_term)
                )
            )
        
        # Trier par nom
        query = query.order_by(Supplier.name)
        
        # Appliquer pagination
        suppliers = query.offset(skip).limit(limit).all()
        
        return suppliers
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération: {str(e)}")

@router.get("/{supplier_id}", response_model=SupplierResponse)
async def get_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Récupérer un fournisseur spécifique"""
    
    supplier = db.query(Supplier).filter(Supplier.supplier_id == supplier_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Fournisseur introuvable")
    
    return supplier

@router.post("/", response_model=SupplierResponse)
async def create_supplier(
    supplier_data: SupplierQuickCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Créer un nouveau fournisseur"""
    
    try:
        # Vérifier si un fournisseur avec ce nom existe déjà
        existing = db.query(Supplier).filter(
            func.lower(Supplier.name) == func.lower(supplier_data.name)
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=400, 
                detail=f"Un fournisseur avec le nom '{supplier_data.name}' existe déjà"
            )
        
        # Créer le nouveau fournisseur
        db_supplier = Supplier(
            name=supplier_data.name,
            phone=supplier_data.phone,
            email=supplier_data.email,
            address=supplier_data.address
        )
        
        db.add(db_supplier)
        db.commit()
        db.refresh(db_supplier)
        
        return db_supplier
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur lors de la création: {str(e)}")

@router.put("/{supplier_id}", response_model=SupplierResponse)
async def update_supplier(
    supplier_id: int,
    supplier_data: SupplierQuickCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Mettre à jour un fournisseur"""
    
    try:
        # Récupérer le fournisseur existant
        supplier = db.query(Supplier).filter(Supplier.supplier_id == supplier_id).first()
        if not supplier:
            raise HTTPException(status_code=404, detail="Fournisseur introuvable")
        
        # Vérifier les doublons de nom (sauf pour le fournisseur actuel)
        existing = db.query(Supplier).filter(
            Supplier.supplier_id != supplier_id,
            func.lower(Supplier.name) == func.lower(supplier_data.name)
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Un autre fournisseur avec le nom '{supplier_data.name}' existe déjà"
            )
        
        # Mettre à jour les champs
        supplier.name = supplier_data.name
        supplier.phone = supplier_data.phone
        supplier.email = supplier_data.email
        supplier.address = supplier_data.address
        
        db.commit()
        db.refresh(supplier)
        
        return supplier
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur lors de la mise à jour: {str(e)}")

@router.delete("/{supplier_id}")
async def delete_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Supprimer un fournisseur"""
    
    try:
        # Récupérer le fournisseur
        supplier = db.query(Supplier).filter(Supplier.supplier_id == supplier_id).first()
        if not supplier:
            raise HTTPException(status_code=404, detail="Fournisseur introuvable")
        
        # Vérifier s'il y a des factures liées
        from ..database import SupplierInvoice
        invoice_count = db.query(SupplierInvoice).filter(
            SupplierInvoice.supplier_id == supplier_id
        ).count()
        
        if invoice_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Impossible de supprimer: {invoice_count} facture(s) sont liées à ce fournisseur"
            )
        
        # Supprimer le fournisseur
        db.delete(supplier)
        db.commit()
        
        return {"message": "Fournisseur supprimé avec succès"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur lors de la suppression: {str(e)}")

@router.get("/search/suggestions")
async def get_supplier_suggestions(
    q: str = Query(..., min_length=2, description="Terme de recherche"),
    limit: int = Query(10, ge=1, le=50, description="Nombre de suggestions"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Récupérer des suggestions de fournisseurs pour l'autocomplétion"""
    
    try:
        search_term = f"%{q.strip().lower()}%"
        
        suppliers = db.query(Supplier).filter(
            or_(
                func.lower(Supplier.name).like(search_term),
                func.lower(Supplier.phone).like(search_term),
                func.lower(Supplier.email).like(search_term)
            )
        ).order_by(Supplier.name).limit(limit).all()
        
        return [
            {
                "id": supplier.supplier_id,
                "name": supplier.name,
                "phone": supplier.phone,
                "email": supplier.email
            }
            for supplier in suppliers
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la recherche: {str(e)}")
