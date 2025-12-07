from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
import os
import uuid
from pathlib import Path

from ..database import (
    get_db, User, Supplier, 
    SupplierInvoice, SupplierInvoicePayment,
    BankTransaction
)
from ..auth import get_current_user
from ..schemas import (
    SupplierInvoiceCreate, SupplierInvoiceResponse, SupplierInvoiceUpdate,
    SupplierInvoicePaymentCreate, SupplierInvoicePaymentResponse
)

router = APIRouter(prefix="/api/supplier-invoices", tags=["supplier-invoices"])

# Helper: invalidate dashboard cache when financial figures change
def _invalidate_dashboard_cache():
    try:
        from . import dashboard as dashboard_router  # local import to avoid potential circular import at module load
        if hasattr(dashboard_router, "_cache") and isinstance(dashboard_router._cache, dict):
            dashboard_router._cache.clear()
    except Exception:
        # Non-blocking; dashboard will eventually refresh by TTL
        pass

# Helper: save uploaded PDF file
def _save_pdf_file(file: UploadFile) -> tuple[str, str]:
    """Sauvegarder un fichier PDF uploadé et retourner le chemin et le nom de fichier"""
    # Créer le dossier de destination s'il n'existe pas
    upload_dir = Path("static/uploads/supplier_invoices")
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Générer un nom de fichier unique
    file_extension = Path(file.filename).suffix if file.filename else ".pdf"
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = upload_dir / unique_filename
    
    # Sauvegarder le fichier
    with open(file_path, "wb") as buffer:
        content = file.file.read()
        buffer.write(content)
    
    # Retourner le chemin relatif et le nom original
    relative_path = f"/static/uploads/supplier_invoices/{unique_filename}"
    return str(relative_path), file.filename or unique_filename

