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
