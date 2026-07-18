"""Canonical model validation rules."""

from datetime import date

import pytest

from finance_sync.canonical import (
    AccountType,
    AssetClass,
    Balance,
    Holding,
    InstitutionAccount,
    SyncPayload,
    Transaction,
)
from finance_sync.exceptions import DataValidationError


def _account(external_id="acct-1", **kwargs):
    defaults = dict(name="Checking", account_type=AccountType.CHECKING)
    defaults.update(kwargs)
    return InstitutionAccount(external_id=external_id, **defaults)


def _holding(**kwargs):
    defaults = dict(account_external_id="acct-1", symbol="VTI", name="Vanguard Total",
                    quantity=10, current_price=250.0, market_value=2500.0,
                    asset_class=AssetClass.ETF)
    defaults.update(kwargs)
    return Holding(**defaults)


def test_valid_payload_passes():
    payload = SyncPayload(
        institution="test",
        accounts=[_account()],
        balances=[Balance(account_external_id="acct-1", current=100.0)],
        holdings=[_holding()],
        transactions=[Transaction(account_external_id="acct-1", external_id="t1",
                                  date=date(2026, 7, 1), description="Coffee", amount=-4.5)],
    )
    payload.validate()  # should not raise


def test_duplicate_transaction_ids_rejected():
    txn = Transaction(account_external_id="acct-1", external_id="t1",
                      date=date(2026, 7, 1), description="Coffee", amount=-4.5)
    payload = SyncPayload(institution="test", accounts=[_account()],
                          transactions=[txn, txn])
    with pytest.raises(DataValidationError):
        payload.validate()


def test_duplicate_holdings_rejected():
    payload = SyncPayload(institution="test", accounts=[_account()],
                          holdings=[_holding(), _holding()])
    with pytest.raises(DataValidationError):
        payload.validate()


def test_orphan_balance_rejected():
    payload = SyncPayload(institution="test", accounts=[_account()],
                          balances=[Balance(account_external_id="ghost", current=1.0)])
    with pytest.raises(DataValidationError):
        payload.validate()


def test_inconsistent_market_value_rejected():
    with pytest.raises(DataValidationError):
        _holding(market_value=9999.0).validate()


def test_negative_quantity_rejected():
    with pytest.raises(DataValidationError):
        _holding(quantity=-1).validate()


def test_gain_loss_math():
    h = _holding(avg_cost=200.0)
    assert h.cost_basis == 2000.0
    assert h.gain_loss == 500.0