@router.get("/", response_model=dict)
async def get_supplier_invoices(
    skip: int = 0,
    limit: int = 50,
    search: Optional[str] = None,
    supplier_id: Optional[int] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer la liste des factures fournisseur"""
    try:
        query = db.query(SupplierInvoice).join(Supplier, Supplier.supplier_id == SupplierInvoice.supplier_id, isouter=True)
        
        if search:
            search_term = f"%{search.lower()}%"
            query = query.filter(
                func.lower(SupplierInvoice.invoice_number).like(search_term) |
                func.lower(Supplier.name).like(search_term)
            )
        
        if supplier_id:
            query = query.filter(SupplierInvoice.supplier_id == supplier_id)
            
        if status:
            query = query.filter(SupplierInvoice.status == status)
        
        # Mettre à jour les statuts basés sur les dates d'échéance
        today = date.today()
        overdue_invoices = query.filter(
            SupplierInvoice.due_date < datetime.combine(today, datetime.min.time()),
            SupplierInvoice.remaining_amount > 0,
            SupplierInvoice.status != "paid"
        ).all()
        
        for invoice in overdue_invoices:
            invoice.status = "overdue"
        
        db.commit()
        
        total = query.count()
        invoices = query.offset(skip).limit(limit).all()
        
        # Enrichir avec les données des fournisseurs
        result_invoices = []
        for invoice in invoices:
            supplier = db.query(Supplier).filter(Supplier.supplier_id == invoice.supplier_id).first()
            invoice_dict = {
                "invoice_id": invoice.invoice_id,
                "supplier_id": invoice.supplier_id,
                "supplier_name": supplier.name if supplier else "Fournisseur supprimé",
                "invoice_number": invoice.invoice_number,
                "invoice_date": invoice.invoice_date,
                "due_date": invoice.due_date,
                "description": invoice.description,  # Nouvelle structure simple
                "amount": float(invoice.amount),      # Montant total direct
                "paid_amount": float(invoice.paid_amount),
                "remaining_amount": float(invoice.remaining_amount),
                "status": invoice.status,
                "payment_method": invoice.payment_method,
                "notes": invoice.notes,
                "pdf_path": invoice.pdf_path,
                "pdf_filename": invoice.pdf_filename,
                "created_at": invoice.created_at
            }
            result_invoices.append(invoice_dict)
        
        return {
            "invoices": result_invoices,
            "total": total,
            "page": (skip // limit) + 1,
            "pages": (total + limit - 1) // limit if total > 0 else 1
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{invoice_id}", response_model=SupplierInvoiceResponse)
async def get_supplier_invoice(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer une facture fournisseur par ID"""
    invoice = db.query(SupplierInvoice).filter(SupplierInvoice.invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Facture non trouvée")
    
    # Enrichir avec les données du fournisseur
    supplier = db.query(Supplier).filter(Supplier.supplier_id == invoice.supplier_id).first()
    
    return SupplierInvoiceResponse(
        invoice_id=invoice.invoice_id,
        supplier_id=invoice.supplier_id,
        supplier_name=supplier.name if supplier else "Fournisseur supprimé",
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        description=invoice.description,
        amount=invoice.amount,
        paid_amount=invoice.paid_amount,
        remaining_amount=invoice.remaining_amount,
        status=invoice.status,
        payment_method=invoice.payment_method,
        notes=invoice.notes,
        pdf_path=invoice.pdf_path,
        pdf_filename=invoice.pdf_filename,
        created_at=invoice.created_at
    )

@router.post("/", response_model=SupplierInvoiceResponse)
async def create_supplier_invoice(
    supplier_id: int = Form(...),
    pdf_file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Créer une nouvelle facture fournisseur - Version simplifiée avec seulement PDF + fournisseur"""
    try:
        # Vérifier que le fournisseur existe
        supplier = db.query(Supplier).filter(Supplier.supplier_id == supplier_id).first()
        if not supplier:
            raise HTTPException(status_code=404, detail="Fournisseur non trouvé")
        
        # Vérifier le type de fichier PDF
        if pdf_file.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés")
        
        # Sauvegarder le fichier PDF
        pdf_path, pdf_filename = _save_pdf_file(pdf_file)
        
        # Générer un numéro de facture automatique basé sur le timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        auto_invoice_number = f"FAC-{timestamp}"
        
        # Créer la facture avec des valeurs par défaut pour les champs obligatoires
        current_time = datetime.now()
        invoice = SupplierInvoice(
            supplier_id=supplier_id,
            invoice_number=auto_invoice_number,
            invoice_date=current_time,  # Date actuelle par défaut
            due_date=None,
            description="Facture PDF - Détails à compléter",  # Description par défaut
            amount=0.0,  # Montant par défaut à 0
            paid_amount=0.0,
            remaining_amount=0.0,
            status="pending",
            payment_method=None,
            notes=None,
            pdf_path=pdf_path,
            pdf_filename=pdf_filename
        )
        
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        # Dashboard KPIs depend on supplier invoices; clear cache
        _invalidate_dashboard_cache()
        
        return await get_supplier_invoice(invoice.invoice_id, current_user, db)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{invoice_id}", response_model=SupplierInvoiceResponse)
async def update_supplier_invoice(
    invoice_id: int,
    invoice_data: SupplierInvoiceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mettre à jour une facture fournisseur"""
    try:
        invoice = db.query(SupplierInvoice).filter(SupplierInvoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        # Mettre à jour les champs modifiables
        for field, value in invoice_data.dict(exclude_unset=True).items():
            if hasattr(invoice, field):
                setattr(invoice, field, value)
        
        # Recalculer le remaining_amount si nécessaire
        if invoice_data.amount is not None:
            invoice.remaining_amount = invoice.amount - invoice.paid_amount
        
        # Mettre à jour le statut automatiquement
        if invoice.remaining_amount <= 0:
            invoice.status = "paid"
        elif invoice.paid_amount > 0:
            invoice.status = "partial"
        elif invoice.due_date and invoice.due_date < datetime.now():
            invoice.status = "overdue"
        else:
            invoice.status = "pending"
        
        db.commit()
        db.refresh(invoice)

        # Dashboard KPIs depend on supplier invoices; clear cache
        _invalidate_dashboard_cache()
        
        return await get_supplier_invoice(invoice_id, current_user, db)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{invoice_id}")
async def delete_supplier_invoice(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Supprimer une facture fournisseur"""
    try:
        invoice = db.query(SupplierInvoice).filter(SupplierInvoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        # Si la facture a des paiements, on doit rétablir le chiffre d'affaires
        if invoice.paid_amount > 0:
            # Créer une transaction bancaire d'entrée pour rétablir le chiffre d'affaires
            refund_transaction = BankTransaction(
                type="entry",
                motif="Annulation paiement fournisseur",
                description=f"Rétablissement suite à suppression facture {invoice.invoice_number}",
                amount=invoice.paid_amount,
                date=date.today(),
                method="virement",
                reference=f"REFUND-{invoice.invoice_number}"
            )
            db.add(refund_transaction)
            
            # Supprimer tous les paiements associés
            db.query(SupplierInvoicePayment).filter(
                SupplierInvoicePayment.supplier_invoice_id == invoice_id
            ).delete()
        
        # Supprimer la facture
        db.delete(invoice)
        db.commit()

        # Dashboard KPIs depend on supplier invoices; clear cache so monthly revenue updates immediately
        _invalidate_dashboard_cache()
        
        return {"message": "Facture fournisseur supprimée avec succès, montant payé rétabli dans le chiffre d'affaires" if invoice.paid_amount > 0 else "Facture fournisseur supprimée avec succès"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{invoice_id}/payments", response_model=SupplierInvoicePaymentResponse)
async def create_payment(
    invoice_id: int,
    payment_data: SupplierInvoicePaymentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ajouter un paiement à une facture fournisseur"""
    try:
        invoice = db.query(SupplierInvoice).filter(SupplierInvoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        # Forcer un montant entier
        from decimal import Decimal
        amount_int = Decimal(str(payment_data.amount)).quantize(Decimal('1'))
        if amount_int <= 0:
            raise HTTPException(status_code=400, detail="Le montant doit être positif")
        
        remaining_int = Decimal(str(invoice.remaining_amount or 0)).quantize(Decimal('1'))
        if amount_int > remaining_int:
            raise HTTPException(status_code=400, detail="Le montant dépasse le solde restant")
        
        # Créer le paiement
        payment = SupplierInvoicePayment(
            supplier_invoice_id=invoice_id,
            amount=amount_int,
            payment_date=payment_data.payment_date,
            payment_method=payment_data.payment_method,
            reference=payment_data.reference,
            notes=payment_data.notes
        )
        db.add(payment)
        
        # Mettre à jour la facture
        invoice.paid_amount += amount_int
        invoice.remaining_amount = invoice.amount - invoice.paid_amount
        
        # Mettre à jour le statut
        if invoice.remaining_amount <= 0:
            invoice.status = "paid"
        else:
            invoice.status = "partial"
        
        # Créer une transaction bancaire de sortie (paiement fournisseur)
        bank_transaction = BankTransaction(
            type="exit",
            motif="Paiement fournisseur",
            description=f"Paiement facture {invoice.invoice_number} - {invoice.supplier.name if invoice.supplier else 'Fournisseur'}",
            amount=amount_int,
            date=payment_data.payment_date.date(),
            method="virement" if payment_data.payment_method in ["virement", "virement bancaire"] else "cheque",
            reference=payment_data.reference or f"PAY-{invoice.invoice_number}"
        )
        db.add(bank_transaction)
        
        db.commit()
        db.refresh(payment)

        # Dashboard KPIs depend on supplier invoices; clear cache
        _invalidate_dashboard_cache()
        
        return SupplierInvoicePaymentResponse(
            payment_id=payment.payment_id,
            supplier_invoice_id=payment.supplier_invoice_id,
            amount=payment.amount,
            payment_date=payment.payment_date,
            payment_method=payment.payment_method,
            reference=payment.reference,
            notes=payment.notes
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{invoice_id}/payments", response_model=List[SupplierInvoicePaymentResponse])
async def get_payments(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer les paiements d'une facture fournisseur"""
    payments = db.query(SupplierInvoicePayment).filter(SupplierInvoicePayment.supplier_invoice_id == invoice_id).all()
    
    return [
        SupplierInvoicePaymentResponse(
            payment_id=payment.payment_id,
            supplier_invoice_id=payment.supplier_invoice_id,
            amount=payment.amount,
            payment_date=payment.payment_date,
            payment_method=payment.payment_method,
            reference=payment.reference,
            notes=payment.notes
        )
        for payment in payments
    ]

@router.get("/stats/summary")
async def get_summary_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer les statistiques des factures fournisseur"""
    try:
        total_invoices = db.query(SupplierInvoice).count()
        pending_invoices = db.query(SupplierInvoice).filter(SupplierInvoice.status == "pending").count()
        overdue_invoices = db.query(SupplierInvoice).filter(SupplierInvoice.status == "overdue").count()
        
        total_amount = db.query(func.sum(SupplierInvoice.amount)).scalar() or 0
        paid_amount = db.query(func.sum(SupplierInvoice.paid_amount)).scalar() or 0
        remaining_amount = total_amount - paid_amount
        
        return {
            "total_invoices": total_invoices,
            "pending_invoices": pending_invoices,
            "overdue_invoices": overdue_invoices,
            "total_amount": float(total_amount),
            "paid_amount": float(paid_amount),
            "remaining_amount": float(remaining_amount)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/pdf/{invoice_id}")
async def get_invoice_pdf(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer le PDF d'une facture fournisseur"""
    invoice = db.query(SupplierInvoice).filter(SupplierInvoice.invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Facture non trouvée")
    
    if not invoice.pdf_path:
        raise HTTPException(status_code=404, detail="Aucun PDF associé à cette facture")
    
    # Convertir le chemin relatif en chemin absolu
    pdf_path = invoice.pdf_path.lstrip("/")
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable")
    
    return FileResponse(
        path=pdf_path,
        filename=invoice.pdf_filename or f"facture_{invoice.invoice_number}.pdf",
        media_type="application/pdf"
    )
