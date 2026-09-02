"""Turn a model score into sentences an analyst can act on.

Two sources of explanation, in priority order:

1. Triggered domain rules. These are already explanations - a rule that fires
   names the behaviour, the wallets, the amounts and the times. They go first
   because they are evidence, not attribution.
2. SHAP attributions over the graph features. These say which measurements the
   model leaned on for *this* wallet, which is why the feature space had to be
   one with named columns. "local_78 contributed 0.03" is not an explanation;
   "14 transfers an hour" is.

TWO THINGS THAT ARE EASY TO GET WRONG HERE, both of which produced nonsense in
an earlier version of this module:

A SHAP sign is not a high/low reading. It says how a feature moved the
prediction, not where the value sits. A *low* clustering coefficient pushes
strongly toward illicit, because purpose-built wallets have counterparties that
never deal with each other - so keying the wording off the sign produced "its
counterparties transact with each other too (0.03 clustering)" about a wallet
whose clustering was near zero. The wording is therefore chosen by comparing
the value against the training median, and SHAP only decides which features are
worth mentioning and in what order.

An explanation must agree with its verdict. Ranking by absolute contribution
put "low transaction frequency" in the reasons for a CRITICAL wallet, because
that feature was influential even though it argued the other way. Only
contributions pointing the same way as the verdict are reported.

A last note on what SHAP does not say: it apportions this one prediction
between features. It does not establish that high velocity causes crime. The
sentences below are observations about the wallet and the reason it was
noticed, and stop there.
"""

from __future__ import annotations

import math
from typing import Any, Callable

import networkx as nx
import numpy as np

from app.services import domain_rules
from app.services.feature_extraction import FEATURE_NAMES

_EXPLAINER_ATTR = "_shap_explainer"

MAX_SHAP_REASONS = 3
RULE_FEATURE_PREFIX = "rule_"

# Fallback medians, used when no trained reference is available. Values come
# from the clean majority of a generated dataset.
DEFAULT_MEDIANS: dict[str, float] = {
    "degree_centrality": 0.006,
    "in_degree": 4.0,
    "out_degree": 4.0,
    "pagerank": 0.0004,
    "clustering_coefficient": 0.15,
    "transaction_velocity": 0.05,
    "total_sent": 0.35,
    "total_received": 0.35,
    "amount_variance": 0.01,
    "fan_in_out_ratio": 1.0,
}


def _swing(variance: float) -> float:
    """Variance is in BTC squared, which means nothing to a reader.

    The square root is a typical deviation in BTC, which does.
    """
    return math.sqrt(max(variance, 0.0))


# `above` is the wording when the wallet's value sits above a typical wallet's,
# `below` when it sits under. Which one appears is decided by the value, never
# by the SHAP sign.
FEATURE_PHRASES: dict[str, dict[str, Callable[[float], str]]] = {
    "transaction_velocity": {
        "above": lambda v: (
            f"Unusually high transaction frequency: {v:,.1f} transfers per hour"
            if v >= 1 else
            f"Slightly more active than a typical wallet ({v:,.2f} transfers per hour)"
        ),
        "below": lambda v: f"Low transaction frequency ({v:,.2f} per hour)",
    },
    "amount_variance": {
        "above": lambda v: (
            f"Transfer sizes swing wildly — typically by {_swing(v):,.3f} BTC, "
            f"mixing large movements with small ones"
            if _swing(v) >= 1.0 else
            f"Transfer sizes vary more than usual (typically by {_swing(v):,.4f} BTC)"
        ),
        "below": lambda v: f"Consistent transfer sizes (varying by {_swing(v):,.4f} BTC)",
    },
    "in_degree": {
        "above": lambda v: f"Receives from {v:,.0f} different wallets",
        "below": lambda v: (
            "Funded by a single source, characteristic of a purpose-made wallet "
            "rather than one in general use"
            if v <= 1 else f"Funded by only {v:,.0f} sources"
        ),
    },
    "out_degree": {
        "above": lambda v: f"Pays out to {v:,.0f} different wallets",
        "below": lambda v: (
            "Sends to a single destination, consistent with a relay in a chain"
            if v <= 1 else f"Sends to only {v:,.0f} destinations"
        ),
    },
    "degree_centrality": {
        "above": lambda v: f"Well connected within the transaction network ({v:.3f} centrality)",
        "below": lambda v: (
            "Sits at the edge of the network rather than within it, as "
            "single-purpose wallets do"
        ),
    },
    "clustering_coefficient": {
        "above": lambda v: f"Its counterparties deal with each other too ({v:.2f} clustering)",
        "below": lambda v: (
            "Its counterparties never deal with each other — the star shape of "
            "a wallet built for one job"
        ),
    },
    "pagerank": {
        "above": lambda v: f"Carries disproportionate value flow for its size ({v:.5f} PageRank)",
        "below": lambda v: f"Peripheral to the network's value flow ({v:.5f} PageRank)",
    },
    "total_sent": {
        "above": lambda v: f"Moved {v:,.4f} BTC out",
        "below": lambda v: f"Sent little ({v:,.4f} BTC)",
    },
    "total_received": {
        "above": lambda v: f"Took in {v:,.4f} BTC",
        "below": lambda v: f"Received little ({v:,.4f} BTC)",
    },
    "fan_in_out_ratio": {
        "above": lambda v: f"Takes in from more sources than it pays out to (ratio {v:,.2f})",
        "below": lambda v: f"Pays out more widely than it takes in (ratio {v:,.2f})",
    },
}


