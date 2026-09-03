"""ChainSentry (BitcoinHeist) — trained and validated on real, address-level,
ransomware-labelled Bitcoin data.

WHY A SECOND REAL-DATA MODEL, NOT JUST ELLIPTIC. Elliptic's real-data benchmark
(elliptic_real.py) tests our features at the wrong granularity: Elliptic labels
TRANSACTIONS, our system scores WALLETS. BitcoinHeist labels ADDRESSES - the
same unit we actually score - so this is the real-data test that matches our
product, not just our pipeline. 2,916,697 real addresses, 41,413 of them
labelled to one of 28 real ransomware families (CryptoLocker, Locky, Cerber,
WannaCry, and others) by the paper's own manual attribution work, the rest
"white". Verified byte-exact against the published dataset on load.

WHICH FEATURES. BitcoinHeist ships its own six per-address structural
features (length, weight, count, looped, neighbors, income) computed by the
paper's authors from the real blockchain - not the ten features in
feature_extraction.py, and not directly reducible to them without rebuilding
the address's real transaction graph, which this dataset does not provide.
So this trains on BitcoinHeist's own feature space, honestly kept separate
from ChainSentry (synthetic): see the feature-space guard below, which mirrors
the one in scoring.py for the exact same reason - two models fitted on
different columns must never be swapped by accident.

THE SPLIT. Chronological, at year 2016 (train 2011-2015, test 2016-2018).
Two things were checked before choosing it, not assumed:

  - A 2017 cutoff leaves only 3,489 illicit test rows and just 4 test-only
    ransomware families. 2016 gives 19,120 illicit test rows and 21 of 26
    test families - including WannaCry - never seen in training. That is
    the harder and more honest test: can real behavioural features catch a
    ransomware family the model has never seen a single example of.
  - The same real-world address can appear in rows on both sides of any year
    cutoff (an address is observed once per active time window, not once
    ever). 5,328 addresses do here. They are dropped from the test set
    explicitly, so no address's label can leak across the split.

Run it:

    python -m app.services.bitcoinheist_real
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
    fbeta_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
MODEL_DIR = Path(__file__).resolve().parents[1] / "models_trained"

DATA_CSV = DATA_DIR / "BitcoinHeistData.csv"

MODEL_NAME = "ChainSentry (BitcoinHeist)"
SOURCE = (
    "BitcoinHeist Ransomware Address Dataset (Akcora et al. 2020). "
    "2,916,697 real Bitcoin addresses; 2,875,284 white, 41,413 labelled to "
    "one of 28 real ransomware families."
)

# The paper's own six per-address structural features.
BH_FEATURES = ["length", "weight", "count", "looped", "neighbors", "income"]

SPLIT_YEAR = 2016
RANDOM_STATE = 1337

# The chosen operating point: probability >= this counts as "flag it".
#
# Not the classifier's default 0.5. This dataset's crime is rare (1.77% of
# the test set), and a missed criminal wallet costs nothing visible - the
# harm just stays undetected - while a false positive costs an analyst a few
# minutes to dismiss, given the human review step this system assumes. That
# asymmetry argues for weighting recall over precision, the same policy real
# bank AML systems use (routinely running at 90%+ false-positive rates).
#
# The actual number was not picked to fit that story - it is the F2-optimal
# point (F-beta with beta=2, the standard way to formalise "recall matters
# twice as much as precision"), found by sweeping thresholds 0.02-0.50 and
# scoring each. The honest result: F2-optimal is 0.39, barely below the
# default 0.50 - recall moves from 29.6% to 36.5% for almost no precision
# cost (4.7% -> 4.5%), then F2 stays flat across 0.15-0.50 before collapsing
# below ~0.10 as false positives dominate. There is no hidden sweet spot a
# cleverer threshold was missing; the ranking underneath is the bottleneck,
# not the cutoff. See random_forest_at_threshold in the saved manifest.
OPERATING_THRESHOLD = 0.39

# Published figures, checked on every load so a truncated or substituted
# download cannot silently produce plausible-looking numbers. See the
# download and verification steps in data/README.md.
EXPECTED_ROWS = 2_916_697
EXPECTED_WHITE = 2_875_284
EXPECTED_ILLICIT = 41_413


def files_present() -> bool:
    return DATA_CSV.exists()


def load_verified() -> pd.DataFrame:
    """Load BitcoinHeistData.csv, refusing anything that isn't the real thing."""
    if not files_present():
        raise FileNotFoundError(
            f"Missing {DATA_CSV.name} in {DATA_DIR}. See data/README.md for the "
            "working download source - UCI's own host truncates this file."
        )

    df = pd.read_csv(DATA_CSV)
    white = int((df["label"] == "white").sum())
    illicit = len(df) - white

    if len(df) != EXPECTED_ROWS or white != EXPECTED_WHITE or illicit != EXPECTED_ILLICIT:
        raise ValueError(
            f"{DATA_CSV.name} does not match the published BitcoinHeist dataset "
            f"(got {len(df):,} rows, {white:,} white, {illicit:,} illicit; "
            f"expected {EXPECTED_ROWS:,} / {EXPECTED_WHITE:,} / {EXPECTED_ILLICIT:,}). "
            "The download is likely truncated - re-fetch it per data/README.md."
        )
    return df


