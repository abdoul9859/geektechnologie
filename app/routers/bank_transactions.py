from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import date

from ..database import get_db, User, BankTransaction
from ..auth import get_current_user
from ..schemas import BankTransactionCreate, BankTransactionResponse

router = APIRouter(prefix="/api/bank-transactions", tags=["bank_transactions"])

# Sécurité: s'assurer que la table existe (utile si l'ordre d'import diffère)
def _ensure_table_exists(db: Session):
    try:
        bind = db.get_bind()
        BankTransaction.__table__.create(bind=bind, checkfirst=True)
    except Exception:
        # Ne pas bloquer si un souci de concurrence survient
        pass

def _ensure_reference_column(db: Session):
    """Ajoute la colonne 'reference' si elle n'existe pas (utile après mise à jour)."""
    try:
        bind = db.get_bind()
        dialect = bind.dialect.name
        if dialect == 'sqlite':
            # Vérifier via PRAGMA
            res = db.execute(text("PRAGMA table_info(bank_transactions)"))
            cols = [row[1] for row in res]
            if 'reference' not in cols:
                db.execute(text("ALTER TABLE bank_transactions ADD COLUMN reference VARCHAR(255)"))
                db.commit()
        else:
            # Tentative générique, ignorée si déjà existante
            try:
                db.execute(text("ALTER TABLE bank_transactions ADD COLUMN reference VARCHAR(255)"))
                db.commit()
            except Exception:
                db.rollback()
    except Exception:
        # Ne pas bloquer l'exécution
        pass

@router.get("/", response_model=dict)
async def get_transactions(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    type: Optional[str] = None,  # 'entry' | 'exit'
    method: Optional[str] = None,  # 'virement' | 'cheque'
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer la liste des transactions bancaires (DB)."""
    try:
        _ensure_table_exists(db)
        _ensure_reference_column(db)
        q = db.query(BankTransaction)

        if search:
            s = f"%{search.lower()}%"
            # filtrer sur motif et description (case-insensitive)
            from sqlalchemy import or_, func as sa_func
            q = q.filter(or_(sa_func.lower(BankTransaction.motif).like(s), sa_func.lower(BankTransaction.description).like(s)))

        if type:
            q = q.filter(BankTransaction.type == type)

        if method:
            q = q.filter(BankTransaction.method == method)

        if start_date:
            q = q.filter(BankTransaction.date >= start_date)
        if end_date:
            q = q.filter(BankTransaction.date <= end_date)

        total = q.count()
        items = q.order_by(BankTransaction.date.desc(), BankTransaction.id.desc()).offset(skip).limit(limit).all()

        return {
            "transactions": [
                BankTransactionResponse(
                    id=item.id,
                    type=item.type,
                    motif=item.motif,
                    description=item.description,
                    amount=item.amount,
                    date=item.date,
                    method=item.method,
                    reference=item.reference,
                ) for item in items
            ],
            "total": total,
            "page": (skip // limit) + 1,
            "pages": (total + limit - 1) // limit,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats/summary")
async def get_transactions_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Statistiques sur les transactions (DB)."""
    try:
        _ensure_table_exists(db)
        _ensure_reference_column(db)
        from sqlalchemy import func as sa_func
        total_entries = db.query(sa_func.coalesce(sa_func.sum(BankTransaction.amount), 0)).filter(BankTransaction.type == "entry").scalar() or 0
        total_exits = db.query(sa_func.coalesce(sa_func.sum(BankTransaction.amount), 0)).filter(BankTransaction.type == "exit").scalar() or 0
        current_balance = total_entries - total_exits

        tx_count = db.query(sa_func.count(BankTransaction.id)).scalar() or 0

        return {
            "current_balance": current_balance,
            "total_entries": total_entries,
            "total_exits": total_exits,
            "transactions_count": tx_count,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/", response_model=BankTransactionResponse)
async def create_transaction(
    transaction_data: BankTransactionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Créer une transaction (persistée en DB)."""
    try:
        _ensure_table_exists(db)
        _ensure_reference_column(db)
        payload = transaction_data.model_dict() if hasattr(transaction_data, 'model_dict') else transaction_data.dict()
        tx = BankTransaction(**payload)
        db.add(tx)
        db.commit()
        db.refresh(tx)
        return BankTransactionResponse(
            id=tx.id,
            type=tx.type,
            motif=tx.motif,
            description=tx.description,
            amount=tx.amount,
            date=tx.date,
            method=tx.method,
            reference=tx.reference,
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{transaction_id}", response_model=BankTransactionResponse)
async def update_transaction(
    transaction_id: int,
    transaction_data: BankTransactionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mettre à jour une transaction existante (tous les champs)."""
    try:
        _ensure_table_exists(db)
        _ensure_reference_column(db)
        tx: BankTransaction | None = db.query(BankTransaction).filter(BankTransaction.id == transaction_id).first()
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction non trouvée")

        payload = transaction_data.model_dict() if hasattr(transaction_data, 'model_dict') else transaction_data.dict()
        for k, v in payload.items():
            setattr(tx, k, v)
        db.commit()
        db.refresh(tx)
        return BankTransactionResponse(
            id=tx.id,
            type=tx.type,
            motif=tx.motif,
            description=tx.description,
            amount=tx.amount,
            date=tx.date,
            method=tx.method,
            reference=tx.reference,
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{transaction_id}")
async def delete_transaction(
    transaction_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Supprimer une transaction."""
    try:
        _ensure_table_exists(db)
        tx = db.query(BankTransaction).filter(BankTransaction.id == transaction_id).first()
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction non trouvée")
        db.delete(tx)
        db.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
