"""Canonical (provider-agnostic) data models.

Every adapter translates its institution's raw API responses into these
models. Downstream consumers — the repository, dashboard, charts, reports,
and AI chat — only ever see these types and never raw provider JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from .exceptions import DataValidationError


class AccountType(str, Enum):
    """Normalized account categories used across the application."""

    CHECKING = "checking"
    SAVINGS = "savings"
    BROKERAGE = "brokerage"
    CRYPTO = "crypto"
    CREDIT = "credit"
    OTHER = "other"

    @property
    def is_cash(self) -> bool:
        return self in (AccountType.CHECKING, AccountType.SAVINGS)

    @property
    def is_investment(self) -> bool:
        return self in (AccountType.BROKERAGE, AccountType.CRYPTO)


class AssetClass(str, Enum):
    """Normalized asset classes for holdings (matches existing UI values)."""

    STOCK = "Stock"
    ETF = "ETF"
    MUTUAL_FUND = "Mutual Fund"
    BOND = "Bond"
    CRYPTO = "Crypto"
    CASH = "Cash"
    OTHER = "Other"


@dataclass(frozen=True)
class InstitutionAccount:
    """A single account at a financial institution."""

    external_id: str
    name: str
    account_type: AccountType
    currency: str = "USD"
    mask: Optional[str] = None  # last-4 style display suffix

    def validate(self) -> None:
        if not self.external_id:
            raise DataValidationError("InstitutionAccount.external_id is required")
        if not self.name:
            raise DataValidationError("InstitutionAccount.name is required")
        if not isinstance(self.account_type, AccountType):
            raise DataValidationError(f"Invalid account_type: {self.account_type!r}")


@dataclass(frozen=True)
class CashAccount(InstitutionAccount):
    """Checking / savings account view (semantic alias of InstitutionAccount)."""


@dataclass(frozen=True)
class InvestmentAccount(InstitutionAccount):
    """Brokerage / crypto account view (semantic alias of InstitutionAccount)."""


@dataclass(frozen=True)
class Balance:
    """Point-in-time balance for one account."""

    account_external_id: str
    current: float
    available: Optional[float] = None
    currency: str = "USD"
    as_of: datetime = field(default_factory=datetime.utcnow)

    def validate(self) -> None:
        if not self.account_external_id:
            raise DataValidationError("Balance.account_external_id is required")
        if self.current is None:
            raise DataValidationError("Balance.current is required")


@dataclass(frozen=True)
class Holding:
    """A single position (equity, fund, bond, or crypto) in an account."""

    account_external_id: str
    symbol: str
    name: str
    quantity: float
    current_price: float
    market_value: float
    asset_class: AssetClass = AssetClass.STOCK
    avg_cost: Optional[float] = None  # per-unit average cost basis
    currency: str = "USD"
    external_id: Optional[str] = None

    @property
    def cost_basis(self) -> Optional[float]:
        if self.avg_cost is None:
            return None
        return round(self.avg_cost * self.quantity, 2)

    @property
    def gain_loss(self) -> Optional[float]:
        basis = self.cost_basis
        if basis is None:
            return None
        return round(self.market_value - basis, 2)

    def validate(self) -> None:
        if not self.account_external_id:
            raise DataValidationError("Holding.account_external_id is required")
        if not self.symbol:
            raise DataValidationError("Holding.symbol is required")
        if self.quantity is None or self.quantity < 0:
            raise DataValidationError(f"Holding {self.symbol}: invalid quantity {self.quantity!r}")
        if self.market_value is None or self.market_value < 0:
            raise DataValidationError(f"Holding {self.symbol}: invalid market_value {self.market_value!r}")
        if not isinstance(self.asset_class, AssetClass):
            raise DataValidationError(f"Holding {self.symbol}: invalid asset_class {self.asset_class!r}")
        # market value must be consistent with qty * price (1% tolerance for rounding)
        if self.quantity and self.current_price:
            expected = self.quantity * self.current_price
            if expected > 0 and abs(expected - self.market_value) / expected > 0.01:
                raise DataValidationError(
                    f"Holding {self.symbol}: market_value {self.market_value} inconsistent "
                    f"with quantity*price {expected:.2f}"
                )


@dataclass(frozen=True)
class Transaction:
    """A cash-account transaction. Amounts are signed: negative = money out."""

    account_external_id: str
    external_id: str
    date: date
    description: str
    amount: float
    currency: str = "USD"
    pending: bool = False
    category_hint: Optional[str] = None  # provider-supplied category, if any

    def validate(self) -> None:
        if not self.account_external_id:
            raise DataValidationError("Transaction.account_external_id is required")
        if not self.external_id:
            raise DataValidationError("Transaction.external_id is required")
        if not self.description:
            raise DataValidationError("Transaction.description is required")
        if self.amount is None:
            raise DataValidationError("Transaction.amount is required")
        if self.date is None:
            raise DataValidationError("Transaction.date is required")


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Aggregated point-in-time picture of everything a connection holds."""

    institution: str
    as_of: datetime
    total_cash: float = 0.0
    total_investments: float = 0.0
    total_crypto: float = 0.0

    @property
    def total(self) -> float:
        return round(self.total_cash + self.total_investments + self.total_crypto, 2)


@dataclass
class SyncPayload:
    """Everything one adapter produced during a sync, in canonical form."""

    institution: str
    accounts: List[InstitutionAccount] = field(default_factory=list)
    balances: List[Balance] = field(default_factory=list)
    holdings: List[Holding] = field(default_factory=list)
    transactions: List[Transaction] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    def validate(self) -> None:
        """Validate internal consistency of the payload; raises DataValidationError."""
        if not self.institution:
            raise DataValidationError("SyncPayload.institution is required")
        account_ids = set()
        for acct in self.accounts:
            acct.validate()
            if acct.external_id in account_ids:
                raise DataValidationError(f"Duplicate account external_id {acct.external_id}")
            account_ids.add(acct.external_id)
        for bal in self.balances:
            bal.validate()
            if bal.account_external_id not in account_ids:
                raise DataValidationError(f"Balance references unknown account {bal.account_external_id}")
        seen_holdings = set()
        for h in self.holdings:
            h.validate()
            if h.account_external_id not in account_ids:
                raise DataValidationError(f"Holding {h.symbol} references unknown account {h.account_external_id}")
            key = (h.account_external_id, h.symbol)
            if key in seen_holdings:
                raise DataValidationError(f"Duplicate holding {h.symbol} in account {h.account_external_id}")
            seen_holdings.add(key)
        seen_txn = set()
        for t in self.transactions:
            t.validate()
            if t.account_external_id not in account_ids:
                raise DataValidationError(f"Transaction references unknown account {t.account_external_id}")
            key = (t.account_external_id, t.external_id)
            if key in seen_txn:
                raise DataValidationError(f"Duplicate transaction external_id {t.external_id}")
            seen_txn.add(key)

    def to_dict(self) -> dict:
        return asdict(self)
