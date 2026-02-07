from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import date

from ..database import BankTransaction, get_next_id
from ..auth import get_current_user
from ..schemas import BankTransactionCreate, BankTransactionResponse

router = APIRouter(prefix="/api/bank-transactions", tags=["bank_transactions"])


@router.get("/", response_model=dict)
async def get_transactions(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    type: Optional[str] = None,  # 'entry' | 'exit'
    method: Optional[str] = None,  # 'virement' | 'cheque'
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user=Depends(get_current_user),
):
    """Recuperer la liste des transactions bancaires (DB)."""
    try:
        filters = {}

        if search:
            # Build an $or filter for motif and description
            pattern = {"$regex": search, "$options": "i"}
            filters["$or"] = [
                {"motif": pattern},
                {"description": pattern},
            ]

        if type:
            filters["type"] = type

        if method:
            filters["method"] = method

        if start_date:
            filters.setdefault("date", {})["$gte"] = start_date
        if end_date:
            filters.setdefault("date", {})["$lte"] = end_date

        total = await BankTransaction.find(filters).count()
        items = (
            await BankTransaction.find(filters)
            .sort([("date", -1), ("transaction_id", -1)])
            .skip(skip)
            .limit(limit)
            .to_list()
        )

        return {
            "transactions": [
                BankTransactionResponse(
                    id=item.transaction_id,
                    type=item.type,
                    motif=item.motif,
                    description=item.description,
                    amount=item.amount,
                    date=item.date,
                    method=item.method,
                    reference=item.reference,
                )
                for item in items
            ],
            "total": total,
            "page": (skip // limit) + 1,
            "pages": (total + limit - 1) // limit,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/summary")
async def get_transactions_stats(
    current_user=Depends(get_current_user),
):
    """Statistiques sur les transactions (DB)."""
    try:
        pipeline_entries = [
            {"$match": {"type": "entry"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        pipeline_exits = [
            {"$match": {"type": "exit"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]

        entries_result = await BankTransaction.aggregate(pipeline_entries).to_list()
        exits_result = await BankTransaction.aggregate(pipeline_exits).to_list()

        total_entries = entries_result[0]["total"] if entries_result else 0
        total_exits = exits_result[0]["total"] if exits_result else 0
        current_balance = total_entries - total_exits

        tx_count = await BankTransaction.count()

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
    current_user=Depends(get_current_user),
):
    """Creer une transaction (persistee en DB)."""
    try:
        payload = transaction_data.model_dump() if hasattr(transaction_data, "model_dump") else transaction_data.dict()
        new_id = await get_next_id("bank_transactions")
        tx = BankTransaction(transaction_id=new_id, **payload)
        await tx.insert()
        return BankTransactionResponse(
            id=tx.transaction_id,
            type=tx.type,
            motif=tx.motif,
            description=tx.description,
            amount=tx.amount,
            date=tx.date,
            method=tx.method,
            reference=tx.reference,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{transaction_id}", response_model=BankTransactionResponse)
async def update_transaction(
    transaction_id: int,
    transaction_data: BankTransactionCreate,
    current_user=Depends(get_current_user),
):
    """Mettre a jour une transaction existante (tous les champs)."""
    try:
        tx = await BankTransaction.find_one(BankTransaction.transaction_id == transaction_id)
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction non trouvee")

        payload = transaction_data.model_dump() if hasattr(transaction_data, "model_dump") else transaction_data.dict()
        for k, v in payload.items():
            setattr(tx, k, v)
        await tx.save()
        return BankTransactionResponse(
            id=tx.transaction_id,
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
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{transaction_id}")
async def delete_transaction(
    transaction_id: int,
    current_user=Depends(get_current_user),
):
    """Supprimer une transaction."""
    try:
        tx = await BankTransaction.find_one(BankTransaction.transaction_id == transaction_id)
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction non trouvee")
        await tx.delete()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
