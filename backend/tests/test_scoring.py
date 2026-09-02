"""Phase 5 tests: graph features, wallet scoring, and explanations.

Models are trained inside the test run on a small generated dataset, so this
needs no committed artefacts and no external data.
"""

from __future__ import annotations

import json

import pytest

from app.models import WalletAlert
from app.services import explainability, scoring, wallet_model
from app.services.data_generator import build_dataset
from app.services.feature_extraction import (
    FEATURE_NAMES,
    extract_all_wallets,
    extract_wallet_features,
    wallet_nodes,
)
from app.services.graph_builder import build_graph

N_NORMAL = 700


@pytest.fixture(scope="module")
def dataset():
    transactions, ground_truth = build_dataset(n_normal=N_NORMAL, seed=4242)
    return build_graph(transactions), ground_truth


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    out = tmp_path_factory.mktemp("wallet_models")
    wallet_model.train(
        n_normal=N_NORMAL, train_seed=11, eval_seed=99, model_dir=out
    )
    return wallet_model.load(out)


# --------------------------------------------------------------------------
# Feature extraction
# --------------------------------------------------------------------------


def test_features_have_stable_shape(dataset):
    graph, _ = dataset
    frame = extract_all_wallets(graph)
    assert list(frame.columns) == FEATURE_NAMES
    assert len(frame) == len(wallet_nodes(graph))
    assert not frame.isna().any().any()
    assert frame.index.name == "wallet_address"


def test_features_exclude_ip_nodes(dataset):
    graph, _ = dataset
    frame = extract_all_wallets(graph)
    ips = [n for n, d in graph.nodes(data=True) if d.get("type") == "ip"]
    assert ips, "fixture should contain IP nodes"
    assert not set(frame.index) & set(ips)


def test_degree_features_match_the_graph(dataset):
    graph, _ = dataset
    wallet = max(wallet_nodes(graph), key=lambda w: graph.degree(w))
    features = extract_wallet_features(graph, wallet)
    # Wallet-projection degrees, so never above the full-graph degree.
    assert features["in_degree"] <= graph.in_degree(wallet)
    assert features["out_degree"] <= graph.out_degree(wallet)
    assert features["transaction_velocity"] >= 0
    assert features["amount_variance"] >= 0


def test_global_metrics_are_cached(dataset):
    graph, _ = dataset
    extract_all_wallets(graph)
    assert "_feature_cache" in graph.graph
    cached = graph.graph["_feature_cache"]
    extract_all_wallets(graph)
    assert graph.graph["_feature_cache"] is cached, "cache should be reused"


def test_unknown_wallet_raises(dataset):
    graph, _ = dataset
    with pytest.raises(KeyError):
        extract_wallet_features(graph, "1NoSuchWalletAnywhere")


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def test_alert_matches_the_schema(dataset, models):
    graph, ground_truth = dataset
    rf, iso, manifest = models
    wallet = next(iter(ground_truth["guilty_wallets"]))
    alert = WalletAlert.model_validate(
        scoring.score_wallet(graph, wallet, rf, iso, manifest)
    )
    assert alert.wallet_address == wallet
    assert 0.0 <= alert.risk_score <= 1.0
    assert 0.0 <= alert.confidence <= 1.0
    assert alert.severity in {"low", "medium", "high", "critical"}
    assert alert.top_reasons
    json.dumps(alert.model_dump())  # must survive the API boundary


def test_planted_wallets_outscore_clean_ones(dataset, models):
    graph, ground_truth = dataset
    rf, iso, manifest = models
    guilty = set(ground_truth["guilty_wallets"])
    wallets = wallet_nodes(graph)
    clean = [w for w in wallets if w not in guilty]

    guilty_scores = [
        scoring.score_wallet(graph, w, rf, iso, manifest, explain=False)["risk_score"]
        for w in list(guilty)[:15]
    ]
    clean_scores = [
        scoring.score_wallet(graph, w, rf, iso, manifest, explain=False)["risk_score"]
        for w in clean[:40]
    ]
    mean_guilty = sum(guilty_scores) / len(guilty_scores)
    mean_clean = sum(clean_scores) / len(clean_scores)
    assert mean_guilty > mean_clean + 0.3, (mean_guilty, mean_clean)


def test_severity_thresholds_are_ordered():
    assert scoring.severity_for(0.95) == "critical"
    assert scoring.severity_for(0.60) == "high"
    assert scoring.severity_for(0.35) == "medium"
    assert scoring.severity_for(0.05) == "low"
    # Monotonic: a higher score never yields a lesser severity.
    order = ["low", "medium", "high", "critical"]
    seen = [order.index(scoring.severity_for(x / 100)) for x in range(0, 101)]
    assert seen == sorted(seen)


def test_confidence_rewards_agreement_and_corroboration():
    decisive_agreeing = scoring._confidence(0.95, 0.92, rules_fired=2)
    uncertain_conflicting = scoring._confidence(0.52, 0.05, rules_fired=0)
    assert decisive_agreeing > uncertain_conflicting
    assert 0.0 <= uncertain_conflicting <= 1.0
    assert 0.0 <= decisive_agreeing <= 1.0


def test_elliptic_model_is_rejected(dataset, models):
    """The Phase 3 models take a different feature space and must not be used."""
    graph, _ = dataset
    _, iso, manifest = models

    class EllipticShaped:
        n_features_in_ = 166

    with pytest.raises(ValueError, match="cannot score wallets"):
        scoring.score_wallet(
            graph, wallet_nodes(graph)[0], EllipticShaped(), iso, manifest
        )


