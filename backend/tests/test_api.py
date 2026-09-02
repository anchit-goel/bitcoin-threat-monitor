"""Phase 6 tests: the API, exercised through real HTTP requests.

These go through TestClient rather than calling the endpoint functions
directly, so status codes, query-parameter validation, response-model coercion
and CORS are all covered as a client would meet them.
"""

from __future__ import annotations

import json
import socket

import pytest
from fastapi.testclient import TestClient

from app.main import EXPLAIN_TOP_N, app, state
from app.services import wallet_model
from app.services.data_generator import build_dataset

N_NORMAL = 700


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    """A small generated dataset written to a JSON file, plus its ground truth."""
    transactions, ground_truth = build_dataset(n_normal=N_NORMAL, seed=555)
    path = tmp_path_factory.mktemp("data") / "transactions.json"
    path.write_text(
        json.dumps([json.loads(t.model_dump_json()) for t in transactions]),
        encoding="utf-8",
    )
    return path, ground_truth


@pytest.fixture(scope="module")
def trained_models(tmp_path_factory):
    out = tmp_path_factory.mktemp("api_models")
    wallet_model.train(n_normal=N_NORMAL, train_seed=21, eval_seed=87, model_dir=out)
    return wallet_model.load(out)


@pytest.fixture(scope="module")
def client(trained_models):
    """A client whose app has models loaded, as it would after a real startup."""
    with TestClient(app) as c:
        state.rf_model, state.iso_model, state.manifest = trained_models
        state.models_loaded = True
        state.model_error = None
        yield c
    state.reset_graph()


@pytest.fixture(scope="module")
def ingested(client, dataset):
    path, ground_truth = dataset
    with path.open("rb") as fh:
        response = client.post("/ingest", files={"file": ("transactions.json", fh, "application/json")})
    assert response.status_code == 200, response.text
    return response.json(), ground_truth


# --------------------------------------------------------------------------
# Before anything is loaded
# --------------------------------------------------------------------------


def test_health_reports_an_empty_system(client):
    state.reset_graph()
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["graph_loaded"] is False
    assert body["models_loaded"] is True
    assert body["model_feature_space"] == "wallet_graph"


def test_endpoints_explain_that_nothing_is_loaded(client):
    state.reset_graph()
    for path in ("/alerts", "/graph", "/wallet/1anything"):
        response = client.get(path)
        assert response.status_code == 409, path
        assert "ingest" in response.json()["detail"].lower()


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


def test_ingest_returns_a_summary(ingested):
    summary, ground_truth = ingested
    assert summary["status"] == "processed"
    assert summary["transactions"] > N_NORMAL
    assert summary["wallets_scored"] > 0
    assert summary["high_risk_count"] > 0
    assert summary["duration_seconds"] > 0
    assert summary["filename"] == "transactions.json"


