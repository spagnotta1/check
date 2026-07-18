"""Deterministic sandbox backends simulating each provider's raw API.

When no live API credentials are configured, adapters talk to these backends
instead of the real institution. Each backend returns JSON in the *provider's
native shape* — the adapter still has to normalize it, so the full adapter
pipeline (fetch → normalize → validate → persist) is exercised end-to-end.

Data is deterministic: the same calendar day always produces the same
balances, prices, and transaction IDs, so repeated syncs are idempotent
(dedupe provably works) while day-over-day syncs show realistic movement.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timedelta
from typing import Dict, List


def _seed(*parts: object) -> int:
    """Stable integer seed from arbitrary parts."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(digest[:12], 16)


def daily_price(symbol: str, base: float, on: date, drift: float = 0.04) -> float:
    """Deterministic pseudo-market price for a symbol on a given day.

    A slow sine wave plus a per-day hash wiggle: stable within a day,
    plausibly different across days.
    """
    ordinal = on.toordinal()
    phase = _seed(symbol) % 360
    wave = math.sin((ordinal + phase) / 9.0) * drift
    wiggle = ((_seed(symbol, ordinal) % 1000) / 1000.0 - 0.5) * drift * 0.5
    return round(base * (1 + wave + wiggle), 2)


def _daily_flow(account_key: str, on: date) -> float:
    """Deterministic net cash movement for an account on a given day."""
    rng = random.Random(_seed("flow", account_key, on.toordinal()))
    return round(rng.uniform(-90, 60), 2)


class SandboxBackend:
    """Base class: shared helpers for simulated provider backends."""

    institution = "sandbox"

    def __init__(self, history_days: int = 90):
        self.history_days = history_days

    # -- helpers -----------------------------------------------------------

    def _balance_on(self, account_key: str, base: float, on: date) -> float:
        """Base balance plus accumulated deterministic daily flows this quarter."""
        start = on - timedelta(days=on.toordinal() % 90)
        total = base
        day = start
        while day <= on:
            total += _daily_flow(account_key, day)
            day += timedelta(days=1)
        return round(total, 2)

    def _transactions_for(self, account_key: str, merchants: List[tuple],
                          today: date) -> List[dict]:
        """Generate deterministic transactions for the trailing window."""
        txns = []
        for offset in range(self.history_days, -1, -1):
            day = today - timedelta(days=offset)
            rng = random.Random(_seed("txn", account_key, day.toordinal()))
            count = rng.choices([0, 1, 2, 3], weights=[35, 40, 20, 5])[0]
            for i in range(count):
                merchant, lo, hi = merchants[rng.randrange(len(merchants))]
                amount = round(-rng.uniform(lo, hi), 2)
                txns.append({
                    "id": f"{account_key}-{day.isoformat()}-{i}",
                    "date": day.isoformat(),
                    "description": merchant,
                    "amount": amount,
                })
            # bi-weekly payroll on checking accounts
            if "checking" in account_key and day.toordinal() % 14 == 3:
                txns.append({
                    "id": f"{account_key}-{day.isoformat()}-payroll",
                    "date": day.isoformat(),
                    "description": "ACME PAYROLL DIRECT DEP",
                    "amount": 2450.00,
                })
        return txns


class CoinbaseSandbox(SandboxBackend):
    """Simulates Coinbase's v2 API (accounts = wallets with crypto balances)."""

    institution = "coinbase"

    _WALLETS = [
        ("cb-wallet-btc", "BTC Wallet", "BTC", "Bitcoin", 0.4215, 67200.0),
        ("cb-wallet-eth", "ETH Wallet", "ETH", "Ethereum", 3.85, 3450.0),
        ("cb-wallet-sol", "SOL Wallet", "SOL", "Solana", 42.5, 168.0),
        ("cb-wallet-usd", "USD Wallet", "USD", "US Dollar", 512.34, 1.0),
    ]

    def get_accounts(self) -> dict:
        today = date.today()
        data = []
        for wid, name, code, cname, qty, base_price in self._WALLETS:
            price = 1.0 if code == "USD" else daily_price(code, base_price, today, drift=0.09)
            data.append({
                "id": wid,
                "name": name,
                "currency": {"code": code, "name": cname},
                "balance": {"amount": f"{qty:.8f}", "currency": code},
                "native_balance": {"amount": f"{qty * price:.2f}", "currency": "USD"},
                "type": "wallet",
            })
        return {"data": data}

    def get_spot_price(self, code: str) -> dict:
        today = date.today()
        base = {w[2]: w[5] for w in self._WALLETS}.get(code, 1.0)
        price = 1.0 if code == "USD" else daily_price(code, base, today, drift=0.09)
        return {"data": {"base": code, "currency": "USD", "amount": f"{price:.2f}"}}


