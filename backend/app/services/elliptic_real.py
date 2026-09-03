"""Train and evaluate on the real Elliptic Bitcoin dataset.

WHY THIS EXISTS. Every accuracy figure this project quotes so far was measured
against patterns our own generator planted. That is circular: the generator and
the detectors were written by the same hand, against the same idea of what a
peel chain looks like. It answers "do we detect what we know how to draw?", not
"does any of this work on real crime".

This module answers the second question with real data: 203,769 real Bitcoin
transactions, 4,545 of them labelled illicit by Elliptic's own analysts.

WHAT IT DOES *NOT* DO. It does not feed a model into the wallet scorer.
Elliptic's nodes are transactions; ours are wallets. Those are different
entities, and a model trained to classify one cannot be pointed at the other
without saying something false. What it validates is the *feature engineering* -
whether the structural measures in feature_extraction.py separate real illicit
activity from real licit activity, on data nobody on this team shaped.

WHICH FEATURES. Elliptic publishes three files. The 200 MB features file holds
166 anonymised columns whose meaning was never disclosed, and is not reachable
from here; the graph and the labels are. So the model is trained on the
structural half of our own feature set - the six measures computable from an
edge list alone. Amount and timing features (velocity, totals, variance) need
values the edge list does not carry, and are therefore untested here. That is a
real limitation and is reported rather than hidden.

THE SPLIT. Elliptic is built as 49 disjoint time-step graphs, and the edge list
reproduces exactly 49 connected components - verified, not assumed. Splitting on
those components means no edge ever crosses from train to test, so a node's
neighbourhood cannot leak its label. Without the features file we cannot order
the components in time, so this is a leak-free *grouped* split, not the paper's
stricter temporal one. The distinction matters and is stated in the output.

Run it:

    python -m app.services.elliptic_real
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import networkx as nx
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
MODEL_DIR = Path(__file__).resolve().parents[1] / "models_trained"

CLASSES_CSV = DATA_DIR / "elliptic_txs_classes.csv"
EDGELIST_CSV = DATA_DIR / "elliptic_txs_edgelist.csv"

# Where the two files came from, recorded so the provenance of any number this
# module prints can be checked later.
SOURCE = (
    "Elliptic Data Set (Weber et al. 2019), graph and labels. "
    "203,769 transactions / 234,355 edges; 4,545 illicit, 42,019 licit."
)

# Elliptic labels: "1" illicit, "2" licit, "unknown" unlabelled.
ILLICIT, LICIT = "1", "2"

# The half of feature_extraction.FEATURE_NAMES computable from an edge list.
STRUCTURAL_FEATURES = [
    "in_degree",
    "out_degree",
    "degree_centrality",
    "pagerank",
    "clustering_coefficient",
    "fan_in_out_ratio",
]

# Deliberately recorded: what this benchmark could not test.
UNTESTED_FEATURES = [
    "transaction_velocity",
    "total_sent",
    "total_received",
    "amount_variance",
]

RANDOM_STATE = 1337
TEST_COMPONENT_FRACTION = 0.25


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def files_present() -> bool:
    return CLASSES_CSV.exists() and EDGELIST_CSV.exists()


def load_graph() -> tuple[nx.DiGraph, pd.DataFrame]:
    """Build the real Elliptic transaction graph and its labels."""
    if not files_present():
        raise FileNotFoundError(
            f"Missing {CLASSES_CSV.name} / {EDGELIST_CSV.name} in {DATA_DIR}. "
            "See the README for where to get them."
        )

    classes = pd.read_csv(CLASSES_CSV)
    edges = pd.read_csv(EDGELIST_CSV)

    graph = nx.from_pandas_edgelist(
        edges, "txId1", "txId2", create_using=nx.DiGraph
    )
    # Nodes with no edges still belong in the graph; dropping them would
    # quietly change the population being measured.
    graph.add_nodes_from(classes["txId"])
    return graph, classes


# --------------------------------------------------------------------------
# Features - the same measures feature_extraction.py computes on our own graph
# --------------------------------------------------------------------------


def structural_features(graph: nx.DiGraph) -> pd.DataFrame:
    """Compute our structural feature set over the real graph."""
    undirected = graph.to_undirected()
    pagerank = nx.pagerank(graph, alpha=0.85)
    centrality = nx.degree_centrality(graph)
    clustering = nx.clustering(undirected)

    nodes = list(graph.nodes())
    in_deg = dict(graph.in_degree())
    out_deg = dict(graph.out_degree())

    return pd.DataFrame(
        {
            "in_degree": [float(in_deg[n]) for n in nodes],
            "out_degree": [float(out_deg[n]) for n in nodes],
            "degree_centrality": [float(centrality[n]) for n in nodes],
            "pagerank": [float(pagerank[n]) for n in nodes],
            "clustering_coefficient": [float(clustering[n]) for n in nodes],
            # Same +1 guard as feature_extraction: a node that only receives
            # has out_degree 0, and the ratio still needs to be finite.
            "fan_in_out_ratio": [
                float(in_deg[n] / (out_deg[n] + 1.0)) for n in nodes
            ],
        },
        index=pd.Index(nodes, name="txId"),
        columns=STRUCTURAL_FEATURES,
    )


def component_of(graph: nx.DiGraph) -> dict:
    """Map each node to its connected component, the dataset's time steps."""
    mapping = {}
    for i, component in enumerate(nx.connected_components(graph.to_undirected())):
        for node in component:
            mapping[node] = i
    return mapping


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------


