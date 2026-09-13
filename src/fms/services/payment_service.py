from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PaymentResult:
    success: bool
    transaction_id: str
    message: str


class PaymentService:
    async def charge(self, amount: Decimal, description: str = "") -> PaymentResult:
        return PaymentResult(
            success=True,
            transaction_id="mock_txn_" + str(hash(str(amount) + description))[:8],
            message="Payment processed successfully (mock)",
        )

    async def refund(self, transaction_id: str, amount: Decimal) -> PaymentResult:
        return PaymentResult(
            success=True,
            transaction_id="mock_refund_" + transaction_id,
            message="Refund processed successfully (mock)",
        )