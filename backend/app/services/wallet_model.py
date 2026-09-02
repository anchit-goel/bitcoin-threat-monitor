"""Train the models that actually score wallets.

WHY THIS EXISTS. The Phase 3 models in ml_baseline.py are fitted on Elliptic's
166 anonymised columns. The wallets we score are described by the ten named
graph features in feature_extraction.py plus four rule flags. The two feature
spaces have no mapping between them - Elliptic's columns cannot be recomputed
from our graph, and ours cannot be expressed in theirs - so the Elliptic models
cannot score our wallets. They remain a reported benchmark; these models do the
work.

AVOIDING A CIRCULAR EVALUATION. Labels come from data/ground_truth.json, which
records the patterns the generator planted. Training and testing on the same
generated dataset would report memorisation as accuracy. So training and
evaluation use *separately seeded* datasets: different wallets, different
addresses, different injected patterns, same generating process. Every figure
this module prints is from wallets it has never seen.

Run it after the synthetic dataset exists:

    python -m app.services.wallet_model
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from app.services import domain_rules
from app.services.data_generator import build_dataset
from app.services.feature_extraction import FEATURE_NAMES, extract_all_wallets
from app.services.graph_builder import build_graph

MODEL_DIR = Path(__file__).resolve().parents[1] / "models_trained"

RULE_FEATURE_NAMES = [f"rule_{name}" for name in domain_rules.ALL_RULES]
ALL_FEATURE_NAMES = FEATURE_NAMES + RULE_FEATURE_NAMES

TRAIN_SEED = 1337
EVAL_SEED = 20260902  # deliberately unrelated to the training seed
RANDOM_STATE = 1337

RF_PATH = "wallet_rf.joblib"
ISO_PATH = "wallet_isolation_forest.joblib"
MANIFEST_PATH = "wallet_manifest.json"


# --------------------------------------------------------------------------
# Dataset assembly
# --------------------------------------------------------------------------


def build_labelled_frame(
    seed: int, n_normal: int = 5_000, verbose: bool = True
) -> pd.DataFrame:
    """Generate a dataset, build its graph, and return features plus labels.

    The returned frame is indexed by wallet address, carries ALL_FEATURE_NAMES
    as columns, and a `label` column that is 1 for wallets the generator
    planted and 0 for the rest.
    """
    transactions, ground_truth = build_dataset(n_normal=n_normal, seed=seed)
    graph = build_graph(transactions)

    features = extract_all_wallets(graph)

    # Rule flags, evaluated per wallet on the same graph.
    flags = pd.DataFrame(
        [domain_rules.rule_vector(graph, w) for w in features.index],
        index=features.index,
        columns=RULE_FEATURE_NAMES,
    )

    guilty = set(ground_truth["guilty_wallets"])
    frame = pd.concat([features, flags], axis=1)
    frame["label"] = [1 if w in guilty else 0 for w in frame.index]

    if verbose:
        print(
            f"  seed {seed}: {len(transactions):,} transactions, "
            f"{len(frame):,} wallets, {int(frame['label'].sum())} planted "
            f"({frame['label'].mean():.1%})"
        )
    return frame


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------


def _report(y_true, y_pred, y_prob, title: str) -> dict:
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = cm.ravel()
    metrics = {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, y_prob)),
        "average_precision": float(average_precision_score(y_true, y_prob)),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }
    print(f"  {title}")
    print(
        "    precision {precision:.3f}   recall {recall:.3f}   F1 {f1:.3f}   "
        "ROC AUC {roc_auc:.3f}".format(**metrics)
    )
    print(f"    TP {tp}  FP {fp}  FN {fn}  TN {tn}")
    return metrics


def train(
    n_normal: int = 5_000,
    train_seed: int = TRAIN_SEED,
    eval_seed: int = EVAL_SEED,
    model_dir: Path | str = MODEL_DIR,
) -> dict:
    """Fit both models on one seeded dataset and score them on another."""
    print("=" * 74)
    print("WALLET-SPACE MODEL")
    print("=" * 74)
    print("  Training and evaluation datasets are separately seeded, so every")
    print("  figure below is measured on wallets the model has never seen.")
    print()

    train_df = build_labelled_frame(train_seed, n_normal)
    eval_df = build_labelled_frame(eval_seed, n_normal)
    print()

    X_train = train_df[ALL_FEATURE_NAMES]
    y_train = train_df["label"]
    X_eval = eval_df[ALL_FEATURE_NAMES]
    y_eval = eval_df["label"]

    rf = RandomForestClassifier(
        n_estimators=300,
        min_samples_leaf=2,
        class_weight="balanced",
        n_jobs=-1,
        random_state=RANDOM_STATE,
    )
    rf.fit(X_train, y_train)

    rf_metrics = _report(
        y_eval, rf.predict(X_eval), rf.predict_proba(X_eval)[:, 1],
        "Random forest, on the held-out seed",
    )
    print()

    # The unsupervised model is fitted on the clean majority only. Trained on
    # everything, it would learn the planted patterns as ordinary and stop
    # calling them anomalous, which defeats the purpose of having it.
    iso = IsolationForest(
        n_estimators=300,
        contamination=float(max(y_train.mean(), 0.005)),
        n_jobs=-1,
        random_state=RANDOM_STATE,
    )
    iso.fit(X_train[y_train == 0])

    train_scores = -iso.score_samples(X_train)
    eval_scores = -iso.score_samples(X_eval)
    # Percentiles rather than min/max: one extreme wallet should not compress
    # every other score into the bottom of the range.
    lo, hi = float(np.percentile(train_scores, 1)), float(np.percentile(train_scores, 99))

    iso_metrics = {
        "roc_auc": float(roc_auc_score(y_eval, eval_scores)),
        "average_precision": float(average_precision_score(y_eval, eval_scores)),
        "score_p1": lo,
        "score_p99": hi,
        "contamination": float(max(y_train.mean(), 0.005)),
    }
    print("  Isolation forest, on the held-out seed (fitted on clean wallets only)")
    print(
        f"    ROC AUC {iso_metrics['roc_auc']:.3f}   "
        f"avg precision {iso_metrics['average_precision']:.3f} "
        f"(baseline {y_eval.mean():.3f})"
    )
    print()

    importances = sorted(
        zip(ALL_FEATURE_NAMES, rf.feature_importances_),
        key=lambda kv: kv[1], reverse=True,
    )
    print("  Feature importance")
    for name, score in importances:
        bar = "#" * max(1, round(score * 60)) if score > 0.004 else ""
        print(f"    {name:<24}{score:.4f}  {bar}")
    print()

    # How much of the work is the ML doing versus the rules? Refit without the
    # rule flags to find out - a fair question to be able to answer on stage.
    rf_nr = RandomForestClassifier(
        n_estimators=300, min_samples_leaf=2, class_weight="balanced",
        n_jobs=-1, random_state=RANDOM_STATE,
    )
    rf_nr.fit(X_train[FEATURE_NAMES], y_train)
    graph_only = _report(
        y_eval, rf_nr.predict(X_eval[FEATURE_NAMES]),
        rf_nr.predict_proba(X_eval[FEATURE_NAMES])[:, 1],
        "Random forest on graph features ALONE, no rule flags",
    )
    print()

    # Reference point for the explanation layer: is this wallet's value high or
    # low compared with a typical wallet? A SHAP sign cannot answer that - it
    # says how the feature moved the prediction, not where the value sits.
    reference = {
        "median": {c: float(X_train[c].median()) for c in ALL_FEATURE_NAMES},
        "p90": {c: float(X_train[c].quantile(0.90)) for c in ALL_FEATURE_NAMES},
    }

    metrics = {
        "random_forest": rf_metrics,
        "random_forest_graph_features_only": graph_only,
        "isolation_forest": iso_metrics,
        "feature_importance": [
            {"feature": n, "importance": float(s)} for n, s in importances
        ],
        "train": {
            "seed": train_seed,
            "wallets": len(train_df),
            "planted": int(y_train.sum()),
        },
        "eval": {
            "seed": eval_seed,
            "wallets": len(eval_df),
            "planted": int(y_eval.sum()),
        },
    }

    paths = save(rf, iso, metrics, Path(model_dir), reference)
    print("=" * 74)
    print("SUMMARY (held-out seed)")
    print("=" * 74)
    print(
        f"  Random forest        precision {rf_metrics['precision']:.3f}  "
        f"recall {rf_metrics['recall']:.3f}  F1 {rf_metrics['f1']:.3f}"
    )
    print(
        f"  Without rule flags   precision {graph_only['precision']:.3f}  "
        f"recall {graph_only['recall']:.3f}  F1 {graph_only['f1']:.3f}"
    )
    print(f"  Isolation forest     ROC AUC {iso_metrics['roc_auc']:.3f}")
    for name, path in paths.items():
        print(f"  -> {path}")
    print("=" * 74)
    return metrics


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def save(
    rf, iso, metrics: dict, model_dir: Path = MODEL_DIR,
    reference: dict | None = None,
) -> dict[str, Path]:
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    rf_path, iso_path = model_dir / RF_PATH, model_dir / ISO_PATH
    manifest_path = model_dir / MANIFEST_PATH

    joblib.dump(rf, rf_path)
    joblib.dump(iso, iso_path)
    manifest_path.write_text(
        json.dumps(
            {
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "feature_space": "wallet_graph",
                "n_features": len(ALL_FEATURE_NAMES),
                "feature_columns": ALL_FEATURE_NAMES,
                "graph_features": FEATURE_NAMES,
                "rule_features": RULE_FEATURE_NAMES,
                "anomaly_score_range": [
                    metrics["isolation_forest"]["score_p1"],
                    metrics["isolation_forest"]["score_p99"],
                ],
                "random_state": RANDOM_STATE,
                "reference": reference or {},
                "metrics": metrics,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"rf": rf_path, "iso": iso_path, "manifest": manifest_path}


def load(model_dir: Path | str = MODEL_DIR):
    """Load the wallet-space models and their manifest."""
    model_dir = Path(model_dir)
    rf_path, iso_path = model_dir / RF_PATH, model_dir / ISO_PATH
    manifest_path = model_dir / MANIFEST_PATH
    if not rf_path.exists() or not iso_path.exists():
        raise FileNotFoundError(
            f"No wallet-space models in {model_dir}. Run "
            f"`python -m app.services.wallet_model` first."
        )
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"feature_columns": ALL_FEATURE_NAMES, "anomaly_score_range": [0.0, 1.0]}
    )
    return joblib.load(rf_path), joblib.load(iso_path), manifest


if __name__ == "__main__":
    train()