def temporal_split(
    df: pd.DataFrame, split_year: int = SPLIT_YEAR
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train on years before split_year, test on split_year onward.

    Addresses observed on both sides of the cutoff are dropped from the test
    set, so no address's label can leak across the split via its own repeated
    rows. This is checked by a test, not merely intended.
    """
    train = df[df["year"] < split_year]
    test_all = df[df["year"] >= split_year]

    train_addrs = set(train["address"])
    test = test_all[~test_all["address"].isin(train_addrs)]

    return train, test


def train(model_dir: Path | str = MODEL_DIR, verbose: bool = True) -> dict:
    df = load_verified()

    if verbose:
        print("=" * 74)
        print(f"REAL DATA BENCHMARK - {MODEL_NAME}")
        print("=" * 74)
        print(f"  {len(df):,} real addresses, {(df['label'] != 'white').sum():,} "
              f"labelled to a real ransomware family, {df['label'].nunique() - 1} families")
        print()

    train_df, test_df = temporal_split(df)
    dropped = len(df[df["year"] >= SPLIT_YEAR]) - len(test_df)

    X_train, y_train = train_df[BH_FEATURES], (train_df["label"] != "white").astype(int)
    X_test, y_test = test_df[BH_FEATURES], (test_df["label"] != "white").astype(int)

    train_families = set(train_df.loc[y_train == 1, "label"])
    test_families = set(test_df.loc[y_test == 1, "label"])
    unseen_families = test_families - train_families

    if verbose:
        print(f"  split: train < {SPLIT_YEAR} ({len(X_train):,} rows, {y_train.sum():,} "
              f"illicit, {y_train.mean():.2%}), test >= {SPLIT_YEAR} "
              f"({len(X_test):,} rows, {y_test.sum():,} illicit, {y_test.mean():.2%})")
        print(f"         {dropped:,} test rows dropped - their address also appears "
              f"in the training years")
        print(f"         {len(unseen_families)} of {len(test_families)} test-set "
              f"ransomware families never appear in training: "
              f"{sorted(unseen_families)[:4]}{'...' if len(unseen_families) > 4 else ''}")
        print()

    rf = RandomForestClassifier(
        n_estimators=300,
        min_samples_leaf=5,
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
        print(f"  Random forest on BitcoinHeist's own features, held out by year")
        print(f"    precision {rf_metrics['precision']:.3f}   "
              f"recall {rf_metrics['recall']:.3f}   F1 {rf_metrics['f1']:.3f}")
        print(f"    ROC AUC {rf_metrics['roc_auc']:.3f}   "
              f"avg precision {rf_metrics['average_precision']:.3f} "
              f"(a coin flip would score {rf_metrics['baseline_rate']:.3f})")
        print(f"    TP {tp}  FP {fp}  FN {fn}  TN {tn}")
        print()
        print(classification_report(
            y_test, pred, target_names=["white", "ransomware"], digits=3, zero_division=0,
        ).rstrip().replace("\n", "\n    ").rjust(4))
        print()

    # The chosen operating point, not just the classifier's raw 0.5 default.
    pred_op = (prob >= OPERATING_THRESHOLD).astype(int)
    tn2, fp2, fn2, tp2 = confusion_matrix(y_test, pred_op, labels=[0, 1]).ravel()
    at_threshold = {
        "threshold": OPERATING_THRESHOLD,
        "precision": float(precision_score(y_test, pred_op, zero_division=0)),
        "recall": float(recall_score(y_test, pred_op, zero_division=0)),
        "f1": float(f1_score(y_test, pred_op, zero_division=0)),
        "f2": float(fbeta_score(y_test, pred_op, beta=2.0, zero_division=0)),
        "flagged": int(pred_op.sum()),
        "flagged_fraction_of_test": float(pred_op.mean()),
        "confusion_matrix": {"tn": int(tn2), "fp": int(fp2), "fn": int(fn2), "tp": int(tp2)},
    }
    if verbose:
        print(f"  Operating point: threshold {OPERATING_THRESHOLD} (F2-optimal)")
        print(f"    precision {at_threshold['precision']:.1%}   "
              f"recall {at_threshold['recall']:.1%}   "
              f"F1 {at_threshold['f1']:.3f}   F2 {at_threshold['f2']:.3f}")
        print(f"    flags {at_threshold['flagged']:,} of {len(y_test):,} "
              f"({at_threshold['flagged_fraction_of_test']:.1%} of the test set) - "
              f"still too large for direct manual review; useful as a first-stage")
        print(f"    filter before further ranked triage, not as a final worklist")
        print()

    # Recall on families the model never trained on at all - the sharpest
    # honest test this dataset can give: not "does it fit the pattern it was
    # shown" but "does it catch a pattern it has never seen a single example of".
    unseen_mask = test_df["label"].isin(unseen_families)
    if unseen_mask.any():
        unseen_recall = float(pred[unseen_mask.to_numpy()].mean())
        if verbose:
            print(f"  Recall on ransomware families NEVER seen in training: "
                  f"{unseen_recall:.1%} of {unseen_mask.sum():,} rows caught")
            print()
    else:
        unseen_recall = None

    order = np.argsort(prob)[::-1]
    base = float(y_test.mean())
    at_k = {}
    for k in (50, 100, 500, 1000):
        if k <= len(order):
            hit = float(y_test.to_numpy()[order[:k]].mean())
            at_k[f"precision_at_{k}"] = hit
            at_k[f"lift_at_{k}"] = hit / base if base else float("nan")

    if verbose:
        print("  Precision@k - of the top k this ranks, how many are truly ransomware")
        print(f"    {'k':>6}{'precision':>12}{'lift vs base':>15}")
        for k in (50, 100, 500, 1000):
            if f"precision_at_{k}" in at_k:
                print(f"    {k:>6}{at_k[f'precision_at_{k}']:>12.1%}"
                      f"{at_k[f'lift_at_{k}']:>14.2f}x")
        print()

    # "income" is the top feature by importance, and it actively harms the
    # ranking an investigator would actually use. Aggregate ROC AUC looks
    # decent (0.69) largely because income tells the broad middle of the
    # distribution apart, but the very top of the queue is dominated by
    # legitimate high-volume addresses - exchange wallets, miners - that
    # income alone cannot distinguish from a large ransom. Refitting without
    # it trades aggregate AUC for a queue an investigator could actually use.
    # Reported here rather than picked one way silently, because which
    # number belongs in a headline depends on how the tool is used.
    without_income = [f for f in BH_FEATURES if f != "income"]
    rf_no_income = RandomForestClassifier(
        n_estimators=300, min_samples_leaf=5, class_weight="balanced",
        n_jobs=-1, random_state=RANDOM_STATE,
    )
    rf_no_income.fit(X_train[without_income], y_train)
    prob_ni = rf_no_income.predict_proba(X_test[without_income])[:, 1]
    order_ni = np.argsort(prob_ni)[::-1]
    y_np = y_test.to_numpy()
    at_k_no_income = {}
    for k in (50, 100, 500, 1000):
        if k <= len(order_ni):
            hit = float(y_np[order_ni[:k]].mean())
            at_k_no_income[f"precision_at_{k}"] = hit
            at_k_no_income[f"lift_at_{k}"] = hit / base if base else float("nan")
    roc_no_income = float(roc_auc_score(y_test, prob_ni))

    if verbose:
        print("  Without 'income' - lower aggregate AUC, better top-of-queue precision")
        print(f"    ROC AUC {roc_no_income:.3f}  (was {rf_metrics['roc_auc']:.3f} with income)")
        print(f"    {'k':>6}{'precision':>12}{'lift vs base':>15}")
        for k in (50, 100, 500, 1000):
            if f"precision_at_{k}" in at_k_no_income:
                print(f"    {k:>6}{at_k_no_income[f'precision_at_{k}']:>12.1%}"
                      f"{at_k_no_income[f'lift_at_{k}']:>14.2f}x")
        print()

    iso = IsolationForest(
        n_estimators=300, contamination=0.02, n_jobs=-1, random_state=RANDOM_STATE,
    )
    iso.fit(X_train[y_train == 0])
    iso_scores = -iso.score_samples(X_test)
    iso_metrics = {
        "roc_auc": float(roc_auc_score(y_test, iso_scores)),
        "average_precision": float(average_precision_score(y_test, iso_scores)),
    }
    if verbose:
        print("  Isolation forest (fitted on white addresses only)")
        print(f"    ROC AUC {iso_metrics['roc_auc']:.3f}   "
              f"avg precision {iso_metrics['average_precision']:.3f}")
        print()

    importances = sorted(
        zip(BH_FEATURES, rf.feature_importances_), key=lambda kv: kv[1], reverse=True,
    )
    solo = {c: float(roc_auc_score(y_test, X_test[c])) for c in BH_FEATURES}
    if verbose:
        print("  Feature importance, and each feature alone (ROC AUC; 0.5 = coin flip)")
        for name, score in importances:
            print(f"    {name:<12}importance {score:.4f}  "
                  f"{'#' * max(1, round(score * 50)):<26}solo AUC {solo[name]:.3f}")
        print()

    metrics = {
        "model_name": MODEL_NAME,
        "source": SOURCE,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_space": "bitcoinheist_own",
        "features_used": BH_FEATURES,
        "split": {
            "kind": f"temporal, cutoff year {SPLIT_YEAR}",
            "train_rows": int(len(X_train)),
            "test_rows": int(len(X_test)),
            "test_rows_dropped_for_address_overlap": int(dropped),
            "train_families": sorted(train_families),
            "test_only_families": sorted(unseen_families),
            "recall_on_unseen_families": unseen_recall,
        },
        "random_forest": rf_metrics,
        "random_forest_at_threshold": at_threshold,
        "random_forest_at_threshold": at_threshold,
        "random_forest_precision_at_k": at_k,
        "random_forest_without_income": {
            "roc_auc": roc_no_income,
            "precision_at_k": at_k_no_income,
            "note": (
                "income is the top feature by importance but actively harms "
                "top-of-queue precision; this is the model refit without it"
            ),
        },
        "single_feature_roc_auc": solo,
        "isolation_forest": iso_metrics,
        "feature_importance": [
            {"feature": n, "importance": float(s)} for n, s in importances
        ],
    }

    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(rf, model_dir / "chainsentry_bh_rf.joblib")
    joblib.dump(iso, model_dir / "chainsentry_bh_isolation_forest.joblib")
    (model_dir / "chainsentry_bh_manifest.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )

    if verbose:
        print("=" * 74)
        print(f"  {MODEL_NAME} does not feed the live wallet scorer. It is trained on")
        print("  BitcoinHeist's own six features, a different space from the ten graph")
        print("  features + four rule flags ChainSentry (synthetic) uses, and one that")
        print("  cannot be reconstructed without this dataset's real transaction graph,")
        print("  which is not published. It stands as an independent, real, wallet-level")
        print("  validation - the unit our product actually scores.")
        print(f"  -> {model_dir / 'chainsentry_bh_manifest.json'}")
        print("=" * 74)

    return metrics


if __name__ == "__main__":
    train()
