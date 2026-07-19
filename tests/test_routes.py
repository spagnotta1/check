"""HTTP API and page rendering for the sync layer."""

from models import FinancialAccount, Holding, InstitutionConnection, Transaction


def _connect(client, institution):
    resp = client.post("/api/connections", json={"institution": institution})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def test_institutions_catalog(client):
    resp = client.get("/api/institutions")
    assert resp.status_code == 200
    slugs = {i["institution"] for i in resp.get_json()}
    assert {"coinbase", "plaid"}.issubset(slugs)


def test_connect_flow_syncs_immediately(client):
    data = _connect(client, "plaid")
    assert data["institution"] == "plaid"
    # SYNC_SYNCHRONOUS=True → the initial sync already ran
    assert FinancialAccount.query.count() == 2
    assert Transaction.query.filter_by(source="sync").count() > 0
    connection = InstitutionConnection.query.first()
    assert connection.status == "connected"
    assert connection.last_sync_status == "success"


def test_connect_unknown_institution_404(client):
    resp = client.post("/api/connections", json={"institution": "nope"})
    assert resp.status_code == 404


def test_manual_refresh_single_and_all(client):
    data = _connect(client, "coinbase")
    resp = client.post(f"/api/connections/{data['id']}/sync")
    assert resp.status_code == 202
    resp = client.post("/api/sync/all")
    assert resp.status_code == 202
    history = client.get("/api/sync/history").get_json()
    assert len(history) >= 3  # connect + single + all


def test_sync_status_endpoint(client):
    _connect(client, "plaid")
    status = client.get("/api/sync/status").get_json()
    assert status["running"] is False
    assert status["connections"][0]["institution"] == "plaid"


def test_net_worth_endpoint(client):
    _connect(client, "plaid")
    _connect(client, "coinbase")
    data = client.get("/api/net-worth").get_json()
    assert data["current"]["net_worth"] > 0
    assert data["current"]["crypto"] > 0
    assert len(data["history"]) == 1  # today's snapshot


def test_disconnect_via_api(client):
    data = _connect(client, "coinbase")
    resp = client.delete(f"/api/connections/{data['id']}")
    assert resp.status_code == 200
    assert FinancialAccount.query.count() == 0


def test_synced_holding_cannot_be_edited_manually(client):
    _connect(client, "coinbase")
    holding = Holding.query.filter_by(source="sync").first()
    assert holding is not None
    resp = client.put(f"/api/holdings/{holding.id}", json={"shares": 1})
    assert resp.status_code == 409
    resp = client.delete(f"/api/holdings/{holding.id}")
    assert resp.status_code == 409


def test_transaction_filters_can_be_cleared(client):
    # Prime the sticky session filters via a drill-down style link.
    resp = client.get("/transactions?type=inbound&category=Income")
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("direction") == "inbound"
        assert sess.get("category") == "Income"

    # Submitting the filter form with everything reset to "All" sends the
    # params as empty strings — that must clear the filters, not fall back
    # to the previously stored session values.
    resp = client.get(
        "/transactions?account=&category=&direction=&start_date=&end_date=&search="
    )
    assert resp.status_code == 200
    assert b'value="inbound" selected' not in resp.data
    with client.session_transaction() as sess:
        assert sess.get("direction") is None
        assert sess.get("category") is None

    # Params absent entirely (e.g. pagination links) still keep session filters.
    client.get("/transactions?direction=outgo")
    resp = client.get("/transactions?page=1")
    assert b'value="outgo"   selected' in resp.data


def test_pages_render(client):
    _connect(client, "coinbase")
    _connect(client, "plaid")
    for path in ("/", "/investments", "/connections", "/sync-history",
                 "/transactions", "/budgets"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    body = client.get("/investments").data.decode()
    assert "Refresh" in body
    assert "Synced" in body
    body = client.get("/connections").data.decode()
    assert "Coinbase" in body and "Connected" in body


def test_dashboard_category_cascading_filter(client):
    from datetime import date
    from models import db, Transaction

    db.session.add(Transaction(account_name="Checking", date=date(2026, 3, 5),
                               description="AJI SUSHI", amount=-77.31, category="Food"))
    db.session.add(Transaction(account_name="Checking", date=date(2026, 3, 6),
                               description="SHELL GAS", amount=-53.19, category="Gas"))
    db.session.add(Transaction(account_name="Checking", date=date(2026, 3, 7),
                               description="MTA TICKET", amount=-20.00, category="Travel"))
    db.session.commit()
    window = "start_date=2026-03-01&end_date=2026-03-31"

    # Unfiltered: all categories feed the charts (running balance ends
    # at -77.31 - 53.19 - 20.00 = -150.50).
    html = client.get(f"/?{window}").get_data(as_text=True)
    assert "-150.5" in html

    # Single category: only Food data reaches the visualizations
    # (balance history JSON), but the breakdown grid still lists the other
    # categories so the user can switch or extend the filter.
    html = client.get(f"/?{window}&category=Food").get_data(as_text=True)
    assert "-77.31" in html
    assert "-130.5" not in html and "-150.5" not in html
    assert "Category: Food" in html                     # active-filter chip
    assert ">Gas</a>" in html                           # grid row still clickable
    assert "category=Food&amp;category=Gas" in html \
        or "category=Food&category=Gas" in html         # row link adds Gas to the selection

    # Multiselect: Food + Gas cascade together, Travel stays excluded.
    html = client.get(f"/?{window}&category=Food&category=Gas").get_data(as_text=True)
    assert "-130.5" in html
    assert "-150.5" not in html
    assert "Category: Food" in html and "Category: Gas" in html
    assert "clear categories" in html

    # No category params → no chips.
    html = client.get(f"/?{window}").get_data(as_text=True)
    assert "Category: Food" not in html