class PlaidSandbox(SandboxBackend):
    """Simulates Plaid's `/accounts`, `/transactions/sync`, and
    `/investments/holdings/get` response shapes for one linked Item.

    Unlike the single-institution sandboxes above, Plaid aggregates many
    account types under one item, so this backend returns both a depository
    (checking) account with transactions and an investment (brokerage)
    account with holdings — demonstrating the breadth an aggregator provides.
    """

    institution = "plaid"

    _MERCHANTS = [
        ("UBER TRIP", 8, 42),
        ("TRADER JOE'S", 15, 90),
        ("SPOTIFY", 11.99, 11.99),
        ("AMAZON.COM", 10, 150),
        ("SHELL OIL", 25, 65),
        ("CVS PHARMACY", 6, 45),
    ]

    _POSITIONS = [
        ("sec-vti", "VTI", "Vanguard Total Stock Market ETF", "etf", 40.2, 271.0, 214.6),
        ("sec-aapl", "AAPL", "Apple Inc", "equity", 22.0, 232.0, 168.3),
        ("sec-vxus", "VXUS", "Vanguard Total International Stock ETF", "etf", 55.0, 62.4, 58.1),
    ]

    def item_id(self) -> str:
        return "sandbox-item-001"

    def institution_name(self) -> str:
        return "Sandbox Financial"

    def get_accounts(self) -> dict:
        today = date.today()
        checking_balance = self._balance_on("plaid-checking", 3100.00, today)
        return {
            "accounts": [
                {
                    "account_id": "plaid-acct-checking",
                    "name": "Plaid Checking",
                    "type": "depository",
                    "subtype": "checking",
                    "mask": "1111",
                    "balances": {"current": checking_balance, "available": checking_balance,
                                 "iso_currency_code": "USD"},
                },
                {
                    "account_id": "plaid-acct-brokerage",
                    "name": "Plaid Brokerage",
                    "type": "investment",
                    "subtype": "brokerage",
                    "mask": "2222",
                    "balances": {"current": None, "available": None, "iso_currency_code": "USD"},
                },
            ],
            "item": {"item_id": self.item_id(), "institution_id": "ins-sandbox"},
        }

    def sync_transactions(self, cursor: object) -> dict:
        today = date.today()
        added = []
        for txn in self._transactions_for("plaid-checking", self._MERCHANTS, today):
            added.append({
                "transaction_id": txn["id"],
                "account_id": "plaid-acct-checking",
                "date": txn["date"],
                "name": txn["description"],
                # Plaid convention: positive amount = money out (opposite of canonical).
                "amount": -txn["amount"],
                "pending": False,
                "iso_currency_code": "USD",
            })
        return {"added": added, "modified": [], "removed": [], "next_cursor": "sandbox-cursor-1",
                "has_more": False}

    def get_holdings(self) -> dict:
        today = date.today()
        holdings, securities = [], []
        for security_id, symbol, name, sec_type, qty, base_price, cost in self._POSITIONS:
            price = daily_price(symbol, base_price, today)
            holdings.append({
                "account_id": "plaid-acct-brokerage",
                "security_id": security_id,
                "quantity": qty,
                "institution_price": price,
                "institution_value": round(qty * price, 2),
                "cost_basis": round(cost * qty, 2),
            })
            securities.append({
                "security_id": security_id,
                "ticker_symbol": symbol,
                "name": name,
                "type": sec_type,
            })
        return {"holdings": holdings, "securities": securities}


SANDBOX_BACKENDS: Dict[str, type] = {
    "coinbase": CoinbaseSandbox,
    "plaid": PlaidSandbox,
}


def get_sandbox(institution: str) -> SandboxBackend:
    """Return the sandbox backend for an institution slug."""
    cls = SANDBOX_BACKENDS.get(institution)
    if cls is None:
        raise KeyError(f"No sandbox backend for institution {institution!r}")
    return cls()
