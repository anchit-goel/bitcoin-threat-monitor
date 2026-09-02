"""Supervised and unsupervised baselines on the Elliptic Bitcoin dataset.

Trains two models and saves them for the API to load at startup:

  RandomForestClassifier  supervised, illicit vs licit
  IsolationForest         unsupervised anomaly scoring, trained without labels

Run it directly once the three Elliptic CSVs are in /data:

    python -m app.services.ml_baseline

Dataset notes, because the file layout has two traps:

  elliptic_txs_features.csv  has NO header row. 167 columns: txId, time step,
      93 local features, 72 aggregated features. Reading it with a default
      header consumes the first transaction as column names.
  elliptic_txs_classes.csv   has a header. Labels are "1" for illicit, "2" for
      licit, and "unknown" for the ~77% that are unlabelled.

The split is temporal, on the time-step column, not random. The dataset is a
sequence of graph snapshots, and a wallet's neighbours appear in the same
snapshot; splitting at random puts a transaction's own neighbourhood on both
sides of the split and inflates every metric.

A NOTE FOR PHASE 5. These models take the Elliptic feature space: 166
anonymised columns whose meaning was never published. They cannot score a
wallet described by the graph features in feature_extraction.py - the two
feature spaces have nothing to do with each other, and passing a 15-column
graph vector to a model expecting 166 columns raises rather than mis-scores.
The manifest saved alongside the models records the exact schema so the
mismatch surfaces immediately. See the module docstring in scoring.py, or the
Phase 3 commit message, for the options.
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
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
MODEL_DIR = Path(__file__).resolve().parents[1] / "models_trained"

FEATURES_CSV = "elliptic_txs_features.csv"
CLASSES_CSV = "elliptic_txs_classes.csv"
EDGELIST_CSV = "elliptic_txs_edgelist.csv"

# The Elliptic paper's own split point: 34 of 49 time steps for training.
DEFAULT_SPLIT_TIME_STEP = 34

ID_COL = "txId"
TIME_COL = "time_step"
LABEL_COL = "label"

RANDOM_STATE = 1337


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _has_header(path: Path) -> bool:
    """Does this CSV start with a header row?

    The canonical features file has none, but some mirrors add one. Sniffing
    beats assuming: if the first field of the first row is not a number, it is
    a header.
    """
    with path.open(encoding="utf-8") as fh:
        first = fh.readline().split(",", 1)[0].strip().strip('"')
    try:
        float(first)
        return False
    except ValueError:
        return True


def load_elliptic(data_dir: Path | str = DATA_DIR) -> pd.DataFrame:
    """Load and merge the features and classes files.

    Returns every row, unknown labels included; filtering happens downstream so
    the caller can report how much of the data is unlabelled.
    """
    data_dir = Path(data_dir)
    features_path = data_dir / FEATURES_CSV
    classes_path = data_dir / CLASSES_CSV

    missing = [p.name for p in (features_path, classes_path) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing {', '.join(missing)} in {data_dir}.\n"
            f"Download the Elliptic Data Set from Kaggle and put "
            f"{FEATURES_CSV}, {CLASSES_CSV} and {EDGELIST_CSV} in that folder."
        )

    features = pd.read_csv(
        features_path, header=0 if _has_header(features_path) else None
    )
    # Name the columns positionally: id, time step, 93 local, the rest aggregated.
    n_local = 93
    names = [ID_COL, TIME_COL]
    names += [f"local_{i}" for i in range(1, n_local + 1)]
    names += [f"agg_{i}" for i in range(1, features.shape[1] - len(names) + 1)]
    if len(names) != features.shape[1]:
        raise ValueError(
            f"{FEATURES_CSV} has {features.shape[1]} columns; expected 167 "
            f"(txId, time step, 93 local, 72 aggregated)."
        )
    features.columns = names

    classes = pd.read_csv(classes_path)
    classes.columns = [ID_COL, "class"]

    merged = features.merge(classes, on=ID_COL, how="left")

    # 1 = illicit, 2 = licit, "unknown" = unlabelled -> 1 / 0 / NaN.
    as_text = merged["class"].astype(str).str.strip()
    label = pd.Series(
        np.where(as_text == "1", 1.0, np.where(as_text == "2", 0.0, np.nan)),
        index=merged.index,
        name=LABEL_COL,
    )
    # Concatenated rather than assigned: inserting a column into a 167-wide,
    # 200k-row frame fragments it and pandas rightly complains.
    return pd.concat([merged.drop(columns=["class"]), label], axis=1)


def feature_columns(df: pd.DataFrame) -> list[str]:
    """The model's input columns: everything but the id, time step and label."""
    return [c for c in df.columns if c not in (ID_COL, TIME_COL, LABEL_COL)]