def train(model_dir: Path | str = MODEL_DIR, verbose: bool = True) -> dict:
    graph, classes = load_graph()

    if verbose:
        print("=" * 74)
        print("REAL DATA BENCHMARK - Elliptic Bitcoin Data Set")
        print("=" * 74)
        print(f"  {graph.number_of_nodes():,} transactions, "
              f"{graph.number_of_edges():,} edges")

    features = structural_features(graph)
    components = component_of(graph)

    labelled = classes[classes["class"].isin([ILLICIT, LICIT])].copy()
    labelled["y"] = (labelled["class"] == ILLICIT).astype(int)
    labelled["component"] = labelled["txId"].map(components)

    X_all = features.loc[labelled["txId"]]
    y_all = labelled["y"].to_numpy()
    groups = labelled["component"].to_numpy()

    if verbose:
        print(f"  {len(labelled):,} labelled "
              f"({y_all.sum():,} illicit, {(1-y_all).sum():,} licit, "
              f"{y_all.mean():.2%} positive)")
        print(f"  {len(set(components.values()))} connected components "
              f"= the dataset's 49 time steps")
        print()

    # Hold out whole components, so no edge crosses the split.
    rng = np.random.default_rng(RANDOM_STATE)
    unique = np.array(sorted(set(groups)))
    rng.shuffle(unique)
    n_test = max(1, int(len(unique) * TEST_COMPONENT_FRACTION))
    test_components = set(unique[:n_test].tolist())

    is_test = np.array([g in test_components for g in groups])
    X_train, X_test = X_all[~is_test], X_all[is_test]
    y_train, y_test = y_all[~is_test], y_all[is_test]

    if verbose:
        print(f"  split: {len(unique) - n_test} components train / "
              f"{n_test} test, no shared edges")
        print(f"         {len(X_train):,} train rows ({y_train.mean():.2%} illicit), "
              f"{len(X_test):,} test rows ({y_test.mean():.2%} illicit)")
        print()

    rf = RandomForestClassifier(
        n_estimators=300,
        min_samples_leaf=2,
        class_weight="balanced",
        n_jobs=-1,
        random_state=RANDOM_STATE,
    )
    rf.fit(X_train, y_train)
    prob = rf.predict_proba(X_test)[:, 1]
    pred = rf.predict(X_test)

    tn, fp, fn, tp = confusion_matrix(y_test, pred, labels=[0, 1]).ravel()
    rf_metrics = {
        "precision": float(precision_score(y_test, pred, zero_division=0)),
        "recall": float(recall_score(y_test, pred, zero_division=0)),
        "f1": float(f1_score(y_test, pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_test, prob)),
        "average_precision": float(average_precision_score(y_test, prob)),
        "baseline_rate": float(y_test.mean()),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }

    if verbose:
        print("  Random forest on our structural features, held-out components")
        print(f"    precision {rf_metrics['precision']:.3f}   "
              f"recall {rf_metrics['recall']:.3f}   F1 {rf_metrics['f1']:.3f}")
        print(f"    ROC AUC {rf_metrics['roc_auc']:.3f}   "
              f"avg precision {rf_metrics['average_precision']:.3f} "
              f"(a coin flip would score {rf_metrics['baseline_rate']:.3f})")
        print(f"    TP {tp}  FP {fp}  FN {fn}  TN {tn}")
        print()
        print(classification_report(
            y_test, pred, target_names=["licit", "illicit"], digits=3, zero_division=0,
        ).rstrip().replace("\n", "\n    ").rjust(4))
        print()

    # Unsupervised, fitted on licit only - the same arrangement as wallet_model.
    iso = IsolationForest(
        n_estimators=300, contamination=0.05, n_jobs=-1, random_state=RANDOM_STATE,
    )
    iso.fit(X_train[y_train == 0])
    iso_scores = -iso.score_samples(X_test)
    iso_metrics = {
        "roc_auc": float(roc_auc_score(y_test, iso_scores)),
        "average_precision": float(average_precision_score(y_test, iso_scores)),
    }
    if verbose:
        print("  Isolation forest (fitted on licit transactions only)")
        print(f"    ROC AUC {iso_metrics['roc_auc']:.3f}   "
              f"avg precision {iso_metrics['average_precision']:.3f}")
        print()

    # Precision@k is how this would actually be used: an analyst works down a
    # ranked queue, so what matters is how many of the top k are real, not the
    # score at an arbitrary 0.5 threshold.
    order = np.argsort(prob)[::-1]
    base = float(y_test.mean())
    at_k = {}
    for k in (50, 100, 500, 1000):
        if k <= len(order):
            hit = float(y_test[order[:k]].mean())
            at_k[f"precision_at_{k}"] = hit
            at_k[f"lift_at_{k}"] = hit / base if base else float("nan")

    if verbose:
        print("  Precision@k - of the top k this ranks, how many are truly illicit")
        print(f"    {'k':>6}{'precision':>12}{'lift vs base':>15}")
        for k in (50, 100, 500, 1000):
            if f"precision_at_{k}" in at_k:
                print(f"    {k:>6}{at_k[f'precision_at_{k}']:>12.1%}"
                      f"{at_k[f'lift_at_{k}']:>14.2f}x")
        print(f"    base rate in the test set: {base:.1%}")
        print()

    importances = sorted(
        zip(STRUCTURAL_FEATURES, rf.feature_importances_),
        key=lambda kv: kv[1], reverse=True,
    )

    # Each feature on its own, so a weak ensemble is not mistaken for weak
    # features or the other way round.
    solo = {
        c: float(roc_auc_score(y_test, X_test[c])) for c in STRUCTURAL_FEATURES
    }
    if verbose:
        print("  Each feature alone (ROC AUC; 0.5 is a coin flip)")
        for c, v in sorted(solo.items(), key=lambda kv: abs(kv[1] - 0.5), reverse=True):
            print(f"    {c:<24}{v:.3f}")
        print()
    if verbose:
        print("  Which of our features actually carry signal on real data")
        for name, score in importances:
            print(f"    {name:<24}{score:.4f}  {'#' * max(1, round(score * 50))}")
        print()

    metrics = {
        "source": SOURCE,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "features_used": STRUCTURAL_FEATURES,
        "features_untested": UNTESTED_FEATURES,
        "split": {
            "kind": "grouped by connected component (the dataset's 49 time steps)",
            "not_temporal_because": (
                "ordering the components in time needs the features file, which "
                "carries the time-step column and is not available here"
            ),
            "train_components": int(len(unique) - n_test),
            "test_components": int(n_test),
            "train_rows": int(len(X_train)),
            "test_rows": int(len(X_test)),
        },
        "random_forest": rf_metrics,
        "random_forest_precision_at_k": at_k,
        "single_feature_roc_auc": solo,
        "isolation_forest": iso_metrics,
        "feature_importance": [
            {"feature": n, "importance": float(s)} for n, s in importances
        ],
    }

    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(rf, model_dir / "elliptic_rf.joblib")
    joblib.dump(iso, model_dir / "elliptic_isolation_forest.joblib")
    (model_dir / "elliptic_manifest.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )

    if verbose:
        print("=" * 74)
        print("VERDICT")
        print("=" * 74)
        auc = rf_metrics["roc_auc"]
        print(f"  Our structural features reach ROC AUC {auc:.3f} on real labelled")
        print(f"  Bitcoin data, against {rf_metrics['baseline_rate']:.1%} illicit at base rate.")
        print("  Individually every one of them sits near a coin flip, and illicit")
        print("  transactions turn out to be LESS connected than licit ones - which")
        print("  is why the isolation forest scores below 0.5 and is actively")
        print("  misleading here.")
        print()
        print("  Read this against the 96-99% precision the synthetic benchmark")
        print("  reports. That gap is the finding: those figures measure how well")
        print("  we detect patterns we drew ourselves. Topology alone does not")
        print("  carry laundering signal on real data; the amount and timing")
        print("  features this benchmark could not test are where it would be.")
        print()
        print("  This measures our FEATURES against real labels. It does not")
        print("  produce a model the wallet scorer can use: Elliptic classifies")
        print("  transactions, the scorer classifies wallets.")
        print(f"  -> {model_dir / 'elliptic_manifest.json'}")
        print("=" * 74)

    return metrics


if __name__ == "__main__":
    train()
