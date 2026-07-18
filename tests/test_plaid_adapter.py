"""Plaid adapter: sandbox pipeline, cursor persistence, and the Link handshake.

The full connect()/sync() pipeline in sandbox mode is already covered by the
parametrized tests in test_adapters.py (EXPECTED_INSTITUTIONS includes
"plaid"); this file covers what's specific to Plaid: multi-account-type
normalization, cursor persistence, and the Link-token/exchange handshake
(mocked — these must never hit the real network in tests).
"""

from unittest.mock import MagicMock, patch

import pytest

from finance_sync.adapters import get_adapter_class
from finance_sync.canonical import AccountType, AssetClass
from finance_sync.exceptions import AuthenticationError, RateLimitError, TokenExpiredError


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = b"{}"
    resp.json.return_value = json_data or {}
    resp.text = "{}"
    return resp


def test_plaid_sandbox_covers_both_cash_and_investment_accounts():
    adapter = get_adapter_class("plaid")()
    adapter.connect()
    payload = adapter.sync()
    types = {a.account_type for a in payload.accounts}
    assert AccountType.CHECKING in types
    assert AccountType.BROKERAGE in types
    assert payload.transactions, "checking account should have transactions"
    assert payload.holdings, "brokerage account should have holdings"
    for h in payload.holdings:
        assert isinstance(h.asset_class, AssetClass)


def test_plaid_transaction_sign_is_flipped_to_canonical_convention():
    # Plaid: positive amount = money out. Canonical: negative = money out.
    adapter = get_adapter_class("plaid")()
    adapter.connect()
    payload = adapter.sync()
    assert any(t.amount < 0 for t in payload.transactions)


def test_plaid_cursor_persists_across_a_sync():
    adapter = get_adapter_class("plaid")()
    adapter.connect()
    assert adapter.credentials.get("cursor") is None
    adapter.sync()
    assert adapter.credentials["cursor"] == "sandbox-cursor-1"


def test_plaid_sync_drains_all_transaction_pages():
    # transactions/sync is paginated; a fresh item with a large backlog spans
    # multiple pages and every page must be fetched, not just the first.
    adapter = get_adapter_class("plaid")()
    adapter.credentials = {"mode": "live", "access_token": "tok", "cursor": None}
    page1 = {"added": [{"transaction_id": "t1"}], "modified": [], "removed": [],
             "next_cursor": "c1", "has_more": True}
    page2 = {"added": [{"transaction_id": "t2"}], "modified": [], "removed": [],
             "next_cursor": "c2", "has_more": False}
    with patch("finance_sync.adapters.plaid_adapter.requests.post") as mock_post:
        mock_post.side_effect = [_mock_response(200, page1), _mock_response(200, page2)]
        raw = adapter._fetch_transactions_raw(None)
    assert [t["transaction_id"] for t in raw["added"]] == ["t1", "t2"]
    assert adapter.credentials["cursor"] == "c2"
    assert mock_post.call_args_list[1].kwargs["json"]["cursor"] == "c1"


def test_create_link_token_returns_token_from_plaid():
    adapter = get_adapter_class("plaid")()
    with patch("finance_sync.adapters.plaid_adapter.requests.post") as mock_post:
        mock_post.return_value = _mock_response(200, {"link_token": "link-abc"})
        token = adapter.create_link_token("user-1")
    assert token == "link-abc"
    url = mock_post.call_args.args[0]
    assert url.endswith("/link/token/create")


def test_connect_with_public_token_exchanges_and_stores_item():
    adapter = get_adapter_class("plaid")()
    with patch("finance_sync.adapters.plaid_adapter.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            200, {"access_token": "access-xyz", "item_id": "item-1"})
        credentials = adapter.connect_with_public_token("public-abc")
    assert credentials == adapter.credentials
    assert credentials["access_token"] == "access-xyz"
    assert credentials["item_id"] == "item-1"
    assert credentials["mode"] == "live"
    assert adapter.is_live


def test_item_login_required_maps_to_token_expired_error():
    adapter = get_adapter_class("plaid")()
    adapter.credentials = {"mode": "live", "access_token": "expired-token"}
    with patch("finance_sync.adapters.plaid_adapter.requests.post") as mock_post:
        mock_post.return_value = _mock_response(400, {"error_code": "ITEM_LOGIN_REQUIRED"})
        with pytest.raises(TokenExpiredError):
            adapter._fetch_accounts_raw()


def test_rate_limit_maps_to_rate_limit_error():
    adapter = get_adapter_class("plaid")()
    adapter.credentials = {"mode": "live", "access_token": "tok"}
    with patch("finance_sync.adapters.plaid_adapter.requests.post") as mock_post:
        mock_post.return_value = _mock_response(400, {"error_code": "RATE_LIMIT_EXCEEDED"})
        with pytest.raises(RateLimitError):
            adapter._fetch_accounts_raw()


def test_holdings_unsupported_by_institution_yields_no_holdings():
    # Capital One (no investments product) rejects investments/holdings/get;
    # the sync must proceed with zero holdings instead of failing.
    adapter = get_adapter_class("plaid")()
    adapter.credentials = {"mode": "live", "access_token": "tok"}
    with patch("finance_sync.adapters.plaid_adapter.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            400, {"error_code": "PRODUCTS_NOT_SUPPORTED",
                  "error_message": 'the following products are not supported '
                                   'by this institution: ["investments"]'})
        raw = adapter._fetch_holdings_raw(None)
    assert raw == {"holdings": [], "securities": []}
    assert adapter._normalize_holdings(None, raw) == []


def test_unmapped_error_falls_back_to_authentication_error():
    adapter = get_adapter_class("plaid")()
    adapter.credentials = {"mode": "live", "access_token": "tok"}
    with patch("finance_sync.adapters.plaid_adapter.requests.post") as mock_post:
        mock_post.return_value = _mock_response(400, {"error_code": "INVALID_REQUEST"})
        with pytest.raises(AuthenticationError):
            adapter._fetch_accounts_raw()