# --------------------------------------------------------------------------
# SHAP
# --------------------------------------------------------------------------


def _get_explainer(rf_model):
    """Build the TreeExplainer once and keep it on the model.

    Constructing it walks all 300 trees; doing that per wallet would dominate
    the cost of scoring a graph.
    """
    explainer = getattr(rf_model, _EXPLAINER_ATTR, None)
    if explainer is None:
        import shap

        explainer = shap.TreeExplainer(rf_model)
        setattr(rf_model, _EXPLAINER_ATTR, explainer)
    return explainer


def illicit_shap_matrix(rf_model, frame) -> np.ndarray | None:
    """SHAP values for the illicit class over a whole frame: (rows, features).

    Taking the batch in one call matters. Explaining 150 wallets one at a time
    added 21 seconds to an ingest; one call over the same 150 rows costs a
    fraction of that, because the per-call overhead of walking a 300-tree
    forest is paid once instead of 150 times.

    Returns None if SHAP cannot run, so scoring degrades to rule-only reasons
    rather than failing outright.
    """
    try:
        explainer = _get_explainer(rf_model)
        raw = explainer.shap_values(frame, check_additivity=False)
    except Exception:
        return None

    values = np.asarray(raw)
    # shap returns either (rows, features, classes) or a per-class list,
    # depending on version; normalise both to the illicit column.
    if values.ndim == 3:
        return values[:, :, -1]
    if values.ndim == 2:
        return values
    if isinstance(raw, list) and raw:
        return np.asarray(raw[-1])
    return None


def _illicit_shap_values(rf_model, feature_vector, feature_names) -> np.ndarray | None:
    """SHAP values for one wallet."""
    import pandas as pd

    matrix = illicit_shap_matrix(
        rf_model, pd.DataFrame([feature_vector], columns=feature_names)
    )
    return None if matrix is None else matrix[0]


def reasons_from_shap(
    shap_row,
    features: dict[str, float],
    names: list[str],
    medians: dict[str, float],
    verdict_illicit: bool,
) -> list[str]:
    """Turn one row of SHAP values into sentences."""
    if shap_row is None:
        return []

    candidates = []
    for i, name in enumerate(names):
        if i >= len(shap_row) or name.startswith(RULE_FEATURE_PREFIX):
            continue
        contribution = float(shap_row[i])
        # Only reasons that argue the same way as the verdict.
        if verdict_illicit and contribution <= 0:
            continue
        if not verdict_illicit and contribution >= 0:
            continue
        candidates.append((name, contribution))

    candidates.sort(key=lambda kv: abs(kv[1]), reverse=True)

    out: list[str] = []
    for name, contribution in candidates[:MAX_SHAP_REASONS]:
        phrases = FEATURE_PHRASES.get(name)
        if not phrases or abs(contribution) < 1e-6:
            continue
        value = float(features.get(name, 0.0))
        # The value decides the wording, not the attribution's sign.
        side = "above" if value > float(medians.get(name, 0.0)) else "below"
        sentence = phrases[side](value)
        if sentence not in out:
            out.append(sentence)
    return out


