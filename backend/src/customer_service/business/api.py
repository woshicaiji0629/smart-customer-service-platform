"""Authenticated mock withdrawal queries."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from customer_service.auth.api import CurrentUserDependency
from customer_service.business.service import (
    MOCK_WITHDRAWAL_SERVICE,
    WithdrawalRecord,
)


class WithdrawalResponse(BaseModel):
    order_id: str
    coin: str
    size: str
    status: Literal["pending", "fail", "success"]
    chain: str
    updated_at: str


router = APIRouter(prefix="/business", tags=["business"])


@router.get("/withdrawals/{order_id}", response_model=WithdrawalResponse)
async def get_withdrawal(
    order_id: str,
    user: CurrentUserDependency,
) -> WithdrawalResponse:
    withdrawal = MOCK_WITHDRAWAL_SERVICE.get_withdrawal(user.user_id, order_id)
    if withdrawal is None:
        raise HTTPException(status_code=404, detail="未找到该用户的提现记录")
    return _withdrawal_response(withdrawal)


def _withdrawal_response(record: WithdrawalRecord) -> WithdrawalResponse:
    return WithdrawalResponse(
        order_id=record.order_id,
        coin=record.coin,
        size=record.size,
        status=record.status,
        chain=record.chain,
        updated_at=record.updated_at,
    )
