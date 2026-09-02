"""Generate CSVs that match the Elliptic dataset's schema exactly.

The real dataset needs a Kaggle account, so it cannot live in the repo and is
not available on a fresh clone or in CI. This fixture reproduces its *shape* -
column count, the missing header on the features file, the 1/2/unknown label
encoding, the illicit minority, the time-step structure - so ml_baseline.py can
be exercised end to end without it.

It is a schema stand-in, not a substitute: the numbers it produces say nothing
about how the models perform on real data. Only run against the genuine CSVs
for figures worth quoting.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# The real dataset's proportions, so the class-imbalance handling is exercised.
N_LOCAL = 93
N_AGG = 72
N_FEATURES = N_LOCAL + N_AGG  # 166
MAX_TIME_STEP = 49
LABELLED_FRACTION = 0.23   # ~23% of the real dataset carries a label
ILLICIT_OF_LABELLED = 0.10  # ~9.8% of labelled rows are illicit

# Illicit rows are shifted on a handful of features. Without some signal the
# test only proves the code runs; with it, the reported metrics are meaningful
# enough to catch a broken split or an inverted label.
N_SIGNAL_FEATURES = 12
SIGNAL_STRENGTH = 0.9


def write_fixture(
    out_dir: Path | str,
    n_rows: int = 12_000,
    seed: int = 1337,
) -> dict[str, Path]:
    """Write the three Elliptic CSVs into `out_dir`. Returns their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    tx_ids = np.arange(230_000, 230_000 + n_rows)
    time_steps = rng.integers(1, MAX_TIME_STEP + 1, size=n_rows)

    n_labelled = int(n_rows * LABELLED_FRACTION)
    labelled_idx = rng.choice(n_rows, size=n_labelled, replace=False)
    n_illicit = max(2, int(n_labelled * ILLICIT_OF_LABELLED))
    illicit_idx = labelled_idx[:n_illicit]
    licit_idx = labelled_idx[n_illicit:]

    features = rng.standard_normal((n_rows, N_FEATURES))
    signal_cols = rng.choice(N_FEATURES, size=N_SIGNAL_FEATURES, replace=False)
    features[np.ix_(illicit_idx, signal_cols)] += SIGNAL_STRENGTH

    # Features file: NO header, txId and time step as the first two columns.
    features_path = out_dir / "elliptic_txs_features.csv"
    pd.DataFrame(
        np.column_stack([tx_ids, time_steps, features])
    ).to_csv(features_path, header=False, index=False)

    # Classes file: header, and the literal string "unknown" for unlabelled.
    labels = np.full(n_rows, "unknown", dtype=object)
    labels[illicit_idx] = "1"
    labels[licit_idx] = "2"
    classes_path = out_dir / "elliptic_txs_classes.csv"
    pd.DataFrame({"txId": tx_ids, "class": labels}).to_csv(classes_path, index=False)

    # Edge list: header, pairs within the same time step.
    order = np.argsort(time_steps, kind="stable")
    src = tx_ids[order][:-1]
    dst = tx_ids[order][1:]
    keep = time_steps[order][:-1] == time_steps[order][1:]
    edges_path = out_dir / "elliptic_txs_edgelist.csv"
    pd.DataFrame({"txId1": src[keep], "txId2": dst[keep]}).to_csv(
        edges_path, index=False
    )

    return {"features": features_path, "classes": classes_path, "edges": edges_path}


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../data/_fixture")
    paths = write_fixture(target)
    for name, path in paths.items():
        print(f"{name:<9} -> {path}  ({path.stat().st_size/1e6:.1f} MB)")
