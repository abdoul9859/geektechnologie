from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional, List

from ..database import Supplier, SupplierInvoice, get_next_id
from ..schemas import SupplierQuickCreate, SupplierResponse
from ..auth import get_current_user
from ..database import User

router = APIRouter(
    prefix="/api/suppliers",
    tags=["suppliers"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/", response_model=List[SupplierResponse])
async def get_suppliers(
    skip: int = Query(0, ge=0, description="Nombre d'elements a ignorer"),
    limit: int = Query(100, ge=1, le=1000, description="Nombre d'elements a recuperer"),
    search: Optional[str] = Query(None, description="Recherche par nom, telephone ou email"),
    current_user: User = Depends(get_current_user),
):
    """Recuperer la liste des fournisseurs avec pagination et recherche"""
    try:
        filters = {}

        if search and search.strip():
            pattern = {"$regex": search.strip(), "$options": "i"}
            filters["$or"] = [
                {"name": pattern},
                {"phone": pattern},
                {"email": pattern},
            ]

        suppliers = (
            await Supplier.find(filters)
            .sort("+name")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

        return suppliers

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la recuperation: {str(e)}")


@router.get("/{supplier_id}", response_model=SupplierResponse)
async def get_supplier(
    supplier_id: int,
    current_user: User = Depends(get_current_user),
):
    """Recuperer un fournisseur specifique"""
    supplier = await Supplier.find_one(Supplier.supplier_id == supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Fournisseur introuvable")

    return supplier


@router.post("/", response_model=SupplierResponse)
async def create_supplier(
    supplier_data: SupplierQuickCreate,
    current_user: User = Depends(get_current_user),
):
    """Creer un nouveau fournisseur"""
    try:
        # Verifier si un fournisseur avec ce nom existe deja (case-insensitive)
        existing = await Supplier.find_one(
            {"name": {"$regex": f"^{supplier_data.name}$", "$options": "i"}}
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Un fournisseur avec le nom '{supplier_data.name}' existe deja",
            )

        new_id = await get_next_id("suppliers")
        db_supplier = Supplier(
            supplier_id=new_id,
            name=supplier_data.name,
            phone=supplier_data.phone,
            email=supplier_data.email,
            address=supplier_data.address,
        )

        await db_supplier.insert()

        return db_supplier

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la creation: {str(e)}")


@router.put("/{supplier_id}", response_model=SupplierResponse)
async def update_supplier(
    supplier_id: int,
    supplier_data: SupplierQuickCreate,
    current_user: User = Depends(get_current_user),
):
    """Mettre a jour un fournisseur"""
    try:
        supplier = await Supplier.find_one(Supplier.supplier_id == supplier_id)
        if not supplier:
            raise HTTPException(status_code=404, detail="Fournisseur introuvable")

        # Verifier les doublons de nom (sauf pour le fournisseur actuel)
        existing = await Supplier.find_one(
            {
                "supplier_id": {"$ne": supplier_id},
                "name": {"$regex": f"^{supplier_data.name}$", "$options": "i"},
            }
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Un autre fournisseur avec le nom '{supplier_data.name}' existe deja",
            )

        supplier.name = supplier_data.name
        supplier.phone = supplier_data.phone
        supplier.email = supplier_data.email
        supplier.address = supplier_data.address

        await supplier.save()

        return supplier

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la mise a jour: {str(e)}")


@router.delete("/{supplier_id}")
async def delete_supplier(
    supplier_id: int,
    current_user: User = Depends(get_current_user),
):
    """Supprimer un fournisseur"""
    try:
        supplier = await Supplier.find_one(Supplier.supplier_id == supplier_id)
        if not supplier:
            raise HTTPException(status_code=404, detail="Fournisseur introuvable")

        # Verifier s'il y a des factures liees
        invoice_count = await SupplierInvoice.find(
            SupplierInvoice.supplier_id == supplier_id
        ).count()

        if invoice_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Impossible de supprimer: {invoice_count} facture(s) sont liees a ce fournisseur",
            )

        await supplier.delete()

        return {"message": "Fournisseur supprime avec succes"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la suppression: {str(e)}")


@router.get("/search/suggestions")
async def get_supplier_suggestions(
    q: str = Query(..., min_length=2, description="Terme de recherche"),
    limit: int = Query(10, ge=1, le=50, description="Nombre de suggestions"),
    current_user: User = Depends(get_current_user),
):
    """Recuperer des suggestions de fournisseurs pour l'autocompletion"""
    try:
        pattern = {"$regex": q.strip(), "$options": "i"}

        suppliers = (
            await Supplier.find(
                {"$or": [{"name": pattern}, {"phone": pattern}, {"email": pattern}]}
            )
            .sort("+name")
            .limit(limit)
            .to_list()
        )

        return [
            {
                "id": supplier.supplier_id,
                "name": supplier.name,
                "phone": supplier.phone,
                "email": supplier.email,
            }
            for supplier in suppliers
        ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la recherche: {str(e)}")
