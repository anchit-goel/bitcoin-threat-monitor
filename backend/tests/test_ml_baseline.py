"""Phase 3 tests, run against a schema-identical stand-in for the Elliptic data.

The real dataset is behind a Kaggle login and cannot be committed, so these
tests exercise ml_baseline.py against tests/elliptic_fixture.py, which
reproduces the dataset's schema - 167 columns, no header on the features file,
1/2/unknown labels, an illicit minority spread across 49 time steps.

What this proves: the loader, the label remapping, the temporal split, the
class-imbalance handling, the metrics and the persistence all work on data
shaped like Elliptic's. What it does not prove: anything about how the models
score on the real thing. Run against the genuine CSVs for figures to quote.
"""

from __future__ import annotations

import csv
import json

import pytest

from app.services import ml_baseline as mb
from tests.elliptic_fixture import N_FEATURES, write_fixture


@pytest.fixture(scope="module")
def elliptic(tmp_path_factory):
    d = tmp_path_factory.mktemp("elliptic")
    write_fixture(d, n_rows=4_000, seed=7)
    return d


@pytest.fixture(scope="module")
def trained(elliptic, tmp_path_factory):
    models = tmp_path_factory.mktemp("models")
    metrics = mb.main(elliptic, models)
    return models, metrics


# --------------------------------------------------------------------------
# Fixture fidelity - if this drifts, everything below tests the wrong shape
# --------------------------------------------------------------------------


def test_fixture_matches_elliptic_layout(elliptic):
    with (elliptic / "elliptic_txs_features.csv").open() as fh:
        first = next(csv.reader(fh))
    assert len(first) == N_FEATURES + 2 == 168 - 1  # txId + time step + 166
    float(first[0])  # first row is data, not a header

    with (elliptic / "elliptic_txs_classes.csv").open() as fh:
        header = next(csv.reader(fh))
    assert header == ["txId", "class"]


def test_header_sniffing(elliptic):
    assert mb._has_header(elliptic / "elliptic_txs_features.csv") is False
    assert mb._has_header(elliptic / "elliptic_txs_classes.csv") is True


# --------------------------------------------------------------------------
# Loading and labelling
# --------------------------------------------------------------------------


def test_load_maps_labels_correctly(elliptic):
    df = mb.load_elliptic(elliptic)
    assert mb.ID_COL in df and mb.TIME_COL in df
    assert len(mb.feature_columns(df)) == N_FEATURES

    # 1 -> 1.0 illicit, 2 -> 0.0 licit, "unknown" -> NaN
    assert set(df[mb.LABEL_COL].dropna().unique()) <= {0.0, 1.0}
    assert df[mb.LABEL_COL].isna().sum() > 0, "unknowns must survive as NaN"
    labelled = df[df[mb.LABEL_COL].notna()]
    assert 0 < labelled[mb.LABEL_COL].mean() < 0.5, "illicit must be the minority"


def test_missing_files_name_what_to_download(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        mb.load_elliptic(tmp_path)
    msg = str(exc.value)
    assert "elliptic_txs_features.csv" in msg
    assert "Kaggle" in msg


def test_wrong_column_count_is_rejected(tmp_path):
    (tmp_path / "elliptic_txs_features.csv").write_text("1,2,3\n4,5,6\n", encoding="utf-8")
    (tmp_path / "elliptic_txs_classes.csv").write_text("txId,class\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected 167"):
        mb.load_elliptic(tmp_path)


# --------------------------------------------------------------------------
# The split is the thing most easily got wrong
# --------------------------------------------------------------------------


def test_temporal_split_has_no_leakage(elliptic):
    df = mb.load_elliptic(elliptic)
    labelled = df[df[mb.LABEL_COL].notna()]
    train, test = mb.temporal_split(labelled)

    assert not train.empty and not test.empty
    assert train[mb.TIME_COL].max() <= mb.DEFAULT_SPLIT_TIME_STEP
    assert test[mb.TIME_COL].min() > mb.DEFAULT_SPLIT_TIME_STEP
    # The whole point: no time step appears on both sides.
    assert not set(train[mb.TIME_COL]) & set(test[mb.TIME_COL])
    # And no transaction is in both.
    assert not set(train[mb.ID_COL]) & set(test[mb.ID_COL])
    assert len(train) + len(test) == len(labelled)


# --------------------------------------------------------------------------
# Training, metrics and persistence
# --------------------------------------------------------------------------


def test_models_and_manifest_are_saved(trained):
    models, _ = trained
    for name in ("rf_classifier.joblib", "isolation_forest.joblib", "manifest.json"):
        assert (models / name).exists(), f"{name} was not written"


def test_manifest_records_the_feature_space(trained):
    models, _ = trained
    manifest = json.loads((models / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["n_features"] == N_FEATURES
    assert len(manifest["feature_columns"]) == N_FEATURES
    assert manifest["feature_space"] == "elliptic"
    # The guard that stops Phase 5 feeding these models graph features.
    assert "cannot score a wallet" in manifest["warning"]


def test_loaded_models_predict_on_the_recorded_schema(trained, elliptic):
    models, _ = trained
    rf, iso, manifest = mb.load_models(models)

    df = mb.load_elliptic(elliptic)
    rows = df[df[mb.LABEL_COL].notna()].head(20)[manifest["feature_columns"]]
    probs = rf.predict_proba(rows)[:, 1]
    assert len(probs) == 20
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert len(iso.score_samples(rows)) == 20


def test_wrong_feature_space_raises_rather_than_mis_scores(trained):
    """Phase 5 guard: a graph feature vector must not silently produce a score."""
    models, _ = trained
    rf, _, _ = mb.load_models(models)
    graph_shaped = [[0.4, 12, 8, 0.002, 0.31, 5.2, 0.9, 44.1, 39.8, 1.5, 0.7, 2.0]]
    with pytest.raises(ValueError):
        rf.predict_proba(graph_shaped)


def test_metrics_are_reported_and_sane(trained):
    _, metrics = trained
    rf = metrics["random_forest"]
    for key in ("precision", "recall", "f1", "roc_auc", "confusion_matrix"):
        assert key in rf
    for key in ("precision", "recall", "f1", "roc_auc"):
        assert 0.0 <= rf[key] <= 1.0

    cm = rf["confusion_matrix"]
    assert set(cm) == {"tn", "fp", "fn", "tp"}
    assert sum(cm.values()) > 0

    # The fixture plants real signal, so a working pipeline must rank better
    # than chance. A failure here means the labels or the split are inverted.
    assert rf["roc_auc"] > 0.7, "model is no better than chance on planted signal"

    iso = metrics["isolation_forest"]
    for key in ("roc_auc", "average_precision", "correlation_with_label"):
        assert key in iso
    assert -1.0 <= iso["correlation_with_label"] <= 1.0


def test_missing_models_say_how_to_train(tmp_path):
    with pytest.raises(FileNotFoundError, match="ml_baseline"):
        mb.load_models(tmp_path)