def test_ingest_rejects_an_unsupported_extension(client):
    response = client.post(
        "/ingest", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 415
    assert ".json" in response.json()["detail"]


def test_ingest_rejects_an_empty_file(client):
    response = client.post(
        "/ingest", files={"file": ("empty.json", b"", "application/json")}
    )
    assert response.status_code in (400, 422)


def test_ingest_reports_which_record_was_malformed(client):
    bad = json.dumps([{"txid": "abc", "timestamp": "2026-01-01T00:00:00Z"}])
    response = client.post(
        "/ingest", files={"file": ("bad.json", bad.encode(), "application/json")}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "record #0" in detail
    assert "src_ip" in detail


def test_a_traversal_filename_cannot_escape(client, dataset):
    """Only the suffix of a client-supplied filename is ever used."""
    path, _ = dataset
    with path.open("rb") as fh:
        response = client.post(
            "/ingest",
            files={"file": ("../../../../evil.json", fh, "application/json")},
        )
    assert response.status_code == 200
    # Reported back verbatim, but never used as a path.
    assert response.json()["filename"] == "../../../../evil.json"


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------


def test_alerts_are_sorted_and_carry_reasons(client, ingested):
    body = client.get("/alerts", params={"limit": 25}).json()
    assert body
    scores = [a["risk_score"] for a in body]
    assert scores == sorted(scores, reverse=True)
    for alert in body:
        assert alert["top_reasons"], alert["wallet_address"]
        assert set(alert) >= {
            "wallet_address", "risk_score", "confidence", "severity",
            "top_reasons", "connected_wallets",
        }


def test_alerts_respect_limit_and_min_severity(client, ingested):
    assert len(client.get("/alerts", params={"limit": 5}).json()) <= 5

    high = client.get("/alerts", params={"min_severity": "high", "limit": 500}).json()
    assert all(a["severity"] in ("high", "critical") for a in high)

    everything = client.get("/alerts", params={"limit": 5000}).json()
    assert len(high) <= len(everything)


def test_alerts_rejects_a_bad_severity(client, ingested):
    assert client.get("/alerts", params={"min_severity": "catastrophic"}).status_code == 422


def test_alerts_below_the_explained_window_still_get_reasons(client, ingested):
    """Explanations are precomputed for the top N only; the rest are built on
    demand, and a client must not be able to tell the difference."""
    everything = client.get("/alerts", params={"limit": 5000}).json()
    if len(everything) <= EXPLAIN_TOP_N:
        pytest.skip("dataset smaller than the precomputed window")
    for alert in everything[EXPLAIN_TOP_N + 5:EXPLAIN_TOP_N + 10]:
        assert alert["top_reasons"], alert["wallet_address"]


# --------------------------------------------------------------------------
# Wallet detail
# --------------------------------------------------------------------------


def test_wallet_detail_includes_a_subgraph(client, ingested):
    top = client.get("/alerts", params={"limit": 1}).json()[0]
    body = client.get(f"/wallet/{top['wallet_address']}").json()

    assert body["wallet_address"] == top["wallet_address"]
    assert body["top_reasons"]
    assert body["hops"] == 2
    sub = body["subgraph"]
    assert sub["nodes"] and sub["links"]
    assert any(n["id"] == top["wallet_address"] for n in sub["nodes"])


def test_wallet_detail_hops_widen_the_neighbourhood(client, ingested):
    address = client.get("/alerts", params={"limit": 1}).json()[0]["wallet_address"]
    one = client.get(f"/wallet/{address}", params={"hops": 1, "limit": 5000}).json()
    two = client.get(f"/wallet/{address}", params={"hops": 2, "limit": 5000}).json()
    assert len(two["subgraph"]["nodes"]) >= len(one["subgraph"]["nodes"])


def test_unknown_wallet_is_404(client, ingested):
    response = client.get("/wallet/1ThisWalletDoesNotExist")
    assert response.status_code == 404
    assert "1ThisWalletDoesNotExist" in response.json()["detail"]


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------


def test_graph_is_capped_and_says_so(client, ingested):
    body = client.get("/graph", params={"limit": 50}).json()
    assert len(body["nodes"]) <= 50
    assert body["total_nodes"] >= len(body["nodes"])
    assert body["truncated"] is True
    # Every link must connect two nodes that were actually returned.
    ids = {n["id"] for n in body["nodes"]}
    for link in body["links"]:
        assert link["source"] in ids and link["target"] in ids


def test_graph_min_risk_filters_wallets(client, ingested):
    body = client.get("/graph", params={"min_risk": 0.8, "limit": 2000}).json()
    for node in body["nodes"]:
        if node["type"] == "wallet":
            assert (node.get("risk_score") or 0) >= 0.8


def test_graph_can_exclude_ip_nodes(client, ingested):
    body = client.get("/graph", params={"include_ips": False, "limit": 2000}).json()
    assert body["nodes"]
    assert all(n["type"] == "wallet" for n in body["nodes"])


def test_graph_nodes_carry_scores_for_colouring(client, ingested):
    body = client.get("/graph", params={"limit": 100}).json()
    wallets = [n for n in body["nodes"] if n["type"] == "wallet"]
    assert wallets
    assert all(n.get("risk_score") is not None for n in wallets)
    assert all("severity" in n for n in wallets)


def test_delete_graph_resets_state(client, dataset):
    path, _ = dataset
    with path.open("rb") as fh:
        client.post("/ingest", files={"file": ("transactions.json", fh, "application/json")})
    assert client.get("/health").json()["graph_loaded"] is True

    assert client.delete("/graph").json()["status"] == "cleared"
    assert client.get("/health").json()["graph_loaded"] is False
    assert client.get("/alerts").status_code == 409


# --------------------------------------------------------------------------
# CORS and the offline guarantee
# --------------------------------------------------------------------------


def test_cors_allows_the_vite_dev_server(client):
    response = client.options(
        "/alerts",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_the_whole_pipeline_makes_no_network_calls(dataset, trained_models, monkeypatch):
    """The hard requirement: an analyst's traffic must not leave the machine.

    Every outbound socket path is replaced with something that raises, then a
    full ingest and score is run. Anything reaching for the network fails the
    test rather than quietly succeeding in an environment that happens to be
    online.
    """
    path, _ = dataset

    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)

    from app.services import geoip, scoring
    from app.services.graph_builder import build_graph, graph_to_json
    from app.services.ingestion import load_transactions

    rf, iso, manifest = trained_models
    transactions = load_transactions(path)
    graph = build_graph(transactions)
    geoip.enrich_graph(graph)           # must not reach out when the db is absent
    alerts = scoring.score_all_wallets(graph, rf, iso, manifest)
    scoring.score_wallet(graph, alerts[0]["wallet_address"], rf, iso, manifest)
    graph_to_json(graph)

    assert alerts


def test_geoip_never_looks_up_private_addresses():
    from app.services import geoip

    for private in ("10.0.0.1", "192.168.1.1", "127.0.0.1", "169.254.0.1", "not-an-ip"):
        assert geoip.is_public_ip(private) is False
        assert geoip.lookup(private) is None
    assert geoip.is_public_ip("8.8.8.8") is True


def test_geoip_degrades_without_a_database():
    """Geo is enrichment, not a dependency."""
    from app.services import geoip

    if geoip.is_available():
        pytest.skip("a GeoLite2 database is installed here")
    assert geoip.lookup("8.8.8.8") is None