def explain_many(
    rf_model,
    frame,
    feature_dicts: list[dict[str, float]],
    findings_per_wallet: list[list[dict[str, Any]]],
    verdicts: list[bool],
    reference: dict[str, Any] | None = None,
    max_reasons: int = 6,
) -> list[list[str]]:
    """Explain a batch of wallets with a single SHAP pass.

    `frame` carries one row per wallet in the same order as the other lists.
    """
    medians = (reference or {}).get("median") or DEFAULT_MEDIANS
    names = list(frame.columns)
    matrix = illicit_shap_matrix(rf_model, frame)

    results: list[list[str]] = []
    for i, features in enumerate(feature_dicts):
        findings = findings_per_wallet[i]
        reasons = [f["reason"] for f in findings]

        if not verdicts[i] and not findings:
            results.append(_benign_summary(features)[:max_reasons])
            continue

        row = None if matrix is None else matrix[i]
        for sentence in reasons_from_shap(row, features, names, medians, verdicts[i]):
            if sentence not in reasons:
                reasons.append(sentence)

        if not reasons:
            reasons.append("Flagged by the model, but no single feature stood out")
        results.append(reasons[:max_reasons])
    return results


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _benign_summary(features: dict[str, float]) -> list[str]:
    """A factual profile for a wallet nothing was found against.

    Running the SHAP path for a low-risk verdict reports the features that
    argued *for* innocence, but the wording for those features carries
    suspicious connotations - it once described a clean wallet as having
    transfer sizes that "swing wildly". A wallet with nothing against it
    deserves a description, not a defence.
    """
    in_deg = features.get("in_degree", 0.0)
    out_deg = features.get("out_degree", 0.0)
    sent = features.get("total_sent", 0.0)
    received = features.get("total_received", 0.0)
    velocity = features.get("transaction_velocity", 0.0)

    counterparties = int(in_deg + out_deg)
    reasons = [
        "No laundering pattern found: none of the peel-chain, structuring, "
        "layering or round-trip detectors matched this wallet"
    ]
    if counterparties:
        reasons.append(
            f"Ordinary connectivity — {int(in_deg):,} incoming and "
            f"{int(out_deg):,} outgoing counterparties"
        )
    if sent or received:
        reasons.append(
            f"Moved {sent:,.4f} BTC out and took in {received:,.4f} BTC, "
            f"at {velocity:,.2f} transfers per hour"
        )
    return reasons


def explain_wallet(
    graph: nx.DiGraph,
    wallet: str,
    rf_model,
    feature_vector: list[float],
    features: dict[str, float] | None = None,
    rule_findings: list[dict[str, Any]] | None = None,
    feature_names: list[str] | None = None,
    reference: dict[str, Any] | None = None,
    verdict_illicit: bool = True,
    max_reasons: int = 6,
) -> list[str]:
    """Plain-English reasons this wallet was scored the way it was.

    Rule findings come first as direct evidence; SHAP attributions over the
    named graph features follow, most influential first.

    `verdict_illicit` keeps the reasons pointing the same way as the score - a
    critical wallet is not explained by the things arguing it is innocent.
    """
    reasons: list[str] = []

    findings = (
        rule_findings
        if rule_findings is not None
        else domain_rules.run_all_rules(graph, wallet)
    )
    reasons.extend(f["reason"] for f in findings)

    if features is None:
        features = dict(zip(FEATURE_NAMES, feature_vector))

    # Nothing found and the model agrees: describe the wallet, do not argue.
    if not verdict_illicit and not findings:
        return _benign_summary(features)[:max_reasons]

    names = feature_names or list(FEATURE_NAMES)
    medians = (reference or {}).get("median") or DEFAULT_MEDIANS

    shap_values = _illicit_shap_values(rf_model, feature_vector, names)
    for sentence in reasons_from_shap(
        shap_values, features, names, medians, verdict_illicit
    ):
        if sentence not in reasons:
            reasons.append(sentence)

    if not reasons:
        reasons.append(
            "Nothing unusual found in this wallet's activity"
            if not verdict_illicit
            else "Flagged by the model, but no single feature stood out"
        )

    return reasons[:max_reasons]
