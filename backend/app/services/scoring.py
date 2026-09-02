"""Turn a wallet into a WalletAlert.

Combines three signals:

  RandomForest      supervised probability that the wallet is illicit
  IsolationForest   unsupervised anomaly score, fitted on clean wallets only
  Domain rules      four hand-written detectors, as flags in the vector and as
                    the source of the plain-English reasons

Both models take the wallet feature space from feature_extraction.py, NOT the
Elliptic space from ml_baseline.py. Passing an Elliptic-fitted model here
raises a clear error rather than producing a meaningless number - see
_check_feature_space below.
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import pandas as pd

from app.models import Severity, WalletAlert
from app.services import domain_rules, explainability, wallet_model
from app.services.feature_extraction import (
    FEATURE_NAMES,
    extract_wallet_features,
)
from app.services.graph_builder import NODE_TYPE_WALLET

# How the two models are combined into one number.
#
# A weighted average, with the supervised model dominant. The RandomForest is
# trained on labelled examples of the exact patterns we care about and is the
# better estimator when the behaviour is one it has seen. The IsolationForest
# contributes a quarter of the weight as a hedge: it is fitted only on clean
# wallets, so it can raise the score for something merely *unusual* that the
# forest has no example of. Taking the maximum instead was rejected - it lets a
# single noisy anomaly score drive the alert, and on the held-out seed the
# isolation forest's precision is much the weaker of the two.
RF_WEIGHT = 0.75
ISO_WEIGHT = 0.25

SEVERITY_THRESHOLDS = [
    (0.80, Severity.CRITICAL),
    (0.55, Severity.HIGH),
    (0.30, Severity.MEDIUM),
]

MAX_CONNECTED_WALLETS = 50


def _check_feature_space(model, manifest: dict | None) -> None:
    """Fail loudly if the model was fitted on a different feature space."""
    expected = len(wallet_model.ALL_FEATURE_NAMES)
    n = getattr(model, "n_features_in_", None)
    if n is not None and n != expected:
        space = (manifest or {}).get("feature_space", "unknown")
        raise ValueError(
            f"This model expects {n} features (feature space: {space}), but the "
            f"wallet feature space has {expected}. The Elliptic models from "
            f"ml_baseline.py cannot score wallets - use "
            f"app.services.wallet_model.load() instead."
        )


def severity_for(risk_score: float) -> Severity:
    for threshold, severity in SEVERITY_THRESHOLDS:
        if risk_score >= threshold:
            return severity
    return Severity.LOW


def _confidence(rf_prob: float, iso_norm: float, rules_fired: int) -> float:
    """How much to trust this score.

    Three ingredients, because "confident" means different things:
      decisiveness - the forest is far from the 0.5 fence rather than on it
      agreement    - the two models, trained differently, say the same thing
      corroboration- at least one hand-written rule found concrete evidence

    A wallet the forest is sure about, the anomaly detector agrees with, and a
    rule can point at, is one an analyst can open first.
    """
    decisiveness = abs(rf_prob - 0.5) * 2
    agreement = 1.0 - abs(rf_prob - iso_norm)
    corroboration = 1.0 if rules_fired else 0.0
    score = 0.5 * decisiveness + 0.3 * agreement + 0.2 * corroboration
    return round(min(max(score, 0.0), 1.0), 4)


def _normalise_anomaly(raw: float, manifest: dict | None) -> float:
    """Map the isolation forest's score into [0, 1] using the training range."""
    lo, hi = 0.0, 1.0
    if manifest:
        span = manifest.get("anomaly_score_range")
        if span and len(span) == 2 and span[1] > span[0]:
            lo, hi = float(span[0]), float(span[1])
    if hi <= lo:
        return 0.0
    return min(max((raw - lo) / (hi - lo), 0.0), 1.0)


def connected_wallets(graph: nx.DiGraph, wallet: str) -> list[str]:
    """Wallet addresses directly linked to this one, IP nodes excluded."""
    neighbours = set(graph.predecessors(wallet)) | set(graph.successors(wallet))
    return sorted(
        n for n in neighbours
        if graph.nodes[n].get("type") == NODE_TYPE_WALLET
    )[:MAX_CONNECTED_WALLETS]