def test_unknown_wallet_raises_in_scoring(dataset, models):
    graph, _ = dataset
    rf, iso, manifest = models
    with pytest.raises(KeyError):
        scoring.score_wallet(graph, "1NopeNotHere", rf, iso, manifest)


def test_score_all_wallets_is_sorted(dataset, models):
    graph, _ = dataset
    rf, iso, manifest = models
    alerts = scoring.score_all_wallets(graph, rf, iso, manifest)
    assert len(alerts) == len(wallet_nodes(graph))
    scores = [a["risk_score"] for a in alerts]
    assert scores == sorted(scores, reverse=True)
    # Bulk scoring skips explanations by default.
    assert all(a["top_reasons"] == [] for a in alerts)


def test_batched_scoring_agrees_with_per_wallet_scoring(dataset, models):
    """score_all_wallets batches its predictions for a 23x speedup; it has to
    produce exactly what the per-wallet path produces, not merely something
    similar."""
    graph, _ = dataset
    rf, iso, manifest = models
    alerts = {a["wallet_address"]: a for a in scoring.score_all_wallets(graph, rf, iso, manifest)}

    for wallet in list(alerts)[:20]:
        single = scoring.score_wallet(graph, wallet, rf, iso, manifest, explain=False)
        batched = alerts[wallet]
        assert single["risk_score"] == pytest.approx(batched["risk_score"], abs=1e-9)
        assert single["severity"] == batched["severity"]
        assert single["confidence"] == pytest.approx(batched["confidence"], abs=1e-9)
        assert single["connected_wallets"] == batched["connected_wallets"]


def test_connected_wallets_excludes_ips(dataset, models):
    graph, _ = dataset
    rf, iso, manifest = models
    wallet = max(wallet_nodes(graph), key=lambda w: graph.degree(w))
    alert = scoring.score_wallet(graph, wallet, rf, iso, manifest, explain=False)
    for neighbour in alert["connected_wallets"]:
        assert graph.nodes[neighbour]["type"] == "wallet"


# --------------------------------------------------------------------------
# Explanations
# --------------------------------------------------------------------------


def test_rule_findings_lead_the_reasons(dataset, models):
    graph, ground_truth = dataset
    rf, iso, manifest = models
    from app.services import domain_rules

    wallet = next(
        (w for w in ground_truth["guilty_wallets"] if domain_rules.run_all_rules(graph, w)),
        None,
    )
    assert wallet, "at least one planted wallet should trigger a rule"
    findings = domain_rules.run_all_rules(graph, wallet)
    alert = scoring.score_wallet(graph, wallet, rf, iso, manifest)
    assert alert["top_reasons"][0] == findings[0]["reason"]


def test_clean_wallets_get_a_profile_not_a_defence(dataset, models):
    """Regression: SHAP once described a clean wallet as 'swinging wildly'."""
    graph, ground_truth = dataset
    rf, iso, manifest = models
    guilty = set(ground_truth["guilty_wallets"])

    checked = 0
    for wallet in wallet_nodes(graph):
        if wallet in guilty:
            continue
        alert = scoring.score_wallet(graph, wallet, rf, iso, manifest)
        if alert["severity"] != "low":
            continue
        blob = " ".join(alert["top_reasons"]).lower()
        for loaded in ("swing wildly", "unusually high", "purpose-made",
                       "built for one job", "obscures the trail"):
            assert loaded not in blob, f"{wallet}: {alert['top_reasons']}"
        checked += 1
        if checked >= 25:
            break
    assert checked, "expected some low-severity wallets to check"


def test_explanation_wording_tracks_the_value_not_the_shap_sign():
    """Regression: a 0.03 clustering coefficient was once described as
    'its counterparties transact with each other too'."""
    phrases = explainability.FEATURE_PHRASES["clustering_coefficient"]
    assert "never deal with each other" in phrases["below"](0.03)
    assert "deal with each other too" in phrases["above"](0.8)


def test_variance_is_reported_in_btc_not_btc_squared():
    """Regression: 'variance 11.603 BTC²' is not a human unit."""
    sentence = explainability.FEATURE_PHRASES["amount_variance"]["above"](9.0)
    assert "3.000 BTC" in sentence  # sqrt(9)
    assert "²" not in sentence


def test_explanations_survive_a_missing_shap(dataset, models, monkeypatch):
    """If SHAP cannot run, rule reasons must still come through."""
    graph, ground_truth = dataset
    rf, iso, manifest = models
    monkeypatch.setattr(
        explainability, "_illicit_shap_values", lambda *a, **k: None
    )
    from app.services import domain_rules

    wallet = next(
        w for w in ground_truth["guilty_wallets"] if domain_rules.run_all_rules(graph, w)
    )
    alert = scoring.score_wallet(graph, wallet, rf, iso, manifest)
    assert alert["top_reasons"], "must degrade to rule reasons, not fail"


def test_model_manifest_records_the_wallet_feature_space(models):
    _, _, manifest = models
    assert manifest["feature_space"] == "wallet_graph"
    assert manifest["feature_columns"] == wallet_model.ALL_FEATURE_NAMES
    assert manifest["reference"]["median"], "medians drive the explanation wording"
    # Held-out evaluation, not the training seed.
    assert manifest["metrics"]["train"]["seed"] != manifest["metrics"]["eval"]["seed"]
