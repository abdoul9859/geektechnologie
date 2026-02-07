from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
import os
import uuid
from pathlib import Path

from ..database import (
    Supplier,
    SupplierInvoice,
    SupplierInvoicePayment,
    BankTransaction,
    get_next_id,
)
from ..auth import get_current_user
from ..schemas import (
    SupplierInvoiceCreate,
    SupplierInvoiceResponse,
    SupplierInvoiceUpdate,
    SupplierInvoicePaymentCreate,
    SupplierInvoicePaymentResponse,
)

router = APIRouter(prefix="/api/supplier-invoices", tags=["supplier-invoices"])


# Helper: invalidate dashboard cache when financial figures change
def _invalidate_dashboard_cache():
    try:
        from . import dashboard as dashboard_router

        if hasattr(dashboard_router, "_cache") and isinstance(dashboard_router._cache, dict):
            dashboard_router._cache.clear()
    except Exception:
        pass


# Helper: save uploaded PDF file
def _save_pdf_file(file: UploadFile) -> tuple:
    """Sauvegarder un fichier PDF uploade et retourner le chemin et le nom de fichier"""
    upload_dir = Path("static/uploads/supplier_invoices")
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_extension = Path(file.filename).suffix if file.filename else ".pdf"
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = upload_dir / unique_filename

    with open(file_path, "wb") as buffer:
        content = file.file.read()
        buffer.write(content)

    relative_path = f"/static/uploads/supplier_invoices/{unique_filename}"
    return str(relative_path), file.filename or unique_filename