def score_wallet(
    graph: nx.DiGraph,
    wallet: str,
    rf_model,
    iso_model,
    manifest: dict | None = None,
    explain: bool = True,
) -> dict[str, Any]:
    """Score one wallet and return a dict matching the WalletAlert schema.

    `explain` can be turned off for bulk scoring, where building SHAP
    attributions for every wallet in a graph is wasted work until someone
    actually opens one.
    """
    if wallet not in graph:
        raise KeyError(f"Wallet {wallet!r} is not present in the graph")
    _check_feature_space(rf_model, manifest)

    features = extract_wallet_features(graph, wallet)
    findings = domain_rules.run_all_rules(graph, wallet)
    fired = {f["rule"] for f in findings}
    rule_flags = {f"rule_{name}": int(name in fired) for name in domain_rules.ALL_RULES}

    vector = [features[name] for name in FEATURE_NAMES] + [
        float(rule_flags[name]) for name in wallet_model.RULE_FEATURE_NAMES
    ]

    # The models were fitted on a named DataFrame. Scoring with a bare list
    # makes sklearn warn and silently trusts positional order; passing the
    # names back means a reordered feature list fails instead of mis-scoring.
    names = (manifest or {}).get("feature_columns") or wallet_model.ALL_FEATURE_NAMES
    row = pd.DataFrame([vector], columns=names)

    rf_prob = float(rf_model.predict_proba(row)[0][1])
    iso_norm = _normalise_anomaly(float(-iso_model.score_samples(row)[0]), manifest)

    risk_score = round(RF_WEIGHT * rf_prob + ISO_WEIGHT * iso_norm, 4)
    severity = severity_for(risk_score)

    alert: dict[str, Any] = {
        "wallet_address": wallet,
        "risk_score": risk_score,
        "confidence": _confidence(rf_prob, iso_norm, len(fired)),
        "severity": severity.value,
        "top_reasons": [],
        "connected_wallets": connected_wallets(graph, wallet),
    }

    if explain:
        alert["top_reasons"] = explainability.explain_wallet(
            graph, wallet, rf_model, vector,
            features=features,
            rule_findings=findings,
            feature_names=list(names),
            reference=(manifest or {}).get("reference"),
            # Explain the verdict that was actually reached, so a low-risk
            # wallet is described by what makes it ordinary.
            verdict_illicit=rf_prob >= 0.5,
        )

    return alert


def score_wallet_alert(*args, **kwargs) -> WalletAlert:
    """score_wallet, validated against the Pydantic schema."""
    return WalletAlert.model_validate(score_wallet(*args, **kwargs))


def attach_explanations(
    graph: nx.DiGraph,
    alerts: list[dict[str, Any]],
    rf_model,
    manifest: dict | None = None,
) -> list[dict[str, Any]]:
    """Fill in `top_reasons` for any alert that lacks them, in one SHAP pass.

    Bulk scoring leaves explanations off, and ingest only precomputes the top
    of the list. Everything below that gets explained here, on demand. Doing it
    one wallet at a time is what makes that expensive: the API served a page of
    1,352 alerts by making 1,200 separate SHAP calls, which took minutes.

    Mutates the alerts in place (they are the cached objects) and returns them.
    """
    from app.services.feature_extraction import extract_all_wallets

    pending = [a for a in alerts if not a["top_reasons"]]
    if not pending:
        return alerts

    features = extract_all_wallets(graph)
    names = (manifest or {}).get("feature_columns") or wallet_model.ALL_FEATURE_NAMES
    addresses = [a["wallet_address"] for a in pending if a["wallet_address"] in features.index]
    if not addresses:
        return alerts

    findings = {w: domain_rules.run_all_rules(graph, w) for w in addresses}
    flags = pd.DataFrame(
        [
            {
                f"rule_{name}": int(any(f["rule"] == name for f in findings[w]))
                for name in domain_rules.ALL_RULES
            }
            for w in addresses
        ],
        index=pd.Index(addresses),
        columns=wallet_model.RULE_FEATURE_NAMES,
    )
    matrix = pd.concat([features.loc[addresses], flags], axis=1)[names]
    rf_probs = rf_model.predict_proba(matrix)[:, 1]

    reasons = explainability.explain_many(
        rf_model,
        matrix,
        [features.loc[w].to_dict() for w in addresses],
        [findings[w] for w in addresses],
        [float(p) >= 0.5 for p in rf_probs],
        reference=(manifest or {}).get("reference"),
    )

    by_address = {a["wallet_address"]: a for a in pending}
    for address, alert_reasons in zip(addresses, reasons):
        by_address[address]["top_reasons"] = alert_reasons
    return alerts