def temporal_split(
    df: pd.DataFrame, split_time_step: int = DEFAULT_SPLIT_TIME_STEP
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split on the time step, keeping the future strictly out of training."""
    return (
        df[df[TIME_COL] <= split_time_step].copy(),
        df[df[TIME_COL] > split_time_step].copy(),
    )


# --------------------------------------------------------------------------
# Reporting helpers
# --------------------------------------------------------------------------


def _rule(char: str = "=", width: int = 74) -> str:
    return char * width


def describe(df: pd.DataFrame) -> dict:
    """Print the class distribution and basic stats, and return them."""
    labelled = df[df[LABEL_COL].notna()]
    illicit = int((labelled[LABEL_COL] == 1).sum())
    licit = int((labelled[LABEL_COL] == 0).sum())
    unknown = int(df[LABEL_COL].isna().sum())
    total = len(df)

    print(_rule())
    print("DATASET")
    print(_rule())
    print(f"  Transactions        : {total:,}")
    print(f"  Time steps          : {int(df[TIME_COL].min())} to {int(df[TIME_COL].max())}")
    print(f"  Feature columns     : {len(feature_columns(df))}")
    print(f"  Labelled            : {len(labelled):,} ({len(labelled)/total:.1%})")
    print(f"    illicit (class 1) : {illicit:,} ({illicit/max(len(labelled),1):.2%} of labelled)")
    print(f"    licit   (class 2) : {licit:,}")
    print(f"  Unlabelled          : {unknown:,} ({unknown/total:.1%})")
    if illicit:
        print(f"  Imbalance ratio     : 1 illicit per {licit/illicit:.0f} licit")
    print()
    return {
        "transactions": total,
        "labelled": len(labelled),
        "illicit": illicit,
        "licit": licit,
        "unknown": unknown,
    }


def _report_classifier(y_true, y_pred, y_prob) -> dict:
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = cm.ravel()
    metrics = {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, y_prob)) if len(set(y_true)) > 1 else float("nan"),
        "average_precision": float(average_precision_score(y_true, y_prob))
        if len(set(y_true)) > 1 else float("nan"),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }

    print("  Precision (illicit) : {precision:.3f}".format(**metrics))
    print("  Recall    (illicit) : {recall:.3f}".format(**metrics))
    print("  F1        (illicit) : {f1:.3f}".format(**metrics))
    print("  ROC AUC             : {roc_auc:.3f}".format(**metrics))
    print("  Avg precision (PR)  : {average_precision:.3f}".format(**metrics))
    print()
    print("  Confusion matrix (rows = actual, cols = predicted)")
    print(f"                 licit   illicit")
    print(f"     licit   {tn:>8,}  {fp:>8,}")
    print(f"     illicit {fn:>8,}  {tp:>8,}")
    print()
    print("  Full report:")
    for line in classification_report(
        y_true, y_pred, target_names=["licit", "illicit"], zero_division=0
    ).splitlines():
        print(f"    {line}")
    print()
    return metrics


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------


def train_random_forest(
    train: pd.DataFrame, test: pd.DataFrame, cols: list[str]
) -> tuple[RandomForestClassifier, dict]:
    """Supervised baseline.

    class_weight="balanced" matters here: illicit is roughly 2% of the labelled
    data, and an unweighted forest maximises accuracy by predicting "licit"
    almost everywhere, which scores 98% accuracy and catches nothing.
    """
    print(_rule())
    print("RANDOM FOREST (supervised)")
    print(_rule())

    X_train, y_train = train[cols], train[LABEL_COL].astype(int)
    X_test, y_test = test[cols], test[LABEL_COL].astype(int)
    print(f"  Train: {len(X_train):,} rows ({int(y_train.sum()):,} illicit)")
    print(f"  Test : {len(X_test):,} rows ({int(y_test.sum()):,} illicit)")
    print()

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=None,
        min_samples_leaf=2,
        class_weight="balanced",
        n_jobs=-1,
        random_state=RANDOM_STATE,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    metrics = _report_classifier(y_test, y_pred, y_prob)

    importances = sorted(
        zip(cols, model.feature_importances_), key=lambda kv: kv[1], reverse=True
    )
    print("  Top 10 features by importance:")
    for name, score in importances[:10]:
        print(f"    {name:<14} {score:.4f}")
    print()
    metrics["top_features"] = [{"feature": n, "importance": float(s)}
                               for n, s in importances[:20]]
    return model, metrics


def train_isolation_forest(
    train: pd.DataFrame, test: pd.DataFrame, cols: list[str]
) -> tuple[IsolationForest, dict]:
    """Unsupervised baseline.

    Fitted on the training features with the labels withheld, then checked
    against them. The question is whether "unusual" and "illicit" coincide at
    all - if they do, the same detector can work on the ~77% of the dataset
    that carries no label, which is the reason for having it.

    contamination is set from the observed illicit rate rather than left at
    "auto", so the decision boundary sits somewhere defensible.
    """
    print(_rule())
    print("ISOLATION FOREST (unsupervised, labels withheld)")
    print(_rule())

    illicit_rate = float(train[LABEL_COL].mean())
    contamination = min(max(illicit_rate, 0.001), 0.5)

    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        n_jobs=-1,
        random_state=RANDOM_STATE,
    )
    model.fit(train[cols])  # no labels passed

    # score_samples is lower for more anomalous points; negate so that a higher
    # value means "more suspicious" and lines up with the illicit label.
    anomaly = -model.score_samples(test[cols])
    y_test = test[LABEL_COL].astype(int)

    auc = float(roc_auc_score(y_test, anomaly)) if len(set(y_test)) > 1 else float("nan")
    ap = float(average_precision_score(y_test, anomaly)) if len(set(y_test)) > 1 else float("nan")
    corr = float(np.corrcoef(anomaly, y_test)[0, 1])

    flagged = model.predict(test[cols]) == -1
    overlap_precision = float(y_test[flagged].mean()) if flagged.any() else 0.0
    overlap_recall = float(flagged[y_test == 1].mean()) if (y_test == 1).any() else 0.0

    print(f"  contamination       : {contamination:.4f} (from observed illicit rate)")
    print(f"  Test rows           : {len(test):,} ({int(y_test.sum()):,} illicit)")
    print()
    print(f"  ROC AUC vs label    : {auc:.3f}   (0.5 = anomaly says nothing about illicit)")
    print(f"  Avg precision       : {ap:.3f}   (baseline = {y_test.mean():.3f}, the illicit rate)")
    print(f"  Point-biserial corr : {corr:+.3f}")
    print(f"  Of those it flags   : {overlap_precision:.1%} are actually illicit")
    print(f"  Of actual illicit   : {overlap_recall:.1%} get flagged")
    print()

    return model, {
        "roc_auc": auc,
        "average_precision": ap,
        "correlation_with_label": corr,
        "contamination": contamination,
        "flagged_precision": overlap_precision,
        "flagged_recall": overlap_recall,
    }


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def save_models(
    rf: RandomForestClassifier,
    iso: IsolationForest,
    cols: list[str],
    metrics: dict,
    model_dir: Path = MODEL_DIR,
) -> dict[str, Path]:
    """Save both models plus a manifest describing their input schema.

    The manifest is what stops Phase 5 from silently feeding these models the
    wrong feature space: it records the exact column names and order they were
    fitted on, so a mismatch is caught with a readable message.
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    rf_path = model_dir / "rf_classifier.joblib"
    iso_path = model_dir / "isolation_forest.joblib"
    manifest_path = model_dir / "manifest.json"

    joblib.dump(rf, rf_path)
    joblib.dump(iso, iso_path)
    manifest_path.write_text(
        json.dumps(
            {
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "feature_space": "elliptic",
                "n_features": len(cols),
                "feature_columns": cols,
                "random_state": RANDOM_STATE,
                "split_time_step": DEFAULT_SPLIT_TIME_STEP,
                "metrics": metrics,
                "warning": (
                    "These models take the 166-column Elliptic feature space. They "
                    "cannot score a wallet described by graph features from "
                    "feature_extraction.py."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"rf": rf_path, "iso": iso_path, "manifest": manifest_path}


def load_models(model_dir: Path = MODEL_DIR):
    """Load both models and the manifest. Used by the API at startup."""
    model_dir = Path(model_dir)
    rf_path = model_dir / "rf_classifier.joblib"
    iso_path = model_dir / "isolation_forest.joblib"
    manifest_path = model_dir / "manifest.json"
    if not rf_path.exists() or not iso_path.exists():
        raise FileNotFoundError(
            f"No trained models in {model_dir}. Run "
            f"`python -m app.services.ml_baseline` first."
        )
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    return joblib.load(rf_path), joblib.load(iso_path), manifest


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------


def main(data_dir: Path | str = DATA_DIR, model_dir: Path | str = MODEL_DIR) -> dict:
    df = load_elliptic(data_dir)
    stats = describe(df)

    labelled = df[df[LABEL_COL].notna()].copy()
    if labelled.empty:
        raise ValueError("No labelled rows found; every class was 'unknown'.")

    cols = feature_columns(df)
    train, test = temporal_split(labelled)
    if train.empty or test.empty:
        raise ValueError(
            f"Temporal split at time step {DEFAULT_SPLIT_TIME_STEP} left an empty "
            f"side (train={len(train)}, test={len(test)})."
        )

    rf, rf_metrics = train_random_forest(train, test, cols)
    iso, iso_metrics = train_isolation_forest(train, test, cols)

    metrics = {"dataset": stats, "random_forest": rf_metrics, "isolation_forest": iso_metrics}
    paths = save_models(rf, iso, cols, metrics, Path(model_dir))

    print(_rule())
    print("SUMMARY")
    print(_rule())
    print(f"  Labelled transactions : {stats['labelled']:,} "
          f"({stats['illicit']:,} illicit, {stats['licit']:,} licit)")
    print(f"  Split                 : time step <= {DEFAULT_SPLIT_TIME_STEP} train, "
          f"> {DEFAULT_SPLIT_TIME_STEP} test")
    print()
    print("  Random forest (supervised)")
    print(f"    precision {rf_metrics['precision']:.3f}   "
          f"recall {rf_metrics['recall']:.3f}   "
          f"F1 {rf_metrics['f1']:.3f}   ROC AUC {rf_metrics['roc_auc']:.3f}")
    cm = rf_metrics["confusion_matrix"]
    print(f"    TP {cm['tp']:,}  FP {cm['fp']:,}  FN {cm['fn']:,}  TN {cm['tn']:,}")
    print()
    print("  Isolation forest (unsupervised)")
    print(f"    ROC AUC {iso_metrics['roc_auc']:.3f} vs the illicit label   "
          f"corr {iso_metrics['correlation_with_label']:+.3f}")
    print(f"    {iso_metrics['flagged_precision']:.1%} of what it flags is illicit; "
          f"it catches {iso_metrics['flagged_recall']:.1%} of illicit")
    print()
    for name, path in paths.items():
        print(f"  -> {path}")
    print(_rule())

    return metrics


if __name__ == "__main__":
    main()