@router.get("/", response_model=dict)
async def get_supplier_invoices(
    skip: int = 0,
    limit: int = 50,
    search: Optional[str] = None,
    supplier_id: Optional[int] = None,
    status: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """Recuperer la liste des factures fournisseur"""
    try:
        filters: dict = {}

        if supplier_id:
            filters["supplier_id"] = supplier_id

        if status:
            filters["status"] = status

        # Mettre a jour les statuts bases sur les dates d'echeance
        today = date.today()
        overdue_invoices = await SupplierInvoice.find(
            {
                "due_date": {"$lt": datetime.combine(today, datetime.min.time())},
                "remaining_amount": {"$gt": 0},
                "status": {"$ne": "paid"},
            }
        ).to_list()

        for invoice in overdue_invoices:
            invoice.status = "overdue"
            await invoice.save()

        # Build search filter after overdue updates
        if search:
            pattern = {"$regex": search, "$options": "i"}
            # Search in invoice_number; we'll also try supplier names below
            filters["$or"] = [{"invoice_number": pattern}]

        total = await SupplierInvoice.find(filters).count()
        invoices = await SupplierInvoice.find(filters).skip(skip).limit(limit).to_list()

        # If there's a search term, also filter by supplier name
        if search and not invoices:
            # Try matching supplier name
            matching_suppliers = await Supplier.find(
                {"name": {"$regex": search, "$options": "i"}}
            ).to_list()
            if matching_suppliers:
                sup_ids = [s.supplier_id for s in matching_suppliers]
                alt_filters = {"supplier_id": {"$in": sup_ids}}
                if supplier_id:
                    alt_filters["supplier_id"] = supplier_id
                if status:
                    alt_filters["status"] = status
                total = await SupplierInvoice.find(alt_filters).count()
                invoices = await SupplierInvoice.find(alt_filters).skip(skip).limit(limit).to_list()

        # If search has $or, also add supplier name search
        if search and "$or" in filters:
            matching_suppliers = await Supplier.find(
                {"name": {"$regex": search, "$options": "i"}}
            ).to_list()
            if matching_suppliers:
                sup_ids = [s.supplier_id for s in matching_suppliers]
                filters["$or"].append({"supplier_id": {"$in": sup_ids}})
                total = await SupplierInvoice.find(filters).count()
                invoices = await SupplierInvoice.find(filters).skip(skip).limit(limit).to_list()

        # Enrichir avec les donnees des fournisseurs
        result_invoices = []
        for invoice in invoices:
            supplier = await Supplier.find_one(Supplier.supplier_id == invoice.supplier_id)
            invoice_dict = {
                "invoice_id": invoice.invoice_id,
                "supplier_id": invoice.supplier_id,
                "supplier_name": supplier.name if supplier else "Fournisseur supprime",
                "invoice_number": invoice.invoice_number,
                "invoice_date": invoice.invoice_date,
                "due_date": invoice.due_date,
                "description": invoice.description,
                "amount": float(invoice.amount or 0),
                "paid_amount": float(invoice.paid_amount),
                "remaining_amount": float(invoice.remaining_amount),
                "status": invoice.status,
                "payment_method": invoice.payment_method,
                "notes": invoice.notes,
                "pdf_path": invoice.pdf_path,
                "pdf_filename": invoice.pdf_filename,
                "created_at": invoice.created_at,
            }
            result_invoices.append(invoice_dict)

        return {
            "invoices": result_invoices,
            "total": total,
            "page": (skip // limit) + 1,
            "pages": (total + limit - 1) // limit if total > 0 else 1,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{invoice_id}", response_model=SupplierInvoiceResponse)
async def get_supplier_invoice(
    invoice_id: int,
    current_user=Depends(get_current_user),
):
    """Recuperer une facture fournisseur par ID"""
    invoice = await SupplierInvoice.find_one(SupplierInvoice.invoice_id == invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Facture non trouvee")

    supplier = await Supplier.find_one(Supplier.supplier_id == invoice.supplier_id)

    return SupplierInvoiceResponse(
        invoice_id=invoice.invoice_id,
        supplier_id=invoice.supplier_id,
        supplier_name=supplier.name if supplier else "Fournisseur supprime",
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
        created_at=invoice.created_at,
    )


@router.post("/", response_model=SupplierInvoiceResponse)
async def create_supplier_invoice(
    supplier_id: int = Form(...),
    pdf_file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """Creer une nouvelle facture fournisseur - Version simplifiee avec seulement PDF + fournisseur"""
    try:
        supplier = await Supplier.find_one(Supplier.supplier_id == supplier_id)
        if not supplier:
            raise HTTPException(status_code=404, detail="Fournisseur non trouve")

        if pdf_file.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptes")

        pdf_path, pdf_filename = _save_pdf_file(pdf_file)

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        auto_invoice_number = f"FAC-{timestamp}"

        current_time = datetime.now()
        new_id = await get_next_id("supplier_invoices")
        invoice = SupplierInvoice(
            invoice_id=new_id,
            supplier_id=supplier_id,
            invoice_number=auto_invoice_number,
            invoice_date=current_time,
            due_date=None,
            description="Facture PDF - Details a completer",
            amount=Decimal("0"),
            paid_amount=Decimal("0"),
            remaining_amount=Decimal("0"),
            status="pending",
            payment_method=None,
            notes=None,
            pdf_path=pdf_path,
            pdf_filename=pdf_filename,
        )

        await invoice.insert()

        _invalidate_dashboard_cache()

        return await get_supplier_invoice(invoice.invoice_id, current_user)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{invoice_id}", response_model=SupplierInvoiceResponse)
async def update_supplier_invoice(
    invoice_id: int,
    invoice_data: SupplierInvoiceUpdate,
    current_user=Depends(get_current_user),
):
    """Mettre a jour une facture fournisseur"""
    try:
        invoice = await SupplierInvoice.find_one(SupplierInvoice.invoice_id == invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvee")

        for field, value in invoice_data.dict(exclude_unset=True).items():
            if hasattr(invoice, field):
                setattr(invoice, field, value)

        if invoice_data.amount is not None:
            invoice.remaining_amount = invoice.amount - invoice.paid_amount

        if invoice.remaining_amount <= 0:
            invoice.status = "paid"
        elif invoice.paid_amount > 0:
            invoice.status = "partial"
        elif invoice.due_date and invoice.due_date < datetime.now():
            invoice.status = "overdue"
        else:
            invoice.status = "pending"

        await invoice.save()

        _invalidate_dashboard_cache()

        return await get_supplier_invoice(invoice_id, current_user)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{invoice_id}")
async def delete_supplier_invoice(
    invoice_id: int,
    current_user=Depends(get_current_user),
):
    """Supprimer une facture fournisseur"""
    try:
        invoice = await SupplierInvoice.find_one(SupplierInvoice.invoice_id == invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvee")

        message = "Facture fournisseur supprimee avec succes"

        if invoice.paid_amount > 0:
            bt_id = await get_next_id("bank_transactions")
            refund_transaction = BankTransaction(
                transaction_id=bt_id,
                type="entry",
                motif="Annulation paiement fournisseur",
                description=f"Retablissement suite a suppression facture {invoice.invoice_number}",
                amount=invoice.paid_amount,
                date=date.today(),
                method="virement",
                reference=f"REFUND-{invoice.invoice_number}",
            )
            await refund_transaction.insert()

            await SupplierInvoicePayment.find(
                SupplierInvoicePayment.supplier_invoice_id == invoice_id
            ).delete()

            message = "Facture fournisseur supprimee avec succes, montant paye retabli dans le chiffre d'affaires"

        await invoice.delete()

        _invalidate_dashboard_cache()

        return {"message": message}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{invoice_id}/payments", response_model=SupplierInvoicePaymentResponse)
async def create_payment(
    invoice_id: int,
    payment_data: SupplierInvoicePaymentCreate,
    current_user=Depends(get_current_user),
):
    """Ajouter un paiement a une facture fournisseur"""
    try:
        invoice = await SupplierInvoice.find_one(SupplierInvoice.invoice_id == invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvee")

        amount_int = Decimal(str(payment_data.amount)).quantize(Decimal("1"))
        if amount_int <= 0:
            raise HTTPException(status_code=400, detail="Le montant doit etre positif")

        remaining_int = Decimal(str(invoice.remaining_amount or 0)).quantize(Decimal("1"))
        if amount_int > remaining_int:
            raise HTTPException(status_code=400, detail="Le montant depasse le solde restant")

        pay_id = await get_next_id("supplier_invoice_payments")
        payment = SupplierInvoicePayment(
            payment_id=pay_id,
            supplier_invoice_id=invoice_id,
            amount=amount_int,
            payment_date=payment_data.payment_date,
            payment_method=payment_data.payment_method,
            reference=payment_data.reference,
            notes=payment_data.notes,
        )
        await payment.insert()

        invoice.paid_amount += amount_int
        invoice.remaining_amount = invoice.amount - invoice.paid_amount

        if invoice.remaining_amount <= 0:
            invoice.status = "paid"
        else:
            invoice.status = "partial"

        # Get supplier name for bank transaction description
        supplier = await Supplier.find_one(Supplier.supplier_id == invoice.supplier_id)
        supplier_name = supplier.name if supplier else "Fournisseur"

        bt_id = await get_next_id("bank_transactions")
        bank_transaction = BankTransaction(
            transaction_id=bt_id,
            type="exit",
            motif="Paiement fournisseur",
            description=f"Paiement facture {invoice.invoice_number} - {supplier_name}",
            amount=amount_int,
            date=payment_data.payment_date.date() if hasattr(payment_data.payment_date, 'date') else payment_data.payment_date,
            method="virement"
            if payment_data.payment_method in ["virement", "virement bancaire"]
            else "cheque",
            reference=payment_data.reference or f"PAY-{invoice.invoice_number}",
        )
        await bank_transaction.insert()

        await invoice.save()

        _invalidate_dashboard_cache()

        return SupplierInvoicePaymentResponse(
            payment_id=payment.payment_id,
            supplier_invoice_id=payment.supplier_invoice_id,
            amount=payment.amount,
            payment_date=payment.payment_date,
            payment_method=payment.payment_method,
            reference=payment.reference,
            notes=payment.notes,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{invoice_id}/payments", response_model=List[SupplierInvoicePaymentResponse])
async def get_payments(
    invoice_id: int,
    current_user=Depends(get_current_user),
):
    """Recuperer les paiements d'une facture fournisseur"""
    payments = await SupplierInvoicePayment.find(
        SupplierInvoicePayment.supplier_invoice_id == invoice_id
    ).to_list()

    return [
        SupplierInvoicePaymentResponse(
            payment_id=payment.payment_id,
            supplier_invoice_id=payment.supplier_invoice_id,
            amount=payment.amount,
            payment_date=payment.payment_date,
            payment_method=payment.payment_method,
            reference=payment.reference,
            notes=payment.notes,
        )
        for payment in payments
    ]


@router.get("/stats/summary")
async def get_summary_stats(
    current_user=Depends(get_current_user),
):
    """Recuperer les statistiques des factures fournisseur"""
    try:
        total_invoices = await SupplierInvoice.count()
        pending_invoices = await SupplierInvoice.find(
            SupplierInvoice.status == "pending"
        ).count()
        overdue_invoices = await SupplierInvoice.find(
            SupplierInvoice.status == "overdue"
        ).count()

        pipeline = [
            {
                "$group": {
                    "_id": None,
                    "total_amount": {"$sum": "$amount"},
                    "paid_amount": {"$sum": "$paid_amount"},
                }
            }
        ]
        agg_result = await SupplierInvoice.aggregate(pipeline).to_list()

        total_amount = float(agg_result[0]["total_amount"]) if agg_result else 0
        paid_amount = float(agg_result[0]["paid_amount"]) if agg_result else 0
        remaining_amount = total_amount - paid_amount

        return {
            "total_invoices": total_invoices,
            "pending_invoices": pending_invoices,
            "overdue_invoices": overdue_invoices,
            "total_amount": total_amount,
            "paid_amount": paid_amount,
            "remaining_amount": remaining_amount,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pdf/{invoice_id}")
async def get_invoice_pdf(
    invoice_id: int,
    current_user=Depends(get_current_user),
):
    """Recuperer le PDF d'une facture fournisseur"""
    invoice = await SupplierInvoice.find_one(SupplierInvoice.invoice_id == invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Facture non trouvee")

    if not invoice.pdf_path:
        raise HTTPException(status_code=404, detail="Aucun PDF associe a cette facture")

    pdf_path = invoice.pdf_path.lstrip("/")
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable")

    return FileResponse(
        path=pdf_path,
        filename=invoice.pdf_filename or f"facture_{invoice.invoice_number}.pdf",
        media_type="application/pdf",
    )