def score_all_wallets(
    graph: nx.DiGraph,
    rf_model,
    iso_model,
    manifest: dict | None = None,
    explain: bool = False,
    explain_top: int = 0,
) -> list[dict[str, Any]]:
    """Score every wallet in the graph, highest risk first.

    `explain_top` builds explanations for the N riskiest wallets only, in one
    batched SHAP pass. That is what the API uses on ingest: nobody reads the
    reasons on the nine-hundredth row of a list sorted by risk, and the rest
    are built on demand when a wallet is opened.

    Predictions are batched rather than made one wallet at a time. A single-row
    call to a 300-tree forest is dominated by per-call overhead, so scoring
    1,332 wallets individually took about 90 ms each - two minutes for one
    ingest. Extracting the whole feature matrix and predicting once brings that
    down by more than an order of magnitude, and the domain rules become the
    remaining cost.

    Explanations are off by default: the API builds them on demand when a
    wallet is actually opened, rather than for a thousand nobody will look at.
    """
    from app.services.feature_extraction import extract_all_wallets

    _check_feature_space(rf_model, manifest)

    features = extract_all_wallets(graph)
    if features.empty:
        return []

    wallets = list(features.index)
    # Keep the findings, not just the flags - the explanation layer needs the
    # reasons, and running every rule a second time to recover them would cost
    # as much as the scoring itself.
    findings_by_wallet = {w: domain_rules.run_all_rules(graph, w) for w in wallets}
    flags = pd.DataFrame(
        [
            {
                f"rule_{name}": int(
                    any(f["rule"] == name for f in findings_by_wallet[w])
                )
                for name in domain_rules.ALL_RULES
            }
            for w in wallets
        ],
        index=features.index,
        columns=wallet_model.RULE_FEATURE_NAMES,
    )

    names = (manifest or {}).get("feature_columns") or wallet_model.ALL_FEATURE_NAMES
    matrix = pd.concat([features, flags], axis=1)[names]

    rf_probs = rf_model.predict_proba(matrix)[:, 1]
    iso_raw = -iso_model.score_samples(matrix)

    alerts: list[dict[str, Any]] = []
    for i, wallet in enumerate(wallets):
        rf_prob = float(rf_probs[i])
        iso_norm = _normalise_anomaly(float(iso_raw[i]), manifest)
        risk_score = round(RF_WEIGHT * rf_prob + ISO_WEIGHT * iso_norm, 4)
        fired = int(flags.iloc[i].sum())

        alert: dict[str, Any] = {
            "wallet_address": wallet,
            "risk_score": risk_score,
            "confidence": _confidence(rf_prob, iso_norm, fired),
            "severity": severity_for(risk_score).value,
            "top_reasons": [],
            "connected_wallets": connected_wallets(graph, wallet),
        }
        alert["_rf_prob"] = rf_prob
        alerts.append(alert)

    alerts.sort(key=lambda a: a["risk_score"], reverse=True)

    wanted = len(alerts) if explain else min(max(explain_top, 0), len(alerts))
    if wanted:
        chosen = alerts[:wanted]
        addresses = [a["wallet_address"] for a in chosen]
        reasons = explainability.explain_many(
            rf_model,
            matrix.loc[addresses],
            [features.loc[w].to_dict() for w in addresses],
            [findings_by_wallet[w] for w in addresses],
            [a["_rf_prob"] >= 0.5 for a in chosen],
            reference=(manifest or {}).get("reference"),
        )
        for alert, alert_reasons in zip(chosen, reasons):
            alert["top_reasons"] = alert_reasons

    for alert in alerts:
        alert.pop("_rf_prob", None)
    return alerts
